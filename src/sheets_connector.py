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
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_WATCHLIST, rows="100", cols="8")
            worksheet.append_row(["Ticker", "Nom", "Conformité Shariah", "Source Vérification", "Catégorie", "Type de Compte", "Prix"])
            for ticker in _local_watchlist_cache:
                worksheet.append_row([ticker, "", "CONFORME", "AAOIFI", "Tech & IA", "PEA" if ".PA" in ticker else "Compte Dollar (CTO)", ""])
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

def read_sharia_statuses_from_sheets():
    """
    Lit les statuts de conformité Sharia pré-renseignés dans la feuille Google Sheets.
    Retourne un dictionnaire {TICKER: STATUT}.
    """
    client, error = get_sheets_client()
    if error:
        return {}

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_WATCHLIST)
        all_rows = worksheet.get_all_values()
        if not all_rows:
            return {}

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
            return {}

        statuses = {}
        for row in all_rows[header_row_idx + 1:]:
            if len(row) > max(ticker_col_idx, sharia_col_idx):
                t = str(row[ticker_col_idx]).strip().upper()
                s = str(row[sharia_col_idx]).strip()
                if t and s:
                    statuses[t] = s
        return statuses
    except Exception as e:
        return {}

def add_ticker_to_sheets(ticker_symbol, name="", category="", is_pea=False, sharia_status="", source_verif="AAOIFI (Agent Trading)", current_price_str=""):
    """
    Ajoute ou met à jour un ticker dans la feuille 'Watchlist' de Google Sheets
    avec un mapping précis et dynamique des colonnes :
    - Col 'Ticker' : Symbole boursier (ex: LLY)
    - Col 'Nom' : Nom de la société (ex: Eli Lilly and Company)
    - Col 'Conformité Shariah' : Statut Sharia (ex: CONFORME)
    - Col 'Source Vérification' : Source (ex: AAOIFI / Zoya)
    - Col 'Catégorie' : Catégorie sectorielle (ex: Santé & Pharma)
    - Col 'Type de Compte' : PEA / Compte Euro / Compte Dollar (CTO)
    - Col 'Prix' : Cours actuel formaté
    """
    global _local_watchlist_cache
    ticker_symbol = ticker_symbol.upper().strip()
    if not ticker_symbol:
        return False, "Le symbole de l'action ne peut pas être vide."

    if ticker_symbol not in _local_watchlist_cache:
        _local_watchlist_cache.append(ticker_symbol)

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
            if col_sharia != -1 and sharia_status:
                worksheet.update_cell(sheet_row_num, col_sharia + 1, sharia_status)
            if col_source != -1 and source_verif:
                worksheet.update_cell(sheet_row_num, col_source + 1, source_verif)
            if col_category != -1 and category:
                worksheet.update_cell(sheet_row_num, col_category + 1, category)
            if col_account != -1:
                worksheet.update_cell(sheet_row_num, col_account + 1, account_val)
            if col_price != -1 and current_price_str:
                worksheet.update_cell(sheet_row_num, col_price + 1, current_price_str)
                
            return True, f"Action {ticker_symbol} ({name or 'N/A'}) mise à jour avec succès dans votre Google Sheet !"

        # 5. Créer et insérer la nouvelle ligne parfaitement alignée
        num_cols = max(len(headers), 7)
        new_row = [""] * num_cols
        new_row[col_ticker] = ticker_symbol
        if col_name != -1: new_row[col_name] = name
        if col_sharia != -1: new_row[col_sharia] = sharia_status or "CONFORME"
        if col_source != -1: new_row[col_source] = source_verif
        if col_category != -1: new_row[col_category] = category
        if col_account != -1: new_row[col_account] = account_val
        if col_price != -1 and current_price_str: new_row[col_price] = current_price_str

        worksheet.append_row(new_row)
        print(f"✅ Action {ticker_symbol} ajoutée avec succès dans la feuille '{GOOGLE_SHEET_NAME_WATCHLIST}'.")
        return True, f"Action {ticker_symbol} ({name or 'N/A'}) ajoutée avec succès dans votre Google Sheet !"
    except Exception as e:
        print(f"⚠️ Erreur lors de l'écriture sur Google Sheets : {str(e)}")
        return True, f"Action {ticker_symbol} ajoutée à la watchlist active (Erreur écriture Google Sheets: {str(e)})."

def write_signals_to_sheets(signals):
    """
    Écrit les opportunités détectées dans la feuille 'Signaux' de Google Sheets (v2.0) en batch.
    signals: Liste de dictionnaires contenant les détails du signal et du dimensionnement R-Max.
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
                s.get("sharia_status", ""),
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
