#!/usr/bin/env python3
"""
Script de comparaison des versions de l'algorithme :
- V1 Classic (Mean reversion basique)
- V2 Standard (Mean reversion simple)
- V3 Institutional (Confluence 3 Piliers & R-Max)
- V4 Sniper & Swing (Confluence 5 Piliers, Manipulation M15/ATR >= 25%, TP scaling)

Exécute les simulations sur 27 ans (1999-2026) et les 4 grands krachs historiques.
"""

import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.backtest_engine import BacktestEngine, HISTORICAL_PERIODS_1999_2026

def run_comparative_benchmark():
    print("=" * 80)
    print("🚀 BENCHMARK COMPARATIF DES STRATÉGIES (V1 vs V2 vs V3 vs V4)")
    print("=" * 80)

    periods_to_test = [
        ("all_cycles", "Global Multi-Décennies (1999 - 2026)"),
        ("crisis_2000", "Bulle Internet (1999 - 2003)"),
        ("crisis_2008", "Crise des Subprimes (2007 - 2009)"),
        ("crisis_2020", "Krach Covid-19 (2020)"),
        ("crisis_2022", "Choc Inflation & Taux (2021 - 2022)"),
        ("2y", "Marché Récent (2024 - 2026)")
    ]

    strategies = [
        ("v1_classic", "V1 Classic"),
        ("v2_standard", "V2 Standard"),
        ("v3_institutional", "V3 Institutional"),
        ("v4_sniper_swing", "V4 Sniper & Swing")
    ]

    results_table = []

    # Initialiser et charger les données une seule fois
    print("\n📥 Chargement des univers historiques...")
    base_engine = BacktestEngine(period="max")
    base_engine.fetch_historical_universe()

    for p_id, p_label in periods_to_test:
        print(f"\n" + "-" * 75)
        print(f"📊 Test Période : {p_label} ({p_id})")
        print("-" * 75)

        for strat_id, strat_label in strategies:
            # Configurer le moteur pour la période et la stratégie
            engine = BacktestEngine(
                period=p_id,
                initial_capital=5000.0,
                tp1_pct=1.25,
                tp2_pct=2.25,
                strategy=strat_id
            )
            # Réutiliser les données déjà chargées
            engine.historical_data = base_engine.historical_data
            engine.macro_data = base_engine.macro_data
            engine.sector_etf_data = base_engine.sector_etf_data
            engine.macro_daily_regime = base_engine.macro_daily_regime

            res = engine.run_simulation()
            m = res.get("metrics", {})

            results_table.append({
                "Periode": p_label,
                "Periode_ID": p_id,
                "Strategie": strat_label,
                "Strategie_ID": strat_id,
                "Trades": m.get("total_trades", 0),
                "WinRate": f"{m.get('win_rate_pct', 0.0):.1f}%",
                "ProfitFactor": f"{m.get('profit_factor', 0.0):.2f}",
                "TotalReturn": f"{m.get('total_return_pct', 0.0):+.1f}%",
                "MaxDrawdown": f"{m.get('max_drawdown_pct', 0.0):.1f}%",
                "Sharpe": f"{m.get('sharpe_ratio', 0.0):.2f}"
            })

            print(f"  {strat_label:<20} | Trades: {m.get('total_trades', 0):>4} | WinRate: {m.get('win_rate_pct', 0.0):>5.1f}% | Return: {m.get('total_return_pct', 0.0):>+7.1f}% | MaxDD: {m.get('max_drawdown_pct', 0.0):>5.1f}% | Sharpe: {m.get('sharpe_ratio', 0.0):>4.2f}", flush=True)

    df_res = pd.DataFrame(results_table)
    os.makedirs(ROOT_DIR / "data", exist_ok=True)
    out_csv = ROOT_DIR / "data" / "benchmark_v1_v2_v3_v4.csv"
    df_res.to_csv(out_csv, index=False)
    print(f"\n✅ Résultats complets exportés dans : {out_csv}", flush=True)

    # Résumé Comparatif Global
    print("\n" + "=" * 80, flush=True)
    print("🏆 RÉSUMÉ COMPARATIF GLOBAL (1999 - 2026)", flush=True)
    print("=" * 80, flush=True)
    global_df = df_res[df_res["Periode_ID"] == "all_cycles"]
    for _, row in global_df.iterrows():
        print(f"  • {row['Strategie']:<20} ➔ Total Return: {row['TotalReturn']:<9} | Win Rate: {row['WinRate']:<7} | Max Drawdown: {row['MaxDrawdown']:<7} | Profit Factor: {row['ProfitFactor']}", flush=True)

if __name__ == "__main__":
    run_comparative_benchmark()
