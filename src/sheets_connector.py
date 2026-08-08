import os
import gspread
from google.oauth2.service_account import Credentials
from src.config import (
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_SPREADSHEET_ID,
    GOOGLE_SHEET_NAME_WATCHLIST,
    GOOGLE_SHEET_NAME_SIGNALS,
    DEFAULT_WATCHLIST
)

def get_sheets_client():
    """
    Initialise et authentifie le client Google Sheets.
    """
    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        return None, f"Fichier de credentials introuvable : {GOOGLE_CREDENTIALS_FILE}"
    
    if not GOOGLE_SPREADSHEET_ID:
        return None, "GOOGLE_SPREADSHEET_ID non configuré dans l'environnement."

    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        return client, None
    except Exception as e:
        return None, f"Erreur d'authentification Google Sheets : {str(e)}"

def read_watchlist_from_sheets():
    """
    Lit la watchlist depuis la feuille Google Sheets configurée.
    Si non configurée ou en cas d'erreur, utilise la watchlist par défaut de config.py.
    """
    client, error = get_sheets_client()
    if error:
        print(f"⚠️ Mode Hors-Connexion Google Sheets : {error}")
        print(f"ℹ️ Utilisation de la watchlist par défaut : {DEFAULT_WATCHLIST}")
        return DEFAULT_WATCHLIST

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_WATCHLIST)
        except gspread.exceptions.WorksheetNotFound:
            # Créer la feuille si elle n'existe pas
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_WATCHLIST, rows="100", cols="5")
            worksheet.append_row(["Ticker", "Nom", "Description"])
            for ticker in DEFAULT_WATCHLIST:
                worksheet.append_row([ticker])
            print(f"✅ Feuille '{GOOGLE_SHEET_NAME_WATCHLIST}' créée et initialisée avec la watchlist par défaut.")
            return DEFAULT_WATCHLIST

        all_rows = worksheet.get_all_values()
        tickers = []
        if all_rows:
            # Trouver la ligne contenant le mot "Ticker"
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
                # Lire les valeurs de la colonne sous le header "Ticker"
                for row in all_rows[header_row_idx + 1:]:
                    if len(row) > ticker_col_idx:
                        ticker = str(row[ticker_col_idx]).strip().upper()
                        # Ignorer les lignes vides, de titre ou de totalisation
                        if ticker and not ticker.startswith("TOTAL") and not ticker.startswith("MOYENNE") and not ticker.startswith("TABLEAU"):
                            tickers.append(ticker)
            else:
                # Fallback sur la première colonne
                for row in all_rows:
                    if row:
                        ticker = str(row[0]).strip().upper()
                        if ticker and ticker != "TICKER" and not ticker.startswith("TABLEAU"):
                            tickers.append(ticker)
            
        return tickers if tickers else DEFAULT_WATCHLIST
    except Exception as e:
        print(f"⚠️ Erreur lors de la lecture de la watchlist Google Sheets : {str(e)}")
        print(f"ℹ️ Utilisation de la watchlist par défaut : {DEFAULT_WATCHLIST}")
        return DEFAULT_WATCHLIST

def write_signals_to_sheets(signals):
    """
    Écrit les opportunités détectées dans la feuille 'Signaux' de Google Sheets.
    signals: Liste de dictionnaires contenant les détails du signal.
    """
    client, error = get_sheets_client()
    if error:
        print(f"⚠️ Impossible d'écrire sur Google Sheets : {error}")
        return False

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        try:
            worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_SIGNALS)
        except gspread.exceptions.WorksheetNotFound:
            # Créer la feuille si elle n'existe pas
            worksheet = sheet.add_worksheet(title=GOOGLE_SHEET_NAME_SIGNALS, rows="500", cols="10")
            worksheet.append_row([
                "Date Détection", "Ticker", "Conformité Sharia", "Cours Actuel", 
                "Baisse (%)", "Support", "Cible Sortie", "RSI", "Verdict"
            ])
            print(f"✅ Feuille '{GOOGLE_SHEET_NAME_SIGNALS}' créée.")

        # Insérer les signaux
        for signal in signals:
            row = [
                signal.get("date", ""),
                signal.get("symbol", ""),
                signal.get("sharia_status", ""),
                signal.get("current_price", 0.0),
                signal.get("drop_pct", 0.0),
                signal.get("support", 0.0),
                signal.get("target_exit", 0.0),
                signal.get("rsi", 0.0),
                signal.get("verdict", "")
            ]
            worksheet.append_row(row)
            
        print(f"✅ {len(signals)} signal/signaux écrit(s) avec succès dans Google Sheets.")
        return True
    except Exception as e:
        print(f"⚠️ Erreur lors de l'écriture des signaux sur Google Sheets : {str(e)}")
        return False

def read_sharia_statuses_from_sheets():
    """
    Lit les statuts de conformité Sharia saisis par l'utilisateur dans le Google Sheet.
    Retourne un dictionnaire {ticker: statut} (ex: {'AAPL': 'CONFORME'}).
    """
    client, error = get_sheets_client()
    if error:
        return {}

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        worksheet = sheet.worksheet(GOOGLE_SHEET_NAME_WATCHLIST)
        all_rows = worksheet.get_all_values()
        
        statuses = {}
        if all_rows:
            # Trouver les lignes d'entête
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
        return statuses
    except Exception as e:
        print(f"⚠️ Erreur lors de la lecture des statuts Sharia : {str(e)}")
        return {}
