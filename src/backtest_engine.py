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

CRISIS_PERIODS = {
    "crisis_2000": {
        "id": "crisis_2000",
        "name": "💥 Krach Dot-Com / Bulle Internet (1999-2003)",
        "start": "1999-01-01",
        "end": "2003-12-31",
        "description": "Éclatement de la bulle spéculative tech (-80% Nasdaq, -50% S&P 500)"
    },
    "crisis_2008": {
        "id": "crisis_2008",
        "name": "🏦 Crise des Subprimes & GFC (2007-2009)",
        "start": "2007-06-01",
        "end": "2009-12-31",
        "description": "Faillite de Lehman Brothers et récession bancaire mondiale (-55% S&P 500, VIX > 80)"
    },
    "crisis_2020": {
        "id": "crisis_2020",
        "name": "🦠 Krach Éclair Covid-19 (2020)",
        "start": "2020-01-01",
        "end": "2020-12-31",
        "description": "Choc pandémique et confinements mondiaux (-35% en 1 mois, VIX > 80)"
    },
    "crisis_2022": {
        "id": "crisis_2022",
        "name": "📉 Bear Market Inflation & Taux (2022)",
        "start": "2021-11-01",
        "end": "2022-12-31",
        "description": "Resserrement monétaire historique des banques centrales (-35% Nasdaq, -20% S&P 500)"
    },
    "all_cycles": {
        "id": "all_cycles",
        "name": "🌐 Cycle Complet Multi-Décennies (1999-2026 / 27 ans)",
        "start": "1999-01-01",
        "end": "2026-08-20",
        "description": "Test exhaustif sur 27 ans incluant 4 krachs majeurs et 4 grands marchés haussiers"
    }
}

class BacktestEngine:
    """
    Moteur de Backtest Walk-Forward pour la stratégie de Swing Trading Mean Reversion (Protocole 8 étapes).
    Supporte les stress-tests sur les grandes crises historiques (1999, 2008, 2020, 2022).
    """

    def __init__(self, symbols=None, period="2y", start_date=None, end_date=None, initial_capital=5000.0, tp1_pct=1.25, tp2_pct=2.25, max_holding_days=10):
        self.symbols = symbols or list(set(DEFAULT_WATCHLIST + DEFAULT_MARKET_POOL))
        self.period = period
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = float(initial_capital)
        self.tp1_pct = float(tp1_pct)
        self.tp2_pct = float(tp2_pct)
        self.max_holding_days = int(max_holding_days)
        self.historical_data = {}
        self.macro_data = {}
        self.sector_etf_data = {}

        # Résolution des périodes de crise prédéfinies
        if self.period in CRISIS_PERIODS:
            c_info = CRISIS_PERIODS[self.period]
            self.start_date = c_info['start']
            self.end_date = c_info['end']
            self.fetch_period = "max"
        elif self.period in ["max", "5y", "10y", "20y", "25y"]:
            self.fetch_period = "max"
        else:
            self.fetch_period = self.period

    def fetch_historical_universe(self, force_refresh=False):
        """
        Télécharge et met en cache l'historique OHLCV pour tous les symboles, indices macro et ETFs sectoriels.
        """
        all_tickers = list(set(self.symbols + list(SECTOR_ETFS.values()) + ["^VIX", "SPY"]))
        p_name = "max" if self.fetch_period == "max" or self.start_date else self.period
        print(f"📥 Téléchargement / Chargement historique pour {len(all_tickers)} actifs (période: {p_name})...")
        
        for ticker in all_tickers:
            cache_file = DATA_CACHE_DIR / f"{ticker.replace('^', '_')}_{p_name}.csv"
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
                if ticker in ["^VIX", "SPY"]:
                    self.macro_data[ticker] = df
                elif ticker in SECTOR_ETFS.values():
                    self.sector_etf_data[ticker] = df
                else:
                    self.historical_data[ticker] = df

        print(f"✅ Données prêtes : {len(self.historical_data)} actions, {len(self.sector_etf_data)} ETFs, {len(self.macro_data)} indices.")
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

        # Plus bas sur 20 jours (Support approximé)
        df['Support_20d'] = df['Low'].rolling(window=20, min_periods=5).min()
        df['Resistance_20d'] = df['High'].rolling(window=20, min_periods=5).max()

        return df

    def run_simulation(self):
        """
        Exécute la simulation chronologique (walk-forward bar-by-bar).
        """
        if not self.historical_data:
            self.fetch_historical_universe()

        if not self.historical_data:
            return {"error": "Aucune donnée historique disponible pour le backtest."}

        # Aligner toutes les dates communes
        all_dates = set()
        for df in self.historical_data.values():
            all_dates.update(df.index)
        sorted_dates = sorted(list(all_dates))

        # Filtrage par dates si spécifié (ex: crises 1999, 2008, 2020, 2022)
        if self.start_date:
            ts_start = pd.to_datetime(self.start_date)
            sorted_dates = [d for d in sorted_dates if d >= ts_start]
        if self.end_date:
            ts_end = pd.to_datetime(self.end_date)
            sorted_dates = [d for d in sorted_dates if d <= ts_end]

        # Ne commencer la simulation qu'après les barres de chauffe si pas de date de début précise
        if not self.start_date and len(sorted_dates) > 60:
            simulation_dates = sorted_dates[50:]
        else:
            simulation_dates = sorted_dates

        # État du portefeuille
        current_cash = self.initial_capital
        active_positions = [] # Liste de dicts
        closed_trades = []    # Liste de dicts
        daily_equity = []     # Historique date -> total equity

        # Pré-évaluation Sharia (Statique pour le backtest sur l'univers)
        sharia_cache = {}
        for sym in self.symbols:
            sharia_cache[sym] = screen_ticker(sym)

        # Pré-évaluation Catégorie & ETF sectoriel
        category_cache = {}
        for sym in self.symbols:
            cat_info = categorize_ticker(sym)
            sec_name = cat_info.get("category", "Autres")
            etf_symbol = SECTOR_ETFS.get(sec_name, "SPY")
            category_cache[sym] = {
                "category": sec_name,
                "is_pea": cat_info.get("is_pea", False),
                "sector_etf": etf_symbol
            }

        # Boucle journalière Walk-Forward
        for date in simulation_dates:
            # 1. Mise à jour des positions actives & vérification des sorties
            positions_to_keep = []
            for pos in active_positions:
                sym = pos['symbol']
                df = self.historical_data.get(sym)
                if df is None or date not in df.index:
                    positions_to_keep.append(pos)
                    continue

                row = df.loc[date]
                high = float(row['High'])
                low = float(row['Low'])
                close = float(row['Close'])
                open_p = float(row['Open'])
                entry_p = pos['entry_price']
                shares = pos['shares']
                pos['days_held'] += 1
                days = pos['days_held']

                is_closed = False
                exit_price = 0.0
                exit_reason = ""

                # A. Stop-Loss Trigger (Priorité au contrôle du risque)
                if low <= pos['stop_loss']:
                    # Sortie au Stop-Loss (au prix du stop ou au prix d'ouverture si gap baissier)
                    exit_price = min(pos['stop_loss'], open_p) if open_p < pos['stop_loss'] else pos['stop_loss']
                    exit_reason = "STOP_LOSS"
                    is_closed = True

                # B. Take Profit 2 (+2.25%) Trigger
                elif high >= pos['tp2_price']:
                    exit_price = pos['tp2_price']
                    exit_reason = "TP2 (+2.25%)"
                    is_closed = True

                # C. Take Profit 1 (+1.25%) Trigger
                elif high >= pos['tp1_price']:
                    exit_price = pos['tp1_price']
                    exit_reason = "TP1 (+1.25%)"
                    is_closed = True

                # D. Time Stop (Invalidation temporelle à J+10 ouvrés)
                elif days >= self.max_holding_days:
                    exit_price = close
                    exit_reason = "TIME_STOP (J+10)"
                    is_closed = True

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

            # 2. Évaluation du régime Macro (VIX)
            vix_df = self.macro_data.get("^VIX")
            macro_regime = "RISK-ON"
            r_max_rate = R_MAX_PCT_STANDARD # 1.0%

            if vix_df is not None and date in vix_df.index:
                vix_close = float(vix_df.loc[date]['Close'])
                if vix_close > 35:
                    macro_regime = "RISK-OFF"
                    r_max_rate = 0.0 # Gel des achats
                elif vix_close >= 20:
                    macro_regime = "NEUTRE"
                    r_max_rate = R_MAX_PCT_REDUCED # 0.5%

            # 3. Détection de nouveaux signaux d'achat (si slots disponibles et cash disponible)
            current_portfolio_value = current_cash + sum(
                (self.historical_data[p['symbol']].loc[date]['Close'] * p['shares'])
                for p in active_positions
                if p['symbol'] in self.historical_data and date in self.historical_data[p['symbol']].index
            )

            # Réserve de cash minimale (25%)
            min_cash_required = current_portfolio_value * MIN_CASH_RESERVE_PCT
            available_cash_for_trades = max(0, current_cash - min_cash_required)

            if macro_regime != "RISK-OFF" and len(active_positions) < MAX_SIMULTANEOUS_POSITIONS and available_cash_for_trades > 100:
                active_symbols = [p['symbol'] for p in active_positions]
                active_sectors = [p['category'] for p in active_positions]

                candidates = []

                for sym in self.symbols:
                    if sym in active_symbols:
                        continue

                    # Filtre Sharia
                    sh_res = sharia_cache.get(sym, {})
                    if sh_res.get("status") == "NON CONFORME":
                        continue

                    # Filtre Secteur (Max 2 positions par secteur)
                    cat_info = category_cache.get(sym, {})
                    sec_cat = cat_info.get("category", "Autres")
                    if active_sectors.count(sec_cat) >= MAX_SECTOR_POSITIONS:
                        continue

                    df = self.historical_data.get(sym)
                    if df is None or date not in df.index:
                        continue

                    # Vérifier historique suffisant à cette date
                    loc_idx = df.index.get_loc(date)
                    if isinstance(loc_idx, (slice, np.ndarray, list)):
                        loc_idx = loc_idx.start if isinstance(loc_idx, slice) else loc_idx[0]
                    if loc_idx < 30:
                        continue

                    row = df.iloc[loc_idx]
                    close = float(row['Close'])
                    sma_200 = float(row['SMA_200']) if not pd.isna(row['SMA_200']) else 0
                    sma_50 = float(row['SMA_50']) if not pd.isna(row['SMA_50']) else 0
                    rsi = float(row['RSI_14']) if not pd.isna(row['RSI_14']) else 50
                    turnover = float(row['SMA_20_Turnover']) if not pd.isna(row['SMA_20_Turnover']) else 10_000_000
                    wick_pct = float(row['Lower_Wick_Pct']) if not pd.isna(row['Lower_Wick_Pct']) else 0

                    # 1. Filtre Tendance de fond : Cours > SMA 200 ou SMA 50
                    if sma_200 > 0 and close < (sma_200 * 0.96): # Tolérance max 4% sous SMA200
                        continue

                    # 2. Filtre Liquidité : Turnover > 1 M€/$
                    if turnover < MIN_AVG_DAILY_VOLUME_USD:
                        continue

                    # 3. Filtre Dip : Baisse de -3% à -8% sur 1, 2 ou 3 séances
                    r1 = float(row['Return_1d']) if not pd.isna(row['Return_1d']) else 0
                    r2 = float(row['Return_2d']) if not pd.isna(row['Return_2d']) else 0
                    r3 = float(row['Return_3d']) if not pd.isna(row['Return_3d']) else 0
                    
                    min_ret = min(r1, r2, r3)
                    if not (-MAX_DROP_PCT <= min_ret <= -MIN_DROP_PCT):
                        continue

                    # 4. Filtre Confluence : RSI < 45 OU mèche basse >= 0.7%
                    has_wick = wick_pct >= 0.7
                    has_rsi_rebound = rsi <= 45
                    if not (has_wick or has_rsi_rebound):
                        continue

                    # Support calculé
                    support = float(row['Support_20d']) if not pd.isna(row['Support_20d']) else (close * 0.96)
                    stop_loss = min(support * 0.99, close * 0.965) # Stop sous support avec buffer
                    stop_dist_pct = (close - stop_loss) / close

                    # Score de confluence
                    score = 6
                    if close > sma_200: score += 1
                    if rsi < 35: score += 1
                    if has_wick: score += 1
                    if min_ret <= -4.0: score += 1

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

                # Trier les candidats par score décroissant et sélectionner les meilleurs
                candidates.sort(key=lambda x: x['score'], reverse=True)

                for cand in candidates:
                    if len(active_positions) >= MAX_SIMULTANEOUS_POSITIONS:
                        break

                    sym = cand['symbol']
                    close = cand['close']
                    stop_loss = cand['stop_loss']
                    stop_dist = cand['stop_dist_pct']

                    # Calcul dimensionnement R-Max
                    r_max_amount = current_portfolio_value * r_max_rate
                    max_nominal = current_portfolio_value * MAX_ALLOCATION_PER_LINE_PCT
                    suggested_nominal = min(r_max_amount / max(stop_dist, 0.02), max_nominal)
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
            total_positions_val = 0.0
            for p in active_positions:
                sym = p['symbol']
                df = self.historical_data.get(sym)
                if df is not None:
                    # Trouver le dernier cours disponible à ou avant cette date
                    sub_df = df.loc[:date]
                    if not sub_df.empty:
                        p_close = float(sub_df.iloc[-1]['Close'])
                    else:
                        p_close = float(p['entry_price'])
                else:
                    p_close = float(p['entry_price'])
                total_positions_val += (p_close * p['shares'])

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

def run_all_crises_stress_test(initial_capital=5000.0, tp1_pct=1.25, tp2_pct=2.25, max_holding_days=10):
    """
    Exécute automatiquement le stress-test sur les 4 grandes crises historiques et sur le cycle complet multi-décennies.
    """
    print("\n" + "="*80)
    print("🌪️  STRESS-TEST MULTI-CRISES & GRANDES DÉCENNIES (1999 - 2026)")
    print("="*80)

    # Pré-téléchargement global
    base_engine = BacktestEngine(period="max", initial_capital=initial_capital, tp1_pct=tp1_pct, tp2_pct=tp2_pct, max_holding_days=max_holding_days)
    base_engine.fetch_historical_universe()

    results_by_crisis = {}

    for c_key, c_info in CRISIS_PERIODS.items():
        print(f"\n▶️ Test en cours : {c_info['name']} ({c_info['start']} -> {c_info['end']})...")
        engine = BacktestEngine(
            symbols=base_engine.symbols,
            period=c_key,
            start_date=c_info['start'],
            end_date=c_info['end'],
            initial_capital=initial_capital,
            tp1_pct=tp1_pct,
            tp2_pct=tp2_pct,
            max_holding_days=max_holding_days
        )
        # Partager les données historiques déjà en mémoire
        engine.historical_data = base_engine.historical_data
        engine.macro_data = base_engine.macro_data
        engine.sector_etf_data = base_engine.sector_etf_data

        res = engine.run_simulation()
        m = res.get('metrics', {})
        results_by_crisis[c_key] = {
            "name": c_info['name'],
            "description": c_info['description'],
            "start": c_info['start'],
            "end": c_info['end'],
            "initial_capital": initial_capital,
            "final_capital": res.get('final_capital', initial_capital),
            "net_pnl": m.get('total_net_pnl', 0.0),
            "return_pct": m.get('total_return_pct', 0.0),
            "win_rate": m.get('win_rate_pct', 0.0),
            "total_trades": m.get('total_trades', 0),
            "winning_trades": m.get('winning_trades', 0),
            "losing_trades": m.get('losing_trades', 0),
            "profit_factor": m.get('profit_factor', 0.0),
            "max_drawdown": m.get('max_drawdown_pct', 0.0),
            "avg_holding_days": m.get('avg_holding_days', 0.0),
            "sharpe_ratio": m.get('sharpe_ratio', 0.0),
            "exit_reasons": m.get('exit_reasons', {})
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

