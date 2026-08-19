import os
import json
import gspread
from datetime import datetime
from google.oauth2.service_account import Credentials
from src.config import (
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_SPREADSHEET_ID,
    GOOGLE_SHEET_NAME_WATCHLIST,
    GOOGLE_SHEET_NAME_SIGNALS,
    DEFAULT_WATCHLIST
)

# Nom de la feuille pour le suivi du portefeuille en direct
GOOGLE_SHEET_NAME_POSITIONS = "Positions"
GOOGLE_SHEET_NAME_JOURNAL = "Journal de Trading"

# Cache local de la watchlist pour les ajouts dynamiques
_local_watchlist_cache = list(DEFAULT_WATCHLIST)
_local_positions_cache = []

def get_sheets_client():
    """
    Initialise et authentifie le client Google Sheets.
    Supporte le chargement depuis une variable d'environnement (pour Render/Prod)
    ou depuis un fichier de clé credentials.json local.
    """
    if not GOOGLE_SPREADSHEET_ID:
        return None, "GOOGLE_SPREADSHEET_ID non configuré dans l'environnement."

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    env_creds = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if env_creds:
        try:
            creds_dict = json.loads(env_creds)
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            client = gspread.authorize(creds)
            return client, None
        except Exception as e:
            return None, f"Erreur d'authentification via GOOGLE_CREDENTIALS_JSON : {str(e)}"

    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        return None, f"Identifiants de connexion introuvables (pas de fichier credentials.json ni de variable GOOGLE_CREDENTIALS_JSON)."

    try:
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        return client, None
    except Exception as e:
        return None, f"Erreur d'authentification Google Sheets via fichier : {str(e)}"

def format_sharia_to_checkbox(status_val):
    """
    Convertit un statut Sharia en valeur booléenne pour la case à cocher Google Sheets (TRUE / FALSE).
    """
    if status_val is True:
        return "TRUE"
    if status_val is False:
        return "FALSE"
        
    s = str(status_val or "").strip().upper()
    if s in ["CONFORME", "HALAL", "TRUE", "VRAI", "1", "YES", "OUI"]:
        return "TRUE"
    elif s in ["NON CONFORME", "HARAM", "FALSE", "FAUX", "0", "NO", "NON"]:
        return "FALSE"
    return ""

def parse_checkbox_to_sharia(cell_val):
    """
    Convertit une valeur de cellule de case à cocher Google Sheets en statut Sharia standardisé.
    """
    s = str(cell_val or "").strip().upper()
    if s in ["TRUE", "VRAI", "1", "CONFORME", "HALAL"]:
        return "CONFORME"
    elif s in ["FALSE", "FAUX", "0", "NON CONFORME", "HARAM"]:
        return "NON CONFORME"
    return "À VÉRIFIER"

_WATCHLIST_SHEETS_CACHE = {"data": [], "ts": 0}
_SHARIA_STATUSES_SHEETS_CACHE = {"data": {}, "ts": 0}
SHEETS_CACHE_TTL = 180  # 3 minutes

def read_watchlist_from_sheets(force_refresh=False):
    """
    Lit la liste des symboles boursiers dans la feuille 'Watchlist' avec cache TTL de 3 minutes.
    """
    global _local_watchlist_cache, _WATCHLIST_SHEETS_CACHE
    import time
    now = time.time()
    if not force_refresh and _WATCHLIST_SHEETS_CACHE["data"] and (now - _WATCHLIST_SHEETS_CACHE["ts"]) < SHEETS_CACHE_TTL:
        return _WATCHLIST_SHEETS_CACHE["data"]

    client, error = get_sheets_client()
    if error:
        return _local_watchlist_cache

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_WATCHLIST)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_WATCHLIST, rows="100", cols="8")
            worksheet.append_row(["Ticker", "Nom", "Conformité Shariah", "Source Vérification", "Catégorie", "Type de Compte", "Prix"])
            for ticker in _local_watchlist_cache:
                worksheet.append_row([ticker, "", "TRUE", "AAOIFI", "Tech & IA", "PEA" if ".PA" in ticker else "Compte Dollar (CTO)", ""])
            _WATCHLIST_SHEETS_CACHE = {"data": _local_watchlist_cache, "ts": now}
            return _local_watchlist_cache

        all_rows = worksheet.get_all_values()
        tickers = []
        if all_rows:
            header_row_idx = -1
            ticker_col_idx = -1
            for r_idx, row in enumerate(all_rows):
                for c_idx, cell in enumerate(row):
                    if str(cell).strip().lower() == "ticker":
                        header_row_idx = r_idx
                        ticker_col_idx = c_idx
                        break
                if header_row_idx != -1:
                    break

            if header_row_idx != -1 and ticker_col_idx != -1:
                for row in all_rows[header_row_idx + 1:]:
                    if len(row) > ticker_col_idx:
                        ticker = str(row[ticker_col_idx]).strip().upper()
                        if ticker and not ticker.startswith("TOTAL") and not ticker.startswith("MOYENNE") and not ticker.startswith("TABLEAU"):
                            tickers.append(ticker)
            else:
                for row in all_rows:
                    if row:
                        ticker = str(row[0]).strip().upper()
                        if ticker and ticker != "TICKER" and not ticker.startswith("TABLEAU"):
                            tickers.append(ticker)
            
        if tickers:
            _local_watchlist_cache = list(dict.fromkeys(tickers)) # Déduplication
            _WATCHLIST_SHEETS_CACHE = {"data": _local_watchlist_cache, "ts": now}
            return _local_watchlist_cache
            
        _WATCHLIST_SHEETS_CACHE = {"data": _local_watchlist_cache, "ts": now}
        return _local_watchlist_cache
    except Exception as e:
        print(f"Warning: read_watchlist_from_sheets failed: {e}")
        return _WATCHLIST_SHEETS_CACHE["data"] if _WATCHLIST_SHEETS_CACHE["data"] else _local_watchlist_cache

def read_sharia_statuses_from_sheets(force_refresh=False):
    """
    Lit les statuts de conformité Sharia avec cache TTL de 3 minutes pour éviter les erreurs de quota Google Sheets.
    """
    global _SHARIA_STATUSES_SHEETS_CACHE
    import time
    now = time.time()
    if not force_refresh and _SHARIA_STATUSES_SHEETS_CACHE["data"] and (now - _SHARIA_STATUSES_SHEETS_CACHE["ts"]) < SHEETS_CACHE_TTL:
        return _SHARIA_STATUSES_SHEETS_CACHE["data"]

    client, error = get_sheets_client()
    if error:
        return _SHARIA_STATUSES_SHEETS_CACHE["data"]

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_WATCHLIST)
        all_rows = worksheet.get_all_values()
        if not all_rows:
            return _SHARIA_STATUSES_SHEETS_CACHE["data"]

        header_row_idx = -1
        ticker_col_idx = -1
        sharia_col_idx = -1

        for r_idx, row in enumerate(all_rows):
            for c_idx, cell in enumerate(row):
                cell_clean = str(cell).strip().lower()
                if cell_clean == "ticker":
                    header_row_idx = r_idx
                    ticker_col_idx = c_idx
                elif any(k in cell_clean for k in ["sharia", "shariah", "conformité", "statut sharia"]):
                    sharia_col_idx = c_idx
            if header_row_idx != -1 and sharia_col_idx != -1:
                break

        if header_row_idx == -1 or ticker_col_idx == -1 or sharia_col_idx == -1:
            return _SHARIA_STATUSES_SHEETS_CACHE["data"]

        statuses = {}
        for row in all_rows[header_row_idx + 1:]:
            if len(row) > max(ticker_col_idx, sharia_col_idx):
                t = str(row[ticker_col_idx]).strip().upper()
                s_cell = str(row[sharia_col_idx]).strip()
                if t:
                    statuses[t] = parse_checkbox_to_sharia(s_cell)
                    
        _SHARIA_STATUSES_SHEETS_CACHE = {"data": statuses, "ts": now}
        return statuses
    except Exception as e:
        print(f"Warning: read_sharia_statuses_from_sheets failed: {e}")
        return _SHARIA_STATUSES_SHEETS_CACHE["data"]

def add_ticker_to_sheets(ticker_symbol, name="", category="", is_pea=False, sharia_status="", source_verif="AAOIFI (Agent Trading)", current_price_str=""):
    """
    Ajoute ou met à jour un ticker dans la feuille 'Watchlist' de Google Sheets
    avec une case à cocher pour la conformité Sharia (TRUE / FALSE) et un alignement dynamique des colonnes.
    """
    global _local_watchlist_cache
    ticker_symbol = ticker_symbol.upper().strip()
    if not ticker_symbol:
        return False, "Le symbole de l'action ne peut pas être vide."

    if ticker_symbol not in _local_watchlist_cache:
        _local_watchlist_cache.append(ticker_symbol)

    sharia_checkbox_val = format_sharia_to_checkbox(sharia_status)

    client, error = get_sheets_client()
    if error:
        return True, f"Action {ticker_symbol} ajoutée à la watchlist active (Mode local)."

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_WATCHLIST)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_WATCHLIST, rows="100", cols="8")
            worksheet.append_row(["Ticker", "Nom", "Conformité Shariah", "Source Vérification", "Catégorie", "Type de Compte", "Prix"])

        all_rows = worksheet.get_all_values()
        
        # 1. Identifier la ligne d'en-tête (cherche 'ticker')
        header_row_idx = -1
        headers = []
        for r_idx, row in enumerate(all_rows):
            for c_idx, cell in enumerate(row):
                if str(cell).strip().lower() == "ticker":
                    header_row_idx = r_idx
                    headers = row
                    break
            if header_row_idx != -1:
                break

        if header_row_idx == -1:
            headers = ["Ticker", "Nom", "Conformité Shariah", "Source Vérification", "Catégorie", "Type de Compte", "Prix"]
            worksheet.append_row(headers)
            header_row_idx = len(all_rows)

        # 2. Mapper les indices des colonnes
        col_ticker = -1
        col_name = -1
        col_sharia = -1
        col_source = -1
        col_category = -1
        col_account = -1
        col_price = -1

        for c_idx, h in enumerate(headers):
            h_clean = str(h).strip().lower()
            if h_clean == "ticker" or h_clean == "symbole":
                col_ticker = c_idx
            elif any(k == h_clean for k in ["nom", "name", "société", "entreprise", "nom de l'entreprise"]):
                col_name = c_idx
            elif any(k in h_clean for k in ["conformité", "shariah", "sharia", "statut sharia"]):
                col_sharia = c_idx
            elif any(k in h_clean for k in ["source", "vérification", "source vérification", "source verif"]):
                col_source = c_idx
            elif any(k in h_clean for k in ["catégorie", "categorie", "category", "secteur"]):
                col_category = c_idx
            elif any(k in h_clean for k in ["type de compte", "compte", "account", "type compte"]):
                col_account = c_idx
            elif any(k in h_clean for k in ["prix", "cours", "price"]):
                col_price = c_idx

        if col_ticker == -1:
            col_ticker = 0

        # 3. Déterminer le type de compte
        if is_pea:
            account_val = "PEA"
        elif any(ticker_symbol.endswith(sfx) for sfx in [".PA", ".DE", ".AS", ".BR", ".MC", ".MI"]):
            account_val = "Compte Euro"
        else:
            account_val = "Compte Dollar (CTO)"

        # 4. Vérifier si le ticker existe déjà pour mettre à jour la ligne
        existing_row_idx = -1
        for r_idx in range(header_row_idx + 1, len(all_rows)):
            row = all_rows[r_idx]
            if len(row) > col_ticker and str(row[col_ticker]).strip().upper() == ticker_symbol:
                existing_row_idx = r_idx
                break

        if existing_row_idx != -1:
            # Mettre à jour la ligne existante
            sheet_row_num = existing_row_idx + 1 # 1-indexed pour gspread
            if col_name != -1 and name:
                worksheet.update_cell(sheet_row_num, col_name + 1, name)
            if col_sharia != -1:
                worksheet.update_cell(sheet_row_num, col_sharia + 1, sharia_checkbox_val)
            if col_source != -1 and source_verif:
                worksheet.update_cell(sheet_row_num, col_source + 1, source_verif)
            if col_category != -1 and category:
                worksheet.update_cell(sheet_row_num, col_category + 1, category)
            if col_account != -1:
                worksheet.update_cell(sheet_row_num, col_account + 1, account_val)
            if col_price != -1 and current_price_str:
                worksheet.update_cell(sheet_row_num, col_price + 1, current_price_str)
                
            return True, f"Action {ticker_symbol} ({name or 'N/A'}) mise à jour avec succès dans votre Google Sheet (Sharia: {'Coché ☑️' if sharia_checkbox_val == 'TRUE' else 'Décoché ☐'}) !"

        # 5. Créer et insérer la nouvelle ligne parfaitement alignée
        num_cols = max(len(headers), 7)
        new_row = [""] * num_cols
        new_row[col_ticker] = ticker_symbol
        if col_name != -1: new_row[col_name] = name
        if col_sharia != -1: new_row[col_sharia] = sharia_checkbox_val
        if col_source != -1: new_row[col_source] = source_verif
        if col_category != -1: new_row[col_category] = category
        if col_account != -1: new_row[col_account] = account_val
        if col_price != -1 and current_price_str: new_row[col_price] = current_price_str

        worksheet.append_row(new_row)
        print(f"✅ Action {ticker_symbol} ajoutée avec succès dans la feuille '{GOOGLE_SHEET_NAME_WATCHLIST}'.")
        return True, f"Action {ticker_symbol} ({name or 'N/A'}) ajoutée avec succès dans votre Google Sheet (Sharia: {'Coché ☑️' if sharia_checkbox_val == 'TRUE' else 'Décoché ☐'}) !"
    except Exception as e:
        print(f"⚠️ Erreur lors de l'écriture sur Google Sheets : {str(e)}")
        return True, f"Action {ticker_symbol} ajoutée à la watchlist active (Erreur écriture Google Sheets: {str(e)})."

def write_signals_to_sheets(signals):
    """
    Écrit les opportunités détectées dans la feuille 'Signaux' de Google Sheets (v2.0) en batch.
    """
    if not signals:
        return True, "Aucun signal à écrire."

    client, error = get_sheets_client()
    if error:
        return False, error

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_SIGNALS)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_SIGNALS, rows="500", cols="16")
            headers = [
                "Date / Heure", "Ticker", "Catégorie", "Compte", "Conformité Shariah", "Régime Macro",
                "Prix Entrée", "Repli (%)", "Support Technique", "Take Profit 1 (+1.25%)",
                "Take Profit 2 (+2.25%)", "Stop-Loss (Invalidation)", "R-Max (€)",
                "Taille Suggérée (€)", "Score Confluence", "Verdict"
            ]
            worksheet.append_row(headers)

        rows_to_append = []
        for s in signals:
            row = [
                s.get("date", ""),
                s.get("symbol", ""),
                s.get("category", ""),
                s.get("account_type", ""),
                format_sharia_to_checkbox(s.get("sharia_status", "")),
                s.get("macro_regime", ""),
                round(s.get("current_price", 0), 2),
                round(s.get("drop_pct", 0), 2),
                round(s.get("support", 0), 2),
                round(s.get("tp1_target", 0), 2),
                round(s.get("tp2_target", 0), 2),
                round(s.get("stop_loss", 0), 2),
                round(s.get("r_max_amount", 0), 2),
                round(s.get("suggested_nominal", 0), 2),
                s.get("confluence_score", 0),
                s.get("verdict", "")
            ]
            rows_to_append.append(row)

        worksheet.append_rows(rows_to_append)
        print(f"✅ {len(rows_to_append)} signal(aux) écrit(s) par lot dans Google Sheets ('{GOOGLE_SHEET_NAME_SIGNALS}').")
        return True, f"{len(rows_to_append)} signal(aux) enregistré(s) avec succès !"
    except Exception as e:
        print(f"❌ Erreur lors de l'écriture par lot sur Google Sheets : {str(e)}")
        return False, f"Erreur Google Sheets : {str(e)}"

# =============================================================================
# MODULE GESTION DES POSITIONS & PORTEFEUILLE EN DIRECT (LIVE TRACKING)
# =============================================================================

def get_or_create_positions_sheet():
    """
    Récupère ou crée la feuille 'Positions' dans Google Sheets avec les en-têtes standardisés.
    """
    client, error = get_sheets_client()
    if error:
        return None, error

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_POSITIONS)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_POSITIONS, rows="200", cols="14")
            headers = [
                "ID Position", "Ticker", "Nom Société", "Date d'Entrée", "PRU (Prix d'Achat)",
                "Quantité", "Capital Engagé", "Stop-Loss", "TP1 (+1.25%)", "TP2 (+2.25%)",
                "Compte (PEA/CTO)", "Devise", "Statut", "Notes"
            ]
            worksheet.append_row(headers)
        return worksheet, None
    except Exception as e:
        return None, str(e)

def read_positions_from_sheets():
    """
    Lit toutes les positions enregistrées depuis l'onglet 'Positions' de Google Sheets.
    Retourne la liste des positions actives (Statut == 'OUVERT' ou vide).
    """
    global _local_positions_cache
    worksheet, err = get_or_create_positions_sheet()
    if err or worksheet is None:
        return _local_positions_cache

    try:
        all_rows = worksheet.get_all_values()
        if len(all_rows) <= 1:
            return _local_positions_cache

        header = all_rows[0]
        header_map = {str(h).strip().lower(): idx for idx, h in enumerate(header)}
        
        positions = []
        for r_idx, row in enumerate(all_rows[1:], start=2):
            if not row or not any(row):
                continue
                
            def get_val(key_candidates, default=""):
                for k in key_candidates:
                    if k in header_map and len(row) > header_map[k]:
                        val = row[header_map[k]].strip()
                        if val:
                            return val
                return default

            ticker = get_val(["ticker", "symbole"]).upper()
            if not ticker or ticker.startswith("TOTAL"):
                continue

            statut = get_val(["statut", "status"], "OUVERT").upper()
            if statut not in ["OUVERT", "OPEN", "ACTIVE", ""]:
                continue

            try:
                pru_str = get_val(["pru", "pru (prix d'achat)", "prix d'achat", "prix entrée", "open price", "prix"])
                pru_clean = pru_str.replace("€", "").replace("$", "").replace(" ", "").replace(",", ".")
                pru = float(pru_clean) if pru_clean else 0.0
            except:
                pru = 0.0

            try:
                qty_str = get_val(["quantité", "quantite", "qty", "volume", "shares", "lots"])
                qty_clean = qty_str.replace(" ", "").replace(",", ".")
                qty = float(qty_clean) if qty_clean else 1.0
            except:
                qty = 1.0

            try:
                sl_str = get_val(["stop-loss", "stop loss", "sl", "invalidation"])
                sl_clean = sl_str.replace("€", "").replace("$", "").replace(" ", "").replace(",", ".")
                stop_loss = float(sl_clean) if sl_clean else pru * 0.97
            except:
                stop_loss = pru * 0.97

            try:
                tp1_str = get_val(["tp1", "tp1 (+1.25%)", "tp1 cible", "target 1"])
                tp1_clean = tp1_str.replace("€", "").replace("$", "").replace(" ", "").replace(",", ".")
                tp1 = float(tp1_clean) if tp1_clean else pru * 1.0125
            except:
                tp1 = pru * 1.0125

            try:
                tp2_str = get_val(["tp2", "tp2 (+2.25%)", "tp2 cible", "target 2"])
                tp2_clean = tp2_str.replace("€", "").replace("$", "").replace(" ", "").replace(",", ".")
                tp2 = float(tp2_clean) if tp2_clean else pru * 1.0225
            except:
                tp2 = pru * 1.0225

            pos_id = get_val(["id position", "id", "ticket"], f"POS-{ticker}-{r_idx}")
            name = get_val(["nom société", "nom", "name"], ticker)
            entry_date = get_val(["date d'entrée", "date achat", "date", "open time"], datetime.now().strftime("%Y-%m-%d"))
            account = get_val(["compte (pea/cto)", "compte", "account", "type de compte"], "PEA" if ".PA" in ticker else "CTO")
            currency = get_val(["devise", "currency"], "EUR" if ("PEA" in account or ".PA" in ticker or ".DE" in ticker) else "USD")
            notes = get_val(["notes", "commentaire", "comment"], "")

            pos_obj = {
                "id": pos_id,
                "row_index": r_idx,
                "symbol": ticker,
                "name": name,
                "entry_date": entry_date,
                "pru": pru,
                "quantity": qty,
                "invested_amount": pru * qty,
                "stop_loss": stop_loss,
                "tp1": tp1,
                "tp2": tp2,
                "account": account,
                "currency": currency,
                "status": "OUVERT",
                "notes": notes
            }
            positions.append(pos_obj)

        _local_positions_cache = positions
        return positions
    except Exception as e:
        print(f"⚠️ Erreur lors de la lecture des positions : {e}")
        return _local_positions_cache

def add_position_to_sheets(position_data):
    """
    Enregistre une nouvelle position dans la feuille 'Positions' de Google Sheets.
    """
    global _local_positions_cache
    worksheet, err = get_or_create_positions_sheet()
    
    ticker = position_data.get("symbol", "").upper().strip()
    name = position_data.get("name", ticker)
    entry_date = position_data.get("entry_date") or datetime.now().strftime("%Y-%m-%d")
    pru = float(position_data.get("pru", 0))
    qty = float(position_data.get("quantity", 1))
    invested = pru * qty
    sl = float(position_data.get("stop_loss", pru * 0.97))
    tp1 = float(position_data.get("tp1", pru * 1.0125))
    tp2 = float(position_data.get("tp2", pru * 1.0225))
    account = position_data.get("account", "PEA" if ".PA" in ticker else "CTO")
    currency = position_data.get("currency", "EUR" if ".PA" in ticker else "USD")
    notes = position_data.get("notes", "")
    pos_id = f"POS-{ticker}-{int(datetime.now().timestamp())}"

    new_pos = {
        "id": pos_id,
        "symbol": ticker,
        "name": name,
        "entry_date": entry_date,
        "pru": pru,
        "quantity": qty,
        "invested_amount": invested,
        "stop_loss": sl,
        "tp1": tp1,
        "tp2": tp2,
        "account": account,
        "currency": currency,
        "status": "OUVERT",
        "notes": notes
    }
    _local_positions_cache.append(new_pos)

    if worksheet is not None:
        try:
            row = [
                pos_id, ticker, name, entry_date, round(pru, 2),
                round(qty, 4) if qty % 1 != 0 else int(qty),
                round(invested, 2), round(sl, 2), round(tp1, 2), round(tp2, 2),
                account, currency, "OUVERT", notes
            ]
            worksheet.append_row(row)
            return True, f"Position {ticker} ({qty} actions à {pru} {currency}) enregistrée avec succès !"
        except Exception as e:
            return True, f"Position {ticker} ajoutée au suivi live local (Erreur Google Sheet: {e})."
            
    return True, f"Position {ticker} enregistrée avec succès (Mode local)."

def close_position_in_sheets(pos_id_or_symbol, exit_price, exit_date=None, notes=""):
    """
    Clôture une position active dans Google Sheets et l'archive dans le 'Journal de Trading'.
    """
    global _local_positions_cache
    if not exit_date:
        exit_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        
    worksheet, err = get_or_create_positions_sheet()
    
    if not _local_positions_cache:
        read_positions_from_sheets()

    target_pos = None
    for pos in _local_positions_cache:
        if pos.get("id") == pos_id_or_symbol or pos.get("symbol") == str(pos_id_or_symbol).upper():
            target_pos = pos
            pos["status"] = "FERMÉ"
            pos["exit_price"] = exit_price
            pos["exit_date"] = exit_date
            break

    if not target_pos:
        return False, f"Position {pos_id_or_symbol} introuvable."

    pru = target_pos.get("pru", 0)
    qty = target_pos.get("quantity", 1)
    pnl_unit = exit_price - pru
    pnl_amount = pnl_unit * qty
    pnl_pct = (pnl_unit / pru * 100) if pru > 0 else 0.0

    client, error = get_sheets_client()
    if client:
        try:
            sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
            # 1. Archiver dans le journal
            try:
                journal_ws = sheet.worksheet(GOOGLE_SHEET_NAME_JOURNAL)
            except gspread.exceptions.WorksheetNotFound:
                journal_ws = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_JOURNAL, rows="500", cols="15")
                journal_ws.append_row([
                    "ID Position", "Ticker", "Nom", "Date Achat", "Date Clôture",
                    "PRU", "Prix Sortie", "Quantité", "Capital Investi",
                    "P&L (€/$)", "P&L (%)", "Résultat", "Compte", "Notes"
                ])

            journal_ws.append_row([
                target_pos.get("id", ""), target_pos.get("symbol", ""), target_pos.get("name", ""),
                target_pos.get("entry_date", ""), exit_date, round(pru, 2), round(exit_price, 2),
                qty, round(pru * qty, 2), round(pnl_amount, 2), f"{pnl_pct:+.2f}%",
                "GAIN 🟢" if pnl_amount >= 0 else "PERTE 🔴", target_pos.get("account", ""), notes
            ])

            # 2. Mettre à jour le statut dans la feuille Positions
            if worksheet and target_pos.get("row_index"):
                worksheet.update_cell(target_pos["row_index"], 13, "FERMÉ")
                
            return True, f"Position {target_pos['symbol']} clôturée avec succès ! P&L : {pnl_amount:+.2f} € ({pnl_pct:+.2f}%)"
        except Exception as e:
            return True, f"Position {target_pos['symbol']} clôturée en local (Erreur Google Sheets: {e})."

    return True, f"Position {target_pos['symbol']} clôturée avec succès ! P&L : {pnl_amount:+.2f} € ({pnl_pct:+.2f}%)"

# Cache mémoire pour le journal de trading
_local_journal_cache = []

def batch_import_journal_to_sheets(closed_trades):
    """
    Importe par lot (batch update) une liste de positions fermées dans l'onglet 'Journal de Trading' de Google Sheets.
    Gère la création de la feuille si nécessaire et évite les quotas de requêtes de l'API Sheets.
    """
    global _local_journal_cache
    if not closed_trades:
        return True, "Aucune position à importer."

    _local_journal_cache = list(closed_trades)

    client, error = get_sheets_client()
    if error or not client:
        return True, f"{len(closed_trades)} trades enregistrés dans le journal local."

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            journal_ws = sheet.worksheet(GOOGLE_SHEET_NAME_JOURNAL)
        except gspread.exceptions.WorksheetNotFound:
            journal_ws = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_JOURNAL, rows=str(max(500, len(closed_trades) + 50)), cols="15")

        headers = [
            "ID Position", "Ticker", "Nom", "Date Achat", "Date Clôture",
            "PRU", "Prix Sortie", "Quantité", "Capital Investi",
            "P&L (€/$)", "P&L (%)", "Résultat", "Compte", "Devise", "Commentaire"
        ]

        rows_to_write = [headers]
        for t in closed_trades:
            rows_to_write.append([
                str(t.get("id", "")),
                str(t.get("symbol", "")),
                str(t.get("name", "")),
                str(t.get("open_time", t.get("entry_date", ""))),
                str(t.get("close_time", t.get("exit_date", ""))),
                float(t.get("pru", 0.0)),
                float(t.get("exit_price", 0.0)),
                float(t.get("quantity", 1.0)),
                float(t.get("invested_amount", 0.0)),
                float(t.get("pnl_amount", 0.0)),
                f"{float(t.get('pnl_pct', 0.0)):+.2f}%",
                str(t.get("result", "GAIN 🟢" if t.get("pnl_amount", 0) >= 0 else "PERTE 🔴")),
                str(t.get("account", "")),
                str(t.get("currency", "EUR")),
                str(t.get("comment", ""))
            ])

        # Batch update unique
        journal_ws.clear()
        journal_ws.update('A1', rows_to_write)
        print(f"✅ {len(closed_trades)} trades écrits en batch dans '{GOOGLE_SHEET_NAME_JOURNAL}'.")
        return True, f"{len(closed_trades)} trades importés avec succès dans votre Journal de Trading Google Sheets !"
    except Exception as e:
        print(f"⚠️ Erreur batch update Journal Google Sheets : {e}")
        return True, f"{len(closed_trades)} trades importés en local (Erreur Google Sheets: {e})."

def read_journal_from_sheets():
    """
    Lit l'historique complet des trades clôturés depuis Google Sheets ou le cache local.
    """
    global _local_journal_cache
    client, error = get_sheets_client()
    if not client:
        return _local_journal_cache

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        journal_ws = sheet.worksheet(GOOGLE_SHEET_NAME_JOURNAL)
        all_rows = journal_ws.get_all_values()
        if not all_rows or len(all_rows) <= 1:
            return _local_journal_cache

        headers = [h.strip().lower() for h in all_rows[0]]
        trades = []
        for r in all_rows[1:]:
            if not r or not any(r):
                continue

            def get_val(idx, default=""):
                return r[idx].strip() if len(r) > idx else default

            try:
                pru = float(get_val(5, "0").replace("€", "").replace("$", "").replace(" ", "").replace(",", "."))
            except:
                pru = 0.0

            try:
                exit_p = float(get_val(6, "0").replace("€", "").replace("$", "").replace(" ", "").replace(",", "."))
            except:
                exit_p = 0.0

            try:
                qty = float(get_val(7, "1").replace(" ", "").replace(",", "."))
            except:
                qty = 1.0

            try:
                pnl = float(get_val(9, "0").replace("€", "").replace("$", "").replace(" ", "").replace(",", "."))
            except:
                pnl = (exit_p - pru) * qty

            try:
                pnl_pct_str = get_val(10, "0").replace("%", "").replace("+", "").replace(" ", "").replace(",", ".")
                pnl_pct = float(pnl_pct_str)
            except:
                pnl_pct = (pnl / (pru * qty) * 100) if (pru * qty) > 0 else 0.0

            trades.append({
                "id": get_val(0),
                "symbol": get_val(1).upper(),
                "name": get_val(2),
                "open_time": get_val(3),
                "close_time": get_val(4),
                "pru": pru,
                "exit_price": exit_p,
                "quantity": qty,
                "invested_amount": pru * qty,
                "pnl_amount": pnl,
                "pnl_pct": pnl_pct,
                "result": get_val(11, "GAIN 🟢" if pnl >= 0 else "PERTE 🔴"),
                "account": get_val(12, "CTO"),
                "currency": get_val(13, "EUR" if "PEA" in get_val(12) or ".PA" in get_val(1) else "USD"),
                "comment": get_val(14)
            })

        _local_journal_cache = trades
        return trades
    except Exception as e:
        print(f"⚠️ Erreur lecture Journal Google Sheets : {e}")
        return _local_journal_cache

def batch_import_positions_to_sheets(open_positions):
    """
    Importe par lot (batch update) les positions actives dans l'onglet 'Positions' de Google Sheets.
    """
    global _local_positions_cache
    if not open_positions:
        return True, "Aucune position active à importer."

    _local_positions_cache = list(open_positions)
    worksheet, err = get_or_create_positions_sheet()
    if err or not worksheet:
        return True, f"{len(open_positions)} positions enregistrées en local."

    try:
        headers = [
            "ID Position", "Ticker", "Nom", "Date Achat", "PRU",
            "Quantité", "Capital Investi", "Stop-Loss", "Take Profit 1", "Take Profit 2",
            "Compte", "Devise", "Statut", "Notes"
        ]

        rows_to_write = [headers]
        for p in open_positions:
            rows_to_write.append([
                str(p.get("id", "")),
                str(p.get("symbol", "")),
                str(p.get("name", "")),
                str(p.get("entry_date", "")),
                float(p.get("pru", 0.0)),
                float(p.get("quantity", 1.0)),
                float(p.get("pru", 0.0) * p.get("quantity", 1.0)),
                float(p.get("stop_loss", p.get("pru", 0.0) * 0.97)),
                float(p.get("tp1", p.get("pru", 0.0) * 1.0125)),
                float(p.get("tp2", p.get("pru", 0.0) * 1.0225)),
                str(p.get("account", "")),
                str(p.get("currency", "EUR")),
                str(p.get("status", "OUVERT")),
                str(p.get("notes", ""))
            ])

        worksheet.clear()
        worksheet.update('A1', rows_to_write)
        print(f"✅ {len(open_positions)} positions actives écrites en batch dans '{GOOGLE_SHEET_NAME_POSITIONS}'.")
        return True, f"{len(open_positions)} positions actives synchronisées dans Google Sheets !"
    except Exception as e:
        print(f"⚠️ Erreur batch update Positions Google Sheets : {e}")
        return True, f"{len(open_positions)} positions enregistrées en local."

_TREASURY_SHEETS_CACHE = {"data": [], "ts": 0}
GOOGLE_SHEET_NAME_TREASURY = "Trésorerie"

def get_or_create_treasury_sheet():
    """
    Récupère ou crée la feuille 'Trésorerie' dans Google Sheets.
    """
    client, error = get_sheets_client()
    if error or not client:
        return None, error

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_TREASURY)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_TREASURY, rows="1500", cols="10")
            headers = ["ID Opération", "Type", "Instrument", "Ticker", "Date/Heure", "Montant", "Compte", "Devise", "Commentaire"]
            worksheet.append_row(headers)
        return worksheet, None
    except Exception as e:
        return None, f"Erreur accès feuille Trésorerie: {str(e)}"

def batch_import_treasury_to_sheets(cash_operations):
    """
    Écrit en batch les opérations de trésorerie dans l'onglet 'Trésorerie' de Google Sheets.
    """
    global _TREASURY_SHEETS_CACHE
    import time
    if not cash_operations:
        return True, "Aucune opération de trésorerie à écrire."

    worksheet, err = get_or_create_treasury_sheet()
    if err or not worksheet:
        _TREASURY_SHEETS_CACHE = {"data": cash_operations, "ts": time.time()}
        return True, f"{len(cash_operations)} opérations de trésorerie enregistrées en local."

    try:
        headers = ["ID Opération", "Type", "Instrument", "Ticker", "Date/Heure", "Montant", "Compte", "Devise", "Commentaire"]
        rows_to_write = [headers]
        for op in cash_operations:
            rows_to_write.append([
                str(op.get("id", "")),
                str(op.get("type", "")),
                str(op.get("instrument", "")),
                str(op.get("symbol", "")),
                str(op.get("time", "")),
                float(op.get("amount", 0.0)),
                str(op.get("account", "")),
                str(op.get("currency", "EUR")),
                str(op.get("comment", ""))
            ])

        worksheet.clear()
        worksheet.update('A1', rows_to_write)
        _TREASURY_SHEETS_CACHE = {"data": cash_operations, "ts": time.time()}
        print(f"✅ {len(cash_operations)} opérations de trésorerie écrites en batch dans '{GOOGLE_SHEET_NAME_TREASURY}'.")
        return True, f"{len(cash_operations)} opérations de trésorerie synchronisées dans Google Sheets !"
    except Exception as e:
        print(f"⚠️ Erreur batch update Trésorerie Google Sheets : {e}")
        _TREASURY_SHEETS_CACHE = {"data": cash_operations, "ts": time.time()}
        return True, f"{len(cash_operations)} opérations enregistrées en local."

def read_treasury_from_sheets(force_refresh=False):
    """
    Lit les opérations de trésorerie depuis Google Sheets avec cache TTL de 3 minutes.
    """
    global _TREASURY_SHEETS_CACHE
    import time
    now = time.time()
    if not force_refresh and _TREASURY_SHEETS_CACHE["data"] and (now - _TREASURY_SHEETS_CACHE["ts"]) < SHEETS_CACHE_TTL:
        return _TREASURY_SHEETS_CACHE["data"]

    worksheet, err = get_or_create_treasury_sheet()
    if err or not worksheet:
        return _TREASURY_SHEETS_CACHE["data"]

    try:
        all_rows = worksheet.get_all_values()
        if not all_rows or len(all_rows) <= 1:
            return _TREASURY_SHEETS_CACHE["data"]

        headers = [str(c).strip().lower() for c in all_rows[0]]
        col_map = {h: idx for idx, h in enumerate(headers)}

        ops = []
        for r in all_rows[1:]:
            if not r or len(r) <= 1:
                continue

            def get_val(keys, default=""):
                for k in keys:
                    if k in col_map and len(r) > col_map[k]:
                        val = str(r[col_map[k]]).strip()
                        if val: return val
                return default

            op_id = get_val(["id opération", "id", "id operation"])
            op_type = get_val(["type", "type opération"])
            instrument = get_val(["instrument", "nom"])
            ticker = get_val(["ticker", "symbole"])
            time_val = get_val(["date/heure", "date", "time"])
            amt_str = get_val(["montant", "amount"]).replace("€", "").replace("$", "").replace(" ", "").replace(",", ".")
            try:
                amt = float(amt_str) if amt_str else 0.0
            except:
                amt = 0.0
            account = get_val(["compte", "account"], "CTO Euro")
            currency = get_val(["devise", "currency"], "EUR")
            comment = get_val(["commentaire", "comment", "notes"])

            ops.append({
                "id": op_id,
                "type": op_type,
                "instrument": instrument,
                "symbol": ticker,
                "time": time_val,
                "amount": amt,
                "account": account,
                "currency": currency,
                "comment": comment
            })

        _TREASURY_SHEETS_CACHE = {"data": ops, "ts": now}
        return ops
    except Exception as e:
        print(f"⚠️ Erreur lecture Trésorerie Google Sheets : {e}")
        return _TREASURY_SHEETS_CACHE["data"]

