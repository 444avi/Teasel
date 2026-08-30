import unittest

from app import create_app


class AppTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_home_and_demo(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        demo = self.client.get("/api/demo").get_json()
        self.assertEqual(len(demo["rungs"]), 4)

    def test_pinned_position_through_api_with_fees(self):
        demo = self.client.get("/api/demo").get_json()
        sides = ["NO", "YES", "NO", "YES"]
        for rung, side in zip(demo["rungs"], sides, strict=True):
            rung.update(side=side, qty=1)
        response = self.client.post("/api/analyze", json={"rungs": demo["rungs"], "fee_rate": .07})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["total_cost_cents"], 180)
        self.assertEqual(result["max_loss_cents"], 80)
        self.assertEqual([item["net_cents"] for item in result["bins"]], [20, -80, 20, -80, 20])

    def test_maker_toggle_reproduces_fee_free_acceptance_case(self):
        demo = self.client.get("/api/demo").get_json()
        sides = ["NO", "YES", "NO", "YES"]
        for rung, side in zip(demo["rungs"], sides, strict=True):
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


if __name__ == "__main__":
    unittest.main()
