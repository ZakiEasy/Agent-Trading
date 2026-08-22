import unittest
from src.institutional_engine import (
    get_macro_sentiment_barometer,
    compute_institutional_rmax_sizing,
    generate_8_step_protocol_analysis,
    scan_watchlist_institutional
)

class TestInstitutionalEngine(unittest.TestCase):
    def test_macro_barometer(self):
        baro = get_macro_sentiment_barometer(force_refresh=False)
        self.assertIsNotNone(baro)
        self.assertIn("regime", baro)
        self.assertIn(baro["regime"], ["RISK-ON", "NEUTRE", "RISK-OFF"])
        self.assertIn("vix", baro)
        self.assertIn("dxy", baro)
        self.assertIn("xly_xlp_ratio", baro)
        self.assertIn("wti_oil", baro)
        self.assertIn("yield_curve", baro)

    def test_rmax_sizing_rules(self):
        capital = 18193.05
        entry = 100.0
        stop = 97.0 # 3.0% dist
        tp = 102.5  # 2.5% dist

        res = compute_institutional_rmax_sizing(capital, entry, stop, tp)
        self.assertEqual(res["capital_total"], capital)
        self.assertAlmostEqual(res["r_max_allowed_eur"], capital * 0.01, places=2)
        self.assertLessEqual(res["risk_monetary_eur"], res["r_max_allowed_eur"] + 0.01)
        self.assertLessEqual(res["suggested_allocation_eur"], res["max_position_allowed_eur"] + 0.01)
        self.assertAlmostEqual(res["cash_reserve_required_eur"], capital * 0.25, places=2)

    def test_protocol_8_steps_structure(self):
        analysis = generate_8_step_protocol_analysis("RMS.PA", capital_total=18193.05)
        self.assertIsNotNone(analysis)
        self.assertIn("symbol", analysis)
        self.assertIn("confluence_score", analysis)
        self.assertTrue(0.0 <= analysis["confluence_score"] <= 10.0)
        self.assertIn("verdict", analysis)
        self.assertIn("steps", analysis)
        self.assertEqual(len(analysis["steps"]), 8)
        
        step_titles = [s["title"] for s in analysis["steps"]]
        self.assertTrue(any("Sharia" in t for t in step_titles))
        self.assertTrue(any("Macro" in t or "Trend Following" in t for t in step_titles))
        self.assertTrue(any("Repli" in t or "Event-Driven" in t for t in step_titles))
        self.assertTrue(any("Fondamentaux" in t for t in step_titles))
        self.assertTrue(any("Timing" in t or "Breakout" in t for t in step_titles))
        self.assertTrue(any("Plan de Trade" in t for t in step_titles))
        self.assertTrue(any("Dimensionnement" in t or "R-Max" in t for t in step_titles))
        self.assertTrue(any("Verdict" in t for t in step_titles))

if __name__ == "__main__":
    unittest.main()
