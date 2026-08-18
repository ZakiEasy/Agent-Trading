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

    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="5d")
        if hist is not None and not hist.empty:
            closes = hist["Close"].values
            current_price = float(closes[-1])
            if len(closes) > 1:
                prev_close = float(closes[-2])
                day_change_pct = ((current_price - prev_close) / prev_close) * 100
        else:
            info = getattr(t, "info", {})
            if isinstance(info, dict):
                current_price = float(info.get("currentPrice") or info.get("regularMarketPrice") or pru)
    except Exception as e:
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
        clean_sym = raw_sym
        if clean_sym.endswith(".FR"):
            clean_sym = clean_sym.replace(".FR", ".PA")
        elif clean_sym.endswith(".US"):
            clean_sym = clean_sym.replace(".US", "")
        elif clean_sym.endswith(".DE"):
            clean_sym = clean_sym # Déjà correct

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
