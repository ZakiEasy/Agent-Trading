import os
import sys
import time
import json
import logging
from datetime import datetime
import pytz
import concurrent.futures

# Set up path so we can import src modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.db_connector import (
    get_watchlist_symbols,
    log_trading_signal,
    log_robot_scan_history,
    purge_robot_logs,
)
from src.institutional_engine import generate_8_step_protocol_analysis
from src.config import CAPITAL_REFERENCE_DEFAULT
from src.paper_trading_engine import execute_paper_trade, update_paper_positions
from src.market_data import fetch_market_data
from src.trading212_execution_engine import execution_engine

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

ROBOT_STATE_FILE = os.path.join(os.path.dirname(__file__), "robot_state.json")


def is_market_open():
    """
    Vérifie si on est dans les heures d'ouverture des bourses (Europe + US).
    Ouverture : 08:00 heure de Paris (marchés allemands)
    Fermeture : 16:00 heure de New York (clôture US, gère automatiquement le décalage d'hiver).
    """
    paris_tz = pytz.timezone("Europe/Paris")
    ny_tz = pytz.timezone("America/New_York")

    now_utc = datetime.now(pytz.utc)
    now_paris = now_utc.astimezone(paris_tz)
    now_ny = now_utc.astimezone(ny_tz)

    if now_paris.weekday() >= 5:  # 5=Samedi, 6=Dimanche
        return False

    is_after_open = now_paris.time() >= datetime.strptime("08:00", "%H:%M").time()
    is_before_close = now_ny.time() <= datetime.strptime("16:00", "%H:%M").time()

    return is_after_open and is_before_close


def get_robot_mode():
    """Lit l'état du robot (normal ou active) depuis le fichier JSON."""
    if os.path.exists(ROBOT_STATE_FILE):
        try:
            with open(ROBOT_STATE_FILE, "r") as f:
                state = json.load(f)
                return state.get("mode", "normal")  # "normal" (10m) or "active" (1m)
        except Exception as e:
            logging.error(f"Erreur de lecture du mode : {e}")
    return "normal"


def run_scan():
    logging.info("Lancement du scan du robot...")
    try:
        watchlist = get_watchlist_symbols(only_active=True)
        
        # Récupérer aussi les positions ouvertes en Paper Trading
        open_paper_symbols = []
        try:
            from src.db_connector import get_db_connection
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT symbol FROM public.paper_trading_positions WHERE status IN ('OPEN', 'TP1_HIT');")
                    open_paper_symbols = [row[0] for row in cur.fetchall()]
        except Exception as e:
            logging.error(f"Erreur recup symbols paper trading: {e}")

        all_symbols_to_fetch = list(set(watchlist + open_paper_symbols))

        if not all_symbols_to_fetch:
            logging.info("Watchlist et positions paper vides.")
            return

        # --- PAPER TRADING UPDATE ---
        current_prices = {}
        for sym in all_symbols_to_fetch:
            try:
                res = fetch_market_data(sym)
                if res and isinstance(res, tuple) and len(res) == 2:
                    _, data = res
                    if data is not None and hasattr(data, "empty") and not data.empty:
                        current_prices[sym] = data.iloc[-1]["Close"]
            except Exception as e:
                logging.error(f"Erreur recup prix {sym}: {e}")

        try:
            update_paper_positions(current_prices)
            # Update health status
            try:
                import json

                with open(
                    os.path.join(os.path.dirname(__file__), "paper_health.json"), "w"
                ) as f:
                    json.dump(
                        {
                            "last_execution": datetime.now(pytz.UTC).isoformat(),
                            "status": "operational",
                        },
                        f,
                    )
            except:
                pass
        except Exception as e_paper_update:
            logging.error(f"⚠️ Erreur MAJ Paper Trading: {e_paper_update}")
        # ----------------------------

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_sym = {
                executor.submit(
                    generate_8_step_protocol_analysis, sym, CAPITAL_REFERENCE_DEFAULT
                ): sym
                for sym in watchlist
            }
            for future in concurrent.futures.as_completed(future_to_sym, timeout=60):
                try:
                    analysis = future.result()
                    if (
                        not analysis
                        or not isinstance(analysis, dict)
                        or "error" in analysis
                    ):
                        continue

                    r_dict = {
                        "symbol": analysis.get("symbol"),
                        "name": analysis.get("name", analysis.get("symbol")),
                        "category": analysis.get("category", "Autres"),
                        "category_icon": analysis.get("category_icon", "📦"),
                        "is_pea": analysis.get("is_pea", False),
                        "account_type": analysis.get("account_type", "CTO (US)"),
                        "sharia": analysis.get("sharia", "NON CONFORME"),
                        "price": analysis.get("current_price", 0.0),
                        "drop": analysis.get("drop", 0.0),
                        "drop_nature": "SURRÉACTION CONJONCTURELLE"
                        if analysis.get("pullback_valid")
                        else "REPLI EN COURS",
                        "avg_daily_volume": analysis.get("avg_daily_volume", 0.0),
                        "has_min_liquidity": True,
                        "sector_rel": "SURPERFORMANCE"
                        if analysis.get("trend_following_valid")
                        else "EN LIGNE",
                        "sector_etf": "SPY",
                        "rsi": analysis.get("rsi", 50.0),
                        "rsi_divergence": analysis.get("rsi_divergence", "AUCUNE"),
                        "confluence_score": analysis.get("confluence_score", 0),
                        "verdict": analysis.get("verdict", "ÉVITER - HORS CRITÈRES"),
                        "verdict_badge": analysis.get("verdict_badge", "badge-neutral"),
                        "verdict_action": analysis.get("verdict_action", ""),
                        "verdict_swing": analysis.get(
                            "verdict_swing", analysis.get("verdict", "ÉVITER")
                        ),
                        "verdict_swing_badge": analysis.get(
                            "verdict_swing_badge", "badge-neutral"
                        ),
                        "verdict_swing_action": analysis.get(
                            "verdict_swing_action", ""
                        ),
                        "verdict_sniper": analysis.get(
                            "verdict_sniper", "NON ÉLIGIBLE"
                        ),
                        "verdict_sniper_badge": analysis.get(
                            "verdict_sniper_badge", "badge-neutral"
                        ),
                        "verdict_sniper_action": analysis.get(
                            "verdict_sniper_action", ""
                        ),
                        "action_plan": analysis.get("action_plan", ""),
                        "execution_timing": analysis.get("execution_timing"),
                        "pricing_plan_sniper": analysis.get("pricing_plan_sniper"),
                        "currency": analysis.get("currency", "EUR"),
                    }

                    # Enregistrement systématique du scan (Option B)
                    log_robot_scan_history(r_dict)

                    if (
                        "ACHAT" in str(r_dict["verdict_swing"])
                        or "ACHAT" in str(r_dict["verdict_sniper"])
                        or "ATTENDRE" in str(r_dict["verdict_sniper"])
                    ):
                        # --- PAPER TRADING AUTO-EXECUTION (Simulation uniquement) ---
                        if "ACHAT" in str(r_dict["verdict_swing"]) or "ACHAT" in str(r_dict["verdict_sniper"]):
                            try:
                                from src.paper_trading_engine import get_paper_account
                                import time
                                
                                pricing = r_dict.get("pricing_plan_sniper", {})
                                if not pricing:
                                    pricing = r_dict.get("pricing_plan", {})
    
                                entry = pricing.get("entry", r_dict.get("price", 0.0))
                                sl = pricing.get("sl", pricing.get("stop_loss", 0.0))
                                tp1 = pricing.get("tp1", pricing.get("take_profit_1", 0.0))
                                tp2 = pricing.get("tp2", pricing.get("take_profit_2", 0.0))
                                currency = r_dict.get("currency", "EUR")
    
                                if entry > 0:
                                    # Calcul de la taille de position pour le Paper Trading
                                    account = get_paper_account()
                                    if account:
                                        balance_eur = float(account["balance_eur"])
                                        balance_usd = float(account["balance_usd"])
                                        
                                        # R-Max: On risque 1% du capital disponible sur ce trade
                                        available = balance_usd if currency == "USD" else balance_eur
                                        risk_amount = available * 0.01
                                        stop_dist = max(0.01, entry - sl) if sl > 0 else (entry * 0.05)
                                        
                                        quantity = round(risk_amount / stop_dist, 2)
                                        nominal_invested = quantity * entry
                                        
                                        # Plafond de la ligne à 20% max de l'enveloppe
                                        if nominal_invested > available * 0.20:
                                            nominal_invested = available * 0.20
                                            quantity = round(nominal_invested / entry, 2)
                                        
                                        # Creation de la proposition virtuelle (aucun impact T212)
                                        proposal_id = f"PAPER_{r_dict['symbol']}_{int(time.time())}"
                                        paper_proposal = {
                                            "proposal_id": proposal_id,
                                            "symbol": r_dict["symbol"],
                                            "currency": currency,
                                            "entry_price": entry,
                                            "quantity": quantity,
                                            "nominal_invested": nominal_invested,
                                            "stop_loss_price": sl,
                                            "tp1_price": tp1,
                                            "tp2_price": tp2,
                                            "trade_plan": {
                                                "entry_price": entry,
                                                "quantity": quantity,
                                                "nominal_invested": nominal_invested,
                                                "stop_loss_price": sl,
                                                "tp1_price": tp1,
                                                "tp2_price": tp2,
                                                "strategy_type": "Autonomous Robot Paper",
                                                "currency_symbol": "€" if currency == "EUR" else "$",
                                                "sector": r_dict.get("sector", "Unknown"),
                                                "industry": r_dict.get("industry", "Unknown")
                                            }
                                        }
                                        
                                        paper_res = execute_paper_trade(paper_proposal)
                                        
                                        if paper_res.get("status") == "success":
                                            r_dict["action_plan"] = str(r_dict.get("action_plan", "")) + f"\n\n✅ PAPER TRADE EXÉCUTÉ: {nominal_invested:.2f} {currency}"
                                            logging.info(f"🟢 Trade Paper exécuté avec succès pour {r_dict['symbol']} : {nominal_invested:.2f} {currency}")
                                        else:
                                            r_dict["action_plan"] = str(r_dict.get("action_plan", "")) + f"\n\n🛑 REJET PAPER TRADE: {paper_res.get('message')}"
                                            logging.error(f"🔴 Erreur exécution Paper Trade {r_dict['symbol']}: {paper_res.get('message')}")
                            except Exception as ex:
                                logging.error(f"Erreur lors du Paper Trading pour {r_dict['symbol']}: {ex}")

                        log_trading_signal(r_dict)
                        logging.info(
                            f"Signal enregistré pour {r_dict['symbol']} : {r_dict['verdict_swing']}"
                        )
                        # ------------------------------------------------------------

                except concurrent.futures.TimeoutError:
                    pass
                except Exception as e:
                    logging.error(f"Erreur d'analyse pour un symbole : {e}")

    except Exception as e:
        logging.error(f"Erreur critique dans le scan : {e}")


def main():
    logging.info("Démarrage du robot de trading autonome (robot_worker.py)...")
    while True:
        mode = get_robot_mode()
        sleep_interval = 60 if mode == "active" else 120

        # Purge des anciens logs à chaque itération (garde 2h)
        purge_robot_logs()

        # Nettoyage automatique des propositions expirées (24h)
        try:
            execution_engine.clean_expired_proposals(24)
        except Exception as e:
            logging.error(f"Erreur nettoyage propositions : {e}")

        # Le Paper Trading tourne 24/7, pas de vérification is_market_open()
        run_scan()

        logging.info(
            f"Attente de {sleep_interval} secondes avant le prochain scan (mode={mode})."
        )
        time.sleep(sleep_interval)


if __name__ == "__main__":
    main()
