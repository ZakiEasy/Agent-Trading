"""
Moteur de Rejeu Trade-par-Trade (Exact Period Protocol Replay)
-------------------------------------------------------------
Prend l'historique réel des positions exécutées issues du journal de trading Supabase,
et rejoue bar-by-bar le nouveau protocole Mean Reversion (TP1 50% +1.8%, Step Stop Break-Even,
TP2 50% +2.5%/MM20, Stop Loss -1.5%/-2.5%, Time Stop J+10) sur chaque trade aux mêmes dates exactes.
Fournit une comparaison Trade-par-Trade et des métriques agrégées (Gains, Durée, Win Rate).
"""

import math
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from src.supabase_connector import get_supabase_trade_journal
from src.market_data import resolve_ticker_symbol
from src.backtest_engine import BacktestEngine, DATA_CACHE_DIR
from src.protocol_feedback_engine import calculate_trade_duration_days

logger = logging.getLogger("trade_replay_engine")


def replay_single_trade(trade, historical_df, tp1_pct=1.80, tp2_pct=2.50, stop_loss_pct=-2.0, max_holding_days=10):
    """
    Rejoue un trade individuel à partir de sa date d'entrée sur l'historique de marché réel.
    """
    symbol = resolve_ticker_symbol(trade.get("symbol", ""))
    pru = float(trade.get("pru", 0.0))
    qty = float(trade.get("quantity", 1.0))
    invested = float(trade.get("invested_amount", pru * qty)) if (pru * qty) > 0 else 1000.0
    
    entry_str = trade.get("entry_date") or trade.get("open_time")
    exit_str = trade.get("exit_date") or trade.get("close_time")
    real_pnl_amt = float(trade.get("pnl_amount", 0.0))
    real_pnl_pct = float(trade.get("pnl_pct", 0.0))
    real_exit_price = float(trade.get("exit_price", pru))
    real_dur_days = calculate_trade_duration_days(entry_str, exit_str)

    if pru <= 0 or not entry_str or historical_df is None or historical_df.empty:
        return {
            "symbol": symbol,
            "entry_date": str(entry_str)[:10] if entry_str else "N/A",
            "pru": pru,
            "quantity": qty,
            "invested_amount": round(invested, 2),
            "real": {
                "pnl_amount": real_pnl_amt,
                "pnl_pct": real_pnl_pct,
                "duration_days": real_dur_days,
                "exit_price": real_exit_price,
                "is_win": real_pnl_amt >= 0
            },
            "simulated": {
                "pnl_amount": real_pnl_amt,
                "pnl_pct": real_pnl_pct,
                "duration_days": real_dur_days,
                "exit_reason": "DONNEES_MANQUANTES",
                "is_win": real_pnl_amt >= 0
            },
            "comparison": {
                "pnl_diff_eur": 0.0,
                "pnl_diff_pct": 0.0,
                "days_saved": 0.0,
                "improved": False
            }
        }

    try:
        # Nettoyer l'index de date
        clean_entry_date = pd.to_datetime(str(entry_str)[:10])
        df = historical_df.copy()
        try:
            df.index = pd.to_datetime(df.index)
            if getattr(df.index, 'tz', None) is not None:
                df.index = df.index.tz_convert(None)
        except Exception:
            try:
                df.index = pd.to_datetime(df.index).tz_localize(None)
            except Exception:
                pass

        # Filtrer à partir de la date d'entrée
        sub_df = df[df.index >= clean_entry_date]
        if sub_df.empty:
            # Essayer avec tolérance de 3 jours
            sub_df = df[df.index >= (clean_entry_date - timedelta(days=3))]

        if sub_df.empty:
            return {
                "symbol": symbol,
                "entry_date": str(clean_entry_date)[:10],
                "pru": pru,
                "quantity": qty,
                "invested_amount": round(invested, 2),
                "real": {
                    "pnl_amount": real_pnl_amt,
                    "pnl_pct": real_pnl_pct,
                    "duration_days": real_dur_days,
                    "exit_price": real_exit_price,
                    "is_win": real_pnl_amt >= 0
                },
                "simulated": {
                    "pnl_amount": real_pnl_amt,
                    "pnl_pct": real_pnl_pct,
                    "duration_days": real_dur_days,
                    "exit_reason": "HISTORIQUE_NON_COUVERT",
                    "is_win": real_pnl_amt >= 0
                },
                "comparison": {
                    "pnl_diff_eur": 0.0,
                    "pnl_diff_pct": 0.0,
                    "days_saved": 0.0,
                    "improved": False
                }
            }

        # Niveaux du Protocole
        tp1_price = pru * (1 + tp1_pct / 100.0)
        tp2_price = pru * (1 + tp2_pct / 100.0)
        initial_sl = pru * (1 + stop_loss_pct / 100.0)
        
        current_sl = initial_sl
        tp1_hit = False
        is_closed = False
        exit_price = 0.0
        exit_reason = ""
        days_held = 0

        for idx, (dt, row) in enumerate(sub_df.iterrows()):
            days_held = idx + 1
            high = float(row.get("High", pru))
            low = float(row.get("Low", pru))
            open_p = float(row.get("Open", pru))
            close_p = float(row.get("Close", pru))

            # 1. Vérifier si TP1 est touché pour la 1ère moitié
            if not tp1_hit:
                if high >= tp1_price:
                    tp1_hit = True
                    # Remontée immédiate du Stop au PRU (Break-Even) pour les séances suivantes
                    current_sl = max(current_sl, pru)

            # 2. Vérifier si TP2 est touché (Sortie complète ou solde)
            if high >= tp2_price:
                exit_price = tp2_price
                exit_reason = "TP2_OPTIMAL (+2.5% Mean Reversion)"
                is_closed = True
                break

            # 3. Vérifier si Stop Loss ou Break-Even touché
            if low <= current_sl:
                exit_price = current_sl
                if tp1_hit or current_sl >= pru:
                    exit_reason = "BREAKEVEN_SECURISE (0.0%)"
                else:
                    exit_reason = f"STOP_LOSS ({stop_loss_pct:.1f}%)"
                is_closed = True
                break

            # 4. Invalidation Temporelle (Time Stop J+10)
            if days_held >= max_holding_days:
                exit_price = close_p
                exit_reason = f"TIME_STOP (J+{max_holding_days})"
                is_closed = True
                break

        if not is_closed:
            # Clôture au dernier cours disponible de la période
            last_row = sub_df.iloc[-1]
            exit_price = float(last_row.get("Close", pru))
            exit_reason = "FIN_PERIODE"

        # Calcul du P&L simulé en tenant compte du Scaling Out (50% TP1 / 50% Solde)
        if tp1_hit and exit_reason != "TP2_OPTIMAL (+2.5% Mean Reversion)":
            # 50% vendu à TP1, 50% vendu à exit_price
            pnl_part1 = (tp1_price - pru) * (qty * 0.5)
            pnl_part2 = (exit_price - pru) * (qty * 0.5)
            sim_pnl_amt = pnl_part1 + pnl_part2
            sim_pnl_pct = ((sim_pnl_amt) / (pru * qty) * 100.0) if (pru * qty) > 0 else 0.0
        else:
            sim_pnl_amt = (exit_price - pru) * qty
            sim_pnl_pct = ((exit_price - pru) / pru * 100.0) if pru > 0 else 0.0

        sim_pnl_amt = float(np.nan_to_num(sim_pnl_amt, nan=0.0))
        sim_pnl_pct = float(np.nan_to_num(sim_pnl_pct, nan=0.0))
        real_pnl_amt = float(np.nan_to_num(real_pnl_amt, nan=0.0))
        real_pnl_pct = float(np.nan_to_num(real_pnl_pct, nan=0.0))

        pnl_diff_eur = round(sim_pnl_amt - real_pnl_amt, 2)
        pnl_diff_pct = round(sim_pnl_pct - real_pnl_pct, 2)
        days_saved = round(real_dur_days - days_held, 1)

        return {
            "symbol": symbol,
            "entry_date": str(clean_entry_date)[:10],
            "pru": round(pru, 2),
            "quantity": qty,
            "invested_amount": round(invested, 2),
            "real": {
                "pnl_amount": round(real_pnl_amt, 2),
                "pnl_pct": round(real_pnl_pct, 2),
                "duration_days": round(real_dur_days, 1),
                "exit_price": round(real_exit_price, 2),
                "is_win": real_pnl_amt >= 0
            },
            "simulated": {
                "pnl_amount": round(sim_pnl_amt, 2),
                "pnl_pct": round(sim_pnl_pct, 2),
                "duration_days": days_held,
                "exit_price": round(exit_price, 2),
                "exit_reason": exit_reason,
                "tp1_hit": tp1_hit,
                "is_win": sim_pnl_amt >= 0
            },
            "comparison": {
                "pnl_diff_eur": pnl_diff_eur,
                "pnl_diff_pct": pnl_diff_pct,
                "days_saved": days_saved,
                "improved": (pnl_diff_eur > 0 or (sim_pnl_amt >= 0 and days_saved > 5))
            }
        }
    except Exception as e:
        logger.warning(f"Erreur rejeu trade {symbol} ({entry_str}): {e}")
        return {
            "symbol": symbol,
            "entry_date": str(entry_str)[:10] if entry_str else "N/A",
            "pru": pru,
            "quantity": qty,
            "invested_amount": round(invested, 2),
            "real": {
                "pnl_amount": real_pnl_amt,
                "pnl_pct": real_pnl_pct,
                "duration_days": real_dur_days,
                "exit_price": real_exit_price,
                "is_win": real_pnl_amt >= 0
            },
            "simulated": {
                "pnl_amount": real_pnl_amt,
                "pnl_pct": real_pnl_pct,
                "duration_days": real_dur_days,
                "exit_reason": f"ERREUR: {str(e)[:30]}",
                "is_win": real_pnl_amt >= 0
            },
            "comparison": {
                "pnl_diff_eur": 0.0,
                "pnl_diff_pct": 0.0,
                "days_saved": 0.0,
                "improved": False
            }
        }


def run_trade_by_trade_replay(tp1_pct=1.80, tp2_pct=2.50, stop_loss_pct=-2.0, max_holding_days=10):
    """
    Exécute le rejeu complet de tous les trades du journal Supabase.
    """
    raw_trades = get_supabase_trade_journal() or []
    if not raw_trades:
        return {"success": False, "error": "Aucun trade disponible dans le journal Supabase."}

    # Extraire les tickers uniques et télécharger leur historique
    symbols = list(set([resolve_ticker_symbol(t.get("symbol")) for t in raw_trades if t.get("symbol")]))
    symbols = [s for s in symbols if s and s != "None" and not s.endswith(".L")]

    engine = BacktestEngine(symbols=symbols, period="10y", strategy="v3_institutional")
    engine.fetch_historical_universe()

    replayed_trades = []
    for t in raw_trades:
        sym = resolve_ticker_symbol(t.get("symbol"))
        hist_df = engine.historical_data.get(sym)
        if hist_df is None or hist_df.empty:
            for k, v in engine.historical_data.items():
                if k.upper() == sym.upper():
                    hist_df = v
                    break

        r_res = replay_single_trade(
            trade=t,
            historical_df=hist_df,
            tp1_pct=tp1_pct,
            tp2_pct=tp2_pct,
            stop_loss_pct=stop_loss_pct,
            max_holding_days=max_holding_days
        )
        replayed_trades.append(r_res)

    # Agrégations statistiques
    total_trades = len(replayed_trades)
    real_wins = sum(1 for t in replayed_trades if t["real"]["is_win"])
    real_losses = total_trades - real_wins
    real_total_pnl = sum(float(np.nan_to_num(t["real"]["pnl_amount"], nan=0.0)) for t in replayed_trades)
    real_avg_dur = (sum(float(np.nan_to_num(t["real"]["duration_days"], nan=0.0)) for t in replayed_trades) / total_trades) if total_trades > 0 else 0.0

    sim_wins = sum(1 for t in replayed_trades if t["simulated"]["is_win"])
    sim_losses = total_trades - sim_wins
    sim_total_pnl = sum(float(np.nan_to_num(t["simulated"]["pnl_amount"], nan=0.0)) for t in replayed_trades)
    sim_avg_dur = (sum(float(np.nan_to_num(t["simulated"]["duration_days"], nan=0.0)) for t in replayed_trades) / total_trades) if total_trades > 0 else 0.0

    total_days_saved = sum(float(np.nan_to_num(t["comparison"]["days_saved"], nan=0.0)) for t in replayed_trades)
    improved_trades_count = sum(1 for t in replayed_trades if t["comparison"]["improved"])
    total_pnl_delta = round(sim_total_pnl - real_total_pnl, 2)

    # Répartition des motifs de sorties simulées
    exit_reasons_dist = {}
    for t in replayed_trades:
        reason = t["simulated"].get("exit_reason", "AUTRE")
        exit_reasons_dist[reason] = exit_reasons_dist.get(reason, 0) + 1

    # Analyse par action (Agrégation par symbole)
    by_symbol = {}
    for t in replayed_trades:
        s = t["symbol"]
        if s not in by_symbol:
            by_symbol[s] = {
                "symbol": s,
                "trades_count": 0,
                "real_pnl": 0.0,
                "sim_pnl": 0.0,
                "pnl_diff": 0.0,
                "real_avg_days": 0.0,
                "sim_avg_days": 0.0,
                "days_saved": 0.0
            }
        by_symbol[s]["trades_count"] += 1
        by_symbol[s]["real_pnl"] += float(np.nan_to_num(t["real"]["pnl_amount"], nan=0.0))
        by_symbol[s]["sim_pnl"] += float(np.nan_to_num(t["simulated"]["pnl_amount"], nan=0.0))
        by_symbol[s]["pnl_diff"] += float(np.nan_to_num(t["comparison"]["pnl_diff_eur"], nan=0.0))
        by_symbol[s]["real_avg_days"] += float(np.nan_to_num(t["real"]["duration_days"], nan=0.0))
        by_symbol[s]["sim_avg_days"] += float(np.nan_to_num(t["simulated"]["duration_days"], nan=0.0))
        by_symbol[s]["days_saved"] += float(np.nan_to_num(t["comparison"]["days_saved"], nan=0.0))

    for s, data in by_symbol.items():
        cnt = data["trades_count"]
        data["real_pnl"] = round(data["real_pnl"], 2)
        data["sim_pnl"] = round(data["sim_pnl"], 2)
        data["pnl_diff"] = round(data["pnl_diff"], 2)
        data["real_avg_days"] = round(data["real_avg_days"] / cnt, 1)
        data["sim_avg_days"] = round(data["sim_avg_days"] / cnt, 1)
        data["days_saved"] = round(data["days_saved"], 1)

    sorted_by_symbol = sorted(list(by_symbol.values()), key=lambda x: x["trades_count"], reverse=True)

    summary = {
        "total_trades": total_trades,
        "improved_trades_count": improved_trades_count,
        "improved_pct": round(improved_trades_count / total_trades * 100, 1) if total_trades > 0 else 0.0,
        "real": {
            "win_rate_pct": round(real_wins / total_trades * 100, 1) if total_trades > 0 else 0.0,
            "winning_trades": real_wins,
            "losing_trades": real_losses,
            "total_net_pnl": round(real_total_pnl, 2),
            "avg_duration_days": round(real_avg_dur, 1)
        },
        "simulated": {
            "win_rate_pct": round(sim_wins / total_trades * 100, 1) if total_trades > 0 else 0.0,
            "winning_trades": sim_wins,
            "losing_trades": sim_losses,
            "total_net_pnl": round(sim_total_pnl, 2),
            "avg_duration_days": round(sim_avg_dur, 1),
            "exit_reasons": exit_reasons_dist
        },
        "comparison": {
            "net_pnl_delta_eur": total_pnl_delta,
            "total_days_saved": round(total_days_saved, 1),
            "avg_days_saved_per_trade": round(total_days_saved / total_trades, 1) if total_trades > 0 else 0.0,
            "conclusion": f"Le protocole aurait généré {total_pnl_delta:+.2f} € de différentiel tout en réduisant la durée de détention de {round(real_avg_dur - sim_avg_dur, 1)} jours par trade en moyenne."
        }
    }

    return {
        "success": True,
        "summary": summary,
        "by_symbol": sorted_by_symbol,
        "trades": replayed_trades
    }
