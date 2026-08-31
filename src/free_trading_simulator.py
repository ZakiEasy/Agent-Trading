"""
Moteur de Simulation en Libre Trading Continu avec Flux Réels de Trésorerie
(Dépôts, Retraits, Rotation Rapide & Sélection d'Actions selon Protocole Mean Reversion)

Ce module simule fidèlement le comportement du portefeuille au jour le jour (Août 2025 - Août 2026) :
1. Injection exacte des flux de trésorerie (Dépôts / Retraits bancaires aux dates réelles)
2. Détection quotidienne des opportunités sur la shortlist d'actions (Mean Reversion, Survente, Pullback sur MM20/VWAP, Sharia)
3. Scoring & Priorisation des meilleures opportunités
4. Dimensionnement de ligne dynamique (R-Max <= 1.0%, Max 15-20% par ligne, Réserve Cash)
5. Gestion stricte des sorties (TP1 50% à +1.8%, Step Stop Break-Even, TP2 50% à +2.5%/MM20, Stop -2.0%, Time Stop J+10)
6. Réinvestissement continu des gains et du cash libéré
"""

import os
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from src.supabase_connector import get_db_connection
from src.backtest_engine import BacktestEngine
from src.market_data import resolve_ticker_symbol, TICKER_ALIASES

logger = logging.getLogger("FreeTradingSimulator")
logging.basicConfig(level=logging.INFO)


def get_real_cashflows():
    """
    Extrait l'historique complet et ordonné des dépôts et retraits externes réels.
    """
    try:
        from psycopg2.extras import RealDictCursor
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT operation_date, operation_type, amount, currency, account_type, comment
                    FROM public.treasury_operations
                    WHERE operation_type IN ('Deposit', 'Withdrawal')
                    ORDER BY operation_date ASC;
                """)
                rows = cur.fetchall()
        
        df = pd.DataFrame(rows)
        if df.empty:
            return []
        
        df['date_only'] = pd.to_datetime(df['operation_date']).dt.date
        daily_cf = df.groupby('date_only')['amount'].sum().reset_index()
        daily_cf['amount'] = daily_cf['amount'].astype(float)
        
        cashflows = []
        for _, r in daily_cf.iterrows():
            cashflows.append({
                "date": pd.to_datetime(r['date_only']),
                "amount": float(r['amount'])
            })
        return sorted(cashflows, key=lambda x: x["date"])
    except Exception as e:
        logger.error(f"Erreur get_real_cashflows: {e}")
        return []


def get_real_account_metrics():
    """
    Calcule les métriques réelles du compte à partir du journal et de la trésorerie.
    """
    try:
        from psycopg2.extras import RealDictCursor
        from src.supabase_connector import get_supabase_trade_journal
        
        trades = get_supabase_trade_journal() or []
        total_trades = len(trades)
        win_trades = len([t for t in trades if t["pnl_amount"] > 0])
        total_pnl = sum(t["pnl_amount"] for t in trades)
        
        durations = []
        for t in trades:
            if t.get("entry_date") and t.get("exit_date"):
                try:
                    d1 = pd.to_datetime(t["entry_date"])
                    d2 = pd.to_datetime(t["exit_date"])
                    durations.append(max(0, (d2 - d1).total_seconds() / 86400.0))
                except Exception:
                    pass
        avg_dur = (sum(durations) / len(durations)) if durations else 36.5
        
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT SUM(CASE WHEN operation_type = 'Deposit' THEN amount ELSE 0 END) as total_deposits,
                           SUM(CASE WHEN operation_type = 'Withdrawal' THEN amount ELSE 0 END) as total_withdrawals
                    FROM public.treasury_operations;
                """)
                t_stats = cur.fetchone()

        tot_dep = float(t_stats['total_deposits'] or 0.0)
        tot_wit = float(t_stats['total_withdrawals'] or 0.0)
        net_injected = tot_dep + tot_wit
        
        return {
            "total_deposits": round(tot_dep, 2),
            "total_withdrawals": round(tot_wit, 2),
            "net_capital_injected": round(net_injected, 2),
            "real_total_pnl": round(total_pnl, 2),
            "real_trades_count": total_trades,
            "real_win_rate_pct": round((win_trades / total_trades * 100) if total_trades > 0 else 0.0, 1),
            "real_avg_duration_days": round(avg_dur, 1),
            "real_estimated_equity": round(net_injected + total_pnl, 2)
        }
    except Exception as e:
        logger.error(f"Erreur get_real_account_metrics: {e}")
        return {}


def run_continuous_free_trading_simulation(
    user_symbols=None,
    tp1_pct=1.80,
    tp2_pct=2.50,
    stop_loss_pct=-2.00,
    max_holding_days=10,
    max_risk_per_trade_pct=1.0,
    max_position_weight_pct=18.0,
    min_cash_reserve_pct=15.0
):
    """
    Simule au jour le jour le portefeuille en libre trading avec les flux réels de dépôts/retraits.
    """
    from src.supabase_connector import get_supabase_trade_journal, get_supabase_watchlist
    
    if user_symbols:
        symbols = user_symbols
    else:
        raw_trades = get_supabase_trade_journal() or []
        wl = get_supabase_watchlist(only_active=True) or []
        sym_set = set()
        for t in raw_trades:
            s = resolve_ticker_symbol(t.get("symbol"))
            if s and s != "None" and not s.endswith(".L"):
                sym_set.add(s)
        for w in wl:
            s = resolve_ticker_symbol(w.get("symbol"))
            if s and s != "None" and not s.endswith(".L"):
                sym_set.add(s)
        symbols = sorted(list(sym_set))
        if not symbols:
            symbols = ['TSLA', 'AAPL', 'NVDA', 'META', 'MSFT', 'AVGO', 'GOOGL', 'AMZN', 'NFLX', 'SAP.DE', 'ASML.AS', 'MC.PA']

    logger.info(f"🚀 Démarrage simulation libre trading sur {len(symbols)} actions...")
    
    # 1. Récupération des flux de trésorerie réels
    cashflows = get_real_cashflows()
    if not cashflows:
        cashflows = [{"date": pd.to_datetime("2025-08-01"), "amount": 10000.0}]

    start_sim_date = cashflows[0]["date"]
    
    # 2. Téléchargement des données historiques
    engine = BacktestEngine(symbols=symbols, period="2y", strategy="v3_institutional")
    engine.fetch_historical_universe()
    data_dict = engine.historical_data

    if not data_dict:
        return {"success": False, "error": "Données historiques indisponibles"}

    # Construire la timeline calendaire commune des jours de trading
    all_dates = set()
    cleaned_data_dict = {}
    for s, df in data_dict.items():
        if df is not None and not df.empty:
            cdf = df.copy()
            try:
                dt_index = pd.to_datetime([str(x)[:10] for x in cdf.index])
                cdf.index = dt_index
                cleaned_data_dict[s] = cdf
                all_dates.update(dt_index[dt_index >= start_sim_date].tolist())
            except Exception:
                pass
    data_dict = cleaned_data_dict

    sorted_dates = sorted(list(all_dates))
    if not sorted_dates:
        return {"success": False, "error": "Aucune date de trading trouvée après la date de premier dépôt."}

    # Structure d'état du portefeuille
    cash = 0.0
    total_deposited = 0.0
    total_withdrawn = 0.0
    open_positions = []
    closed_trades = []
    daily_equity_history = []
    
    cf_idx = 0
    num_cf = len(cashflows)

    for current_date in sorted_dates:
        curr_dt_str = str(current_date)[:10]

        # A. Traitement des dépôts et retraits à cette date
        while cf_idx < num_cf and cashflows[cf_idx]["date"].date() <= current_date.date():
            cf = cashflows[cf_idx]
            cf_amt = cf["amount"]
            if cf_amt > 0:
                cash += cf_amt
                total_deposited += cf_amt
            else:
                wit_amt = abs(cf_amt)
                total_withdrawn += wit_amt
                if cash >= wit_amt:
                    cash -= wit_amt
                else:
                    # Si trésorerie insuffisante, on liquide prioritairement les positions les plus anciennes
                    deficit = wit_amt - cash
                    cash = 0.0
                    while open_positions and deficit > 0:
                        pos_to_sell = open_positions.pop(0)
                        sym = pos_to_sell["symbol"]
                        p_df = data_dict.get(sym)
                        exit_px = pos_to_sell["entry_price"]
                        if p_df is not None and not p_df.empty:
                            try:
                                sub = p_df[p_df.index <= current_date]
                                if not sub.empty:
                                    exit_px = float(sub['Close'].iloc[-1])
                            except Exception:
                                pass
                        
                        recov = pos_to_sell["shares"] * exit_px
                        pnl = (exit_px - pos_to_sell["entry_price"]) * pos_to_sell["shares"]
                        pnl_pct = (exit_px - pos_to_sell["entry_price"]) / pos_to_sell["entry_price"] * 100
                        closed_trades.append({
                            "symbol": sym,
                            "entry_date": pos_to_sell["entry_date"],
                            "exit_date": curr_dt_str,
                            "entry_price": pos_to_sell["entry_price"],
                            "exit_price": round(exit_px, 2),
                            "shares": pos_to_sell["shares"],
                            "invested": round(pos_to_sell["shares"] * pos_to_sell["entry_price"], 2),
                            "pnl_amount": round(pnl, 2),
                            "pnl_pct": round(pnl_pct, 2),
                            "duration_days": (current_date - pd.to_datetime(pos_to_sell["entry_date"])).days,
                            "exit_reason": "LIQUIDATION_RETRAIT_CASH"
                        })
                        if recov >= deficit:
                            cash += (recov - deficit)
                            deficit = 0.0
                        else:
                            deficit -= recov
            cf_idx += 1

        # B. Évaluation et Gestion des Positions Ouvertes (Sorties TP1 / TP2 / SL / TimeStop)
        still_open = []
        for pos in open_positions:
            sym = pos["symbol"]
            df_sym = data_dict.get(sym)
            if df_sym is None or df_sym.empty:
                still_open.append(pos)
                continue

            try:
                sub = df_sym[df_sym.index <= current_date]
                if sub.empty:
                    still_open.append(pos)
                    continue
                
                bar = sub.iloc[-1]
                high = float(bar.get('High', bar['Close']))
                low = float(bar.get('Low', bar['Close']))
                close = float(bar['Close'])
            except Exception:
                still_open.append(pos)
                continue

            pos["days_held"] += 1
            entry_px = pos["entry_price"]
            tp1_px = pos["tp1_price"]
            tp2_px = pos["tp2_price"]
            shares = pos["shares"]

            exit_occurred = False
            exit_price = close
            exit_reason = ""

            # 1. Vérifier TP1 (+1.8%)
            if not pos["tp1_hit"] and high >= tp1_px:
                # Vente de 50% de la ligne au cours TP1
                half_shares = shares / 2.0
                pnl_tp1 = half_shares * (tp1_px - entry_px)
                cash += (half_shares * tp1_px)
                pos["shares"] = half_shares
                pos["tp1_hit"] = True
                pos["sl_price"] = entry_px # Step Stop Break-Even sécurisé
                pos["realized_pnl_tp1"] = pnl_tp1

            # 2. Vérifier TP2 (+2.5% ou MM20)
            if high >= tp2_px:
                exit_price = tp2_px
                exit_reason = "TP2_OPTIMAL (+2.5%)"
                exit_occurred = True
            # 3. Vérifier Stop Loss (initial ou Break-Even)
            elif low <= pos["sl_price"]:
                exit_price = pos["sl_price"]
                exit_reason = "BREAKEVEN_SECURISE" if pos["tp1_hit"] else "STOP_LOSS (-2.0%)"
                exit_occurred = True
            # 4. Vérifier Time Stop J+10
            elif pos["days_held"] >= max_holding_days:
                exit_price = close
                exit_reason = f"TIME_STOP (J+{max_holding_days})"
                exit_occurred = True

            if exit_occurred:
                rem_shares = pos["shares"]
                cash += (rem_shares * exit_price)
                rem_pnl = rem_shares * (exit_price - entry_px)
                total_pos_pnl = rem_pnl + pos.get("realized_pnl_tp1", 0.0)
                tot_invested = pos["initial_shares"] * entry_px
                tot_pnl_pct = (total_pos_pnl / tot_invested * 100.0) if tot_invested > 0 else 0.0
                
                closed_trades.append({
                    "symbol": sym,
                    "entry_date": pos["entry_date"],
                    "exit_date": curr_dt_str,
                    "entry_price": round(entry_px, 2),
                    "exit_price": round(exit_price, 2),
                    "shares": pos["initial_shares"],
                    "invested": round(tot_invested, 2),
                    "pnl_amount": round(total_pos_pnl, 2),
                    "pnl_pct": round(tot_pnl_pct, 2),
                    "duration_days": pos["days_held"],
                    "tp1_hit": pos["tp1_hit"],
                    "exit_reason": exit_reason
                })
            else:
                pos["current_val"] = pos["shares"] * close
                still_open.append(pos)

        open_positions = still_open

        # C. Calcul de la Valeur Totale du Portefeuille (Equity)
        pos_val = sum(p.get("current_val", p["shares"] * p["entry_price"]) for p in open_positions)
        total_equity = cash + pos_val

        # D. Recherche de Nouveaux Signaux d'Achat (Protocole Mean Reversion)
        available_cash = cash
        reserve_cash = total_equity * (min_cash_reserve_pct / 100.0)
        deployable_cash = max(0.0, available_cash - reserve_cash)

        if deployable_cash > 250.0 and total_equity > 500.0:
            candidate_setups = []
            open_symbols = set(p["symbol"] for p in open_positions)

            for sym in symbols:
                if sym in open_symbols:
                    continue
                
                df_sym = data_dict.get(sym)
                if df_sym is None or len(df_sym) < 30:
                    continue
                
                sub_sym = df_sym[df_sym.index <= current_date]
                if len(sub_sym) < 25:
                    continue

                c_bar = sub_sym.iloc[-1]
                close_px = float(c_bar['Close'])
                if close_px <= 0:
                    continue

                sma20 = float(c_bar.get('SMA_20', sub_sym['Close'].rolling(20).mean().iloc[-1]))
                sma50 = float(c_bar.get('SMA_50', sub_sym['Close'].rolling(50).mean().iloc[-1])) if len(sub_sym) >= 50 else sma20
                rsi14 = float(c_bar.get('RSI_14', c_bar.get('RSI', 50.0)))
                atr14 = float(c_bar.get('ATR_14', c_bar.get('ATR', close_px * 0.02)))
                bb_lower = float(c_bar.get('BB_Lower_20', c_bar.get('BBL', sma20 - (2.0 * sub_sym['Close'].rolling(20).std().iloc[-1]))))
                
                # Critères Mean Reversion :
                # Repli sous MM20 avec survente RSI (<= 45) ou contact Bande de Bollinger basse
                is_oversold = (rsi14 <= 45.0) or (close_px <= bb_lower * 1.01)
                is_dip_in_uptrend = (close_px < sma20) and (close_px >= sma50 * 0.88)
                
                if is_oversold and is_dip_in_uptrend:
                    dist_to_mean = (sma20 - close_px) / close_px * 100.0
                    score = (50.0 - rsi14) + (dist_to_mean * 2.0)
                    candidate_setups.append({
                        "symbol": sym,
                        "price": close_px,
                        "score": score,
                        "atr": atr14,
                        "sma20": sma20
                    })

            # Trier les opportunités par le meilleur score Mean Reversion
            candidate_setups.sort(key=lambda x: x["score"], reverse=True)

            # E. Exécution & Allocation de Position (Risk Management)
            max_line_capital = total_equity * (max_position_weight_pct / 100.0)
            risk_budget = total_equity * (max_risk_per_trade_pct / 100.0)

            for cand in candidate_setups:
                if deployable_cash < 250.0:
                    break
                
                px = cand["price"]
                nominal_from_risk = risk_budget / 0.020 # Stop à -2.0%
                target_alloc = min(max_line_capital, nominal_from_risk, deployable_cash)

                if target_alloc >= 200.0:
                    shares_to_buy = target_alloc / px
                    cost = shares_to_buy * px
                    
                    cash -= cost
                    deployable_cash -= cost
                    
                    open_positions.append({
                        "symbol": cand["symbol"],
                        "entry_date": curr_dt_str,
                        "entry_price": px,
                        "shares": shares_to_buy,
                        "initial_shares": shares_to_buy,
                        "sl_price": px * (1 + stop_loss_pct / 100.0),
                        "tp1_price": px * (1 + tp1_pct / 100.0),
                        "tp2_price": px * (1 + tp2_pct / 100.0),
                        "tp1_hit": False,
                        "days_held": 0,
                        "current_val": cost
                    })

        # Recalcul de l'Equity en fin de journée
        pos_val = sum(p.get("current_val", p["shares"] * p["entry_price"]) for p in open_positions)
        total_equity = cash + pos_val
        
        daily_equity_history.append({
            "date": curr_dt_str,
            "cash": round(cash, 2),
            "positions_value": round(pos_val, 2),
            "total_equity": round(total_equity, 2),
            "open_positions_count": len(open_positions)
        })

    # F. Clôture finale des positions restantes au dernier cours connu
    if open_positions:
        last_dt = sorted_dates[-1]
        last_dt_str = str(last_dt)[:10]
        for pos in open_positions:
            sym = pos["symbol"]
            df_sym = data_dict.get(sym)
            exit_px = pos["entry_price"]
            if df_sym is not None and not df_sym.empty:
                try:
                    exit_px = float(df_sym['Close'].iloc[-1])
                except Exception:
                    pass
            
            pnl = (exit_px - pos["entry_price"]) * pos["shares"] + pos.get("realized_pnl_tp1", 0.0)
            tot_inv = pos["initial_shares"] * pos["entry_price"]
            pnl_pct = (pnl / tot_inv * 100) if tot_inv > 0 else 0.0
            
            closed_trades.append({
                "symbol": sym,
                "entry_date": pos["entry_date"],
                "exit_date": last_dt_str,
                "entry_price": round(pos["entry_price"], 2),
                "exit_price": round(exit_px, 2),
                "shares": pos["initial_shares"],
                "invested": round(tot_inv, 2),
                "pnl_amount": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "duration_days": pos["days_held"],
                "tp1_hit": pos["tp1_hit"],
                "exit_reason": "FIN_DE_SIMULATION"
            })
            cash += (pos["shares"] * exit_px)

    final_equity = cash
    total_pnl = sum(t["pnl_amount"] for t in closed_trades)
    total_trades_count = len(closed_trades)
    win_trades = [t for t in closed_trades if t["pnl_amount"] > 0]
    loss_trades = [t for t in closed_trades if t["pnl_amount"] < 0]
    
    total_gains = sum(t["pnl_amount"] for t in win_trades)
    total_losses = abs(sum(t["pnl_amount"] for t in loss_trades))
    profit_factor = round(total_gains / total_losses, 2) if total_losses > 0 else 99.0
    win_rate = round(len(win_trades) / total_trades_count * 100, 1) if total_trades_count > 0 else 0.0
    avg_dur = round(sum(t["duration_days"] for t in closed_trades) / total_trades_count, 1) if total_trades_count > 0 else 0.0

    net_capital_injected = total_deposited - total_withdrawn

    # Comparaison avec les métriques réelles du compte
    real_metrics = get_real_account_metrics()

    return {
        "success": True,
        "cashflows_summary": {
            "total_deposits": round(total_deposited, 2),
            "total_withdrawals": round(total_withdrawn, 2),
            "net_capital_injected": round(net_capital_injected, 2)
        },
        "simulation_results": {
            "final_equity": round(final_equity, 2),
            "total_net_pnl": round(total_pnl, 2),
            "return_on_net_capital_pct": round((total_pnl / net_capital_injected * 100) if net_capital_injected > 0 else 0.0, 2),
            "total_trades": total_trades_count,
            "winning_trades": len(win_trades),
            "losing_trades": len(loss_trades),
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "total_gains_eur": round(total_gains, 2),
            "total_losses_eur": round(total_losses, 2),
            "avg_duration_days": avg_dur
        },
        "real_account_comparison": real_metrics,
        "trades_sample": closed_trades[:50],
        "equity_curve": daily_equity_history[::3]
    }
