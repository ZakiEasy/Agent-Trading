import math
from src.config import (
    CAPITAL_REFERENCE_DEFAULT,
    R_MAX_PCT_STANDARD,
    R_MAX_PCT_REDUCED,
    MAX_ALLOCATION_PER_LINE_PCT,
    MIN_CASH_RESERVE_PCT,
    MAX_SECTOR_POSITIONS,
    MAX_SIMULTANEOUS_POSITIONS,
    WEEKLY_DRAWDOWN_LIMIT_PCT,
    TARGET_TP1_DEFAULT,
    TARGET_TP2_DEFAULT,
    HOLDING_PERIOD_DAYS,
    HOLDING_PERIOD_MIN_DAYS,
    HOLDING_PERIOD_MAX_DAYS
)

def calculate_trade_sizing(
    capital_total=CAPITAL_REFERENCE_DEFAULT,
    entry_price=100.0,
    stop_loss_price=97.0,
    macro_regime="RÉGIME RISK-ON (Favorable)",
    is_drawdown_circuit_breaker=False,
    tp1_pct=TARGET_TP1_DEFAULT,
    tp2_pct=TARGET_TP2_DEFAULT
):
    """
    Calcule le dimensionnement exact du trade selon le Principe du R-Max (Section 6.B).
    Formule :
      Distance Stop-Loss (%) = (Prix Entrée - Prix Stop-Loss) / Prix Entrée
      R-Max (€) = Capital Total * (1.0% en Risk-On, 0.5% en Neutre/Vigilance ou Drawdown)
      Allocation Nominale (€) = min( R-Max (€) / Distance Stop-Loss (%), 0.25 * Capital Total )
    """
    if entry_price <= 0:
        entry_price = 100.0
    if stop_loss_price >= entry_price or stop_loss_price <= 0:
        stop_loss_price = entry_price * 0.97

    # 1. Distance au Stop-Loss
    stop_distance_pct = (entry_price - stop_loss_price) / entry_price
    if stop_distance_pct <= 0:
        stop_distance_pct = 0.03

    # 2. Détermination du taux R-Max selon le régime macro et l'état de drawdown
    if "RISK-OFF" in macro_regime.upper():
        r_max_pct = 0.0
    elif is_drawdown_circuit_breaker or "NEUTRE" in macro_regime.upper() or "VIGILANCE" in macro_regime.upper() or "CONTRARIENNE" in macro_regime.upper():
        r_max_pct = R_MAX_PCT_REDUCED # 0.5%
    else:
        r_max_pct = R_MAX_PCT_STANDARD # 1.0%

    r_max_amount = capital_total * r_max_pct

    # 3. Allocation Nominale Théorique & Plafond de 25% par ligne
    max_line_nominal = capital_total * MAX_ALLOCATION_PER_LINE_PCT
    if r_max_pct > 0 and stop_distance_pct > 0:
        raw_nominal = r_max_amount / stop_distance_pct
        suggested_nominal = min(raw_nominal, max_line_nominal)
    else:
        suggested_nominal = 0.0

    # 4. Nombre d'actions entier (Comptes cash / au comptant sans fractionnement)
    shares_count = math.floor(suggested_nominal / entry_price) if entry_price > 0 else 0
    actual_nominal = shares_count * entry_price
    actual_monetary_risk = shares_count * (entry_price - stop_loss_price)

    # 5. Cibles de gains TP1 et TP2
    tp1_price = entry_price * (1 + tp1_pct / 100)
    tp2_price = entry_price * (1 + tp2_pct / 100)
    
    potential_gain_tp1_pct = tp1_pct
    potential_gain_tp2_pct = tp2_pct
    potential_loss_pct = stop_distance_pct * 100

    potential_gain_tp1_amount = shares_count * (tp1_price - entry_price)
    potential_gain_tp2_amount = shares_count * (tp2_price - entry_price)

    # 6. Ratios Rendement / Risque (R:R)
    rr_tp1 = potential_gain_tp1_pct / potential_loss_pct if potential_loss_pct > 0 else 0.0
    rr_tp2 = potential_gain_tp2_pct / potential_loss_pct if potential_loss_pct > 0 else 0.0

    # 7. Réserve de liquidités minimale (25% à 30%)
    cash_reserve_required = capital_total * MIN_CASH_RESERVE_PCT

    return {
        "capital_reference": capital_total,
        "entry_price": entry_price,
        "stop_loss_price": stop_loss_price,
        "stop_distance_pct": potential_loss_pct,
        "r_max_pct": r_max_pct * 100,
        "r_max_amount": r_max_amount,
        "max_line_limit": max_line_nominal,
        "suggested_nominal": suggested_nominal,
        "actual_nominal": actual_nominal,
        "shares_count": shares_count,
        "actual_monetary_risk": actual_monetary_risk,
        "cash_reserve_required": cash_reserve_required,
        "tp1_price": tp1_price,
        "tp1_pct": potential_gain_tp1_pct,
        "tp1_gain_amount": potential_gain_tp1_amount,
        "tp2_price": tp2_price,
        "tp2_pct": potential_gain_tp2_pct,
        "tp2_gain_amount": potential_gain_tp2_amount,
        "risk_reward_tp1": rr_tp1,
        "risk_reward_tp2": rr_tp2,
        "holding_period_days": HOLDING_PERIOD_DAYS,
        "time_stop": f"Invalidation temporelle : Clôture obligatoire à J+{HOLDING_PERIOD_DAYS} ouvrés si TP non atteint",
        "holding_range": f"{HOLDING_PERIOD_MIN_DAYS} à {HOLDING_PERIOD_MAX_DAYS} jours (cible médiane {HOLDING_PERIOD_DAYS}j ouvrés)"
    }

def calculate_confluence_score(
    sharia_res,
    macro_barometer,
    drop_details,
    has_qualified_drop,
    tech_setup,
    has_blackout,
    trade_plan,
    fund_quality=None,
    sector_strength=None
):
    """
    Calcule le Score de Confluence Globale (0 à 10 points) et attribue le verdict final (Section 4.8).
    
    Barème de Confluence (10 Points) :
    1. Conformité Sharia (2 pts) : Statut CONFORME
    2. Contexte Macro & Secteur (2 pts) : Risk-On (+1.5) ou Neutre (+0.5), Force relative sectorielle (+0.5)
    3. Qualification du Dip (2 pts) : Baisse de -3% à -8% conjoncturelle (+2), modérée (+1)
    4. Analyse Technique & Divergence (2 pts) : SMA 200 haussière / Support (+1), Divergence RSI haussière / Rejet (+1)
    5. Dynamique Flux & R:R (2 pts) : Volume / QQE (+1), Ratio R:R >= 1:1.0 (+1)
    """
    score = 0
    breakdown = []
    fund_quality = fund_quality or {}
    sector_strength = sector_strength or {}

    # 1. Conformité Sharia (Normes AAOIFI)
    sharia_status = sharia_res.get("status", "DONNÉES INSUFFISANTES")
    if sharia_status == "CONFORME":
        score += 2
        breakdown.append({"criterion": "1. Conformité Sharia (AAOIFI)", "points": 2, "max": 2, "status": "Validé 🕌 (Ratios < 33% Cap 24m)"})
    elif sharia_status == "DONNÉES INSUFFISANTES":
        score += 0
        breakdown.append({"criterion": "1. Conformité Sharia (AAOIFI)", "points": 0, "max": 2, "status": "Données insuffisantes ⚠️"})
    else:
        breakdown.append({"criterion": "1. Conformité Sharia (AAOIFI)", "points": 0, "max": 2, "status": "Non Conforme ❌"})

    # 2. Contexte Macroéconomique & Tendance Sectorielle
    regime = macro_barometer.get("regime", "")
    rel_status = sector_strength.get("relative_strength", "EN LIGNE")
    macro_pts = 0
    if "RISK-ON" in regime.upper():
        macro_pts += 1.5
    elif "CONTRARIENNE" in regime.upper():
        macro_pts += 1.5
    elif "NEUTRE" in regime.upper() or "VIGILANCE" in regime.upper():
        macro_pts += 0.5

    if rel_status == "SURPERFORMANCE":
        macro_pts += 0.5
    elif rel_status == "EN LIGNE":
        macro_pts += 0.25

    macro_pts_rounded = min(2, math.ceil(macro_pts))
    score += macro_pts_rounded
    breakdown.append({
        "criterion": "2. Macro & Force Sectorielle",
        "points": macro_pts_rounded,
        "max": 2,
        "status": f"{regime} | Secteur : {rel_status}"
    })

    # 3. Qualification du Dip (-3% à -8%)
    drop_pct = drop_details.get("drop_pct", 0.0)
    drop_nature = drop_details.get("nature", "")
    if has_qualified_drop and -8.0 <= drop_pct <= -3.0 and "CONJONCTURELLE" in drop_nature.upper():
        score += 2
        breakdown.append({"criterion": "3. Dip Conjoncturel (-3% à -8%)", "points": 2, "max": 2, "status": f"Optimal ({drop_pct:.2f}%) 🎯 Mispricing"})
    elif -3.0 < drop_pct <= -1.5:
        score += 1
        breakdown.append({"criterion": "3. Dip Conjoncturel (-3% à -8%)", "points": 1, "max": 2, "status": f"Modéré ({drop_pct:.2f}%) ⚖️"})
    else:
        breakdown.append({"criterion": "3. Dip Conjoncturel (-3% à -8%)", "points": 0, "max": 2, "status": f"Hors fenêtre ({drop_pct:.2f}%) ⚪"})

    # 4. Analyse Technique & Divergence RSI
    tech_pts = 0
    if tech_setup.get("is_above_sma200", False) or tech_setup.get("mrc_oversold", False):
        tech_pts += 1
        
    has_div = tech_setup.get("rsi_divergence", {}).get("has_divergence", False)
    has_rejection = tech_setup.get("support_rejection", False)
    if has_div or has_rejection:
        tech_pts += 1
    elif tech_setup.get("rsi", 50) < 35:
        tech_pts += 0.5
        
    tech_pts_rounded = min(2, math.ceil(tech_pts))
    score += tech_pts_rounded
    breakdown.append({
        "criterion": "4. Technique, Divergence RSI & Rejet",
        "points": tech_pts_rounded,
        "max": 2,
        "status": f"{'Divergence RSI 🔥' if has_div else 'Mèche de Rejet 🟢' if has_rejection else 'Support / SMA 200'}"
    })

    # 5. Dynamique Flux & Ratio Risque / Rendement
    flux_pts = 0
    if tech_setup.get("volume_confirmed", False) or tech_setup.get("qqe_buy_signal", False):
        flux_pts += 1
    if trade_plan.get("risk_reward_tp1", 0) >= 0.8:
        flux_pts += 1
        
    score += flux_pts
    breakdown.append({
        "criterion": "5. Flux Volume / QQE & R:R",
        "points": flux_pts,
        "max": 2,
        "status": f"R:R 1:{trade_plan.get('risk_reward_tp1', 0):.2f} ({'Volume Confirmé' if tech_setup.get('volume_confirmed') else 'Standard'})"
    })

    # Filtres éliminatoires (Hard Filters) & Verdict Décisionnel
    is_large_cap = fund_quality.get("is_large_cap", True)
    has_min_liquidity = fund_quality.get("has_min_liquidity", True)

    if sharia_status == "NON CONFORME":
        verdict = "ÉVITER - HORS CRITÈRES (Non conforme Sharia)"
        decision_badge = "danger"
        synthesis = "Exclusion immédiate : le titre ne satisfait pas aux critères éthiques Sharia (AAOIFI)."
    elif sharia_status == "DONNÉES INSUFFISANTES":
        verdict = "ÉVITER - HORS CRITÈRES (Données Sharia insuffisantes)"
        decision_badge = "danger"
        synthesis = "Arrêt de l'analyse : Données financières ou bilan insuffisants pour certifier la conformité AAOIFI."
    elif not is_large_cap:
        verdict = "ÉVITER - HORS CRITÈRES (Cap < 2 Mrd)"
        decision_badge = "danger"
        synthesis = "Capitalisation boursière inférieure au filtre institutionnel de 2 Mrd €/$."
    elif not has_min_liquidity:
        verdict = "ÉVITER - HORS CRITÈRES (Liquidité < 1 M€/$)"
        decision_badge = "danger"
        synthesis = "Volume quotidien moyen inférieur à 1 M€/$. Risque de slippage trop élevé."
    elif has_blackout:
        verdict = "ÉVITER - HORS CRITÈRES (Blackout Résultats < 10j)"
        decision_badge = "danger"
        synthesis = "Publication de résultats ou événement majeur sous 10 jours ouvrés. Risque de gap non maîtrisable."
    elif "RISK-OFF" in regime.upper():
        verdict = "GEL TOTAL DES ACHATS (Macro Risk-Off)"
        decision_badge = "danger"
        synthesis = "Le baromètre macroéconomique global impose la conservation des liquidités (Cash is a position)."
    elif not tech_setup.get("is_above_sma200", True) and not "CONTRARIENNE" in regime.upper() and not has_div:
        verdict = "ATTENDRE REPLI SUR SUPPORT (Sous SMA 200)"
        decision_badge = "neutral"
        synthesis = "Titre sous sa SMA 200 sans divergence haussière confirmée. Attendre une structure de retournement."
    elif score >= 7 and (has_qualified_drop or has_div or has_rejection):
        verdict = "ACHETER LE REBOND"
        decision_badge = "success"
        synthesis = f"Excellente confluence ({score}/10). Excès vendeur conjoncturel sur support clé avec catalyseur technique."
    elif score >= 4:
        verdict = "ATTENDRE REPLI SUR SUPPORT"
        decision_badge = "neutral"
        synthesis = f"Configuration en observation (Score {score}/10). Attendre un test propre du support ou une mèche de rejet."
    else:
        verdict = "ÉVITER - HORS CRITÈRES"
        decision_badge = "danger"
        synthesis = f"Score de confluence insuffisant ({score}/10). Pas d'avantage statistique pour un swing trade 10 jours."

    return {
        "confluence_score": score,
        "score_max": 10,
        "verdict": verdict,
        "decision_badge": decision_badge,
        "synthesis": synthesis,
        "breakdown": breakdown
    }
