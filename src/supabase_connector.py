"""
Module de Connexion & Gestion de Base de Données Supabase (PostgreSQL 17)
Fournit un accès persistant et performant pour la Watchlist, le Portefeuille,
les Signaux de Trading et les Régimes Macroéconomiques.
"""

import os
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Configuration Supabase
SUPABASE_PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF", "wszevrqncyusohdmiswe")
SUPABASE_URL = os.getenv("SUPABASE_URL", f"https://{SUPABASE_PROJECT_REF}.supabase.co")
SUPABASE_DB_HOST = os.getenv("SUPABASE_DB_HOST", f"db.{SUPABASE_PROJECT_REF}.supabase.co")
SUPABASE_DB_PORT = int(os.getenv("SUPABASE_DB_PORT", "5432"))
SUPABASE_DB_NAME = os.getenv("SUPABASE_DB_NAME", "postgres")
SUPABASE_DB_USER = os.getenv("SUPABASE_DB_USER", "postgres")
SUPABASE_DB_PASSWORD = os.getenv("SUPABASE_DB_PASSWORD", "LAk4UUk@Tfs@9qC")

def get_db_connection():
    """Crée et retourne une connexion PostgreSQL directe à Supabase."""
    return psycopg2.connect(
        host=SUPABASE_DB_HOST,
        port=SUPABASE_DB_PORT,
        user=SUPABASE_DB_USER,
        password=SUPABASE_DB_PASSWORD,
        dbname=SUPABASE_DB_NAME,
        connect_timeout=10
    )


# ==============================================================================
# --- 1. GESTION DE LA WATCHLIST ---
# ==============================================================================

def get_supabase_watchlist(only_active=True):
    """Récupère la liste des tickers suivis depuis Supabase."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = "SELECT * FROM public.watchlist"
                if only_active:
                    query += " WHERE is_active = TRUE"
                query += " ORDER BY symbol ASC;"
                cur.execute(query)
                return cur.fetchall()
    except Exception as e:
        print(f"⚠️ Erreur get_supabase_watchlist: {e}")
        return []


def add_or_update_watchlist_item(symbol, name=None, category=None, category_icon=None, 
                                is_pea=False, account_type=None, sharia_status=None, 
                                currency="EUR", notes=None):
    """Ajoute ou met à jour une action dans la Watchlist Supabase."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO public.watchlist (
                        symbol, name, category, category_icon, is_pea, account_type, sharia_status, currency, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol) DO UPDATE SET
                        name = COALESCE(EXCLUDED.name, public.watchlist.name),
                        category = COALESCE(EXCLUDED.category, public.watchlist.category),
                        category_icon = COALESCE(EXCLUDED.category_icon, public.watchlist.category_icon),
                        is_pea = EXCLUDED.is_pea,
                        account_type = COALESCE(EXCLUDED.account_type, public.watchlist.account_type),
                        sharia_status = COALESCE(EXCLUDED.sharia_status, public.watchlist.sharia_status),
                        currency = COALESCE(EXCLUDED.currency, public.watchlist.currency),
                        notes = COALESCE(EXCLUDED.notes, public.watchlist.notes),
                        is_active = TRUE
                    RETURNING *;
                """, (symbol.upper(), name, category, category_icon or '📦', is_pea, account_type or ('🇫🇷 PEA' if is_pea else 'CTO (US)'), sharia_status or 'DONNÉES INSUFFISANTES', currency, notes))
                conn.commit()
                return cur.fetchone()
    except Exception as e:
        print(f"⚠️ Erreur add_or_update_watchlist_item ({symbol}): {e}")
        return None


def delete_from_watchlist(symbol):
    """Désactive ou supprime une action de la Watchlist."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM public.watchlist WHERE symbol = %s;", (symbol.upper(),))
                conn.commit()
                return True
    except Exception as e:
        print(f"⚠️ Erreur delete_from_watchlist ({symbol}): {e}")
        return False


# ==============================================================================
# --- 2. GESTION DES POSITIONS & DU PORTEFEUILLE ---
# ==============================================================================

def get_supabase_positions(status="ACTIVE"):
    """Récupère les positions du portefeuille selon leur statut."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if status == "ALL":
                    cur.execute("SELECT * FROM public.positions ORDER BY opened_at DESC;")
                else:
                    cur.execute("SELECT * FROM public.positions WHERE status = %s ORDER BY opened_at DESC;", (status,))
                return cur.fetchall()
    except Exception as e:
        print(f"⚠️ Erreur get_supabase_positions: {e}")
        return []


def save_or_update_position(pos_data):
    """Enregistre ou met à jour une position dans Supabase."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO public.positions (
                        symbol, company_name, broker, account_type, pru, quantity, invested_capital,
                        current_price, stop_loss, take_profit_1, take_profit_2, r_max_amount, r_max_pct,
                        currency, status, unrealized_pnl_eur, unrealized_pnl_pct, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *;
                """, (
                    pos_data.get("symbol", "").upper(),
                    pos_data.get("company_name"),
                    pos_data.get("broker", "Trading 212"),
                    pos_data.get("account_type", "CTO"),
                    pos_data.get("pru", 0.0),
                    pos_data.get("quantity", 0.0),
                    pos_data.get("invested_capital", 0.0),
                    pos_data.get("current_price"),
                    pos_data.get("stop_loss", 0.0),
                    pos_data.get("take_profit_1"),
                    pos_data.get("take_profit_2"),
                    pos_data.get("r_max_amount"),
                    pos_data.get("r_max_pct", 1.0),
                    pos_data.get("currency", "EUR"),
                    pos_data.get("status", "ACTIVE"),
                    pos_data.get("unrealized_pnl_eur", 0.0),
                    pos_data.get("unrealized_pnl_pct", 0.0),
                    pos_data.get("notes")
                ))
                conn.commit()
                return cur.fetchone()
    except Exception as e:
        print(f"⚠️ Erreur save_or_update_position: {e}")
        return None


# ==============================================================================
# --- 3. GESTION DES SIGNAUX & SCANS ---
# ==============================================================================

def log_trading_signal(sig):
    """Enregistre un signal émis par le moteur de scan protocolaire."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO public.trading_signals (
                        symbol, company_name, strategy, verdict_swing, verdict_swing_badge,
                        verdict_sniper, verdict_sniper_badge, confluence_score, current_price,
                        pullback_pct, rsi_14, atr_14_d1, m15_range, m15_ratio_atr_pct,
                        entry_target, stop_loss, take_profit_1, take_profit_2, rr_ratio,
                        execution_phase, ideal_execution_time, max_execution_time, action_plan
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                """, (
                    sig.get("symbol", "").upper(),
                    sig.get("company_name", sig.get("name")),
                    sig.get("strategy", "v3_institutional"),
                    sig.get("verdict_swing"),
                    sig.get("verdict_swing_badge"),
                    sig.get("verdict_sniper"),
                    sig.get("verdict_sniper_badge"),
                    sig.get("confluence_score"),
                    sig.get("current_price", sig.get("price")),
                    sig.get("pullback_pct", sig.get("drop")),
                    sig.get("rsi_14", sig.get("rsi")),
                    sig.get("atr_14_d1"),
                    sig.get("m15_range"),
                    sig.get("m15_ratio_atr_pct"),
                    sig.get("entry_target"),
                    sig.get("stop_loss"),
                    sig.get("take_profit_1"),
                    sig.get("take_profit_2"),
                    sig.get("rr_ratio"),
                    sig.get("execution_phase"),
                    sig.get("ideal_execution_time"),
                    sig.get("max_execution_time"),
                    sig.get("action_plan")
                ))
                conn.commit()
                return cur.fetchone()
    except Exception as e:
        print(f"⚠️ Erreur log_trading_signal ({sig.get('symbol')}): {e}")
        return None


def get_recent_signals(limit=30):
    """Récupère les derniers signaux générés."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM public.trading_signals
                    ORDER BY signal_timestamp DESC
                    LIMIT %s;
                """, (limit,))
                return cur.fetchall()
    except Exception as e:
        print(f"⚠️ Erreur get_recent_signals: {e}")
        return []


# ==============================================================================
# --- 4. GESTION DU BAROMÈTRE MACRO & PARAMÈTRES ---
# ==============================================================================

def log_macro_regime(macro_data):
    """Enregistre une lecture du baromètre macro."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO public.macro_regimes (
                        regime, vix_value, vix_status, dxy_value, dxy_status,
                        wti_value, wti_status, yield_spread_10y_2y, yield_curve_status,
                        spy_trend_200d, composite_score, action_rule, summary
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                """, (
                    macro_data.get("regime", "NEUTRE"),
                    macro_data.get("vix_value"),
                    macro_data.get("vix_status"),
                    macro_data.get("dxy_value"),
                    macro_data.get("dxy_status"),
                    macro_data.get("wti_value"),
                    macro_data.get("wti_status"),
                    macro_data.get("yield_spread_10y_2y"),
                    macro_data.get("yield_curve_status"),
                    macro_data.get("spy_trend_200d"),
                    macro_data.get("composite_score"),
                    macro_data.get("action_rule"),
                    macro_data.get("summary")
                ))
                conn.commit()
                return cur.fetchone()
    except Exception as e:
        print(f"⚠️ Erreur log_macro_regime: {e}")
        return None
