from decimal import Decimal
import json
import unittest

from teasel.options import OptionLeg, analyze_options


D = Decimal


def call(direction, strike, premium, **kwargs):
    return OptionLeg("CALL", direction, kwargs.pop("qty", 1), D(strike), premium, **kwargs)


def put(direction, strike, premium, **kwargs):
    return OptionLeg("PUT", direction, kwargs.pop("qty", 1), D(strike), premium, **kwargs)


def codes(result):
    return [guard.code for guard in result.guards]


BULL_CALL_SPREAD = [call("LONG", "100", 600), call("SHORT", "110", 200)]


class OptionsGoldenCaseTests(unittest.TestCase):
    def test_01_long_call(self):
        legs = [call("LONG", "100", 500)]
        result = analyze_options(legs)
        self.assertEqual(result.net_premium_cents, -50_000)
        self.assertEqual(result.max_loss_cents, 50_000)
        self.assertTrue(result.max_profit_unlimited)
        self.assertIsNone(result.max_profit_cents)
        self.assertEqual(result.breakevens, (D("105"),))
        self.assertEqual(analyze_options(legs, spot=D("120")).pl_at_spot_cents, 150_000)

    def test_02_short_put(self):
        result = analyze_options([put("SHORT", "50", 200)])
        self.assertEqual(result.net_premium_cents, 20_000)
        self.assertEqual(result.max_profit_cents, 20_000)
        self.assertEqual(result.max_loss_cents, 480_000)
        self.assertFalse(result.max_loss_unlimited)
        self.assertEqual(result.breakevens, (D("48"),))

    def test_03_bull_call_spread(self):
        result = analyze_options(BULL_CALL_SPREAD)
        self.assertEqual(result.net_premium_cents, -40_000)
        self.assertEqual(result.max_loss_cents, 40_000)
        self.assertEqual(result.max_profit_cents, 60_000)
        self.assertEqual(result.breakevens, (D("104"),))
        self.assertEqual(result.upside_slope_cents_per_dollar, 0)

    def test_04_chart_range_and_vertices(self):
        result = analyze_options(BULL_CALL_SPREAD)
        self.assertEqual(result.price_range, (D("89"), D("121")))
        self.assertEqual(
            result.vertices,
            ((D("89"), -40_000), (D("100"), -40_000), (D("104"), 0), (D("110"), 60_000), (D("121"), 60_000)),
        )

    def test_05_pl_at_spot(self):
        self.assertEqual(analyze_options(BULL_CALL_SPREAD, spot=D("103")).pl_at_spot_cents, -10_000)

    def test_06_iron_condor(self):
        result = analyze_options([
            put("LONG", "90", 100), put("SHORT", "95", 250),
            call("SHORT", "105", 250), call("LONG", "110", 100),
        ])
        self.assertEqual(result.net_premium_cents, 30_000)
        self.assertEqual(result.max_profit_cents, 30_000)
        self.assertEqual(result.max_loss_cents, 20_000)
        self.assertEqual(result.breakevens, (D("92"), D("108")))

    def test_07_covered_call(self):
        result = analyze_options([
            OptionLeg("STOCK", "LONG", 100, None, 10_000),
            call("SHORT", "105", 300),
        ])
        self.assertEqual(result.max_profit_cents, 80_000)
        self.assertEqual(result.max_loss_cents, 970_000)
        self.assertEqual(result.breakevens, (D("97"),))
        self.assertEqual(result.upside_slope_cents_per_dollar, 0)
        self.assertNotIn("UNLIMITED_LOSS", codes(result))

    def test_08_long_straddle(self):
        result = analyze_options([call("LONG", "100", 400), put("LONG", "100", 350)])
        self.assertEqual(result.max_loss_cents, 75_000)
        self.assertTrue(result.max_profit_unlimited)
        self.assertEqual(result.breakevens, (D("92.5"), D("107.5")))

    def test_09_short_call(self):
        result = analyze_options([call("SHORT", "100", 300)])
        self.assertTrue(result.max_loss_unlimited)
        self.assertIsNone(result.max_loss_cents)
        self.assertEqual(result.max_profit_cents, 30_000)
        self.assertEqual(result.breakevens, (D("103"),))
        guard = result.guards[0]
        self.assertEqual(guard.code, "UNLIMITED_LOSS")
        self.assertEqual(guard.severity, "warning")
        self.assertEqual(guard.leg_indexes, (0,))

    def test_10_commission(self):
        result = analyze_options([call("LONG", "100", 500, commission_cents=65)])
        self.assertEqual(result.commissions_cents, 65)
        self.assertEqual(result.max_loss_cents, 50_065)
        self.assertEqual(result.breakevens, (D("105.0065"),))

    def test_11_multiplier(self):
        result = analyze_options([call("LONG", "100", 500, multiplier=10)])
        self.assertEqual(result.net_premium_cents, -5_000)

    def test_12_zero_premium_long_call(self):
        result = analyze_options([call("LONG", "100", 0)])
        self.assertEqual(result.breakevens, ())
        self.assertEqual(result.breakeven_ranges, ((D("0"), D("100")),))
        self.assertIn("ZERO_PREMIUM", codes(result))

    def test_13_zero_premium_straddle_touches_zero(self):
        result = analyze_options([call("LONG", "100", 0), put("LONG", "100", 0)])
        self.assertEqual(result.breakevens, (D("100"),))
        self.assertEqual(result.breakeven_ranges, ())

    def test_14_guaranteed_profit(self):
        result = analyze_options([call("LONG", "100", 500), call("SHORT", "100", 700)])
        self.assertIn("GUARANTEED_PROFIT", codes(result))
        self.assertEqual(result.max_loss_cents, 0)

    def test_15_premium_below_intrinsic(self):
        result = analyze_options([put("LONG", "100", 300)], spot=D("90"))
        guards = [guard for guard in result.guards if guard.code == "PREMIUM_BELOW_INTRINSIC"]
        self.assertEqual(len(guards), 1)
        self.assertEqual(guards[0].leg_indexes, (0,))
        self.assertEqual(guards[0].message, "Leg 1 premium is below its intrinsic value at the entered spot.")


class OptionsGuardTests(unittest.TestCase):
    def test_no_profit_possible_is_info(self):
        result = analyze_options([put("LONG", "100", 10_000)])
        self.assertEqual(codes(result), ["NO_PROFIT_POSSIBLE"])
        self.assertEqual(result.guards[0].severity, "info")

    def test_mixed_multipliers(self):
        result = analyze_options([call("LONG", "100", 500), call("SHORT", "110", 200, multiplier=10)])
        self.assertIn("MIXED_MULTIPLIERS", codes(result))

    def test_guard_order(self):
        result = analyze_options(
            [call("SHORT", "100", 0), call("SHORT", "110", 0, multiplier=10)], spot=D("120"),
        )
        self.assertEqual(
            codes(result),
            ["UNLIMITED_LOSS", "NO_PROFIT_POSSIBLE", "PREMIUM_BELOW_INTRINSIC",
             "PREMIUM_BELOW_INTRINSIC", "ZERO_PREMIUM", "MIXED_MULTIPLIERS"],
        )
        self.assertEqual(result.guards[4].leg_indexes, (0, 1))

    def test_guard_messages_have_no_em_dashes(self):
        result = analyze_options(
            [call("SHORT", "100", 0), call("SHORT", "110", 0, multiplier=10)], spot=D("120"),
        )
        self.assertTrue(all("—" not in guard.message for guard in result.guards))


class OptionsEdgeCaseTests(unittest.TestCase):
    def test_zero_range_extends_to_infinity(self):
        result = analyze_options([call("LONG", "100", 500), call("SHORT", "100", 500)])
        self.assertEqual(result.breakeven_ranges, ((D("0"), None),))
        self.assertEqual(result.breakevens, ())

    def test_vertices_split_gain_and_loss(self):
        result = analyze_options([call("LONG", "100", 400), put("LONG", "100", 350)])
        for (_, left), (_, right) in zip(result.vertices, result.vertices[1:]):
            self.assertTrue((left >= 0 and right >= 0) or (left <= 0 and right <= 0))

    def test_price_range_override(self):
        result = analyze_options(BULL_CALL_SPREAD, price_range=(D("95"), D("105")))
        self.assertEqual(result.price_range, (D("95"), D("105")))
        self.assertEqual(result.vertices[0], (D("95"), -40_000))
        self.assertEqual(result.vertices[-1], (D("105"), 10_000))
        with self.assertRaises(ValueError):
            analyze_options(BULL_CALL_SPREAD, price_range=(D("105"), D("95")))
        with self.assertRaises(ValueError):
            analyze_options(BULL_CALL_SPREAD, price_range=(D("-1"), D("95")))

    def test_spot_validation(self):
        for spot in (D("0"), D("-1"), D("100.001")):
            with self.assertRaises(ValueError):
                analyze_options(BULL_CALL_SPREAD, spot=spot)


class OptionsValidationTests(unittest.TestCase):
    def test_16_leg_validation(self):
        invalid = [
            dict(kind="FUTURE", direction="LONG", qty=1, strike=D("100"), premium_cents=0),
            dict(kind="CALL", direction="SIDEWAYS", qty=1, strike=D("100"), premium_cents=0),
            dict(kind="CALL", direction="LONG", qty=1.5, strike=D("100"), premium_cents=0),
            dict(kind="CALL", direction="LONG", qty=0, strike=D("100"), premium_cents=0),
            dict(kind="CALL", direction="LONG", qty=1, strike=None, premium_cents=0),
            dict(kind="PUT", direction="LONG", qty=1, strike=D("0"), premium_cents=0),
            dict(kind="PUT", direction="LONG", qty=1, strike=D("-5"), premium_cents=0),
            dict(kind="STOCK", direction="LONG", qty=1, strike=D("100"), premium_cents=0),
            dict(kind="CALL", direction="LONG", qty=1, strike=D("100.001"), premium_cents=0),
            dict(kind="CALL", direction="LONG", qty=1, strike=D("100"), premium_cents=1.5),
            dict(kind="CALL", direction="LONG", qty=1, strike=D("100"), premium_cents=-1),
            dict(kind="CALL", direction="LONG", qty=1, strike=D("100"), premium_cents=0, multiplier=0),
            dict(kind="CALL", direction="LONG", qty=1, strike=D("100"), premium_cents=0, multiplier="100"),
            dict(kind="CALL", direction="LONG", qty=1, strike=D("100"), premium_cents=0, commission_cents=-1),
            dict(kind="CALL", direction="LONG", qty=1, strike=D("100"), premium_cents=0, commission_cents=0.5),
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                OptionLeg(**kwargs)
        with self.assertRaisesRegex(ValueError, "at least one leg is required"):
            analyze_options([])

    def test_normalizes_case_and_strike(self):
        leg = OptionLeg("call", "short", 1, "100.50", 0)
        self.assertEqual((leg.kind, leg.direction, leg.strike), ("CALL", "SHORT", D("100.50")))
        self.assertEqual(leg.effective_multiplier, 100)
        self.assertEqual(OptionLeg("STOCK", "LONG", 5, None, 100, multiplier=100).effective_multiplier, 1)

    def test_17_serialization(self):
        payload = analyze_options([call("SHORT", "100", 300)]).to_dict()
        json.dumps(payload)
        self.assertIsNone(payload["max_loss_cents"])
        self.assertIsNone(payload["spot"])
        self.assertEqual(payload["breakevens"], [103.0])
        self.assertEqual(payload["guards"][0]["leg_indexes"], [0])
        ranges = analyze_options([call("LONG", "100", 500), call("SHORT", "100", 500)]).to_dict()["breakeven_ranges"]
        self.assertEqual(ranges, [[0.0, None]])
        json.dumps(ranges)


if __name__ == "__main__":
    unittest.main()
