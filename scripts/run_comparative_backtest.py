"""
Script de Backtest Comparatif Multi-Stratégies
================================================
Compare V1 (Classic), V2 (Standard), V3 (Institutional), V5 (Mean Reversion + POC)
sur 5 grandes périodes historiques de crise + cycles de reprise.

Usage:
    PYTHONPATH=.. python scripts/run_comparative_backtest.py
"""
import sys
import os
import json
import math
from datetime import datetime

# Ajouter le répertoire racine du projet
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtest_engine import BacktestEngine, HISTORICAL_PERIODS_1999_2026

# ─────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────
STRATEGIES = [
    {"id": "v1_classic",           "label": "V1 Classic (RSI brut)"},
    {"id": "v2_standard",          "label": "V2 Standard (MR simple)"},
    {"id": "v3_institutional",     "label": "V3 Institutionnel (5 Piliers)"},
    {"id": "v5_mean_reversion_poc","label": "V5 MR + POC (Protocole 8 Étapes)"},
]

# Périodes sélectionnées pour le comparatif
KEY_PERIODS = [
    "crisis_2000",
    "crisis_2008",
    "crisis_2020",
    "crisis_2022",
    "bull_run_recent_2023_2026",
]

INITIAL_CAPITAL = 10_000.0  # 10 000 € de capital simulé

# ─────────────────────────────────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────────────────────────────────
def format_eur(v):
    if v is None:
        return "N/A"
    return f"+{v:,.0f} €" if v >= 0 else f"{v:,.0f} €"

def format_pct(v):
    if v is None:
        return "N/A"
    return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"

def color(v, is_pct=False):
    """Retourne une indicateur coloré ASCII"""
    if v is None:
        return "⬜"
    if is_pct:
        return "🟢" if v > 0 else ("🔴" if v < -5 else "🟡")
    return "🟢" if v > 0 else "🔴"

# ─────────────────────────────────────────────────────────────────────────
# Exécution du backtest comparatif
# ─────────────────────────────────────────────────────────────────────────
def run_comparative():
    print("\n" + "═"*80)
    print("  BACKTEST COMPARATIF MULTI-STRATÉGIES — Agent Trading v5 POC")
    print("═"*80)
    print(f"  Capital initial : {INITIAL_CAPITAL:,.0f} €")
    print(f"  Périodes testées : {', '.join(KEY_PERIODS)}")
    print(f"  Stratégies : {', '.join(s['label'] for s in STRATEGIES)}")
    print("═"*80 + "\n")

    results = {}  # period_id -> strategy_id -> metrics

    for period_key in KEY_PERIODS:
        period_info = HISTORICAL_PERIODS_1999_2026.get(period_key)
        if not period_info:
            print(f"  ⚠️  Période '{period_key}' introuvable. Skip.")
            continue

        print(f"\n{'─'*70}")
        print(f"  📅  {period_info['name']}")
        print(f"      {period_info.get('start', period_info.get('start_date', 'N/A'))} → {period_info.get('end', period_info.get('end_date', 'N/A'))}")
        print(f"{'─'*70}")

        results[period_key] = {"period_name": period_info['name']}

        for strat in STRATEGIES:
            sid = strat["id"]
            slabel = strat["label"]
            try:
                engine = BacktestEngine(
                    strategy=sid,
                    initial_capital=INITIAL_CAPITAL,
                    start_date=period_info.get('start'),
                    end_date=period_info.get('end'),
                )
                result = engine.run_simulation()

                if not result or not result.get("metrics"):
                    print(f"  ❌  {slabel} — Pas de résultat.")
                    results[period_key][sid] = None
                    continue

                m = result["metrics"]
                results[period_key][sid] = m

                total_ret = m.get("total_return_pct", 0)
                sharpe = m.get("sharpe_ratio", 0)
                mdd = m.get("max_drawdown_pct", 0)
                wr = m.get("win_rate_pct", 0)
                trades = m.get("total_trades", 0)
                avg_dur = m.get("avg_duration_days", 0)
                net_pnl = m.get("net_pnl_eur", 0)

                print(f"\n  {color(total_ret, True)}  {slabel}")
                print(f"     ├─ Rendement Total   : {format_pct(total_ret)}")
                print(f"     ├─ PnL Net            : {format_eur(net_pnl)}")
                print(f"     ├─ Sharpe Ratio       : {sharpe:.2f}")
                print(f"     ├─ Max Drawdown       : {format_pct(mdd)}")
                print(f"     ├─ Win Rate           : {wr:.1f}%")
                print(f"     ├─ Nbre de Trades     : {trades}")
                print(f"     └─ Durée Moy. (j)     : {avg_dur:.1f} j")

            except Exception as e:
                print(f"  ❌  {slabel} — Erreur: {e}")
                results[period_key][sid] = {"error": str(e)}

    # ─────────────────────────────────────────────────────────────────────
    # Tableau récapitulatif global
    # ─────────────────────────────────────────────────────────────────────
    print("\n\n" + "═"*80)
    print("  SYNTHÈSE COMPARATIVE — Rendement Total par Période & Stratégie")
    print("═"*80)

    # En-têtes
    col_w = 14
    header_labels = ["Période"] + [s["id"].replace("_", " ")[:12] for s in STRATEGIES]
    print("  " + " | ".join(h.ljust(col_w) for h in header_labels))
    print("  " + "─" * (col_w * len(header_labels) + 3 * len(header_labels)))

    for period_key, pdata in results.items():
        pname = pdata.get("period_name", period_key)[:col_w]
        row_cells = [pname.ljust(col_w)]
        for strat in STRATEGIES:
            sid = strat["id"]
            m = pdata.get(sid)
            if m and isinstance(m, dict) and "total_return_pct" in m:
                val = m["total_return_pct"]
                cell = format_pct(val)
            else:
                cell = "  N/A  "
            row_cells.append(cell.ljust(col_w))
        print("  " + " | ".join(row_cells))

    print("═"*80)

    # ─────────────────────────────────────────────────────────────────────
    # Meilleure stratégie globale
    # ─────────────────────────────────────────────────────────────────────
    strat_totals = {}
    strat_sharpes = {}
    for strat in STRATEGIES:
        sid = strat["id"]
        rets = []
        sharpes = []
        for pdata in results.values():
            m = pdata.get(sid)
            if m and isinstance(m, dict):
                if "total_return_pct" in m:
                    rets.append(m["total_return_pct"])
                if "sharpe_ratio" in m:
                    sharpes.append(m["sharpe_ratio"])
        strat_totals[sid] = sum(rets) / len(rets) if rets else 0
        strat_sharpes[sid] = sum(sharpes) / len(sharpes) if sharpes else 0

    print("\n  🏆  Classement Global (Rendement Moyen sur toutes périodes)")
    ranking = sorted(strat_totals.items(), key=lambda x: x[1], reverse=True)
    for rank, (sid, avg_ret) in enumerate(ranking, 1):
        slabel = next(s["label"] for s in STRATEGIES if s["id"] == sid)
        sharpe_avg = strat_sharpes.get(sid, 0)
        medal = ["🥇", "🥈", "🥉", "4️⃣ "][rank - 1]
        print(f"  {medal}  {slabel}")
        print(f"       Rendement moyen : {format_pct(avg_ret)} | Sharpe moyen : {sharpe_avg:.2f}")

    # ─────────────────────────────────────────────────────────────────────
    # Export JSON
    # ─────────────────────────────────────────────────────────────────────
    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "data",
        "comparative_backtest_results.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        # Nettoyer les valeurs NaN/Inf pour JSON
        def clean(obj):
            if isinstance(obj, dict):
                return {k: clean(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean(i) for i in obj]
            elif isinstance(obj, float):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return obj
            return obj
        json.dump(clean({
            "generated_at": datetime.now().isoformat(),
            "strategies": [s["id"] for s in STRATEGIES],
            "periods": list(results.keys()),
            "results": results,
            "global_ranking": [{"strategy": sid, "avg_return_pct": r, "avg_sharpe": strat_sharpes[sid]} for sid, r in ranking]
        }), f, ensure_ascii=False, indent=2)

    print(f"\n  💾  Résultats exportés → data/comparative_backtest_results.json")
    print("═"*80 + "\n")

if __name__ == "__main__":
    run_comparative()
