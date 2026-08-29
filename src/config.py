import os
from pathlib import Path
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ==============================================================================
# --- 1. Paramètres de Trading & Univers d'Actifs (Version 2.0) ---
# ==============================================================================

# Watchlist complète (49 actions suivies et qualifiées)
DEFAULT_WATCHLIST = [
    # Hyperscalers & Géants Cloud/IA
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "UBER", "BABA", "BKNG", "BYDDY",
    # Éditeurs de Logiciels & SaaS
    "CRM", "SAP", "ESTC", "ASAN",
    # Semi-conducteurs & Équipements
    "NVDA", "TSM", "AVGO", "ASML", "ASML.AS", "ARM", "QCOM", "INTC", "STMPA.PA",
    # Mémoire & Stockage de Données
    "DELL", "005930.KS", "SNDK",
    # Luxe & Cosmétique Prestige (PEA)
    "MC.PA", "OR.PA", "RMS.PA",
    # Santé & Pharmacie
    "SAN.PA", "LLY", "MRK", "MRK.DE",
    # Industrie, Défense & Aéro
    "AIR.PA", "SU.PA", "LR.PA",
    # Énergie & Transition
    "TTE.PA", "ENGI.PA", "GTT.PA", "XOM",
    # Matériaux & Chimie
    "AI.PA", "LIN.PA", "GOLD",
    # Télécoms & Réseaux
    "CSCO", "ERIC", "NOK", "RYAAY",
    # ETFs & Indices Factoriels
    "IS3E.DE", "IS3R.DE"
]

# Market Pool élargi pour le scan de marché (Large & Mid Caps US & EU)
DEFAULT_MARKET_POOL = [
    # US Tech & Large Caps
    "MSFT", "AAPL", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "AMD",
    # Europe / CAC 40
    "MC.PA", "OR.PA", "AIR.PA", "RMS.PA", "KER.PA", "EL.PA", "SAN.PA", "TTE.PA", "ASML.AS"
]

# Capitalisation boursière minimale pour univers de qualité (Large/Mid Caps > 2 Mrd)
MIN_MARKET_CAP_USD = 2_000_000_000

# Liquidité minimale quotidienne pour éliminer le risque de slippage (Volume quotidien > 1 M€/$)
MIN_AVG_DAILY_VOLUME_USD = 1_000_000

# ETF Sectoriels de référence pour analyse de force relative Top-Down
SECTOR_ETFS = {
    "Hyperscalers & Géants Cloud/IA": "XLK",
    "Éditeurs de Logiciels & SaaS": "XLK",
    "Semi-conducteurs & Équipements": "SMH",
    "Mémoire & Stockage de Données": "SMH",
    "Luxe & Cosmétique Prestige": "XLY",
    "Consommation Mondiale & E-Commerce": "XLY",
    "Industrie, Défense & Aéro": "XLI",
    "Santé & Pharmacie": "XLV",
    "Énergie & Transition": "XLE",
    "Matériaux & Chimie": "XLB",
    "ETFs & Indices Factoriels": "SPY",
    "Autres": "SPY"
}

# Seuils de variation pour la détection de baisse (Dip Mean Reversion)
MIN_DROP_PCT = 3.0   # Baisse minimale de -3%
MAX_DROP_PCT = 8.0   # Baisse maximale de -8%
LOOKBACK_DAYS = 3    # Période d'évaluation de la baisse (1 à 3 sessions)

# Cibles de rebond tactique (Take Profit) & Gestion Dynamique du Stop
TARGET_TP1_MIN = 1.0   # TP1 : +1.0% à +1.5%
TARGET_TP1_MAX = 1.5
TARGET_TP1_DEFAULT = 1.25 # Médiane TP1

TARGET_TP2_MIN = 2.0   # TP2 : +2.0% à +2.5%
TARGET_TP2_MAX = 2.5
TARGET_TP2_DEFAULT = 2.25 # Médiane TP2

# Optimisations de Gestion de Position issues du Backtest
BREAKEVEN_TRIGGER_PCT = 0.80      # +0.80% de gain -> Remontée immédiate du Stop-Loss au prix d'achat (0.0% de risque)
TRAILING_STOP_TP1_PCT = 1.25      # À TP1 (+1.25%) -> Vente de 50% et stop suiveur à TP1 pour le solde vers TP2

# Compatibilité legacy
TARGET_REBOUND_MIN = TARGET_TP1_MIN
TARGET_REBOUND_MAX = TARGET_TP2_MAX

# Horizon temporel de détention (Invalidation temporelle Time Stop : J+10 ouvrés)
HOLDING_PERIOD_MIN_DAYS = 3
HOLDING_PERIOD_MAX_DAYS = 15
HOLDING_PERIOD_DAYS = 10
EARNINGS_BLACKOUT_DAYS = 10  # 10 jours ouvrés sans résultats ni événement majeur

# ==============================================================================
# --- 2. Gestion du Risque & Paramètres de Capital (R-Max) ---
# ==============================================================================

# Capital de référence standard (Aligné sur le capital total réel du portefeuille : 18 183,05 €)
CAPITAL_REFERENCE_DEFAULT = 18183.05

# R-Max : Perte maximale tolérée par trade en % du capital total
R_MAX_PCT_STANDARD = 0.010   # 1.0% en régime RISK-ON (ex: 181,83 € pour 18 183,05 €)
R_MAX_PCT_REDUCED = 0.005    # 0.5% en régime NEUTRE/VIGILANCE (ex: 90,92 € pour 18 183,05 €)

# Taille maximale par ligne en % du capital total (20% à 25%)
MAX_ALLOCATION_PER_LINE_PCT = 0.25

# Réserve de liquidité minimale permanente obligatoire (25% à 30% du capital)
MIN_CASH_RESERVE_PCT = 0.25

# Nombre maximal de positions simultanées (2 à 4 lignes actives)
MAX_SIMULTANEOUS_POSITIONS = 4

# Contrôle de corrélation : Maximum 2 positions simultanées au sein du même secteur
MAX_SECTOR_POSITIONS = 2

# Plafond de Drawdown hebdomadaire : -3.0% sur une semaine glissante -> Circuit Breaker
WEEKLY_DRAWDOWN_LIMIT_PCT = 3.0

# ==============================================================================
# --- 3. Baromètre Macroéconomique & Régime de Marché ---
# ==============================================================================

# Tickers des indicateurs macroéconomiques
MACRO_TICKERS = {
    "VIX": "^VIX",             # Volatilité S&P 500
    "DXY": "DX-Y.NYB",         # Dollar Index
    "DXY_ALT": "UUP",          # Invesco DB US Dollar Index Bullish Fund (fallback)
    "XLY": "XLY",              # Consommation Discrétionnaire
    "XLP": "XLP",              # Consommation de Base
    "TNX_10Y": "^TNX",         # Rendement US 10 Ans (* 10)
    "IRX_2Y": "^IRX",          # Rendement US Court Terme / 13-week bill
    "WTI": "CL=F",             # Pétrole Brut WTI
    "BRENT": "BZ=F",           # Pétrole Brut Brent
    "GOLD": "GC=F"             # Or
}

# Seuils des indicateurs macro
# VIX
VIX_FAVORABLE_MAX = 18.0       # < 18 : Marché calme (Risk-On)
VIX_ALERT_MAX = 22.0           # 18-22 : Vigilance / Neutre (Ajusté suite au backtest des crises)
VIX_RISK_OFF_MAX = 35.0        # 22-35 : Stress haussier non stabilisé (Risk-Off)
VIX_CONTRARIAN_SPIKE = 35.0    # > 35-40 : Panique extrême = opportunité contrarienne

# DXY
DXY_FAVORABLE_MAX = 102.0      # < 102 : Liquidité abondante
DXY_ALERT_MAX = 105.0          # 102-105 : Consolidation
# > 105 : Resserrement de liquidité (Défavorable)

# Yield Curve (10Y - 2Y)
YIELD_CURVE_FAVORABLE_MIN = 0.20 # > +0.20% : Écart positif et stable
YIELD_CURVE_INVERTED_MAX = -0.20 # < -0.20% : Inversion prononcée

# Pétrole WTI (Variation mensuelle)
OIL_MONTHLY_ALERT_PCT = 5.0      # +5% à +20% : Hausse modérée
OIL_MONTHLY_PARABOLIC_PCT = 20.0 # > +20% : Hausse parabolique

# ==============================================================================
# --- 4. Filtres de Conformité Sharia (Normes AAOIFI / MSCI Islamic) ---
# ==============================================================================

SHARIA_MAX_DEBT_RATIO = 0.33        # Dette totale portant intérêt / Capitalisation boursière < 33%
SHARIA_MAX_CASH_RATIO = 0.33        # Liquidités & Placements rémunérés / Capitalisation boursière < 33%
SHARIA_MAX_RECEIVABLES_RATIO = 0.33 # Créances clients / Capitalisation boursière < 33%
SHARIA_MAX_ILLICIT_REVENUE_PCT = 0.05 # Revenus non conformes tolérés < 5%

# Secteurs & activités prohibés (Business Screen)
SHARIA_EXCLUDED_INDUSTRIES = [
    "bank", "financial services", "insurance", "conventional", "brewery", "distillery", 
    "alcohol", "gambling", "casino", "defense", "military", "weapon", "tobacco", 
    "entertainment", "media", "adult entertainment", "pork"
]

# ==============================================================================
# --- 5. Intégration Google Sheets ---
# ==============================================================================

GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", str(BASE_DIR / "credentials.json"))
GOOGLE_SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID", "")
GOOGLE_SHEET_NAME_WATCHLIST = os.getenv("GOOGLE_SHEET_NAME_WATCHLIST", "Suivi d'Investissement")
GOOGLE_SHEET_NAME_SIGNALS = os.getenv("GOOGLE_SHEET_NAME_SIGNALS", "Signaux")
GOOGLE_SHEET_NAME_POSITIONS = os.getenv("GOOGLE_SHEET_NAME_POSITIONS", "Positions")
GOOGLE_SHEET_NAME_JOURNAL = os.getenv("GOOGLE_SHEET_NAME_JOURNAL", "Journal de Trading")
GOOGLE_SHEET_NAME_TREASURY = os.getenv("GOOGLE_SHEET_NAME_TREASURY", "Trésorerie")

# ==============================================================================
# --- 6. Plafond XTB Commissions à 0% & Tarification ---
# ==============================================================================

# Plafond mensuel de volume de transaction (achats + ventes) à 0% de commission
XTB_MONTHLY_ZERO_COMMISSION_LIMIT = 100_000.0  # 100 000 € par mois civil
# Taux de commission au-delà du plafond mensuel (0.20%, minimum 10 €)
XTB_COMMISSION_RATE_OVER_LIMIT = 0.002
XTB_MIN_COMMISSION_OVER_LIMIT_EUR = 10.0

# ==============================================================================
# --- 7. Intégration API Trading 212 (Multi-Broker & Anti-FIFO) ---
# ==============================================================================

TRADING212_API_KEY = os.getenv("TRADING212_API_KEY", "")
TRADING212_API_SECRET = os.getenv("TRADING212_API_SECRET", "")
TRADING212_ENVIRONMENT = os.getenv("TRADING212_ENVIRONMENT", "live").lower().strip()  # "live" ou "demo"

if TRADING212_ENVIRONMENT == "demo":
    TRADING212_BASE_URL = "https://demo.trading212.com/api/v0"
else:
    TRADING212_BASE_URL = "https://live.trading212.com/api/v0"

