"""
Moteur d'Analyse Post-Trade & Feedback Protocole (Trading Agent v2.0)
--------------------------------------------------------------------
Compare les trades exécutés issus du journal de trading aux règles du Protocole
en 8 étapes (Durée de détention, Paliers TP1 +1.25% / TP2 +2.25%, Stop Loss et Cassures).
Génère des diagnostics statistiques, un score de discipline /100 et des recommandations chiffrées.
"""

from datetime import datetime
import math
import logging

logger = logging.getLogger("protocol_feedback_engine")

TARGET_TP1_PCT = 1.25      # Palier 1 : Sécurisation initiale
TARGET_TP2_PCT = 2.25      # Palier 2 : Accélération du rebond
STRICT_STOP_LOSS_PCT = -2.5 # Seuil d'invalidation max recommandé
OPTIMAL_MAX_DAYS = 5.0     # Fenêtre de détention optimale (Mean Reversion)
MAX_HOLDING_DAYS_LIMIT = 10.0 # Seuil d'overholding (Time Stop)


def calculate_trade_duration_days(entry_date_str, exit_date_str):
    """
    Calcule la durée de détention exacte en jours décimaux.
    """
    if not entry_date_str or not exit_date_str:
        return 1.0
    try:
        s_entry = str(entry_date_str)[:19].replace("T", " ")
        s_exit = str(exit_date_str)[:19].replace("T", " ")
        
        fmt = "%Y-%m-%d %H:%M:%S" if len(s_entry) > 10 else "%Y-%m-%d"
        fmt_exit = "%Y-%m-%d %H:%M:%S" if len(s_exit) > 10 else "%Y-%m-%d"
        
        d1 = datetime.strptime(s_entry, fmt)
        d2 = datetime.strptime(s_exit, fmt_exit)
        diff_sec = (d2 - d1).total_seconds()
        return max(0.01, round(diff_sec / 86400.0, 2))
    except Exception:
        return 1.0


def audit_single_trade(trade):
    """
    Génère un audit individuel d'un trade par rapport au protocole.
    """
    symbol = trade.get("symbol", "N/A")
    pnl_pct = float(trade.get("pnl_pct", 0.0))
    pnl_amt = float(trade.get("pnl_amount", 0.0))
    pru = float(trade.get("pru", 0.0))
    exit_p = float(trade.get("exit_price", 0.0))
    qty = float(trade.get("quantity", 1.0))
    currency = trade.get("currency", "EUR")
    
    dur_days = calculate_trade_duration_days(trade.get("entry_date") or trade.get("open_time"), 
                                             trade.get("exit_date") or trade.get("close_time"))
    
    # Évaluation du trade
    status = "NEUTRE"
    status_label = "Conforme"
    badge_class = "badge-neutral"
    score = 100
    points_forts = []
    axes_amelioration = []
    
    is_win = pnl_pct >= 0.0
    
    # 1. Évaluation Durée
    if dur_days <= OPTIMAL_MAX_DAYS:
        points_forts.append(f"Durée optimale ({dur_days:.1f}j <= {OPTIMAL_MAX_DAYS:.0f}j)")
    elif dur_days > MAX_HOLDING_DAYS_LIMIT:
        axes_amelioration.append(f"Position conservée trop longtemps ({dur_days:.1f}j > {MAX_HOLDING_DAYS_LIMIT:.0f}j). Risque d'usure de capital.")
        score -= 20
    else:
        points_forts.append(f"Durée acceptable ({dur_days:.1f}j)")
        
    # 2. Évaluation Sortie / TP / SL
    if is_win:
        if pnl_pct >= TARGET_TP2_PCT:
            status = "TP2_OPTIMAL"
            status_label = "Capture Maximale TP2 🟢"
            badge_class = "badge-success"
            points_forts.append(f"Objectif TP2 atteint ou dépassé (+{pnl_pct:.2f}% >= +{TARGET_TP2_PCT}%)")
        elif pnl_pct >= TARGET_TP1_PCT - 0.25:
            status = "TP1_CONFORME"
            status_label = "Objectif TP1 Validé 🟢"
            badge_class = "badge-success"
            points_forts.append(f"Objectif TP1 atteint (+{pnl_pct:.2f}%)")
        else:
            status = "SORTIE_PREMATUREE"
            status_label = "Sortie Prématurée 🟡"
            badge_class = "badge-warning"
            shortfall_pct = round(TARGET_TP1_PCT - pnl_pct, 2)
            missed_gain = round((pru * (TARGET_TP1_PCT / 100.0) - (exit_p - pru)) * qty, 2)
            axes_amelioration.append(f"Sortie avant le TP1 cible (+{pnl_pct:.2f}% vs +{TARGET_TP1_PCT}%). Manque à gagner estimé: +{max(0.0, missed_gain):.2f} {currency}.")
            score -= 15
    else:
        if pnl_pct >= STRICT_STOP_LOSS_PCT:
            status = "STOP_CONTROLE"
            status_label = "Perte Maîtrisée 🟡"
            badge_class = "badge-warning"
            points_forts.append(f"Perte coupée proprement ({pnl_pct:.2f}% >= {STRICT_STOP_LOSS_PCT}%)")
        else:
            status = "CASSURE_NON_COUPEE"
            status_label = "Cassure Non Coupée 🔴"
            badge_class = "badge-danger"
            excess_loss_pct = abs(pnl_pct - STRICT_STOP_LOSS_PCT)
            excess_loss_amt = round(abs(pnl_amt) - (pru * abs(STRICT_STOP_LOSS_PCT) / 100.0 * qty), 2)
            axes_amelioration.append(f"Dépassement du stop loss conseillé ({pnl_pct:.2f}% vs {STRICT_STOP_LOSS_PCT}%). Perte excédentaire évitable: {max(0.0, excess_loss_amt):.2f} {currency}.")
            score -= 40

    score = max(10, min(100, score))
    
    return {
        "trade_id": trade.get("id"),
        "symbol": symbol,
        "name": trade.get("name") or trade.get("company_name") or symbol,
        "entry_date": trade.get("entry_date") or trade.get("open_time"),
        "exit_date": trade.get("exit_date") or trade.get("close_time"),
        "duration_days": dur_days,
        "pru": pru,
        "exit_price": exit_p,
        "quantity": qty,
        "pnl_amount": pnl_amt,
        "pnl_pct": pnl_pct,
        "currency": currency,
        "account": trade.get("account") or trade.get("account_type", "CTO Euro"),
        "broker": trade.get("broker", "XTB"),
        "status_code": status,
        "status_label": status_label,
        "badge_class": badge_class,
        "discipline_score": score,
        "points_forts": points_forts,
        "axes_amelioration": axes_amelioration
    }


def analyze_executed_trades_against_protocol(trades_list):
    """
    Analyse globale de l'ensemble des trades clôturés par rapport au Protocole.
    Renvoie le score de discipline, l'analyse par durée, l'analyse TP1/TP2,
    l'analyse des pertes et les recommandations concrètes.
    """
    if not trades_list:
        return {
            "success": False,
            "total_trades": 0,
            "error": "Aucun trade disponible pour l'analyse."
        }

    total_trades = len(trades_list)
    audited_trades = [audit_single_trade(t) for t in trades_list]
    
    # 1. Distribution par Tranche de Durée
    duration_buckets = {
        "infra_1d": {"label": "< 1 jour (Intraday)", "count": 0, "wins": 0, "losses": 0, "pnl_eur": 0.0, "pnl_usd": 0.0, "total_pnl_pct": 0.0},
        "1_to_3d": {"label": "1 à 3 jours (Rebond Rapide)", "count": 0, "wins": 0, "losses": 0, "pnl_eur": 0.0, "pnl_usd": 0.0, "total_pnl_pct": 0.0},
        "3_to_5d": {"label": "3 à 5 jours (Swing Standard)", "count": 0, "wins": 0, "losses": 0, "pnl_eur": 0.0, "pnl_usd": 0.0, "total_pnl_pct": 0.0},
        "5_to_10d": {"label": "5 à 10 jours (Extension)", "count": 0, "wins": 0, "losses": 0, "pnl_eur": 0.0, "pnl_usd": 0.0, "total_pnl_pct": 0.0},
        "over_10d": {"label": "> 10 jours (Overholding)", "count": 0, "wins": 0, "losses": 0, "pnl_eur": 0.0, "pnl_usd": 0.0, "total_pnl_pct": 0.0}
    }
    
    durations = []
    for at in audited_trades:
        d = at["duration_days"]
        durations.append(d)
        pnl = at["pnl_amount"]
        pct = at["pnl_pct"]
        is_w = pct >= 0
        curr = at["currency"]
        
        if d < 1.0:
            key = "infra_1d"
        elif d <= 3.0:
            key = "1_to_3d"
        elif d <= 5.0:
            key = "3_to_5d"
        elif d <= 10.0:
            key = "5_to_10d"
        else:
            key = "over_10d"
            
        b = duration_buckets[key]
        b["count"] += 1
        b["total_pnl_pct"] += pct
        if curr == "USD":
            b["pnl_usd"] += pnl
        else:
            b["pnl_eur"] += pnl
            
        if is_w:
            b["wins"] += 1
        else:
            b["losses"] += 1

    # Calcul des métriques finales par tranche
    for key, b in duration_buckets.items():
        cnt = b["count"]
        b["win_rate_pct"] = round((b["wins"] / cnt * 100), 1) if cnt > 0 else 0.0
        b["avg_pnl_pct"] = round(b["total_pnl_pct"] / cnt, 2) if cnt > 0 else 0.0
        b["pnl_eur"] = round(b["pnl_eur"], 2)
        b["pnl_usd"] = round(b["pnl_usd"], 2)

    avg_duration = round(sum(durations) / total_trades, 1) if total_trades > 0 else 0.0
    sorted_dur = sorted(durations)
    median_duration = sorted_dur[len(sorted_dur) // 2] if sorted_dur else 0.0
    
    # Comparaison Durée Rapide (<= 5j) vs Overholding (> 10j)
    fast_trades = [at for at in audited_trades if at["duration_days"] <= 5.0]
    slow_trades = [at for at in audited_trades if at["duration_days"] > 10.0]
    
    fast_win_rate = round(len([t for t in fast_trades if t["pnl_pct"] >= 0]) / len(fast_trades) * 100, 1) if fast_trades else 0.0
    slow_win_rate = round(len([t for t in slow_trades if t["pnl_pct"] >= 0]) / len(slow_trades) * 100, 1) if slow_trades else 0.0
    
    # 2. Analyse des Prises de Bénéfices (TP1 & TP2)
    winning_trades = [at for at in audited_trades if at["pnl_pct"] >= 0]
    total_wins = len(winning_trades)
    
    premature_exits = [at for at in winning_trades if at["pnl_pct"] < 1.0]
    tp1_exits = [at for at in winning_trades if 1.0 <= at["pnl_pct"] < 2.0]
    tp2_exits = [at for at in winning_trades if at["pnl_pct"] >= 2.0]
    
    # Estimation du manque à gagner sur sorties prématurées
    money_left_on_table_eur = 0.0
    for at in premature_exits:
        if at["currency"] == "EUR":
            # Si avait attendu TP1 (+1.25%)
            gain_ideal = at["pru"] * (TARGET_TP1_PCT / 100.0) * at["quantity"]
            gain_reel = at["pnl_amount"]
            if gain_ideal > gain_reel:
                money_left_on_table_eur += (gain_ideal - gain_reel)
    money_left_on_table_eur = round(money_left_on_table_eur, 2)
    
    # 3. Analyse des Pertes & Cassures de Support
    losing_trades = [at for at in audited_trades if at["pnl_pct"] < 0]
    total_losses = len(losing_trades)
    
    controlled_losses = [at for at in losing_trades if at["pnl_pct"] >= STRICT_STOP_LOSS_PCT]
    moderate_losses = [at for at in losing_trades if -4.0 <= at["pnl_pct"] < STRICT_STOP_LOSS_PCT]
    excessive_losses = [at for at in losing_trades if at["pnl_pct"] < -4.0]
    
    # Économies potentielles si stop strict -2.5% avait été appliqué
    potential_loss_savings_eur = 0.0
    for at in losing_trades:
        if at["pnl_pct"] < STRICT_STOP_LOSS_PCT and at["currency"] == "EUR":
            loss_cap = at["pru"] * (abs(STRICT_STOP_LOSS_PCT) / 100.0) * at["quantity"]
            actual_loss = abs(at["pnl_amount"])
            if actual_loss > loss_cap:
                potential_loss_savings_eur += (actual_loss - loss_cap)
    potential_loss_savings_eur = round(potential_loss_savings_eur, 2)
    
    # 4. Calcul du Score Global de Discipline Protocole (0 à 100)
    score_sum = sum(at["discipline_score"] for at in audited_trades)
    global_discipline_score = round(score_sum / total_trades, 1) if total_trades > 0 else 0.0
    
    if global_discipline_score >= 90:
        discipline_rank = "Maître du Protocole 💎"
        discipline_badge = "badge-success"
        discipline_desc = "Exécution institutionnelle quasi parfaite. Respect strict des stops et des prises de profit."
    elif global_discipline_score >= 75:
        discipline_rank = "Exécution Solide 🟢"
        discipline_badge = "badge-success"
        discipline_desc = "Très bon respect des règles du protocole. Quelques ajustements possibles sur le timing de sortie."
    elif global_discipline_score >= 60:
        discipline_rank = "Amélioration Possible 🟡"
        discipline_badge = "badge-warning"
        discipline_desc = "Des sorties prématurées ou des stops dépassés érodent une partie des gains théoriques."
    else:
        discipline_rank = "Vigilance Requise 🔴"
        discipline_badge = "badge-danger"
        discipline_desc = "Trop de cassures non coupées ou de positions gardées trop longtemps. Risque de drawdown excessif."

    # 5. Synthèse & Recommandations Concrètes (Actionable Insights)
    recommendations = []
    
    # Recommandation 1 : Durée & Time Stop
    if slow_win_rate < fast_win_rate - 10:
        recommendations.append({
            "topic": "Durée de Détention & Time Stop",
            "icon": "⏱️",
            "severity": "HAUTE",
            "stat": f"Win Rate 0-5j : {fast_win_rate}% vs >10j : {slow_win_rate}%",
            "action": f"Activer un **Time Stop systématique à J+{int(OPTIMAL_MAX_DAYS)} ou J+7**. Vos trades mean reversion atteignent leur rentabilité maximale dans les 72 premières heures. Au-delà de 10 jours, le risque d'usure augmente considérablement."
        })
        
    # Recommandation 2 : Stop Loss & Cassures de Support
    if len(excessive_losses) > 0:
        recommendations.append({
            "topic": "Gestion des Cassures de Support",
            "icon": "🛡️",
            "severity": "CRITIQUE" if potential_loss_savings_eur > 200 else "MOYENNE",
            "stat": f"{len(excessive_losses)} trades avec perte > -4.0% (Économie possible: +{potential_loss_savings_eur:.2f} €)",
            "action": f"Couper strictement à **{STRICT_STOP_LOSS_PCT}% maximum** sur invalidation de support. Éviter d'espérer un rebond tardif quand la structure technique se dégrade."
        })
        
    # Recommandation 3 : Prises de bénéfices TP1
    if len(premature_exits) > 15:
        recommendations.append({
            "topic": "Prises de Bénéfices TP1 (+1.25%)",
            "icon": "🎯",
            "severity": "MOYENNE",
            "stat": f"{len(premature_exits)} trades sortis sous les +1.0% (Manque à gagner: +{money_left_on_table_eur:.2f} €)",
            "action": f"Laisser courir jusqu'au premier palier cible de **+{TARGET_TP1_PCT}%**. Les sorties à +0.3% / +0.5% réduisent le ratio Gain/Perte global du portefeuille."
        })
        
    # Recommandation 4 : Conservation des Vainqueurs TP2
    if len(tp2_exits) > total_wins * 0.4:
        recommendations.append({
            "topic": "Excellente Capture de Momentum (TP2+)",
            "icon": "🚀",
            "severity": "POSITIF",
            "stat": f"{len(tp2_exits)} trades clôturés au-dessus de +2.0% ({round(len(tp2_exits)/total_wins*100, 1)}% des gains)",
            "action": "Conserver la règle de sortie partielle au TP1 (+1.25%) avec trailing stop breakeven pour laisser courir le solde vers le TP2 (+2.25%)."
        })

    # Performance théorique optimisée
    net_pnl_eur_actuel = sum(at["pnl_amount"] for at in audited_trades if at["currency"] == "EUR")
    net_pnl_eur_optimise = round(net_pnl_eur_actuel + potential_loss_savings_eur + (money_left_on_table_eur * 0.7), 2)
    gain_optimisation_total = round(net_pnl_eur_optimise - net_pnl_eur_actuel, 2)

    # 6. Audit détaillé par Action (Ticker Level Breakdown)
    ticker_audits = audit_by_ticker(trades_list)

    return {
        "success": True,
        "total_trades": total_trades,
        "discipline_score": global_discipline_score,
        "discipline_rank": discipline_rank,
        "discipline_badge": discipline_badge,
        "discipline_desc": discipline_desc,
        "duration_metrics": {
            "avg_duration_days": avg_duration,
            "median_duration_days": median_duration,
            "fast_trades_count": len(fast_trades),
            "fast_trades_win_rate": fast_win_rate,
            "slow_trades_count": len(slow_trades),
            "slow_trades_win_rate": slow_win_rate,
            "buckets": duration_buckets
        },
        "tp_metrics": {
            "target_tp1_pct": TARGET_TP1_PCT,
            "target_tp2_pct": TARGET_TP2_PCT,
            "total_winning_trades": total_wins,
            "premature_count": len(premature_exits),
            "premature_pct": round(len(premature_exits) / total_wins * 100, 1) if total_wins > 0 else 0.0,
            "tp1_count": len(tp1_exits),
            "tp1_pct": round(len(tp1_exits) / total_wins * 100, 1) if total_wins > 0 else 0.0,
            "tp2_count": len(tp2_exits),
            "tp2_pct": round(len(tp2_exits) / total_wins * 100, 1) if total_wins > 0 else 0.0,
            "money_left_on_table_eur": money_left_on_table_eur
        },
        "sl_metrics": {
            "strict_sl_pct": STRICT_STOP_LOSS_PCT,
            "total_losing_trades": total_losses,
            "controlled_count": len(controlled_losses),
            "controlled_pct": round(len(controlled_losses) / total_losses * 100, 1) if total_losses > 0 else 0.0,
            "moderate_count": len(moderate_losses),
            "moderate_pct": round(len(moderate_losses) / total_losses * 100, 1) if total_losses > 0 else 0.0,
            "excessive_count": len(excessive_losses),
            "excessive_pct": round(len(excessive_losses) / total_losses * 100, 1) if total_losses > 0 else 0.0,
            "potential_loss_savings_eur": potential_loss_savings_eur
        },
        "optimization_simulation": {
            "actual_pnl_eur": round(net_pnl_eur_actuel, 2),
            "optimized_pnl_eur": net_pnl_eur_optimise,
            "potential_gain_eur": gain_optimisation_total
        },
        "recommendations": recommendations,
        "ticker_audits": ticker_audits,
        "audited_trades": audited_trades
    }


def audit_by_ticker(trades_list):
    """
    Regroupe et audite l'ensemble des trades exécutés par action/symbole.
    Fournit les statistiques agrégées (Win Rate, PnL, Score de Discipline, Durée)
    et la liste complète des trades pour chaque titre.
    """
    if not trades_list:
        return []
    
    from collections import defaultdict
    by_sym = defaultdict(list)
    for t in trades_list:
        sym = str(t.get("symbol", "")).upper().strip()
        if sym:
            by_sym[sym].append(t)
            
    ticker_summaries = []
    for sym, t_list in by_sym.items():
        audits = [audit_single_trade(t) for t in t_list]
        audits.sort(key=lambda x: str(x.get("exit_date") or x.get("entry_date") or ""), reverse=True)
        
        total_t = len(audits)
        wins = [a for a in audits if a["pnl_pct"] >= 0]
        losses = [a for a in audits if a["pnl_pct"] < 0]
        win_rate = round(len(wins) / total_t * 100, 1) if total_t > 0 else 0.0
        
        total_pnl = round(sum(a["pnl_amount"] for a in audits), 2)
        avg_pnl_pct = round(sum(a["pnl_pct"] for a in audits) / total_t, 2) if total_t > 0 else 0.0
        avg_dur = round(sum(a["duration_days"] for a in audits) / total_t, 1) if total_t > 0 else 0.0
        
        # Discipline Score moyen
        disc_score = round(sum(a["discipline_score"] for a in audits) / total_t, 1) if total_t > 0 else 0.0
        
        # Sorties
        tp2_cnt = len([a for a in audits if a["status_code"] == "TP2_OPTIMAL"])
        tp1_cnt = len([a for a in audits if a["status_code"] == "TP1_CONFORME"])
        premature_cnt = len([a for a in audits if a["status_code"] == "SORTIE_PREMATUREE"])
        controlled_loss_cnt = len([a for a in audits if a["status_code"] == "STOP_CONTROLE"])
        excessive_loss_cnt = len([a for a in audits if a["status_code"] == "CASSURE_NON_COUPEE"])
        
        currency = audits[0]["currency"] if audits else "EUR"
        name = audits[0]["name"] if audits else sym
        accounts = list(set(a["account"] for a in audits if a.get("account")))
        
        # Diagnostic personnalisé pour cette action
        strengths = []
        improvements = []
        if win_rate >= 85.0:
            strengths.append(f"Taux de réussite remarquable ({win_rate}% sur {total_t} trades).")
        if avg_dur <= 5.0:
            strengths.append(f"Durée moyenne conforme au mean reversion ({avg_dur}j).")
        if tp2_cnt >= total_t * 0.4:
            strengths.append(f"Excellente capture des extensions TP2 ({tp2_cnt} trades sur {total_t}).")
            
        if avg_dur > 15.0:
            improvements.append(f"Durée moyenne excessive ({avg_dur}j). Risque d'usure de capital.")
        if premature_cnt > total_t * 0.3:
            improvements.append(f"{premature_cnt} sorties prématurées sous les +1.0% (gains coupés trop tôt).")
        if excessive_loss_cnt > 0:
            improvements.append(f"{excessive_loss_cnt} cassures de support non coupées (> -2.5%).")
            
        if not strengths:
            strengths.append("Historique de trading régulier.")
        if not improvements:
            improvements.append("Exécution irréprochable sur cette action.")
            
        ticker_summaries.append({
            "symbol": sym,
            "name": name,
            "currency": currency,
            "accounts": accounts,
            "total_trades": total_t,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl_pct": avg_pnl_pct,
            "avg_duration_days": avg_dur,
            "discipline_score": disc_score,
            "tp2_count": tp2_cnt,
            "tp1_count": tp1_cnt,
            "premature_count": premature_cnt,
            "controlled_loss_count": controlled_loss_cnt,
            "excessive_loss_count": excessive_loss_cnt,
            "strengths": strengths,
            "improvements": improvements,
            "trades": audits
        })
        
    ticker_summaries.sort(key=lambda x: (x["total_trades"], x["total_pnl"]), reverse=True)
    return ticker_summaries


def get_ticker_deep_audit(symbol, trades_list):
    """
    Renvoie l'audit approfondi d'une action spécifique à partir de son symbole.
    """
    if not symbol or not trades_list:
        return None
    sym_upper = str(symbol).upper().strip()
    all_summaries = audit_by_ticker(trades_list)
    for s in all_summaries:
        if s["symbol"] == sym_upper:
            return s
    return None

