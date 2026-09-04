"""
Rapport de Rejeu Trade-par-Trade : Journal Réel vs Protocole V5 POC
====================================================================
Compare chaque position exécutée avec ce qu'aurait donné le protocole
Mean Reversion (TP1 +1.8%, Break-Even, TP2 +2.5%/MM20, Stop -2%, Time Stop J+10).

Usage:
    PYTHONPATH=.. python scripts/run_trade_replay_report.py
"""
import sys
import os
import json
import math
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trade_replay_engine import run_trade_by_trade_replay


def fmt_eur(v):
    if v is None: return "   N/A  "
    s = f"+{v:,.2f} €" if v >= 0 else f"{v:,.2f} €"
    return s

def fmt_pct(v):
    if v is None: return " N/A "
    return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"

def icon(v):
    if v is None: return "⬜"
    return "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")


def main():
    print("\n" + "═"*90)
    print("  REJEU TRADE-PAR-TRADE : Journal Réel vs Protocole V5 Mean Reversion + POC")
    print("═"*90)
    print("  Paramètres : TP1 +1.80% (50%), Break-Even actif, TP2 +2.50%/MM20, Stop -2.0%, Time Stop J+10")
    print("═"*90 + "\n")

    print("  ⏳ Chargement des données Supabase & téléchargement historiques…")
    res = run_trade_by_trade_replay(tp1_pct=1.80, tp2_pct=2.50, stop_loss_pct=-2.0, max_holding_days=10)

    if not res.get("success"):
        print(f"  ❌ Erreur: {res.get('error')}")
        return

    summary = res["summary"]
    trades = res["trades"]
    by_sym = res.get("by_symbol", [])

    # ─────────────────────────────────────────────────────────────────────
    # Résumé Exécutif
    # ─────────────────────────────────────────────────────────────────────
    total = summary["total_trades"]
    real = summary["real"]
    sim = summary["simulated"]
    comp = summary["comparison"]

    print(f"\n  📊  RÉSUMÉ EXÉCUTIF ({total} trades analysés)")
    print(f"  {'─'*60}")
    print(f"  {'Métrique':<35} {'Journal Réel':>15} {'Protocole V5':>15}")
    print(f"  {'─'*65}")
    print(f"  {'PnL Net Total':<35} {fmt_eur(real['total_net_pnl']):>15} {fmt_eur(sim['total_net_pnl']):>15}")
    print(f"  {'Win Rate':<35} {fmt_pct(real['win_rate_pct']):>15} {fmt_pct(sim['win_rate_pct']):>15}")
    print(f"  {'Trades Gagnants':<35} {real['winning_trades']:>15} {sim['winning_trades']:>15}")
    print(f"  {'Trades Perdants':<35} {real['losing_trades']:>15} {sim['losing_trades']:>15}")
    print(f"  {'Durée Moyenne (jours)':<35} {real['avg_duration_days']:>15.1f} {sim['avg_duration_days']:>15.1f}")
    print(f"  {'─'*65}")
    print(f"\n  {icon(comp['net_pnl_delta_eur'])}  Différentiel PnL : {fmt_eur(comp['net_pnl_delta_eur'])}")
    print(f"  ⏱️  Jours d'exposition économisés : {comp['total_days_saved']:.0f}j "
          f"({comp['avg_days_saved_per_trade']:.1f}j/trade)")
    print(f"  🎯  Trades améliorés par le protocole : {summary['improved_trades_count']}/{total} "
          f"({summary['improved_pct']:.1f}%)")

    # ─────────────────────────────────────────────────────────────────────
    # Répartition des sorties simulées
    # ─────────────────────────────────────────────────────────────────────
    print(f"\n  📤  RÉPARTITION DES SORTIES (Protocole V5)")
    print(f"  {'─'*50}")
    for reason, count in sorted(sim["exit_reasons"].items(), key=lambda x: -x[1]):
        pct_r = count / total * 100
        bar = "█" * int(pct_r / 3) + "░" * (34 - int(pct_r / 3))
        print(f"  {reason:<40} {count:>3} ({pct_r:>5.1f}%)  {bar}")

    # ─────────────────────────────────────────────────────────────────────
    # Tableau par Action
    # ─────────────────────────────────────────────────────────────────────
    print(f"\n  📋  ANALYSE PAR ACTION (Top actifs par nombre de trades)")
    print(f"  {'─'*90}")
    print(f"  {'Symbole':<12} {'Trades':>6} {'PnL Réel':>12} {'PnL V5':>12} {'Δ PnL':>12} "
          f"{'Dur. Réelle':>12} {'Dur. V5':>12} {'Jours Économisés':>16}")
    print(f"  {'─'*90}")
    for sym_data in by_sym[:20]:
        s = sym_data["symbol"]
        n = sym_data["trades_count"]
        rp = sym_data["real_pnl"]
        sp = sym_data["sim_pnl"]
        dp = sym_data["pnl_diff"]
        rd = sym_data["real_avg_days"]
        sd = sym_data["sim_avg_days"]
        ds = sym_data["days_saved"]

        d_icon = icon(dp)
        print(f"  {s:<12} {n:>6} {fmt_eur(rp):>12} {fmt_eur(sp):>12} "
              f"{d_icon} {fmt_eur(dp):>10} {rd:>12.1f}j {sd:>12.1f}j {ds:>+14.1f}j")

    # ─────────────────────────────────────────────────────────────────────
    # Top 10 Trades où le Protocole Aurait Fait Mieux
    # ─────────────────────────────────────────────────────────────────────
    improved = [t for t in trades if t["comparison"]["pnl_diff_eur"] > 0]
    improved.sort(key=lambda x: x["comparison"]["pnl_diff_eur"], reverse=True)

    print(f"\n  🏆  TOP 10 TRADES — Gains Supérieurs avec le Protocole V5")
    print(f"  {'─'*80}")
    print(f"  {'Symbole':<10} {'Entrée':>12} {'PRU':>8} {'PnL Réel':>10} {'PnL V5':>10} "
          f"{'Δ PnL':>10} {'Sortie V5':>25}")
    print(f"  {'─'*80}")
    for t in improved[:10]:
        print(f"  {t['symbol']:<10} {t['entry_date']:>12} {t['pru']:>8.2f}  "
              f"{fmt_eur(t['real']['pnl_amount']):>10} {fmt_eur(t['simulated']['pnl_amount']):>10} "
              f"🟢 {fmt_eur(t['comparison']['pnl_diff_eur']):>8}  "
              f"{t['simulated']['exit_reason'][:25]}")

    # ─────────────────────────────────────────────────────────────────────
    # Top 10 Trades où le Réel a Mieux Performé
    # ─────────────────────────────────────────────────────────────────────
    underperformed = [t for t in trades if t["comparison"]["pnl_diff_eur"] < 0]
    underperformed.sort(key=lambda x: x["comparison"]["pnl_diff_eur"])

    print(f"\n  📉  TOP 10 TRADES — Journal Réel Meilleur que le Protocole V5")
    print(f"  {'─'*80}")
    print(f"  {'Symbole':<10} {'Entrée':>12} {'PRU':>8} {'PnL Réel':>10} {'PnL V5':>10} "
          f"{'Δ PnL':>10} {'Sortie V5':>25}")
    print(f"  {'─'*80}")
    for t in underperformed[:10]:
        print(f"  {t['symbol']:<10} {t['entry_date']:>12} {t['pru']:>8.2f}  "
              f"{fmt_eur(t['real']['pnl_amount']):>10} {fmt_eur(t['simulated']['pnl_amount']):>10} "
              f"🔴 {fmt_eur(t['comparison']['pnl_diff_eur']):>8}  "
              f"{t['simulated']['exit_reason'][:25]}")

    # ─────────────────────────────────────────────────────────────────────
    # Conclusion
    # ─────────────────────────────────────────────────────────────────────
    print(f"\n  {'═'*90}")
    print(f"  📝  CONCLUSION ANALYTIQUE")
    print(f"  {'─'*90}")
    print(f"  {comp['conclusion']}")
    print(f"\n  Points clés :")
    days_saved = comp["total_days_saved"]
    pnl_delta = comp["net_pnl_delta_eur"]
    improved_pct = summary["improved_pct"]
    print(f"  • Le protocole V5 a amélioré {improved_pct:.0f}% des trades en termes de durée d'exposition.")
    if days_saved > 0:
        print(f"  • En réduisant de {days_saved:.0f} jours d'exposition, le capital est resté plus disponible")
        print(f"    pour d'autres opportunités Mean Reversion (Time in Market = protection algorithmique).")
    if pnl_delta < 0:
        print(f"  • Le différentiel négatif de {fmt_eur(pnl_delta)} s'explique par la différence entre")
        print(f"    une gestion active longue (journal réel) et la rigueur du protocole court terme V5.")
        print(f"  • Le protocole V5 préserve le capital par un Time Stop J+10 strict là où le journal")
        print(f"    réel a parfois maintenu des positions plus longtemps avec de meilleurs prix de sortie.")
    else:
        print(f"  • Gain net de {fmt_eur(pnl_delta)} sur {total} trades avec des sorties plus disciplinées.")

    # ─────────────────────────────────────────────────────────────────────
    # Export JSON
    # ─────────────────────────────────────────────────────────────────────
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "trade_replay_results.json")

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

    top_by_symbol = sorted(by_sym, key=lambda x: abs(x["pnl_diff"]), reverse=True)[:30]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(clean({
            "generated_at": datetime.now().isoformat(),
            "parameters": {
                "tp1_pct": 1.80, "tp2_pct": 2.50,
                "stop_loss_pct": -2.0, "max_holding_days": 10
            },
            "summary": summary,
            "by_symbol": top_by_symbol,
        }), f, ensure_ascii=False, indent=2)

    print(f"\n  💾  Résultats exportés → data/trade_replay_results.json")
    print(f"  {'═'*90}\n")


if __name__ == "__main__":
    main()
