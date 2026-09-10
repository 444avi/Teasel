from __future__ import annotations

import os
from decimal import Decimal

from flask import Flask, jsonify, render_template, request

from teasel.engine import FeeModel, Leg, analyze_position
from teasel.kalshi import KalshiIngestError, fetch_ladder


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


def create_app() -> Flask:
    app = Flask(__name__)
    # This is a local research tool; UI edits should appear without requiring
    # the long-running snapshot server to be restarted.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/demo")
    def demo():
        return jsonify(_demo_snapshot())

    @app.post("/api/fetch")
    def fetch_event():
        payload = request.get_json(silent=True) or {}
        try:
            snapshot = fetch_ladder(
                str(payload.get("event") or ""),
                str(payload.get("probability_source") or "mid"),
            )
            return jsonify(snapshot.to_dict())
        except KalshiIngestError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception:
            app.logger.exception("unexpected Kalshi fetch error")
            return jsonify({"error": "The Kalshi snapshot could not be fetched right now."}), 502

    @app.post("/api/analyze")
    def analyze():
        payload = request.get_json(silent=True) or {}
        try:
            raw_rungs = payload.get("rungs") or []
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


app = create_app()

if __name__ == "__main__":
    debug = os.environ.get("TEASEL_DEBUG", "").lower() in {"1", "true", "yes"}
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5050")),
        debug=debug,
        use_reloader=False,
    )
