from decimal import Decimal
import unittest

from teasel.engine import (
    FeeModel,
    Leg,
    analyze_position,
    build_bins,
    derive_bin_probabilities,
    derive_cumulative_probabilities,
)


THRESHOLDS = [Decimal("82.99"), Decimal("83.99"), Decimal("84.99"), Decimal("85.99")]


def oil_legs(fee_model: FeeModel | None = None):
    raw = [
        ("oil-1", "82.99", "NO", 26),
        ("oil-2", "83.99", "YES", 65),
        ("oil-3", "84.99", "NO", 67),
        ("oil-4", "85.99", "YES", 15),
    ]
    model = fee_model or FeeModel(use_maker=True)
    return [
        Leg(market_id, Decimal(threshold), side, 1, price, model.fee_cents(price, 1))
        for market_id, threshold, side, price in raw
    ]


class EngineTests(unittest.TestCase):
    def test_open_tail_bin_labels(self):
        bins = build_bins([10, 20])
        self.assertEqual([item.label for item in bins], ["≤ 10", "(10, 20]", "≥ 20"])
        self.assertIsNone(bins[0].lower)
        self.assertIsNone(bins[-1].upper)

    def test_pinned_comb_fee_free(self):
        result = analyze_position(THRESHOLDS, oil_legs(), [Decimal(".75"), ".64", ".36", ".17"])
        self.assertEqual(result.total_cost_cents, 173)
        self.assertEqual([item.gross_cents for item in result.bins], [200, 100, 200, 100, 200])
        self.assertEqual([item.net_cents for item in result.bins], [27, -73, 27, -73, 27])
        self.assertEqual(result.min_gross_cents, 100)
        self.assertEqual(result.max_loss_cents, 73)
        self.assertEqual(result.max_gain_cents, 27)
        self.assertEqual(result.market_ev_cents, Decimal("-3.00"))
        self.assertEqual(result.profit_probability, Decimal(".70"))
        self.assertFalse(result.arbitrage_alarm)

    def test_fee_pinning(self):
        model = FeeModel(rate=Decimal(".07"))
        legs = oil_legs(model)
        self.assertEqual([leg.fee_cents for leg in legs], [2, 2, 2, 1])
        result = analyze_position(THRESHOLDS, legs)
        self.assertEqual(result.total_fees_cents, 7)
        self.assertEqual(result.total_cost_cents, 180)
        self.assertEqual(result.max_loss_cents, 80)
        self.assertEqual(result.max_gain_cents, 20)

    def test_stacked_yes_is_a_ramp(self):
        thresholds = [10, 20, 30]
        legs = [Leg(f"m{i}", Decimal(t), "YES", 1, 10) for i, t in enumerate(thresholds)]
        result = analyze_position(thresholds, legs)
        self.assertEqual([item.gross_cents for item in result.bins], [0, 100, 200, 300])
        self.assertEqual([item.net_cents for item in result.bins], [-30, 70, 170, 270])

    def test_inverted_ladder_refuses_ev(self):
        probabilities = derive_bin_probabilities([Decimal(".70"), Decimal(".75")])
        self.assertFalse(probabilities.valid)
        self.assertIsNone(probabilities.bin_probabilities)
        result = analyze_position([10, 20], [Leg("m", Decimal(10), "YES", 1, 40)], [".70", ".75"])
        self.assertFalse(result.monotonicity_ok)
        self.assertIsNone(result.market_ev_cents)
        self.assertTrue(any("increases" in message for message in result.guard_messages))

    def test_bracket_probabilities_convert_to_cumulative(self):
        cumulative = derive_cumulative_probabilities([".25", ".11", ".28", ".19", ".17"])
        self.assertEqual(cumulative, (Decimal(".75"), Decimal(".64"), Decimal(".36"), Decimal(".17")))
        self.assertEqual(derive_bin_probabilities(cumulative).bin_probabilities, tuple(map(Decimal, [".25", ".11", ".28", ".19", ".17"])))

    def test_guaranteed_arbitrage_flag(self):
        legs = [
            Leg("yes", Decimal(10), "YES", 1, 40),
            Leg("no", Decimal(10), "NO", 1, 40),
        ]
        result = analyze_position([10], legs, [Decimal(".5")])
        self.assertEqual([item.gross_cents for item in result.bins], [100, 100])
        self.assertTrue(result.guaranteed_arbitrage)
        self.assertTrue(result.arbitrage_alarm)
        self.assertEqual(result.max_loss_cents, 0)
        self.assertEqual(result.max_gain_cents, 20)


if __name__ == "__main__":
    unittest.main()
