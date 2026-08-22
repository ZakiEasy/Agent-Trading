import os
import time
import base64
import requests
from src.config import (
    TRADING212_API_KEY,
    TRADING212_API_SECRET,
    TRADING212_ENVIRONMENT,
    TRADING212_BASE_URL
)

# Cache en mémoire avec TTL de 60s
_T212_CACHE = {
    "cash": {"data": None, "ts": 0},
    "portfolio": {"data": None, "ts": 0},
    "account": {"data": None, "ts": 0}
}
T212_CACHE_TTL = 60  # secondes

# Clé API dynamique configurable en session
_RUNTIME_CONFIG = {
    "api_key": TRADING212_API_KEY,
    "api_secret": TRADING212_API_SECRET,
    "environment": TRADING212_ENVIRONMENT
}

def set_runtime_trading212_config(api_key=None, api_secret=None, environment=None):
    """
    Met à jour la configuration Trading 212 à l'exécution.
    """
    global _RUNTIME_CONFIG, _T212_CACHE
    if api_key is not None:
        _RUNTIME_CONFIG["api_key"] = api_key.strip()
    if api_secret is not None:
        _RUNTIME_CONFIG["api_secret"] = api_secret.strip()
    if environment is not None:
        _RUNTIME_CONFIG["environment"] = environment.lower().strip()
    
    # Invalider le cache
    _T212_CACHE = {
        "cash": {"data": None, "ts": 0},
        "portfolio": {"data": None, "ts": 0},
        "account": {"data": None, "ts": 0}
    }

def get_trading212_base_url():
    env = _RUNTIME_CONFIG.get("environment") or "live"
    if env == "demo":
        return "https://demo.trading212.com/api/v0"
    return "https://live.trading212.com/api/v0"

def get_trading212_headers(api_key=None, api_secret=None):
    """
    Construit les headers d'authentification pour l'API Trading 212.
    Supporte les formats Token direct et Basic Auth (Key:Secret).
    """
    key = api_key if api_key is not None else _RUNTIME_CONFIG.get("api_key")
    secret = api_secret if api_secret is not None else _RUNTIME_CONFIG.get("api_secret")

    if not key:
        return None

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    if secret:
        # Basic Auth: base64(key:secret)
        raw_creds = f"{key}:{secret}"
        encoded = base64.b64encode(raw_creds.encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {encoded}"
    else:
        # Direct API Key header
        headers["Authorization"] = key

    return headers

def normalize_t212_ticker(t212_ticker):
    """
    Convertit un ticker Trading 212 (ex: 'AAPL_US_EQ', 'SAN_FR_EQ', 'MC_FR_EQ', 'ASML_NL_EQ')
    en symbole boursier universel Yahoo Finance / Google Sheets (ex: 'AAPL', 'SAN.PA', 'MC.PA', 'ASML.AS').
    """
    if not t212_ticker:
        return ""
    
    sym = str(t212_ticker).strip().upper()
    
    # Remplacements de suffixes fréquents Trading 212
    if sym.endswith("_US_EQ"):
        return sym.replace("_US_EQ", "")
    elif sym.endswith("_FR_EQ"):
        return sym.replace("_FR_EQ", ".PA")
    elif sym.endswith("_DE_EQ"):
        return sym.replace("_DE_EQ", ".DE")
    elif sym.endswith("_NL_EQ"):
        return sym.replace("_NL_EQ", ".AS")
    elif sym.endswith("_UK_EQ"):
        return sym.replace("_UK_EQ", ".L")
    elif sym.endswith("_ES_EQ"):
        return sym.replace("_ES_EQ", ".MC")
    elif sym.endswith("_IT_EQ"):
        return sym.replace("_IT_EQ", ".MI")
    elif sym.endswith("_BE_EQ"):
        return sym.replace("_BE_EQ", ".BR")
    elif sym.endswith("_CH_EQ"):
        return sym.replace("_CH_EQ", ".SW")
        
    return sym

def test_trading212_connection(api_key=None, api_secret=None, environment=None):
    """
    Vérifie si les identifiants Trading 212 sont valides en appelant l'endpoint de cash ou d'info compte.
    """
    headers = get_trading212_headers(api_key, api_secret)
    if not headers:
        return {
            "connected": False,
            "error": "Clé API Trading 212 non renseignée.",
            "environment": environment or _RUNTIME_CONFIG.get("environment", "live")
        }

    env = environment or _RUNTIME_CONFIG.get("environment", "live")
    base_url = "https://demo.trading212.com/api/v0" if env == "demo" else "https://live.trading212.com/api/v0"

    try:
        url = f"{base_url}/equity/account/cash"
        res = requests.get(url, headers=headers, timeout=8)
        
        if res.status_code == 200:
            cash_data = res.json()
            formatted = {
                "connected": True,
                "free": float(cash_data.get("free", 0.0)),
                "total": float(cash_data.get("total", 0.0)),
                "invested": float(cash_data.get("invested", 0.0)),
                "ppl": float(cash_data.get("ppl", 0.0)),
                "currency": "EUR"
            }
            _T212_CACHE["cash"] = {"data": formatted, "ts": time.time()}
            return {
                "connected": True,
                "environment": env,
                "message": "Connexion à Trading 212 réussie !",
                "cash": cash_data
            }
        elif res.status_code in [401, 403]:
            return {
                "connected": False,
                "error": f"Authentification refusée par Trading 212 (Code HTTP {res.status_code}). Vérifiez votre clé API.",
                "environment": env
            }
        elif res.status_code == 429:
            if _T212_CACHE["cash"]["data"]:
                return {
                    "connected": True,
                    "environment": env,
                    "message": "Connexion active (Rate limit 429 temporaire, données en cache)",
                    "cash": _T212_CACHE["cash"]["data"]
                }
            return {
                "connected": False,
                "error": "Limite d'appels atteinte (HTTP 429). Réessayez dans quelques secondes.",
                "environment": env
            }
        else:
            return {
                "connected": False,
                "error": f"Erreur Trading 212 (Code HTTP {res.status_code}) : {res.text}",
                "environment": env
            }
    except requests.exceptions.RequestException as e:
        return {
            "connected": False,
            "error": f"Erreur de connexion réseau vers Trading 212 : {str(e)}",
            "environment": env
        }

def get_trading212_cash(force_refresh=False):
    """
    Récupère le solde d'espèces et de compte Trading 212 avec cache de 60s.
    """
    global _T212_CACHE
    now = time.time()
    if not force_refresh and _T212_CACHE["cash"]["data"] and (now - _T212_CACHE["cash"]["ts"]) < T212_CACHE_TTL:
        return _T212_CACHE["cash"]["data"]

    headers = get_trading212_headers()
    if not headers:
        return {
            "connected": False,
            "free": 0.0,
            "total": 0.0,
            "invested": 0.0,
            "ppl": 0.0,
            "currency": "EUR"
        }

    base_url = get_trading212_base_url()
    try:
        url = f"{base_url}/equity/account/cash"
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            formatted = {
                "connected": True,
                "free": float(data.get("free", 0.0)),
                "total": float(data.get("total", 0.0)),
                "invested": float(data.get("invested", 0.0)),
                "ppl": float(data.get("ppl", 0.0)),
                "currency": "EUR"
            }
            _T212_CACHE["cash"] = {"data": formatted, "ts": now}
            return formatted
        elif res.status_code == 429 and _T212_CACHE["cash"]["data"]:
            return _T212_CACHE["cash"]["data"]
        else:
            return {
                "connected": False,
                "error": f"HTTP {res.status_code}: {res.text}",
                "free": 0.0,
                "total": 0.0,
                "invested": 0.0,
                "ppl": 0.0,
                "currency": "EUR"
            }
    except Exception as e:
        print(f"⚠️ Erreur récupération Cash Trading 212: {e}")
        if _T212_CACHE["cash"]["data"]:
            return _T212_CACHE["cash"]["data"]
        return {
            "connected": False,
            "error": str(e),
            "free": 0.0,
            "total": 0.0,
            "invested": 0.0,
            "ppl": 0.0,
            "currency": "EUR"
        }

def get_trading212_open_positions(force_refresh=False):
    """
    Récupère la liste des positions ouvertes depuis l'API Trading 212.
    Format normalisé compatible avec le portefeuille de l'application.
    """
    global _T212_CACHE
    now = time.time()
    if not force_refresh and _T212_CACHE["portfolio"]["data"] and (now - _T212_CACHE["portfolio"]["ts"]) < T212_CACHE_TTL:
        return _T212_CACHE["portfolio"]["data"]

    headers = get_trading212_headers()
    if not headers:
        return []

    base_url = get_trading212_base_url()
    try:
        url = f"{base_url}/equity/portfolio"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            raw_positions = res.json()
            if not isinstance(raw_positions, list):
                raw_positions = [raw_positions]

            positions = []
            for item in raw_positions:
                t212_ticker = item.get("ticker", "")
                norm_symbol = normalize_t212_ticker(t212_ticker)
                
                qty = float(item.get("quantity", 0.0))
                pru = float(item.get("averagePrice", 0.0))
                current_price = float(item.get("currentPrice", pru))
                ppl = float(item.get("ppl", 0.0))
                
                invested = pru * qty
                current_val = current_price * qty
                pnl_pct = (ppl / invested * 100) if invested > 0 else 0.0
                
                init_date = str(item.get("initialFillDate", ""))
                if init_date:
                    init_date = init_date.split("T")[0]

                positions.append({
                    "id": f"T212_{t212_ticker}",
                    "symbol": norm_symbol,
                    "raw_symbol": t212_ticker,
                    "name": norm_symbol,
                    "entry_date": init_date or time.strftime("%Y-%m-%d"),
                    "pru": pru,
                    "quantity": qty,
                    "invested_amount": invested,
                    "current_price": current_price,
                    "current_value": current_val,
                    "pnl_amount": ppl,
                    "pnl_pct": pnl_pct,
                    "stop_loss": pru * 0.97,
                    "tp1": pru * 1.0125,
                    "tp2": pru * 1.0225,
                    "account": "Trading 212",
                    "broker": "Trading 212",
                    "currency": "EUR" if ".PA" in norm_symbol or ".DE" in norm_symbol or ".AS" in norm_symbol else "USD",
                    "status": "OUVERT",
                    "notes": "Synchronisé via API Trading 212"
                })

            _T212_CACHE["portfolio"] = {"data": positions, "ts": now}
            return positions
        else:
            print(f"⚠️ Erreur HTTP {res.status_code} Trading 212 portfolio: {res.text}")
            return []
    except Exception as e:
        print(f"⚠️ Erreur récupération positions Trading 212: {e}")
        return []

def get_trading212_orders_history(limit=50):
    """
    Récupère l'historique récent des ordres exécutés sur Trading 212.
    """
    headers = get_trading212_headers()
    if not headers:
        return []

    base_url = get_trading212_base_url()
    try:
        url = f"{base_url}/equity/orders/history?limit={limit}"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            orders = data.get("items", []) if isinstance(data, dict) else data
            return orders if isinstance(orders, list) else []
        return []
    except Exception as e:
        print(f"⚠️ Erreur récupération ordres Trading 212: {e}")
        return []
