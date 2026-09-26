import time
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import jwt
from arboretum_auth import AuthConfig
from arboretum_auth.errors import VerificationUnavailable
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import create_app
from app import MAX_EVENT_CHARS, MAX_JSON_BYTES, MAX_OPTION_LEGS, MAX_RUNGS
from teasel.rate_limit import RateLimiter


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_key = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        cls.public_key = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.rate_db = os.path.join(self.tempdir.name, "requests.sqlite3")
        config = AuthConfig(
            issuer="https://accounts.example", login_url="https://accounts.example/login",
            refresh_url="https://accounts.example/refresh", public_key=self.public_key,
            allowed_return_hosts=("localhost",), return_origin="http://localhost",
        )
        self.app = create_app(
            auth_config=config, testing=True,
            rate_limiter=RateLimiter(self.rate_db, {"fetch": 12, "analyze": 120, "options_analyze": 120}),
        )
        self.client = self.app.test_client()

    def _token(self, **overrides):
        now = int(time.time())
        claims = {
            "iss": "https://accounts.example", "aud": "arboretum-tools",
            "sub": "acct_1", "email": "verified@example.com", "plan": "free",
            "wos_user_id": "user_1", "sid": "session_1", "iat": now, "exp": now + 900,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": "test"})

    def _authenticate(self, **overrides):
        self.client.set_cookie("arb_session", self._token(**overrides))

    def test_health_is_public(self):
        self.assertEqual(self.client.get("/healthz").get_json(), {"status": "ok"})

    def test_production_configuration_fails_closed(self):
        env = {
            "TEASEL_ENV": "production",
            "TEASEL_PUBLIC_URL": "https://teasel.arboretuminvestments.net",
            "ARBORETUM_ACCOUNTS_URL": "https://accounts.arboretuminvestments.net",
            "ARBORETUM_ISSUER": "https://accounts.arboretuminvestments.net",
            "ARBORETUM_AUDIENCE": "arboretum-tools",
            "ARBORETUM_JWKS_URL": "https://accounts.arboretuminvestments.net/.well-known/jwks.json",
            "ARBORETUM_ALLOWED_RETURN_HOSTS": "teasel.arboretuminvestments.net",
            "ARBORETUM_RETURN_ORIGIN": "https://teasel.arboretuminvestments.net",
            "TEASEL_RATE_LIMIT_DB": self.rate_db,
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(create_app().config["TRUSTED_HOSTS"], ["teasel.arboretuminvestments.net"])
        env["ARBORETUM_ISSUER"] = "https://wrong.example"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                create_app()
        env.pop("ARBORETUM_RETURN_ORIGIN")
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                create_app()

    def test_anonymous_routes_fail_closed(self):
        home = self.client.get("/?view=example")
        self.assertEqual(home.status_code, 302)
        self.assertEqual(urlsplit(home.headers["Location"]).netloc, "accounts.example")
        target = parse_qs(urlsplit(home.headers["Location"]).query)["return_to"][0]
        self.assertEqual(target, "http://localhost/?view=example&_arb_auth_attempt=1")
        for path, method in (("/api/demo", "get"), ("/api/fetch", "post"), ("/api/analyze", "post")):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, json={} if method == "post" else None)
                self.assertEqual(response.status_code, 401)
                self.assertTrue(response.is_json)
                self.assertIn("/refresh", response.get_json()["auth_url"])
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_valid_free_session_can_use_all_routes(self):
        self._authenticate()
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"verified@example.com", home.data)
        self.assertIn(b"Free", home.data)
        self.assertEqual(home.headers["Cache-Control"], "private, no-store")
        self.assertEqual(len(self.client.get("/api/demo").get_json()["rungs"]), 4)
        with patch("app.fetch_ladder") as fetch:
            fetch.return_value.to_dict.return_value = {"rungs": [], "event_ticker": "TEST"}
            self.assertEqual(self.client.post("/api/fetch", json={"event": "TEST"}).status_code, 200)
        rungs = self.client.get("/api/demo").get_json()["rungs"]
        self.assertEqual(self.client.post("/api/analyze", json={"rungs": rungs}).status_code, 200)

    def test_premium_and_max_can_use_teasel(self):
        for plan in ("premium", "max"):
            with self.subTest(plan=plan):
                self._authenticate(plan=plan)
                self.assertEqual(self.client.get("/").status_code, 200)
                self.assertEqual(self.client.get("/api/demo").status_code, 200)
                rungs = self.client.get("/api/demo").get_json()["rungs"]
                self.assertEqual(self.client.post("/api/analyze", json={"rungs": rungs}).status_code, 200)

    def test_bad_signature_issuer_audience_and_expiry(self):
        bad_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode({"iss": "https://accounts.example", "aud": "arboretum-tools", "sub": "acct_1", "iat": 1, "exp": 9999999999}, bad_key, algorithm="RS256")
        now = int(time.time())
        tokens = [forged, self._token(iss="https://wrong.example"), self._token(aud="wrong"), self._token(iat=now - 10000, exp=now - 9000)]
        for token in tokens:
            with self.subTest(token=token[:20]):
                self.client.set_cookie("arb_session", token)
                self.assertEqual(self.client.get("/api/demo").status_code, 401)

    def test_expired_session_refreshes_without_loop(self):
        now = int(time.time())
        self._authenticate(iat=now - 10000, exp=now - 9000)
        response = self.client.get("/?example=1")
        self.assertIn("/refresh", response.headers["Location"])
        self.assertEqual(self.client.get("/?example=1&_arb_auth_attempt=1").status_code, 401)
        self._authenticate()
        response = self.client.get("/?example=1&_arb_auth_attempt=1")
        self.assertEqual(response.headers["Location"], "http://localhost/?example=1")

    def test_verifier_outage_is_503_and_keeps_cookie(self):
        self._authenticate()
        auth = self.app.extensions["arboretum_auth"]
        with patch.object(auth.verifier, "verify", side_effect=VerificationUnavailable("offline")):
            for path in ("/", "/api/demo"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("Set-Cookie", response.headers)

    def test_safe_links_and_origin(self):
        auth = self.app.extensions["arboretum_auth"]
        self.assertEqual(parse_qs(urlsplit(auth.login_redirect("https://evil.example/")).query)["return_to"], ["/"])
        self.assertEqual(self.client.get("/", headers={"Host": "evil.example"}).status_code, 400)
        self._authenticate()
        home = self.client.get("/").data
        self.assertIn(
            b'href="https://accounts.example/account?return_to=http%3A%2F%2F127.0.0.1%3A5050%2F"',
            home,
        )
        self.assertIn(b'href="https://accounts.example/logout"', home)
        self.assertNotIn(b"INTERNAL RESEARCH", home)
        self.assertEqual(self.client.post("/api/analyze", json={"rungs": []}, headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_limits_are_shared_by_two_workers_and_keyed_by_account_id(self):
        tick = [1000]
        first = RateLimiter(self.rate_db, {"fetch": 2, "analyze": 3}, clock=lambda: tick[0])
        second = RateLimiter(self.rate_db, {"fetch": 2, "analyze": 3}, clock=lambda: tick[0])
        config = self.app.extensions["arboretum_auth"].config
        worker_a = create_app(auth_config=config, rate_limiter=first, testing=True).test_client()
        worker_b = create_app(auth_config=config, rate_limiter=second, testing=True).test_client()
        token = self._token(sub="acct_1", email="same@example.com")
        worker_a.set_cookie("arb_session", token)
        worker_b.set_cookie("arb_session", token)
        with patch("app.fetch_ladder") as fetch:
            fetch.return_value.to_dict.return_value = {"event_ticker": "TEST"}
            self.assertEqual(worker_a.post("/api/fetch", json={"event": "TEST"}).status_code, 200)
            self.assertEqual(worker_b.post("/api/fetch", json={"event": "TEST"}).status_code, 200)
            limited = worker_a.post("/api/fetch", json={"event": "TEST"})
            self.assertEqual(limited.status_code, 429)
            self.assertTrue(limited.is_json)
            self.assertEqual(limited.headers["Retry-After"], "20")
            self.assertEqual(fetch.call_count, 2)
            worker_b.set_cookie("arb_session", self._token(sub="acct_2", email="same@example.com"))
            self.assertEqual(worker_b.post("/api/fetch", json={"event": "TEST"}).status_code, 200)
            tick[0] += 20
            self.assertEqual(worker_a.post("/api/fetch", json={"event": "TEST"}).status_code, 200)

        rungs = worker_a.get("/api/demo").get_json()["rungs"]
        for _ in range(3):
            self.assertEqual(worker_a.post("/api/analyze", json={"rungs": rungs}).status_code, 200)
        self.assertEqual(worker_a.post("/api/analyze", json={"rungs": rungs}).status_code, 429)

    def test_atomic_concurrent_counter(self):
        limiter_a = RateLimiter(self.rate_db, {"fetch": 3}, clock=lambda: 1200)
        limiter_b = RateLimiter(self.rate_db, {"fetch": 3}, clock=lambda: 1200)
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda n: (limiter_a if n % 2 else limiter_b).consume("acct_1", "fetch"), range(12)))
        self.assertEqual(outcomes.count(None), 3)
        self.assertEqual(outcomes.count(60), 9)

    def test_input_caps_and_unauthenticated_requests(self):
        anonymous = self.client.post("/api/fetch", json={"event": "x" * (MAX_EVENT_CHARS + 1)})
        self.assertEqual(anonymous.status_code, 401)
        self._authenticate()
        self.assertEqual(
            self.client.post("/api/fetch", json={"event": "x" * (MAX_EVENT_CHARS + 1)}).status_code,
            422,
        )
        self.assertEqual(
            self.client.post("/api/analyze", json={"rungs": [{}] * (MAX_RUNGS + 1)}).status_code,
            422,
        )
        for endpoint in ("fetch", "analyze"):
            response = self.client.post(
                f"/api/{endpoint}", data=b"x" * (MAX_JSON_BYTES + 1),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 413)
            self.assertTrue(response.is_json)

    def test_rate_storage_outage_fails_closed(self):
        self._authenticate()
        limiter = self.app.extensions["teasel_rate_limiter"]
        with patch.object(limiter, "consume", side_effect=sqlite3.OperationalError("disk unavailable")):
            response = self.client.post("/api/fetch", json={"event": "TEST"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Retry-After", response.headers)

    def test_home_and_demo(self):
        self._authenticate()
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"Arboretum Investments", home.data)
        demo = self.client.get("/api/demo").get_json()
        self.assertEqual(len(demo["rungs"]), 4)

    def test_pinned_position_through_api_with_fees(self):
        self._authenticate()
        demo = self.client.get("/api/demo").get_json()
        sides = ["NO", "YES", "NO", "YES"]
        for rung, side in zip(demo["rungs"], sides):
            rung.update(side=side, qty=1)
        response = self.client.post("/api/analyze", json={"rungs": demo["rungs"], "fee_rate": .07})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["selected_leg_count"], 4)
        self.assertEqual(result["total_cost_cents"], 180)
        self.assertEqual(result["max_loss_cents"], 80)
        self.assertEqual([item["net_cents"] for item in result["bins"]], [20, -80, 20, -80, 20])

    def test_maker_toggle_reproduces_fee_free_acceptance_case(self):
        self._authenticate()
        demo = self.client.get("/api/demo").get_json()
        sides = ["NO", "YES", "NO", "YES"]
        for rung, side in zip(demo["rungs"], sides):
            rung.update(side=side, qty=1)
        response = self.client.post(
            "/api/analyze",
            json={"rungs": demo["rungs"], "fee_rate": .07, "use_maker": True},
        )
        result = response.get_json()
        self.assertEqual(result["total_cost_cents"], 173)
        self.assertEqual(result["total_fees_cents"], 0)
        self.assertEqual(result["market_ev_cents"], -3.0)
        self.assertEqual(result["profit_probability"], .7)

    BULL_CALL_SPREAD = {
        "spot": "103",
        "legs": [
            {"kind": "CALL", "direction": "LONG", "qty": 1, "strike": "100", "premium": "6.00"},
            {"kind": "CALL", "direction": "SHORT", "qty": 1, "strike": "110", "premium": "2.00"},
        ],
    }

    def test_anonymous_options_routes_fail_closed(self):
        page = self.client.get("/options")
        self.assertEqual(page.status_code, 302)
        self.assertEqual(urlsplit(page.headers["Location"]).netloc, "accounts.example")
        for path, method in (
            ("/api/options/analyze", "post"), ("/api/options/preset", "post"), ("/api/options/presets", "get"),
        ):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, json={} if method == "post" else None)
                self.assertEqual(response.status_code, 401)
                self.assertTrue(response.is_json)
                self.assertIn("auth_url", response.get_json())

    def test_options_analyze_golden_spread(self):
        self._authenticate()
        response = self.client.post("/api/options/analyze", json=self.BULL_CALL_SPREAD)
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["max_profit_cents"], 60000)
        self.assertEqual(result["max_loss_cents"], 40000)
        self.assertEqual(result["breakevens"], [104.0])
        self.assertEqual(result["pl_at_spot_cents"], -10000)
        numeric = {"legs": [dict(leg, strike=float(leg["strike"]), premium=float(leg["premium"]))
                            for leg in self.BULL_CALL_SPREAD["legs"]]}
        self.assertEqual(self.client.post("/api/options/analyze", json=numeric).get_json()["breakevens"], [104.0])

    def test_options_leg_caps(self):
        self._authenticate()
        leg = self.BULL_CALL_SPREAD["legs"][0]
        cases = {
            "too many legs": [leg] * (MAX_OPTION_LEGS + 1),
            "qty 0": [dict(leg, qty=0)],
            "qty over cap": [dict(leg, qty=10_001)],
            "strike precision": [dict(leg, strike="100.001")],
            "negative premium": [dict(leg, premium="-1")],
            "unknown kind": [dict(leg, kind="FUTURE")],
            "bad direction": [dict(leg, direction="UP")],
            "zero strike": [dict(leg, strike="0")],
            "multiplier over cap": [dict(leg, multiplier=1001)],
            "commission over cap": [dict(leg, commission="10000.01")],
            "no legs": [],
        }
        for name, legs in cases.items():
            with self.subTest(case=name):
                response = self.client.post("/api/options/analyze", json={"legs": legs})
                self.assertEqual(response.status_code, 422)
                self.assertIn("error", response.get_json())
        self.assertEqual(
            self.client.post("/api/options/analyze", json={"legs": [leg] * 17}).get_json()["error"],
            "At most 16 legs are allowed.",
        )
        self.assertTrue(
            self.client.post("/api/options/analyze", json={"legs": [leg, dict(leg, qty=0)]})
            .get_json()["error"].startswith("Leg 2:")
        )
        self.assertEqual(self.client.post("/api/options/analyze", json=[]).status_code, 422)
        stock = {"kind": "STOCK", "direction": "LONG", "qty": 1_000_000, "premium": "100"}
        self.assertEqual(self.client.post("/api/options/analyze", json={"legs": [stock]}).status_code, 200)
        stock["qty"] = 1_000_001
        self.assertEqual(self.client.post("/api/options/analyze", json={"legs": [stock]}).status_code, 422)

    def test_options_body_cap_and_origin(self):
        self._authenticate()
        for path in ("/api/options/analyze", "/api/options/preset"):
            with self.subTest(path=path):
                response = self.client.post(path, data=b"x" * (MAX_JSON_BYTES + 1), content_type="application/json")
                self.assertEqual(response.status_code, 413)
                self.assertTrue(response.is_json)
                response = self.client.post(path, json=self.BULL_CALL_SPREAD, headers={"Origin": "https://evil.example"})
                self.assertEqual(response.status_code, 403)

    def test_options_rate_bucket_is_separate(self):
        limiter = RateLimiter(self.rate_db, {"fetch": 12, "analyze": 120, "options_analyze": 2})
        config = self.app.extensions["arboretum_auth"].config
        client = create_app(auth_config=config, rate_limiter=limiter, testing=True).test_client()
        client.set_cookie("arb_session", self._token())
        for _ in range(2):
            self.assertEqual(client.post("/api/options/analyze", json=self.BULL_CALL_SPREAD).status_code, 200)
        limited = client.post("/api/options/analyze", json=self.BULL_CALL_SPREAD)
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)
        preset = client.post("/api/options/preset", json={"name": "long_call", "spot": "100", "width": "5", "qty": 1})
        self.assertEqual(preset.status_code, 429)
        rungs = client.get("/api/demo").get_json()["rungs"]
        self.assertEqual(client.post("/api/analyze", json={"rungs": rungs}).status_code, 200)
        self.assertEqual(client.get("/api/options/presets").status_code, 200)

    def test_options_presets_feed_analyze(self):
        self._authenticate()
        catalog = self.client.get("/api/options/presets").get_json()["presets"]
        self.assertEqual(catalog[0], {"name": "long_call", "label": "Long call"})
        response = self.client.post(
            "/api/options/preset", json={"name": "iron_condor", "spot": "101.37", "width": "5", "qty": 1},
        )
        self.assertEqual(response.status_code, 200)
        legs = response.get_json()["legs"]
        self.assertEqual(len(legs), 4)
        self.assertEqual([leg["strike"] for leg in legs], ["90", "95", "105", "110"])
        analyzed = self.client.post("/api/options/analyze", json={"spot": "101.37", "legs": legs})
        self.assertEqual(analyzed.status_code, 200)
        self.assertIn("ZERO_PREMIUM", [guard["code"] for guard in analyzed.get_json()["guards"]])
        unknown = self.client.post("/api/options/preset", json={"name": "nope", "spot": "100", "width": "5"})
        self.assertEqual(unknown.status_code, 422)
        bad_strike = self.client.post("/api/options/preset", json={"name": "bear_put_spread", "spot": "3", "width": "5"})
        self.assertEqual(bad_strike.status_code, 422)


if __name__ == "__main__":
    unittest.main()
