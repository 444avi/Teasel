from __future__ import annotations

import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from flask import Flask, jsonify, render_template, request
from arboretum_auth import AuthConfig
from arboretum_auth.flask_integration import FlaskAuth
from arboretum_auth.verifier import SessionVerifier
from werkzeug.exceptions import RequestEntityTooLarge

from teasel.engine import FeeModel, Leg, analyze_position
from teasel.kalshi import KalshiIngestError, fetch_ladder
from teasel.rate_limit import RateLimiter

MAX_JSON_BYTES = 64 * 1024
MAX_EVENT_CHARS = 256
MAX_RUNGS = 64


def _demo_snapshot() -> dict:
    rungs = [
        ("WTI-82.99", 82.99, 76, 26, .75),
        ("WTI-83.99", 83.99, 65, 37, .64),
        ("WTI-84.99", 84.99, 39, 67, .36),
        ("WTI-85.99", 85.99, 15, 83, .17),
    ]
    return {
        "event_ticker": "KXWTI-DEMO",
        "title": "Oil Price (WTI) tomorrow?",
        "is_demo": True,
        "warnings": [],
        "rungs": [
            {
                "market_id": market_id,
                "threshold": threshold,
                "operator": ">",
                "yes_ask_cents": yes_ask,
                "no_ask_cents": no_ask,
                "probability_above": probability,
                "probability_source": "displayed demo",
                "status": "active",
                "subtitle": f"Above ${threshold:.2f}",
            }
            for market_id, threshold, yes_ask, no_ask, probability in rungs
        ],
    }


def create_app(
    auth_config: AuthConfig | None = None,
    verifier: SessionVerifier | None = None,
    rate_limiter: RateLimiter | None = None,
    *,
    testing: bool = False,
) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing
    app.config["MAX_CONTENT_LENGTH"] = MAX_JSON_BYTES
    config = auth_config or AuthConfig.from_env()
    public_url = os.environ.get("TEASEL_PUBLIC_URL", "http://127.0.0.1:5050").rstrip("/")
    public_parts = urlsplit(public_url)
    public_host = public_parts.hostname
    if (
        not public_host or public_parts.scheme not in ("http", "https")
        or public_parts.path not in ("", "/") or public_parts.query or public_parts.fragment
        or public_parts.username or public_parts.password
    ):
        raise RuntimeError("TEASEL_PUBLIC_URL must be an origin without a path or query")
    environment = os.environ.get("TEASEL_ENV", "").strip().lower()
    if not testing and environment not in ("development", "staging", "production"):
        raise RuntimeError("TEASEL_ENV must be development, staging, or production")
    if not testing and environment in ("staging", "production"):
        required = (
            "TEASEL_PUBLIC_URL", "ARBORETUM_ACCOUNTS_URL", "ARBORETUM_ISSUER",
            "ARBORETUM_AUDIENCE", "ARBORETUM_JWKS_URL",
            "ARBORETUM_ALLOWED_RETURN_HOSTS", "ARBORETUM_RETURN_ORIGIN",
            "TEASEL_RATE_LIMIT_DB",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError("Teasel authentication configuration is incomplete: " + ", ".join(missing))
        accounts_url = os.environ["ARBORETUM_ACCOUNTS_URL"].rstrip("/")
        if (
            public_parts.scheme != "https"
            or urlsplit(accounts_url).scheme != "https"
            or config.issuer != accounts_url
            or config.login_url != accounts_url + "/login"
            or config.refresh_url != accounts_url + "/refresh"
            or config.jwks_url != accounts_url + "/.well-known/jwks.json"
            or config.return_origin != public_url
            or config.allowed_return_hosts != (public_host,)
            or config.public_key or config.jwks
            or config.cookie_name != "arb_session"
            or not 0 <= config.leeway_seconds <= 30
        ):
            raise RuntimeError("Teasel authentication URLs or key source are unsafe")
        if environment == "production" and (
            public_host != "teasel.arboretuminvestments.net"
            or urlsplit(accounts_url).hostname != "accounts.arboretuminvestments.net"
        ):
            raise RuntimeError("Production Teasel and account-service hostnames do not match the rollout")
    db_path = os.environ.get("TEASEL_RATE_LIMIT_DB", "")
    if not db_path:
        db_path = str(Path(__file__).resolve().parent / "var" / "rate_limits.sqlite3")
    if environment in ("staging", "production") and not testing and not Path(db_path).is_absolute():
        raise RuntimeError("TEASEL_RATE_LIMIT_DB must be an absolute shared local path")

    def positive_limit(name: str, default: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError as exc:
            raise RuntimeError(f"{name} must be a positive integer") from exc
        if value < 1:
            raise RuntimeError(f"{name} must be a positive integer")
        return value

    if rate_limiter is None:
        try:
            rate_limiter = RateLimiter(
                db_path,
                {
                    "fetch": positive_limit("TEASEL_FETCH_PER_MINUTE", 12),
                    "analyze": positive_limit("TEASEL_ANALYZE_PER_MINUTE", 120),
                },
            )
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError("Teasel rate-limit storage is unavailable") from exc
    app.config["TRUSTED_HOSTS"] = [public_host] if not testing else ["localhost", "127.0.0.1", public_host]
    auth = FlaskAuth(config, verifier=verifier)
    app.extensions["arboretum_auth"] = auth
    app.extensions["teasel_rate_limiter"] = rate_limiter
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True

    @app.after_request
    def no_store_identity(response):
        if request.path != "/healthz" and not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers.add("Vary", "Cookie")
        if response.status_code in (429, 502, 503):
            app.logger.warning("teasel_response status=%d endpoint=%s", response.status_code, request.endpoint)
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def oversized_json(_error):
        if request.path.startswith("/api/"):
            return jsonify(error=f"JSON body exceeds {MAX_JSON_BYTES} bytes."), 413
        return "Request body too large.", 413

    @app.before_request
    def restrict_api_origin():
        if request.method != "POST" or not request.path.startswith("/api/"):
            return None
        origin = request.headers.get("Origin")
        if origin and origin != public_url:
            return jsonify(error="Cross-origin API requests are not allowed."), 403
        if request.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            return jsonify(error="Cross-origin API requests are not allowed."), 403
        return None

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    def limited(endpoint: str):
        try:
            retry_after = rate_limiter.consume(auth.current_user().id, endpoint)
        except (OSError, sqlite3.Error):
            app.logger.exception("teasel rate-limit storage unavailable")
            return jsonify(error="Request limits are temporarily unavailable."), 503
        if retry_after is not None:
            response = jsonify(error="Rate limit exceeded. Try again shortly.")
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response
        return None

    @app.get("/")
    @auth.login_required()
    def index():
        user = auth.current_user()
        accounts_url = config.login_url.removesuffix("/login")
        account_url = accounts_url + "/account?" + urlencode(
            {"return_to": public_url + "/"}
        )
        return render_template(
            "index.html", user=user, account_url=account_url,
            logout_url=accounts_url + "/logout",
        )

    @app.get("/api/demo")
    @auth.login_required(api=True)
    def demo():
        return jsonify(_demo_snapshot())

    @app.post("/api/fetch")
    @auth.login_required(api=True)
    def fetch_event():
        denial = limited("fetch")
        if denial is not None:
            return denial
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON object."), 422
        event = payload.get("event") or ""
        if not isinstance(event, str) or len(event) > MAX_EVENT_CHARS:
            return jsonify(error=f"Event input must be at most {MAX_EVENT_CHARS} characters."), 422
        try:
            snapshot = fetch_ladder(
                event,
                str(payload.get("probability_source") or "mid"),
            )
            return jsonify(snapshot.to_dict())
        except KalshiIngestError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception:
            app.logger.exception("unexpected Kalshi fetch error")
            return jsonify({"error": "The Kalshi snapshot could not be fetched right now."}), 502

    @app.post("/api/analyze")
    @auth.login_required(api=True)
    def analyze():
        denial = limited("analyze")
        if denial is not None:
            return denial
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON object."), 422
        raw_rungs = payload.get("rungs") or []
        if not isinstance(raw_rungs, list) or len(raw_rungs) > MAX_RUNGS:
            return jsonify(error=f"At most {MAX_RUNGS} rungs are allowed."), 422
        try:
            thresholds = [Decimal(str(rung["threshold"])) for rung in raw_rungs]
            maker = bool(payload.get("use_maker", False))
            fee_rate = Decimal(str(payload.get("fee_rate", "0.07")))
            fee_model = FeeModel(rate=fee_rate, use_maker=maker)
            legs: list[Leg] = []
            for rung in raw_rungs:
                side = str(rung.get("side") or "").upper()
                qty = int(rung.get("qty") or 0)
                if side not in ("YES", "NO") or qty <= 0:
                    continue
                price = rung.get("yes_ask_cents") if side == "YES" else rung.get("no_ask_cents")
                if price is None:
                    raise ValueError(f"No executable {side} ask for {rung.get('market_id')}")
                price_cents = int(price)
                legs.append(
                    Leg(
                        market_id=str(rung.get("market_id") or ""),
                        threshold=Decimal(str(rung["threshold"])),
                        side=side,
                        qty=qty,
                        entry_price_cents=price_cents,
                        fee_cents=fee_model.fee_cents(price_cents, qty),
                    )
                )
            probabilities = [rung.get("probability_above") for rung in raw_rungs]
            p_above = probabilities if all(value is not None for value in probabilities) else None
            result = analyze_position(thresholds, legs, p_above)
            response = result.to_dict()
            response["selected_leg_count"] = len(legs)
            return jsonify(response)
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 422

    return app


if __name__ == "__main__":
    app = create_app()
    debug = os.environ.get("TEASEL_DEBUG", "").lower() in {"1", "true", "yes"}
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5050")),
        debug=debug,
        use_reloader=False,
    )
