import os
import json
import gspread
from google.oauth2.service_account import Credentials
from src.config import (
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_SPREADSHEET_ID,
    GOOGLE_SHEET_NAME_WATCHLIST,
    GOOGLE_SHEET_NAME_SIGNALS,
    DEFAULT_WATCHLIST
)

# Cache local de la watchlist pour les ajouts dynamiques
_local_watchlist_cache = list(DEFAULT_WATCHLIST)

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

def read_watchlist_from_sheets():
    """
    Lit la watchlist depuis la feuille Google Sheets configurée.
    Si non configurée ou en cas d'erreur, utilise la watchlist en mémoire.
    """
    global _local_watchlist_cache
    client, error = get_sheets_client()
    if error:
        return _local_watchlist_cache

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_WATCHLIST)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_WATCHLIST, rows="100", cols="6")
            worksheet.append_row(["Ticker", "Nom", "Catégorie", "Compte", "Conformité Shariah"])
            for ticker in _local_watchlist_cache:
                worksheet.append_row([ticker, "", "", "PEA" if ".PA" in ticker else "CTO", ""])
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
            return _local_watchlist_cache
        return _local_watchlist_cache
    except Exception as e:
        return _local_watchlist_cache

def add_ticker_to_sheets(ticker_symbol, name="", category="", is_pea=False, sharia_status=""):
    """
    Ajoute un nouveau ticker dans la feuille 'Watchlist' de Google Sheets.
    Évite les doublons et met à jour le cache local.
    """
    global _local_watchlist_cache
    ticker_symbol = ticker_symbol.upper().strip()
    if not ticker_symbol:
        return False, "Le symbole de l'action ne peut pas être vide."

    # Ajouter au cache local s'il n'existe pas déjà
    if ticker_symbol not in _local_watchlist_cache:
        _local_watchlist_cache.append(ticker_symbol)

    client, error = get_sheets_client()
    if error:
        return True, f"Action {ticker_symbol} ajoutée à la watchlist active (Mode local/mémoire)."

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_WATCHLIST)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_WATCHLIST, rows="100", cols="6")
            worksheet.append_row(["Ticker", "Nom", "Catégorie", "Compte", "Conformité Shariah"])

        all_rows = worksheet.get_all_values()
        
        # Vérifier si le ticker existe déjà dans le Google Sheet
        ticker_col_idx = 0
        if all_rows:
            for c_idx, cell in enumerate(all_rows[0]):
                if str(cell).strip().lower() == "ticker":
                    ticker_col_idx = c_idx
                    break
            for row in all_rows[1:]:
                if len(row) > ticker_col_idx and str(row[ticker_col_idx]).strip().upper() == ticker_symbol:
                    return True, f"L'action {ticker_symbol} est déjà présente dans votre Google Sheet."

        # Insérer la nouvelle ligne
        account_str = "PEA" if is_pea else "CTO (US)"
        new_row = [ticker_symbol, name, category, account_str, sharia_status]
        worksheet.append_row(new_row)
        
        print(f"✅ Action {ticker_symbol} ajoutée avec succès dans la feuille '{GOOGLE_SHEET_NAME_WATCHLIST}'.")
        return True, f"Action {ticker_symbol} ({name or 'N/A'}) ajoutée avec succès dans votre Google Sheet !"
    except Exception as e:
        print(f"⚠️ Erreur lors de l'ajout du ticker sur Google Sheets : {str(e)}")
        return True, f"Action {ticker_symbol} ajoutée à la watchlist active (Erreur écriture Google Sheets: {str(e)})."

def write_signals_to_sheets(signals):
    """
    Écrit les opportunités détectées dans la feuille 'Signaux' de Google Sheets (v2.0) en batch.
    signals: Liste de dictionnaires contenant les détails du signal et du dimensionnement R-Max.
    """
    if not signals:
        return True

    client, error = get_sheets_client()
    if error:
        return False

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_SIGNALS)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_SIGNALS, rows="500", cols="16")
            worksheet.append_row([
                "Date Détection", "Ticker", "Conformité Sharia", "Catégorie", "Compte", "Régime Macro", "Cours Actuel", 
                "Dip (%)", "Support", "TP1 Cible (+1.25%)", "TP2 Cible (+2.25%)", "Stop-Loss (-1%)",
                "R-Max Risque (€)", "Alloc. Suggérée (€)", "Score Confluence", "Verdict"
            ])

        rows_to_append = []
        for signal in signals:
            row = [
                signal.get("date", ""),
                signal.get("symbol", ""),
                signal.get("sharia_status", ""),
                signal.get("category", ""),
                signal.get("account_type", ""),
                signal.get("macro_regime", ""),
                signal.get("current_price", 0.0),
                signal.get("drop_pct", 0.0),
                signal.get("support", 0.0),
                signal.get("tp1_target", 0.0),
                signal.get("tp2_target", 0.0),
                signal.get("stop_loss", 0.0),
                signal.get("r_max_amount", 0.0),
                signal.get("suggested_nominal", 0.0),
                f"{signal.get('confluence_score', 0)}/10",
                signal.get("verdict", "")
            ]
            rows_to_append.append(row)
            
        if hasattr(worksheet, 'append_rows'):
            worksheet.append_rows(rows_to_append)
        else:
            for r in rows_to_append:
                worksheet.append_row(r)
                
        return True
    except Exception as e:
        print(f"⚠️ Erreur lors de l'écriture des signaux sur Google Sheets : {str(e)}")
        return False

def read_sharia_statuses_from_sheets():
    """
    Lit les statuts de conformité Sharia saisis par l'utilisateur dans le Google Sheet.
    """
    import time
    global _sharia_statuses_cache, _sharia_cache_timestamp
    
    if '_sharia_statuses_cache' not in globals():
        globals()['_sharia_statuses_cache'] = None
    if '_sharia_cache_timestamp' not in globals():
        globals()['_sharia_cache_timestamp'] = 0
        
    now = time.time()
    if globals()['_sharia_statuses_cache'] is not None and (now - globals()['_sharia_cache_timestamp']) < 60:
        return globals()['_sharia_statuses_cache']

    client, error = get_sheets_client()
    if error:
        return {}

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_WATCHLIST)
        all_rows = worksheet.get_all_values()
        
        statuses = {}
        if all_rows:
            header_row_idx = -1
            ticker_col_idx = -1
            sharia_col_idx = -1
            for r_idx, row in enumerate(all_rows):
                for c_idx, cell in enumerate(row):
                    cell_clean = str(cell).strip().lower()
                    if cell_clean == "ticker":
                        header_row_idx = r_idx
                        ticker_col_idx = c_idx
                    elif "conformité" in cell_clean or "shariah" in cell_clean:
                        sharia_col_idx = c_idx
                if header_row_idx != -1 and sharia_col_idx != -1:
                    break

            if header_row_idx != -1 and ticker_col_idx != -1 and sharia_col_idx != -1:
                for row in all_rows[header_row_idx + 1:]:
                    if len(row) > max(ticker_col_idx, sharia_col_idx):
                        ticker = str(row[ticker_col_idx]).strip().upper()
                        status = str(row[sharia_col_idx]).strip().upper()
                        if ticker and status:
                            statuses[ticker] = status
                            
        globals()['_sharia_statuses_cache'] = statuses
        globals()['_sharia_cache_timestamp'] = now
        return statuses
    except Exception as e:
        return {}
