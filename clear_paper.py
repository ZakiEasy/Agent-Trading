from src.db_connector import get_db_connection

try:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.paper_trading_positions;")
            # Optionnel: on reset le compte
            cur.execute("""
                UPDATE public.paper_trading_account 
                SET balance_eur = 6895.47, balance_usd = 10000.00, updated_at = NOW()
            """)
        conn.commit()
    print("Paper trading tables cleared and account reset.")
except Exception as e:
    print(e)
