import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from src.config import (
    MIN_DROP_PCT,
    MAX_DROP_PCT,
    LOOKBACK_DAYS,
    HOLDING_PERIOD_DAYS,
    MIN_MARKET_CAP_USD
)

def categorize_ticker(symbol, info=None):
    """
    Détermine l'éligibilité au PEA (Euronext / Europe) et la catégorie sectorielle en français.
    """
    symbol = str(symbol or "").upper().strip()
    if not isinstance(info, dict):
        info = {}
    
    # 1. Éligibilité PEA (Titres européens / français Euronext)
    eu_suffixes = ['.PA', '.AS', '.BR', '.DE', '.MC', '.MI', '.LS', '.VI', '.IR', '.HE']
    is_pea = any(symbol.endswith(sfx) for sfx in eu_suffixes) or symbol in [
        'MC.PA', 'OR.PA', 'AIR.PA', 'RMS.PA', 'KER.PA', 'SAN.PA', 'TTE.PA', 'EL.PA', 'ASML.AS', 'SAP.DE', 'LR.PA', 'GTT.PA', 'STMPA.PA', 'AI.PA', 'ENGI.PA', 'SU.PA'
    ]
    account_type = "PEA (Europe)" if is_pea else "CTO (US)"
    
    sector = str(info.get('sector', '') or '')
    industry = str(info.get('industry', '') or '')
    sec_lower = (sector + ' ' + industry).lower()
    
    # 2. Catégories Thématiques & Sectorielles
    if any(k in sec_lower for k in ['technology', 'software', 'semiconductor', 'hardware', 'electronic', 'it ']):
        category = "Tech & IA"
        category_icon = "💻"
    elif any(k in sec_lower for k in ['health', 'pharma', 'biotech', 'drug', 'medical', 'care']):
        category = "Santé & Pharma"
        category_icon = "💊"
    elif any(k in sec_lower for k in ['luxury', 'consumer cyclical', 'apparel', 'retail', 'beverage', 'cosmetic', 'luxe']):
        category = "Luxe & Consommation"
        category_icon = "💎"
    elif any(k in sec_lower for k in ['industrial', 'aerospace', 'defense', 'machinery', 'transport', 'airline']):
        category = "Industrie & Aéro"
        category_icon = "🏭"
    elif any(k in sec_lower for k in ['energy', 'oil', 'gas', 'petroleum', 'solar', 'clean energy']):
        category = "Énergie & Pétrole"
        category_icon = "⚡"
    elif any(k in sec_lower for k in ['materials', 'chemical', 'mining', 'steel']):
        category = "Matériaux & Chimie"
        category_icon = "🧪"
    elif any(k in sec_lower for k in ['defensive', 'food', 'grocery', 'household', 'consumer defensive']):
        category = "Agro & Défensif"
        category_icon = "🛒"
    elif any(k in sec_lower for k in ['financial', 'bank', 'real estate', 'reit', 'insurance']):
        category = "Finance & Immo"
        category_icon = "🏦"
    else:
        category = sector if sector else "Autres"
        category_icon = "📦"
        
    return {
        "is_pea": is_pea,
        "account_type": account_type,
        "category": category,
        "category_icon": category_icon,
        "sector_raw": sector,
        "industry_raw": industry
    }

def calculate_rsi(prices, period=14):
    """
    Calcule le Relative Strength Index (RSI) classique sur N périodes.
    """
    deltas = np.diff(prices)
    if len(deltas) < period:
        return np.full_like(prices, 50.0)
        
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100. / (1. + rs)

    for i in range(period, len(prices)):
        delta = deltas[i-1]
        if delta > 0:
            upval = delta
            downval = 0.
        else:
            upval = 0.
            downval = -delta

        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100. - 100. / (1. + rs)
        
    return rsi

def detect_rsi_divergence(close_prices, rsi_values, window=25):
    """
    Détecte les divergences haussières sur le RSI (14) selon la Section 5.
    Divergence Haussière : Le cours teste un plus bas (ou égal) alors que le RSI
    marque un point bas plus élevé -> Signe d'épuisement de la dynamique vendeuse.
    """
    if len(close_prices) < window:
        window = len(close_prices)
        
    if window < 10:
        return {
            "has_divergence": False,
            "type": "AUCUNE",
            "description": "Historique insuffisant pour l'analyse de divergence."
        }
        
    closes = close_prices[-window:]
    rsis = rsi_values[-window:]
    
    # 1. Identifier les creux locaux (Swing Lows)
    low_indices = []
    for i in range(1, len(closes) - 1):
        if closes[i] <= closes[i-1] and closes[i] <= closes[i+1]:
            low_indices.append(i)
            
    # Ajouter le dernier point si c'est un creux récent (5 derniers jours)
    if closes[-1] <= np.min(closes[-5:]):
        if not low_indices or low_indices[-1] != len(closes) - 1:
            low_indices.append(len(closes) - 1)
            
    if len(low_indices) >= 2:
        idx2 = low_indices[-1]
        idx1 = low_indices[-2]
        
        price1, price2 = closes[idx1], closes[idx2]
        rsi1, rsi2 = rsis[idx1], rsis[idx2]
        
        if price2 <= price1 * 1.005 and rsi2 >= (rsi1 + 1.5):
            return {
                "has_divergence": True,
                "type": "DIVERGENCE HAUSSIÈRE",
                "description": f"Divergence Haussière confirmée (Creux prix : {price1:.2f} ➔ {price2:.2f} vs Creux RSI : {rsi1:.1f} ➔ {rsi2:.1f}). Épuisement vendeur.",
                "details": {
                    "price_low_prev": float(price1),
                    "price_low_curr": float(price2),
                    "rsi_low_prev": float(rsi1),
                    "rsi_low_curr": float(rsi2)
                }
            }

    min_p_idx = int(np.argmin(closes))
    min_r_idx = int(np.argmin(rsis))
    
    if closes[-1] <= closes[min_p_idx] * 1.015 and rsis[-1] >= rsis[min_r_idx] + 3.0:
        return {
            "has_divergence": True,
            "type": "DIVERGENCE HAUSSIÈRE DE REPRISE",
            "description": f"Divergence haussière de reprise (RSI {rsis[-1]:.1f} en net rebond vs creux {rsis[min_r_idx]:.1f} sur prix bas).",
            "details": {
                "price_low_prev": float(closes[min_p_idx]),
                "price_low_curr": float(closes[-1]),
                "rsi_low_prev": float(rsis[min_r_idx]),
                "rsi_low_curr": float(rsis[-1])
            }
        }

    return {
        "has_divergence": False,
        "type": "AUCUNE",
        "description": "Pas de divergence haussière significative détectée sur le RSI.",
        "details": {}
    }

# ---------------------------------------------------------------------
# IN-MEMORY TTL CACHES & RATE LIMIT RESILIENCE
# ---------------------------------------------------------------------
import time

_HIST_CACHE = {}      # symbol -> {"hist": df, "ticker": obj, "ts": timestamp}
_INFO_CACHE = {}      # symbol -> {"info": dict, "ts": timestamp}
_FX_CACHE = {}        # currency -> {"rate": float, "ts": timestamp}

HIST_CACHE_TTL = 300  # 5 minutes
INFO_CACHE_TTL = 900  # 15 minutes
FX_CACHE_TTL = 1800   # 30 minutes

def get_ticker_info(ticker_symbol):
    """
    Récupère les informations fondamentales et sectorielles avec cache TTL de 15 minutes.
    """
    symbol = str(ticker_symbol or "").upper().strip()
    if not symbol:
        return {}
        
    now = time.time()
    if symbol in _INFO_CACHE and (now - _INFO_CACHE[symbol]["ts"]) < INFO_CACHE_TTL:
        return _INFO_CACHE[symbol]["info"]
        
    info = {}
    try:
        t = yf.Ticker(symbol)
        raw_info = getattr(t, 'info', None)
        if isinstance(raw_info, dict) and raw_info:
            info = raw_info
    except Exception as e:
        # En cas d'erreur de rate-limit, retourner le cache périmé s'il existe
        if symbol in _INFO_CACHE:
            return _INFO_CACHE[symbol]["info"]
        print(f"Warning: get_ticker_info({symbol}) failed: {e}")
        
    if info:
        _INFO_CACHE[symbol] = {"info": info, "ts": now}
    return info

def get_usd_conversion_rate(currency_code):
    """
    Récupère le taux de conversion vers le USD avec cache TTL de 30 minutes.
    """
    if not currency_code:
        return 1.0
    currency_code = currency_code.upper().strip()
    if currency_code == "USD":
        return 1.0
        
    now = time.time()
    if currency_code in _FX_CACHE and (now - _FX_CACHE[currency_code]["ts"]) < FX_CACHE_TTL:
        return _FX_CACHE[currency_code]["rate"]
        
    factor = 1.0
    lookup_code = currency_code
    if lookup_code in ["GBX", "GBP"]:
        lookup_code = "GBP"
        if currency_code == "GBX":
            factor = 0.01

    rate = 1.0
    try:
        t = yf.Ticker(f"{lookup_code}USD=X")
        hist = t.history(period="1d")
        if not hist.empty:
            rate = float(hist['Close'].values[-1]) * factor
        else:
            t_inv = yf.Ticker(f"USD{lookup_code}=X")
            hist_inv = t_inv.history(period="1d")
            if not hist_inv.empty:
                rate = (1.0 / float(hist_inv['Close'].values[-1])) * factor
    except Exception as e:
        if currency_code in _FX_CACHE:
            return _FX_CACHE[currency_code]["rate"]
        print(f"Error fetching exchange rate for {currency_code}: {e}")
        
    _FX_CACHE[currency_code] = {"rate": rate, "ts": now}
    return rate

def get_usd_to_eur_rate():
    """
    Retourne le taux de conversion 1 USD vers EUR (ex: 0.92 EUR pour 1 USD).
    """
    eur_usd = get_usd_conversion_rate("EUR")
    return (1.0 / eur_usd) if eur_usd > 0 else 0.92

def fetch_market_data(ticker_symbol):
    """
    Récupère les données historiques de cours avec cache TTL de 5 minutes et gestion de reprise en cas de rate-limit.
    """
    symbol = str(ticker_symbol or "").upper().strip()
    if not symbol:
        return None, "Symbole invalide."
        
    now = time.time()
    if symbol in _HIST_CACHE and (now - _HIST_CACHE[symbol]["ts"]) < HIST_CACHE_TTL:
        cached = _HIST_CACHE[symbol]
        return cached["ticker"], cached["hist"].copy()

    ticker_obj = yf.Ticker(symbol)
    hist = None
    
    # 2 essais avec backoff
    for attempt in range(2):
        try:
            hist = ticker_obj.history(period="300d")
            if hist is not None and not hist.empty:
                break
        except Exception as e:
            if attempt == 0:
                time.sleep(0.4)
            else:
                print(f"Warning: fetch_market_data({symbol}) attempt {attempt+1} failed: {e}")
                
    if hist is None or hist.empty:
        # Si échec mais cache disponible (même périmé), l'utiliser en secours
        if symbol in _HIST_CACHE:
            cached = _HIST_CACHE[symbol]
            return cached["ticker"], cached["hist"].copy()
        return None, "Aucun historique de cours disponible."
        
    hist = hist.dropna(subset=['Close'])
    if hist.empty:
        return None, "Données de cours incomplètes."

    currency = "USD"
    try:
        raw_info = getattr(ticker_obj, 'info', None)
        if isinstance(raw_info, dict):
            currency = raw_info.get("currency", "USD").upper()
    except:
        currency = "USD"
        
    if currency == "EUR":
        target_currency = "EUR"
    else:
        target_currency = "USD"
        
    if currency != target_currency:
        rate = get_usd_conversion_rate(currency)
        if rate != 1.0:
            for col in ['Open', 'High', 'Low', 'Close']:
                if col in hist.columns:
                    hist[col] = hist[col] * rate
        currency = "USD"
    else:
        currency = target_currency
        
    hist.attrs['currency'] = currency
    _HIST_CACHE[symbol] = {"ticker": ticker_obj, "hist": hist, "ts": now}
    return ticker_obj, hist

def calculate_qqe(close_prices, rsi_period=14, sf=5, wilder_period=27):
    """
    Calcule le QQE (Quantitative Qualitative Estimation) pour les signaux de momentum.
    """
    rsi = calculate_rsi(close_prices, period=rsi_period)
    rsi_df = pd.Series(rsi)
    smoothed_rsi = rsi_df.ewm(span=sf, adjust=False).mean().values
    
    rsi_change = np.abs(np.diff(smoothed_rsi))
    rsi_change = np.insert(rsi_change, 0, 0)
    rsi_change_df = pd.Series(rsi_change)
    
    wilder_ma = rsi_change_df.ewm(alpha=1/wilder_period, adjust=False).mean().values
    atr_rsi = wilder_ma * 4.236
    
    tr = np.zeros_like(smoothed_rsi)
    for i in range(1, len(smoothed_rsi)):
        prev_tr = tr[i-1]
        curr_rsi = smoothed_rsi[i]
        curr_atr = atr_rsi[i]
        
        if curr_rsi > prev_tr:
            new_tr = curr_rsi - curr_atr
            tr[i] = max(new_tr, prev_tr) if (prev_tr != 0 and smoothed_rsi[i-1] > prev_tr) else new_tr
        else:
            new_tr = curr_rsi + curr_atr
            tr[i] = min(new_tr, prev_tr) if (prev_tr != 0 and smoothed_rsi[i-1] < prev_tr) else new_tr
            
    buy_signal = (smoothed_rsi[-1] > tr[-1]) and (smoothed_rsi[-2] <= tr[-2])
    return {
        "smoothed_rsi": float(smoothed_rsi[-1]),
        "qqe_line": float(tr[-1]),
        "buy_signal": bool(buy_signal)
    }

def calculate_sector_relative_strength(ticker_symbol, category, hist):
    """
    Calcule la tendance et la force relative du titre par rapport à son ETF sectoriel de référence (Étape 2).
    """
    from src.config import SECTOR_ETFS
    sector_etf = SECTOR_ETFS.get(category, "SPY")
    
    ticker_5d_perf = 0.0
    if hist is not None and not hist.empty and len(hist) >= 6:
        close_p = hist['Close'].values
        ticker_5d_perf = ((close_p[-1] - close_p[-6]) / close_p[-6]) * 100
        
    # Récupérer l'historique de l'ETF sectoriel
    etf_5d_perf = 0.0
    etf_trend = "NEUTRE"
    try:
        _, etf_hist = fetch_market_data(sector_etf)
        if etf_hist is not None and not etf_hist.empty and len(etf_hist) >= 6:
            etf_closes = etf_hist['Close'].values
            etf_5d_perf = ((etf_closes[-1] - etf_closes[-6]) / etf_closes[-6]) * 100
            etf_sma50 = float(etf_hist['Close'].rolling(window=min(50, len(etf_hist))).mean().values[-1])
            etf_trend = "HAUSSIÈRE" if etf_closes[-1] >= etf_sma50 else "BAISSIÈRE"
    except Exception as e:
        print(f"Notice: calculate_sector_relative_strength ({sector_etf}): {e}")
        
    diff_perf = ticker_5d_perf - etf_5d_perf
    if diff_perf >= 1.0:
        rel_status = "SURPERFORMANCE"
        badge = "success"
    elif diff_perf <= -1.5:
        rel_status = "SOUS-PERFORMANCE"
        badge = "danger"
    else:
        rel_status = "EN LIGNE"
        badge = "neutral"
        
    return {
        "sector_etf": sector_etf,
        "etf_trend": etf_trend,
        "etf_5d_perf": float(etf_5d_perf),
        "ticker_5d_perf": float(ticker_5d_perf),
        "diff_perf": float(diff_perf),
        "relative_strength": rel_status,
        "badge": badge,
        "summary": f"{category} ({sector_etf} {etf_trend}) : Titre {ticker_5d_perf:+.1f}% vs ETF {etf_5d_perf:+.1f}% ({rel_status})"
    }

def check_fundamental_quality(ticker_obj, info=None, symbol=None, hist=None):
    """
    Évalue les critères d'excellence fondamentale et de liquidité (Section 2 & 4) :
    - Capitalisation boursière Large/Mid Cap (> 2 Mrd $/€)
    - Volume moyen quotidien négocié > 1 M€/$ (élimination du slippage)
    - Free Cash Flow récurrent et marges opérationnelles solides
    """
    from src.config import MIN_AVG_DAILY_VOLUME_USD, MIN_MARKET_CAP_USD
    
    sym = symbol or (info.get("symbol", "") if isinstance(info, dict) else "")
    if info is None or not isinstance(info, dict) or not info:
        info = get_ticker_info(sym) if sym else {}
            
    cat_meta = categorize_ticker(sym, info)
    
    # 1. Extraction Multi-Sources de la Capitalisation Boursière
    market_cap = 0.0
    if isinstance(info, dict):
        market_cap = float(info.get("marketCap") or 0.0)
        if market_cap == 0.0:
            shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
            price = info.get("currentPrice") or info.get("previousClose") or info.get("regularMarketPrice")
            if shares and price and float(shares) > 0 and float(price) > 0:
                market_cap = float(shares) * float(price)
            elif info.get("enterpriseValue"):
                market_cap = float(info.get("enterpriseValue"))
            elif info.get("totalAssets"):
                market_cap = float(info.get("totalAssets"))

    # Fallback via ticker_obj ou fast_info de yfinance
    if market_cap == 0.0:
        try:
            if ticker_obj is not None:
                fast_cap = getattr(getattr(ticker_obj, 'fast_info', None), 'market_cap', None)
                if fast_cap and float(fast_cap) > 0:
                    market_cap = float(fast_cap)
            if market_cap == 0.0 and sym:
                t_inst = yf.Ticker(sym)
                fast_cap = getattr(getattr(t_inst, 'fast_info', None), 'market_cap', None)
                if fast_cap and float(fast_cap) > 0:
                    market_cap = float(fast_cap)
        except Exception:
            pass

    fcf = info.get("freeCashflow", 0) if isinstance(info, dict) else 0
    op_margin = info.get("operatingMargins", 0) if isinstance(info, dict) else 0
    revenue_growth = info.get("revenueGrowth", 0) if isinstance(info, dict) else 0
    profit_margin = info.get("profitMargins", 0) if isinstance(info, dict) else 0
    
    # 2. Calcul du volume quotidien moyen négocié en monnaie (turnover journalier)
    avg_daily_volume = 0.0
    if hist is not None and not hist.empty and len(hist) >= 5:
        try:
            turnover_series = hist['Volume'] * hist['Close']
            avg_daily_volume = float(turnover_series.tail(20).mean())
        except Exception:
            pass
            
    if avg_daily_volume == 0.0 and isinstance(info, dict):
        vol = info.get("averageVolume", 0) or info.get("volume24Hr", 0) or info.get("regularMarketVolume", 0) or 0
        price = info.get("currentPrice") or info.get("previousClose") or 100.0
        avg_daily_volume = float(vol * price)
        
    # 3. Validation de la Capitalisation Boursière avec conversion de devise
    currency = (info.get("currency") if isinstance(info, dict) else None) or "USD"
    fx_to_usd = get_usd_conversion_rate(currency)
    market_cap_usd = float(market_cap * fx_to_usd) if market_cap > 0 else 0.0

    if market_cap > 0:
        # Seuil 2 Mrd USD (ou 2 Mrd dans la devise locale)
        is_large_cap = bool(market_cap_usd >= MIN_MARKET_CAP_USD or market_cap >= MIN_MARKET_CAP_USD)
    else:
        # Si Yahoo n'a pas renseigné la capitalisation (ex: ETF, certains ADRs),
        # mais que la liquidité quotidienne est confirmée (> 1 M€/$), on ne bloque pas le titre
        is_large_cap = bool(avg_daily_volume >= MIN_AVG_DAILY_VOLUME_USD)

    has_min_liquidity = bool(avg_daily_volume >= MIN_AVG_DAILY_VOLUME_USD)
    is_fcf_positive = bool(fcf > 0 or fcf is None or fcf == 0)
    is_profitable = bool(op_margin > 0 or profit_margin > 0)
    
    if is_large_cap and has_min_liquidity and is_profitable:
        health_status = "SOLIDE"
    elif is_large_cap and has_min_liquidity:
        health_status = "MOYENNE"
    elif not has_min_liquidity:
        health_status = "ILLIQUIDE (< 1 M€/$)"
    else:
        health_status = "SPÉCULATIVE (< 2 Mrd)"
        
    cap_display = f"{market_cap/1e9:.1f}B" if market_cap > 0 else ("> 2B (Validé liquidité)" if is_large_cap else "< 2B")
    
    return {
        "market_cap": market_cap,
        "market_cap_usd": market_cap_usd,
        "is_large_cap": is_large_cap,
        "avg_daily_volume": avg_daily_volume,
        "has_min_liquidity": has_min_liquidity,
        "free_cash_flow": fcf,
        "is_fcf_positive": is_fcf_positive,
        "operating_margin": op_margin,
        "revenue_growth": revenue_growth,
        "profit_margin": profit_margin,
        "sector": cat_meta["sector_raw"],
        "industry": cat_meta["industry_raw"],
        "category": cat_meta["category"],
        "category_icon": cat_meta["category_icon"],
        "is_pea": cat_meta["is_pea"],
        "account_type": cat_meta["account_type"],
        "health_status": health_status,
        "summary": f"Cap: {cap_display} | Vol/j: {avg_daily_volume/1e6:.1f}M | Marge Op: {op_margin*100:.1f}%"
    }

def analyze_technical_setup(hist):
    """
    Analyse technique complète : SMA 20/50/200, MRC Channel, Divergence RSI, QQE, Volume et Mèches de rejet.
    """
    close_prices = hist['Close'].values
    high_prices = hist['High'].values
    low_prices = hist['Low'].values
    volume_values = hist['Volume'].values
    
    current_price = float(close_prices[-1])
    
    # 1. Calcul du RSI & Divergences
    rsi_values = calculate_rsi(close_prices)
    current_rsi = float(rsi_values[-1])
    rsi_divergence = detect_rsi_divergence(close_prices, rsi_values)
    
    # 2. Moyennes mobiles & Tendances Daily / Hebdo
    sma_20 = float(hist['Close'].rolling(window=20).mean().values[-1])
    sma_50 = float(hist['Close'].rolling(window=50).mean().values[-1])
    
    if len(close_prices) >= 200:
        sma_200 = float(hist['Close'].rolling(window=200).mean().values[-1])
    else:
        sma_200 = float(hist['Close'].mean())
        
    trend_daily = "HAUSSIÈRE" if current_price >= sma_200 else "BAISSIÈRE"
    trend_weekly = "HAUSSIÈRE" if current_price >= sma_50 else "CONSOLIDATION"
    
    # 3. Canal de Retour à la Moyenne (MRC)
    mrc_mean = sma_20
    mrc_std = float(hist['Close'].rolling(window=20).std().values[-1])
    mrc_lower = mrc_mean - 2 * mrc_std
    mrc_upper = mrc_mean + 2 * mrc_std
    mrc_oversold = bool(current_price <= mrc_lower)
    
    # 4. Signaux QQE
    qqe = calculate_qqe(close_prices)
    
    # 5. Volume & Moyenne Mobile 20
    current_volume = float(volume_values[-1])
    volume_sma_20 = float(hist['Volume'].rolling(window=20).mean().values[-1])
    volume_confirmed = bool(current_volume > volume_sma_20)
    
    # 6. Niveaux de support & résistance
    support_10d = float(np.min(low_prices[-11:-1])) if len(low_prices) > 11 else float(np.min(low_prices[:-1]))
    support_30d = float(np.min(low_prices[-31:-1])) if len(low_prices) > 31 else float(np.min(low_prices[:-1]))
    
    support = support_10d if support_10d < current_price else support_30d
    if support >= current_price:
        support = current_price * 0.97
        
    resistance_10d = float(np.max(high_prices[-11:-1])) if len(high_prices) > 11 else float(np.max(high_prices[:-1]))
    resistance_30d = float(np.max(high_prices[-31:-1])) if len(high_prices) > 31 else float(np.max(high_prices[:-1]))
    
    resistance = resistance_10d if resistance_10d > current_price else resistance_30d
    if resistance <= current_price:
        resistance = current_price * 1.03
        
    latest_open = float(hist['Open'].values[-1])
    latest_low = float(low_prices[-1])
    lower_wick_pct = ((min(current_price, latest_open) - latest_low) / current_price) * 100 if current_price else 0.0
    support_rejection = bool(lower_wick_pct >= 0.8 or current_price > support * 1.005)

    close_30d = close_prices[-30:] if len(close_prices) >= 30 else close_prices
    if len(close_30d) > 1:
        daily_returns = np.diff(close_30d) / close_30d[:-1]
        hist_vol = float(np.std(daily_returns) * np.sqrt(252) * 100)
    else:
        hist_vol = 0.0
        
    currency = hist.attrs.get('currency', 'USD')
    
    return {
        "current_price": current_price,
        "rsi": current_rsi,
        "rsi_divergence": rsi_divergence,
        "trend_daily": trend_daily,
        "trend_weekly": trend_weekly,
        "is_above_sma200": bool(current_price >= sma_200),
        "sma_20": sma_20,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "mrc_mean": mrc_mean,
        "mrc_lower": mrc_lower,
        "mrc_upper": mrc_upper,
        "mrc_oversold": mrc_oversold,
        "qqe_smoothed_rsi": qqe["smoothed_rsi"],
        "qqe_line": qqe["qqe_line"],
        "qqe_buy_signal": qqe["buy_signal"],
        "current_volume": current_volume,
        "volume_sma_20": volume_sma_20,
        "volume_confirmed": volume_confirmed,
        "support": support,
        "lower_wick_pct": float(lower_wick_pct),
        "support_rejection": support_rejection,
        "resistance": resistance,
        "recent_high": float(np.max(high_prices[-10:])),
        "historical_volatility": hist_vol,
        "currency": currency
    }

def qualify_price_drop(hist):
    """
    Détermine si le titre a subi une baisse récente de -3% à -8% sur 1 à 3 sessions (Section 2 & 4).
    Qualifie la baisse comme :
      - [CONJONCTURELLE (Mispricing)] : Baisse de -3% à -8% s'approchant d'un support
      - [STRUCTURELLE (Rejet)] : Baisse excessive > -8% ou effondrement continu
      - [HORS CRITÈRES] : Variation hors de la fenêtre optimale
    """
    if hist is None or hist.empty:
        return False, {
            "drop_pct": 0.0,
            "lookback_days": 1,
            "reference_price": 0.0,
            "nature": "HISTORIQUE INDISPONIBLE",
            "cause_summary": "Pas de données de cours."
        }
        
    close_prices = hist['Close'].values
    current_price = close_prices[-1]
    
    detected_drop = 0.0
    detected_lookback = 0
    reference_price = 0.0
    
    for days in range(1, LOOKBACK_DAYS + 1):
        if len(close_prices) > (days + 1):
            prev_price = close_prices[-(days + 1)]
            drop = ((current_price - prev_price) / prev_price) * 100
            
            if -MAX_DROP_PCT <= drop <= -MIN_DROP_PCT:
                if abs(drop) > abs(detected_drop):
                    detected_drop = drop
                    detected_lookback = days
                    reference_price = prev_price
                    
    if detected_drop != 0.0:
        return True, {
            "drop_pct": float(detected_drop),
            "lookback_days": int(detected_lookback),
            "reference_price": float(reference_price),
            "nature": "CONJONCTURELLE (Mispricing)",
            "cause_summary": f"Surréaction vendeuse court terme ({detected_drop:.2f}% sur {detected_lookback}j) vers support technique."
        }
        
    actual_1d_drop = ((current_price - close_prices[-2]) / close_prices[-2]) * 100 if len(close_prices) > 1 else 0.0
    
    if actual_1d_drop < -MAX_DROP_PCT:
        return False, {
            "drop_pct": float(actual_1d_drop),
            "lookback_days": 1,
            "reference_price": float(close_prices[-2]) if len(close_prices) > 1 else current_price,
            "nature": "STRUCTURELLE (Rejet)",
            "cause_summary": f"Baisse excessive ({actual_1d_drop:.2f}% > -8%). Risque de chute libre structurelle sans stabilisation."
        }

    return False, {
        "drop_pct": float(actual_1d_drop),
        "lookback_days": 1,
        "reference_price": float(close_prices[-2]) if len(close_prices) > 1 else current_price,
        "nature": "HORS CRITÈRES (-3% à -8%)",
        "cause_summary": f"Variation actuelle ({actual_1d_drop:+.2f}%) en dehors de la fenêtre optimale de Mean Reversion."
    }

def check_earnings_blackout(ticker_obj):
    """
    Vérifie si une publication de résultats (Earnings) est prévue dans les 7 à 10 jours ouvrés (Section 4.A).
    """
    try:
        calendar = getattr(ticker_obj, 'calendar', None)
        if calendar is None or (isinstance(calendar, dict) and not calendar):
            return False, "Aucun événement corporatif immédiat trouvé."
            
        earnings_date = None
        if isinstance(calendar, dict) and "Earnings Date" in calendar:
            earnings_date = calendar["Earnings Date"]
        elif hasattr(calendar, 'get') and calendar.get("Earnings Date"):
            earnings_date = calendar.get("Earnings Date")
            
        if not earnings_date and isinstance(calendar, pd.DataFrame) and not calendar.empty:
            if "Value" in calendar.columns and "Earnings Date" in calendar.index:
                earnings_date = calendar.loc["Earnings Date"].values[0]
            else:
                for col in calendar.columns:
                    for val in calendar[col]:
                        if isinstance(val, (datetime, pd.Timestamp)):
                            earnings_date = [val]
                            break
        
        if not earnings_date:
            return False, "Pas de date de résultats imminente détectée."

        if not isinstance(earnings_date, list):
            earnings_date = [earnings_date]

        now = datetime.now()
        ten_days_later = now + timedelta(days=HOLDING_PERIOD_DAYS)
        
        for date_val in earnings_date:
            if isinstance(date_val, str):
                try:
                    dt = pd.to_datetime(date_val).to_pydatetime()
                except:
                    continue
            elif isinstance(date_val, (datetime, pd.Timestamp)):
                dt = date_val.to_pydatetime() if hasattr(date_val, 'to_pydatetime') else date_val
            else:
                continue
                
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
                
            if now <= dt <= ten_days_later:
                return True, f"Blackout Activé : Publication de résultats le {dt.strftime('%d/%m/%Y')} (sous {HOLDING_PERIOD_DAYS} jours)."
                
        return False, "Aucune publication sous 10 jours ouvrés."
    except Exception as e:
        return False, f"Vérification calendrier indisponible ({str(e)})."
