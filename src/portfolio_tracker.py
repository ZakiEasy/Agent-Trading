import io
import csv
import concurrent.futures
from datetime import datetime
import pandas as pd
import yfinance as yf

from src.sheets_connector import (
    read_positions_from_sheets,
    add_position_to_sheets,
    close_position_in_sheets
)
from src.market_data import get_usd_conversion_rate

_LIVE_QUOTE_CACHE = {}  # symbol -> {"price": float, "day_change": float, "ts": float}
LIVE_QUOTE_TTL = 60  # 60 secondes

def fetch_live_quote_for_position(pos):
    """
    Récupère le cours en direct, la variation du jour et les métadonnées pour une position active.
    """
    symbol = pos.get("symbol", "").upper().strip()
    pru = float(pos.get("pru", 0.0))
    qty = float(pos.get("quantity", 1.0))
    sl = float(pos.get("stop_loss", pru * 0.97))
    tp1 = float(pos.get("tp1", pru * 1.0125))
    tp2 = float(pos.get("tp2", pru * 1.0225))
    entry_date_str = str(pos.get("entry_date", ""))

    days_held = 0
    if entry_date_str:
        try:
            entry_dt = pd.to_datetime(entry_date_str).to_pydatetime()
            days_held = max(0, (datetime.now() - entry_dt).days)
        except:
            days_held = 0

    current_price = pru
    day_change_pct = 0.0
    currency = pos.get("currency", "EUR" if ".PA" in symbol else "USD")

    import time
    now = time.time()
    if symbol in _LIVE_QUOTE_CACHE and (now - _LIVE_QUOTE_CACHE[symbol]["ts"]) < LIVE_QUOTE_TTL:
        current_price = _LIVE_QUOTE_CACHE[symbol]["price"]
        day_change_pct = _LIVE_QUOTE_CACHE[symbol]["day_change"]
    else:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="5d")
            if hist is not None and not hist.empty:
                closes = hist["Close"].values
                current_price = float(closes[-1])
                if len(closes) > 1:
                    prev_close = float(closes[-2])
                    day_change_pct = ((current_price - prev_close) / prev_close) * 100
                _LIVE_QUOTE_CACHE[symbol] = {"price": current_price, "day_change": day_change_pct, "ts": now}
            else:
                info = getattr(t, "info", {})
                if isinstance(info, dict):
                    current_price = float(info.get("currentPrice") or info.get("regularMarketPrice") or pru)
                    _LIVE_QUOTE_CACHE[symbol] = {"price": current_price, "day_change": 0.0, "ts": now}
        except Exception as e:
            if symbol in _LIVE_QUOTE_CACHE:
                current_price = _LIVE_QUOTE_CACHE[symbol]["price"]
                day_change_pct = _LIVE_QUOTE_CACHE[symbol]["day_change"]
            else:
                current_price = pru

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
        "name": pos.get("name", symbol),
        "account": pos.get("account", "PEA" if ".PA" in symbol else "CTO"),
        "currency": currency,
        "entry_date": entry_date_str,
        "days_held": days_held,
        "pru": round(pru, 2),
        "quantity": round(qty, 4) if qty % 1 != 0 else int(qty),
        "invested_amount": round(invested_amount, 2),
        "current_price": round(current_price, 2),
        "current_value": round(current_value, 2),
        "day_change_pct": round(day_change_pct, 2),
        "pnl_amount": round(pnl_amount, 2),
        "pnl_pct": round(pnl_pct, 2),
        "stop_loss": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "dist_to_sl_pct": round(dist_to_sl_pct, 2),
        "dist_to_tp1_pct": round(dist_to_tp1_pct, 2),
        "dist_to_tp2_pct": round(dist_to_tp2_pct, 2),
        "status_badge": status_badge,
        "status_label": status_label,
        "status_action": status_action,
        "progress_pct": progress_pct,
        "notes": pos.get("notes", "")
    }

def get_live_portfolio_summary():
    """
    Récupère l'ensemble des positions ouvertes et calcule les indicateurs clés du portefeuille.
    """
    raw_positions = read_positions_from_sheets()
    if not raw_positions:
        return {
            "total_invested": 0.0,
            "total_current_value": 0.0,
            "total_pnl_amount": 0.0,
            "total_pnl_pct": 0.0,
            "open_positions_count": 0,
            "alerts_count": 0,
            "positions": []
        }

    # Calcul parallèle multi-threadé
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        positions_live = list(executor.map(fetch_live_quote_for_position, raw_positions))

    total_invested = sum(p["invested_amount"] for p in positions_live)
    total_value = sum(p["current_value"] for p in positions_live)
    total_pnl = total_value - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0
    alerts_count = sum(1 for p in positions_live if p["status_badge"] in ["tp1_reached", "tp2_reached", "sl_danger", "time_warning"])

    return {
        "total_invested": round(total_invested, 2),
        "total_current_value": round(total_value, 2),
        "total_pnl_amount": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "open_positions_count": len(positions_live),
        "alerts_count": alerts_count,
        "positions": positions_live
    }

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
    Exemples: 'STM.FR' -> 'STM.PA', 'AAPL.US' -> 'AAPL', 'HIJP.UK' -> 'HIJP.L'
    """
    sym = str(raw_sym).strip().upper()
    if sym.endswith(".FR"):
        return sym[:-3] + ".PA"
    elif sym.endswith(".US"):
        return sym[:-3]
    elif sym.endswith(".UK"):
        return sym[:-3] + ".L"
    return sym

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
