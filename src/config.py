import os
from pathlib import Path
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Paramètres de Trading ---
# Watchlist par défaut si Google Sheets n'est pas configuré ou est vide
DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "NVDA", "ASML", "MC.PA", "OR.PA"
]

# Seuils de variation pour la détection de baisse conjoncturelle
MIN_DROP_PCT = 3.0   # Baisse minimale de -3%
MAX_DROP_PCT = 8.0   # Baisse maximale de -8%
LOOKBACK_DAYS = 3    # Période d'évaluation de la baisse (1 à 3 sessions)

# Cibles de rebond et invalidation
TARGET_REBOUND_MIN = 1.0
TARGET_REBOUND_MAX = 2.0
HOLDING_PERIOD_DAYS = 10

# --- Filtres de Conformité Sharia (Normes AAOIFI / MSCI Islamic) ---
SHARIA_MAX_DEBT_RATIO = 0.33        # Dette totale / Capitalisation boursière < 33%
SHARIA_MAX_CASH_RATIO = 0.33        # Liquidités & Placements rémunérés / Capitalisation boursière < 33%
SHARIA_MAX_RECEIVABLES_RATIO = 0.33 # Créances clients / Capitalisation boursière < 33%

# Secteurs exclus d'office (Business Screen)
SHARIA_EXCLUDED_INDUSTRIES = [
    "bank", "financial services", "insurance", "conventional", "brewery", "distillery", 
    "alcohol", "gambling", "casino", "defense", "military", "weapon", "tobacco", 
    "entertainment", "media", "adult entertainment", "pork"
]

# --- Intégration Google Sheets ---
# Nom ou chemin du fichier de credentials Google
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", str(BASE_DIR / "credentials.json"))
GOOGLE_SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID", "")
GOOGLE_SHEET_NAME_WATCHLIST = os.getenv("GOOGLE_SHEET_NAME_WATCHLIST", "Watchlist")
GOOGLE_SHEET_NAME_SIGNALS = os.getenv("GOOGLE_SHEET_NAME_SIGNALS", "Signaux")
