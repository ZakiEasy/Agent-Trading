import os
import time
import base64
import requests
from src.config import (
    TRADING212_READ_API_KEY,
    TRADING212_READ_API_SECRET,
    TRADING212_EXEC_API_KEY,
    TRADING212_EXEC_API_SECRET,
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

# Clés API dynamiques séparées : LECTURE SEULE vs EXÉCUTION / ROBOT
_RUNTIME_CONFIG = {
    "read_api_key": TRADING212_READ_API_KEY,
    "read_api_secret": TRADING212_READ_API_SECRET,
    "exec_api_key": TRADING212_EXEC_API_KEY,
    "exec_api_secret": TRADING212_EXEC_API_SECRET,
    "api_key": TRADING212_READ_API_KEY,
    "api_secret": TRADING212_READ_API_SECRET,
    "environment": TRADING212_ENVIRONMENT
}

def load_persisted_trading212_config():
    """Charge la configuration et les clés API Trading 212 depuis Supabase (table app_settings)."""
    global _RUNTIME_CONFIG
    try:
        from src.supabase_connector import get_app_setting
        cfg = get_app_setting("trading212_api_config")
        if cfg and isinstance(cfg, dict):
            if cfg.get("read_api_key"):
                _RUNTIME_CONFIG["read_api_key"] = cfg["read_api_key"]
            if cfg.get("read_api_secret"):
                _RUNTIME_CONFIG["read_api_secret"] = cfg["read_api_secret"]
            if cfg.get("exec_api_key"):
                _RUNTIME_CONFIG["exec_api_key"] = cfg["exec_api_key"]
            if cfg.get("exec_api_secret"):
                _RUNTIME_CONFIG["exec_api_secret"] = cfg["exec_api_secret"]
            if cfg.get("api_key"):
                _RUNTIME_CONFIG["api_key"] = cfg["api_key"]
            if cfg.get("api_secret"):
                _RUNTIME_CONFIG["api_secret"] = cfg["api_secret"]
            if cfg.get("environment"):
                _RUNTIME_CONFIG["environment"] = cfg["environment"]
    except Exception as e:
        pass

def save_persisted_trading212_config():
    """Sauvegarde la configuration Trading 212 dans Supabase pour pérenniser la connexion."""
    try:
        from src.supabase_connector import save_app_setting
        payload = {
            "read_api_key": _RUNTIME_CONFIG.get("read_api_key"),
            "read_api_secret": _RUNTIME_CONFIG.get("read_api_secret"),
            "exec_api_key": _RUNTIME_CONFIG.get("exec_api_key"),
            "exec_api_secret": _RUNTIME_CONFIG.get("exec_api_secret"),
            "api_key": _RUNTIME_CONFIG.get("api_key"),
            "api_secret": _RUNTIME_CONFIG.get("api_secret"),
            "environment": _RUNTIME_CONFIG.get("environment", "live")
        }
        save_app_setting("trading212_api_config", payload, "Identifiants API Trading 212")
    except Exception as e:
        pass

# Restaurer la configuration depuis Supabase si disponible
load_persisted_trading212_config()

def set_runtime_trading212_config(
    read_api_key=None,
    read_api_secret=None,
    exec_api_key=None,
    exec_api_secret=None,
    api_key=None,
    api_secret=None,
    environment=None
):
    """
    Met à jour la configuration Trading 212 à l'exécution avec clés séparées et persistance Supabase.
    """
    global _RUNTIME_CONFIG, _T212_CACHE
    if read_api_key is not None:
        _RUNTIME_CONFIG["read_api_key"] = read_api_key.strip()
    if read_api_secret is not None:
        _RUNTIME_CONFIG["read_api_secret"] = read_api_secret.strip()
    if exec_api_key is not None:
        _RUNTIME_CONFIG["exec_api_key"] = exec_api_key.strip()
    if exec_api_secret is not None:
        _RUNTIME_CONFIG["exec_api_secret"] = exec_api_secret.strip()
    if api_key is not None:
        clean_key = api_key.strip()
        _RUNTIME_CONFIG["api_key"] = clean_key
        if read_api_key is None:
            _RUNTIME_CONFIG["read_api_key"] = clean_key
        if exec_api_key is None:
            _RUNTIME_CONFIG["exec_api_key"] = clean_key
    if api_secret is not None:
        clean_secret = api_secret.strip()
        _RUNTIME_CONFIG["api_secret"] = clean_secret
        if read_api_secret is None:
            _RUNTIME_CONFIG["read_api_secret"] = clean_secret
        if exec_api_secret is None:
            _RUNTIME_CONFIG["exec_api_secret"] = clean_secret
    if environment is not None:
        _RUNTIME_CONFIG["environment"] = environment.lower().strip()
    
    # Invalider le cache
    _T212_CACHE = {
        "cash": {"data": None, "ts": 0},
        "portfolio": {"data": None, "ts": 0},
        "account": {"data": None, "ts": 0}
    }

    # Sauvegarder immédiatement dans Supabase
    save_persisted_trading212_config()

def get_trading212_base_url():
    env = _RUNTIME_CONFIG.get("environment") or "live"
    if env == "demo":
        return "https://demo.trading212.com/api/v0"
    return "https://live.trading212.com/api/v0"

def _build_auth_headers(key, secret=None):
    """Construit les headers d'authentification Bearer ou Basic Auth."""
    if not key:
        return None
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    if secret:
        raw_creds = f"{key}:{secret}"
        encoded = base64.b64encode(raw_creds.encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {encoded}"
    else:
        headers["Authorization"] = key
    return headers

def get_trading212_read_headers(api_key=None, api_secret=None):
    """Headers pour les opérations de LECTURE SEULE (Portefeuille, Cash, Dividendes)."""
    key = api_key or _RUNTIME_CONFIG.get("read_api_key") or _RUNTIME_CONFIG.get("api_key")
    secret = api_secret or _RUNTIME_CONFIG.get("read_api_secret") or _RUNTIME_CONFIG.get("api_secret")
    return _build_auth_headers(key, secret)

def get_trading212_exec_headers(api_key=None, api_secret=None):
    """Headers pour les opérations d'EXÉCUTION / ROBOT (Passation et annulation d'ordres)."""
    key = api_key or _RUNTIME_CONFIG.get("exec_api_key") or _RUNTIME_CONFIG.get("api_key")
    secret = api_secret or _RUNTIME_CONFIG.get("exec_api_secret") or _RUNTIME_CONFIG.get("api_secret")
    return _build_auth_headers(key, secret)

def get_trading212_headers(api_key=None, api_secret=None, purpose="read"):
    """Fonction générique construisant les headers selon la finalité (read ou exec)."""
    if purpose == "exec":
        return get_trading212_exec_headers(api_key, api_secret)
    return get_trading212_read_headers(api_key, api_secret)


def normalize_t212_ticker(t212_ticker):
    """
    Convertit un ticker Trading 212 (ex: 'AAPL_US_EQ', 'SANp_EQ', 'ORp_EQ', 'LRp_EQ', 'STMpp_EQ', 'ASMLa_EQ')
    en symbole boursier universel Yahoo Finance / Google Sheets (ex: 'AAPL', 'SAN.PA', 'OR.PA', 'LR.PA', 'STM.PA', 'ASML.AS').
    """
    if not t212_ticker:
        return ""
    
    sym = str(t212_ticker).strip()
    
    # Cas particuliers de tickers Euronext sur Yahoo Finance
    if sym.startswith("STM") and ("_EQ" in sym or "pp_" in sym or "p_" in sym):
        return "STMPA.PA"

    # 1. Remplacements de suffixes explicites de pays
    if sym.endswith("_US_EQ"):
        return sym.replace("_US_EQ", "").upper()
    elif sym.endswith("_FR_EQ"):
        return sym.replace("_FR_EQ", ".PA").upper()
    elif sym.endswith("_DE_EQ"):
        return sym.replace("_DE_EQ", ".DE").upper()
    elif sym.endswith("_NL_EQ"):
        return sym.replace("_NL_EQ", ".AS").upper()
    elif sym.endswith("_UK_EQ"):
        return sym.replace("_UK_EQ", ".L").upper()
    elif sym.endswith("_ES_EQ"):
        return sym.replace("_ES_EQ", ".MC").upper()
    elif sym.endswith("_IT_EQ"):
        return sym.replace("_IT_EQ", ".MI").upper()
    elif sym.endswith("_BE_EQ"):
        return sym.replace("_BE_EQ", ".BR").upper()
    elif sym.endswith("_CH_EQ"):
        return sym.replace("_CH_EQ", ".SW").upper()
    
    # 2. Suffixes compacts de places boursières Trading 212
    if sym.endswith("pp_EQ"):
        base = sym[:-5]
        return f"{base}.PA".upper()
    elif sym.endswith("p_EQ"):
        base = sym[:-4]
        return f"{base}.PA".upper()
    elif sym.endswith("d_EQ"):
        base = sym[:-4]
        return f"{base}.DE".upper()
    elif sym.endswith("a_EQ"):
        base = sym[:-4]
        return f"{base}.AS".upper()
    elif sym.endswith("l_EQ"):
        base = sym[:-4]
        return f"{base}.L".upper()
    elif sym.endswith("m_EQ"):
        base = sym[:-4]
        return f"{base}.MI".upper()
    elif sym.endswith("_EQ"):
        base = sym[:-3]
        return base.upper()
        
    return sym.upper()

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


_T212_PERMS_CACHE = {"data": None, "ts": 0}

def check_trading212_api_permissions(force_refresh=False):
    """
    Diagnostique précisément les droits accordés aux clés API Trading 212 (avec cache de 30s) :
    1. Clé de LECTURE SEULE (Solde, Portefeuille, Cash, Dividendes)
    2. Clé d'EXÉCUTION / ROBOT (Passation, Modification, Annulation d'ordres)
    """
    global _T212_PERMS_CACHE
    now = time.time()
    if not force_refresh and _T212_PERMS_CACHE["data"] and (now - _T212_PERMS_CACHE["ts"]) < 30:
        return _T212_PERMS_CACHE["data"]

    read_headers = get_trading212_read_headers()
    exec_headers = get_trading212_exec_headers()
    base_url = get_trading212_base_url()
    env = _RUNTIME_CONFIG.get("environment", "live")

    # 1. Test Clé Lecture Seule
    read_ok = False
    read_msg = ""
    if not read_headers:
        read_msg = "Clé LECTURE non configurée."
    else:
        try:
            r_cash = requests.get(f"{base_url}/equity/account/cash", headers=read_headers, timeout=6)
            if r_cash.status_code == 200:
                read_ok = True
                read_msg = "✅ Clé LECTURE valide et connectée."
            elif r_cash.status_code == 429:
                read_ok = True
                read_msg = "✅ Clé LECTURE connectée (Flux actif, limitation temporaire 429)."
            elif r_cash.status_code in [401, 403]:
                read_ok = False
                read_msg = f"❌ Clé LECTURE rejetée (HTTP {r_cash.status_code})"
            else:
                read_msg = f"Statut lecture : HTTP {r_cash.status_code}"
        except Exception as e:
            read_msg = f"Erreur réseau lecture : {str(e)}"

    # 2. Test Clé Exécution Ordres (Robot)
    orders_ok = False
    orders_msg = ""
    if not exec_headers:
        orders_msg = "Clé EXÉCUTION non configurée."
    else:
        try:
            r_ord = requests.get(f"{base_url}/equity/orders", headers=exec_headers, timeout=6)
            if r_ord.status_code == 200:
                orders_ok = True
                orders_msg = "✅ Clé EXÉCUTION active : Permissions d'ordres et gestion des paliers validées."
            elif r_ord.status_code == 429:
                orders_ok = True
                orders_msg = "✅ Clé EXÉCUTION active : Permissions d'ordres validées (Flux actif, limitation 429)."
            elif r_ord.status_code in [401, 403]:
                orders_ok = False
                orders_msg = "⚠️ Clé EXÉCUTION rejetée ou sans permission d'ordres."
            else:
                orders_msg = f"Statut ordres : HTTP {r_ord.status_code}"
        except Exception as e:
            orders_msg = f"Erreur réseau exécution : {str(e)}"

    overall_valid = read_ok and orders_ok

    if orders_ok:
        msg = orders_msg
    elif read_ok:
        msg = read_msg
    elif not read_headers and not exec_headers:
        msg = "Clés API non configurées"
    else:
        msg = f"{orders_msg} | {read_msg}"

    result = {
        "valid": overall_valid,
        "read_permission": read_ok,
        "orders_permission": orders_ok,
        "read_message": read_msg,
        "orders_message": orders_msg,
        "message": msg,
        "environment": env
    }

    _T212_PERMS_CACHE = {"data": result, "ts": now}
    return result



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

                is_eur = (".PA" in norm_symbol or ".DE" in norm_symbol or ".AS" in norm_symbol or "p_EQ" in t212_ticker or "pp_EQ" in t212_ticker)
                currency = "EUR" if is_eur else "USD"

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
                    "currency": currency,
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

def get_trading212_orders_history(limit=50, max_pages=3):
    """
    Récupère l'historique des ordres exécutés sur Trading 212 via l'endpoint officiel v0
    (/equity/history/orders) avec gestion de la pagination par curseur.
    """
    headers = get_trading212_headers()
    if not headers:
        return []

    base_url = get_trading212_base_url()
    all_orders = []
    current_url = f"{base_url}/equity/history/orders?limit={min(limit, 50)}"
    page_count = 0

    try:
        while current_url and page_count < max_pages:
            res = requests.get(current_url, headers=headers, timeout=10)
            if res.status_code != 200:
                print(f"⚠️ Erreur récupération historique ordres Trading 212 (HTTP {res.status_code}): {res.text}")
                break

            data = res.json()
            items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            all_orders.extend(items)

            # Pagination
            next_path = data.get("nextPagePath") if isinstance(data, dict) else None
            if next_path:
                if next_path.startswith("http"):
                    current_url = next_path
                else:
                    current_url = f"https://live.trading212.com/api/v0{next_path}" if "live" in base_url else f"https://demo.trading212.com/api/v0{next_path}"
                page_count += 1
            else:
                current_url = None

        return all_orders
    except Exception as e:
        print(f"⚠️ Erreur récupération ordres Trading 212: {e}")
        return all_orders

def get_trading212_dividends_history(limit=50):
    """
    Récupère l'historique des dividendes versés sur Trading 212.
    """
    headers = get_trading212_headers()
    if not headers:
        return []

    base_url = get_trading212_base_url()
    try:
        url = f"{base_url}/equity/history/dividends?limit={limit}"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            items = data.get("items", []) if isinstance(data, dict) else data
            return items if isinstance(items, list) else []
        return []
    except Exception as e:
        print(f"⚠️ Erreur récupération dividendes Trading 212: {e}")
        return []

def sync_trading212_history_to_journal():
    """
    Récupère l'historique des ordres exécutés via l'API Trading 212 et construit les entrées du Journal de Trading.
    Gère à la fois le format structuré {order: ..., fill: ...} et les formats directs d'ordres.
    """
    orders = get_trading212_orders_history(limit=50, max_pages=4)
    if not orders:
        return []

    closed_trades = []
    for item in orders:
        if not isinstance(item, dict):
            continue

        # Extraction de l'ordre et du fill
        order = item.get("order", item)
        fill = item.get("fill", {})

        status = str(order.get("status", "")).upper()
        if status != "FILLED":
            continue

        side = str(order.get("side", "")).upper()
        if side != "SELL":
            continue

        order_id = str(order.get("id", fill.get("id", "")))
        raw_ticker = str(order.get("ticker", item.get("ticker", "")))
        ticker = normalize_t212_ticker(raw_ticker)
        
        instrument = order.get("instrument", {})
        name = instrument.get("name") or ticker

        qty = abs(float(fill.get("quantity", order.get("filledQuantity", order.get("quantity", 0.0)))))
        fill_price = float(fill.get("price", order.get("fillPrice", order.get("limitPrice", 0.0))))
        
        wallet = fill.get("walletImpact", {})
        currency = wallet.get("currency") or order.get("currency") or ("EUR" if (".PA" in ticker or ".DE" in ticker or ".AS" in ticker) else "USD")
        
        pnl = float(wallet.get("realisedProfitLoss", order.get("ppl", order.get("result", 0.0))))
        net_val = float(wallet.get("netValue", fill_price * qty))
        
        invested = (net_val - pnl) if (net_val > 0 and pnl != 0) else (fill_price * qty)
        pru = (invested / qty) if (qty > 0 and invested > 0) else fill_price
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0.0
        
        exec_date = str(fill.get("filledAt", order.get("createdAt", time.strftime("%Y-%m-%d %H:%M:%S"))))
        if "T" in exec_date:
            exec_date = exec_date.replace("T", " ").split(".")[0].replace("Z", "")

        closed_trades.append({
            "id": f"T212_{order_id}",
            "symbol": ticker,
            "name": name,
            "open_time": exec_date,
            "close_time": exec_date,
            "pru": round(pru, 2),
            "exit_price": round(fill_price, 2),
            "quantity": qty,
            "invested_amount": round(invested, 2),
            "pnl_amount": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "days_held": 1,
            "result": "GAIN 🟢" if pnl >= 0 else "PERTE 🔴",
            "account": "Trading 212",
            "currency": currency,
            "comment": "Synchronisé via API Trading 212"
        })

    return closed_trades

def parse_trading212_csv(csv_text_or_bytes):
    """
    Parse les exports CSV de Trading 212 (Historique des transactions, ordres et trésorerie).
    Auto-détecte :
      - Les trades clôturés (Actions 'Market sell', 'Limit sell', 'Stop sell')
      - Les positions ouvertes / achats récents
      - Les opérations de trésorerie (Dépôts, Retraits, Dividendes, Intérêts)
    """
    import csv
    import io

    if isinstance(csv_text_or_bytes, bytes):
        try:
            text = csv_text_or_bytes.decode('utf-8')
        except UnicodeDecodeError:
            text = csv_text_or_bytes.decode('latin-1', errors='replace')
    else:
        text = str(csv_text_or_bytes)

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return {"closed_positions": [], "open_positions": [], "cash_operations": []}

    delimiter = ';' if lines[0].count(';') > lines[0].count(',') else ','
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    all_rows = [r for r in reader if r]

    if len(all_rows) < 2:
        return {"closed_positions": [], "open_positions": [], "cash_operations": []}

    header_idx = -1
    header = []
    for idx, r in enumerate(all_rows[:5]):
        r_clean = [str(c).strip().lower() for c in r]
        if any(k in r_clean for k in ["action", "ticker", "time", "date/heure", "result", "total", "isin"]):
            header_idx = idx
            header = r_clean
            break

    if header_idx == -1:
        header_idx = 0
        header = [str(c).strip().lower() for c in all_rows[0]]

    col_map = {}
    for idx, col in enumerate(header):
        c = col.strip().lower()
        if c == "action":
            col_map["action"] = idx
        elif any(k in c for k in ["time", "date/heure", "date"]):
            col_map["time"] = idx
        elif any(k == c or k in c for k in ["ticker", "symbole", "symbol"]) and "currency" not in c:
            col_map["ticker"] = idx
        elif any(k == c or k in c for k in ["name", "nom", "société", "societe"]) and "currency" not in c and "fee" not in c:
            col_map["name"] = idx
        elif any(k in c for k in ["no. of shares", "shares", "nombre", "quantité", "quantite", "qty"]):
            col_map["quantity"] = idx
        elif ("price / share" in c or "prix / action" in c or c == "price" or c == "cours" or c == "prix") and "currency" not in c and "devise" not in c:
            col_map["price"] = idx
        elif c.startswith("currency") or c == "devise":
            if "currency" not in col_map or "price" in c:
                col_map["currency"] = idx
        elif (c.startswith("result") or c.startswith("résultat") or c == "profit" or c == "gain") and "currency" not in c:
            col_map["result"] = idx
        elif (c.startswith("total") or c == "montant") and "currency" not in c:
            col_map["total"] = idx
        elif c == "id" or c == "transaction id" or c == "identifiant":
            col_map["id"] = idx

    closed_positions = []
    cash_operations = []

    for r in all_rows[header_idx + 1:]:
        if not r or len(r) <= 1:
            continue

        def get_val(key, default=""):
            idx = col_map.get(key)
            if idx is not None and idx < len(r):
                return str(r[idx]).strip()
            return default

        action = get_val("action").lower()
        time_str = get_val("time")
        raw_ticker = get_val("ticker").upper()
        ticker = normalize_t212_ticker(raw_ticker)
        name = get_val("name") or ticker
        qty_str = get_val("quantity", "0").replace(" ", "").replace(",", ".")
        price_str = get_val("price", "0").replace(" ", "").replace(",", ".").replace("€", "").replace("$", "")
        result_str = get_val("result", "0").replace(" ", "").replace(",", ".").replace("€", "").replace("$", "")
        total_str = get_val("total", "0").replace(" ", "").replace(",", ".").replace("€", "").replace("$", "")
        curr_str = get_val("currency", "EUR").upper()
        tid = get_val("id") or f"T212_{abs(hash(time_str + raw_ticker + action))}"

        try:
            qty = float(qty_str) if qty_str else 0.0
            price = float(price_str) if price_str else 0.0
            pnl_amount = float(result_str) if result_str else 0.0
            total_val = float(total_str) if total_str else (price * qty)
        except Exception:
            continue

        # 1. Trades de vente clôturés
        if any(k in action for k in ["sell", "vente"]):
            if qty > 0 and (price > 0 or total_val > 0):
                exit_price = price if price > 0 else (total_val / qty)
                invested = (total_val - pnl_amount) if total_val > 0 else (price * qty - pnl_amount)
                pru = (invested / qty) if (qty > 0 and invested > 0) else exit_price
                pnl_pct = (pnl_amount / invested * 100) if invested > 0 else 0.0
                
                closed_positions.append({
                    "id": f"T212_{tid}",
                    "symbol": ticker,
                    "name": name,
                    "open_time": time_str,
                    "close_time": time_str,
                    "pru": round(pru, 2),
                    "exit_price": round(exit_price, 2),
                    "quantity": qty,
                    "invested_amount": round(invested, 2),
                    "pnl_amount": round(pnl_amount, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "days_held": 1,
                    "result": "GAIN 🟢" if pnl_amount >= 0 else "PERTE 🔴",
                    "account": "Trading 212",
                    "currency": curr_str or ("EUR" if ".PA" in ticker else "USD"),
                    "comment": f"Export Trading 212 ({action})"
                })

        # 2. Opérations de Cash / Dividendes / Intérêts
        elif any(k in action for k in ["deposit", "dépôt", "depot", "withdrawal", "retrait", "dividend", "dividende", "interest", "intérêt", "interet"]):
            op_type = "DIVIDENDE" if "dividend" in action else ("INTERET" if "interest" in action else ("DEPOT" if ("deposit" in action or "dépôt" in action) else "RETRAIT"))
            cash_amt = abs(total_val if total_val > 0 else (pnl_amount if pnl_amount > 0 else price))
            cash_operations.append({
                "id": f"T212_CASH_{tid}",
                "date": time_str,
                "type": op_type,
                "symbol": ticker if op_type == "DIVIDENDE" else "",
                "amount": cash_amt,
                "currency": curr_str or "EUR",
                "account": "Trading 212",
                "description": f"Trading 212: {action.title()} {name or ''}".strip()
            })

    return {
        "closed_positions": closed_positions,
        "open_positions": [],
        "cash_operations": cash_operations
    }


# ==============================================================================
# --- 6. EXÉCUTION D'ORDRES, GESTION DU CARNET & CONVERSIONS DE TICKERS ---
# ==============================================================================

def convert_yahoo_ticker_to_t212(symbol):
    """
    Convertit un symbole standard (ex: AVGO, MC.PA, ASML.AS) en ticker officiel Trading 212.
    """
    s = str(symbol or "").upper().strip()
    if not s:
        return ""
    
    MAPPINGS = {
        "TSLA": "TSLA_US_EQ",
        "AAPL": "AAPL_US_EQ",
        "NVDA": "NVDA_US_EQ",
        "META": "META_US_EQ",
        "MSFT": "MSFT_US_EQ",
        "AVGO": "AVGO_US_EQ",
        "GOOGL": "GOOGL_US_EQ",
        "AMZN": "AMZN_US_EQ",
        "NFLX": "NFLX_US_EQ",
        "UBER": "UBER_US_EQ",
        "ARM": "ARM_US_EQ",
        "BKNG": "BKNG_US_EQ",
        "STX": "STX_US_EQ",
        "VRT": "VRT_US_EQ",
        "ESTC": "ESTC_US_EQ",
        "ASAN": "ASAN_US_EQ",
        "MC.PA": "MCp_EQ",
        "RMS.PA": "RMSp_EQ",
        "OR.PA": "ORp_EQ",
        "SAN.PA": "SANp_EQ",
        "LR.PA": "LRp_EQ",
        "ENGI.PA": "ENGIp_EQ",
        "TEP.PA": "TEPp_EQ",
        "STMPA.PA": "STMpp_EQ",
        "STM.PA": "STMpp_EQ",
        "ASML.AS": "ASMLa_EQ",
        "SAP.DE": "SAPd_EQ",
        "LIN.DE": "LINd_EQ",
        "BAYN.DE": "BAYNd_EQ",
        "HFG.DE": "HFGd_EQ",
        "SU.PA": "SUp_EQ",
        "CA.PA": "CAp_EQ",
        "BNP.PA": "BNPp_EQ",
        "GLE.PA": "GLEp_EQ",
        "AIR.PA": "AIRp_EQ",
        "TTE.PA": "TTEp_EQ",
        "IS3R.DE": "IS3Rd_EQ"
    }
    
    if s in MAPPINGS:
        return MAPPINGS[s]
    
    if s.endswith(".PA"):
        base = s.replace(".PA", "")
        return f"{base}p_EQ"
    elif s.endswith(".DE"):
        base = s.replace(".DE", "")
        return f"{base}d_EQ"
    elif s.endswith(".AS"):
        base = s.replace(".AS", "")
        return f"{base}a_EQ"
    elif "." not in s:
        return f"{s}_US_EQ"
        
    return s


def sanitize_t212_quantity(quantity):
    """
    Assure la conformité de la quantité avec les règles strictes de précision Trading 212 :
    - Si la quantité est entière ou très proche d'un entier (ex: 6.0, -6.0) -> int (précision 0)
    - Si la quantité est décimale -> arrondi à 2 décimales maximum (ex: 6.51 ou -6.51)
    Évite l'erreur HTTP 400 'quantity-precision-mismatch'.
    """
    try:
        val = float(quantity)
        if abs(val - round(val)) < 1e-4:
            return int(round(val))
        return round(val, 2)
    except Exception:
        return quantity


def place_trading212_limit_order(symbol, quantity, limit_price, time_validity="DAY"):
    """
    Émet un ordre à cours limité (Limit Order) d'achat sur Trading 212 (Utilise la clé EXÉCUTION / ROBOT).
    """
    headers = get_trading212_exec_headers()
    if not headers:
        return {"success": False, "error": "Clé API d'Exécution Trading 212 manquante ou non configurée."}
    
    t212_ticker = convert_yahoo_ticker_to_t212(symbol)
    base_url = get_trading212_base_url()
    url = f"{base_url}/equity/orders/limit"
    
    clean_qty = sanitize_t212_quantity(quantity)
    payload = {
        "ticker": t212_ticker,
        "quantity": clean_qty,
        "limitPrice": float(round(limit_price, 2)),
        "timeValidity": str(time_validity).upper()
    }
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code in [200, 201]:
            data = resp.json()
            return {"success": True, "order": data, "ticker": t212_ticker}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}", "status_code": resp.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}


def place_trading212_market_order(symbol, quantity):
    """
    Émet un ordre au marché (Market Order) d'achat ou de vente sur Trading 212 (Utilise la clé EXÉCUTION / ROBOT).
    """
    headers = get_trading212_exec_headers()
    if not headers:
        return {"success": False, "error": "Clé API d'Exécution Trading 212 manquante ou non configurée."}
    
    t212_ticker = convert_yahoo_ticker_to_t212(symbol)
    base_url = get_trading212_base_url()
    url = f"{base_url}/equity/orders/market"
    
    clean_qty = sanitize_t212_quantity(quantity)
    payload = {
        "ticker": t212_ticker,
        "quantity": clean_qty
    }
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code in [200, 201]:
            data = resp.json()
            return {"success": True, "order": data, "ticker": t212_ticker}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}", "status_code": resp.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}


def place_trading212_stop_order(symbol, quantity, stop_price, time_validity="GTC"):
    """
    Émet un ordre Stop (Stop-Loss ou Stop-Achat) sur Trading 212 (Utilise la clé EXÉCUTION / ROBOT).
    """
    headers = get_trading212_exec_headers()
    if not headers:
        return {"success": False, "error": "Clé API d'Exécution Trading 212 manquante ou non configurée."}
    
    t212_ticker = convert_yahoo_ticker_to_t212(symbol)
    base_url = get_trading212_base_url()
    url = f"{base_url}/equity/orders/stop"
    
    clean_qty = sanitize_t212_quantity(quantity)
    payload = {
        "ticker": t212_ticker,
        "quantity": clean_qty,
        "stopPrice": float(round(stop_price, 2)),
        "timeValidity": str(time_validity).upper()
    }
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code in [200, 201]:
            data = resp.json()
            return {"success": True, "order": data, "ticker": t212_ticker}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}", "status_code": resp.status_code}
    except Exception as e:
        return {"success": False, "error": str(e)}


def cancel_trading212_order(order_id):
    """
    Annule un ordre spécifique sur Trading 212 par son ID (Utilise la clé EXÉCUTION / ROBOT).
    """
    headers = get_trading212_exec_headers()
    if not headers:
        return {"success": False, "error": "Clé API d'Exécution Trading 212 manquante ou non configurée."}
    
    base_url = get_trading212_base_url()
    url = f"{base_url}/equity/orders/{order_id}"
    
    try:
        resp = requests.delete(url, headers=headers, timeout=10)
        if resp.status_code in [200, 204]:
            return {"success": True, "message": f"Ordre {order_id} annulé avec succès."}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_trading212_open_orders():
    """
    Récupère tous les ordres en attente / ouverts sur le carnet Trading 212 (Utilise la clé EXÉCUTION / ROBOT).
    """
    headers = get_trading212_exec_headers()
    if not headers:
        return []
    
    base_url = get_trading212_base_url()
    url = f"{base_url}/equity/orders"
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json() or []
        return []
    except Exception:
        return []


def cancel_all_trading212_orders():
    """
    Annule immédiatement TOUS les ordres ouverts (fonction d'urgence Kill-Switch).
    """
    open_orders = get_trading212_open_orders()
    cancelled = []
    errors = []
    
    for o in open_orders:
        oid = o.get("id")
        if oid:
            res = cancel_trading212_order(oid)
            if res.get("success"):
                cancelled.append(oid)
            else:
                errors.append({"id": oid, "error": res.get("error")})
                
    return {
        "success": True,
        "total_open_orders": len(open_orders),
        "cancelled_count": len(cancelled),
        "cancelled_order_ids": cancelled,
        "errors": errors
    }

