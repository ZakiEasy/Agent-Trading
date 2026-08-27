import os
import sys
import json
import math
from pathlib import Path

# Ajouter le répertoire racine au PYTHONPATH
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from src.config import (
    DEFAULT_WATCHLIST,
    DEFAULT_MARKET_POOL,
    SECTOR_ETFS,
    MIN_DROP_PCT,
    MAX_DROP_PCT,
    LOOKBACK_DAYS,
    TARGET_TP1_DEFAULT,
    TARGET_TP2_DEFAULT,
    HOLDING_PERIOD_DAYS,
    CAPITAL_REFERENCE_DEFAULT,
    R_MAX_PCT_STANDARD,
    R_MAX_PCT_REDUCED,
    MAX_ALLOCATION_PER_LINE_PCT,
    MIN_CASH_RESERVE_PCT,
    MAX_SIMULTANEOUS_POSITIONS,
    MAX_SECTOR_POSITIONS,
    MIN_AVG_DAILY_VOLUME_USD,
    MIN_MARKET_CAP_USD
)
from src.market_data import categorize_ticker, calculate_rsi
from src.sharia_screen import screen_ticker

# Cache directory for historical data
DATA_CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"
DATA_CACHE_DIR.mkdir(exist_ok=True)

HISTORICAL_PERIODS_1999_2026 = {
    # 1. Stress-Tests Grandes Crises & Krachs
    "crisis_2000": {
        "id": "crisis_2000",
        "name": "💥 Krach Dot-Com & Déflation Tech (1999-2003)",
        "start": "1999-01-01",
        "end": "2003-12-31",
        "category": "Grandes Crises",
        "description": "Éclatement de la bulle spéculative internet (-80% Nasdaq, -50% S&P 500)"
    },
    "crisis_2008": {
        "id": "crisis_2008",
        "name": "🏦 Grande Crise Financière (GFC) & Subprimes (2007-2009)",
        "start": "2007-06-01",
        "end": "2009-12-31",
        "category": "Grandes Crises",
        "description": "Faillite de Lehman Brothers et récession bancaire mondiale (-55% S&P 500, VIX > 80)"
    },
    "crisis_2020": {
        "id": "crisis_2020",
        "name": "🦠 Krach Éclair Covid-19 (2020)",
        "start": "2020-01-01",
        "end": "2020-12-31",
        "category": "Grandes Crises",
        "description": "Choc pandémique et confinements mondiaux (-35% en 1 mois, VIX > 80)"
    },
    "crisis_2022": {
        "id": "crisis_2022",
        "name": "📉 Bear Market Inflation & Choc de Taux (2022)",
        "start": "2021-11-01",
        "end": "2022-12-31",
        "category": "Grandes Crises",
        "description": "Resserrement monétaire historique des banques centrales (-35% Nasdaq, -20% S&P 500)"
    },

    # 2. Cycles Macroéconomiques Clés
    "cycle_2003_2007": {
        "id": "cycle_2003_2007",
        "name": "🐂 Super-Cycle Matières Premières & Crédit (2003-2007)",
        "start": "2003-01-01",
        "end": "2007-06-01",
        "category": "Cycles Macro",
        "description": "Croissance mondiale synchronisée, boom des émergents et de l'immobilier"
    },
    "cycle_2010_2015": {
        "id": "cycle_2010_2015",
        "name": "🇪🇺 Crise Dette Euro & Reprise Quantitative Easing (2010-2015)",
        "start": "2010-01-01",
        "end": "2015-12-31",
        "category": "Cycles Macro",
        "description": "Tensions sur les dettes souveraines européennes et politiques accommodantes Fed/BCE"
    },
    "cycle_2015_2019": {
        "id": "cycle_2015_2019",
        "name": "🌐 Guerre Commerciale US-Chine & Volatilité (2015-2019)",
        "start": "2015-01-01",
        "end": "2019-12-31",
        "category": "Cycles Macro",
        "description": "Choc déflationniste chinois (2015), tensions douanières et baisse des taux"
    },
    "cycle_2023_2026": {
        "id": "cycle_2023_2026",
        "name": "🤖 Boom Intelligence Artificielle & Nouveaux Sommets (2023-2026)",
        "start": "2023-01-01",
        "end": "2026-08-20",
        "category": "Cycles Macro",
        "description": "Rallye technologique mondial porté par l'IA générative et les semi-conducteurs"
    },

    # 3. Grandes Décennies
    "decade_2000s": {
        "id": "decade_2000s",
        "name": "📅 Décennie 2000-2009 : Les 2 Grands Krachs (Dot-Com + GFC)",
        "start": "1999-01-01",
        "end": "2009-12-31",
        "category": "Décennies",
        "description": "10 ans de forte turbulence boursière et de dégonflement d'actifs"
    },
    "decade_2010s": {
        "id": "decade_2010s",
        "name": "📅 Décennie 2010-2019 : Le Grand Marché Haussier Post-Crise",
        "start": "2010-01-01",
        "end": "2019-12-31",
        "category": "Décennies",
        "description": "10 ans de hausse ininterrompue des actions sous régime de taux zéro"
    },
    "decade_2020s": {
        "id": "decade_2020s",
        "name": "📅 Décennie 2020-2026 : Hyper-Volatilité, Covid & Révolution IA",
        "start": "2020-01-01",
        "end": "2026-08-20",
        "category": "Décennies",
        "description": "Cycles macro express : Pandémie, choc inflationniste et essor de l'IA"
    },

    # 4. Cycle Global Exhaustif
    "all_cycles": {
        "id": "all_cycles",
        "name": "🌐 Cycle Complet Multi-Décennies (1999-2026 / 27 ans)",
        "start": "1999-01-01",
        "end": "2026-08-20",
        "category": "Cycle Global",
        "description": "Test exhaustif sur 27 ans incluant 4 krachs majeurs et 4 grands marchés haussiers"
    }
}

CRISIS_PERIODS = HISTORICAL_PERIODS_1999_2026

class BacktestEngine:
    """
    Moteur de Backtest Walk-Forward pour la stratégie de Swing Trading (V2 Standard & V3 Institutionnelle).
    Supporte l'analyse multi-décennies (1999 à 2026) et les stress-tests sur toutes les crises historiques.
    """

    def __init__(self, symbols=None, period="2y", start_date=None, end_date=None, initial_capital=5000.0, tp1_pct=1.25, tp2_pct=2.25, max_holding_days=10, strategy="v3_institutional"):
        self.symbols = symbols or list(set(DEFAULT_WATCHLIST + DEFAULT_MARKET_POOL))
        self.period = period
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = float(initial_capital)
        self.tp1_pct = float(tp1_pct)
        self.tp2_pct = float(tp2_pct)
        self.max_holding_days = int(max_holding_days)
        self.strategy = strategy or "v3_institutional"
        self.historical_data = {}
        self.macro_data = {}
        self.sector_etf_data = {}
        self.macro_daily_regime = {}

        # Résolution des périodes historiques prédéfinies
        VALID_YF_PERIODS = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]
        if self.period in HISTORICAL_PERIODS_1999_2026:
            c_info = HISTORICAL_PERIODS_1999_2026[self.period]
            self.start_date = c_info['start']
            self.end_date = c_info['end']
            self.fetch_period = "max"
        elif self.period in VALID_YF_PERIODS and not self.start_date:
            self.fetch_period = self.period
        else:
            self.fetch_period = "max"

    def fetch_historical_universe(self, force_refresh=False):
        """
        Télécharge et met en cache l'historique OHLCV pour tous les symboles, indices macro et ETFs sectoriels.
        Inclut les 5 piliers du Baromètre Macroéconomique (VIX, DXY, Ratio XLY/XLP, SPY, Pétrole WTI, Taux US).
        """
        macro_series = ["^VIX", "SPY", "DX-Y.NYB", "CL=F", "^TNX", "XLY", "XLP"]
        all_tickers = list(set(self.symbols + list(SECTOR_ETFS.values()) + macro_series))
        p_name = self.fetch_period if (self.fetch_period in ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"] and not self.start_date) else "max"
        print(f"📥 Téléchargement / Chargement historique pour {len(all_tickers)} actifs (période: {p_name})...")
        
        for ticker in all_tickers:
            cache_file = DATA_CACHE_DIR / f"{ticker.replace('^', '_').replace('=', '_')}_{p_name}.csv"
            df = None
            if not force_refresh and cache_file.exists():
                try:
                    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                except Exception as e:
                    df = None

            if df is None or df.empty:
                try:
                    t = yf.Ticker(ticker)
                    df = t.history(period=p_name, interval="1d")
                    if not df.empty:
                        # Clean column names
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(0)
                        # Remove timezone for clean date matching
                        if hasattr(df.index, 'tz') and df.index.tz is not None:
                            df.index = df.index.tz_localize(None)
                        df.to_csv(cache_file)
                except Exception as e:
                    print(f"⚠️ Erreur téléchargement pour {ticker}: {e}")
                    df = pd.DataFrame()

            if df is not None and not df.empty:
                # Harmoniser le fuseau horaire
                if hasattr(df.index, 'tz') and df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                # Precompute technical indicators
                df = self._precompute_indicators(df)
                if ticker in macro_series or ticker in ["^VIX", "SPY", "DX-Y.NYB", "CL=F", "^TNX"]:
                    self.macro_data[ticker] = df
                if ticker in SECTOR_ETFS.values() or ticker in ["XLY", "XLP"]:
                    self.sector_etf_data[ticker] = df
                if ticker not in macro_series and ticker not in SECTOR_ETFS.values():
                    self.historical_data[ticker] = df

        print(f"✅ Données prêtes : {len(self.historical_data)} actions, {len(self.sector_etf_data)} ETFs, {len(self.macro_data)} indices macro.")
        self._precompute_macro_regimes()
        return len(self.historical_data) > 0

    def _precompute_indicators(self, df):
        """
        Calcule les indicateurs clés sur chaque série temporelle :
        - SMA 200 (Daily)
        - SMA 50 (Hebdo approximé)
        - RSI 14
        - Mèche basse (%)
        - Volume moyen 20 jours
        - Variation 1j, 2j, 3j
        """
        if len(df) < 20:
            return df

        df = df.copy()
        df['SMA_200'] = df['Close'].rolling(window=200, min_periods=20).mean()
        df['SMA_50'] = df['Close'].rolling(window=50, min_periods=10).mean()
        df['SMA_20_Vol'] = df['Volume'].rolling(window=20, min_periods=5).mean()
        df['Daily_Turnover'] = df['Close'] * df['Volume']
        df['SMA_20_Turnover'] = df['Daily_Turnover'].rolling(window=20, min_periods=5).mean()

        # RSI 14
        close_vals = df['Close'].values
        df['RSI_14'] = calculate_rsi(close_vals, period=14)

        # Mèche basse = (min(Open, Close) - Low) / Open * 100
        min_body = np.minimum(df['Open'].values, df['Close'].values)
        lower_wick = (min_body - df['Low'].values) / np.where(df['Open'].values > 0, df['Open'].values, 1) * 100
        df['Lower_Wick_Pct'] = lower_wick

        # Retours glissants pour détection de Dip (-3% à -8%)
        df['Return_1d'] = df['Close'].pct_change(1) * 100
        df['Return_2d'] = df['Close'].pct_change(2) * 100
        df['Return_3d'] = df['Close'].pct_change(3) * 100

        # ATR 14 (Average True Range)
        h = df['High']
        l = df['Low']
        c_prev = df['Close'].shift(1)
        tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
        df['ATR_14'] = tr.rolling(window=14, min_periods=5).mean()

        # Fibonacci 50% & 61.8% sur les 60 derniers jours
        h_60 = df['High'].rolling(window=60, min_periods=10).max()
        l_60 = df['Low'].rolling(window=60, min_periods=10).min()
        diff_60 = h_60 - l_60
        df['Fib_50'] = h_60 - (0.500 * diff_60)
        df['Fib_61_8'] = h_60 - (0.618 * diff_60)

        # Plus bas sur 20 jours (Support approximé)
        df['Support_20d'] = df['Low'].rolling(window=20, min_periods=5).min()
        df['Resistance_20d'] = df['High'].rolling(window=20, min_periods=5).max()

        return df

    def _precompute_macro_regimes(self):
        """
        Précalcule de manière vectorisée le régime macroéconomique (5 piliers) pour chaque date de l'historique :
        1. VIX (^VIX) : Volatilité et sentiment de marché
        2. DXY (DX-Y.NYB) : Dollar Index / liquidités mondiales
        3. Ratio XLY / XLP : Appétit pour le risque vs rotation défensive
        4. SPY vs SMA 200 : Tendance structurelle de marché
        5. Pétrole WTI (CL=F) : Choc inflationniste
        """
        self.macro_daily_regime = {}

        spy_df = self.macro_data.get("SPY")
        vix_df = self.macro_data.get("^VIX")
        dxy_df = self.macro_data.get("DX-Y.NYB")
        wti_df = self.macro_data.get("CL=F")
        xly_df = self.sector_etf_data.get("XLY")
        xlp_df = self.sector_etf_data.get("XLP")

        if spy_df is None or spy_df.empty:
            return

        # Ratio XLY / XLP
        xly_xlp_chg5 = None
        if xly_df is not None and xlp_df is not None and not xly_df.empty and not xlp_df.empty:
            common = xly_df.index.intersection(xlp_df.index)
            if len(common) > 10:
                s_ratio = xly_df.loc[common, 'Close'] / xlp_df.loc[common, 'Close']
                xly_xlp_chg5 = s_ratio.pct_change(5)

        # WTI 20d variation
        wti_chg20 = None
        if wti_df is not None and not wti_df.empty:
            wti_chg20 = wti_df['Close'].pct_change(20) * 100

        for date in spy_df.index:
            score = 0
            vix_val = None
            spy_above_sma = True

            # 1. VIX
            if vix_df is not None and date in vix_df.index:
                vix_val = float(vix_df.loc[date]['Close'])
                if vix_val > 35:
                    score -= 3 # Panique extrême / Risk-Off immédiat
                elif vix_val >= 22:
                    score -= 1 # Zone de vigilance / stress
                elif vix_val <= 18:
                    score += 1 # Marché calme / Risk-On

            # 2. DXY
            if dxy_df is not None and date in dxy_df.index:
                dxy_val = float(dxy_df.loc[date]['Close'])
                if dxy_val < 102:
                    score += 1
                elif dxy_val > 105:
                    score -= 1

            # 3. Ratio XLY / XLP
            if xly_xlp_chg5 is not None and date in xly_xlp_chg5.index:
                c5 = float(xly_xlp_chg5.loc[date])
                if not pd.isna(c5):
                    if c5 > 0:
                        score += 1
                    elif c5 < 0:
                        score -= 1

            # 4. SPY vs SMA 200
            spy_close = float(spy_df.loc[date]['Close'])
            spy_sma = float(spy_df.loc[date]['SMA_200']) if not pd.isna(spy_df.loc[date]['SMA_200']) else spy_close
            spy_above_sma = (spy_close >= spy_sma)
            if spy_above_sma:
                score += 1
            else:
                score -= 1

            # 5. Pétrole WTI
            if wti_chg20 is not None and date in wti_chg20.index:
                w20 = float(wti_chg20.loc[date])
                if not pd.isna(w20) and w20 > 20.0:
                    score -= 1

            # Verdict Final
            if (vix_val is not None and vix_val > 35) or score <= -2 or (not spy_above_sma and vix_val is not None and vix_val > 25):
                regime = "RISK-OFF"
                rate = 0.0
            elif score >= 2 and (vix_val is None or vix_val <= 22) and spy_above_sma:
                regime = "RISK-ON"
                rate = R_MAX_PCT_STANDARD
            else:
                regime = "NEUTRE"
                rate = R_MAX_PCT_REDUCED

            self.macro_daily_regime[date] = (regime, rate, score)

    def run_simulation(self):
        """
        Exécute la simulation chronologique (walk-forward bar-by-bar).
        """
        if not self.historical_data:
            self.fetch_historical_universe()

        if not self.historical_data:
            return {"success": False, "error": "Aucune donnée historique disponible pour le backtest."}

        if not self.macro_daily_regime:
            self._precompute_macro_regimes()

        # Aligner toutes les dates communes
        all_dates = set()
        for df in self.historical_data.values():
            all_dates.update(df.index)
        sorted_dates = sorted(list(all_dates))

        # Filtrage par dates si spécifié (ex: crises 1999, 2008, 2020, 2022)
        if self.start_date:
            ts_start = pd.to_datetime(self.start_date)
            sorted_dates = [d for d in sorted_dates if d >= ts_start]
        elif self.period in ["1mo", "3mo", "6mo", "1y", "2y", "3y", "5y", "10y", "ytd"]:
            days_map = {"1mo": 22, "3mo": 66, "6mo": 126, "1y": 252, "2y": 504, "3y": 756, "5y": 1260, "10y": 2520, "ytd": 170}
            n_days = days_map.get(self.period, 504)
            if len(sorted_dates) > n_days:
                sorted_dates = sorted_dates[-n_days:]

        if self.end_date:
            ts_end = pd.to_datetime(self.end_date)
            sorted_dates = [d for d in sorted_dates if d <= ts_end]

        # Ne commencer la simulation qu'après les barres de chauffe si pas de date de début précise
        if not self.start_date and len(sorted_dates) > 60 and self.period == "max":
            simulation_dates = sorted_dates[50:]
        else:
            simulation_dates = sorted_dates

        # État du portefeuille
        current_cash = self.initial_capital
        active_positions = [] # Liste de dicts
        closed_trades = []    # Liste de dicts
        daily_equity = []     # Historique date -> total equity

        # Pré-évaluation Sharia (Cache mémoire global)
        global _GLOBAL_SHARIA_CACHE, _GLOBAL_CATEGORY_CACHE
        if '_GLOBAL_SHARIA_CACHE' not in globals():
            _GLOBAL_SHARIA_CACHE = {}
        if '_GLOBAL_CATEGORY_CACHE' not in globals():
            _GLOBAL_CATEGORY_CACHE = {}

        sharia_cache = {}
        for sym in self.symbols:
            if sym not in _GLOBAL_SHARIA_CACHE:
                _GLOBAL_SHARIA_CACHE[sym] = screen_ticker(sym)
            sharia_cache[sym] = _GLOBAL_SHARIA_CACHE[sym]

        # Pré-évaluation Catégorie & ETF sectoriel
        category_cache = {}
        for sym in self.symbols:
            if sym not in _GLOBAL_CATEGORY_CACHE:
                cat_info = categorize_ticker(sym)
                sec_name = cat_info.get("category", "Autres")
                etf_symbol = SECTOR_ETFS.get(sec_name, "SPY")
                _GLOBAL_CATEGORY_CACHE[sym] = {
                    "category": sec_name,
                    "is_pea": cat_info.get("is_pea", False),
                    "sector_etf": etf_symbol
                }
            category_cache[sym] = _GLOBAL_CATEGORY_CACHE[sym]

        # Pré-extraction ultra-rapide des données sous forme de dictionnaires date -> dict
        sym_fast_data = {}
        for sym, df in self.historical_data.items():
            dates_list = df.index
            c_arr = df['Close'].values
            o_arr = df['Open'].values
            h_arr = df['High'].values
            l_arr = df['Low'].values
            s200_arr = df['SMA_200'].values if 'SMA_200' in df.columns else np.zeros(len(df))
            s50_arr = df['SMA_50'].values if 'SMA_50' in df.columns else np.zeros(len(df))
            r14_arr = df['RSI_14'].values if 'RSI_14' in df.columns else np.full(len(df), 50.0)
            to_arr = df['SMA_20_Turnover'].values if 'SMA_20_Turnover' in df.columns else np.full(len(df), 10_000_000.0)
            lw_arr = df['Lower_Wick_Pct'].values if 'Lower_Wick_Pct' in df.columns else np.zeros(len(df))
            atr_arr = df['ATR_14'].values if 'ATR_14' in df.columns else (c_arr * 0.02)
            f50_arr = df['Fib_50'].values if 'Fib_50' in df.columns else (c_arr * 0.98)
            f61_arr = df['Fib_61_8'].values if 'Fib_61_8' in df.columns else (c_arr * 0.96)
            r1_arr = df['Return_1d'].values if 'Return_1d' in df.columns else np.zeros(len(df))
            r2_arr = df['Return_2d'].values if 'Return_2d' in df.columns else np.zeros(len(df))
            r3_arr = df['Return_3d'].values if 'Return_3d' in df.columns else np.zeros(len(df))
            sup_arr = df['Support_20d'].values if 'Support_20d' in df.columns else (c_arr * 0.98)

            sym_map = {}
            for idx, dt in enumerate(dates_list):
                if idx < 20: continue
                sym_map[dt] = {
                    "Close": float(c_arr[idx]),
                    "Open": float(o_arr[idx]),
                    "High": float(h_arr[idx]),
                    "Low": float(l_arr[idx]),
                    "SMA_200": float(s200_arr[idx]) if not np.isnan(s200_arr[idx]) else 0.0,
                    "SMA_50": float(s50_arr[idx]) if not np.isnan(s50_arr[idx]) else 0.0,
                    "RSI_14": float(r14_arr[idx]) if not np.isnan(r14_arr[idx]) else 50.0,
                    "SMA_20_Turnover": float(to_arr[idx]) if not np.isnan(to_arr[idx]) else 10_000_000.0,
                    "Lower_Wick_Pct": float(lw_arr[idx]) if not np.isnan(lw_arr[idx]) else 0.0,
                    "ATR_14": float(atr_arr[idx]) if not np.isnan(atr_arr[idx]) else (float(c_arr[idx]) * 0.02),
                    "Fib_50": float(f50_arr[idx]) if not np.isnan(f50_arr[idx]) else (float(c_arr[idx]) * 0.98),
                    "Fib_61_8": float(f61_arr[idx]) if not np.isnan(f61_arr[idx]) else (float(c_arr[idx]) * 0.96),
                    "Return_1d": float(r1_arr[idx]) if not np.isnan(r1_arr[idx]) else 0.0,
                    "Return_2d": float(r2_arr[idx]) if not np.isnan(r2_arr[idx]) else 0.0,
                    "Return_3d": float(r3_arr[idx]) if not np.isnan(r3_arr[idx]) else 0.0,
                    "Support_20d": float(sup_arr[idx]) if not np.isnan(sup_arr[idx]) else (float(c_arr[idx]) * 0.98)
                }
            sym_fast_data[sym] = sym_map

        # Boucle journalière Walk-Forward
        for date in simulation_dates:
            # 1. Mise à jour des positions actives & vérification des sorties
            positions_to_keep = []
            for pos in active_positions:
                sym = pos['symbol']
                sym_dict = sym_fast_data.get(sym, {})
                row = sym_dict.get(date)
                if row is None:
                    positions_to_keep.append(pos)
                    continue

                high = row['High']
                low = row['Low']
                close = row['Close']
                open_p = row['Open']
                entry_p = pos['entry_price']
                shares = pos['shares']
                pos['days_held'] += 1
                days = pos['days_held']

                is_closed = False
                exit_price = 0.0
                exit_reason = ""

                # A. Vérification des Gaps d'ouverture (Open)
                if open_p >= pos['tp2_price']:
                    exit_price = open_p
                    exit_reason = "TP2 (+2.25%)"
                    is_closed = True
                elif open_p >= pos['tp1_price']:
                    exit_price = open_p
                    exit_reason = "TP1 (+1.25%)"
                    is_closed = True
                elif open_p <= pos['stop_loss']:
                    exit_price = open_p
                    exit_reason = "BREAKEVEN (0.0%)" if pos.get('is_breakeven', False) else "STOP_LOSS"
                    is_closed = True

                # B. Exécution des ordres Limites en séance (TP prioritaire si le cours monte)
                elif high >= pos['tp2_price']:
                    exit_price = pos['tp2_price']
                    exit_reason = "TP2 (+2.25%)"
                    is_closed = True
                elif high >= pos['tp1_price']:
                    exit_price = pos['tp1_price']
                    exit_reason = "TP1 (+1.25%)"
                    is_closed = True
                elif low <= pos['stop_loss']:
                    exit_price = pos['stop_loss']
                    exit_reason = "BREAKEVEN (0.0%)" if pos.get('is_breakeven', False) else "STOP_LOSS"
                    is_closed = True

                # C. Invalidation Temporelle (Time Stop J+10)
                elif days >= self.max_holding_days:
                    exit_price = close
                    exit_reason = "TIME_STOP (J+10)"
                    is_closed = True

                # D. Activation du Breakeven pour les jours suivants si la clôture est > +0.80% (Stratégies V3 & V4)
                if self.strategy in ["v3_institutional", "v4_sniper_swing"] and not is_closed:
                    if close >= entry_p * 1.008 and pos['stop_loss'] < entry_p:
                        pos['stop_loss'] = entry_p # Stop remonté au PRU pour la session suivante
                        pos['is_breakeven'] = True

                if is_closed:
                    pnl_amount = (exit_price - entry_p) * shares
                    pnl_pct = ((exit_price - entry_p) / entry_p) * 100
                    current_cash += (exit_price * shares)

                    closed_trades.append({
                        "symbol": sym,
                        "category": pos['category'],
                        "entry_date": pos['entry_date'].strftime("%Y-%m-%d"),
                        "entry_price": round(entry_p, 2),
                        "exit_date": date.strftime("%Y-%m-%d"),
                        "exit_price": round(exit_price, 2),
                        "shares": shares,
                        "nominal_invested": round(entry_p * shares, 2),
                        "pnl_amount": round(pnl_amount, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "exit_reason": exit_reason,
                        "days_held": days,
                        "r_multiple": round(pnl_amount / max(pos['r_max_amount'], 1), 2)
                    })
                else:
                    positions_to_keep.append(pos)

            active_positions = positions_to_keep

            # 2. Évaluation du Baromètre Macroéconomique (5 Piliers : VIX, DXY, XLY/XLP, SPY, WTI)
            macro_regime, r_max_rate, macro_score = self.macro_daily_regime.get(
                date, ("RISK-ON", R_MAX_PCT_STANDARD, 1)
            )

            # 3. Détection de nouveaux signaux d'achat (si slots disponibles et cash disponible)
            current_portfolio_value = current_cash + sum(
                (sym_fast_data.get(p['symbol'], {}).get(date, {}).get('Close', p['entry_price']) * p['shares'])
                for p in active_positions
            )

            # Réserve de cash minimale (25% pour V3/V4, 10% pour V2, 0% pour V1)
            if self.strategy in ["v3_institutional", "v4_sniper_swing"]:
                min_cash_pct = MIN_CASH_RESERVE_PCT
            elif self.strategy == "v2_standard":
                min_cash_pct = 0.10
            else:
                min_cash_pct = 0.05

            min_cash_required = current_portfolio_value * min_cash_pct
            available_cash_for_trades = max(0, current_cash - min_cash_required)

            is_v4 = (self.strategy == "v4_sniper_swing")
            is_v3 = (self.strategy == "v3_institutional")
            is_v1 = (self.strategy == "v1_classic")
            is_inst = is_v3 or is_v4

            can_trade_macro = (macro_regime != "RISK-OFF") if is_inst else True

            if can_trade_macro and len(active_positions) < MAX_SIMULTANEOUS_POSITIONS and available_cash_for_trades > 100:
                active_symbols = [p['symbol'] for p in active_positions]
                active_sectors = [p['category'] for p in active_positions]

                candidates = []

                for sym in self.symbols:
                    if sym in active_symbols:
                        continue

                    # Filtre Sharia AAOIFI (V3 & V4 uniquement)
                    if is_inst:
                        sh_res = sharia_cache.get(sym, {})
                        if sh_res.get("status") == "NON CONFORME":
                            continue

                    # Filtre Secteur (Max 2 positions par secteur)
                    cat_info = category_cache.get(sym, {})
                    sec_cat = cat_info.get("category", "Autres")
                    if is_inst and active_sectors.count(sec_cat) >= MAX_SECTOR_POSITIONS:
                        continue

                    sym_dict = sym_fast_data.get(sym, {})
                    row = sym_dict.get(date)
                    if row is None:
                        continue

                    close = row['Close']
                    sma_200 = row['SMA_200']
                    sma_50 = row['SMA_50']
                    rsi = row['RSI_14']
                    turnover = row['SMA_20_Turnover']
                    wick_pct = row['Lower_Wick_Pct']
                    atr_14 = row['ATR_14']
                    fib_50 = row['Fib_50']
                    fib_61_8 = row['Fib_61_8']

                    r1 = row['Return_1d']
                    r2 = row['Return_2d']
                    r3 = row['Return_3d']
                    min_ret = min(r1, r2, r3)

                    if is_v4:
                        # V4 Sniper & Swing : 5 Piliers Confluence
                        # 1. Tendance saine : Cours > MM200 (tolérance max 2%)
                        if sma_200 > 0 and close < (sma_200 * 0.98):
                            continue

                        # 2. Liquidité institutionnelle
                        if turnover < MIN_AVG_DAILY_VOLUME_USD:
                            continue

                        # 3. Repli Event-Driven de -2.5% à -8.0%
                        if not (-MAX_DROP_PCT <= min_ret <= -2.5):
                            continue

                        # 4. Confluence Fibonacci 50-61.8% & Filtre Manipulation M15/ATR
                        is_in_fibo = (close >= fib_61_8 * 0.985 and close <= fib_50 * 1.025)
                        daily_range = float(row['High'] - row['Low'])
                        ratio_atr = (daily_range / atr_14) if atr_14 > 0 else 0
                        has_open_manip = (ratio_atr >= 0.25 and wick_pct >= 0.6)
                        has_wick_reversal = wick_pct >= 0.75 or rsi <= 40

                        if not (is_in_fibo or has_open_manip or has_wick_reversal):
                            continue

                        # Support & Stop Loss serré sous creux d'invalidation (1.2% à 1.8% max)
                        support = row['Support_20d']
                        stop_loss = max(support * 0.995, close * 0.985)
                        stop_dist_pct = (close - stop_loss) / close

                        # Score de confluence
                        score = 7
                        if close > sma_200: score += 1
                        if is_in_fibo: score += 1
                        if has_open_manip: score += 1.5
                        if rsi <= 35: score += 1
                        if wick_pct >= 1.0: score += 1

                    elif is_v3:
                        # V3 Institutional : 3 Moteurs (Trend SMA200 + Event -3%/-8% + Breakout Confirmation)
                        if sma_200 > 0 and close < (sma_200 * 0.96):
                            continue
                        if turnover < MIN_AVG_DAILY_VOLUME_USD:
                            continue
                        if not (-MAX_DROP_PCT <= min_ret <= -MIN_DROP_PCT):
                            continue

                        min_wick_req = 0.9 if macro_regime == "NEUTRE" else 0.6
                        max_rsi_req = 40 if macro_regime == "NEUTRE" else 45
                        has_wick = wick_pct >= min_wick_req
                        has_rsi_rebound = rsi <= max_rsi_req
                        if not (has_wick or has_rsi_rebound):
                            continue

                        support = row['Support_20d']
                        stop_loss = min(support * 0.99, close * 0.965)
                        stop_dist_pct = (close - stop_loss) / close

                        score = 6
                        if close > sma_200: score += 1
                        if rsi < 35: score += 1
                        if has_wick: score += 1
                        if min_ret <= -4.0: score += 1

                    elif is_v1:
                        # V1 Classic : Signal RSI bas (< 30) ou croisement basique, Stop 5.0%
                        if rsi > 32 and min_ret > -2.0:
                            continue
                        stop_loss = close * 0.95
                        stop_dist_pct = 0.05
                        score = 4 if rsi <= 30 else 2

                    else:
                        # V2 Standard : Mean Reversion Simple (RSI < 38 ou Dip < -2.5%)
                        if rsi > 38 and min_ret > -2.5:
                            continue
                        stop_loss = close * 0.965
                        stop_dist_pct = 0.035
                        score = 5 if rsi <= 30 else 3

                    candidates.append({
                        "symbol": sym,
                        "score": score,
                        "category": sec_cat,
                        "close": close,
                        "stop_loss": stop_loss,
                        "stop_dist_pct": stop_dist_pct,
                        "rsi": rsi,
                        "drop_pct": min_ret
                    })

                # Trier les candidats par score décroissant
                candidates.sort(key=lambda x: x['score'], reverse=True)

                for cand in candidates:
                    if len(active_positions) >= MAX_SIMULTANEOUS_POSITIONS:
                        break

                    sym = cand['symbol']
                    close = cand['close']
                    stop_loss = cand['stop_loss']
                    stop_dist = cand['stop_dist_pct']

                    if is_inst:
                        # V3 & V4 : Calcul dimensionnement R-Max exact (1% du capital max par trade)
                        r_max_amount = current_portfolio_value * r_max_rate
                        max_nominal = current_portfolio_value * MAX_ALLOCATION_PER_LINE_PCT
                        suggested_nominal = min(r_max_amount / max(stop_dist, 0.012), max_nominal)
                    else:
                        # V1 & V2 : Allocation fixe par position (20% du capital)
                        r_max_amount = current_portfolio_value * 0.02
                        suggested_nominal = current_portfolio_value * 0.20

                    suggested_nominal = min(suggested_nominal, available_cash_for_trades)

                    shares = math.floor(suggested_nominal / close)
                    if shares <= 0:
                        continue

                    nominal_used = shares * close
                    current_cash -= nominal_used
                    available_cash_for_trades -= nominal_used

                    tp1_price = round(close * (1 + self.tp1_pct / 100), 2)
                    tp2_price = round(close * (1 + self.tp2_pct / 100), 2)

                    active_positions.append({
                        "symbol": sym,
                        "category": cand['category'],
                        "entry_date": date,
                        "entry_price": close,
                        "stop_loss": round(stop_loss, 2),
                        "tp1_price": tp1_price,
                        "tp2_price": tp2_price,
                        "shares": shares,
                        "r_max_amount": r_max_amount,
                        "days_held": 0
                    })

            # 4. Enregistrement de l'equity quotidienne (avec gestion des jours fériés croisés US/EU)
            total_positions_val = sum(
                (sym_fast_data.get(p['symbol'], {}).get(date, {}).get('Close', p['entry_price']) * p['shares'])
                for p in active_positions
            )

            daily_equity.append({
                "date": date.strftime("%Y-%m-%d"),
                "cash": round(current_cash, 2),
                "positions_value": round(total_positions_val, 2),
                "total_equity": round(current_cash + total_positions_val, 2),
                "active_positions_count": len(active_positions)
            })

        # Clôturer les positions restantes à la dernière date au cours de clôture
        if simulation_dates:
            last_date = simulation_dates[-1]
            for pos in active_positions:
                sym = pos['symbol']
                df = self.historical_data.get(sym)
                if df is not None:
                    sub_df = df.loc[:last_date]
                    close = float(sub_df.iloc[-1]['Close']) if not sub_df.empty else pos['entry_price']
                else:
                    close = pos['entry_price']
                pnl_amount = (close - pos['entry_price']) * pos['shares']
                pnl_pct = ((close - pos['entry_price']) / pos['entry_price']) * 100
                current_cash += (close * pos['shares'])

                closed_trades.append({
                    "symbol": sym,
                    "category": pos['category'],
                    "entry_date": pos['entry_date'].strftime("%Y-%m-%d"),
                    "entry_price": round(pos['entry_price'], 2),
                    "exit_date": last_date.strftime("%Y-%m-%d"),
                    "exit_price": round(close, 2),
                    "shares": pos['shares'],
                    "nominal_invested": round(pos['entry_price'] * pos['shares'], 2),
                    "pnl_amount": round(pnl_amount, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": "FIN_SIMULATION",
                    "days_held": pos['days_held'],
                    "r_multiple": round(pnl_amount / max(pos['r_max_amount'], 1), 2)
                })
            
            # Mettre à jour le dernier point d'equity après clôture totale
            if daily_equity:
                daily_equity[-1]['cash'] = round(current_cash, 2)
                daily_equity[-1]['positions_value'] = 0.0
                daily_equity[-1]['total_equity'] = round(current_cash, 2)
                daily_equity[-1]['active_positions_count'] = 0

        # Calcul des métriques globales
        metrics = self._compute_performance_metrics(closed_trades, daily_equity)
        diagnostics = self._diagnose_strategy(closed_trades, metrics)

        return {
            "success": True,
            "period": self.period,
            "initial_capital": self.initial_capital,
            "final_capital": round(daily_equity[-1]['total_equity'] if daily_equity else self.initial_capital, 2),
            "metrics": metrics,
            "diagnostics": diagnostics,
            "equity_curve": daily_equity,
            "trades": closed_trades
        }

    def _compute_performance_metrics(self, trades, equity_curve):
        """
        Calcule l'ensemble des KPIs financiers et statistiques de la stratégie.
        """
        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "total_net_pnl": 0.0,
                "total_return_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "avg_holding_days": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "payoff_ratio": 0.0,
                "expectancy_pct": 0.0
            }

        total_trades = len(trades)
        winning_trades = [t for t in trades if t['pnl_amount'] > 0]
        losing_trades = [t for t in trades if t['pnl_amount'] < 0]
        breakeven_trades = [t for t in trades if t['pnl_amount'] == 0]

        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0.0

        gross_profit = sum(t['pnl_amount'] for t in winning_trades)
        gross_loss = abs(sum(t['pnl_amount'] for t in losing_trades))
        net_pnl = gross_profit - gross_loss
        total_return_pct = (net_pnl / self.initial_capital) * 100

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        avg_win_pct = np.mean([t['pnl_pct'] for t in winning_trades]) if winning_trades else 0.0
        avg_loss_pct = np.mean([t['pnl_pct'] for t in losing_trades]) if losing_trades else 0.0
        avg_holding_days = np.mean([t['days_held'] for t in trades]) if trades else 0.0

        payoff_ratio = (abs(avg_win_pct) / abs(avg_loss_pct)) if abs(avg_loss_pct) > 0 else 0.0
        
        # Expectancy = (Win% * AvgWin) - (Loss% * AvgLoss)
        win_prob = win_count / total_trades if total_trades > 0 else 0.0
        loss_prob = loss_count / total_trades if total_trades > 0 else 0.0
        expectancy_pct = (win_prob * avg_win_pct) + (loss_prob * avg_loss_pct)

        # Calcul Max Drawdown sur l'equity curve
        max_dd_pct = 0.0
        if equity_curve:
            equities = [pt['total_equity'] for pt in equity_curve]
            peak = equities[0]
            for eq in equities:
                if eq > peak:
                    peak = eq
                dd = ((peak - eq) / peak) * 100
                if dd > max_dd_pct:
                    max_dd_pct = dd

        # Calcul Sharpe & Sortino (base journalière annualisée)
        sharpe = 0.0
        sortino = 0.0
        if len(equity_curve) > 2:
            eq_series = pd.Series([pt['total_equity'] for pt in equity_curve])
            daily_returns = eq_series.pct_change().dropna()
            mean_ret = daily_returns.mean()
            std_ret = daily_returns.std()
            neg_std_ret = daily_returns[daily_returns < 0].std()

            if std_ret > 0:
                sharpe = (mean_ret / std_ret) * np.sqrt(252)
            if neg_std_ret > 0:
                sortino = (mean_ret / neg_std_ret) * np.sqrt(252)

        # Répartition par motif de sortie
        reasons_count = {}
        for t in trades:
            r = t['exit_reason']
            reasons_count[r] = reasons_count.get(r, 0) + 1

        # Répartition par ticker
        ticker_stats = {}
        for t in trades:
            sym = t['symbol']
            if sym not in ticker_stats:
                ticker_stats[sym] = {"trades": 0, "wins": 0, "pnl": 0.0}
            ticker_stats[sym]["trades"] += 1
            if t['pnl_amount'] > 0:
                ticker_stats[sym]["wins"] += 1
            ticker_stats[sym]["pnl"] += t['pnl_amount']

        for sym, s in ticker_stats.items():
            s["win_rate"] = round((s["wins"] / s["trades"]) * 100, 1)
            s["pnl"] = round(s["pnl"], 2)

        return {
            "total_trades": total_trades,
            "winning_trades": win_count,
            "losing_trades": loss_count,
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "total_net_pnl": round(net_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "avg_holding_days": round(avg_holding_days, 1),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "payoff_ratio": round(payoff_ratio, 2),
            "expectancy_pct": round(expectancy_pct, 2),
            "exit_reasons": reasons_count,
            "ticker_stats": ticker_stats
        }

    def _diagnose_strategy(self, trades, metrics):
        """
        Analyse les points forts, les failles structurelles et propose des optimisations concrètes.
        """
        strengths = []
        flaws = []
        recommendations = []

        win_rate = metrics.get("win_rate_pct", 0)
        profit_factor = metrics.get("profit_factor", 0)
        max_dd = metrics.get("max_drawdown_pct", 0)
        reasons = metrics.get("exit_reasons", {})

        # 1. Évaluation globale
        if win_rate >= 65:
            strengths.append(f"Taux de réussite élevé ({win_rate}%) : Le filtre de Mean Reversion sur repli temporaire capture efficacement les rebonds.")
        elif win_rate < 55:
            flaws.append(f"Taux de réussite modéré ({win_rate}%) : Présence de faux signaux ou cassures de support.")

        if profit_factor >= 1.5:
            strengths.append(f"Facteur de profit solide ({profit_factor}) : Les gains cumulés surpassent nettement les pertes.")
        elif profit_factor < 1.1:
            flaws.append(f"Facteur de profit faible ({profit_factor}) : L'asymétrie entre Take Profit court (+1.25%) et Stop Loss (-3.0%) pénalise l'espérance mathématique.")

        if max_dd <= 5.0:
            strengths.append(f"Drawdown très maîtrisé ({max_dd}%) grâce au respect strict du dimensionnement R-Max (≤ 1.0%) et à la réserve de cash (25%).")
        else:
            flaws.append(f"Drawdown sensible ({max_dd}%) lors des phases de corrélation baissière généralisée.")

        # 2. Analyse du Time Stop (J+10)
        time_stops = reasons.get("TIME_STOP (J+10)", 0)
        total_t = metrics.get("total_trades", 1)
        time_stop_pct = (time_stops / total_t) * 100 if total_t > 0 else 0

        if time_stop_pct > 30:
            flaws.append(f"Fréquence élevée d'invalidation temporelle ({round(time_stop_pct, 1)}% des trades sortis à J+10) : Indique une stagnation prolongée après l'entrée.")
            recommendations.append("Optimiser le timing d'entrée : Exiger une mèche de rejet plus prononcée (≥ 1.0%) ou une hausse de volume pour éviter les actions qui latéralisent sans rebondir.")
        else:
            strengths.append(f"Efficacité du Time Stop : Seulement {round(time_stop_pct, 1)}% des positions atteignent les 10 jours, confirmant la rapidité du retour à la moyenne.")

        # 3. Asymétrie TP vs SL
        avg_win = metrics.get("avg_win_pct", 0)
        avg_loss = abs(metrics.get("avg_loss_pct", 0))
        if avg_loss > (avg_win * 1.5):
            flaws.append(f"Asymétrie défavorable : Perte moyenne (-{avg_loss}%) supérieure au gain moyen (+{avg_win}%). Pour être rentable, la stratégie nécessite un Win Rate > 68%.")
            recommendations.append("Introduire un Stop Suiveur (Trailing Stop) ou remonter le Stop à Breakeven (0.0%) dès que le titre atteint +0.8% de gain.")

        # 4. Filtre de marché baissier
        recommendations.append("Renforcer le filtre de régime VIX : En cas de VIX > 22, réduire la taille par ligne à 10% ou n'intervenir que sur les titres en surperformance sectorielle nette.")

        return {
            "strengths": strengths,
            "flaws": flaws,
            "recommendations": recommendations
        }

def run_all_crises_stress_test(initial_capital=5000.0, tp1_pct=1.25, tp2_pct=2.25, max_holding_days=10, strategy="v3_institutional"):
    """
    Exécute automatiquement le stress-test sur toutes les périodes historiques (1999 à 2026) :
    Grandes crises, cycles macro, grandes décennies et cycle complet 27 ans.
    """
    print("\n" + "="*80)
    print("🌪️  STRESS-TEST MULTI-CRISES & GRANDES DÉCENNIES (1999 - 2026)")
    print("="*80)

    # Pré-téléchargement global
    base_engine = BacktestEngine(period="max", initial_capital=initial_capital, tp1_pct=tp1_pct, tp2_pct=tp2_pct, max_holding_days=max_holding_days, strategy=strategy)
    base_engine.fetch_historical_universe()

    results_by_crisis = {}

    for c_key, c_info in HISTORICAL_PERIODS_1999_2026.items():
        print(f"\n▶️ Test en cours : {c_info['name']} ({c_info['start']} -> {c_info['end']})...")
        try:
            engine = BacktestEngine(
                symbols=base_engine.symbols,
                period=c_key,
                start_date=c_info['start'],
                end_date=c_info['end'],
                initial_capital=initial_capital,
                tp1_pct=tp1_pct,
                tp2_pct=tp2_pct,
                max_holding_days=max_holding_days,
                strategy=strategy
            )
            # Partager les données historiques déjà en mémoire
            engine.historical_data = base_engine.historical_data
            engine.macro_data = base_engine.macro_data
            engine.sector_etf_data = base_engine.sector_etf_data
            engine.macro_daily_regime = base_engine.macro_daily_regime

            res = engine.run_simulation()
            m = res.get('metrics', {})
            results_by_crisis[c_key] = {
                "name": c_info['name'],
                "category": c_info.get('category', 'Général'),
                "description": c_info['description'],
                "start": c_info['start'],
                "end": c_info['end'],
                "initial_capital": float(initial_capital),
                "final_capital": float(res.get('final_capital', initial_capital)),
                "net_pnl": float(m.get('total_net_pnl', 0.0)),
                "return_pct": float(m.get('total_return_pct', 0.0)),
                "win_rate": float(m.get('win_rate_pct', 0.0)),
                "total_trades": int(m.get('total_trades', 0)),
                "winning_trades": int(m.get('winning_trades', 0)),
                "losing_trades": int(m.get('losing_trades', 0)),
                "profit_factor": float(m.get('profit_factor', 0.0)),
                "max_drawdown": float(m.get('max_drawdown_pct', 0.0)),
                "avg_holding_days": float(m.get('avg_holding_days', 0.0)),
                "sharpe_ratio": float(m.get('sharpe_ratio', 0.0)),
                "exit_reasons": m.get('exit_reasons', {})
            }
        except Exception as e:
            print(f"⚠️ Erreur simulation période {c_key} ({c_info.get('name')}): {e}")
            results_by_crisis[c_key] = {
                "name": c_info['name'],
                "category": c_info.get('category', 'Général'),
                "description": c_info['description'],
                "start": c_info['start'],
                "end": c_info['end'],
                "initial_capital": float(initial_capital),
                "final_capital": float(initial_capital),
                "net_pnl": 0.0,
                "return_pct": 0.0,
                "win_rate": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "avg_holding_days": 0.0,
                "sharpe_ratio": 0.0,
                "exit_reasons": {},
                "error": str(e)
            }

    return results_by_crisis

if __name__ == "__main__":
    crisis_res = run_all_crises_stress_test()
    print("\n" + "="*80)
    print("📊 TABLEAU COMPARATIF DES PERFORMANCES PAR PÉRIODE DE CRISE")
    print("="*80)
    for k, v in crisis_res.items():
        print(f"\n📌 {v['name']}")
        print(f"   Contexte : {v['description']}")
        print(f"   Dates    : {v['start']} -> {v['end']}")
        print(f"   Capital  : {v['initial_capital']} € -> {v['final_capital']} € (Gain: {v['net_pnl']:+.2f} € / {v['return_pct']:+.2f} %)")
        print(f"   Win Rate : {v['win_rate']} % ({v['winning_trades']}W / {v['losing_trades']}L sur {v['total_trades']} trades)")
        print(f"   PF / DD  : Profit Factor = {v['profit_factor']} | Max Drawdown = -{v['max_drawdown']} %")
        print(f"   Durée    : {v['avg_holding_days']} jours | Sorties: {v['exit_reasons']}")

