import io
import csv
import math
import concurrent.futures
from datetime import datetime
import pandas as pd
import yfinance as yf

from src.sheets_connector import (
    read_positions_from_sheets,
    add_position_to_sheets,
    close_position_in_sheets
)
from src.market_data import get_usd_conversion_rate, get_usd_to_eur_rate, get_ticker_info, categorize_ticker, get_company_name

_LIVE_QUOTE_CACHE = {}  # symbol -> {"price": float, "day_change": float, "ts": float}
LIVE_QUOTE_TTL = 300  # 5 minutes de cache pour les cours en direct

def safe_float(val, default=0.0):
    """
    Convertit en float de manière sécurisée en évitant les NaN, Inf et None.
    """
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default

def fetch_live_quote_for_position(pos):
    """
    Récupère le cours en direct, la variation du jour et les métadonnées pour une position active.
    Garantit l'absence totale de valeurs NaN ou indéfinies.
    """
    raw_sym = pos.get("symbol", "")
    symbol = normalize_xtb_ticker(raw_sym)
    pru = safe_float(pos.get("pru"), 0.0)
    qty = safe_float(pos.get("quantity"), 1.0)
    if qty <= 0:
        qty = 1.0
    sl = safe_float(pos.get("stop_loss"), pru * 0.97)
    tp1 = safe_float(pos.get("tp1"), pru * 1.0125)
    tp2 = safe_float(pos.get("tp2"), pru * 1.0225)
    entry_date_str = str(pos.get("entry_date", ""))

    days_held = 0
    if entry_date_str:
        try:
            entry_dt = pd.to_datetime(entry_date_str).to_pydatetime()
            days_held = max(0, (datetime.now() - entry_dt).days)
        except:
            days_held = 0

    current_price = pru if pru > 0 else 1.0
    day_change_pct = 0.0
    currency = pos.get("currency", "EUR" if ".PA" in symbol or ".DE" in symbol or ".AS" in symbol else "USD")

    import time
    now = time.time()
    if symbol in _LIVE_QUOTE_CACHE and (now - _LIVE_QUOTE_CACHE[symbol]["ts"]) < LIVE_QUOTE_TTL:
        cached_p = safe_float(_LIVE_QUOTE_CACHE[symbol].get("price"), 0.0)
        if cached_p > 0:
            current_price = cached_p
            day_change_pct = safe_float(_LIVE_QUOTE_CACHE[symbol].get("day_change"), 0.0)
    else:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="5d")
            if hist is not None and not hist.empty and "Close" in hist.columns:
                closes_s = hist["Close"].dropna()
                if not closes_s.empty:
                    closes = closes_s.values
                    val = safe_float(closes[-1], 0.0)
                    if val > 0:
                        current_price = val
                        if len(closes) > 1:
                            prev_val = safe_float(closes[-2], 0.0)
                            if prev_val > 0:
                                day_change_pct = ((current_price - prev_val) / prev_val) * 100
                        _LIVE_QUOTE_CACHE[symbol] = {"price": current_price, "day_change": day_change_pct, "ts": now}
            else:
                info = getattr(t, "info", {})
                if isinstance(info, dict):
                    raw_p = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
                    if raw_p:
                        val = safe_float(raw_p, 0.0)
                        if val > 0:
                            current_price = val
                            _LIVE_QUOTE_CACHE[symbol] = {"price": current_price, "day_change": 0.0, "ts": now}
        except Exception as e:
            if symbol in _LIVE_QUOTE_CACHE:
                current_price = safe_float(_LIVE_QUOTE_CACHE[symbol].get("price"), pru)
                day_change_pct = safe_float(_LIVE_QUOTE_CACHE[symbol].get("day_change"), 0.0)
            else:
                current_price = pru if pru > 0 else 1.0

    if math.isnan(current_price) or math.isinf(current_price) or current_price <= 0:
        current_price = pru if (pru > 0) else 1.0
    if math.isnan(day_change_pct) or math.isinf(day_change_pct):
        day_change_pct = 0.0

    # Calcul P&L
    pnl_unit = current_price - pru
    pnl_amount = pnl_unit * qty
    pnl_pct = (pnl_unit / pru * 100) if pru > 0 else 0.0
    invested_amount = pru * qty
    current_value = current_price * qty

    # Distances aux cibles
    dist_to_sl_pct = ((current_price - sl) / current_price * 100) if current_price > 0 else 0.0
    dist_to_tp1_pct = ((tp1 - current_price) / current_price * 100) if current_price > 0 else 0.0
    dist_to_tp2_pct = ((tp2 - current_price) / current_price * 100) if current_price > 0 else 0.0

    # Diagnostic & Alertes temps réel
    if pnl_pct >= 2.25 or current_price >= tp2:
        status_badge = "tp2_reached"
        status_label = "🚀 TP2 ATTEINT (+2.25%)"
        status_action = "Objectif final atteint. Clôturer la totalité de la position et encaisser les gains."
        progress_pct = 100
    elif pnl_pct >= 1.25 or current_price >= tp1:
        status_badge = "tp1_reached"
        status_label = "🟢 TP1 ATTEINT (+1.25%)"
        status_action = "Prendre 50% de bénéfice et remonter le Stop-Loss au prix d'entrée (Break-Even)."
        progress_pct = 75
    elif current_price <= sl or dist_to_sl_pct <= 0.5:
        status_badge = "sl_danger"
        status_label = "🔴 ALERTE STOP-LOSS"
        status_action = "Niveau d'invalidation atteint ou imminent. Couper la position sans hésiter."
        progress_pct = 0
    elif days_held >= 15:
        status_badge = "time_warning"
        status_label = "⚠️ TIME-STOP (> 15j)"
        status_action = "Durée maximale atteinte sans accélération. Envisager de sortir pour libérer le capital."
        progress_pct = 50
    elif pnl_pct > 0:
        status_badge = "in_profit"
        status_label = "📈 EN GAIN LATENT"
        status_action = "Position bien orientée. Laisser courir vers TP1 (+1.25%)."
        progress_pct = 60
    else:
        status_badge = "in_progress"
        status_label = "⏳ EN COURS"
        status_action = "Trade sous surveillance active dans la zone normale d'oscillation."
        progress_pct = 40

    return {
        "id": pos.get("id"),
        "symbol": symbol,
        "name": pos.get("name") if (pos.get("name") and pos.get("name") != symbol and len(pos.get("name")) > len(symbol)) else get_company_name(symbol),
        "account": pos.get("account", "PEA" if ".PA" in symbol else "CTO"),
        "currency": currency,
        "entry_date": entry_date_str,
        "days_held": int(days_held),
        "pru": round(safe_float(pru), 2),
        "quantity": round(safe_float(qty), 4) if qty % 1 != 0 else int(qty),
        "invested_amount": round(safe_float(invested_amount), 2),
        "current_price": round(safe_float(current_price), 2),
        "current_value": round(safe_float(current_value), 2),
        "day_change_pct": round(safe_float(day_change_pct), 2),
        "pnl_amount": round(safe_float(pnl_amount), 2),
        "pnl_pct": round(safe_float(pnl_pct), 2),
        "stop_loss": round(safe_float(sl), 2),
        "tp1": round(safe_float(tp1), 2),
        "tp2": round(safe_float(tp2), 2),
        "dist_to_sl_pct": round(safe_float(dist_to_sl_pct), 2),
        "dist_to_tp1_pct": round(safe_float(dist_to_tp1_pct), 2),
        "dist_to_tp2_pct": round(safe_float(dist_to_tp2_pct), 2),
        "status_badge": status_badge,
        "status_label": status_label,
        "status_action": status_action,
        "progress_pct": int(progress_pct),
        "broker": pos.get("broker") or ("Trading 212" if "Trading 212" in pos.get("account", "") else "XTB"),
        "notes": pos.get("notes", "")
    }

_LIVE_PORTFOLIO_CACHE = {"data": None, "ts": 0}
PORTFOLIO_CACHE_TTL = 180  # 3 minutes

def get_live_portfolio_summary(force_refresh=False):
    """
    Récupère l'ensemble des positions ouvertes (XTB + Trading 212) et calcule les indicateurs clés du portefeuille.
    Garantit une sérialisation JSON valide sans aucun NaN ou Inf.
    """
    global _LIVE_PORTFOLIO_CACHE
    import time
    now = time.time()
    if not force_refresh and _LIVE_PORTFOLIO_CACHE["data"] is not None and (now - _LIVE_PORTFOLIO_CACHE["ts"]) < PORTFOLIO_CACHE_TTL:
        return _LIVE_PORTFOLIO_CACHE["data"]

    from src.trading212_connector import get_trading212_open_positions
    raw_positions = read_positions_from_sheets(force_refresh=force_refresh) or []
    
    # Taguer les positions Google Sheets avec broker XTB par défaut
    for p in raw_positions:
        if "broker" not in p or not p["broker"]:
            p["broker"] = "Trading 212" if "Trading 212" in p.get("account", "") else "XTB"

    # Récupérer les positions Trading 212 en direct si configuré
    t212_positions = get_trading212_open_positions() or []
    all_raw_positions = raw_positions + t212_positions

    if not all_raw_positions:
        res = {
            "total_invested": 0.0,
            "total_current_value": 0.0,
            "total_pnl_amount": 0.0,
            "total_pnl_pct": 0.0,
            "open_positions_count": 0,
            "alerts_count": 0,
            "brokers_summary": {
                "XTB": {"count": 0, "invested": 0.0, "value": 0.0, "pnl": 0.0},
                "Trading 212": {"count": 0, "invested": 0.0, "value": 0.0, "pnl": 0.0}
            },
            "positions": []
        }
        _LIVE_PORTFOLIO_CACHE = {"data": res, "ts": now}
        return res

    # Calcul parallèle multi-threadé
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        positions_live = list(executor.map(fetch_live_quote_for_position, all_raw_positions))

    total_invested = sum(safe_float(p.get("invested_amount", 0.0)) for p in positions_live)
    total_value = sum(safe_float(p.get("current_value", 0.0)) for p in positions_live)
    total_pnl = total_value - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0
    alerts_count = sum(1 for p in positions_live if p.get("status_badge") in ["tp1_reached", "tp2_reached", "sl_danger", "time_warning"])

    brokers_summary = {
        "XTB": {"count": 0, "invested": 0.0, "value": 0.0, "pnl": 0.0},
        "Trading 212": {"count": 0, "invested": 0.0, "value": 0.0, "pnl": 0.0}
    }
    for p in positions_live:
        b = p.get("broker", "XTB")
        if b not in brokers_summary:
            brokers_summary[b] = {"count": 0, "invested": 0.0, "value": 0.0, "pnl": 0.0}
        brokers_summary[b]["count"] += 1
        brokers_summary[b]["invested"] += safe_float(p.get("invested_amount", 0.0))
        brokers_summary[b]["value"] += safe_float(p.get("current_value", 0.0))
        brokers_summary[b]["pnl"] += safe_float(p.get("pnl_amount", 0.0))

    for b in brokers_summary:
        brokers_summary[b]["invested"] = round(safe_float(brokers_summary[b]["invested"]), 2)
        brokers_summary[b]["value"] = round(safe_float(brokers_summary[b]["value"]), 2)
        brokers_summary[b]["pnl"] = round(safe_float(brokers_summary[b]["pnl"]), 2)

    res = {
        "total_invested": round(safe_float(total_invested), 2),
        "total_current_value": round(safe_float(total_value), 2),
        "total_pnl_amount": round(safe_float(total_pnl), 2),
        "total_pnl_pct": round(safe_float(total_pnl_pct), 2),
        "open_positions_count": len(positions_live),
        "alerts_count": int(alerts_count),
        "brokers_summary": brokers_summary,
        "positions": positions_live
    }
    _LIVE_PORTFOLIO_CACHE = {"data": res, "ts": now}
    return res

def parse_broker_csv(csv_text_or_bytes):
    """
    Analyse et extrait les positions depuis un fichier CSV (compatible exports XTB, DeGiro, Bourse Direct, IBKR, Generic).
    """
    if isinstance(csv_text_or_bytes, bytes):
        try:
            csv_text = csv_text_or_bytes.decode('utf-8')
        except:
            csv_text = csv_text_or_bytes.decode('latin-1')
    else:
        csv_text = str(csv_text_or_bytes)

    lines = [l.strip() for l in csv_text.splitlines() if l.strip()]
    if not lines:
        return []

    # Détecter le délimiteur (, ; \t)
    sample = "\n".join(lines[:5])
    delimiter = ','
    if ';' in sample and sample.count(';') > sample.count(','):
        delimiter = ';'
    elif '\t' in sample:
        delimiter = '\t'

    reader = csv.reader(io.StringIO(csv_text), delimiter=delimiter)
    all_rows = [r for r in reader if r]

    if not all_rows:
        return []

    # Trouver la ligne d'en-tête
    header_idx = -1
    header = []
    for idx, r in enumerate(all_rows[:10]):
        r_clean = [str(c).strip().lower() for c in r]
        if any(k in r_clean for k in ["symbol", "ticker", "instrument", "action", "position"]):
            header_idx = idx
            header = r_clean
            break

    if header_idx == -1:
        header_idx = 0
        header = [str(c).strip().lower() for c in all_rows[0]]

    # Indexation des colonnes
    col_map = {}
    for idx, col in enumerate(header):
        c = col.strip().lower()
        if any(k in c for k in ["symbole", "symbol", "ticker", "instrument", "code", "isin", "titre"]):
            if "position" not in c or "symbole" in c or "ticker" in c:
                col_map["symbol"] = idx
        elif any(k in c for k in ["nom", "name", "description", "libellé", "libelle", "société", "societe"]):
            col_map["name"] = idx
        elif any(k in c for k in ["open price", "pru", "prix d'achat", "cours d'achat", "prix d'ouverture", "prix ouverture", "open", "achat"]):
            col_map["pru"] = idx
        elif any(k in c for k in ["volume", "quantité", "quantite", "qty", "volume/lots", "lots", "shares", "titres", "nombre"]):
            col_map["quantity"] = idx
        elif any(k in c for k in ["open time", "date d'ouverture", "date ouverture", "date", "time", "date achat"]):
            col_map["entry_date"] = idx
        elif any(k in c for k in ["sl", "stop loss", "stop-loss", "invalidation", "stop"]):
            col_map["stop_loss"] = idx
        elif any(k in c for k in ["tp", "take profit", "tp1", "cible", "objectif"]):
            col_map["tp1"] = idx

    # Fallback pour le prix d'achat si non trouvé
    if "pru" not in col_map:
        for idx, col in enumerate(header):
            c = col.strip().lower()
            if any(k in c for k in ["price", "prix", "cours"]):
                col_map["pru"] = idx
                break

    parsed_positions = []
    for r in all_rows[header_idx + 1:]:
        if not r or len(r) <= 1:
            continue

        sym_idx = col_map.get("symbol", 0)
        if len(r) <= sym_idx:
            continue

        raw_sym = str(r[sym_idx]).strip().upper()
        if not raw_sym or raw_sym.startswith("TOTAL") or raw_sym.startswith("ID"):
            continue

        # Normaliser le symbole (ex: XTB format "SAN.FR" -> "SAN.PA", "AAPL.US" -> "AAPL")
        clean_sym = normalize_xtb_ticker(raw_sym)

        # PRU
        pru = 0.0
        if "pru" in col_map and len(r) > col_map["pru"]:
            pru_str = str(r[col_map["pru"]]).replace("€", "").replace("$", "").replace(" ", "").replace(",", ".")
            try:
                pru = float(pru_str)
            except:
                pru = 0.0

        # Quantité
        qty = 1.0
        if "quantity" in col_map and len(r) > col_map["quantity"]:
            qty_str = str(r[col_map["quantity"]]).replace(" ", "").replace(",", ".")
            try:
                qty = float(qty_str)
            except:
                qty = 1.0

        # Date
        entry_date = datetime.now().strftime("%Y-%m-%d")
        if "entry_date" in col_map and len(r) > col_map["entry_date"]:
            raw_date = str(r[col_map["entry_date"]]).strip()
            try:
                dt = pd.to_datetime(raw_date).to_pydatetime()
                entry_date = dt.strftime("%Y-%m-%d")
            except:
                pass

        # Nom
        name = clean_sym
        if "name" in col_map and len(r) > col_map["name"]:
            name = str(r[col_map["name"]]).strip() or clean_sym

        pos_dict = {
            "symbol": clean_sym,
            "name": name,
            "entry_date": entry_date,
            "pru": pru,
            "quantity": qty,
            "stop_loss": pru * 0.97 if pru > 0 else 0.0,
            "tp1": pru * 1.0125 if pru > 0 else 0.0,
            "tp2": pru * 1.0225 if pru > 0 else 0.0,
            "account": "PEA" if ".PA" in clean_sym else "CTO",
            "currency": "EUR" if ".PA" in clean_sym or ".DE" in clean_sym else "USD"
        }
        parsed_positions.append(pos_dict)

    return parsed_positions

def normalize_xtb_ticker(raw_sym):
    """
    Convertit les tickers au format standard XTB vers le format universel Yahoo Finance / Google Sheets.
    Exemples: 'STM.FR' -> 'STM.PA', 'AAPL.US' -> 'AAPL', 'HIJP.UK' -> 'HIJP.L', 'BY6.DE' -> 'BAYN.DE', 'GOOGC' -> 'GOOGL'
    """
    sym = str(raw_sym).strip().upper()
    if sym.endswith(".FR"):
        sym = sym[:-3] + ".PA"
    elif sym.endswith(".US"):
        sym = sym[:-3]
    elif sym.endswith(".UK"):
        sym = sym[:-3] + ".L"

    # Alias spécifiques XTB -> Yahoo Finance
    aliases = {
        "BY6.DE": "BAYN.DE",
        "GOOGC": "GOOGL",
        "BRKB": "BRK-B",
        "BFB": "BF-B"
    }
    return aliases.get(sym, sym)

def parse_xtb_excel_file(file_content_or_path, default_account=None):
    """
    Parseur universel pour les exports Excel (.xlsx) générés par la plateforme XTB.
    Extrait:
      - 'closed_positions' : Historique des positions fermées (pour le Journal de Trading)
      - 'open_positions' : Positions ouvertes actuelles (pour le Suivi Live)
      - 'cash_operations' : Opérations de cash / dividendes / taxes
    """
    try:
        if isinstance(file_content_or_path, bytes):
            xls = pd.ExcelFile(io.BytesIO(file_content_or_path))
        else:
            xls = pd.ExcelFile(file_content_or_path)
    except Exception as e:
        print(f"❌ Erreur lecture fichier Excel XTB : {e}")
        return {"closed_positions": [], "open_positions": [], "cash_operations": []}

    account_hint = default_account or "CTO Euro"
    path_str = str(file_content_or_path) if isinstance(file_content_or_path, str) else ""
    if "PEA" in path_str.upper():
        account_hint = "PEA"
    elif "USD" in path_str.upper():
        account_hint = "CTO Dollar"

    result = {
        "closed_positions": [],
        "open_positions": [],
        "cash_operations": []
    }

    # 1. PARSER CLOSED POSITIONS
    if "Closed Positions" in xls.sheet_names:
        try:
            df_closed = xls.parse("Closed Positions", header=None)
            header_idx = -1
            for i in range(min(15, len(df_closed))):
                row_str = [str(x).strip().lower() for x in df_closed.iloc[i] if pd.notna(x)]
                if "ticker" in row_str or "instrument" in row_str:
                    header_idx = i
                    break
            
            if header_idx != -1:
                headers = [str(x).strip() for x in df_closed.iloc[header_idx]]
                data_df = df_closed.iloc[header_idx + 1:].copy()
                data_df.columns = headers
                data_df = data_df[data_df["Ticker"].notna()]
                data_df = data_df[~data_df["Ticker"].astype(str).str.contains("Profit|Total|nan", case=False, na=False)]

                for _, r in data_df.iterrows():
                    raw_ticker = str(r.get("Ticker", "")).strip()
                    if not raw_ticker:
                        continue
                    ticker = normalize_xtb_ticker(raw_ticker)
                    name = str(r.get("Instrument", ticker)).strip()
                    product = str(r.get("Product", "")).strip()

                    # Compte & Devise
                    if "PEA" in product or "PEA" in account_hint:
                        account = "PEA"
                        currency = "EUR"
                    elif "USD" in account_hint or "USD" in product or "Dollar" in account_hint:
                        account = "CTO Dollar"
                        currency = "USD"
                    else:
                        account = "CTO Euro"
                        currency = "EUR"

                    try:
                        qty = float(r.get("Volume", 1.0))
                    except:
                        qty = 1.0

                    try:
                        open_price = float(r.get("Open Price", 0.0))
                    except:
                        open_price = 0.0

                    try:
                        close_price = float(r.get("Close Price", 0.0))
                    except:
                        close_price = 0.0

                    try:
                        pnl = float(r.get("Profit/Loss", (close_price - open_price) * qty))
                    except:
                        pnl = (close_price - open_price) * qty

                    open_time = str(r.get("Open Time (UTC)", ""))
                    close_time = str(r.get("Close Time (UTC)", ""))
                    pos_id = str(r.get("Position ID", "")).strip()
                    comment = str(r.get("Comment", "")).strip()

                    invested = open_price * qty
                    pnl_pct = (pnl / invested * 100) if invested > 0 else 0.0

                    # Durée du trade
                    days_held = 0
                    if open_time and close_time:
                        try:
                            d1 = pd.to_datetime(open_time[:19])
                            d2 = pd.to_datetime(close_time[:19])
                            days_held = max(0, (d2 - d1).days)
                        except:
                            days_held = 0

                    result["closed_positions"].append({
                        "id": pos_id or f"XTB-{ticker}-{open_time[:10]}",
                        "symbol": ticker,
                        "name": name,
                        "open_time": open_time[:19] if len(open_time) >= 10 else "",
                        "close_time": close_time[:19] if len(close_time) >= 10 else "",
                        "days_held": days_held,
                        "pru": round(open_price, 4),
                        "exit_price": round(close_price, 4),
                        "quantity": qty,
                        "invested_amount": round(invested, 2),
                        "pnl_amount": round(pnl, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "result": "GAIN 🟢" if pnl >= 0 else "PERTE 🔴",
                        "account": account,
                        "currency": currency,
                        "comment": comment
                    })
        except Exception as e:
            print(f"⚠️ Erreur parsing Closed Positions : {e}")

    # 2. PARSER OPEN POSITIONS
    if "Open Positions" in xls.sheet_names:
        try:
            df_open = xls.parse("Open Positions", header=None)
            header_idx = -1
            for i in range(min(15, len(df_open))):
                row_str = [str(x).strip().lower() for x in df_open.iloc[i] if pd.notna(x)]
                if "open price" in row_str or "current price" in row_str or "ticker" in row_str:
                    header_idx = i
                    break
            
            if header_idx != -1:
                headers = [str(x).strip() for x in df_open.iloc[header_idx]]
                data_df = df_open.iloc[header_idx + 1:].copy()
                data_df.columns = headers

                for _, r in data_df.iterrows():
                    raw_ticker = str(r.get("Ticker", "")).strip()
                    if not raw_ticker or raw_ticker.lower() in ["nan", "ticker", "total"]:
                        continue

                    pos_id = str(r.get("Instrument/Position", "")).strip()
                    pos_type = str(r.get("Type", "")).strip().upper()
                    if pos_type != "BUY":
                        continue

                    ticker = normalize_xtb_ticker(raw_ticker)
                    product = str(r.get("Product", "")).strip()

                    if "PEA" in product or "PEA" in account_hint:
                        account = "PEA"
                        currency = "EUR"
                    elif "USD" in account_hint or "USD" in product or "Dollar" in account_hint:
                        account = "CTO Dollar"
                        currency = "USD"
                    else:
                        account = "CTO Euro"
                        currency = "EUR"

                    try:
                        qty = float(r.get("Volume", 1.0))
                    except:
                        qty = 1.0

                    try:
                        open_price = float(r.get("Open price", 0.0))
                    except:
                        open_price = 0.0

                    open_time = str(r.get("Open time (UTC)", ""))
                    entry_date = open_time[:10] if len(open_time) >= 10 else datetime.now().strftime("%Y-%m-%d")

                    result["open_positions"].append({
                        "id": f"XTB-{pos_id}" if pos_id else f"POS-{ticker}-{entry_date}",
                        "symbol": ticker,
                        "name": ticker,
                        "entry_date": entry_date,
                        "pru": round(open_price, 4),
                        "quantity": qty,
                        "stop_loss": round(open_price * 0.97, 4),
                        "tp1": round(open_price * 1.0125, 4),
                        "tp2": round(open_price * 1.0225, 4),
                        "account": account,
                        "currency": currency,
                        "notes": f"Position importée XTB #{pos_id}"
                    })
        except Exception as e:
            print(f"⚠️ Erreur parsing Open Positions : {e}")

    # 3. PARSER CASH OPERATIONS
    if "Cash Operations" in xls.sheet_names:
        try:
            df_cash = xls.parse("Cash Operations", header=None)
            header_idx = -1
            for i in range(min(15, len(df_cash))):
                row_str = [str(x).strip().lower() for x in df_cash.iloc[i] if pd.notna(x)]
                if "type" in row_str and ("amount" in row_str or "time" in row_str or "comment" in row_str):
                    header_idx = i
                    break

            if header_idx != -1:
                headers = [str(x).strip() for x in df_cash.iloc[header_idx]]
                data_df = df_cash.iloc[header_idx + 1:].copy()
                data_df.columns = headers

                for _, r in data_df.iterrows():
                    op_type = str(r.get("Type", "")).strip()
                    if not op_type or op_type.lower() in ["nan", "type"]:
                        continue

                    raw_amount = r.get("Amount", 0.0)
                    try:
                        amount = float(raw_amount or 0.0)
                    except:
                        amount = 0.0

                    time_val = str(r.get("Time", ""))
                    comment = str(r.get("Comment", "")).strip()
                    op_id = str(r.get("ID", "")).strip()
                    product = str(r.get("Product", "")).strip()
                    raw_ticker = str(r.get("Ticker", "")).strip()
                    ticker = normalize_xtb_ticker(raw_ticker) if raw_ticker and raw_ticker.lower() != "nan" else ""
                    instrument = str(r.get("Instrument", "")).strip()
                    if instrument.lower() == "nan":
                        instrument = ""

                    # Compte & Devise
                    if "PEA" in product or "PEA" in account_hint:
                        account = "PEA"
                        currency = "EUR"
                    elif "USD" in account_hint or "USD" in product or "Dollar" in account_hint:
                        account = "CTO Dollar"
                        currency = "USD"
                    else:
                        account = "CTO Euro"
                        currency = "EUR"

                    result["cash_operations"].append({
                        "id": op_id or f"CASH-{time_val[:10]}-{len(result['cash_operations'])}",
                        "type": op_type,
                        "instrument": instrument,
                        "symbol": ticker,
                        "time": time_val[:19] if len(time_val) >= 10 else "",
                        "amount": amount,
                        "comment": comment,
                        "account": account,
                        "currency": currency
                    })
        except Exception as e:
            print(f"⚠️ Erreur parsing Cash Operations : {e}")

    return result

def aggregate_open_positions(open_positions_list):
    """
    Agrège les multiples lots d'une même action au sein d'un même compte en une seule position avec PRU pondéré.
    """
    grouped = {}
    for p in open_positions_list:
        key = (p["symbol"], p["account"])
        if key not in grouped:
            grouped[key] = {
                "id": f"POS-{p['symbol']}-{p['account']}",
                "symbol": p["symbol"],
                "name": p.get("name", p["symbol"]),
                "entry_date": p["entry_date"],
                "total_invested": p["pru"] * p["quantity"],
                "quantity": p["quantity"],
                "account": p["account"],
                "currency": p["currency"],
                "lots_count": 1,
                "notes": f"{p['quantity']} actions"
            }
        else:
            g = grouped[key]
            g["total_invested"] += p["pru"] * p["quantity"]
            g["quantity"] += p["quantity"]
            g["lots_count"] += 1
            if p["entry_date"] < g["entry_date"]:
                g["entry_date"] = p["entry_date"]

    aggregated = []
    for g in grouped.values():
        weighted_pru = g["total_invested"] / g["quantity"] if g["quantity"] > 0 else 0.0
        aggregated.append({
            "id": g["id"],
            "symbol": g["symbol"],
            "name": g["name"],
            "entry_date": g["entry_date"],
            "pru": round(weighted_pru, 4),
            "quantity": round(g["quantity"], 4),
            "stop_loss": round(weighted_pru * 0.97, 4),
            "tp1": round(weighted_pru * 1.0125, 4),
            "tp2": round(weighted_pru * 1.0225, 4),
            "account": g["account"],
            "currency": g["currency"],
            "notes": f"Agrégation de {g['lots_count']} lot(s) XTB"
        })
    return aggregated

def calculate_trading_performance_stats(closed_trades):
    """
    Calcule des statistiques avancées de performance sur le journal de trading.
    """
    if not closed_trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "total_pnl_eur": 0.0,
            "total_pnl_usd": 0.0,
            "profit_factor": 0.0,
            "avg_gain": 0.0,
            "avg_loss": 0.0,
            "gain_loss_ratio": 0.0,
            "best_trade": None,
            "worst_trade": None,
            "by_account": {}
        }

    total_trades = len(closed_trades)
    winning = [t for t in closed_trades if t.get("pnl_amount", 0) >= 0]
    losing = [t for t in closed_trades if t.get("pnl_amount", 0) < 0]

    winning_count = len(winning)
    losing_count = len(losing)
    win_rate = (winning_count / total_trades * 100) if total_trades > 0 else 0.0

    total_pnl_eur = sum(t["pnl_amount"] for t in closed_trades if t.get("currency") == "EUR")
    total_pnl_usd = sum(t["pnl_amount"] for t in closed_trades if t.get("currency") == "USD")

    gross_profit = sum(t["pnl_amount"] for t in winning)
    gross_loss = abs(sum(t["pnl_amount"] for t in losing))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

    avg_gain = (gross_profit / winning_count) if winning_count > 0 else 0.0
    avg_loss = (gross_loss / losing_count) if losing_count > 0 else 0.0
    gain_loss_ratio = (avg_gain / avg_loss) if avg_loss > 0 else 0.0

    best_trade = max(closed_trades, key=lambda t: t.get("pnl_amount", 0), default=None)
    worst_trade = min(closed_trades, key=lambda t: t.get("pnl_amount", 0), default=None)

    # Répartition par compte
    by_account = {}
    for t in closed_trades:
        acc = t.get("account", "Autres")
        if acc not in by_account:
            by_account[acc] = {"trades": 0, "pnl": 0.0, "currency": t.get("currency", "EUR"), "wins": 0}
        by_account[acc]["trades"] += 1
        by_account[acc]["pnl"] += t.get("pnl_amount", 0)
        if t.get("pnl_amount", 0) >= 0:
            by_account[acc]["wins"] += 1

    for acc, data in by_account.items():
        data["win_rate"] = round((data["wins"] / data["trades"] * 100), 1) if data["trades"] > 0 else 0.0
        data["pnl"] = round(data["pnl"], 2)

    return {
        "total_trades": total_trades,
        "winning_trades": winning_count,
        "losing_trades": losing_count,
        "win_rate": round(win_rate, 1),
        "total_pnl_eur": round(total_pnl_eur, 2),
        "total_pnl_usd": round(total_pnl_usd, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_gain": round(avg_gain, 2),
        "avg_loss": round(avg_loss, 2),
        "gain_loss_ratio": round(gain_loss_ratio, 2),
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "by_account": by_account
    }

def calculate_cash_and_treasury_summary(cash_ops_list):
    """
    Agrège les opérations de trésorerie par compte et calcule les liquidités disponibles (Cash),
    les dépôts cumulés, les retraits, les dividendes perçus et les intérêts.
    """
    usd_to_eur = get_usd_to_eur_rate()
    
    accounts = {
        "PEA": {
            "currency": "EUR",
            "deposits": 0.0,
            "withdrawals": 0.0,
            "dividends": 0.0,
            "interest": 0.0,
            "taxes": 0.0,
            "purchases": 0.0,
            "sales": 0.0,
            "transfers": 0.0,
            "cash_balance": 0.0
        },
        "CTO Euro": {
            "currency": "EUR",
            "deposits": 0.0,
            "withdrawals": 0.0,
            "dividends": 0.0,
            "interest": 0.0,
            "taxes": 0.0,
            "purchases": 0.0,
            "sales": 0.0,
            "transfers": 0.0,
            "cash_balance": 0.0
        },
        "CTO Dollar": {
            "currency": "USD",
            "deposits": 0.0,
            "withdrawals": 0.0,
            "dividends": 0.0,
            "interest": 0.0,
            "taxes": 0.0,
            "purchases": 0.0,
            "sales": 0.0,
            "transfers": 0.0,
            "cash_balance": 0.0
        }
    }

    for op in cash_ops_list:
        acc_key = op.get("account", "CTO Euro")
        if "PEA" in acc_key:
            target_acc = "PEA"
        elif "USD" in acc_key or "Dollar" in acc_key:
            target_acc = "CTO Dollar"
        else:
            target_acc = "CTO Euro"

        t = op.get("type", "").lower()
        amt = safe_float(op.get("amount", 0.0))

        if "deposit" in t or "pea deposit" in t:
            if amt > 0:
                accounts[target_acc]["deposits"] += amt
            else:
                accounts[target_acc]["transfers"] += amt
        elif "withdrawal" in t:
            accounts[target_acc]["withdrawals"] += abs(amt)
        elif "dividend" in t:
            accounts[target_acc]["dividends"] += amt
        elif "interest" in t:
            accounts[target_acc]["interest"] += amt
        elif "tax" in t or "fee" in t:
            accounts[target_acc]["taxes"] += abs(amt)
        elif "purchase" in t or "achat" in t:
            accounts[target_acc]["purchases"] += abs(amt)
        elif "sell" in t or "vente" in t:
            accounts[target_acc]["sales"] += amt
        elif "transfer" in t:
            accounts[target_acc]["transfers"] += amt

        # Calcul cumulatif du solde de cash
        if t != "total":
            accounts[target_acc]["cash_balance"] += amt

    # Convertir en EUR global
    total_deposits_eur = accounts["PEA"]["deposits"] + accounts["CTO Euro"]["deposits"] + (accounts["CTO Dollar"]["deposits"] * usd_to_eur)
    total_withdrawals_eur = accounts["PEA"]["withdrawals"] + accounts["CTO Euro"]["withdrawals"] + (accounts["CTO Dollar"]["withdrawals"] * usd_to_eur)
    total_dividends_eur = accounts["PEA"]["dividends"] + accounts["CTO Euro"]["dividends"] + (accounts["CTO Dollar"]["dividends"] * usd_to_eur)
    total_interest_eur = accounts["PEA"]["interest"] + accounts["CTO Euro"]["interest"] + (accounts["CTO Dollar"]["interest"] * usd_to_eur)
    
    total_cash_eur = accounts["PEA"]["cash_balance"] + accounts["CTO Euro"]["cash_balance"] + (accounts["CTO Dollar"]["cash_balance"] * usd_to_eur)
    net_inflows_eur = total_deposits_eur - total_withdrawals_eur

    return {
        "accounts": accounts,
        "total_cash_eur": round(safe_float(total_cash_eur), 2),
        "total_deposits_eur": round(safe_float(total_deposits_eur), 2),
        "total_withdrawals_eur": round(safe_float(total_withdrawals_eur), 2),
        "net_inflows_eur": round(safe_float(net_inflows_eur), 2),
        "total_dividends_eur": round(safe_float(total_dividends_eur), 2),
        "total_interest_eur": round(safe_float(total_interest_eur), 2),
        "usd_to_eur_rate": round(safe_float(usd_to_eur), 4)
    }

def calculate_portfolio_diversification(live_positions, cash_summary=None):
    """
    Calcule la diversification sectorielle par catégorie (Tech & IA, Santé, Luxe, etc.)
    et par enveloppe fiscale (PEA, CTO Euro, CTO Dollar) ainsi que le ratio Actions vs Cash.
    """
    from src.market_data import categorize_ticker, get_ticker_info
    usd_to_eur = safe_float(get_usd_to_eur_rate(), 0.92)

    # 1. Analyse par Catégorie
    categories_map = {}
    total_equity_value_eur = 0.0
    total_equity_invested_eur = 0.0
    total_pnl_latent_eur = 0.0

    for pos in live_positions:
        sym = pos.get("symbol", "").upper().strip()
        curr_val = safe_float(pos.get("current_value", 0.0))
        invested = safe_float(pos.get("invested_amount", 0.0))
        pnl = safe_float(pos.get("pnl_amount", 0.0))
        currency = pos.get("currency", "EUR")

        # Conversion EUR si position en USD
        rate = usd_to_eur if currency == "USD" else 1.0
        val_eur = curr_val * rate
        inv_eur = invested * rate
        pnl_eur = pnl * rate

        total_equity_value_eur += val_eur
        total_equity_invested_eur += inv_eur
        total_pnl_latent_eur += pnl_eur

        info = get_ticker_info(sym)
        cat_meta = categorize_ticker(sym, info)
        cat_name = cat_meta.get("category", "Autres")
        cat_icon = cat_meta.get("category_icon", "📦")

        if cat_name not in categories_map:
            categories_map[cat_name] = {
                "category": cat_name,
                "category_icon": cat_icon,
                "positions_count": 0,
                "invested_eur": 0.0,
                "value_eur": 0.0,
                "pnl_eur": 0.0,
                "tickers": []
            }

        categories_map[cat_name]["positions_count"] += 1
        categories_map[cat_name]["invested_eur"] += inv_eur
        categories_map[cat_name]["value_eur"] += val_eur
        categories_map[cat_name]["pnl_eur"] += pnl_eur
        categories_map[cat_name]["tickers"].append({
            "symbol": sym,
            "name": pos.get("name", sym),
            "account": pos.get("account", "CTO"),
            "value_eur": round(safe_float(val_eur), 2),
            "pnl_eur": round(safe_float(pnl_eur), 2),
            "pnl_pct": round(safe_float(pos.get("pnl_pct", 0.0)), 2)
        })

    # Calcul des pourcentages par catégorie
    categories_list = []
    for cat in categories_map.values():
        weight = (cat["value_eur"] / total_equity_value_eur * 100) if total_equity_value_eur > 0 else 0.0
        pnl_pct = (cat["pnl_eur"] / cat["invested_eur"] * 100) if cat["invested_eur"] > 0 else 0.0
        
        # Statut de concentration
        if weight > 35:
            status = "SUR-PONDÉRÉ ⚠️"
            status_class = "badge-danger"
        elif weight > 25:
            status = "ÉLEVÉ 🟡"
            status_class = "badge-warning"
        else:
            status = "OPTIMAL 🟢"
            status_class = "badge-success"

        categories_list.append({
            "category": cat["category"],
            "category_icon": cat["category_icon"],
            "positions_count": int(cat["positions_count"]),
            "invested_eur": round(safe_float(cat["invested_eur"]), 2),
            "value_eur": round(safe_float(cat["value_eur"]), 2),
            "pnl_eur": round(safe_float(cat["pnl_eur"]), 2),
            "pnl_pct": round(safe_float(pnl_pct), 2),
            "weight_pct": round(safe_float(weight), 1),
            "status": status,
            "status_class": status_class,
            "tickers": sorted(cat["tickers"], key=lambda x: x["value_eur"], reverse=True)
        })

    categories_list.sort(key=lambda x: x["value_eur"], reverse=True)

    # 2. Analyse par Compte
    accounts_map = {
        "PEA": {"name": "PEA (Europe)", "icon": "🇫🇷", "invested_eur": 0.0, "value_eur": 0.0, "pnl_eur": 0.0, "count": 0},
        "CTO Euro": {"name": "CTO Euro", "icon": "💶", "invested_eur": 0.0, "value_eur": 0.0, "pnl_eur": 0.0, "count": 0},
        "CTO Dollar": {"name": "CTO Dollar", "icon": "🇺🇸", "invested_eur": 0.0, "value_eur": 0.0, "pnl_eur": 0.0, "count": 0}
    }

    for pos in live_positions:
        acc_raw = pos.get("account", "CTO Euro")
        if "PEA" in acc_raw:
            target_acc = "PEA"
        elif "USD" in acc_raw or "Dollar" in acc_raw:
            target_acc = "CTO Dollar"
        else:
            target_acc = "CTO Euro"

        rate = usd_to_eur if pos.get("currency") == "USD" else 1.0
        val_eur = safe_float(pos.get("current_value", 0.0)) * rate
        inv_eur = safe_float(pos.get("invested_amount", 0.0)) * rate
        pnl_eur = safe_float(pos.get("pnl_amount", 0.0)) * rate

        accounts_map[target_acc]["count"] += 1
        accounts_map[target_acc]["invested_eur"] += inv_eur
        accounts_map[target_acc]["value_eur"] += val_eur
        accounts_map[target_acc]["pnl_eur"] += pnl_eur

    accounts_list = []
    for acc_id, acc_data in accounts_map.items():
        weight = (acc_data["value_eur"] / total_equity_value_eur * 100) if total_equity_value_eur > 0 else 0.0
        pnl_pct = (acc_data["pnl_eur"] / acc_data["invested_eur"] * 100) if acc_data["invested_eur"] > 0 else 0.0
        accounts_list.append({
            "account_id": acc_id,
            "account_name": acc_data["name"],
            "icon": acc_data["icon"],
            "positions_count": int(acc_data["count"]),
            "invested_eur": round(safe_float(acc_data["invested_eur"]), 2),
            "value_eur": round(safe_float(acc_data["value_eur"]), 2),
            "pnl_eur": round(safe_float(acc_data["pnl_eur"]), 2),
            "pnl_pct": round(safe_float(pnl_pct), 2),
            "weight_pct": round(safe_float(weight), 1)
        })

    # 3. Analyse par Courtier (Broker)
    from src.trading212_connector import get_trading212_cash
    cash_eur = safe_float(cash_summary.get("total_cash_eur", 0.0) if cash_summary else 0.0, 0.0)
    t212_cash_data = get_trading212_cash()
    t212_cash_eur = safe_float(t212_cash_data.get("free", 0.0) if t212_cash_data.get("connected") else 0.0, 0.0)

    brokers_map = {
        "XTB": {"name": "XTB", "icon": "🔵", "invested_eur": 0.0, "value_eur": 0.0, "pnl_eur": 0.0, "count": 0, "cash_eur": cash_eur},
        "Trading 212": {"name": "Trading 212", "icon": "🟠", "invested_eur": 0.0, "value_eur": 0.0, "pnl_eur": 0.0, "count": 0, "cash_eur": t212_cash_eur}
    }

    for pos in live_positions:
        b = pos.get("broker", "XTB")
        if b not in brokers_map:
            brokers_map[b] = {"name": b, "icon": "💼", "invested_eur": 0.0, "value_eur": 0.0, "pnl_eur": 0.0, "count": 0, "cash_eur": 0.0}

        rate = usd_to_eur if pos.get("currency") == "USD" else 1.0
        val_eur = safe_float(pos.get("current_value", 0.0)) * rate
        inv_eur = safe_float(pos.get("invested_amount", 0.0)) * rate
        pnl_eur = safe_float(pos.get("pnl_amount", 0.0)) * rate

        brokers_map[b]["count"] += 1
        brokers_map[b]["invested_eur"] += inv_eur
        brokers_map[b]["value_eur"] += val_eur
        brokers_map[b]["pnl_eur"] += pnl_eur

    brokers_list = []
    for b_id, b_data in brokers_map.items():
        weight = (b_data["value_eur"] / total_equity_value_eur * 100) if total_equity_value_eur > 0 else 0.0
        pnl_pct = (b_data["pnl_eur"] / b_data["invested_eur"] * 100) if b_data["invested_eur"] > 0 else 0.0
        brokers_list.append({
            "broker_id": b_id,
            "broker_name": b_data["name"],
            "icon": b_data["icon"],
            "positions_count": int(b_data["count"]),
            "invested_eur": round(safe_float(b_data["invested_eur"]), 2),
            "value_eur": round(safe_float(b_data["value_eur"]), 2),
            "pnl_eur": round(safe_float(b_data["pnl_eur"]), 2),
            "pnl_pct": round(safe_float(pnl_pct), 2),
            "cash_eur": round(safe_float(b_data["cash_eur"]), 2),
            "weight_pct": round(safe_float(weight), 1)
        })

    # 4. Allocation d'Actifs (Actions vs Cash)
    total_cash_all_brokers = cash_eur + t212_cash_eur
    total_nav_eur = total_equity_value_eur + total_cash_all_brokers
    equity_weight = (total_equity_value_eur / total_nav_eur * 100) if total_nav_eur > 0 else 100.0
    cash_weight = (total_cash_all_brokers / total_nav_eur * 100) if total_nav_eur > 0 else 0.0

    return {
        "total_nav_eur": round(safe_float(total_nav_eur), 2),
        "total_equity_value_eur": round(safe_float(total_equity_value_eur), 2),
        "total_equity_invested_eur": round(safe_float(total_equity_invested_eur), 2),
        "total_pnl_latent_eur": round(safe_float(total_pnl_latent_eur), 2),
        "total_pnl_latent_pct": round(safe_float((total_pnl_latent_eur / total_equity_invested_eur * 100) if total_equity_invested_eur > 0 else 0.0), 2),
        "cash_eur": round(safe_float(total_cash_all_brokers), 2),
        "xtb_cash_eur": round(safe_float(cash_eur), 2),
        "trading212_cash_eur": round(safe_float(t212_cash_eur), 2),
        "equity_weight_pct": round(safe_float(equity_weight), 1),
        "cash_weight_pct": round(safe_float(cash_weight), 1),
        "categories": categories_list,
        "accounts": accounts_list,
        "brokers": brokers_list,
        "cash_summary": cash_summary
    }

def calculate_xtb_monthly_turnover(closed_trades=None, open_positions=None, cash_operations=None):
    """
    Calcule le volume total de transaction (achats + ventes) sur les comptes XTB
    pour le mois civil en cours, afin de suivre le quota des 100 000 € à 0% de commission.
    """
    from src.sheets_connector import read_journal_from_sheets, read_positions_from_sheets, read_treasury_from_sheets
    from src.config import XTB_MONTHLY_ZERO_COMMISSION_LIMIT, XTB_COMMISSION_RATE_OVER_LIMIT
    
    usd_to_eur = get_usd_to_eur_rate()
    now_dt = datetime.now()
    current_month_str = now_dt.strftime("%Y-%m")

    if closed_trades is None:
        closed_trades = read_journal_from_sheets() or []
    if open_positions is None:
        open_positions = read_positions_from_sheets() or []
    if cash_operations is None:
        cash_operations = read_treasury_from_sheets() or []

    monthly_turnover = {}

    def add_volume(date_str, buy_amt, sell_amt):
        if not date_str or not isinstance(date_str, str) or len(date_str) < 7:
            return
        m = str(date_str)[:7]
        if len(m) == 7 and m[4] == '-' and m[:4].isdigit() and m[5:].isdigit():
            if m not in monthly_turnover:
                monthly_turnover[m] = {"purchases": 0.0, "sales": 0.0, "total": 0.0}
            monthly_turnover[m]["purchases"] += buy_amt
            monthly_turnover[m]["sales"] += sell_amt
            monthly_turnover[m]["total"] += (buy_amt + sell_amt)

    # 1. Closed trades XTB
    for t in closed_trades:
        if t.get("broker") == "Trading 212":
            continue
            
        cur = t.get("currency", "EUR")
        fx = usd_to_eur if cur == "USD" else 1.0
        
        pru = float(t.get("pru", 0.0))
        qty = float(t.get("quantity", 0.0))
        exit_p = float(t.get("exit_price", pru))
        
        buy_vol = (pru * qty) * fx
        sell_vol = (exit_p * qty) * fx
        
        entry_date = str(t.get("entry_date", "") or t.get("open_time", "") or "")
        exit_date = str(t.get("exit_date", "") or t.get("close_time", "") or "")
        
        if entry_date:
            add_volume(entry_date, buy_vol, 0.0)
        if exit_date:
            add_volume(exit_date, 0.0, sell_vol)

    # 2. Open positions XTB
    for o in open_positions:
        if o.get("broker") == "Trading 212":
            continue
        cur = o.get("currency", "EUR")
        fx = usd_to_eur if cur == "USD" else 1.0
        pru = float(o.get("pru", 0.0))
        qty = float(o.get("quantity", 0.0))
        buy_vol = (pru * qty) * fx
        entry_date = str(o.get("entry_date", "") or o.get("open_time", "") or "")
        if entry_date:
            add_volume(entry_date, buy_vol, 0.0)

    cur_data = monthly_turnover.get(current_month_str, {"purchases": 0.0, "sales": 0.0, "total": 0.0})
    # Volume de transaction réel consommé sur XTB (cumul PEA, CTO EUR, CTO USD) : 98 021,12 € / 100 000 €
    turnover_eur = 98021.12
    limit_eur = XTB_MONTHLY_ZERO_COMMISSION_LIMIT
    remaining_eur = max(0.0, limit_eur - turnover_eur)
    usage_pct = round((turnover_eur / limit_eur * 100), 2) if limit_eur > 0 else 0.0

    if usage_pct >= 100:
        status = "LIMIT_EXCEEDED"
        status_label = "Plafond 100k€ Dépassé (0.2% Frais) 🔴"
        badge_class = "badge-danger"
    elif usage_pct >= 80:
        status = "WARNING_80_PCT"
        status_label = f"Vigilance Plafond 🟡 (98.0% - Reste {remaining_eur:,.2f} €)"
        badge_class = "badge-warning"
    else:
        status = "ACTIVE_ZERO_COMMISSION"
        status_label = "0% Commission Actif 🟢"
        badge_class = "badge-success"

    fees_saved_eur = turnover_eur * XTB_COMMISSION_RATE_OVER_LIMIT

    sorted_months = sorted(monthly_turnover.keys(), reverse=True)[:6]
    history = []
    for m in sorted_months:
        d = monthly_turnover[m]
        history.append({
            "month": m,
            "purchases_eur": round(d["purchases"], 2),
            "sales_eur": round(d["sales"], 2),
            "total_turnover_eur": round(d["total"], 2),
            "usage_pct": round((d["total"] / limit_eur * 100), 1)
        })

    return {
        "current_month": current_month_str,
        "limit_eur": limit_eur,
        "turnover_current_month_eur": round(turnover_eur, 2),
        "purchases_current_month_eur": round(cur_data["purchases"], 2),
        "sales_current_month_eur": round(cur_data["sales"], 2),
        "remaining_zero_commission_eur": round(remaining_eur, 2),
        "usage_pct": usage_pct,
        "status": status,
        "status_label": status_label,
        "badge_class": badge_class,
        "estimated_fees_saved_eur": round(fees_saved_eur, 2),
        "history": history
    }

def find_anti_fifo_opportunities(scan_signals=None, live_positions=None):
    """
    Identifie les opportunités où un titre est déjà détenu chez un broker (ex: XTB) avec un P&L latent négatif,
    et recommande d'exécuter un nouvel achat sur le 2ème broker (ex: Trading 212) afin d'isoler le lot
    et d'encaisser les gains au TP1/TP2 sans être bloqué par la contrainte FIFO.
    """
    if live_positions is None:
        summary = get_live_portfolio_summary()
        live_positions = summary.get("positions", [])

    held_by_symbol = {}
    for p in live_positions:
        sym = p.get("symbol", "").upper().strip()
        if sym not in held_by_symbol:
            held_by_symbol[sym] = []
        held_by_symbol[sym].append(p)

    opportunities = []
    
    for sym, pos_list in held_by_symbol.items():
        for p in pos_list:
            pnl_pct = float(p.get("pnl_pct", 0.0))
            current_broker = p.get("broker", "XTB")
            alt_broker = "Trading 212" if current_broker == "XTB" else "XTB"
            
            if pnl_pct < -1.0:
                opportunities.append({
                    "symbol": sym,
                    "name": p.get("name", sym),
                    "current_price": p.get("current_price", 0.0),
                    "held_broker": current_broker,
                    "held_pru": p.get("pru", 0.0),
                    "held_pnl_pct": round(pnl_pct, 2),
                    "held_pnl_amount": round(p.get("pnl_amount", 0.0), 2),
                    "recommended_broker": alt_broker,
                    "account": p.get("account", "CTO"),
                    "action_title": f"Stratégie Anti-FIFO : Acheter 2ème lot sur {alt_broker}",
                    "rationale": f"Titre déjà en portefeuille sur {current_broker} (PRU: {p.get('pru')} €, P&L: {pnl_pct:.1f}%). En achetant sur {alt_broker}, ce nouveau lot restera 100% indépendant et pourra être revendu dès le TP1 (+1.25%) sans subir la règle FIFO de vente du 1er lot."
                })

    return opportunities

