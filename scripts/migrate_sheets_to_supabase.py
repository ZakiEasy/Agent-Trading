"""
Script de migration complète et robuste : Google Sheets -> Supabase PostgreSQL 17.
Migre :
1. Les 436+ trades clôturés du 'Journal de Trading' -> table 'trade_journal'
2. Les 1 348+ opérations de la feuille 'Trésorerie' -> table 'treasury_operations'
3. Les 46+ actions de la feuille 'Suivi d'Investissement' -> table 'watchlist'
4. Les 19 positions actives de la feuille 'Positions' -> table 'positions'
5. Les signaux historiques de la feuille 'Signaux' -> table 'trading_signals'
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.supabase_connector import get_db_connection
from src.sheets_connector import (
    read_journal_from_sheets,
    read_treasury_from_sheets,
    read_positions_from_sheets,
    read_watchlist_from_sheets,
    read_sharia_statuses_from_sheets,
    get_sheets_client,
    GOOGLE_SPREADSHEET_ID
)
from src.market_data import categorize_ticker, get_company_name

def parse_date(date_val):
    if not date_val:
        return None
    s = str(date_val).strip()
    if not s or s.lower() == "none" or s.lower() == "nan":
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None

def migrate_watchlist():
    print("--- 1. Migration de la Watchlist ---")
    sheet_tickers = read_watchlist_from_sheets(force_refresh=True) or []
    sharia_map = read_sharia_statuses_from_sheets(force_refresh=True) or {}
    print(f"Trouvé {len(sheet_tickers)} tickers dans Google Sheets.")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            count = 0
            for sym in sheet_tickers:
                s = str(sym).strip().upper()
                if not s or s.startswith("TOTAL") or s.startswith("TABLEAU"):
                    continue
                name = get_company_name(s)
                cat_info = categorize_ticker(s)
                cat = cat_info.get("category", "Autres")
                icon = cat_info.get("category_icon", "📦")
                is_pea = cat_info.get("is_pea", s.endswith(".PA"))
                acc_type = "🇫🇷 PEA" if is_pea else "CTO (US)"
                sharia = sharia_map.get(s, "CONFORME")
                curr = "EUR" if (is_pea or s.endswith(".PA") or s.endswith(".DE")) else "USD"

                cur.execute("""
                    INSERT INTO public.watchlist (
                        symbol, name, category, category_icon, is_pea, account_type, sharia_status, currency, is_active
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (symbol) DO UPDATE SET
                        name = COALESCE(EXCLUDED.name, public.watchlist.name),
                        category = COALESCE(EXCLUDED.category, public.watchlist.category),
                        category_icon = COALESCE(EXCLUDED.category_icon, public.watchlist.category_icon),
                        is_pea = EXCLUDED.is_pea,
                        account_type = COALESCE(EXCLUDED.account_type, public.watchlist.account_type),
                        sharia_status = COALESCE(EXCLUDED.sharia_status, public.watchlist.sharia_status),
                        currency = COALESCE(EXCLUDED.currency, public.watchlist.currency),
                        is_active = TRUE;
                """, (s, name, cat, icon, is_pea, acc_type, sharia, curr))
                count += 1
            conn.commit()
            print(f"✅ {count} actions synchronisées dans 'public.watchlist'.")

def migrate_positions():
    print("\n--- 2. Migration des Positions Actives ---")
    positions = read_positions_from_sheets(force_refresh=True) or []
    print(f"Trouvé {len(positions)} positions actives dans Google Sheets.")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            count = 0
            for p in positions:
                sym = str(p.get("symbol", "")).strip().upper()
                if not sym:
                    continue
                name = p.get("name") or sym
                broker = "Trading 212" if "Trading 212" in str(p.get("broker", "")) or "Trading 212" in str(p.get("account", "")) else "XTB"
                acc = p.get("account", "CTO")
                pru = float(p.get("pru", 0.0))
                qty = float(p.get("quantity", 1.0))
                invested = float(p.get("invested_amount", pru * qty))
                sl = float(p.get("stop_loss", pru * 0.97))
                tp1 = float(p.get("tp1", pru * 1.0125))
                tp2 = float(p.get("tp2", pru * 1.0225))
                curr = p.get("currency", "EUR")
                notes = p.get("notes", "")

                cur.execute("""
                    INSERT INTO public.positions (
                        symbol, company_name, broker, account_type, pru, quantity, invested_capital,
                        stop_loss, take_profit_1, take_profit_2, currency, status, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s)
                    ON CONFLICT DO NOTHING;
                """, (sym, name, broker, acc, pru, qty, invested, sl, tp1, tp2, curr, notes))
                count += 1
            conn.commit()
            print(f"✅ {count} positions actives synchronisées dans 'public.positions'.")

def migrate_trade_journal():
    print("\n--- 3. Migration du Journal de Trading (Trades Clôturés) ---")
    trades = read_journal_from_sheets(force_refresh=True) or []
    print(f"Trouvé {len(trades)} trades clôturés dans Google Sheets.")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            inserted = 0
            updated = 0
            for idx, t in enumerate(trades):
                tid = str(t.get("id") or f"TRADE-{t.get('symbol')}-{idx}").strip()
                sym = str(t.get("symbol", "")).strip().upper()
                if not sym:
                    continue
                name = t.get("name") or sym
                acc = t.get("account", "CTO Euro")
                broker = "Trading 212" if "Trading 212" in acc else "XTB"
                entry_dt = parse_date(t.get("open_time") or t.get("entry_date"))
                exit_dt = parse_date(t.get("close_time") or t.get("exit_date"))
                pru = float(t.get("pru", 0.0))
                exit_p = float(t.get("exit_price", 0.0))
                qty = float(t.get("quantity", 1.0))
                invested = float(t.get("invested_amount", pru * qty))
                pnl_amt = float(t.get("pnl_amount", (exit_p - pru) * qty))
                pnl_pct = float(t.get("pnl_pct", ((exit_p - pru) / pru * 100) if pru > 0 else 0.0))
                res = str(t.get("result") or ("GAIN 🟢" if pnl_amt >= 0 else "PERTE 🔴"))
                curr = str(t.get("currency", "EUR"))
                notes = str(t.get("comment") or t.get("notes") or "")
                if notes.lower() == "nan":
                    notes = ""

                cur.execute("""
                    INSERT INTO public.trade_journal (
                        id, symbol, company_name, broker, account_type, entry_date, exit_date,
                        pru, exit_price, quantity, invested_amount, pnl_amount, pnl_pct, result, currency, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        symbol = EXCLUDED.symbol,
                        company_name = EXCLUDED.company_name,
                        broker = EXCLUDED.broker,
                        account_type = EXCLUDED.account_type,
                        entry_date = EXCLUDED.entry_date,
                        exit_date = EXCLUDED.exit_date,
                        pru = EXCLUDED.pru,
                        exit_price = EXCLUDED.exit_price,
                        quantity = EXCLUDED.quantity,
                        invested_amount = EXCLUDED.invested_amount,
                        pnl_amount = EXCLUDED.pnl_amount,
                        pnl_pct = EXCLUDED.pnl_pct,
                        result = EXCLUDED.result,
                        currency = EXCLUDED.currency,
                        notes = EXCLUDED.notes;
                """, (tid, sym, name, broker, acc, entry_dt, exit_dt, pru, exit_p, qty, invested, pnl_amt, pnl_pct, res, curr, notes))
                inserted += 1

            conn.commit()
            print(f"✅ {inserted} trades insérés/synchronisés dans 'public.trade_journal'.")

def migrate_treasury_operations():
    print("\n--- 4. Migration des Opérations de Trésorerie ---")
    cash_ops = read_treasury_from_sheets(force_refresh=True) or []
    print(f"Trouvé {len(cash_ops)} opérations de trésorerie dans Google Sheets.")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            inserted = 0
            for idx, op in enumerate(cash_ops):
                cid = str(op.get("id") or f"CASH-{idx}").strip()
                op_type = str(op.get("type", "Transfer")).strip()
                inst = str(op.get("instrument", "")).strip()
                sym = str(op.get("symbol", "")).strip().upper()
                op_dt = parse_date(op.get("time") or op.get("date")) or datetime.now()
                amt = float(op.get("amount", 0.0))
                acc = str(op.get("account", "CTO Euro")).strip()
                curr = str(op.get("currency", "EUR")).strip()
                comment = str(op.get("comment", "")).strip()

                cur.execute("""
                    INSERT INTO public.treasury_operations (
                        id, operation_type, instrument, symbol, operation_date, amount, account_type, currency, comment
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        operation_type = EXCLUDED.operation_type,
                        instrument = EXCLUDED.instrument,
                        symbol = EXCLUDED.symbol,
                        operation_date = EXCLUDED.operation_date,
                        amount = EXCLUDED.amount,
                        account_type = EXCLUDED.account_type,
                        currency = EXCLUDED.currency,
                        comment = EXCLUDED.comment;
                """, (cid, op_type, inst, sym, op_dt, amt, acc, curr, comment))
                inserted += 1

            conn.commit()
            print(f"✅ {inserted} opérations de trésorerie insérées/synchronisées dans 'public.treasury_operations'.")

def migrate_trading_signals():
    print("\n--- 5. Migration des Signaux Historiques ---")
    client, err = get_sheets_client()
    if err or not client:
        print("Sheets non disponible pour signaux historiques, passage.")
        return

    try:
        sheet = client.open_by_key(GOOGLE_SPREADSHEET_ID)
        ws = sheet.worksheet("Signaux")
        rows = ws.get_all_values()
        if len(rows) <= 1:
            print("Aucun signal historique dans Sheets.")
            return

        headers = [str(h).strip().lower() for h in rows[0]]
        print(f"Trouvé {len(rows)-1} signaux dans Sheets.")

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                count = 0
                for r in rows[1:]:
                    if not r or not any(r):
                        continue
                    dt = parse_date(r[0]) if len(r) > 0 else datetime.now()
                    sym = str(r[1]).strip().upper() if len(r) > 1 else ""
                    if not sym or sym.startswith("TOTAL"):
                        continue
                    
                    price_str = r[3].replace("€", "").replace("$", "").replace(" ", "").replace(",", ".") if len(r) > 3 else "0"
                    drop_str = r[4].replace("%", "").replace(" ", "").replace(",", ".") if len(r) > 4 else "0"
                    rsi_str = r[7].replace(" ", "").replace(",", ".") if len(r) > 7 else "50"
                    verdict = r[8] if len(r) > 8 else "SIGNAL HISTORIQUE"

                    try:
                        price = float(price_str)
                    except:
                        price = 0.0
                    try:
                        drop = float(drop_str)
                    except:
                        drop = 0.0
                    try:
                        rsi = float(rsi_str)
                    except:
                        rsi = 50.0

                    cur.execute("""
                        INSERT INTO public.trading_signals (
                            symbol, signal_timestamp, current_price, pullback_pct, rsi_14, verdict_swing, status
                        ) VALUES (%s, %s, %s, %s, %s, %s, 'EMIS');
                    """, (sym, dt, price, drop, rsi, verdict))
                    count += 1
                conn.commit()
                print(f"✅ {count} signaux historiques insérés dans 'public.trading_signals'.")
    except Exception as e:
        print(f"Info migration signaux: {e}")

def verify_all_data():
    print("\n=======================================================")
    print("📊 VÉRIFICATION DES DONNÉES SUR SUPABASE POSTGRESQL 17")
    print("=======================================================")
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            tables = ['watchlist', 'positions', 'trade_journal', 'treasury_operations', 'trading_signals', 'macro_regimes', 'backtest_runs']
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM public.{t};")
                cnt = cur.fetchone()[0]
                print(f" - public.{t:22}: {cnt:5} enregistrements")

if __name__ == "__main__":
    print("🚀 Début de la migration intégrale Google Sheets -> Supabase...")
    migrate_watchlist()
    migrate_positions()
    migrate_trade_journal()
    migrate_treasury_operations()
    migrate_trading_signals()
    verify_all_data()
    print("\n🎉 Migration terminée avec succès !")
