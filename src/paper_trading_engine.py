import os
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from psycopg2.extras import RealDictCursor
from src.db_connector import get_db_connection

logger = logging.getLogger("agent_trading.paper")


def init_paper_trading_db():
    """Crée les tables nécessaires au Paper Trading dans Supabase (PostgreSQL)."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # 1. Table du compte Paper Trading
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.paper_trading_account (
                        id SERIAL PRIMARY KEY,
                        balance_eur NUMERIC DEFAULT 0,
                        balance_usd NUMERIC DEFAULT 0,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    );
                """)

                # Vérifier s'il faut l'initialiser
                cur.execute("SELECT id FROM public.paper_trading_account LIMIT 1;")
                if not cur.fetchone():
                    # Initialisation demandée : 16000€, on change 10000$ (taux estimé 1.10 temporaire)
                    # En production, on peut requêter le vrai taux, mais pour l'init :
                    # 10000 USD = 9090.90 EUR + 0.15% (13.63 EUR) = 9104.53 EUR
                    # 16000 - 9104.53 = 6895.47 EUR
                    # Laissons 6000 EUR et le reste sert de base
                    cur.execute("""
                        INSERT INTO public.paper_trading_account (balance_eur, balance_usd, updated_at)
                        VALUES (6895.47, 10000.00, NOW());
                    """)
                    logger.info(
                        "✅ Compte Paper Trading initialisé (6895.47 €, 10000.00 $)."
                    )

                # 2. Table des positions Paper Trading
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.paper_trading_positions (
                        id SERIAL PRIMARY KEY,
                        proposal_id VARCHAR(255) UNIQUE,
                        symbol VARCHAR(50) NOT NULL,
                        status VARCHAR(50) DEFAULT 'OPEN', -- OPEN, CLOSED, TP1_HIT, STOPPED, CANCELLED
                        entry_price NUMERIC,
                        quantity NUMERIC,
                        nominal_invested NUMERIC,
                        currency VARCHAR(10) DEFAULT 'EUR',
                        stop_loss_price NUMERIC,
                        tp1_price NUMERIC,
                        tp2_price NUMERIC,
                        realized_pnl NUMERIC DEFAULT 0,
                        unrealized_pnl NUMERIC DEFAULT 0,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        closed_at TIMESTAMP WITH TIME ZONE,
                        trade_plan JSONB
                    );
                """)
                conn.commit()
                logger.info("✅ Tables Paper Trading vérifiées/créées.")
    except Exception as e:
        logger.error(f"⚠️ Erreur initialisation Paper Trading DB : {e}", exc_info=True)


def get_paper_account():
    """Récupère l'état du compte Paper Trading."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM public.paper_trading_account LIMIT 1;")
                return cur.fetchone()
    except Exception as e:
        logger.error(f"⚠️ Erreur get_paper_account : {e}")
        return None


def execute_paper_trade(proposal):
    """Exécute virtuellement une proposition de trade sur le compte Paper Trading."""
    try:
        symbol = proposal.get("symbol")
        entry_price = float(proposal.get("entry_price", 0))
        quantity = float(proposal.get("quantity", 0))
        nominal = float(proposal.get("nominal_invested", 0))
        currency = proposal.get("currency", "EUR").upper()

        # 1. Vérification du solde et Gestion du Risque (Rules de Portfolio)
        account = get_paper_account()
        if not account:
            logger.warning(f"❌ Paper Trade rejeté pour {symbol}: Compte Paper Trading introuvable.")
            return {"status": "error", "message": "Compte non trouvé"}

        balance_eur = float(account["balance_eur"])
        balance_usd = float(account["balance_usd"])

        # Le nominal est la taille de la position dans la devise de l'action.
        cost = nominal

        if currency == "USD":
            if balance_usd < cost:
                logger.warning(f"❌ Paper Trade rejeté pour {symbol}: Fonds USD insuffisants (Solde: {balance_usd}$, Requis: {cost}$)")
                return {"status": "error", "message": f"Fonds USD insuffisants ({balance_usd} < {cost})"}
            new_balance_usd = balance_usd - cost
            new_balance_eur = balance_eur
        else:  # EUR
            if balance_eur < cost:
                logger.warning(f"❌ Paper Trade rejeté pour {symbol}: Fonds EUR insuffisants (Solde: {balance_eur}€, Requis: {cost}€)")
                return {"status": "error", "message": f"Fonds EUR insuffisants ({balance_eur} < {cost})"}
            new_balance_eur = balance_eur - cost
            new_balance_usd = balance_usd
            
        # --- RÈGLES DE GESTION DES RISQUES (INSTITUTIONAL) ---
        USD_TO_EUR = 0.91
        
        # Récupération des positions actives pour calculer le portefeuille global
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT nominal_invested, currency, trade_plan FROM public.paper_trading_positions WHERE status IN ('OPEN', 'TP1_HIT');")
                active_positions = cur.fetchall()
                
        invested_eur = 0
        invested_usd = 0
        sector_exposure_eur = {}
        
        for pos in active_positions:
            nom_pos = float(pos[0] or 0)
            cur_pos = pos[1]
            trade_plan_json = pos[2] or {}
            
            # Conversion en EUR pour l'équivalent portefeuille
            nom_pos_eur = nom_pos if cur_pos == "EUR" else nom_pos * USD_TO_EUR
            
            if cur_pos == "USD":
                invested_usd += nom_pos
            else:
                invested_eur += nom_pos
                
            # Cumul de l'exposition sectorielle
            if isinstance(trade_plan_json, dict):
                sector = trade_plan_json.get("sector", "Unknown")
                sector_exposure_eur[sector] = sector_exposure_eur.get(sector, 0) + nom_pos_eur
                
        total_cash_eur_equiv = balance_eur + (balance_usd * USD_TO_EUR)
        total_invested_eur_equiv = invested_eur + (invested_usd * USD_TO_EUR)
        global_portfolio_value_eur = total_cash_eur_equiv + total_invested_eur_equiv
        
        cost_in_eur = cost if currency == "EUR" else cost * USD_TO_EUR
        projected_cash_eur_equiv = total_cash_eur_equiv - cost_in_eur
        
        # Règle 1: Cash Reserve Ratio (25% minimum)
        if projected_cash_eur_equiv < global_portfolio_value_eur * 0.25:
            logger.warning(f"❌ Paper Trade rejeté pour {symbol}: Violation de la réserve de cash. (Résiduel projeté: {projected_cash_eur_equiv:.2f}€ < Min 25%: {global_portfolio_value_eur * 0.25:.2f}€)")
            return {
                "status": "error", 
                "message": f"Violation Risque: Cash reserve résiduel ({projected_cash_eur_equiv:.2f}€) < 25% du portefeuille ({global_portfolio_value_eur * 0.25:.2f}€)"
            }
            
        # Règle 2: Limite de position individuelle (Max 20% par ligne)
        if cost_in_eur > global_portfolio_value_eur * 0.20:
            logger.warning(f"❌ Paper Trade rejeté pour {symbol}: Violation limite position 20%. (Taille: {cost_in_eur:.2f}€ > Max: {global_portfolio_value_eur * 0.20:.2f}€)")
            return {
                "status": "error", 
                "message": f"Violation Risque: Ligne ({cost_in_eur:.2f}€) dépasse le max de 20% du portefeuille ({global_portfolio_value_eur * 0.20:.2f}€)"
            }
            
        # Règle 3: Diversification Devises (Max 60% d'exposition sur une seule devise)
        projected_target_curr_invested_eur = (invested_usd * USD_TO_EUR + cost_in_eur) if currency == "USD" else (invested_eur + cost_in_eur)
        if projected_target_curr_invested_eur > global_portfolio_value_eur * 0.60:
            logger.warning(f"❌ Paper Trade rejeté pour {symbol}: Violation Diversification devise {currency}. (Exp projetée: {projected_target_curr_invested_eur:.2f}€ > Max 60%: {global_portfolio_value_eur * 0.60:.2f}€)")
            return {
                "status": "error", 
                "message": f"Violation Diversification: L'exposition globale sur {currency} dépasserait 60% du portefeuille."
            }
            
        # Règle 4: Diversification Sectorielle (Max 30% par secteur)
        trade_plan_proposal = proposal.get("trade_plan", {})
        proposal_sector = trade_plan_proposal.get("sector", "Unknown")
        
        projected_sector_exposure_eur = sector_exposure_eur.get(proposal_sector, 0) + cost_in_eur
        if projected_sector_exposure_eur > global_portfolio_value_eur * 0.30:
            logger.warning(f"❌ Paper Trade rejeté pour {symbol}: Violation Sectorielle pour {proposal_sector}. (Exp projetée: {projected_sector_exposure_eur:.2f}€ > Max 30%: {global_portfolio_value_eur * 0.30:.2f}€)")
            return {
                "status": "error", 
                "message": f"Violation Sectorielle: Le secteur '{proposal_sector}' dépasserait la limite de 30% du portefeuille."
            }
        # -----------------------------------------------------

        # 2. Mise à jour de la base de données
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Créer la position
                cur.execute(
                    """
                    INSERT INTO public.paper_trading_positions (
                        proposal_id, symbol, status, entry_price, quantity, nominal_invested,
                        currency, stop_loss_price, tp1_price, tp2_price, trade_plan, created_at
                    ) VALUES (%s, %s, 'OPEN', %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (proposal_id) DO NOTHING;
                """,
                    (
                        proposal.get("proposal_id"),
                        symbol,
                        entry_price,
                        quantity,
                        nominal,
                        currency,
                        proposal.get("stop_loss_price"),
                        proposal.get("tp1_price"),
                        proposal.get("tp2_price"),
                        json.dumps(proposal.get("trade_plan", {})),
                    ),
                )

                # Déduire les fonds
                cur.execute(
                    """
                    UPDATE public.paper_trading_account
                    SET balance_eur = %s, balance_usd = %s, updated_at = NOW()
                    WHERE id = %s;
                """,
                    (new_balance_eur, new_balance_usd, account["id"]),
                )

                conn.commit()
                logger.info(
                    f"✅ Paper Trade Exécuté: {symbol} x{quantity} ({nominal} {currency})"
                )
                return {"status": "success", "message": "Position virtuelle ouverte"}
    except Exception as e:
        logger.error(f"⚠️ Erreur execute_paper_trade : {e}")
        return {"status": "error", "message": str(e)}


def update_paper_positions(current_prices):
    """
    Vérifie les positions ouvertes par rapport aux prix actuels du marché (current_prices).
    current_prices est un dict: {'AAPL': 150.2, ...}
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM public.paper_trading_positions WHERE status IN ('OPEN', 'TP1_HIT');"
                )
                positions = cur.fetchall()

                account = get_paper_account()
                if not account:
                    return
                balance_eur = float(account["balance_eur"])
                balance_usd = float(account["balance_usd"])

                for pos in positions:
                    symbol = pos["symbol"]
                    if symbol not in current_prices:
                        continue

                    current_price = current_prices[symbol]
                    sl = float(pos["stop_loss_price"] or 0)
                    tp1 = float(pos["tp1_price"] or 0)
                    tp2 = float(pos["tp2_price"] or 0)
                    status = pos["status"]
                    entry = float(pos["entry_price"])
                    qty = float(pos["quantity"])

                    new_status = status
                    pnl_to_realize = 0
                    cash_to_return = 0

                    # Logique de déclenchement (Long seulement pour l'instant)
                    if current_price <= sl:
                        # Stop Loss Touché
                        new_status = "STOPPED"
                        # On vend toute la quantité restante
                        remaining_qty = qty if status == "OPEN" else qty / 2
                        cash_to_return = remaining_qty * current_price
                        # PNL sur cette tranche
                        pnl_to_realize = (current_price - entry) * remaining_qty

                    elif status == "OPEN" and tp1 > 0 and current_price >= tp1:
                        # TP1 Touché
                        new_status = "TP1_HIT"
                        # On vend 50% de la quantité
                        sold_qty = qty / 2
                        cash_to_return = sold_qty * current_price
                        pnl_to_realize = (current_price - entry) * sold_qty
                        # Mise à jour du Stop Loss à Break-Even (Prix d'entrée)
                        cur.execute(
                            "UPDATE public.paper_trading_positions SET stop_loss_price = %s WHERE id = %s;",
                            (entry, pos["id"]),
                        )

                    elif status == "TP1_HIT" and tp2 > 0 and current_price >= tp2:
                        # TP2 Touché
                        new_status = "CLOSED"
                        # On vend les 50% restants
                        sold_qty = qty / 2
                        cash_to_return = sold_qty * current_price
                        pnl_to_realize = (current_price - entry) * sold_qty

                    if new_status != status:
                        # Mettre à jour la position
                        realized_total = (
                            float(pos["realized_pnl"] or 0) + pnl_to_realize
                        )
                        close_time_sql = (
                            "NOW()" if new_status in ["CLOSED", "STOPPED"] else "NULL"
                        )

                        cur.execute(
                            f"""
                            UPDATE public.paper_trading_positions
                            SET status = %s, realized_pnl = %s, closed_at = {close_time_sql}
                            WHERE id = %s;
                        """,
                            (new_status, realized_total, pos["id"]),
                        )

                        # Rendre le cash et le PNL
                        if pos["currency"] == "USD":
                            balance_usd += cash_to_return
                        else:
                            balance_eur += cash_to_return

                        logger.info(
                            f"🔄 Paper Trade {symbol} -> {new_status}. PNL Réalisé: {pnl_to_realize:.2f} {pos['currency']}"
                        )

                # Mettre à jour le compte
                cur.execute(
                    """
                    UPDATE public.paper_trading_account
                    SET balance_eur = %s, balance_usd = %s, updated_at = NOW()
                    WHERE id = %s;
                """,
                    (balance_eur, balance_usd, account["id"]),
                )

                conn.commit()
    except Exception as e:
        logger.error(f"⚠️ Erreur update_paper_positions : {e}")


def get_paper_trading_data():
    """Récupère l'intégralité des données du simulateur pour l'interface web."""
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Compte
                cur.execute("SELECT * FROM public.paper_trading_account LIMIT 1;")
                account = cur.fetchone() or {}

                # Positions actives
                cur.execute(
                    "SELECT * FROM public.paper_trading_positions WHERE status IN ('OPEN', 'TP1_HIT') ORDER BY created_at DESC;"
                )
                active_positions = cur.fetchall()

                # Historique (limité aux 50 derniers)
                cur.execute(
                    "SELECT * FROM public.paper_trading_positions WHERE status NOT IN ('OPEN', 'TP1_HIT') ORDER BY closed_at DESC LIMIT 50;"
                )
                history = cur.fetchall()

                return {
                    "account": account,
                    "active_positions": active_positions,
                    "history": history,
                }
    except Exception as e:
        logger.error(f"⚠️ Erreur get_paper_trading_data: {e}")
        return {"account": {}, "active_positions": [], "history": []}


if __name__ == "__main__":
    init_paper_trading_db()
