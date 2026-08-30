from decimal import Decimal
import unittest

from avilytics.kalshi import (
    extract_ask_cents,
    extract_threshold,
    ingest_markets,
    parse_event_ticker,
)


class IngestTests(unittest.TestCase):
    def test_parses_event_link(self):
        self.assertEqual(
            parse_event_ticker("https://kalshi.com/markets/kxwti/oil-price/KXWTI-26AUG30"),
            "KXWTI-26AUG30",
        )

    def test_derives_ask_from_opposite_bid(self):
        market = {"yes_bid_dollars": "0.34", "no_bid_dollars": "0.61"}
        self.assertEqual(extract_ask_cents(market, "yes"), Decimal(39))
        self.assertEqual(extract_ask_cents(market, "no"), Decimal(66))

    def test_prefers_direct_ask(self):
        market = {"yes_ask_dollars": "0.42", "no_bid_dollars": "0.61"}
        self.assertEqual(extract_ask_cents(market, "yes"), Decimal(42))

    def test_threshold_field_and_operator(self):
        info = extract_threshold({"ticker": "M1", "floor_strike": "82.99", "yes_sub_title": "Above $82.99"})
        self.assertEqual(info.threshold, Decimal("82.99"))
        self.assertEqual(info.operator, ">")

    def test_ingest_sorts_ladder_and_uses_mid(self):
        markets = [
            {"ticker": "M2", "floor_strike": "20", "yes_sub_title": "Above $20", "yes_bid": 30, "no_bid": 60, "status": "active", "event_title": "Example"},
            {"ticker": "M1", "floor_strike": "10", "yes_sub_title": "Above $10", "yes_bid": 60, "no_bid": 30, "status": "active", "event_title": "Example"},
        ]
        snapshot = ingest_markets("EXAMPLE", markets)
        self.assertEqual([r.threshold for r in snapshot.rungs], [Decimal(10), Decimal(20)])
        self.assertEqual([r.probability_above for r in snapshot.rungs], [Decimal(".65"), Decimal(".35")])
        self.assertEqual(snapshot.title, "Example")


if __name__ == "__main__":
    unittest.main()
