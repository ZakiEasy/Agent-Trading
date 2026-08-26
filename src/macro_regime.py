import time
from datetime import datetime
import yfinance as yf
import pandas as pd
import numpy as np
from src.config import (
    MACRO_TICKERS,
    VIX_FAVORABLE_MAX,
    VIX_ALERT_MAX,
    VIX_RISK_OFF_MAX,
    VIX_CONTRARIAN_SPIKE,
    DXY_FAVORABLE_MAX,
    DXY_ALERT_MAX,
    YIELD_CURVE_FAVORABLE_MIN,
    YIELD_CURVE_INVERTED_MAX,
    OIL_MONTHLY_ALERT_PCT,
    OIL_MONTHLY_PARABOLIC_PCT
)

# Cache en mémoire des indicateurs macro (durée de validité : 5 minutes)
_macro_cache = None
_macro_cache_time = 0
CACHE_TTL_SECONDS = 300

def _get_clean_history(ticker_symbol, period="1mo"):
    """
    Récupère l'historique nettoyé des valeurs NaN d'un ticker yfinance.
    """
    try:
        t = yf.Ticker(ticker_symbol)
        hist = t.history(period=period)
        if hist.empty:
            return None
        hist = hist.dropna(subset=['Close'])
        return hist
    except Exception as e:
        print(f"Erreur fetch macro ticker {ticker_symbol}: {e}")
        return None

def fetch_raw_macro_indicators():
    """
    Interroge les marchés financiers pour récupérer les 5 indicateurs macroéconomiques clés.
    """
    macro_data = {}

    # 1. VIX (Volatilité S&P 500)
    vix_hist = _get_clean_history(MACRO_TICKERS["VIX"], period="1mo")
    if vix_hist is not None and not vix_hist.empty:
        vix_val = float(vix_hist['Close'].values[-1])
        vix_prev = float(vix_hist['Close'].values[-2]) if len(vix_hist) > 1 else vix_val
        macro_data["VIX"] = {
            "value": vix_val,
            "change_1d": ((vix_val - vix_prev) / vix_prev) * 100 if vix_prev else 0.0,
            "raw": vix_val
        }
    else:
        macro_data["VIX"] = {"value": 16.5, "change_1d": 0.0, "raw": 16.5}

    # 2. DXY (Dollar Index)
    dxy_hist = _get_clean_history(MACRO_TICKERS["DXY"], period="1mo")
    if dxy_hist is None or dxy_hist.empty:
        dxy_hist = _get_clean_history(MACRO_TICKERS["DXY_ALT"], period="1mo")
        
    if dxy_hist is not None and not dxy_hist.empty:
        dxy_val = float(dxy_hist['Close'].values[-1])
        dxy_prev = float(dxy_hist['Close'].values[-2]) if len(dxy_hist) > 1 else dxy_val
        macro_data["DXY"] = {
            "value": dxy_val,
            "change_1d": ((dxy_val - dxy_prev) / dxy_prev) * 100 if dxy_prev else 0.0,
            "raw": dxy_val
        }
    else:
        macro_data["DXY"] = {"value": 103.0, "change_1d": 0.0, "raw": 103.0}

    # 3. Ratio Sectoriel XLY / XLP (Consommation Discrétionnaire vs Défensive)
    xly_hist = _get_clean_history(MACRO_TICKERS["XLY"], period="1mo")
    xlp_hist = _get_clean_history(MACRO_TICKERS["XLP"], period="1mo")
    
    if xly_hist is not None and xlp_hist is not None and not xly_hist.empty and not xlp_hist.empty:
        common_idx = xly_hist.index.intersection(xlp_hist.index)
        if len(common_idx) > 1:
            ratio_series = xly_hist.loc[common_idx, 'Close'] / xlp_hist.loc[common_idx, 'Close']
            current_ratio = float(ratio_series.values[-1])
            prev_ratio = float(ratio_series.values[-5]) if len(ratio_series) >= 5 else float(ratio_series.values[0])
            ratio_change_5d = ((current_ratio - prev_ratio) / prev_ratio) * 100 if prev_ratio else 0.0
            macro_data["XLY_XLP"] = {
                "value": current_ratio,
                "change_5d": ratio_change_5d,
                "is_rising": bool(ratio_change_5d > 0)
            }
        else:
            macro_data["XLY_XLP"] = {"value": 1.40, "change_5d": 0.5, "is_rising": True}
    else:
        macro_data["XLY_XLP"] = {"value": 1.40, "change_5d": 0.0, "is_rising": True}

    # 4. Courbe des Taux US (10Y - 2Y Yield Spread)
    tnx_hist = _get_clean_history(MACRO_TICKERS["TNX_10Y"], period="5d")
    irx_hist = _get_clean_history(MACRO_TICKERS["IRX_2Y"], period="5d")
    
    if tnx_hist is not None and irx_hist is not None and not tnx_hist.empty and not irx_hist.empty:
        # ^TNX est le taux 10Y (en %), ^IRX est le taux 13-week (en %)
        tnx_val = float(tnx_hist['Close'].values[-1])
        # Si ^TNX est exprimé sous forme de 45.0 pour 4.5%, on normalise
        if tnx_val > 20:
            tnx_val = tnx_val / 10.0
            
        irx_val = float(irx_hist['Close'].values[-1])
        if irx_val > 20:
            irx_val = irx_val / 10.0
            
        spread = tnx_val - irx_val
        macro_data["YIELD_CURVE"] = {
            "spread": spread,
            "10y_yield": tnx_val,
            "2y_yield": irx_val
        }
    else:
        macro_data["YIELD_CURVE"] = {"spread": 0.40, "10y_yield": 4.4, "2y_yield": 4.0}

    # 5. Pétrole WTI & Brent
    wti_hist = _get_clean_history(MACRO_TICKERS["WTI"], period="1mo")
    if wti_hist is not None and not wti_hist.empty:
        wti_val = float(wti_hist['Close'].values[-1])
        wti_start_month = float(wti_hist['Close'].values[0]) if len(wti_hist) > 0 else wti_val
        wti_change_month = ((wti_val - wti_start_month) / wti_start_month) * 100 if wti_start_month else 0.0
        macro_data["WTI"] = {
            "value": wti_val,
            "change_1m": wti_change_month
        }
    else:
        macro_data["WTI"] = {"value": 75.0, "change_1m": 1.0}

    # Matières Premières Additionnelles (Or, Brent)
    brent_hist = _get_clean_history(MACRO_TICKERS["BRENT"], period="5d")
    gold_hist = _get_clean_history(MACRO_TICKERS["GOLD"], period="5d")
    
    macro_data["BRENT"] = {"value": float(brent_hist['Close'].values[-1]) if brent_hist is not None and not brent_hist.empty else 80.0}
    macro_data["GOLD"] = {"value": float(gold_hist['Close'].values[-1]) if gold_hist is not None and not gold_hist.empty else 2400.0}

    return macro_data

def evaluate_macro_indicators(raw_data):
    """
    Évalue chaque indicateur macro selon les seuils institutionnels de la Section 2.
    """
    evaluations = {}
    scores = []

    # 1. Évaluation VIX
    vix = raw_data["VIX"]["value"]
    if vix > VIX_CONTRARIAN_SPIKE:
        evaluations["VIX"] = {
            "value": f"{vix:.2f}",
            "status": "EXCEPTION CONTRARIENNE",
            "badge": "warning",
            "desc": "Spike de panique extrême (>35). Opportunité contrarienne d'achats fractionnés sur supports.",
            "favorable": True,
            "score": 1
        }
        scores.append(1)
    elif vix < VIX_FAVORABLE_MAX:
        evaluations["VIX"] = {
            "value": f"{vix:.2f}",
            "status": "RISK-ON (Favorable)",
            "badge": "success",
            "desc": "Marché calme et serein (<18). Faible demande de couverture.",
            "favorable": True,
            "score": 1
        }
        scores.append(1)
    elif vix <= VIX_ALERT_MAX:
        evaluations["VIX"] = {
            "value": f"{vix:.2f}",
            "status": "ALERTE / NEUTRE",
            "badge": "neutral",
            "desc": "Zone de vigilance (18-25). Risque de volatilité accrue.",
            "favorable": False,
            "score": 0
        }
        scores.append(0)
    else:
        evaluations["VIX"] = {
            "value": f"{vix:.2f}",
            "status": "RISK-OFF (Défavorable)",
            "badge": "danger",
            "desc": "Stress haussier non stabilisé (25-35). Pression vendeuse forte.",
            "favorable": False,
            "score": -1
        }
        scores.append(-1)

    # 2. Évaluation DXY
    dxy = raw_data["DXY"]["value"]
    if dxy < DXY_FAVORABLE_MAX:
        evaluations["DXY"] = {
            "value": f"{dxy:.2f}",
            "status": "RISK-ON (Favorable)",
            "badge": "success",
            "desc": "Dollar stable ou baissier (<102). Liquidité mondiale abondante.",
            "favorable": True,
            "score": 1
        }
        scores.append(1)
    elif dxy <= DXY_ALERT_MAX:
        evaluations["DXY"] = {
            "value": f"{dxy:.2f}",
            "status": "CONSOLIDATION / NEUTRE",
            "badge": "neutral",
            "desc": "Dollar en consolidation (102-105). Impact neutre sur la liquidité.",
            "favorable": False,
            "score": 0
        }
        scores.append(0)
    else:
        evaluations["DXY"] = {
            "value": f"{dxy:.2f}",
            "status": "RISK-OFF (Défavorable)",
            "badge": "danger",
            "desc": "Tendance haussière forte (>105). Resserrement des liquidités mondiales.",
            "favorable": False,
            "score": -1
        }
        scores.append(-1)

    # 3. Évaluation Ratio Sectoriel XLY / XLP
    xly_xlp = raw_data["XLY_XLP"]
    if xly_xlp["is_rising"]:
        evaluations["XLY_XLP"] = {
            "value": f"{xly_xlp['value']:.3f} (+{xly_xlp['change_5d']:.1f}%)",
            "status": "RISK-ON (Favorable)",
            "badge": "success",
            "desc": "Ratio en hausse. Les investisseurs privilégient la croissance et prennent du risque.",
            "favorable": True,
            "score": 1
        }
        scores.append(1)
    elif abs(xly_xlp["change_5d"]) <= 0.5:
        evaluations["XLY_XLP"] = {
            "value": f"{xly_xlp['value']:.3f} ({xly_xlp['change_5d']:.1f}%)",
            "status": "NEUTRE / PLAT",
            "badge": "neutral",
            "desc": "Équilibre entre secteurs cycliques et défensifs.",
            "favorable": False,
            "score": 0
        }
        scores.append(0)
    else:
        evaluations["XLY_XLP"] = {
            "value": f"{xly_xlp['value']:.3f} ({xly_xlp['change_5d']:.1f}%)",
            "status": "RISK-OFF (Défavorable)",
            "badge": "danger",
            "desc": "Ratio en baisse continue. Rotation des capitaux vers les valeurs défensives.",
            "favorable": False,
            "score": -1
        }
        scores.append(-1)

    # 4. Évaluation Yield Curve (10Y - 2Y)
    spread = raw_data["YIELD_CURVE"]["spread"]
    if spread > YIELD_CURVE_FAVORABLE_MIN:
        evaluations["YIELD_CURVE"] = {
            "value": f"+{spread:.2f}%",
            "status": "RISK-ON (Favorable)",
            "badge": "success",
            "desc": "Écart positif et stable (> +0,20%). Cycle économique régulier.",
            "favorable": True,
            "score": 1
        }
        scores.append(1)
    elif spread >= YIELD_CURVE_INVERTED_MAX:
        evaluations["YIELD_CURVE"] = {
            "value": f"{spread:+.2f}%",
            "status": "NEUTRE / TRANSITION",
            "badge": "neutral",
            "desc": "Écart proche de zéro. Incertitude sur les perspectives de politique monétaire.",
            "favorable": False,
            "score": 0
        }
        scores.append(0)
    else:
        evaluations["YIELD_CURVE"] = {
            "value": f"{spread:.2f}%",
            "status": "RISK-OFF (Inversion)",
            "badge": "danger",
            "desc": "Inversion prononcée (< -0,20%). Signal avancé de ralentissement ou récession.",
            "favorable": False,
            "score": -1
        }
        scores.append(-1)

    # 5. Évaluation Pétrole WTI
    wti = raw_data["WTI"]
    if wti["change_1m"] <= OIL_MONTHLY_ALERT_PCT:
        evaluations["WTI"] = {
            "value": f"{wti['value']:.2f} $ ({wti['change_1m']:+.1f}%/mois)",
            "status": "RISK-ON (Favorable)",
            "badge": "success",
            "desc": "Cours du pétrole stables ou en baisse contrôlée. Pression inflationniste modérée.",
            "favorable": True,
            "score": 1
        }
        scores.append(1)
    elif wti["change_1m"] <= OIL_MONTHLY_PARABOLIC_PCT:
        evaluations["WTI"] = {
            "value": f"{wti['value']:.2f} $ ({wti['change_1m']:+.1f}%/mois)",
            "status": "ALERTE (Hausse modérée)",
            "badge": "neutral",
            "desc": "Hausse modérée des cours énergétiques (+5% à +20%). Vigilance sur l'inflation.",
            "favorable": False,
            "score": 0
        }
        scores.append(0)
    else:
        evaluations["WTI"] = {
            "value": f"{wti['value']:.2f} $ ({wti['change_1m']:+.1f}%/mois)",
            "status": "RISK-OFF (Hausse parabolique)",
            "badge": "danger",
            "desc": "Hausse parabolique du brut (> +20%). Risque de choc sur les coûts de production.",
            "favorable": False,
            "score": -1
        }
        scores.append(-1)

    return evaluations, scores

def determine_global_macro_regime(evaluations, scores, raw_data):
    """
    Synthétise le Régime de Marché Global et applique les règles d'exposition de la Section 2.B.
    """
    vix_val = raw_data["VIX"]["value"]
    total_score = sum(scores)

    # 1. Exception Contrarienne (Spike VIX > 35-40)
    if vix_val >= VIX_CONTRARIAN_SPIKE:
        return {
            "regime": "EXCEPTION CONTRARIENNE",
            "badge": "warning",
            "sizing_multiplier": 0.5,
            "r_max_pct": 0.005,
            "action_rule": "Spike VIX > 35-40 : Panique maximale. Achats fractionnés autorisés uniquement sur supports majeurs.",
            "allowed_to_trade": True,
            "summary": "Marché en panique extrême. Opportunités de rebond asymétriques avec entrées échelonnées et risque strict."
        }

    # 2. Régime Risk-On (Favorable)
    if total_score >= 2 and vix_val < VIX_ALERT_MAX and raw_data["DXY"]["value"] <= DXY_ALERT_MAX:
        return {
            "regime": "RÉGIME RISK-ON (Favorable)",
            "badge": "success",
            "sizing_multiplier": 1.0,
            "r_max_pct": 0.010,
            "action_rule": "Autorisation 100% de la taille standard. Swing trading actif sur setups qualifiés.",
            "allowed_to_trade": True,
            "summary": "Environnement porteur et liquidité saine. Les acheteurs interviennent sur les replis."
        }

    # 3. Régime Neutre / Vigilance
    if total_score >= -1 and vix_val <= VIX_ALERT_MAX:
        return {
            "regime": "RÉGIME NEUTRE / VIGILANCE",
            "badge": "neutral",
            "sizing_multiplier": 0.5,
            "r_max_pct": 0.005,
            "action_rule": "Réduction de la taille par ligne à 50% du nominal (R-Max = 0,5%). Niveaux d'entrée très stricts.",
            "allowed_to_trade": True,
            "summary": "Contexte macro contrasté. Sélectivité maximale et dimensionnement défensif."
        }

    # 4. Régime Risk-Off / Panic Runaway (Défavorable)
    return {
        "regime": "RÉGIME RISK-OFF / PANIC RUNAWAY",
        "badge": "danger",
        "sizing_multiplier": 0.0,
        "r_max_pct": 0.0,
        "action_rule": "GEL TOTAL des nouveaux achats. Préservation maximale du cash. Aucune nouvelle ouverture de position.",
        "allowed_to_trade": False,
        "summary": "Stress de marché élevé ou resserrement de liquidité. Protection intégrale du capital."
    }

def get_macro_barometer(force_refresh=False):
    """
    Point d'entrée principal pour obtenir le Baromètre Macroéconomique complet.
    Utilise le cache mémoire pour des performances optimales.
    """
    global _macro_cache, _macro_cache_time
    now = time.time()
    now_dt = datetime.now()
    analysis_date = now_dt.strftime("%d/%m/%Y")
    analysis_time = now_dt.strftime("%H:%M:%S CET")
    analysis_timestamp = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    last_updated_str = f"{analysis_date} à {analysis_time}"

    if not force_refresh and _macro_cache is not None and (now - _macro_cache_time) < CACHE_TTL_SECONDS:
        return _macro_cache

    try:
        raw_data = fetch_raw_macro_indicators()
        evaluations, scores = evaluate_macro_indicators(raw_data)
        regime_info = determine_global_macro_regime(evaluations, scores, raw_data)

        # Ajouter l'heure sur chaque indicateur
        for k in evaluations:
            evaluations[k]["updated_at"] = analysis_time

        barometer = {
            "timestamp": analysis_timestamp,
            "analysis_date": analysis_date,
            "analysis_time": analysis_time,
            "analysis_timestamp": analysis_timestamp,
            "last_updated_str": last_updated_str,
            "regime": regime_info["regime"],
            "badge": regime_info["badge"],
            "sizing_multiplier": regime_info["sizing_multiplier"],
            "r_max_pct": regime_info["r_max_pct"],
            "action_rule": regime_info["action_rule"],
            "allowed_to_trade": regime_info["allowed_to_trade"],
            "summary": regime_info["summary"],
            "indicators": evaluations,
            "raw": raw_data,
            "commodities": {
                "Pétrole WTI": f"{raw_data['WTI']['value']:.2f} $",
                "Pétrole Brent": f"{raw_data['BRENT']['value']:.2f} $",
                "Or": f"{raw_data['GOLD']['value']:.2f} $"
            },
            "rules": [
                "Pas d'ouverture de position si CPI, réunion FED/BCE, ou NFP sous 24-48h.",
                "GEL TOTAL des nouveaux achats en Régime Risk-Off.",
                "Taille réduite à 50% du nominal (R-Max = 0,5%) en Régime Neutre / Vigilance."
            ]
        }

        _macro_cache = barometer
        _macro_cache_time = now
        return barometer
    except Exception as e:
        print(f"Erreur calcul baromètre macro : {e}")
        # En cas d'erreur réseau, renvoyer un fallback défensif
        return {
            "timestamp": analysis_timestamp,
            "analysis_date": analysis_date,
            "analysis_time": analysis_time,
            "analysis_timestamp": analysis_timestamp,
            "last_updated_str": last_updated_str,
            "regime": "RÉGIME NEUTRE / VIGILANCE (Fallback)",
            "badge": "neutral",
            "sizing_multiplier": 0.5,
            "r_max_pct": 0.005,
            "action_rule": "Réduction de la taille à 50% (Mode défensif en attente de données marché).",
            "allowed_to_trade": True,
            "summary": "Données macro temporairement estimées.",
            "indicators": {},
            "commodities": {"Pétrole WTI": "N/A", "Pétrole Brent": "N/A", "Or": "N/A"},
            "rules": [
                "Vérifier le calendrier économique réel avant d'engager du capital."
            ]
        }
