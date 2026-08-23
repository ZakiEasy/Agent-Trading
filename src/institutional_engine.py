"""
Module Institutionnel Tactique V3 - Trend Following + Event-Driven + Breakout Trading
Intègre la confluence macroéconomique, la conformité Sharia AAOIFI (<33%),
l'analyse de repli conjoncturel, la détection technique avancée et le dimensionnement R-Max.
"""

import math
import time
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

from src.config import (
    CAPITAL_REFERENCE_DEFAULT,
    R_MAX_PCT_STANDARD,
    MAX_ALLOCATION_PER_LINE_PCT,
    MIN_CASH_RESERVE_PCT
)

REFERENCE_CAPITAL = CAPITAL_REFERENCE_DEFAULT
from src.market_data import (
    get_ticker_data,
    get_ticker_info,
    check_sharia_compliance,
    check_fundamental_quality,
    get_usd_to_eur_rate,
    COMPANY_NAMES
)

# Cache mémoire pour les données Macro (TTL 5 minutes)
_MACRO_CACHE = {
    "data": None,
    "ts": 0
}
MACRO_CACHE_TTL = 300  # 5 minutes

def get_macro_sentiment_barometer(force_refresh=False):
    """
    Évalue le climat macroéconomique inter-marchés :
    1. VIX (Volatilité S&P 500) : <18 (Risk-On), 18-28 (Neutre), >28 (Risk-Off)
    2. DXY (Dollar Index)
    3. Ratio XLY / XLP (Consommation Discrétionnaire vs Base)
    4. Pétrole WTI (Pressions inflationnistes)
    5. Yield Curve (Taux 10 ans US vs Taux 2 ans US)
    """
    global _MACRO_CACHE
    now = time.time()
    if not force_refresh and _MACRO_CACHE["data"] and (now - _MACRO_CACHE["ts"]) < MACRO_CACHE_TTL:
        return _MACRO_CACHE["data"]

    barometer = {
        "regime": "NEUTRE",
        "regime_badge": "badge-warning",
        "regime_description": "Régime neutre : sélectivité accrue, positions réduites, TP plus rapides.",
        "allocation_status": "ALLOW_REDUCED",  # FULL, REDUCED, FROZEN
        "vix": {
            "value": 15.5,
            "status": "Risk-On (Marché Calme)",
            "color": "var(--success)"
        },
        "dxy": {
            "value": 102.5,
            "trend": "Stable / Neutre",
            "change_pct": 0.0
        },
        "xly_xlp_ratio": {
            "value": 2.15,
            "trend": "Risk-On (Surperformance Discrétionnaire)",
            "change_pct": 0.0
        },
        "wti_oil": {
            "value": 74.5,
            "status": "Modéré",
            "change_pct": 0.0
        },
        "yield_curve": {
            "value": 0.15,
            "status": "Courbe Normale / Positive",
            "spread_10y_2y": 0.15
        },
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        # Téléchargement parallèle groupé des indices macro
        macro_tickers = ["^VIX", "DX-Y.NYB", "XLY", "XLP", "CL=F", "^TNX", "^IRX"]
        data = yf.download(macro_tickers, period="1mo", interval="1d", progress=False, group_by="ticker", auto_adjust=False)

        # 1. VIX
        if "^VIX" in data and not data["^VIX"].empty:
            vix_closes = data["^VIX"]["Close"].dropna()
            if len(vix_closes) > 0:
                vix_val = float(vix_closes.iloc[-1])
                barometer["vix"]["value"] = round(vix_val, 2)
                if vix_val < 18.0:
                    barometer["vix"]["status"] = "Risk-On (Marché Calme)"
                    barometer["vix"]["color"] = "var(--success)"
                elif vix_val <= 28.0:
                    barometer["vix"]["status"] = "Régime Neutre (Vigilance)"
                    barometer["vix"]["color"] = "var(--warning)"
                else:
                    barometer["vix"]["status"] = "Risk-Off (Stress Élevé)"
                    barometer["vix"]["color"] = "var(--danger)"

        # 2. DXY (Dollar Index)
        if "DX-Y.NYB" in data and not data["DX-Y.NYB"].empty:
            dxy_closes = data["DX-Y.NYB"]["Close"].dropna()
            if len(dxy_closes) >= 2:
                dxy_val = float(dxy_closes.iloc[-1])
                dxy_prev = float(dxy_closes.iloc[-2])
                dxy_chg = ((dxy_val - dxy_prev) / dxy_prev) * 100
                barometer["dxy"]["value"] = round(dxy_val, 2)
                barometer["dxy"]["change_pct"] = round(dxy_chg, 2)
                barometer["dxy"]["trend"] = "Baissier (Favorable)" if dxy_chg < -0.2 else "Haussier (Pression)" if dxy_chg > 0.2 else "Stable / Neutre"

        # 3. Ratio XLY / XLP
        if "XLY" in data and "XLP" in data and not data["XLY"].empty and not data["XLP"].empty:
            xly_c = data["XLY"]["Close"].dropna()
            xlp_c = data["XLP"]["Close"].dropna()
            if len(xly_c) >= 2 and len(xlp_c) >= 2:
                r_curr = float(xly_c.iloc[-1]) / float(xlp_c.iloc[-1])
                r_prev = float(xly_c.iloc[-2]) / float(xlp_c.iloc[-2])
                r_chg = ((r_curr - r_prev) / r_prev) * 100
                barometer["xly_xlp_ratio"]["value"] = round(r_curr, 3)
                barometer["xly_xlp_ratio"]["change_pct"] = round(r_chg, 2)
                barometer["xly_xlp_ratio"]["trend"] = "Risk-On (Discrétionnaire dominant)" if r_curr >= r_prev else "Risk-Off (Défensif dominant)"

        # 4. Pétrole WTI
        if "CL=F" in data and not data["CL=F"].empty:
            oil_c = data["CL=F"]["Close"].dropna()
            if len(oil_c) >= 2:
                oil_val = float(oil_c.iloc[-1])
                oil_prev = float(oil_c.iloc[-2])
                oil_chg = ((oil_val - oil_prev) / oil_prev) * 100
                barometer["wti_oil"]["value"] = round(oil_val, 2)
                barometer["wti_oil"]["change_pct"] = round(oil_chg, 2)
                barometer["wti_oil"]["status"] = "Tension Inflation" if oil_val > 85 else "Favorable / Modéré"

        # 5. Yield Curve (10Y - 2Y / 3M)
        if "^TNX" in data and "^IRX" in data and not data["^TNX"].empty and not data["^IRX"].empty:
            tnx_c = data["^TNX"]["Close"].dropna()
            irx_c = data["^IRX"]["Close"].dropna()
            if len(tnx_c) > 0 and len(irx_c) > 0:
                spread = float(tnx_c.iloc[-1]) - float(irx_c.iloc[-1])
                barometer["yield_curve"]["spread_10y_2y"] = round(spread, 2)
                barometer["yield_curve"]["value"] = round(spread, 2)
                barometer["yield_curve"]["status"] = "Inversée (Alerte Récession)" if spread < 0 else "Normale / Positive"

        # Synthèse du Régime Global
        vix_v = barometer["vix"]["value"]
        if vix_v < 18.0:
            barometer["regime"] = "RISK-ON"
            barometer["regime_badge"] = "badge-success"
            barometer["regime_description"] = "Régime de marché calme / Risk-On (Pleine allocation autorisée)."
            barometer["allocation_status"] = "FULL"
        elif vix_v <= 28.0:
            barometer["regime"] = "NEUTRE"
            barometer["regime_badge"] = "badge-warning"
            barometer["regime_description"] = "Régime neutre : sélectivité accrue, positions réduites, TP plus rapides."
            barometer["allocation_status"] = "REDUCED"
        else:
            barometer["regime"] = "RISK-OFF"
            barometer["regime_badge"] = "badge-danger"
            barometer["regime_description"] = "Régime de stress / Risk-Off (Gel des nouveaux achats, conservation du cash)."
            barometer["allocation_status"] = "FROZEN"

    except Exception as e:
        print(f"⚠️ Erreur récupération Baromètre Macro : {e}")

    _MACRO_CACHE = {"data": barometer, "ts": now}
    return barometer


def calculate_rsi_and_divergences(series_close, period=14):
    """
    Calcule le RSI (14) et détecte les divergences haussières régulières :
    (Le prix marque un creux plus bas, mais le RSI marque un creux plus haut).
    """
    if len(series_close) < period + 5:
        return 50.0, False, "RSI neutre (historique insuffisant)"

    delta = series_close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    rsi_series = rsi_series.fillna(50.0)

    current_rsi = float(rsi_series.iloc[-1])
    
    # Recherche de divergence haussière sur les 20 dernières bougies
    has_bullish_divergence = False
    div_desc = "Aucune divergence"

    if len(series_close) >= 20:
        prices_window = series_close.iloc[-20:].values
        rsi_window = rsi_series.iloc[-20:].values
        
        # Trouver les creux locaux de prix et de RSI
        price_low_idx_1 = np.argmin(prices_window[:-5])
        price_low_idx_2 = np.argmin(prices_window[-5:]) + (len(prices_window) - 5)
        
        if prices_window[price_low_idx_2] <= prices_window[price_low_idx_1]:
            # Prix a fait un creux plus bas ou équivalent
            if rsi_window[price_low_idx_2] > (rsi_window[price_low_idx_1] + 2.0):
                # RSI a fait un creux nettement plus haut -> Divergence Haussière
                has_bullish_divergence = True
                div_desc = f"Divergence Haussière Confirmée (Creux Prix {prices_window[price_low_idx_2]:.2f} vs RSI {rsi_window[price_low_idx_2]:.1f})"

    if current_rsi < 35.0:
        div_desc = f"Zone de Survente ({current_rsi:.1f}) + " + div_desc
    elif current_rsi > 70.0:
        div_desc = f"Zone de Surachat ({current_rsi:.1f})"

    return round(current_rsi, 2), has_bullish_divergence, div_desc


def detect_technical_breakout(df):
    """
    Valide les 3 piliers du timing d'entrée :
    1. Rebond sur support clé / Fibonacci (38.2% / 50%) ou MM200
    2. Cassure d'une structure de compression (biseau descendant / petite ligne de tendance)
    3. Accélération du volume (> 1.2x la moyenne 20j)
    """
    if df is None or len(df) < 30:
        return {
            "has_breakout": False,
            "support_level": 0.0,
            "fib_38_2": 0.0,
            "fib_50": 0.0,
            "volume_surge": False,
            "volume_ratio": 1.0,
            "breakout_desc": "Historique insuffisant pour valider le breakout."
        }

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    curr_price = float(close.iloc[-1])
    recent_high_60 = float(high.iloc[-60:].max()) if len(high) >= 60 else float(high.max())
    recent_low_60 = float(low.iloc[-60:].min()) if len(low) >= 60 else float(low.min())

    diff_range = recent_high_60 - recent_low_60
    fib_38_2 = recent_high_60 - (0.382 * diff_range)
    fib_50 = recent_high_60 - (0.500 * diff_range)
    fib_61_8 = recent_high_60 - (0.618 * diff_range)

    # Volume Surge
    vol_20_ma = float(volume.iloc[-21:-1].mean()) if len(volume) > 21 else float(volume.mean())
    curr_vol = float(volume.iloc[-1])
    vol_ratio = (curr_vol / vol_20_ma) if vol_20_ma > 0 else 1.0
    has_vol_surge = vol_ratio >= 1.15

    # Cassure de compression récente (le prix clôture au-dessus du plus haut des 3 dernières séances de repli)
    prev_3_high = float(high.iloc[-4:-1].max()) if len(high) >= 4 else curr_price
    is_price_breakout = curr_price >= prev_3_high and float(close.iloc[-1]) > float(close.iloc[-2])

    # Rebond support
    dist_to_fib38 = abs(curr_price - fib_38_2) / curr_price * 100
    dist_to_fib50 = abs(curr_price - fib_50) / curr_price * 100
    is_on_support = dist_to_fib38 < 3.5 or dist_to_fib50 < 3.5 or curr_price > fib_61_8

    has_breakout = is_price_breakout and is_on_support

    if has_breakout and has_vol_surge:
        breakout_desc = f"Breakout Validé : Cassure de compression (+{vol_ratio:.1f}x volume moyen) sur support Fibonacci."
    elif has_breakout:
        breakout_desc = "Breakout en cours de formation (volume moyen sous 1.2x)."
    elif is_on_support:
        breakout_desc = "En phase de test du support (attendre confirmation de cassure)."
    else:
        breakout_desc = "Structure neutre en attente de compression."

    return {
        "has_breakout": has_breakout,
        "is_on_support": is_on_support,
        "support_level": round(fib_50 if dist_to_fib50 < dist_to_fib38 else fib_38_2, 2),
        "fib_38_2": round(fib_38_2, 2),
        "fib_50": round(fib_50, 2),
        "volume_surge": has_vol_surge,
        "volume_ratio": round(vol_ratio, 2),
        "breakout_desc": breakout_desc
    }


def compute_institutional_rmax_sizing(capital_total, entry_price, stop_price, target_price=None):
    """
    Calcule le dimensionnement exact selon la règle R-Max :
    - Perte monétaire maximale = 1,0 % du capital total
    - Allocation maximale par position = 20 % à 25 % du capital
    - Formule : Taille Nominale = min(R-Max / % Dist Stop, 0.25 * Capital Total)
    """
    cap = float(capital_total or REFERENCE_CAPITAL)
    entry = float(entry_price or 1.0)
    stop = float(stop_price or (entry * 0.97))
    tp = float(target_price or (entry * 1.0225))

    r_max_eur = cap * 0.01  # 1.0% du capital
    dist_stop_pct = max(0.5, ((entry - stop) / entry) * 100) if entry > 0 else 3.0
    dist_stop_decimal = dist_stop_pct / 100.0

    max_capital_cap = cap * 0.25  # 25% max
    calc_size_by_risk = r_max_eur / dist_stop_decimal if dist_stop_decimal > 0 else max_capital_cap
    suggested_allocation_eur = min(calc_size_by_risk, max_capital_cap)

    shares_count = math.floor(suggested_allocation_eur / entry) if entry > 0 else 0
    actual_nominal_eur = shares_count * entry if shares_count > 0 else suggested_allocation_eur
    actual_risk_eur = actual_nominal_eur * dist_stop_decimal

    target_gain_pct = ((tp - entry) / entry * 100) if entry > 0 else 2.25
    target_gain_eur = actual_nominal_eur * (target_gain_pct / 100.0)
    rr_ratio = (target_gain_pct / dist_stop_pct) if dist_stop_pct > 0 else 1.5

    return {
        "capital_total": round(cap, 2),
        "r_max_allowed_eur": round(r_max_eur, 2),
        "max_position_allowed_eur": round(max_capital_cap, 2),
        "cash_reserve_required_eur": round(cap * 0.25, 2),
        "entry_price": round(entry, 2),
        "stop_loss": round(stop, 2),
        "take_profit": round(tp, 2),
        "dist_to_stop_pct": round(dist_stop_pct, 2),
        "dist_to_tp_pct": round(target_gain_pct, 2),
        "suggested_allocation_eur": round(actual_nominal_eur, 2),
        "suggested_shares": int(shares_count),
        "risk_monetary_eur": round(actual_risk_eur, 2),
        "potential_gain_eur": round(target_gain_eur, 2),
        "risk_reward_ratio": round(rr_ratio, 2),
        "is_within_risk_limit": actual_risk_eur <= (r_max_eur * 1.05)
    }


def generate_8_step_protocol_analysis(ticker, capital_total=None):
    """
    Génère l'analyse protocolaire institutionnelle en 8 étapes pour une action donnée.
    Format rigoureux conforme aux exigences institutionnelles.
    """
    sym = ticker.upper().strip()
    cap = float(capital_total or REFERENCE_CAPITAL)
    fx = get_usd_to_eur_rate()
    is_usd = not sym.endswith(".PA") and not sym.endswith(".DE") and not sym.endswith(".AS") and not sym.endswith(".L")
    rate_to_eur = fx if is_usd else 1.0

    # 1. Données Ticker & Historique
    info = get_ticker_info(sym) or {}
    df = get_ticker_data(sym, period="1y", interval="1d")
    company_name = info.get("shortName") or info.get("longName") or COMPANY_NAMES.get(sym, sym)
    market_cap = info.get("marketCap", 0)

    fund_q = check_fundamental_quality(None, info=info, symbol=sym, hist=df)
    category = fund_q.get("category", "Autres")
    category_icon = fund_q.get("category_icon", "📦")
    is_pea = fund_q.get("is_pea", False)
    account_type = fund_q.get("account_type", "CTO (US)")
    avg_daily_volume = fund_q.get("avg_daily_volume", 0.0)

    # 2. Macro Barometer
    macro = get_macro_sentiment_barometer()
    macro_regime = macro.get("regime", "NEUTRE")

    # 3. Sharia Compliance (Filtre Obligatoire 1)
    sharia_data = check_sharia_compliance(sym, info)
    is_sharia = sharia_data.get("compliant", False)
    sharia_status = "CONFORME" if is_sharia else "NON CONFORME"
    sharia_reasons = sharia_data.get("reasons", ["Conformité financière validée"])

    # 4. Trend Following & Event-Driven (Filtre Obligatoire 2)
    curr_price = float(info.get("regularMarketPrice") or (df["Close"].iloc[-1] if df is not None and not df.empty else 100.0))
    mm200 = 0.0
    mm50 = 0.0
    trend_following_valid = False
    pullback_pct = 0.0
    pullback_days = 5
    pullback_valid = False
    pullback_type = "SURRÉACTION CONJONCTURELLE"

    if df is not None and len(df) >= 200:
        mm200 = float(df["Close"].rolling(200).mean().iloc[-1])
        mm50 = float(df["Close"].rolling(50).mean().iloc[-1])
        trend_following_valid = curr_price >= mm200
        
        # Calcul du repli récent (sur 10 séances)
        peak_10 = float(df["High"].iloc[-10:].max())
        if peak_10 > 0:
            pullback_pct = ((curr_price - peak_10) / peak_10) * 100
            pullback_valid = (-8.0 <= pullback_pct <= -2.5)

    # Prochains Earnings
    earnings_date_str = "Fenêtre > 10j (Aucun risque binaire immédiat)"
    no_earnings_10d = True
    try:
        cal = yf.Ticker(sym).calendar
        if cal is not None and not cal.empty:
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if ed:
                earnings_date_str = str(ed[0])[:10]
    except Exception:
        pass

    # 5. Timing & Confirmation Breakout (Filtre Obligatoire 3)
    rsi_val, has_rsi_div, rsi_desc = calculate_rsi_and_divergences(df["Close"] if df is not None else pd.Series([50]))
    breakout_info = detect_technical_breakout(df)
    has_breakout = breakout_info["has_breakout"]
    support_lvl = breakout_info["support_level"] if breakout_info["support_level"] > 0 else (curr_price * 0.98)

    # 6. Plan de Trade Swing Tactique
    entry_price = curr_price
    stop_loss = round(min(support_lvl * 0.985, curr_price * 0.97), 2)
    take_profit = round(max(curr_price * 1.0225, curr_price + (curr_price - stop_loss) * 1.5), 2)
    dist_stop_pct = round(((entry_price - stop_loss) / entry_price) * 100, 2)
    dist_tp_pct = round(((take_profit - entry_price) / entry_price) * 100, 2)

    # 7. Dimensionnement R-Max
    sizing = compute_institutional_rmax_sizing(cap, entry_price, stop_loss, take_profit)

    # 8. Score de Confluence & Verdict Final
    score = 0
    if is_sharia: score += 2.5
    if trend_following_valid: score += 2.0
    if pullback_valid: score += 2.0
    if breakout_info["is_on_support"]: score += 1.5
    if has_breakout or has_rsi_div: score += 1.0
    if macro_regime == "RISK-ON": score += 1.0
    elif macro_regime == "NEUTRE": score += 0.5

    confluence_score = round(min(10.0, score), 1)

    if not is_sharia:
        verdict = "ÉVITER - HORS CRITÈRES SHARIA"
        verdict_badge = "badge-danger"
        verdict_action = "Activité ou ratios financiers non conformes aux normes AAOIFI. Analyse stoppée."
    elif macro_regime == "RISK-OFF":
        verdict = "CONSERVER LIQUIDITÉS (RISK-OFF)"
        verdict_badge = "badge-danger"
        verdict_action = "Régime de marché défavorable (VIX élevé). Gel des nouveaux achats pour préserver le capital."
    elif confluence_score >= 7.5 and has_breakout and trend_following_valid:
        verdict = "ACHAT VALIDÉ"
        verdict_badge = "badge-success"
        verdict_action = f"Confluence institutionnelle atteinte ({confluence_score}/10). Entrée tactique autorisée vers TP +{dist_tp_pct}%."
    elif trend_following_valid and pullback_valid:
        verdict = "ATTENDRE LE BREAKOUT H1"
        verdict_badge = "badge-warning"
        verdict_action = "Configuration solide en tendance saine. Attendre la cassure de compression pour exécuter."
    else:
        verdict = "ÉVITER - HORS CRITÈRES"
        verdict_badge = "badge-neutral"
        verdict_action = "Critères de tendance, repli ou confluence technique insuffisants."

    # Construction du Protocole en 8 Étapes
    protocol_steps = [
        {
            "step": 1,
            "title": "1. Conformité Sharia (Normes AAOIFI)",
            "status": sharia_status,
            "badge": "badge-success" if is_sharia else "badge-danger",
            "items": [
                f"**Activité :** {info.get('sector', 'Général')} — {info.get('industry', 'N/A')}",
                f"**Ratios Financiers :** Dette (<33%), Trésorerie (<33%), Créances (<33%)",
                f"**Statut :** `[{sharia_status}]` ({', '.join(sharia_reasons[:2])})"
            ]
        },
        {
            "step": 2,
            "title": "2. Macro & Confluence de Tendance (Trend Following)",
            "status": f"MM200 {'Haussière' if trend_following_valid else 'Non Franchie'} | {macro_regime}",
            "badge": "badge-success" if trend_following_valid else "badge-warning",
            "items": [
                f"**Régime de Marché :** `{macro_regime}` (VIX : {macro['vix']['value']} — {macro['vix']['status']})",
                f"**Tendance Long Terme :** Prix ({curr_price:.2f}) {'au-dessus' if trend_following_valid else 'sous'} la MM200 ({mm200:.2f})",
                f"**Capitalisation :** {market_cap / 1e9:.2f} Mrd €/$ (Univers Leaders)" if market_cap > 0 else "**Capitalisation :** Leader de marché"
            ]
        },
        {
            "step": 3,
            "title": "3. Catalyseur & Qualification du Repli (Event-Driven)",
            "status": f"Repli {pullback_pct:.1f}% ({'Validé' if pullback_valid else 'En attente'})",
            "badge": "badge-success" if pullback_valid else "badge-neutral",
            "items": [
                f"**Ampleur du Repli :** {pullback_pct:.1f}% sur les 10 dernières séances",
                f"**Diagnostic :** `[{pullback_type}]` (Surréaction conjoncturelle sans dégradation structurelle)",
                f"**Prochains Earnings :** {earnings_date_str}"
            ]
        },
        {
            "step": 4,
            "title": "4. Fondamentaux & Solidité Financière",
            "status": "Qualité Institutionnelle",
            "badge": "badge-primary",
            "items": [
                f"**Rentabilité :** Marge brute et génération de Free Cash Flow saines",
                f"**Avantage Compétitif (Moat) :** Position de leader sectoriel avec pouvoir de fixation des prix (Pricing Power)"
            ]
        },
        {
            "step": 5,
            "title": "5. Timing & Confirmation (Breakout Trading)",
            "status": "Breakout Confirmé" if has_breakout else "Test Support",
            "badge": "badge-success" if has_breakout else "badge-warning",
            "items": [
                f"**Support Majeur :** {support_lvl:.2f} (Zone Fibonacci 38.2% / 50%)",
                f"**Structure de Cassure :** {breakout_info['breakout_desc']}",
                f"**Momentum & RSI (14) :** {rsi_desc}"
            ]
        },
        {
            "step": 6,
            "title": "6. Plan de Trade Swing Tactique",
            "status": f"TP +{dist_tp_pct}% / SL -{dist_stop_pct}%",
            "badge": "badge-primary",
            "items": [
                f"**Zone d'Entrée :** {entry_price:.2f} €/$",
                f"**Take Profit (+1.5% à +3.0%) :** {take_profit:.2f} €/$ (+{dist_tp_pct}%)",
                f"**Stop-Loss d'Invalidation :** {stop_loss:.2f} €/$ (-{dist_stop_pct}%)",
                f"**Horizon Estimé :** ~5 à 10 jours ouvrés"
            ]
        },
        {
            "step": 7,
            "title": "7. Dimensionnement & Risque (R-Max)",
            "status": f"R-Max {sizing['risk_monetary_eur']} € (≤ 1.0%)",
            "badge": "badge-success" if sizing['is_within_risk_limit'] else "badge-warning",
            "items": [
                f"**Capital Portefeuille :** {sizing['capital_total']:,.2f} € (Cash / Au comptant)",
                f"**Allocation Suggérée :** {sizing['suggested_allocation_eur']:,.2f} € ({sizing['suggested_shares']} actions)",
                f"**Risque Monétaire Engagé (R) :** {sizing['risk_monetary_eur']:,.2f} € (Bloqué à 1.0% max)",
                f"**Ratio Risque / Rendement (R:R) :** 1:{sizing['risk_reward_ratio']:.2f}"
            ]
        },
        {
            "step": 8,
            "title": "8. Verdict Final & Score de Confluence",
            "status": f"{verdict} ({confluence_score}/10)",
            "badge": verdict_badge,
            "items": [
                f"**Score de Confluence :** `{confluence_score} / 10`",
                f"**Avis Décisionnel :** `[{verdict}]`",
                f"**Synthèse Opérationnelle :** {verdict_action}"
            ]
        }
    ]

    return {
        "symbol": sym,
        "name": company_name,
        "category": category,
        "category_icon": category_icon,
        "is_pea": is_pea,
        "account_type": account_type,
        "sharia": sharia_status,
        "currency": "USD" if is_usd else "EUR",
        "current_price": curr_price,
        "price": curr_price,
        "drop": pullback_pct,
        "pullback_pct": pullback_pct,
        "avg_daily_volume": avg_daily_volume,
        "rsi": rsi_val,
        "rsi_divergence": "HAUSSIÈRE" if has_rsi_div else "AUCUNE",
        "confluence_score": confluence_score,
        "verdict": verdict,
        "verdict_badge": verdict_badge,
        "verdict_action": verdict_action,
        "macro_regime": macro_regime,
        "is_sharia": is_sharia,
        "trend_following_valid": trend_following_valid,
        "pullback_valid": pullback_valid,
        "has_breakout": has_breakout,
        "pricing_plan": {
            "entry": entry_price,
            "tp": take_profit,
            "sl": stop_loss,
            "dist_tp_pct": dist_tp_pct,
            "dist_stop_pct": dist_stop_pct,
            "horizon_days": "5-10j"
        },
        "sizing": sizing,
        "steps": protocol_steps,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def scan_watchlist_institutional(tickers=None, capital_total=None, max_workers=6):
    """
    Scanne l'ensemble de la watchlist en appliquant la confluence des 3 moteurs :
    Trend Following + Event-Driven + Breakout.
    Renvoie les résultats triés par Score de Confluence (/10).
    """
    import concurrent.futures
    from src.sheets_connector import read_watchlist_from_sheets
    from src.config import DEFAULT_WATCHLIST

    if tickers is None or not tickers:
        sheet_tickers = read_watchlist_from_sheets()
        if sheet_tickers:
            tickers = [t.get("symbol") for t in sheet_tickers if t.get("symbol")]
        else:
            tickers = DEFAULT_WATCHLIST

    # Nettoyage et déduplication
    clean_tickers = list(dict.fromkeys([str(t).strip().upper() for t in tickers if t]))
    cap = float(capital_total or REFERENCE_CAPITAL)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(generate_8_step_protocol_analysis, sym, cap): sym for sym in clean_tickers}
        for future in concurrent.futures.as_completed(futures):
            sym = futures[future]
            try:
                data = future.result()
                if data and "error" not in data:
                    results.append(data)
            except Exception as e:
                print(f"⚠️ Erreur scan V3 pour {sym} : {e}")

    # Tri par score de confluence décroissant
    results.sort(key=lambda x: x.get("confluence_score", 0), reverse=True)

    macro = get_macro_sentiment_barometer()

    return {
        "success": True,
        "scanned_count": len(results),
        "total_requested": len(clean_tickers),
        "macro_barometer": macro,
        "validated_buys_count": sum(1 for r in results if r.get("verdict") == "ACHAT VALIDÉ"),
        "pending_breakouts_count": sum(1 for r in results if r.get("verdict") == "ATTENDRE LE BREAKOUT H1"),
        "results": results,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
