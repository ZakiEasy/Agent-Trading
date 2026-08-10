import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from src.config import MIN_DROP_PCT, MAX_DROP_PCT, LOOKBACK_DAYS, HOLDING_PERIOD_DAYS

def calculate_rsi(prices, period=14):
    """
    Calcule le Relative Strength Index (RSI).
    """
    deltas = np.diff(prices)
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

def get_usd_conversion_rate(currency_code):
    """
    Récupère le taux de conversion depuis une autre devise vers le USD.
    """
    if not currency_code:
        return 1.0
    currency_code = currency_code.upper().strip()
    if currency_code == "USD":
        return 1.0
        
    # Pence sterling (GBp/GBX) -> GBP -> USD
    factor = 1.0
    if currency_code in ["GBX", "GBP"]:
        currency_code = "GBP"
        if currency_code == "GBX":
            factor = 0.01

    ticker_name = f"{currency_code}USD=X"
    try:
        t = yf.Ticker(ticker_name)
        hist = t.history(period="1d")
        if not hist.empty:
            return float(hist['Close'].values[-1]) * factor
        else:
            # Essayer l'inverse USD/CURR
            t_inv = yf.Ticker(f"USD{currency_code}=X")
            hist_inv = t_inv.history(period="1d")
            if not hist_inv.empty:
                return (1.0 / float(hist_inv['Close'].values[-1])) * factor
    except Exception as e:
        print(f"Error fetching exchange rate for {currency_code}: {e}")
    return 1.0

def fetch_market_data(ticker_symbol):
    """
    Récupère les données de marché historiques et actuelles.
    Garde nativement le USD et EUR. Convertit les autres devises (ex: KRW) en USD.
    On récupère 300 jours d'historique pour calculer la SMA 200 de fond.
    """
    ticker_obj = yf.Ticker(ticker_symbol)
    
    # Récupérer l'historique sur les 300 derniers jours
    hist = ticker_obj.history(period="300d")
    if hist.empty:
        return None, "Aucun historique de cours disponible."
        
    # Détecter la devise
    try:
        info = ticker_obj.info
        currency = info.get("currency", "USD").upper()
    except:
        currency = "USD"
        
    # Normaliser la devise cible
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
            print(f"Converted {ticker_symbol} prices from {currency} to USD using rate {rate:.6f}")
        currency = "USD"
    else:
        currency = target_currency
        
    hist.attrs['currency'] = currency
    return ticker_obj, hist

def calculate_qqe(close_prices, rsi_period=14, sf=5, wilder_period=27):
    """
    Calcule le QQE (Quantitative Qualitative Estimation) pour les signaux de momentum.
    """
    rsi = calculate_rsi(close_prices, period=rsi_period)
    
    # 2. Lissage du RSI via EMA (sf=5)
    rsi_df = pd.Series(rsi)
    smoothed_rsi = rsi_df.ewm(span=sf, adjust=False).mean().values
    
    # 3. ATR rapide du RSI lissé
    rsi_change = np.abs(np.diff(smoothed_rsi))
    rsi_change = np.insert(rsi_change, 0, 0)
    rsi_change_df = pd.Series(rsi_change)
    
    # Wilder's MA de la variation du RSI
    wilder_ma = rsi_change_df.ewm(alpha=1/wilder_period, adjust=False).mean().values
    
    # Trailing Band
    atr_rsi = wilder_ma * 4.236
    
    # Calcul de la ligne de trailing stop QQE
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
            
    # Signal d'achat: le RSI lissé repasse au-dessus de la ligne QQE
    buy_signal = (smoothed_rsi[-1] > tr[-1]) and (smoothed_rsi[-2] <= tr[-2])
    return {
        "smoothed_rsi": float(smoothed_rsi[-1]),
        "qqe_line": float(tr[-1]),
        "buy_signal": bool(buy_signal)
    }

def analyze_technical_setup(hist):
    """
    Analyse les indicateurs techniques et identifie les niveaux clés.
    Ajoute la SMA 200, le canal MRC, les signaux QQE et la confirmation du Volume.
    """
    close_prices = hist['Close'].values
    high_prices = hist['High'].values
    low_prices = hist['Low'].values
    volume_values = hist['Volume'].values
    
    current_price = close_prices[-1]
    
    # 1. Calcul du RSI
    rsi_values = calculate_rsi(close_prices)
    current_rsi = rsi_values[-1]
    
    # 2. Moyennes mobiles (SMA 20, SMA 50 et SMA 200 de fond)
    sma_20 = hist['Close'].rolling(window=20).mean().values[-1]
    sma_50 = hist['Close'].rolling(window=50).mean().values[-1]
    
    # Fallback pour SMA 200 si historique plus court
    if len(close_prices) >= 200:
        sma_200 = hist['Close'].rolling(window=200).mean().values[-1]
    else:
        sma_200 = hist['Close'].mean()
    
    # 3. Canal de Retour à la Moyenne (MRC - Mean Reversion Channel)
    mrc_mean = sma_20
    mrc_std = hist['Close'].rolling(window=20).std().values[-1]
    mrc_lower = mrc_mean - 2 * mrc_std
    mrc_upper = mrc_mean + 2 * mrc_std
    mrc_oversold = bool(current_price <= mrc_lower)
    
    # 4. Signaux QQE
    qqe = calculate_qqe(close_prices)
    
    # 5. Volume + Moyenne Mobile 20 périodes du Volume
    current_volume = float(volume_values[-1])
    volume_sma_20 = float(hist['Volume'].rolling(window=20).mean().values[-1])
    volume_confirmed = bool(current_volume > volume_sma_20)
    
    # 6. Niveaux de support & résistance
    # Support 1 : Plus bas des 10 dernières sessions (hors session en cours)
    support_10d = np.min(low_prices[-11:-1]) if len(low_prices) > 11 else np.min(low_prices[:-1])
    support_30d = np.min(low_prices[-31:-1]) if len(low_prices) > 31 else np.min(low_prices[:-1])
    
    support = support_10d if support_10d < current_price else support_30d
    if support >= current_price:
        support = current_price * 0.97
        
    # Résistance 1 : Plus haut des 10 dernières sessions (hors session en cours)
    resistance_10d = np.max(high_prices[-11:-1]) if len(high_prices) > 11 else np.max(high_prices[:-1])
    resistance_30d = np.max(high_prices[-31:-1]) if len(high_prices) > 31 else np.max(high_prices[:-1])
    
    resistance = resistance_10d if resistance_10d > current_price else resistance_30d
    if resistance <= current_price:
        resistance = current_price * 1.03
        
    # 7. Volatilité Historique Annualisée (30 jours)
    close_30d = close_prices[-30:] if len(close_prices) >= 30 else close_prices
    if len(close_30d) > 1:
        daily_returns = np.diff(close_30d) / close_30d[:-1]
        hist_vol = np.std(daily_returns) * np.sqrt(252) * 100
    else:
        hist_vol = 0.0
        
    # VIX du marché (CBOE Volatility Index)
    vix_val = None
    try:
        vix_hist = yf.Ticker("^VIX").history(period="1d")
        if not vix_hist.empty:
            vix_val = vix_hist['Close'].values[-1]
    except:
        pass
        
    currency = hist.attrs.get('currency', 'USD')
    return {
        "current_price": current_price,
        "rsi": current_rsi,
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
        "resistance": resistance,
        "recent_high": np.max(high_prices[-10:]),
        "historical_volatility": hist_vol,
        "vix": vix_val,
        "currency": currency
    }

def qualify_price_drop(hist):
    """
    Détermine si le titre a subi une baisse récente de -3% à -8% sur 1 à 3 sessions.
    """
    close_prices = hist['Close'].values
    current_price = close_prices[-1]
    
    detected_drop = 0.0
    detected_lookback = 0
    reference_price = 0.0
    
    # Tester les baisses par rapport aux 1, 2 et 3 sessions précédentes
    for days in range(1, LOOKBACK_DAYS + 1):
        prev_price = close_prices[-(days + 1)]
        drop = ((current_price - prev_price) / prev_price) * 100
        
        # On cherche une baisse (valeur négative)
        if -MAX_DROP_PCT <= drop <= -MIN_DROP_PCT:
            # On prend la baisse la plus significative qualifiante
            if abs(drop) > abs(detected_drop):
                detected_drop = drop
                detected_lookback = days
                reference_price = prev_price
                
    if detected_drop != 0.0:
        return True, {
            "drop_pct": detected_drop,
            "lookback_days": detected_lookback,
            "reference_price": reference_price,
            "nature": "CONJONCTURELLE" # Par défaut, sera qualifié en détail dans le CLI/rapport
        }
        
    return False, {
        "drop_pct": ((current_price - close_prices[-2]) / close_prices[-2]) * 100,
        "lookback_days": 1,
        "reference_price": close_prices[-2],
        "nature": "HORS CRITÈRES"
    }

def check_earnings_blackout(ticker_obj):
    """
    Vérifie si une publication de résultats (Earnings) est prévue dans les 10 prochains jours.
    """
    try:
        calendar = ticker_obj.calendar
        if calendar is None or (isinstance(calendar, dict) and not calendar):
            return False, "Aucun calendrier de résultats trouvé (yfinance)."
            
        # yfinance renvoie souvent les dates de résultats sous forme de DataFrame ou Dictionnaire
        # Si c'est un dictionnaire ou un DataFrame
        earnings_date = None
        if isinstance(calendar, dict) and "Earnings Date" in calendar:
            earnings_date = calendar["Earnings Date"]
        elif hasattr(calendar, 'get') and calendar.get("Earnings Date"):
            earnings_date = calendar.get("Earnings Date")
            
        if not earnings_date and isinstance(calendar, pd.DataFrame) and not calendar.empty:
            # Parfois yfinance met les dates en lignes/colonnes
            if "Value" in calendar.columns:
                earnings_date = calendar.loc["Earnings Date"].values[0]
            else:
                # Essayer de lire la première valeur temporelle
                for col in calendar.columns:
                    for val in calendar[col]:
                        if isinstance(val, (datetime, pd.Timestamp)):
                            earnings_date = [val]
                            break
        
        if not earnings_date:
            return False, "Pas de date de résultats imminente détectée."

        # Traiter les listes de dates
        if not isinstance(earnings_date, list):
            earnings_date = [earnings_date]

        now = datetime.now()
        ten_days_later = now + timedelta(days=HOLDING_PERIOD_DAYS)
        
        for date_val in earnings_date:
            if isinstance(date_val, str):
                try:
                    # Tenter de parser la date
                    dt = pd.to_datetime(date_val).to_pydatetime()
                except:
                    continue
            elif isinstance(date_val, (datetime, pd.Timestamp)):
                dt = date_val.to_pydatetime() if hasattr(date_val, 'to_pydatetime') else date_val
            else:
                continue
                
            # Rendre naïf pour comparaison
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
                
            if now <= dt <= ten_days_later:
                return True, f"Blackout Activé : Publication prévue le {dt.strftime('%d/%m/%Y')} (sous {HOLDING_PERIOD_DAYS} jours)."
                
        return False, "Aucune publication sous 10 jours."
    except Exception as e:
        # En cas d'erreur de yfinance sur calendar (très fréquent), on ne bloque pas mais on l'indique
        return False, f"Impossible de vérifier le calendrier des résultats : {str(e)}"
