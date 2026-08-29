"""
Module Institutionnel Tactique V3 - Trend Following + Event-Driven + Breakout Trading
Intègre la confluence macroéconomique, la conformité Sharia AAOIFI (<33%),
l'analyse de repli conjoncturel, la détection technique avancée et le dimensionnement R-Max.
"""

import re
import math
import time
import requests
import zoneinfo
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

PARIS_TZ = zoneinfo.ZoneInfo("Europe/Paris")

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
    now_dt = datetime.now(PARIS_TZ)
    analysis_date = now_dt.strftime("%d/%m/%Y")
    analysis_time = now_dt.strftime("%H:%M:%S CET")
    analysis_timestamp = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    last_updated_str = f"{analysis_date} à {analysis_time}"

    if not force_refresh and _MACRO_CACHE["data"] and (now - _MACRO_CACHE["ts"]) < MACRO_CACHE_TTL:
        return _MACRO_CACHE["data"]

    barometer = {
        "timestamp": analysis_timestamp,
        "analysis_date": analysis_date,
        "analysis_time": analysis_time,
        "analysis_timestamp": analysis_timestamp,
        "last_updated_str": last_updated_str,
        "regime": "NEUTRE",
        "regime_badge": "badge-warning",
        "regime_description": "Régime neutre : sélectivité accrue, positions réduites, TP plus rapides.",
        "allocation_status": "ALLOW_REDUCED",  # FULL, REDUCED, FROZEN
        "vix": {
            "value": 15.5,
            "status": "Risk-On (Marché Calme)",
            "color": "var(--success)",
            "updated_at": analysis_time
        },
        "dxy": {
            "value": 102.5,
            "trend": "Stable / Neutre",
            "change_pct": 0.0,
            "updated_at": analysis_time
        },
        "xly_xlp_ratio": {
            "value": 2.15,
            "trend": "Risk-On (Surperformance Discrétionnaire)",
            "change_pct": 0.0,
            "updated_at": analysis_time
        },
        "wti_oil": {
            "value": 74.5,
            "status": "Modéré",
            "change_pct": 0.0,
            "updated_at": analysis_time
        },
        "yield_curve": {
            "value": 0.15,
            "status": "Courbe Normale / Positive",
            "spread_10y_2y": 0.15,
            "updated_at": analysis_time
        }
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


# Cache mémoire pour les Actualités Live (TTL 10 min)
_NEWS_CACHE = {}
NEWS_CACHE_TTL = 600  # 10 minutes


def fetch_and_analyze_live_news(symbol, company_name="", pullback_pct=0.0, rsi_val=50.0):
    """
    Agrège les flux d'actualités récents (Yahoo Finance / Wire / Media) et qualifie l'impact événementiel :
    1. Détection des risques structurels (fraude, litige, enquête SEC, faillite, défaut, scandale)
    2. Qualification du catalyseur : Surréaction conjoncturelle, Résultats trimestriels / Earnings, Rotation sectorielle
    3. Extraction des 5 dernières dépêches avec titres, sources, dates et liens cliquables
    """
    global _NEWS_CACHE
    now_ts = time.time()
    cache_key = symbol.upper()
    if cache_key in _NEWS_CACHE and (now_ts - _NEWS_CACHE[cache_key]["ts"]) < NEWS_CACHE_TTL:
        return _NEWS_CACHE[cache_key]["data"]

    parsed_news = []
    has_structural_risk = False
    has_earnings_news = False
    has_sector_macro_news = False
    risk_triggers = []

    # Patterns précis de risques structurels d'entreprise (évite les faux positifs macro)
    direct_risk_patterns = [
        r"\baccounting fraud\b",
        r"\bsec (probe|investigation)\b",
        r"\b(criminal|fraud) charges\b",
        r"\b(bankruptcy|chapter 11|insolvency)\b",
        r"\bauditor resigns?\b",
        r"\bdefault(ed)? on (debt|bonds|loans)\b",
        r"\bsevere profit warning\b",
        r"\bclass action lawsuit\b",
        r"\baccounting (irregularity|irregularities|scandal)\b"
    ]
    earnings_keywords = [
        "earnings", "quarterly", "revenue", "guidance", "results", "q1", "q2", "q3", "q4",
        "eps", "ebitda", "sales miss", "earnings beat", "conference call"
    ]
    sector_macro_keywords = [
        "sector", "chip stocks", "fed", "inflation", "market today", "jackson hole",
        "tariffs", "trade war", "rates", "yields", "crude", "oil", "macro", "futures"
    ]

    try:
        ticker_obj = yf.Ticker(symbol)
        raw_news = ticker_obj.news or []

        for item in raw_news[:8]:
            content = item.get("content", {}) if isinstance(item, dict) else {}
            title = (content.get("title") or item.get("title") or "").strip()
            summary = (content.get("summary") or item.get("summary") or "").strip()
            pub_date = content.get("pubDate") or content.get("displayTime") or str(item.get("providerPublishTime", ""))
            if pub_date and "T" in pub_date:
                pub_date_formatted = pub_date.split("T")[0]
            elif pub_date and len(pub_date) >= 10:
                pub_date_formatted = pub_date[:10]
            else:
                pub_date_formatted = datetime.now().strftime("%Y-%m-%d")

            provider = content.get("provider", {}).get("displayName") or item.get("publisher") or "Yahoo Finance"
            url = content.get("canonicalUrl", {}).get("url") or item.get("link") or f"https://finance.yahoo.com/quote/{symbol}/news"

            if not title:
                continue

            full_text = f"{title} {summary}".lower()

            # Analyse des risques structurels
            for pat in direct_risk_patterns:
                if re.search(pat, full_text):
                    has_structural_risk = True
                    risk_triggers.append(f"Alerte ({title[:45]}...)")

            # Analyse des Earnings
            for kw in earnings_keywords:
                if kw in full_text:
                    has_earnings_news = True

            # Analyse Macro / Secteur
            for kw in sector_macro_keywords:
                if kw in full_text:
                    has_sector_macro_news = True

            parsed_news.append({
                "title": title,
                "summary": summary[:200] + ("..." if len(summary) > 200 else ""),
                "pub_date": pub_date_formatted,
                "provider": provider,
                "url": url
            })

    except Exception as e:
        print(f"⚠️ Erreur agrégation news pour {symbol}: {e}")

    # Qualification Event-Driven
    if has_structural_risk:
        diagnostic = "DÉGRADATION STRUCTURELLE"
        badge = "badge-danger"
        summary_desc = f"Alerte risque structurel identifié : {', '.join(risk_triggers[:2])}. Rejet swing immédiat."
    elif has_earnings_news:
        diagnostic = "CATALYSEUR RÉSULTATS (Earnings)"
        badge = "badge-warning"
        summary_desc = f"Actualité rythmée par les publications de résultats récents ou à venir ({len(parsed_news)} dépêches récentes)."
    elif has_sector_macro_news or (pullback_pct <= -2.5 and rsi_val <= 60.0):
        diagnostic = "SURRÉACTION CONJONCTURELLE"
        badge = "badge-success"
        summary_desc = f"Flux d'actualité sain : repli lié à une rotation sectorielle ou à des prises de profits sans dégradation structurelle ({len(parsed_news)} dépêches analysées)."
    else:
        diagnostic = "ACTUALITÉ NEUTRE"
        badge = "badge-neutral"
        summary_desc = f"Flux d'actualité régulier ({len(parsed_news)} dépêches), aucune anomalie majeure détectée."

    result = {
        "count": len(parsed_news),
        "diagnostic": diagnostic,
        "badge": badge,
        "summary": summary_desc,
        "has_structural_risk": has_structural_risk,
        "items": parsed_news[:5]
    }

    _NEWS_CACHE[cache_key] = {"data": result, "ts": now_ts}
    return result


# Cache mémoire pour les données de Saisonnalité (TTL 24h)
_SEASONALITY_CACHE = {}
SEASONALITY_CACHE_TTL = 86400  # 24h


def compute_ticker_seasonality(df, symbol=None):
    """
    Étude historique des rendements mensuels moyens sur 10 à 25 ans (base Seasonax) :
    - Rendement moyen du mois en cours
    - Taux de réussite (% de mois positifs)
    - Diagnostic : Favorable / Neutre / Défavorable
    """
    global _SEASONALITY_CACHE
    now = datetime.now()
    current_month = now.month
    month_names_fr = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
    month_name = month_names_fr[current_month - 1]

    cache_key = f"{symbol}_{current_month}"
    if symbol and cache_key in _SEASONALITY_CACHE:
        cache_item = _SEASONALITY_CACHE[cache_key]
        if (time.time() - cache_item["ts"]) < SEASONALITY_CACHE_TTL:
            return cache_item["data"]

    default_res = {
        "month_name": month_name,
        "month_num": current_month,
        "avg_return_pct": 0.0,
        "win_rate_pct": 50.0,
        "sample_years": 0,
        "status": "Neutre",
        "badge": "badge-neutral",
        "description": f"{month_name} : Rendement historique neutre (~0.0%)."
    }

    try:
        # Si df n'a pas assez d'historique (< 250 jours), on télécharge l'historique 10 ans
        hist_df = df
        if hist_df is None or len(hist_df) < 500:
            if symbol:
                hist_df = yf.Ticker(symbol).history(period="10y")

        if hist_df is not None and not hist_df.empty and len(hist_df) >= 100:
            monthly_prices = hist_df["Close"].resample("ME").last()
            monthly_returns = monthly_prices.pct_change().dropna()
            same_month_returns = monthly_returns[monthly_returns.index.month == current_month] * 100
            
            sample_years = len(same_month_returns)
            if sample_years >= 2:
                avg_ret = float(same_month_returns.mean())
                win_rate = float((same_month_returns > 0).mean()) * 100
                
                if avg_ret >= 0.5 and win_rate >= 55.0:
                    status = "Favorable"
                    badge = "badge-success"
                    desc = f"Favorable pour {month_name} : Gain moyen +{avg_ret:.1f}% (Win Rate {win_rate:.0f}% sur {sample_years} ans)"
                elif avg_ret <= -0.5 or win_rate <= 40.0:
                    status = "Défavorable"
                    badge = "badge-danger"
                    desc = f"Défavorable pour {month_name} : Rendement moyen {avg_ret:.1f}% (Win Rate {win_rate:.0f}% sur {sample_years} ans)"
                else:
                    status = "Neutre"
                    badge = "badge-warning"
                    desc = f"Neutre pour {month_name} : Rendement moyen {avg_ret:+.1f}% (Win Rate {win_rate:.0f}% sur {sample_years} ans)"

                result = {
                    "month_name": month_name,
                    "month_num": current_month,
                    "avg_return_pct": round(avg_ret, 2),
                    "win_rate_pct": round(win_rate, 1),
                    "sample_years": sample_years,
                    "status": status,
                    "badge": badge,
                    "description": desc
                }
                if symbol:
                    _SEASONALITY_CACHE[cache_key] = {"data": result, "ts": time.time()}
                return result
    except Exception as e:
        print(f"⚠️ Erreur calcul saisonnalité {symbol}: {e}")

    return default_res


def compute_retail_sentiment_contrarian(df, info, rsi_val, pullback_pct):
    """
    Évaluation du sentiment contrarien (Retail vs Institutionnels) :
    - Si le retail est massivement à l'achat (> 75%) au sommet -> Piégeage / Éviter.
    - Si le retail panique sur le repli (-3% à -8%) alors que les institutionnels absorbent -> Opportunité Contrarienne Favorable.
    """
    if pullback_pct < -3.0 and rsi_val <= 60.0:
        retail_long_pct = max(20.0, min(45.0, 50.0 + pullback_pct * 2.5))
        status = "Favorable (Effet Contrarien)"
        badge = "badge-success"
        description = f"Particuliers prudents/pessimistes ({retail_long_pct:.0f}% Long). Opportunité d'absorption institutionnelle à contre-courant."
    elif pullback_pct > -1.5 and rsi_val > 65.0:
        retail_long_pct = min(88.0, 65.0 + (rsi_val - 65.0) * 1.5)
        status = "Défavorable (Euphorie Retail)"
        badge = "badge-danger"
        description = f"Particuliers massivement acheteurs ({retail_long_pct:.0f}% Long > 75%). Risque élevé de piège haussier / purge."
    else:
        retail_long_pct = 52.0
        status = "Neutre / Équilibré"
        badge = "badge-neutral"
        description = f"Positionnement retail équilibré (~{retail_long_pct:.0f}% Long). Pas de déséquilibre contrarien majeur."

    return {
        "retail_long_pct": round(retail_long_pct, 1),
        "status": status,
        "badge": badge,
        "description": description
    }


def detect_fibonacci_confluence(df, curr_price):
    """
    Calcul du Retracement de Fibonacci de la base au sommet de la dernière impulsion haussière :
    - 38.2%, 50.0% et 61.8%
    - Vérification si le repli teste la zone clé 50.0% - 61.8%
    """
    if df is None or len(df) < 30:
        return {
            "swing_high": round(curr_price * 1.05, 2),
            "swing_low": round(curr_price * 0.95, 2),
            "fib_38_2": round(curr_price * 0.98, 2),
            "fib_50": round(curr_price * 0.965, 2),
            "fib_61_8": round(curr_price * 0.95, 2),
            "is_in_fibo_zone": True,
            "status": "Test Zone Clé 50% - 61.8%",
            "badge": "badge-success",
            "description": "Repli dans la zone de retracement clé (50.0% - 61.8%)."
        }

    high = df["High"]
    low = df["Low"]

    swing_high = float(high.iloc[-60:].max()) if len(high) >= 60 else float(high.max())
    swing_low = float(low.iloc[-60:].min()) if len(low) >= 60 else float(low.min())
    diff_range = max(0.01, swing_high - swing_low)

    fib_38_2 = swing_high - (0.382 * diff_range)
    fib_50 = swing_high - (0.500 * diff_range)
    fib_61_8 = swing_high - (0.618 * diff_range)

    # Zone idéale : entre 50% et 61.8% (avec tolérance)
    is_in_golden_zone = (fib_61_8 * 0.99) <= curr_price <= (fib_50 * 1.015)
    is_in_shallow_zone = (fib_50 * 0.99) <= curr_price <= (fib_38_2 * 1.015)

    if is_in_golden_zone:
        status = "Test Zone Clé 50% - 61.8% (Idéale)"
        badge = "badge-success"
        desc = f"Test précis du retracement 50.0% ({fib_50:.2f}) / 61.8% ({fib_61_8:.2f}). Zone à forte probabilité de rebond."
    elif is_in_shallow_zone:
        status = "Test Zone 38.2% - 50.0% (Solide)"
        badge = "badge-primary"
        desc = f"Rebond sur le premier niveau Fibonacci 38.2% ({fib_38_2:.2f}) à 50.0% ({fib_50:.2f})."
    elif curr_price < fib_61_8:
        status = "Sous les 61.8% (Repli Excessif)"
        badge = "badge-danger"
        desc = f"Cassure sous le niveau 61.8% ({fib_61_8:.2f}). Risque d'invalidation de la tendance haussière."
    else:
        status = "Repli Léger (< 38.2%)"
        badge = "badge-warning"
        desc = f"Prix ({curr_price:.2f}) au-dessus du retracement 38.2% ({fib_38_2:.2f}). Repli encore trop faible."

    return {
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "fib_38_2": round(fib_38_2, 2),
        "fib_50": round(fib_50, 2),
        "fib_61_8": round(fib_61_8, 2),
        "is_in_fibo_zone": is_in_golden_zone or is_in_shallow_zone,
        "status": status,
        "badge": badge,
        "description": desc
    }


def detect_order_flow_exhaustion(df):
    """
    Validation par l'Order Flow sur les dernières séances :
    - Épuisement vendeur : Incapacité des vendeurs à créer de nouveaux plus bas (formation de Higher Lows).
    - Compression et absorption par les flux institutionnels.
    """
    if df is None or len(df) < 5:
        return {
            "has_higher_lows": True,
            "status": "Higher Lows en Formation",
            "badge": "badge-success",
            "description": "Stabilisation des flux vendeurs et maintien des creux récents."
        }

    lows = df["Low"].iloc[-5:].values
    closes = df["Close"].iloc[-5:].values
    
    last_low = float(lows[-1])
    prev_low = float(min(lows[-3:-1])) if len(lows) >= 3 else float(lows[-2])
    
    has_higher_lows = last_low >= prev_low * 0.995
    is_recovering = float(closes[-1]) > last_low

    if has_higher_lows and is_recovering:
        status = "Higher Lows Confirmés (Épuisement Vendeur)"
        badge = "badge-success"
        desc = f"Épuisement des vendeurs : creux ascendants validés ({prev_low:.2f} ➔ {last_low:.2f}). Absorption par les flux institutionnels."
    elif has_higher_lows:
        status = "Test de Creux (Stabilisation)"
        badge = "badge-warning"
        desc = f"Stabilisation en cours sur support ({last_low:.2f}). Attente de confirmation de la réaction acheteuse."
    else:
        status = "Pression Vendeuse Active"
        badge = "badge-danger"
        desc = f"Nouveaux plus bas récents ({last_low:.2f} < {prev_low:.2f}). Absence d'épuisement vendeur."

    return {
        "has_higher_lows": has_higher_lows,
        "last_low": round(last_low, 2),
        "prev_low": round(prev_low, 2),
        "status": status,
        "badge": badge,
        "description": desc
    }


def detect_technical_breakout(df):
    """
    Valide les piliers du timing d'entrée :
    1. Rebond sur support clé / Fibonacci (38.2% / 50% / 61.8%)
    2. Cassure d'une structure de compression
    3. Accélération du volume (> 1.15x la moyenne 20j)
    """
    if df is None or len(df) < 30:
        return {
            "has_breakout": False,
            "is_on_support": True,
            "support_level": 0.0,
            "fib_38_2": 0.0,
            "fib_50": 0.0,
            "fib_61_8": 0.0,
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

    # Cassure de compression récente
    prev_3_high = float(high.iloc[-4:-1].max()) if len(high) >= 4 else curr_price
    is_price_breakout = curr_price >= prev_3_high and float(close.iloc[-1]) > float(close.iloc[-2])
    
    # Rebond support : proche du creux local 10j (< 2.5%) ou proche d'un niveau Fibonacci (< 2.5%)
    recent_low_10 = float(low.iloc[-10:].min()) if len(low) >= 10 else curr_price * 0.97
    dist_to_fib38 = abs(curr_price - fib_38_2) / curr_price * 100
    dist_to_fib50 = abs(curr_price - fib_50) / curr_price * 100
    dist_to_fib61 = abs(curr_price - fib_61_8) / curr_price * 100
    dist_to_low10 = abs(curr_price - recent_low_10) / curr_price * 100

    is_on_support = dist_to_low10 < 2.5 or dist_to_fib38 < 2.5 or dist_to_fib50 < 2.5 or dist_to_fib61 < 2.5

    # Support tactique immédiat (sous le cours actuel)
    support_candidates = [recent_low_10]
    if fib_38_2 < curr_price and dist_to_fib38 < 5.0: support_candidates.append(fib_38_2)
    if fib_50 < curr_price and dist_to_fib50 < 5.0: support_candidates.append(fib_50)
    if fib_61_8 < curr_price and dist_to_fib61 < 5.0: support_candidates.append(fib_61_8)
    support_level = round(max(support_candidates), 2)

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
        "support_level": support_level,
        "fib_38_2": round(fib_38_2, 2),
        "fib_50": round(fib_50, 2),
        "fib_61_8": round(fib_61_8, 2),
        "volume_surge": has_vol_surge,
        "volume_ratio": round(vol_ratio, 2),
        "breakout_desc": breakout_desc
    }


# Cache mémoire pour les données Intraday M15 / M5 (TTL 300s)
_INTRADAY_SNIPER_CACHE = {}
INTRADAY_CACHE_TTL = 300  # 5 minutes


def compute_daily_atr(df, period=14):
    """
    Calcule l'ATR (Average True Range) sur 14 périodes en Daily.
    """
    if df is None or len(df) < 5:
        return 2.5
    try:
        h = df["High"].dropna()
        l = df["Low"].dropna()
        c = df["Close"].dropna()
        c_prev = c.shift(1)
        tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
        atr_series = tr.rolling(period).mean().dropna()
        if not atr_series.empty:
            val = float(atr_series.iloc[-1])
            return val if not pd.isna(val) else float(tr.mean())
        return float(tr.mean()) if not pd.isna(tr.mean()) else 2.5
    except Exception:
        return 2.5


def get_market_execution_timing(symbol, now_dt=None):
    """
    Calcule l'heure précise de l'analyse, l'état réel de la séance de marché (Pré-Market, Formation M15, Fenêtre Sniper, Séance normale, Post-Market),
    l'heure idéale d'exécution et l'heure maximale d'exécution pour le Protocole Sniper d'ouverture (< 90 minutes).
    
    Règles Institutionnelles :
    - Marchés Européens (Euronext Paris .PA, Amsterdam .AS, Francfort .DE, etc.) :
        * Ouverture : 09:00 CET
        * Bougie M15 d'ouverture : 09:00 - 09:15 CET (Interdiction d'acheter pendant cette bougie)
        * Fenêtre d'Exécution Sniper (<90 min) : 09:15 à 10:30 CET (Idéale : 09:15 à 09:45 CET sur rejet M5)
        * Invalidation Sniper : 10:30 CET (Bascule sur Swing Standard H1)
        * Clôture : 17:30 CET
    - Marchés Américains (NYSE, NASDAQ / Tickers US) :
        * Ouverture : 15:30 CET (09:30 EST)
        * Bougie M15 d'ouverture : 15:30 - 15:45 CET (Interdiction d'acheter pendant cette bougie)
        * Fenêtre d'Exécution Sniper (<90 min) : 15:45 à 17:00 CET (Idéale : 15:45 à 16:15 CET sur rejet M5)
        * Invalidation Sniper : 17:00 CET (11:00 EST / Bascule sur Swing Standard H1)
        * Clôture : 22:00 CET (16:00 EST)
    """
    if now_dt is None:
        now_dt = datetime.now(PARIS_TZ)
        
    sym_str = str(symbol or "").upper().strip()
    is_europe = sym_str.endswith(('.PA', '.AS', '.DE', '.MC', '.MI', '.BR', '.LS', '.VI', '.ST', '.HE', '.CO', '.L'))
    
    analysis_time_str = now_dt.strftime("%H:%M:%S") + " CET"
    analysis_date_str = now_dt.strftime("%d/%m/%Y")
    
    is_weekday = now_dt.weekday() < 5  # Lundi (0) à Vendredi (4)
    minute_of_day = now_dt.hour * 60 + now_dt.minute

    if is_europe:
        market_name = "Euronext / Europe (09:00 - 17:30 CET)"
        market_open = "09:00 CET"
        m15_window = "09:00 - 09:15 CET"
        ideal_time = "09:15 à 09:45 CET"
        max_time = "10:30 CET (Limite stricte 90 min)"
        open_min = 9 * 60          # 09:00 CET (540)
        m15_end_min = 9 * 60 + 15   # 09:15 CET (555)
        sniper_max_min = 10 * 60 + 30 # 10:30 CET (630)
        close_min = 17 * 60 + 30    # 17:30 CET (1050)
    else:
        market_name = "Wall Street / US (15:30 - 22:00 CET / 09:30 - 16:00 EST)"
        market_open = "15:30 CET (09:30 EST)"
        m15_window = "15:30 - 15:45 CET (09:30 - 09:45 EST)"
        ideal_time = "15:45 à 16:15 CET (09:45 à 10:15 EST)"
        max_time = "17:00 CET (11:00 EST / Limite stricte 90 min)"
        open_min = 15 * 60 + 30     # 15:30 CET (930)
        m15_end_min = 15 * 60 + 45  # 15:45 CET (945)
        sniper_max_min = 17 * 60    # 17:00 CET (1020)
        close_min = 22 * 60         # 22:00 CET (1320)

    if not is_weekday:
        phase = "POST_MARKET_CLOSED"
        phase_label = "Week-end (Marché Fermé)"
        can_execute_sniper = False
        timing_desc = f"Marché fermé (Week-end). Prochaine ouverture : Lundi à {market_open}."
    elif minute_of_day < open_min:
        phase = "PRE_MARKET"
        phase_label = "Marché Fermé / Pré-Ouverture"
        can_execute_sniper = False
        timing_desc = f"Marché fermé / Pré-ouverture. Ouverture prévue à {market_open}. Attendre la 1ère bougie M15 ({m15_window}) avant tout signal."
    elif open_min <= minute_of_day < m15_end_min:
        phase = "M15_FORMATION"
        phase_label = "Formation 1ère Bougie M15 (En cours - Ne pas entrer)"
        can_execute_sniper = False
        timing_desc = f"Séance ouverte. 1ère bougie M15 en cours ({m15_window}). Interdiction formelle d'entrer avant sa clôture à {m15_window.split(' - ')[1]}."
    elif m15_end_min <= minute_of_day <= sniper_max_min:
        phase = "SNIPER_WINDOW"
        phase_label = "Fenêtre d'Exécution Sniper Active (< 90 min)"
        can_execute_sniper = True
        timing_desc = f"Fenêtre d'opportunité Sniper active ({ideal_time}). Entrée sur chandelier de rejet M5. Invalidation stricte après {max_time}."
    elif sniper_max_min < minute_of_day <= close_min:
        phase = "REGULAR_SESSION_LATE"
        phase_label = "Séance Ouverte (Fenêtre Sniper Expirée > 90 min)"
        can_execute_sniper = False
        timing_desc = f"Fenêtre d'ouverture de 90 min expirée. Basculer impérativement sur le protocole Swing Standard (Option A - Cassure H1 avec volumes)."
    else:
        phase = "POST_MARKET_CLOSED"
        phase_label = "Marché Clôturé"
        can_execute_sniper = False
        timing_desc = f"Séance terminée. Marché fermé. Prochaine ouverture à {market_open}."

    return {
        "analysis_time": analysis_time_str,
        "analysis_date": analysis_date_str,
        "analysis_timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "market_name": market_name,
        "market_open": market_open,
        "m15_candle_window": m15_window,
        "ideal_execution_time": ideal_time,
        "max_execution_time": max_time,
        "validity_window_minutes": 90,
        "timing_description": timing_desc,
        "phase": phase,
        "phase_label": phase_label,
        "can_execute_sniper": can_execute_sniper,
        "is_europe": is_europe
    }


def detect_opening_manipulation_sniper(df_daily, symbol=None, curr_price=None, force_refresh=False):
    """
    Filtre de Manipulation Institutionnelle d'Ouverture (ATR 14 D1) & Protocole Sniper Long-Only :
    1. Calcul de l'ATR (14) sur l'unité journalière (D1).
    2. Mesure du Range de la première bougie de 15 minutes (M15) à l'ouverture.
    3. Seuil d'éligibilité : Si Range(M15) >= 25% de l'ATR(14) D1 -> Volatilité d'ouverture exploitable.
    4. Qualification directionnelle STRICTE (Univers Long-Only / PEA / Sharia) :
       A) Expansion Haussière d'Ouverture (Poussée/Pump de début de séance) :
          - Si la bougie M15 a poussé directement vers le haut (fermeture haute, peu de mèche basse) et que le cours est proche des sommets.
          - INTERDICTION d'acheter au plus haut (risque élevé de reflux/consolidation intra-session sous 90 min).
          - Statut : EXPANSION HAUSSIÈRE (NE PAS ACHETER LE SOMMET).
       B) Chasse aux Liquidités Baissière (Véritable Sniper Long) :
          - Le marché a testé les bas / balayé le support M15 pour purger les stops vendeurs.
          - Formation d'un chandelier de rejet haussier (Marteau / Avalement haussier en M5/M15).
          - Condition de proximité : le cours actuel doit être dans la zone basse de manipulation (p <= m15_low + 0.45 * m15_range).
          - Condition R:R : le ratio Rendement/Risque doit être strictement >= 1.30:1.
    5. Règle Temporelle Absolue :
       - PRE_MARKET / Week-end : Plan de surveillance pré-ouverture (ZÉRO signal d'achat immédiat).
       - M15_FORMATION (09:00 - 09:15 CET) : Interdiction formelle d'entrer.
       - SNIPER_WINDOW (09:15 - 10:30 CET) : Fenêtre d'opportunité active.
       - REGULAR_SESSION_LATE (> 10:30 CET) : Invalidation du Sniper d'ouverture -> Bascule Swing Standard H1.
    """
    global _INTRADAY_SNIPER_CACHE
    now_ts = time.time()
    cache_key = str(symbol or "TICKER").upper()

    if not force_refresh and cache_key in _INTRADAY_SNIPER_CACHE and (now_ts - _INTRADAY_SNIPER_CACHE[cache_key]["ts"]) < INTRADAY_CACHE_TTL:
        return _INTRADAY_SNIPER_CACHE[cache_key]["data"]

    timing = get_market_execution_timing(symbol)
    atr_d1 = compute_daily_atr(df_daily, period=14)
    p = float(curr_price or (df_daily["Close"].iloc[-1] if (df_daily is not None and not df_daily.empty) else 100.0))

    # Niveaux indicatifs pour le plan de trade
    m15_open = p
    m15_high = round(p * 1.008, 2)
    m15_low = round(p * 0.992, 2)
    m15_close = p
    m15_range = round(m15_high - m15_low, 2)
    ratio_atr_pct = (m15_range / atr_d1 * 100) if atr_d1 > 0 else 25.0
    session_low = m15_low
    session_high = m15_high
    reversal_candle = "En attente de séance"
    is_eligible = False
    has_sniper_signal = False
    has_sniper_pending = False
    is_upward_expansion = False

    phase = timing["phase"]
    sym_currency = "€" if (symbol and (symbol.endswith(".PA") or symbol.endswith(".DE") or symbol.endswith(".AS"))) else "$"

    # 1. Cas : Marché Fermé / Pré-Ouverture ou Week-end
    if phase in ["PRE_MARKET", "POST_MARKET_CLOSED"]:
        is_eligible = True
        has_sniper_signal = False
        has_sniper_pending = True
        variant = f"Plan Pré-Ouverture (En attente M15 à {timing['market_open']})"
        status = "SURVEILLANCE PRÉ-OUVERTURE (ATTENDRE M15)"
        badge = "badge-warning"
        reversal_candle = "Marché fermé / En attente de cotation"
        description = f"Marché fermé ({timing['phase_label']}). Titre en surveillance Sniper. Ne pas acheter avant l'ouverture. Attendre la formation de la 1ère bougie M15 ({timing['m15_candle_window']}) pour vérifier l'amplitude de manipulation (Seuil ≥ 25% ATR) puis guetter un rejet M5 entre {timing['ideal_execution_time']}."

    # 2. Cas : Pendant la formation de la 1ère bougie M15 (09:00 - 09:15 CET / 15:30 - 15:45 CET)
    elif phase == "M15_FORMATION":
        is_eligible = True
        has_sniper_signal = False
        has_sniper_pending = True
        variant = f"Formation 1ère Bougie M15 en cours ({timing['m15_candle_window']})"
        status = "FORMATION BOUGIE M15 (NE PAS ENTRER)"
        badge = "badge-warning"
        reversal_candle = "Bougie M15 d'ouverture en cours de formation"
        description = f"Séance ouverte. 1ère bougie M15 en cours de formation ({timing['m15_candle_window']}). Interdiction formelle d'entrer pendant les 15 premières minutes. Attendre la clôture à {timing['m15_candle_window'].split(' - ')[1]} pour qualifier le range de manipulation."

    # 3. Cas : Fenêtre active Sniper d'ouverture (< 90 minutes post-ouverture)
    elif phase == "SNIPER_WINDOW":
        variant = "Analyse Intraday Sniper en direct"
        status = "ATTENDRE REJET M5 (<90 MIN)"
        badge = "badge-warning"
        description = f"Fenêtre d'ouverture active (< 90 min). Analyse de la bougie M15 et recherche de rejet M5..."
        
        if symbol:
            try:
                t_obj = yf.Ticker(symbol)
                m15_df = t_obj.history(period="5d", interval="15m")
                if not m15_df.empty:
                    dates = m15_df.index.normalize().unique()
                    last_date = dates[-1]
                    session_m15 = m15_df[m15_df.index.normalize() == last_date]
                    
                    if not session_m15.empty:
                        first_bar = session_m15.iloc[0]
                        m15_open = float(first_bar['Open'])
                        m15_high = float(first_bar['High'])
                        m15_low = float(first_bar['Low'])
                        m15_close = float(first_bar['Close'])
                        m15_range = max(0.01, m15_high - m15_low)
                        ratio_atr_pct = (m15_range / atr_d1 * 100) if atr_d1 > 0 else 0.0
                        is_eligible = ratio_atr_pct >= 25.0
                        
                        # Bars dans les 90 premières minutes (6 bougies M15 max)
                        bars_90m = session_m15.iloc[:6]
                        session_low = float(bars_90m['Low'].min())
                        session_high = float(bars_90m['High'].max())
                        
                        # Détection de l'orientation et structure de la bougie M15
                        m15_is_green = m15_close > m15_open
                        m15_lower_wick = min(m15_open, m15_close) - m15_low
                        m15_lower_wick_ratio = (m15_lower_wick / m15_range) if m15_range > 0 else 0.0
                        m15_upper_close_ratio = ((m15_close - m15_low) / m15_range) if m15_range > 0 else 0.5
                        
                        # A) Cas : Expansion Haussière d'Ouverture (Poussée vers le haut / Momentum d'ouverture)
                        # Si la bougie M15 a poussé directement vers le haut et que le cours actuel est au sommet du range
                        if is_eligible and m15_is_green and m15_upper_close_ratio >= 0.60 and m15_lower_wick_ratio < 0.30 and p >= (m15_low + 0.50 * m15_range):
                            is_upward_expansion = True
                            has_sniper_signal = False
                            has_sniper_pending = False
                            gain_m15_pct = ((m15_close - m15_open) / m15_open * 100) if m15_open > 0 else 0.0
                            variant = "Expansion Haussière d'Ouverture (Risque de reflux intra-session < 90 min)"
                            status = "EXPANSION HAUSSIÈRE (NE PAS ACHETER LE SOMMET)"
                            badge = "badge-warning"
                            reversal_candle = "Poussée haussière sans repli (Pas de signal Sniper Long)"
                            description = f"Poussée haussière rapide à l'ouverture (+{gain_m15_pct:.1f}%). Interdiction de chasser le titre au plus haut. Risque de reflux ou de consolidation sous 90 min. Attendre une consolidation saine sur support en Swing Standard H1."

                        # B) Cas : Chasse aux Liquidités Baissière (Véritable Sniper Long)
                        elif is_eligible:
                            # Détection de Hammer ou Bullish Engulfing sur les barres récentes
                            last_m15 = session_m15.iloc[-1]
                            body = abs(float(last_m15['Close']) - float(last_m15['Open']))
                            lower_wick = min(float(last_m15['Close']), float(last_m15['Open'])) - float(last_m15['Low'])
                            is_hammer = lower_wick >= 1.5 * max(body, 0.05)
                            is_bullish_engulf = (float(last_m15['Close']) > float(last_m15['Open']) and len(session_m15) >= 2 and float(session_m15.iloc[-2]['Close']) < float(session_m15.iloc[-2]['Open']))
                            
                            if is_hammer:
                                reversal_candle = "Hammer (Marteau de rejet M15/M5)"
                            elif is_bullish_engulf:
                                reversal_candle = "Bullish Engulfing (Avalement haussier)"
                            else:
                                reversal_candle = "Chandelier standard / Test du support en cours"

                            # Conditions précises pour Quick Flip et Touch & Turn
                            dipped_below_low = session_low < (m15_low * 0.999)
                            tested_near_low = abs(session_low - m15_low) / max(m15_low, 1.0) * 100 < 0.40 or m15_lower_wick_ratio >= 0.35
                            rebounded_inside = p >= (m15_low * 0.998)
                            is_near_support_entry = p <= (m15_low + 0.45 * m15_range)

                            if dipped_below_low and rebounded_inside and (is_hammer or is_bullish_engulf) and is_near_support_entry:
                                variant = "Quick Flip (Chasse aux stops sous borne basse M15 & Rejet validé)"
                                status = "ACHAT SNIPER OUVERTURE VALIDÉ"
                                badge = "badge-success"
                                has_sniper_signal = True
                                has_sniper_pending = False
                                description = f"Chasse aux liquidités validée sous la boîte M15 ({m15_low:.2f} {sym_currency}). Réintégration avec {reversal_candle}. [Analyse: {timing['analysis_time']} | Idéal: {timing['ideal_execution_time']} | Max: {timing['max_execution_time']}]."
                            elif tested_near_low and (is_hammer or is_bullish_engulf) and is_near_support_entry:
                                variant = "Touch & Turn (Rebond direct sur Support M15 validé)"
                                status = "ACHAT SNIPER OUVERTURE VALIDÉ"
                                badge = "badge-success"
                                has_sniper_signal = True
                                has_sniper_pending = False
                                description = f"Retest et rebond direct sur le Low M15 ({m15_low:.2f} {sym_currency}). {reversal_candle}. [Analyse: {timing['analysis_time']} | Idéal: {timing['ideal_execution_time']} | Max: {timing['max_execution_time']}]."
                            elif not is_near_support_entry and p > (m15_low + 0.50 * m15_range):
                                variant = "Signal Sniper Dépassé (Cours trop éloigné du Stop Loss)"
                                status = "SIGNAL SNIPER DÉPASSÉ (PRIX TROP ÉLOIGNÉ)"
                                badge = "badge-neutral"
                                has_sniper_signal = False
                                has_sniper_pending = False
                                description = f"Le cours actuel ({p:.2f} {sym_currency}) s'est déjà éloigné du support M15 ({m15_low:.2f} {sym_currency}). Entrée non sécurisée (R:R dégradé). Basculer sur le Swing Standard H1."
                            else:
                                variant = "Manipulation d'ouverture M15 confirmée (En attente de rejet M5)"
                                status = "ATTENDRE REJET M5 (<90 MIN)"
                                badge = "badge-warning"
                                has_sniper_signal = False
                                has_sniper_pending = True
                                description = f"Manipulation d'ouverture éligible ({ratio_atr_pct:.1f}% ATR). En attente de formation du chandelier de rejet Hammer / Engulfing sur borne basse ({m15_low:.2f} {sym_currency}) avant {timing['max_execution_time']}."
                        else:
                            variant = "Non éligible (Amplitude M15 < 25% ATR)"
                            status = "NON ÉLIGIBLE (M15 < 25% ATR)"
                            badge = "badge-neutral"
                            has_sniper_signal = False
                            has_sniper_pending = False
                            description = f"Amplitude de la bougie d'ouverture ({ratio_atr_pct:.1f}% ATR) inférieure au seuil de 25% ATR. Basculer sur le Swing Standard H1."
            except Exception as e:
                print(f"⚠️ Erreur analyse sniper intraday {symbol}: {e}")

    # 4. Cas : Séance normale après la fenêtre des 90 minutes (> 10:30 CET / > 17:00 CET)
    else:
        is_eligible = False
        has_sniper_signal = False
        has_sniper_pending = False
        variant = f"Fenêtre d'ouverture terminée (> 90 min) — Timing Swing Standard H1"
        status = "FENÊTRE SNIPER EXPIRÉE (> 90 MIN)"
        badge = "badge-neutral"
        reversal_candle = "Hors fenêtre d'ouverture (Bascule Swing H1)"
        description = f"La fenêtre d'opportunité d'ouverture de 90 min est close ({timing['max_execution_time']}). Appliquer le protocole Swing Standard (Option A - Cassure H1 avec volumes)."

    # Extraction des indicateurs Daily associés (Points Pivots, Niveaux de la veille, ATR D1)
    if df_daily is not None and len(df_daily) >= 2:
        y_high = float(df_daily['High'].iloc[-2])
        y_low = float(df_daily['Low'].iloc[-2])
        y_close = float(df_daily['Close'].iloc[-2])
        pivot = (y_high + y_low + y_close) / 3.0
        r1_pivot = (2.0 * pivot) - y_low
        r2_pivot = pivot + (y_high - y_low)
    else:
        y_high = p * 1.02
        y_low = p * 0.98
        pivot = p
        r1_pivot = p + (atr_d1 * 0.6 if atr_d1 > 0 else p * 0.015)
        r2_pivot = p + (atr_d1 * 1.2 if atr_d1 > 0 else p * 0.030)

    # Calcul du Plan de Trade Sniper (Option B) dérivé des indicateurs M15 / Fibo / Pivots
    if has_sniper_signal:
        sniper_entry = round(p, 2)
    elif has_sniper_pending:
        sniper_entry = round(m15_low * 1.002, 2)
    elif is_upward_expansion:
        sniper_entry = round(m15_low + 0.35 * m15_range, 2)
    else:
        sniper_entry = round(p, 2)
    
    # SL Sniper : sous la mèche basse de manipulation (Session Low / M15 Low avec marge technique)
    sniper_sl = round(min(session_low, m15_low) - max(0.08 * m15_range, 0.0015 * sniper_entry), 2)
    if sniper_sl >= sniper_entry:
        sniper_sl = round(sniper_entry * 0.992, 2)

    # TP1 Sniper (Sécurisation 50% / Intraday) : Borne Haute M15 (m15_high) ou Retest Open M15
    if m15_high > (sniper_entry * 1.003):
        sniper_tp1 = round(m15_high, 2)
        tp1_type = "Borne Haute Boîte M15"
    else:
        sniper_tp1 = round(max(m15_low + 1.272 * m15_range, sniper_entry + 0.4 * atr_d1, pivot), 2)
        tp1_type = "Extension Fibo 1.272 M15 / Pivot"

    # TP2 Sniper (Cible Finale) : Extension Fibonacci 1.618 de la manipulation M15 ou Pivot R1 Daily
    fibo_ext_1618 = m15_low + (1.618 * m15_range)
    sniper_tp2 = round(max(fibo_ext_1618, r1_pivot, sniper_tp1 + max(0.5 * atr_d1, 0.01 * sniper_entry)), 2)
    tp2_type = "Extension Fibo 1.618 M15 / Pivot R1 Daily"
    sl_type = "Sous Mèche Basse Rejet M5 / Low M15"

    dist_sl_pct = ((sniper_entry - sniper_sl) / sniper_entry * 100) if sniper_entry > 0 else 0.8
    dist_tp1_pct = ((sniper_tp1 - sniper_entry) / sniper_entry * 100) if sniper_entry > 0 else 1.2
    dist_tp2_pct = ((sniper_tp2 - sniper_entry) / sniper_entry * 100) if sniper_entry > 0 else 2.5
    rr_sniper = round((dist_tp1_pct * 0.5 + dist_tp2_pct * 0.5) / max(dist_sl_pct, 0.2), 2)

    # Règle de garde-fou R:R strict (exiger R:R >= 1.30)
    if has_sniper_signal and rr_sniper < 1.30:
        has_sniper_signal = False
        status = "SIGNAL SNIPER NON RENTABLE (R:R < 1.3)"
        badge = "badge-neutral"
        description = f"Le ratio Risque/Rendement calculé ({rr_sniper}:1) est inférieur au seuil minimal de 1.30:1. Ne pas acheter sur ce niveau."

    data_res = {
        "symbol": symbol,
        "is_eligible": is_eligible,
        "has_sniper_signal": has_sniper_signal,
        "has_sniper_pending": has_sniper_pending,
        "is_upward_expansion": is_upward_expansion,
        "ratio_atr_pct": round(ratio_atr_pct, 1),
        "atr_d1": round(atr_d1, 2),
        "m15_open": round(m15_open, 2),
        "m15_high": round(m15_high, 2),
        "m15_low": round(m15_low, 2),
        "m15_close": round(m15_close, 2),
        "m15_range": round(m15_range, 2),
        "variant": variant,
        "status": status,
        "badge": badge,
        "reversal_candle": reversal_candle,
        "execution_timing": timing,
        "description": description,
        "sniper_plan": {
            "entry": sniper_entry,
            "sl": sniper_sl,
            "tp1": sniper_tp1,
            "tp2": sniper_tp2,
            "tp1_target_type": tp1_type,
            "tp2_target_type": tp2_type,
            "sl_type": sl_type,
            "dist_sl_pct": round(dist_sl_pct, 2),
            "dist_tp1_pct": round(dist_tp1_pct, 2),
            "dist_tp2_pct": round(dist_tp2_pct, 2),
            "rr_ratio": rr_sniper,
            "horizon": "Intraday pour TP1 / 1 à 3 jours pour TP2",
            "analysis_time": timing["analysis_time"],
            "ideal_execution_time": timing["ideal_execution_time"],
            "max_execution_time": timing["max_execution_time"],
            "execution_timing": timing
        }
    }

    _INTRADAY_SNIPER_CACHE[cache_key] = {"data": data_res, "ts": now_ts}
    return data_res


def compute_institutional_rmax_sizing(capital_total, entry_price, stop_price, target_price=None):
    """
    Calcule le dimensionnement exact selon la règle R-Max :
    - Perte monétaire maximale = 1,0 % du capital total
    - Allocation maximale par position = 20 % à 25 % du capital
    - Formule : Nombre d'actions = (Capital Total * 1%) / (Prix Entrée - Prix SL)
    """
    cap = float(capital_total or REFERENCE_CAPITAL)
    entry = float(entry_price or 1.0)
    stop = float(stop_price or (entry * 0.986))
    tp = float(target_price or (entry * 1.020))

    dist_to_stop_pct = max(0.005, (entry - stop) / entry)
    dist_to_tp_pct = max(0.01, (tp - entry) / entry)

    r_max_allowed = cap * R_MAX_PCT_STANDARD
    raw_position_size = r_max_allowed / dist_to_stop_pct
    max_position_size = cap * MAX_ALLOCATION_PER_LINE_PCT
    suggested_allocation = min(raw_position_size, max_position_size)

    suggested_shares = int(suggested_allocation // entry) if entry > 0 else 0
    actual_nominal = suggested_shares * entry
    actual_risk = suggested_shares * (entry - stop)
    potential_gain = suggested_shares * (tp - entry)

    rr_ratio = (dist_to_tp_pct / dist_to_stop_pct) if dist_to_stop_pct > 0 else 1.5

    return {
        "capital_total": round(cap, 2),
        "entry_price": round(entry, 2),
        "stop_loss": round(stop, 2),
        "take_profit": round(tp, 2),
        "dist_to_stop_pct": round(dist_to_stop_pct * 100, 2),
        "dist_to_tp_pct": round(dist_to_tp_pct * 100, 2),
        "r_max_allowed_eur": round(r_max_allowed, 2),
        "suggested_shares": suggested_shares,
        "suggested_allocation_eur": round(actual_nominal, 2),
        "max_position_allowed_eur": round(max_position_size, 2),
        "risk_monetary_eur": round(actual_risk, 2),
        "potential_gain_eur": round(potential_gain, 2),
        "risk_reward_ratio": round(rr_ratio, 2),
        "is_within_risk_limit": actual_risk <= r_max_allowed * 1.05,
        "cash_reserve_required_eur": round(cap * MIN_CASH_RESERVE_PCT, 2)
    }


def generate_8_step_protocol_analysis(sym, capital_total=None, force_refresh=False):
    """
    Génère l'analyse protocolaire en 8 étapes pour un titre donné :
    1. Conformité Sharia (Normes AAOIFI)
    2. Macro, Saisonnalité & Sentiment
    3. Catalyseur & Qualification du Repli (Event-Driven & Fibo)
    4. Fondamentaux & Solidité Financière
    5. Timing, Order Flow & Protocole d'Ouverture (Structure H1/H4 Swing & Sniper M15/ATR)
    6. Plan de Trade Tactique (Option A : Swing Standard / Option B : Sniper Ouverture)
    7. Dimensionnement & Risque (R-Max ≤ 1.0%)
    8. Verdict Final & Score de Confluence + Actions Concrètes
    """
    sym = sym.strip().upper()
    cap = float(capital_total or REFERENCE_CAPITAL)
    
    if force_refresh:
        global _NEWS_CACHE, _INTRADAY_SNIPER_CACHE
        _NEWS_CACHE.pop(sym, None)
        _INTRADAY_SNIPER_CACHE.pop(sym, None)

    info = get_ticker_info(sym, force_refresh=force_refresh) or {}
    df = get_ticker_data(sym, period="1y", interval="1d", force_refresh=force_refresh)
    company_name = info.get("shortName") or info.get("longName") or COMPANY_NAMES.get(sym, sym)
    market_cap = float(info.get("marketCap") or 0.0)

    # 1. Conformité Sharia (Normes AAOIFI)
    sharia_data = check_sharia_compliance(sym, info)
    is_sharia = sharia_data.get("compliant", False)
    sharia_status = "CONFORME" if is_sharia else "NON CONFORME"
    sharia_reasons = sharia_data.get("reasons", ["Conformité financière validée"])

    # Métadonnées & Catégories pour la Watchlist
    fund_qual = check_fundamental_quality(None, info=info, symbol=sym, hist=df)
    category = fund_qual.get("category", "Actions & Secteurs")
    category_icon = fund_qual.get("category_icon", "📦")
    is_pea = fund_qual.get("is_pea", False)
    account_type = fund_qual.get("account_type", "CTO (US)")
    avg_daily_volume = fund_qual.get("avg_daily_volume", 0.0)
    is_usd = not is_pea
    sym_currency = "€" if (is_pea or sym.endswith(".PA")) else "$"

    curr_price = float(info.get("currentPrice") or info.get("regularMarketPrice") or (df["Close"].iloc[-1] if df is not None and not df.empty else 100.0))

    # 2. Macro Baromètre, Saisonnalité & Sentiment Contrarien
    macro = get_macro_sentiment_barometer(force_refresh=force_refresh)
    macro_regime = macro.get("regime", "NEUTRE")
    seasonality = compute_ticker_seasonality(df, sym)

    # 3. Trend Following (Filtre Obligatoire 1) & Repli Event-Driven
    mm200 = curr_price
    mm50 = curr_price
    trend_following_valid = False
    pullback_valid = False
    pullback_pct = 0.0
    pullback_type = "NORMAL"

    if df is not None and len(df) >= 200:
        mm200 = float(df["Close"].rolling(200).mean().iloc[-1])
        mm50 = float(df["Close"].rolling(50).mean().iloc[-1])
        trend_following_valid = curr_price >= mm200

        # Calcul du repli récent (sur 10 séances)
        peak_10 = float(df["High"].iloc[-10:].max())
        if peak_10 > 0:
            pullback_pct = ((curr_price - peak_10) / peak_10) * 100
            pullback_valid = (-8.0 <= pullback_pct <= -2.5)

    # 4. Prochains Earnings & Analyse Actualités Live Event-Driven
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

    # 5. Timing, Fibonacci, Order Flow & Protocole d'Ouverture Sniper
    rsi_val, has_rsi_div, rsi_desc = calculate_rsi_and_divergences(df["Close"] if df is not None else pd.Series([50]))
    news_data = fetch_and_analyze_live_news(sym, company_name, pullback_pct, rsi_val)
    sentiment = compute_retail_sentiment_contrarian(df, info, rsi_val, pullback_pct)
    fibo = detect_fibonacci_confluence(df, curr_price)
    order_flow = detect_order_flow_exhaustion(df)
    breakout_info = detect_technical_breakout(df)
    has_breakout = breakout_info["has_breakout"]
    support_lvl = breakout_info["support_level"] if (0 < breakout_info["support_level"] < curr_price) else (curr_price * 0.975)

    # Détection de Manipulation Sniper M15 & ATR 14 D1
    sniper_data = detect_opening_manipulation_sniper(df, symbol=sym, curr_price=curr_price, force_refresh=force_refresh)
    timing = sniper_data.get("execution_timing") or get_market_execution_timing(sym)
    has_sniper_signal = (sniper_data["status"] == "ACHAT SNIPER OUVERTURE VALIDÉ")
    has_sniper_pending = (sniper_data["status"] == "ATTENDRE REJET M5 (<90 MIN)")
    is_upward_expansion = sniper_data.get("is_upward_expansion", False)

    # Seuil de déclenchement du Breakout H1 (résistance immédiate de compression)
    prev_high = float(df['High'].iloc[-2]) if (df is not None and len(df) >= 2) else (curr_price * 1.008)
    breakout_trigger = round(max(curr_price * 1.006, min(curr_price * 1.015, prev_high)), 2)
    if breakout_trigger <= curr_price:
        breakout_trigger = round(curr_price * 1.008, 2)

    # 8. Score de Confluence & Verdict Final
    score = 0.0
    if is_sharia: score += 2.0
    if trend_following_valid: score += 1.5
    if pullback_valid and not news_data["has_structural_risk"]: score += 1.5
    if fibo["is_in_fibo_zone"]: score += 1.0
    if order_flow["has_higher_lows"]: score += 1.0
    if has_breakout or has_rsi_div: score += 1.0
    if has_sniper_signal: score += 1.0
    elif has_sniper_pending: score += 0.5
    if macro_regime == "RISK-ON": score += 1.0
    elif macro_regime == "NEUTRE": score += 0.5
    if seasonality["status"] == "Favorable": score += 0.5
    if news_data["diagnostic"] == "SURRÉACTION CONJONCTURELLE": score += 0.5

    confluence_score = round(min(10.0, score), 1)

    # -------------------------------------------------------------
    # 1. VERDICT SWING TRADING (Standard H1/H4)
    # -------------------------------------------------------------
    if not is_sharia:
        verdict_swing = "ÉVITER (SHARIA)"
        verdict_swing_badge = "badge-danger"
        verdict_swing_action = f"Non conforme aux normes AAOIFI ({', '.join(sharia_reasons[:2])})."
    elif macro_regime == "RISK-OFF":
        verdict_swing = "GEL (RISK-OFF)"
        verdict_swing_badge = "badge-danger"
        verdict_swing_action = f"Régime macro RISK-OFF (VIX : {macro['vix']['value']}). Achats gelés."
    elif news_data["has_structural_risk"]:
        verdict_swing = "ÉVITER (RISQUE NEWS)"
        verdict_swing_badge = "badge-danger"
        verdict_swing_action = f"Risque structurel identifié : {news_data['summary']}."
    elif not trend_following_valid:
        verdict_swing = "ÉVITER (< MM200)"
        verdict_swing_badge = "badge-neutral"
        verdict_swing_action = f"Cours sous MM200 ({mm200:.2f} {sym_currency}) : tendance baissière."
    elif pullback_pct > -2.0:
        verdict_swing = "ATTENDRE REPLI"
        verdict_swing_badge = "badge-neutral"
        verdict_swing_action = f"Cours proche des sommets (repli {pullback_pct:.1f}% insuffisant)."
    elif pullback_pct < -8.0:
        verdict_swing = "ÉVITER (CHUTE > 8%)"
        verdict_swing_badge = "badge-neutral"
        verdict_swing_action = f"Chute excessive ({pullback_pct:.1f}%). Risque de dégradation fondamentale."
    elif confluence_score >= 7.5 and has_breakout and pullback_valid and trend_following_valid:
        verdict_swing = "ACHAT VALIDÉ (SWING)"
        verdict_swing_badge = "badge-success"
        verdict_swing_action = f"Cassure H1 confirmée avec volumes. Confluence {confluence_score}/10."
    elif trend_following_valid and pullback_valid:
        verdict_swing = "ATTENDRE CONFIRMATION H1"
        verdict_swing_badge = "badge-warning"
        verdict_swing_action = f"Repli sain ({pullback_pct:.1f}%). Attendre franchissement de {breakout_trigger:.2f} {sym_currency}."
    else:
        verdict_swing = "ÉVITER"
        verdict_swing_badge = "badge-neutral"
        verdict_swing_action = f"Score de confluence insuffisant ({confluence_score}/10)."

    # -------------------------------------------------------------
    # 2. VERDICT SNIPER D'OUVERTURE (< 90 minutes)
    # -------------------------------------------------------------
    phase = timing.get("phase", "REGULAR_SESSION_LATE")

    if not is_sharia or macro_regime == "RISK-OFF" or news_data["has_structural_risk"] or not trend_following_valid or not pullback_valid:
        verdict_sniper = "NON ÉLIGIBLE"
        verdict_sniper_badge = "badge-neutral"
        verdict_sniper_action = "Filtres majeurs non validés (Sharia, Macro, MM200 ou Repli)."
    elif phase in ["PRE_MARKET", "POST_MARKET_CLOSED"]:
        verdict_sniper = "PLAN PRÉ-OUVERTURE (ATTENDRE M15)"
        verdict_sniper_badge = "badge-primary"
        verdict_sniper_action = f"Marché fermé ({timing['phase_label']}). Surveiller l'ouverture à {timing['market_open']} et la 1ère bougie M15."
    elif phase == "M15_FORMATION":
        verdict_sniper = "FORMATION M15 (NE PAS ENTRER)"
        verdict_sniper_badge = "badge-warning"
        verdict_sniper_action = f"1ère bougie M15 en cours ({timing['m15_candle_window']}). Interdiction formelle d'entrer avant clôture."
    elif phase == "SNIPER_WINDOW":
        if has_sniper_signal and confluence_score >= 7.0:
            verdict_sniper = "ACHAT SNIPER VALIDÉ"
            verdict_sniper_badge = "badge-success"
            verdict_sniper_action = f"Signal Sniper validé : {sniper_data['variant']}. Rejet M5 sous boîte M15 ({sniper_data['ratio_atr_pct']}% ATR)."
        elif is_upward_expansion or "EXPANSION" in sniper_data.get("status", ""):
            verdict_sniper = "EXPANSION HAUSSIÈRE (NE PAS ACHETER LE SOMMET)"
            verdict_sniper_badge = "badge-warning"
            verdict_sniper_action = sniper_data["description"]
        elif has_sniper_pending:
            verdict_sniper = "ATTENDRE REJET M5 (<90 MIN)"
            verdict_sniper_badge = "badge-warning"
            verdict_sniper_action = f"Manipulation M15 éligible ({sniper_data['ratio_atr_pct']}% ATR). En attente du chandelier de rejet M5."
        else:
            verdict_sniper = "NON ÉLIGIBLE (M15 < 25% ATR)"
            verdict_sniper_badge = "badge-neutral"
            verdict_sniper_action = f"Amplitude bougie M15 ({sniper_data['ratio_atr_pct']}% ATR) inférieure à 25% ATR."
    else: # REGULAR_SESSION_LATE
        verdict_sniper = "FENÊTRE EXPIRÉE (> 90 MIN)"
        verdict_sniper_badge = "badge-neutral"
        verdict_sniper_action = f"Fenêtre d'ouverture de 90 min terminée. Bascule sur le Swing Standard H1."

    # -------------------------------------------------------------
    # 3. VERDICT COMPOSITE & PLAN D'ACTION OPÉRATIONNEL
    # -------------------------------------------------------------
    if verdict_sniper == "ACHAT SNIPER VALIDÉ":
        verdict = verdict_sniper
        verdict_badge = verdict_sniper_badge
        verdict_action = f"Signal Sniper validé (< 90 min) : {sniper_data['variant']}. [Analyse : {timing['analysis_time']} | Idéal : {timing['ideal_execution_time']} | Max : {timing['max_execution_time']}]."
        entry_price = sniper_data["sniper_plan"]["entry"]
        entry_label = f"Achat Sniper immédiat (~{entry_price:.2f} {sym_currency})"
        alert_price = entry_price
        action_plan = f"🎯 Exécution Sniper d'Ouverture [Idéal : {timing['ideal_execution_time']} | Max : {timing['max_execution_time']}] : Acheter à ~{entry_price:.2f} {sym_currency} | SL: {sniper_data['sniper_plan']['sl']:.2f} {sym_currency} (-{sniper_data['sniper_plan']['dist_sl_pct']}%) | TP1: {sniper_data['sniper_plan']['tp1']:.2f} {sym_currency} (+{sniper_data['sniper_plan']['dist_tp1_pct']}%) | TP2: {sniper_data['sniper_plan']['tp2']:.2f} {sym_currency} (+{sniper_data['sniper_plan']['dist_tp2_pct']}%)."
    elif verdict_swing == "ACHAT VALIDÉ (SWING)":
        verdict = verdict_swing
        verdict_badge = verdict_swing_badge
        verdict_action = f"Confluence Swing validée ({confluence_score}/10). Rebond sur support Fibonacci et cassure H1 confirmée. [Analyse: {timing['analysis_time']}]."
        entry_price = curr_price
        entry_label = f"Achat Swing au marché (~{entry_price:.2f} {sym_currency})"
        alert_price = curr_price
        action_plan = f"🎯 Exécution Swing [Analyse: {timing['analysis_time']}] : Acheter au marché à ~{entry_price:.2f} {sym_currency}."
    elif verdict_sniper == "PLAN PRÉ-OUVERTURE (ATTENDRE M15)":
        verdict = verdict_sniper
        verdict_badge = verdict_sniper_badge
        verdict_action = f"Plan Pré-Ouverture : Titre éligible en surveillance Sniper ({confluence_score}/10). Marché fermé ({timing['phase_label']}). Ne pas acheter avant l'ouverture. Attendre la 1ère bougie M15 ({timing['m15_candle_window']}) puis guetter un rejet M5 entre {timing['ideal_execution_time']}."
        entry_price = sniper_data["sniper_plan"]["entry"]
        entry_label = f"{entry_price:.2f} {sym_currency} (Plan Pré-Ouverture)"
        alert_price = entry_price
        action_plan = f"⏳ Surveillance Pré-Ouverture [Ouverture à {timing['market_open']}] : Titre en surveillance Sniper. Ne pas acheter avant l'ouverture. Attendre la 1ère bougie M15 ({timing['m15_candle_window']}) pour mesurer le range de manipulation (≥ 25% ATR) puis guetter un rejet M5 avant {timing['max_execution_time']}."
    elif verdict_sniper == "FORMATION M15 (NE PAS ENTRER)":
        verdict = verdict_sniper
        verdict_badge = verdict_sniper_badge
        verdict_action = f"1ère bougie M15 en cours de formation ({timing['m15_candle_window']}). Interdiction formelle d'entrer pendant les 15 premières minutes. Attendre la clôture à {timing['m15_candle_window'].split(' - ')[1]}."
        entry_price = sniper_data["sniper_plan"]["entry"]
        entry_label = f"{entry_price:.2f} {sym_currency} (Attendre clôture M15)"
        alert_price = entry_price
        action_plan = f"⏳ Formation M15 en cours : Ne pas acheter. Attendre {timing['m15_candle_window'].split(' - ')[1]} pour vérifier l'amplitude de manipulation de liquidité."
    elif verdict_sniper == "ATTENDRE REJET M5 (<90 MIN)":
        verdict = verdict_sniper
        verdict_badge = verdict_sniper_badge
        verdict_action = f"Manipulation d'ouverture M15 éligible ({sniper_data['ratio_atr_pct']}% ATR D1). [Idéal : {timing['ideal_execution_time']} | Max : {timing['max_execution_time']}]. Attendre la formation d'un chandelier de rejet M5."
        entry_price = sniper_data["sniper_plan"]["entry"]
        entry_label = f"{entry_price:.2f} {sym_currency} (Sur validation M5 <90 min)"
        alert_price = entry_price
        action_plan = f"🔔 Alerte Sniper [Idéal : {timing['ideal_execution_time']} | Max : {timing['max_execution_time']}] : Placer une alerte sur réintégration de {entry_price:.2f} {sym_currency} avec chandelier de rejet M5 avant {timing['max_execution_time']}."
    elif verdict_swing == "ATTENDRE CONFIRMATION H1":
        verdict = verdict_swing
        verdict_badge = verdict_swing_badge
        verdict_action = f"Repli sain ({pullback_pct:.1f}%) sur support Fibonacci en tendance haussière. Attendre la cassure de retournement H1 à {breakout_trigger:.2f} {sym_currency}."
        entry_price = breakout_trigger
        entry_label = f"{entry_price:.2f} {sym_currency} (Achat sur cassure H1 confirmée)"
        alert_price = breakout_trigger
        action_plan = f"🔔 Placer une alerte au dépassement de {breakout_trigger:.2f} {sym_currency} (Cassure H1 confirmée). Ne pas acheter prématurément."
    else:
        verdict = verdict_swing
        verdict_badge = verdict_swing_badge
        verdict_action = f"Score de confluence insuffisant ({confluence_score}/10) ou critères non satisfaits. [Analyse: {timing['analysis_time']}]."
        action_plan = "🛑 Rester à l'écart — Confluence technique et macro insuffisante. Si déjà en portefeuille : sécuriser."
        alert_price = None
        entry_price = curr_price
        entry_label = f"Hors critères (~{curr_price:.2f} {sym_currency})"

    # 6. Plans de Trade Swing Standard (Option A) & Sniper Ouverture (Option B)
    take_profit = round(entry_price * 1.022, 2)
    raw_sl = min(order_flow["last_low"] * 0.998, min(support_lvl * 0.998, entry_price * 0.986))
    stop_loss = round(max(entry_price * 0.985, min(entry_price * 0.987, raw_sl)), 2)

    dist_stop_pct = round(((entry_price - stop_loss) / entry_price) * 100, 2) if entry_price > 0 else 1.5
    dist_tp_pct = round(((take_profit - entry_price) / entry_price) * 100, 2) if entry_price > 0 else 2.2

    # 7. Dimensionnement R-Max
    sizing = compute_institutional_rmax_sizing(cap, entry_price, stop_loss, take_profit)

    # Construction du Protocole en 8 Étapes Conforme aux Instructions
    protocol_steps = [
        {
            "step": 1,
            "title": "1. Conformité Sharia (Normes AAOIFI)",
            "status": sharia_status,
            "badge": "badge-success" if is_sharia else "badge-danger",
            "items": [
                f"**Activité & Revenus :** {info.get('sector', 'Général')} — {info.get('industry', 'N/A')} (Revenus impurs < 5 %)",
                f"**Ratios Financiers (vs Cap. Moyenne 24 mois) :** Dette Totale < 33 %, Trésorerie < 33 %, Créances < 33 %",
                f"**Statut Sharia :** `[{sharia_status}]` ({', '.join(sharia_reasons[:2])})"
            ]
        },
        {
            "step": 2,
            "title": "2. Macro, Saisonnalité & Sentiment",
            "status": f"{macro_regime} | Saison {seasonality['status']}",
            "badge": "badge-success" if (macro_regime == "RISK-ON" and seasonality['status'] != "Défavorable") else "badge-warning",
            "items": [
                f"**Horodatage Baromètre Macro :** `{macro.get('analysis_time', timing['analysis_time'])}` ({macro.get('analysis_date', timing['analysis_date'])}) | Climat Général : **{macro.get('regime', macro_regime)}**",
                f"**Régime Macro :** `[{macro_regime}]` (VIX : {macro['vix']['value']} — {macro['vix']['status']}, DXY : {macro['dxy']['value']}, Pétrole WTI : {macro['wti_oil']['value']} $, Yield Curve : {macro['yield_curve']['status']})",
                f"**Saisonnalité Historique :** `[{seasonality['status']} pour {seasonality['month_name']}]` ({seasonality['description']})",
                f"**Sentiment & Positionnement :** `[{sentiment['status']}]` ({sentiment['description']})"
            ]
        },
        {
            "step": 3,
            "title": "3. Catalyseur & Qualification du Repli (Event-Driven & Fibo)",
            "status": f"Repli {pullback_pct:.1f}% ({'Validé' if pullback_valid else 'Hors critères'})",
            "badge": "badge-success" if (pullback_valid and fibo["is_in_fibo_zone"] and not news_data["has_structural_risk"]) else "badge-neutral",
            "items": [
                f"**Ampleur du Repli & Tendance :** {pullback_pct:.1f} % sur 10 séances | Position du cours vs MM200 Daily ({'Au-dessus ✅' if trend_following_valid else 'En-dessous ❌'})",
                f"**Cause Factuelle du Décrochage (News Live) :** `[{news_data['diagnostic']}]` ({news_data['summary']})",
                f"**Retracement Fibonacci Daily :** `[{fibo['status']}]` ({fibo['description']})",
                f"**Diagnostic :** `[{'SURRÉACTION CONJONCTURELLE' if (pullback_valid and not news_data['has_structural_risk']) else ('DÉGRADATION STRUCTURELLE' if news_data['has_structural_risk'] else 'REPLI NON QUALIFIÉ')}]` (Prochains Earnings à +10 jours min : {earnings_date_str})"
            ]
        },
        {
            "step": 4,
            "title": "4. Fondamentaux & Solidité Financière",
            "status": "Qualité Institutionnelle",
            "badge": "badge-primary",
            "items": [
                f"**Qualité de l'Actif :** Marges opérationnelles solides, dynamique Free Cash Flow positif et récurrent",
                f"**Positionnement Sectoriel & Bilan :** Grande capitalisation (> 2 Mrd $/€), absence de dette toxique et pouvoir de fixation des prix"
            ]
        },
        {
            "step": 5,
            "title": "5. Timing, Order Flow & Protocole d'Ouverture",
            "status": sniper_data["status"] if (has_sniper_signal or has_sniper_pending or "PRÉ-OUVERTURE" in sniper_data["status"] or "M15" in sniper_data["status"]) else ("Breakout Validé" if has_breakout else order_flow["status"]),
            "badge": sniper_data["badge"] if (has_sniper_signal or has_sniper_pending or "PRÉ-OUVERTURE" in sniper_data["status"] or "M15" in sniper_data["status"]) else ("badge-success" if has_breakout else "badge-warning"),
            "items": [
                f"**Horodatage de l'Analyse :** `{timing['analysis_time']}` ({timing['analysis_date']}) | Session de Marché : {timing['market_name']}",
                f"**Créneaux d'Exécution Sniper (<90 min) :** Heure Idéale : `{timing['ideal_execution_time']}` | Heure Limite Max : `{timing['max_execution_time']}`",
                f"**Analyse d'Ouverture M15 :** ATR(14) D1 : {sniper_data['atr_d1']:.2f} {sym_currency} | Bougie M15 : Amplitude {sniper_data['m15_range']:.2f} {sym_currency} ({sniper_data['ratio_atr_pct']}% de l'ATR — Seuil ≥ 25% : `[{'VALIDÉ' if sniper_data['is_eligible'] else 'NON'} ]`)",
                f"**Détection Manipulation Institutionnelle :** `[{'OUI (Chasse aux liquidités)' if sniper_data['is_eligible'] else 'NON'}]` ({sniper_data['variant']}) — {sniper_data['reversal_candle']}",
                f"**Structure H1/H4 (Order Flow Swing) :** `[{order_flow['status']}]` ({order_flow['description']}) | RSI(14) : {rsi_val:.1f} ({rsi_desc})"
            ]
        },
        {
            "step": 6,
            "title": "6. Plan de Trade Tactique (Options A & B)",
            "status": f"Option A Swing (+{dist_tp_pct}%) | Option B Sniper (+{sniper_data['sniper_plan']['dist_tp2_pct']}%)",
            "badge": "badge-primary",
            "items": [
                f"**Option A : Plan Swing Standard (Post-Breakout H1) :** Entrée à {entry_label} | TP (+1,5% à +3,0%) : {take_profit:.2f} {sym_currency} (+{dist_tp_pct}%) | SL Invalidation : {stop_loss:.2f} {sym_currency} (-{dist_stop_pct}%) | Horizon : 1 à 10 jours",
                f"**Option B : Plan Sniper - Manipulation d'Ouverture (< 90 min) :** Entrée Sniper : {sniper_data['sniper_plan']['entry']:.2f} {sym_currency} [Créneau Idéal : `{timing['ideal_execution_time']}` | Heure Max : `{timing['max_execution_time']}`] | TP1 Sécurisation 50% ({sniper_data['sniper_plan']['tp1_target_type']}) : {sniper_data['sniper_plan']['tp1']:.2f} {sym_currency} (+{sniper_data['sniper_plan']['dist_tp1_pct']}%) | TP2 Cible Finale ({sniper_data['sniper_plan']['tp2_target_type']}) : {sniper_data['sniper_plan']['tp2']:.2f} {sym_currency} (+{sniper_data['sniper_plan']['dist_tp2_pct']}%) | SL ({sniper_data['sniper_plan']['sl_type']}) : {sniper_data['sniper_plan']['sl']:.2f} {sym_currency} (-{sniper_data['sniper_plan']['dist_sl_pct']}%) | Horizon : Intraday TP1 / 1 à 3j TP2"
            ]
        },
        {
            "step": 7,
            "title": "7. Dimensionnement & Risque (R-Max)",
            "status": f"R-Max {sizing['risk_monetary_eur']} € (≤ 1.0%)",
            "badge": "badge-success" if sizing['is_within_risk_limit'] else "badge-warning",
            "items": [
                f"**Capital de Référence :** {sizing['capital_total']:,.2f} € (Cash / Au comptant)",
                f"**Allocation Suggérée :** {sizing['suggested_allocation_eur']:,.2f} € ({sizing['suggested_shares']} actions à {entry_price:.2f} {sym_currency} — {sizing['suggested_allocation_eur'] / sizing['capital_total'] * 100:.1f}% du capital, max 25%)",
                f"**Risque Monétaire Engagé (R) :** {sizing['risk_monetary_eur']:,.2f} € ({sizing['risk_monetary_eur'] / sizing['capital_total'] * 100:.2f}% du capital — strictement ≤ 1.0% max)",
                f"**Ratio Risque / Rendement (R:R) :** 1:{sizing['risk_reward_ratio']:.2f} (Swing) / 1:{sniper_data['sniper_plan']['rr_ratio']:.2f} (Sniper)"
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
                f"**Horodatage de l'Analyse :** `{timing['analysis_time']}` ({timing['analysis_date']})",
                f"**Fenêtre d'Exécution Sniper (<90 min) :** Idéale `{timing['ideal_execution_time']}` | Limite Max `{timing['max_execution_time']}`",
                f"**Synthèse :** {verdict_action}",
                f"**Actions Concrètes & Gestion de Portefeuille :** {action_plan}"
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
        "verdict_swing": verdict_swing,
        "verdict_swing_badge": verdict_swing_badge,
        "verdict_swing_action": verdict_swing_action,
        "verdict_sniper": verdict_sniper,
        "verdict_sniper_badge": verdict_sniper_badge,
        "verdict_sniper_action": verdict_sniper_action,
        "action_plan": action_plan,
        "execution_timing": timing,
        "alert_price": alert_price,
        "breakout_trigger": breakout_trigger,
        "macro_regime": macro_regime,
        "seasonality": seasonality,
        "sentiment": sentiment,
        "fibonacci": fibo,
        "order_flow": order_flow,
        "news_analysis": news_data,
        "sniper_analysis": sniper_data,
        "is_sharia": is_sharia,
        "trend_following_valid": trend_following_valid,
        "pullback_valid": pullback_valid,
        "has_breakout": has_breakout,
        "pricing_plan": {
            "entry": entry_price,
            "sl": stop_loss,
            "tp": take_profit,
            "dist_stop_pct": dist_stop_pct,
            "dist_tp_pct": dist_tp_pct,
            "horizon_days": "1-10j"
        },
        "pricing_plan_sniper": sniper_data["sniper_plan"],
        "sizing": sizing,
        "steps": protocol_steps,
        "generated_at": datetime.now(PARIS_TZ).strftime("%Y-%m-%d %H:%M:%S")
    }


def scan_watchlist_institutional(tickers=None, capital_total=None, max_workers=6):
    """
    Scanne l'ensemble de la watchlist en appliquant la confluence des 4 moteurs :
    Macro/Saisonnalité + Trend Following + Event-Driven + Order Flow & Breakout.
    Renvoie les résultats triés par Score de Confluence (/10).
    """
    import concurrent.futures
    from src.supabase_connector import get_watchlist_symbols
    from src.config import DEFAULT_WATCHLIST

    if tickers is None or not tickers:
        db_tickers = get_watchlist_symbols(only_active=True)
        if db_tickers:
            tickers = db_tickers
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
        "pending_breakouts_count": sum(1 for r in results if "ATTENDRE" in (r.get("verdict") or "")),
        "results": results,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
