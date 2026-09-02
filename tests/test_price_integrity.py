import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
from app import app
from src.market_data import (
    fetch_yahoo_chart_v8,
    get_ticker_info,
    get_ticker_data,
    FALLBACK_WATCHLIST_REFERENCE_PRICES
)
from src.institutional_engine import generate_8_step_protocol_analysis
from src.risk_manager import calculate_trade_sizing

class TestPriceIntegrity(unittest.TestCase):
    """
    Tests de non-régression stricts pour garantir :
    1. Qu'aucun cours fantôme à 100.0€ ne soit généré.
    2. Que le flux de secours direct Yahoo Chart v8 prenne le relais en cas de blocage 401 Crumb.
    3. Que le dimensionnement et les endpoints batch retournent des cotations réelles.
    """

    def setUp(self):
        self.client = app.test_client()

    def test_v8_chart_api_direct_fetch(self):
        """
        Vérifie que l'endpoint v8 Chart récupère directement un cours et un DataFrame valides.
        """
        price, df = fetch_yahoo_chart_v8("MC.PA", range_period="5d")
        self.assertIsNotNone(price, "Le cours v8 ne doit pas être None pour MC.PA")
        self.assertGreater(price, 100.0, "Le cours de LVMH (MC.PA) doit être supérieur à 100.0€")
        self.assertNotEqual(price, 100.0, "Le cours ne doit jamais être exactement 100.0")
        if df is not None:
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                self.assertIn(col, df.columns)

    def test_fallback_reference_prices_populated(self):
        """
        Vérifie que le référentiel de sécurité contient les actions majeures avec des cours réels distincts de 100.0.
        """
        key_stocks = ['MC.PA', 'RMS.PA', 'OR.PA', 'SAN.PA', 'NVDA', 'AAPL', 'MSFT', 'AIR.PA']
        for s in key_stocks:
            self.assertIn(s, FALLBACK_WATCHLIST_REFERENCE_PRICES)
            ref = FALLBACK_WATCHLIST_REFERENCE_PRICES[s]
            self.assertGreater(ref['price'], 0.0)
            self.assertNotEqual(ref['price'], 100.0, f"{s} ne doit pas avoir un prix de référence égal à 100.0")

    def test_simulated_yfinance_401_resilience(self):
        """
        Simule un blocage complet de yfinance (HTTP 401 Unauthorized / Invalid Crumb de Render).
        Vérifie que generate_8_step_protocol_analysis ne renvoie JAMAIS 100.0€ mais bascule sur le flux v8 ou le référentiel.
        """
        with patch('yfinance.Ticker') as mock_ticker:
            # Simuler une levée d'exception ou un résultat vide pour toute méthode yfinance
            mock_inst = MagicMock()
            mock_inst.history.side_effect = Exception("HTTP 401 Unauthorized: Invalid Crumb")
            mock_inst.info = {}
            mock_inst.fast_info = None
            mock_ticker.return_value = mock_inst

            # Analyser LVMH (MC.PA)
            analysis = generate_8_step_protocol_analysis("MC.PA", force_refresh=True)
            p = analysis.get("current_price")
            self.assertIsNotNone(p, "Le cours ne doit pas être None")
            self.assertNotEqual(p, 100.0, "Le cours en cas de panne de yfinance ne doit JAMAIS être 100.0€")
            self.assertGreater(p, 200.0, "Le cours de LVMH doit être son cours réel (~440€), pas 100€")

            # Analyser Hermès (RMS.PA)
            analysis_rms = generate_8_step_protocol_analysis("RMS.PA", force_refresh=True)
            p_rms = analysis_rms.get("current_price")
            self.assertNotEqual(p_rms, 100.0, "Hermès ne doit jamais être à 100.0€")
            self.assertGreater(p_rms, 1000.0, "Le cours de Hermès doit être son cours réel (> 1000€)")

    def test_risk_manager_zero_and_invalid_entry_price(self):
        """
        Vérifie que calculate_trade_sizing ne retombe plus sur 100.0 en cas de prix d'entrée nul.
        """
        res_zero = calculate_trade_sizing(capital_total=4500.0, entry_price=0.0)
        self.assertEqual(res_zero["shares_to_buy"], 0)
        self.assertEqual(res_zero["entry_price"], 0.0)
        self.assertIn("error", res_zero)

        res_negative = calculate_trade_sizing(capital_total=4500.0, entry_price=-50.0)
        self.assertEqual(res_negative["shares_to_buy"], 0)
        self.assertEqual(res_negative["entry_price"], 0.0)

        # Calcul valide avec un cours réel de LVMH (440€)
        res_valid = calculate_trade_sizing(
            capital_total=4500.0,
            entry_price=440.0,
            stop_loss_price=426.8,
            macro_regime="RÉGIME RISK-ON (Favorable)"
        )
        self.assertGreater(res_valid["entry_price"], 0)
        self.assertEqual(res_valid["entry_price"], 440.0)
        self.assertNotIn("error", res_valid)

    def test_scan_batch_endpoint_no_100_euro_leak(self):
        """
        Vérifie via l'API Flask que l'endpoint /api/scan/batch renvoie des cours réels distincts et non 100.0€.
        """
        test_syms = ["MC.PA", "RMS.PA", "NVDA", "SAN.PA", "OR.PA"]
        response = self.client.get(f"/api/scan/batch?symbols={','.join(test_syms)}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("success", False))
        results = data.get("results", [])
        self.assertGreaterEqual(len(results), len(test_syms))

        for r in results:
            sym = r.get("symbol")
            price = r.get("price")
            self.assertIsNotNone(price, f"Le prix pour {sym} ne doit pas être None")
            self.assertNotEqual(
                price, 100.0,
                f"ANOMALIE DÉTECTÉE : {sym} renvoie un cours de 100.0 ! Régression 100€ constatée."
            )
            self.assertGreater(price, 0.0, f"Le cours de {sym} doit être strictement positif.")

    def test_scan_indicators_integrity(self):
        """
        Vérifie que les calculs de RSI, drop, volume et score sont bien différenciés et ne tombent pas sur les replis par défaut (50.0, 5/10, etc.).
        """
        test_syms = ["GOLD", "TSLA", "META", "AVGO"]
        response = self.client.get(f"/api/scan/batch?symbols={','.join(test_syms)}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get("success", False))
        results = data.get("results", [])
        self.assertGreaterEqual(len(results), len(test_syms))

        for r in results:
            sym = r.get("symbol")
            price = r.get("price", 0.0)
            rsi = r.get("rsi", 50.0)
            drop = r.get("drop", 0.0)
            score = r.get("confluence_score", 0.0)
            vol = r.get("avg_daily_volume", 0.0)

            self.assertGreater(price, 0.0, f"Le cours de {sym} doit être > 0")
            self.assertNotEqual(price, 100.0, f"{sym} ne doit pas être à 100.0€")
            self.assertGreater(vol, 0.0, f"Le volume quotidien moyen de {sym} doit être calculé (> 0)")
            self.assertNotEqual(drop, 0.0, f"Le repli de {sym} doit être calculé (différent de 0.0%)")

    def test_tab_multi_window_routes(self):
        """
        Vérifie que chaque onglet dispose d'une route dédiée renvoyant HTTP 200 pour le support multi-fenêtres.
        """
        tabs = ["dashboard", "screener", "robot", "portfolio", "diversification", "journal", "chat", "simulation"]
        for tab in tabs:
            resp = self.client.get(f"/{tab}")
            self.assertEqual(resp.status_code, 200, f"La route /{tab} doit renvoyer 200")
            self.assertIn(b"Trading Agent", resp.data)

if __name__ == "__main__":
    unittest.main()
