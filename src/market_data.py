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

def fetch_market_data(ticker_symbol):
    """
    Récupère les données de marché historiques et actuelles.
    """
    ticker_obj = yf.Ticker(ticker_symbol)
    
    # Récupérer l'historique sur les 60 derniers jours (suffisant pour RSI 14 et SMA 50)
    hist = ticker_obj.history(period="60d")
    if hist.empty:
        return None, "Aucun historique de cours disponible."
        
    return ticker_obj, hist

def analyze_technical_setup(hist):
    """
    Analyse les indicateurs techniques et identifie les niveaux clés.
    """
    close_prices = hist['Close'].values
    high_prices = hist['High'].values
    low_prices = hist['Low'].values
    
    current_price = close_prices[-1]
    
    # 1. Calcul du RSI
    rsi_values = calculate_rsi(close_prices)
    current_rsi = rsi_values[-1]
    
    # 2. Moyennes mobiles (SMA 20 et SMA 50)
    sma_20 = hist['Close'].rolling(window=20).mean().values[-1]
    sma_50 = hist['Close'].rolling(window=50).mean().values[-1]
    
    # 3. Niveaux de support & résistance
    # Support 1 : Plus bas des 10 dernières sessions (hors session en cours)
    support_10d = np.min(low_prices[-11:-1]) if len(low_prices) > 11 else np.min(low_prices[:-1])
    # Support 2 : Plus bas des 30 dernières sessions
    support_30d = np.min(low_prices[-31:-1]) if len(low_prices) > 31 else np.min(low_prices[:-1])
    
    support = support_10d if support_10d < current_price else support_30d
    if support >= current_price:
        support = current_price * 0.97 # Fallback
        
    # Résistance 1 : Plus haut des 10 dernières sessions (hors session en cours)
    resistance_10d = np.max(high_prices[-11:-1]) if len(high_prices) > 11 else np.max(high_prices[:-1])
    # Résistance 2 : Plus haut des 30 dernières sessions
    resistance_30d = np.max(high_prices[-31:-1]) if len(high_prices) > 31 else np.max(high_prices[:-1])
    
    resistance = resistance_10d if resistance_10d > current_price else resistance_30d
    if resistance <= current_price:
        resistance = current_price * 1.03 # Fallback
        
    # 4. Volatilité Historique Annualisée (30 jours)
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
        
    return {
        "current_price": current_price,
        "rsi": current_rsi,
        "sma_20": sma_20,
        "sma_50": sma_50,
        "support": support,
        "resistance": resistance,
        "recent_high": np.max(high_prices[-10:]),
        "historical_volatility": hist_vol,
        "vix": vix_val
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
