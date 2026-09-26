from decimal import Decimal
import unittest

from teasel.options import analyze_options
from teasel.options_presets import build_preset, preset_catalog


SPOT = Decimal("101.37")
WIDTH = Decimal("5")


class OptionsPresetTests(unittest.TestCase):
    def test_every_preset_builds_around_atm_100(self):
        catalog = preset_catalog()
        self.assertEqual(len(catalog), 10)
        self.assertEqual(catalog[0], {"name": "long_call", "label": "Long call"})
        for entry in catalog:
            with self.subTest(preset=entry["name"]):
                legs = build_preset(entry["name"], SPOT, WIDTH)
                self.assertTrue(legs)
                analyze_options(legs)
        self.assertEqual(build_preset("long_call", SPOT, WIDTH)[0].strike, Decimal("100"))

    def test_iron_condor_strikes(self):
        legs = build_preset("iron_condor", SPOT, WIDTH)
        self.assertEqual([leg.strike for leg in legs], [Decimal(90), Decimal(95), Decimal(105), Decimal(110)])
        self.assertEqual([leg.direction for leg in legs], ["LONG", "SHORT", "SHORT", "LONG"])
        self.assertTrue(all(leg.premium_cents == 0 and leg.multiplier == 100 for leg in legs))

    def test_covered_call_stock_leg(self):
        stock = build_preset("covered_call", SPOT, WIDTH)[0]
        self.assertEqual((stock.kind, stock.qty, stock.premium_cents, stock.strike), ("STOCK", 100, 10_137, None))

    def test_butterfly_middle_leg_and_qty_scaling(self):
        legs = build_preset("call_butterfly", SPOT, WIDTH, qty=3)
        self.assertEqual([leg.qty for leg in legs], [3, 6, 3])
        self.assertEqual(build_preset("call_butterfly", SPOT, WIDTH)[1].qty, 2)

    def test_rejects_non_positive_strike(self):
        with self.assertRaises(ValueError):
            build_preset("bear_put_spread", Decimal("3"), Decimal("5"))

    def test_rejects_bad_inputs(self):
        bad = [
            ("no_such_preset", SPOT, WIDTH, 1),
            ("long_call", SPOT, Decimal("0"), 1),
            ("long_call", SPOT, Decimal("0.001"), 1),
            ("long_call", Decimal("-1"), WIDTH, 1),
            ("long_call", Decimal("1.234"), WIDTH, 1),
            ("long_call", SPOT, WIDTH, 0),
            ("long_call", SPOT, WIDTH, 10_001),
        ]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(ValueError):
                build_preset(*args)


if __name__ == "__main__":
    unittest.main()
