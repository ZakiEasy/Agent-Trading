"""
Module de Connexion & Gestion de Base de Données Supabase (PostgreSQL 17)
Fournit un accès persistant, performant et centralisé pour :
- La Watchlist (Univers de Surveillance & Conformité Sharia)
- Le Portefeuille & Positions Actives (Multi-Brokers)
- Le Journal de Trading (Historique complet des trades clôturés)
- La Trésorerie (Opérations de cash, dépôts, retraits, dividendes)
- Les Signaux de Trading & Baromètre Macroéconomique
"""

import os
import time
import json
from datetime import datetime
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None
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
    if not psycopg2:
        raise RuntimeError("psycopg2 non installé")
    return psycopg2.connect(
        host=SUPABASE_DB_HOST,
        port=SUPABASE_DB_PORT,
        user=SUPABASE_DB_USER,
        password=SUPABASE_DB_PASSWORD,
        dbname=SUPABASE_DB_NAME,
        connect_timeout=10
    )


def _get_xtb_snapshot_data():
    """Charge les données consolidées depuis data/xtb_history_snapshot.json en tant que fallback persistant."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    snap_path = os.path.join(base_dir, "data", "xtb_history_snapshot.json")
    if os.path.exists(snap_path):
        try:
            with open(snap_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Erreur lecture snapshot XTB ({snap_path}): {e}")
    return {}


def _save_xtb_snapshot_data(patch_dict):
    """Met à jour atomiquement les clés du snapshot local data/xtb_history_snapshot.json."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    snap_path = os.path.join(base_dir, "data", "xtb_history_snapshot.json")
    data = _get_xtb_snapshot_data() or {}
    data.update(patch_dict)
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    try:
        os.makedirs(os.path.dirname(snap_path), exist_ok=True)
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ Erreur écriture snapshot XTB ({snap_path}): {e}")
        return False


# ==============================================================================
# --- 1. GESTION DE LA WATCHLIST ---
# ==============================================================================

def get_supabase_watchlist(only_active=True):
    """Récupère la liste des actions suivies depuis Supabase."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = "SELECT * FROM public.watchlist"
                if only_active:
                    query += " WHERE is_active = TRUE"
                query += " ORDER BY symbol ASC;"
                cur.execute(query)
                return cur.fetchall() or []
    except Exception as e:
        print(f"⚠️ Erreur get_supabase_watchlist: {e}")
        return []


def get_watchlist_symbols(only_active=True):
    """Récupère uniquement la liste des symboles/tickers sous forme de list[str]."""
    items = get_supabase_watchlist(only_active=only_active)
    return [item["symbol"].upper() for item in items if item.get("symbol")]


def get_watchlist_item(symbol):
    """Récupère une action spécifique de la Watchlist par son symbole."""
    if not symbol:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM public.watchlist WHERE UPPER(symbol) = %s LIMIT 1;", (str(symbol).upper().strip(),))
                return cur.fetchone()
    except Exception as e:
        print(f"⚠️ Erreur get_watchlist_item ({symbol}): {e}")
        return None


def add_or_update_watchlist_item(symbol, name=None, category=None, category_icon=None, 
                                is_pea=False, account_type=None, sharia_status=None, 
                                sharia_source="AAOIFI (Agent Trading)", currency="EUR", notes=None):
    """Ajoute ou met à jour une action dans la Watchlist Supabase."""
    s = str(symbol or "").upper().strip()
    if not s:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO public.watchlist (
                        symbol, name, category, category_icon, is_pea, account_type, sharia_status, sharia_source, currency, notes, is_active
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (symbol) DO UPDATE SET
                        name = COALESCE(EXCLUDED.name, public.watchlist.name),
                        category = COALESCE(EXCLUDED.category, public.watchlist.category),
                        category_icon = COALESCE(EXCLUDED.category_icon, public.watchlist.category_icon),
                        is_pea = EXCLUDED.is_pea,
                        account_type = COALESCE(EXCLUDED.account_type, public.watchlist.account_type),
                        sharia_status = COALESCE(EXCLUDED.sharia_status, public.watchlist.sharia_status),
                        sharia_source = COALESCE(EXCLUDED.sharia_source, public.watchlist.sharia_source),
                        currency = COALESCE(EXCLUDED.currency, public.watchlist.currency),
                        notes = COALESCE(EXCLUDED.notes, public.watchlist.notes),
                        is_active = TRUE
                    RETURNING *;
                """, (
                    s,
                    name or s,
                    category or 'Autres',
                    category_icon or '📦',
                    is_pea,
                    account_type or ('🇫🇷 PEA' if is_pea else 'CTO (US)'),
                    sharia_status or 'DONNÉES INSUFFISANTES',
                    sharia_source,
                    currency,
                    notes
                ))
                conn.commit()
                return cur.fetchone()
    except Exception as e:
        print(f"⚠️ Erreur add_or_update_watchlist_item ({symbol}): {e}")
        return None


def delete_from_watchlist(symbol):
    """Supprime définitivement une action de la Watchlist Supabase et nettoie Google Sheets & caches."""
    s = str(symbol or "").upper().strip()
    if not s:
        return False
    from src.market_data import resolve_ticker_symbol
    resolved = resolve_ticker_symbol(s)
    
    db_success = False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM public.watchlist 
                    WHERE UPPER(symbol) = %s 
                       OR UPPER(symbol) = %s 
                       OR UPPER(name) ILIKE %s;
                """, (s, resolved, f"%{s}%"))
                conn.commit()
                db_success = True
    except Exception as e:
        print(f"⚠️ Erreur delete_from_watchlist BDD ({symbol}): {e}")
        
    # Nettoyer également Google Sheets et les caches locaux en mémoire
    try:
        from src.sheets_connector import delete_ticker_from_sheets
        delete_ticker_from_sheets(s)
        if resolved != s:
            delete_ticker_from_sheets(resolved)
    except Exception as e:
        print(f"⚠️ Erreur delete_ticker_from_sheets ({symbol}): {e}")

    return True


# ==============================================================================
# --- 2. GESTION DES POSITIONS DU PORTEFEUILLE (ACTIF / LIVE) ---
# ==============================================================================

def get_supabase_positions(status="ACTIVE"):
    """Récupère les positions du portefeuille selon leur statut (ACTIVE, CLOSED, ALL)."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if status == "ALL":
                    cur.execute("SELECT * FROM public.positions ORDER BY opened_at DESC;")
                else:
                    cur.execute("SELECT * FROM public.positions WHERE status = %s ORDER BY opened_at DESC;", (status,))
                rows = cur.fetchall() or []
                # Formater les champs pour compatibilité portefeuille
                for r in rows:
                    if r.get("id"):
                        r["id"] = str(r["id"])
                if rows:
                    return rows
    except Exception as e:
        print(f"⚠️ Erreur get_supabase_positions: {e}")

    # Fallback local JSON snapshot
    snap = _get_xtb_snapshot_data()
    open_pos = snap.get("open_positions", [])
    if open_pos:
        formatted = []
        for p in open_pos:
            st = p.get("status", "ACTIVE")
            if status != "ALL" and st != status:
                continue
            formatted.append({
                "id": str(p.get("id")),
                "symbol": p.get("symbol"),
                "company_name": p.get("name") or p.get("company_name") or p.get("symbol"),
                "broker": p.get("broker", "XTB"),
                "account_type": p.get("account") or p.get("account_type", "CTO Euro"),
                "pru": float(p.get("pru", 0.0)),
                "quantity": float(p.get("quantity", 0.0)),
                "invested_capital": float(p.get("invested_amount") or p.get("invested_capital", 0.0)),
                "current_price": float(p.get("current_price") or p.get("pru", 0.0)),
                "stop_loss": float(p.get("stop_loss", 0.0)),
                "take_profit_1": float(p.get("tp1") or p.get("take_profit_1", 0.0)),
                "take_profit_2": float(p.get("tp2") or p.get("take_profit_2", 0.0)),
                "currency": p.get("currency", "EUR"),
                "status": st,
                "notes": p.get("notes", "")
            })
        return formatted

    return []


def save_or_update_position(pos_data):
    """Enregistre ou met à jour une position active dans Supabase."""
    try:
        sym = str(pos_data.get("symbol", "")).upper().strip()
        if not sym:
            return None
            
        pru = float(pos_data.get("pru", 0.0))
        qty = float(pos_data.get("quantity", 1.0))
        invested = float(pos_data.get("invested_capital") or pos_data.get("invested_amount") or (pru * qty))
        sl = float(pos_data.get("stop_loss", pru * 0.97))
        tp1 = float(pos_data.get("take_profit_1") or pos_data.get("tp1") or (pru * 1.0125))
        tp2 = float(pos_data.get("take_profit_2") or pos_data.get("tp2") or (pru * 1.0225))

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
                    sym,
                    pos_data.get("company_name") or pos_data.get("name") or sym,
                    pos_data.get("broker", "XTB"),
                    pos_data.get("account_type") or pos_data.get("account", "CTO"),
                    pru,
                    qty,
                    invested,
                    float(pos_data.get("current_price") or pru),
                    sl,
                    tp1,
                    tp2,
                    pos_data.get("r_max_amount"),
                    float(pos_data.get("r_max_pct", 1.0)),
                    pos_data.get("currency", "EUR"),
                    pos_data.get("status", "ACTIVE"),
                    float(pos_data.get("unrealized_pnl_eur", 0.0)),
                    float(pos_data.get("unrealized_pnl_pct", 0.0)),
                    pos_data.get("notes") or pos_data.get("comment", "")
                ))
                conn.commit()
                res = cur.fetchone()
                if res and res.get("id"):
                    res["id"] = str(res["id"])
                return res
    except Exception as e:
        print(f"⚠️ Erreur save_or_update_position: {e}")
        return None


def batch_save_positions(open_positions):
    """
    Synchronise par lot la liste des positions actives dans Supabase (remplace les actives existantes).
    """
    if not open_positions:
        return True, "Aucune position active à enregistrer."
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Supprimer les anciennes positions actives avant d'insérer le nouvel état agrégé
                cur.execute("DELETE FROM public.positions WHERE status = 'ACTIVE';")
                for p in open_positions:
                    sym = str(p.get("symbol", "")).upper().strip()
                    if not sym:
                        continue
                    pru = float(p.get("pru", 0.0))
                    qty = float(p.get("quantity", 1.0))
                    invested = float(p.get("invested_amount") or p.get("invested_capital") or (pru * qty))
                    sl = float(p.get("stop_loss", pru * 0.97))
                    tp1 = float(p.get("tp1") or p.get("take_profit_1") or (pru * 1.0125))
                    tp2 = float(p.get("tp2") or p.get("take_profit_2") or (pru * 1.0225))
                    broker = p.get("broker") or ("Trading 212" if "Trading 212" in str(p.get("account", "")) else "XTB")
                    acc = p.get("account") or p.get("account_type", "CTO")
                    cur.execute("""
                        INSERT INTO public.positions (
                            symbol, company_name, broker, account_type, pru, quantity, invested_capital,
                            stop_loss, take_profit_1, take_profit_2, currency, status, notes
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s);
                    """, (
                        sym,
                        p.get("name") or sym,
                        broker,
                        acc,
                        pru,
                        qty,
                        invested,
                        sl,
                        tp1,
                        tp2,
                        p.get("currency", "EUR"),
                        p.get("notes", "")
                    ))
                conn.commit()
                # Sauvegarder également dans le snapshot local
                _save_xtb_snapshot_data({"open_positions": open_positions})
                return True, f"{len(open_positions)} positions actives synchronisées en base de données !"
    except Exception as e:
        print(f"⚠️ Erreur batch_save_positions BDD (bascule snapshot local): {e}")
        _save_xtb_snapshot_data({"open_positions": open_positions})
        return True, f"{len(open_positions)} positions actives sauvegardées dans le snapshot local (BDD hors-ligne) !"


def close_supabase_position(pos_id_or_symbol, exit_price, exit_date=None, notes=""):
    """
    Clôture une position active dans Supabase et archive le trade clôturé dans 'trade_journal'.
    """
    try:
        now_dt = datetime.now()
        exit_p = float(exit_price)
        target_pos = None

        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Trouver la position
                cur.execute("""
                    SELECT * FROM public.positions 
                    WHERE (id::text = %s OR UPPER(symbol) = %s) AND status = 'ACTIVE'
                    LIMIT 1;
                """, (str(pos_id_or_symbol), str(pos_id_or_symbol).upper()))
                target_pos = cur.fetchone()

                if not target_pos:
                    return False, f"Position active '{pos_id_or_symbol}' introuvable."

                pru = float(target_pos.get("pru", 0.0))
                qty = float(target_pos.get("quantity", 1.0))
                pnl_amt = (exit_p - pru) * qty
                pnl_pct = ((exit_p - pru) / pru * 100) if pru > 0 else 0.0
                res_str = "GAIN 🟢" if pnl_amt >= 0 else "PERTE 🔴"
                sym = target_pos["symbol"].upper()

                # 2. Mettre à jour le statut dans positions
                cur.execute("""
                    UPDATE public.positions SET
                        status = 'CLOSED',
                        closed_at = NOW(),
                        realized_pnl_eur = %s,
                        realized_pnl_pct = %s,
                        notes = COALESCE(notes, '') || ' | Clôturé à ' || %s || ' €'
                    WHERE id = %s;
                """, (pnl_amt, pnl_pct, exit_p, target_pos["id"]))

                # 3. Insérer dans trade_journal
                journal_id = f"CLO-{sym}-{int(now_dt.timestamp())}"
                cur.execute("""
                    INSERT INTO public.trade_journal (
                        id, symbol, company_name, broker, account_type, entry_date, exit_date,
                        pru, exit_price, quantity, invested_amount, pnl_amount, pnl_pct, result, currency, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING;
                """, (
                    journal_id,
                    sym,
                    target_pos.get("company_name", sym),
                    target_pos.get("broker", "XTB"),
                    target_pos.get("account_type", "CTO"),
                    target_pos.get("opened_at") or now_dt,
                    now_dt,
                    pru,
                    exit_p,
                    qty,
                    pru * qty,
                    pnl_amt,
                    pnl_pct,
                    res_str,
                    target_pos.get("currency", "EUR"),
                    notes or target_pos.get("notes", "")
                ))
                conn.commit()
                return True, f"Position {sym} clôturée avec succès en BDD ! P&L : {pnl_amt:+.2f} € ({pnl_pct:+.2f}%)"
    except Exception as e:
        print(f"⚠️ Erreur close_supabase_position: {e}")
        return False, str(e)


# ==============================================================================
# --- 3. GESTION DU JOURNAL DE TRADING (TRADES CLÔTURÉS) ---
# ==============================================================================

def get_supabase_trade_journal(account_type=None, limit=None):
    """
    Récupère la liste complète des trades clôturés depuis la table 'public.trade_journal'.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = "SELECT * FROM public.trade_journal"
                params = []
                if account_type:
                    query += " WHERE account_type = %s"
                    params.append(account_type)
                query += " ORDER BY exit_date DESC NULLS LAST, created_at DESC"
                if limit and int(limit) > 0:
                    query += f" LIMIT {int(limit)}"
                query += ";"
                cur.execute(query, tuple(params))
                rows = cur.fetchall() or []
                
                # Normaliser pour sérialisation JSON
                trades = []
                for r in rows:
                    trades.append({
                        "id": str(r.get("id")),
                        "symbol": r.get("symbol", "").upper(),
                        "name": r.get("company_name") or r.get("symbol"),
                        "open_time": r["entry_date"].strftime("%Y-%m-%d %H:%M:%S") if r.get("entry_date") else "",
                        "close_time": r["exit_date"].strftime("%Y-%m-%d %H:%M:%S") if r.get("exit_date") else "",
                        "entry_date": r["entry_date"].strftime("%Y-%m-%d %H:%M:%S") if r.get("entry_date") else "",
                        "exit_date": r["exit_date"].strftime("%Y-%m-%d %H:%M:%S") if r.get("exit_date") else "",
                        "pru": float(r.get("pru", 0.0)),
                        "exit_price": float(r.get("exit_price", 0.0)),
                        "quantity": float(r.get("quantity", 1.0)),
                        "invested_amount": float(r.get("invested_amount", 0.0)),
                        "pnl_amount": float(r.get("pnl_amount", 0.0)),
                        "pnl_pct": float(r.get("pnl_pct", 0.0)),
                        "result": r.get("result", "GAIN 🟢"),
                        "account": r.get("account_type", "CTO Euro"),
                        "broker": r.get("broker", "XTB"),
                        "currency": r.get("currency", "EUR"),
                        "comment": r.get("notes") or ""
                    })
                if trades:
                    return trades
    except Exception as e:
        print(f"⚠️ Erreur get_supabase_trade_journal: {e}")

    # Fallback local JSON snapshot
    snap = _get_xtb_snapshot_data()
    closed = snap.get("closed_positions", [])
    if closed:
        filtered = []
        for t in closed:
            acc = t.get("account") or t.get("account_type", "CTO Euro")
            if account_type and acc != account_type:
                continue
            filtered.append(t)
        if limit and int(limit) > 0:
            filtered = filtered[:int(limit)]
        return filtered

    return []


def batch_save_trade_journal(trades_list):
    """
    Insère ou met à jour par lot une liste de trades clôturés dans 'public.trade_journal'.
    """
    if not trades_list:
        return True, "Aucun trade à enregistrer."
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                count = 0
                for idx, t in enumerate(trades_list):
                    tid = str(t.get("id") or f"TRADE-{t.get('symbol')}-{idx}").strip()
                    sym = str(t.get("symbol", "")).upper().strip()
                    if not sym:
                        continue
                    pru = float(t.get("pru", 0.0))
                    exit_p = float(t.get("exit_price", 0.0))
                    qty = float(t.get("quantity", 1.0))
                    inv = float(t.get("invested_amount", pru * qty))
                    pnl_amt = float(t.get("pnl_amount", (exit_p - pru) * qty))
                    pnl_pct = float(t.get("pnl_pct", ((exit_p - pru) / pru * 100) if pru > 0 else 0.0))
                    acc = t.get("account") or t.get("account_type", "CTO Euro")
                    broker = t.get("broker") or ("Trading 212" if "Trading 212" in acc else "XTB")
                    
                    cur.execute("""
                        INSERT INTO public.trade_journal (
                            id, symbol, company_name, broker, account_type, entry_date, exit_date,
                            pru, exit_price, quantity, invested_amount, pnl_amount, pnl_pct, result, currency, notes
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            company_name = EXCLUDED.company_name,
                            pru = EXCLUDED.pru,
                            exit_price = EXCLUDED.exit_price,
                            quantity = EXCLUDED.quantity,
                            invested_amount = EXCLUDED.invested_amount,
                            pnl_amount = EXCLUDED.pnl_amount,
                            pnl_pct = EXCLUDED.pnl_pct,
                            result = EXCLUDED.result,
                            notes = EXCLUDED.notes;
                    """, (
                        tid, sym, t.get("name") or sym, broker, acc,
                        t.get("open_time") or t.get("entry_date") or None,
                        t.get("close_time") or t.get("exit_date") or None,
                        pru, exit_p, qty, inv, pnl_amt, pnl_pct,
                        t.get("result", "GAIN 🟢" if pnl_amt >= 0 else "PERTE 🔴"),
                        t.get("currency", "EUR"),
                        t.get("comment") or t.get("notes") or ""
                    ))
                    count += 1
                conn.commit()
                # Sauvegarder également dans le snapshot local
                _save_xtb_snapshot_data({"closed_positions": trades_list})
                return True, f"{count} trades enregistrés dans le Journal en base de données !"
    except Exception as e:
        print(f"⚠️ Erreur batch_save_trade_journal BDD (bascule snapshot local): {e}")
        _save_xtb_snapshot_data({"closed_positions": trades_list})
        return True, f"{len(trades_list)} trades sauvegardés dans le snapshot local (BDD hors-ligne) !"


# ==============================================================================
# --- 4. GESTION DE LA TRÉSORERIE & DES FLUX D'ESPÈCES ---
# ==============================================================================

def get_supabase_treasury_operations(account_type=None, limit=None):
    """
    Récupère les opérations de trésorerie depuis la table 'public.treasury_operations'.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                query = "SELECT * FROM public.treasury_operations"
                params = []
                if account_type:
                    query += " WHERE account_type = %s"
                    params.append(account_type)
                query += " ORDER BY operation_date DESC, created_at DESC"
                if limit and int(limit) > 0:
                    query += f" LIMIT {int(limit)}"
                query += ";"
                cur.execute(query, tuple(params))
                rows = cur.fetchall() or []
                
                ops = []
                for r in rows:
                    ops.append({
                        "id": str(r.get("id")),
                        "type": r.get("operation_type", "Transfer"),
                        "instrument": r.get("instrument") or "",
                        "symbol": (r.get("symbol") or "").upper(),
                        "time": r["operation_date"].strftime("%Y-%m-%d %H:%M:%S") if r.get("operation_date") else "",
                        "date": r["operation_date"].strftime("%Y-%m-%d %H:%M:%S") if r.get("operation_date") else "",
                        "amount": float(r.get("amount", 0.0)),
                        "account": r.get("account_type", "CTO Euro"),
                        "currency": r.get("currency", "EUR"),
                        "comment": r.get("comment") or ""
                    })
                if ops:
                    return ops
    except Exception as e:
        print(f"⚠️ Erreur get_supabase_treasury_operations: {e}")

    # Fallback local JSON snapshot
    snap = _get_xtb_snapshot_data()
    cash_ops = snap.get("cash_operations", [])
    if cash_ops:
        filtered = []
        for op in cash_ops:
            acc = op.get("account") or op.get("account_type", "CTO Euro")
            if account_type and acc != account_type:
                continue
            filtered.append(op)
        if limit and int(limit) > 0:
            filtered = filtered[:int(limit)]
        return filtered

    return []


def batch_save_treasury_operations(cash_operations):
    """
    Insère ou met à jour par lot des opérations de trésorerie dans 'public.treasury_operations'.
    """
    if not cash_operations:
        return True, "Aucune opération de trésorerie à enregistrer."
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                count = 0
                for idx, op in enumerate(cash_operations):
                    cid = str(op.get("id") or f"CASH-{idx}").strip()
                    op_type = str(op.get("type", "Transfer")).strip()
                    amt = float(op.get("amount", 0.0))
                    acc = str(op.get("account", "CTO Euro")).strip()
                    curr = str(op.get("currency", "EUR")).strip()
                    comm = str(op.get("comment", "")).strip()
                    
                    cur.execute("""
                        INSERT INTO public.treasury_operations (
                            id, operation_type, instrument, symbol, operation_date, amount, account_type, currency, comment
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            operation_type = EXCLUDED.operation_type,
                            amount = EXCLUDED.amount,
                            comment = EXCLUDED.comment;
                    """, (
                        cid, op_type, op.get("instrument", ""),
                        (op.get("symbol") or "").upper(),
                        op.get("time") or op.get("date") or datetime.now(),
                        amt, acc, curr, comm
                    ))
                    count += 1
                conn.commit()
                # Sauvegarder également dans le snapshot local
                _save_xtb_snapshot_data({"cash_operations": cash_operations})
                return True, f"{count} opérations de trésorerie synchronisées en base de données !"
    except Exception as e:
        print(f"⚠️ Erreur batch_save_treasury_operations BDD (bascule snapshot local): {e}")
        _save_xtb_snapshot_data({"cash_operations": cash_operations})
        return True, f"{len(cash_operations)} opérations de trésorerie sauvegardées dans le snapshot local (BDD hors-ligne) !"


# ==============================================================================
# --- 5. GESTION DES SIGNAUX & SCANS ---
# ==============================================================================

def log_trading_signal(sig):
    """Enregistre un signal émis par le moteur de scan protocolaire dans Supabase."""
    try:
        sym = str(sig.get("symbol", "")).upper().strip()
        if not sym:
            return None
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
                    sym,
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


def get_recent_signals(limit=50):
    """Récupère les derniers signaux générés."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM public.trading_signals
                    ORDER BY signal_timestamp DESC
                    LIMIT %s;
                """, (limit,))
                rows = cur.fetchall() or []
                for r in rows:
                    if r.get("id"):
                        r["id"] = str(r["id"])
                return rows
    except Exception as e:
        print(f"⚠️ Erreur get_recent_signals: {e}")
        return []


# ==============================================================================
# --- 6. GESTION DU BAROMÈTRE MACRO & PARAMÈTRES ---
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


def get_latest_macro_regime():
    """Récupère le dernier état enregistré du baromètre macro."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM public.macro_regimes ORDER BY recorded_at DESC LIMIT 1;")
                res = cur.fetchone()
                if res and res.get("id"):
                    res["id"] = str(res["id"])
                return res
    except Exception as e:
        print(f"⚠️ Erreur get_latest_macro_regime: {e}")
        return None


def get_app_setting(key, default=None):
    """Récupère un paramètre de configuration depuis Supabase (table public.app_settings)."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT value FROM public.app_settings WHERE key = %s LIMIT 1;", (key,))
                row = cur.fetchone()
                if row and row.get("value") is not None:
                    val = row["value"]
                    if isinstance(val, str):
                        try:
                            return json.loads(val)
                        except Exception:
                            return val
                    return val
                return default
    except Exception as e:
        print(f"⚠️ Erreur get_app_setting ({key}): {e}")
        return default


def save_app_setting(key, value, description=None):
    """Enregistre ou met à jour un paramètre JSON dans Supabase (table public.app_settings)."""
    try:
        val_json = json.dumps(value) if not isinstance(value, str) else value
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO public.app_settings (key, value, description, updated_at)
                    VALUES (%s, %s::jsonb, %s, NOW())
                    ON CONFLICT (key) DO UPDATE 
                    SET value = EXCLUDED.value,
                        description = COALESCE(EXCLUDED.description, public.app_settings.description),
                        updated_at = NOW()
                    RETURNING key;
                """, (key, val_json, description))
                conn.commit()
                return True
    except Exception as e:
        print(f"⚠️ Erreur save_app_setting ({key}): {e}")
        return False


# ==============================================================================
# --- 7. CACHE CENTRALISÉ DE MARCHÉ SUPABASE (CLOUD SYNC) ---
# ==============================================================================

def get_market_data_cache(symbol):
    """Récupère les données de marché en cache Cloud Supabase pour un symbole."""
    s = str(symbol or "").upper().strip()
    if not s:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM public.market_data_cache 
                    WHERE symbol = %s;
                """, (s,))
                row = cur.fetchone()
                if row:
                    return dict(row)
                return None
    except Exception as e:
        return None

def get_all_market_data_cache():
    """Récupère tout le cache de marché Supabase sous forme de dict symbol -> data."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM public.market_data_cache;")
                rows = cur.fetchall() or []
                return {r["symbol"]: dict(r) for r in rows}
    except Exception:
        return {}

def save_market_data_cache(symbol, price=0.0, currency="USD", drop_pct=0.0, rsi=50.0, 
                           avg_daily_volume=0.0, confluence_score=5.0, 
                           verdict_swing="ATTENDRE", verdict_sniper="NON ÉLIGIBLE", 
                           ohlcv_json=None, info_json=None):
    """Sauvegarde ou met à jour les données de marché d'un symbole dans Supabase."""
    s = str(symbol or "").upper().strip()
    if not s:
        return False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.market_data_cache (
                        symbol, price, currency, drop_pct, rsi, avg_daily_volume, 
                        confluence_score, verdict_swing, verdict_sniper, 
                        ohlcv_json, info_json, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, NOW()
                    )
                    ON CONFLICT (symbol) DO UPDATE SET
                        price = EXCLUDED.price,
                        currency = EXCLUDED.currency,
                        drop_pct = EXCLUDED.drop_pct,
                        rsi = EXCLUDED.rsi,
                        avg_daily_volume = EXCLUDED.avg_daily_volume,
                        confluence_score = EXCLUDED.confluence_score,
                        verdict_swing = EXCLUDED.verdict_swing,
                        verdict_sniper = EXCLUDED.verdict_sniper,
                        ohlcv_json = COALESCE(EXCLUDED.ohlcv_json, public.market_data_cache.ohlcv_json),
                        info_json = COALESCE(EXCLUDED.info_json, public.market_data_cache.info_json),
                        updated_at = NOW();
                """, (
                    s, price, currency, drop_pct, rsi, avg_daily_volume, 
                    confluence_score, verdict_swing, verdict_sniper,
                    json.dumps(ohlcv_json) if ohlcv_json else None,
                    json.dumps(info_json) if info_json else None
                ))
                conn.commit()
                return True
    except Exception as e:
        print(f"⚠️ Erreur save_market_data_cache ({s}): {e}")
        return False

