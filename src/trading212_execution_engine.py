"""
Moteur d'Exécution Semi-Automatique Trading 212 & Gestionnaire de Paliers Multi-Stratégies
(Go Humain, Gestion Multi-Ordres TP1 / Step Stop BE / TP2 / SL / Time Stop, Multi-Devises EUR/USD)

Ce module orchestre le cycle de vie complet des positions sous protocole institutionnel :
1. Calcul du plan de trade selon la stratégie (Mean Reversion, Sniper, Sneak) et la devise (EUR/USD)
2. Validation des garde-fous CSO et respect du Plafond Dédié à l'Automate
3. Mise en attente de validation humaine explicite ('PENDING_APPROVAL')
4. Émission de l'ordre d'entrée après 'GO HUMAIN' sur Trading 212
5. Placement et ajustement continu des ordres de sortie :
   - Initial Stop-Loss
   - Atteinte TP1 : Vente de 50% + Annulation de l'ancien SL + Création du Step Stop à Break-Even (0.0%)
   - Atteinte TP2 : Vente des 50% restants + Annulation du Stop
   - Invalidation SL ou Time Stop : Clôture propre du solde
6. Archivage automatique dans la table Supabase `trade_journal`.
"""

import os
import time
import json
import logging
import uuid
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

from src.order_guardrails import guardrails_engine, STRATEGY_GRID_PROFILES
from src.trading212_connector import (
    place_trading212_limit_order,
    place_trading212_market_order,
    place_trading212_stop_order,
    cancel_trading212_order,
    cancel_all_trading212_orders,
    get_trading212_cash,
    get_trading212_open_positions,
    get_trading212_open_orders,
    convert_yahoo_ticker_to_t212,
    check_trading212_api_permissions
)
from src.supabase_connector import batch_save_trade_journal
from src.market_data import resolve_ticker_symbol

logger = logging.getLogger("Trading212ExecutionEngine")
logging.basicConfig(level=logging.INFO)


class Trading212ExecutionEngine:
    def __init__(self):
        # File d'attente des propositions générées en attente de Go Humain
        self.pending_proposals = {}
        
        # Positions actives de l'automate sous gestion de paliers
        self.active_managed_positions = {}
        
        # Historique d'exécution
        self.execution_history = []

    def propose_trade(
        self,
        symbol,
        entry_price,
        strategy_type="Mean Reversion",
        custom_sl_price=None,
        custom_tp1_price=None,
        custom_tp2_price=None,
        quantity=None,
        nominal_capital=None,
        notes=""
    ):
        """
        Génère une proposition de plan de trade adaptée à la stratégie (Mean Reversion, Sniper, Sneak)
        et la soumet aux garde-fous avant mise en attente de Go Humain.
        """
        sym = resolve_ticker_symbol(symbol).upper()
        currency = guardrails_engine.get_instrument_currency(sym)

        # 1. Calcul de la grille stratégique selon la méthode
        grid = guardrails_engine.calculate_strategy_grid(
            symbol=sym,
            entry_price=entry_price,
            strategy_type=strategy_type
        )

        sl_price = custom_sl_price if custom_sl_price is not None else grid["stop_loss_price"]
        tp1_price = custom_tp1_price if custom_tp1_price is not None else grid["tp1_price"]
        tp2_price = custom_tp2_price if custom_tp2_price is not None else grid["tp2_price"]
        step_stop_be = grid["step_stop_be_price"]
        time_stop_days = grid["time_stop_days"]

        # 2. Calcul dynamique de la quantité (basé sur l'enveloppe respective EUR ou USD et R-Max 1%)
        if currency == "USD":
            automate_ceiling = guardrails_engine.allocated_automate_capital_ceiling_usd
            deployed_cap = sum(p.get("nominal_invested", 0.0) for p in guardrails_engine.active_automate_positions.values() if p.get("currency") == "USD")
        else:
            automate_ceiling = guardrails_engine.allocated_automate_capital_ceiling_eur
            deployed_cap = sum(p.get("nominal_invested", 0.0) for p in guardrails_engine.active_automate_positions.values() if p.get("currency") == "EUR")

        avail_automate_cap = max(0.0, automate_ceiling - deployed_cap)

        if (quantity is None or quantity <= 0) and nominal_capital is not None and float(nominal_capital) > 0 and entry_price > 0:
            raw_qty = float(nominal_capital) / entry_price
            # Dimensionnement par Valeur / Montant : supporte les fractions d'actions (0.XX) à 2 décimales
            quantity = max(0.01, round(raw_qty, 2))

        if quantity is None or quantity <= 0:
            risk_target_monetary = automate_ceiling * (guardrails_engine.max_risk_per_trade_pct / 100.0)
            stop_dist = max(0.01, entry_price - sl_price)
            calc_qty = risk_target_monetary / stop_dist
            
            # Plafond de ligne max (ex: 20% de l'enveloppe respective)
            max_alloc_monetary = automate_ceiling * (guardrails_engine.max_position_allocation_pct / 100.0)
            nominal_from_alloc = max_alloc_monetary / entry_price
            
            # Limiter au cash disponible sur l'enveloppe respective
            nominal_from_avail = avail_automate_cap / entry_price
            
            final_qty = min(calc_qty, nominal_from_alloc, nominal_from_avail)
            quantity = max(0.01, round(final_qty, 2))

        # 3. Validation par les Garde-Fous Institutionnels
        is_valid, reason, trade_plan = guardrails_engine.validate_trade_plan(
            symbol=sym,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            quantity=quantity,
            strategy_type=strategy_type,
            action_type="ENTRY_BUY"
        )

        proposal_id = f"PROP_{sym}_{strategy_type[:4].upper()}_{int(time.time())}"

        if not is_valid:
            return {
                "success": False,
                "error": reason,
                "proposal_id": proposal_id,
                "status": "REJECTED_BY_GUARDRAILS",
                "strategy_type": strategy_type
            }

        # 4. Enregistrement de la proposition
        trade_plan["time_stop_days"] = time_stop_days
        trade_plan["strategy_description"] = grid["description"]

        proposal_obj = {
            "proposal_id": proposal_id,
            "symbol": sym,
            "t212_ticker": convert_yahoo_ticker_to_t212(sym),
            "currency": currency,
            "status": "PENDING_APPROVAL",
            "strategy_type": strategy_type,
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "trade_plan": trade_plan,
            "notes": notes,
            "last_execution_error": None
        }

        self.pending_proposals[proposal_id] = proposal_obj
        logger.info(f"📋 Proposition créée : {proposal_id} ({sym} - {strategy_type}) en attente de confirmation.")

        return {
            "success": True,
            "message": f"Proposition créée pour {sym} ({strategy_type}). En attente de confirmation humaine.",
            "proposal": proposal_obj
        }

    def update_proposal(
        self,
        proposal_id,
        quantity=None,
        nominal_capital=None,
        entry_price=None,
        custom_sl_price=None,
        custom_tp1_price=None,
        custom_tp2_price=None
    ):
        """
        Met à jour une proposition de trade en attente (capital, nombre d'actions ou cours)
        avec re-validation instantanée des garde-fous et support des fractions d'actions (0.XX).
        """
        if proposal_id not in self.pending_proposals:
            return {"success": False, "error": f"Proposition {proposal_id} introuvable ou déjà traitée."}

        prop = self.pending_proposals[proposal_id]
        plan = prop.get("trade_plan", {})
        sym = prop["symbol"]
        strat_type = prop.get("strategy_type", "Mean Reversion")

        ep = float(entry_price) if (entry_price is not None and float(entry_price) > 0) else float(plan.get("entry_price", 0.0))
        sl_p = float(custom_sl_price) if custom_sl_price is not None else plan.get("stop_loss_price")
        tp1_p = float(custom_tp1_price) if custom_tp1_price is not None else plan.get("tp1_price")
        tp2_p = float(custom_tp2_price) if custom_tp2_price is not None else plan.get("tp2_price")

        # Calcul de la nouvelle quantité avec support des fractions (0.XX) et respect strict de la précision Trading 212
        if quantity is not None and float(quantity) > 0:
            val_q = float(quantity)
            if abs(val_q - round(val_q)) < 1e-4:
                qty = float(int(round(val_q)))
            else:
                qty = round(val_q, 2)
        elif nominal_capital is not None and float(nominal_capital) > 0 and ep > 0:
            raw_qty = float(nominal_capital) / ep
            qty = max(0.01, round(raw_qty, 2))
        else:
            qty = float(plan.get("quantity", 1.0))

        # Re-calcul et validation par les garde-fous
        is_valid, reason, new_plan = guardrails_engine.validate_trade_plan(
            symbol=sym,
            entry_price=ep,
            stop_loss_price=sl_p,
            tp1_price=tp1_p,
            tp2_price=tp2_p,
            quantity=qty,
            strategy_type=strat_type,
            action_type="ENTRY_BUY"
        )

        if not is_valid:
            return {
                "success": False,
                "error": reason,
                "proposal_id": proposal_id
            }

        new_plan["time_stop_days"] = plan.get("time_stop_days", 10)
        new_plan["strategy_description"] = plan.get("strategy_description", "")
        prop["trade_plan"] = new_plan
        prop["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        # Réinitialiser le message d'erreur si la proposition a été modifiée
        prop["last_execution_error"] = None
        prop["status"] = "PENDING_APPROVAL"

        logger.info(f"✏️ Proposition modifiée : {proposal_id} ({sym} - Qty: {qty} | Capital: {new_plan['nominal_invested']} {new_plan['currency_symbol']})")
        return {
            "success": True,
            "message": f"Proposition {sym} mise à jour avec succès : {new_plan['nominal_invested']:.2f} {new_plan['currency_symbol']} ({qty} actions).",
            "proposal": prop
        }

    def approve_and_execute_trade(self, proposal_id, order_type="LIMIT", time_validity="DAY"):
        """
        Validation 'GO HUMAIN' : émet l'ordre d'achat sur Trading 212
        et initialise la gestion des paliers (TP1 / Step Stop BE / TP2 / SL).
        Si la transmission échoue, la proposition est maintenue en attente pour correction.
        """
        proposal = self.pending_proposals.get(proposal_id)
        if not proposal:
            return {"success": False, "error": f"Proposition {proposal_id} introuvable."}

        if proposal["status"] not in ["PENDING_APPROVAL", "EXECUTION_FAILED"]:
            return {"success": False, "error": f"Statut non éligible ({proposal['status']})."}

        plan = proposal["trade_plan"]
        sym = plan["symbol"]
        qty = plan["quantity"]
        entry_px = plan["entry_price"]
        sl_px = plan["stop_loss_price"]
        tp1_px = plan["tp1_price"]
        tp2_px = plan["tp2_price"]
        currency = plan.get("currency", "EUR")
        nominal = plan.get("nominal_invested", entry_px * qty)
        entry_hash = plan["idempotency_hash"]
        strategy_type = proposal.get("strategy_type", "Mean Reversion")
        time_stop_days = plan.get("time_stop_days", 10)

        # Vérification Kill-Switch
        if guardrails_engine.is_kill_switch_active:
            return {"success": False, "error": "🛑 Action bloquée : Kill-Switch activé."}

        # 1. Émission de l'ordre d'achat principal
        if order_type.upper() == "MARKET":
            res_entry = place_trading212_market_order(sym, qty)
        else:
            res_entry = place_trading212_limit_order(sym, qty, entry_px, time_validity=time_validity)

        if not res_entry.get("success"):
            err_msg = res_entry.get("error", "Erreur d'émission Trading 212")
            guardrails_engine.register_order_error(err_msg)
            
            # MAINTENIR LA PROPOSITION DANS LA LISTE : NE PAS L'EFFACER !
            proposal["status"] = "PENDING_APPROVAL"
            proposal["last_execution_error"] = err_msg
            proposal["execution_error"] = err_msg
            proposal["last_execution_attempt"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            logger.warning(f"⚠️ Échec d'émission Trading 212 pour {sym} : {err_msg}. Proposition {proposal_id} maintenue.")
            return {"success": False, "error": f"Échec transmission Trading 212 : {err_msg}", "proposal_id": proposal_id}

        # 2. Enregistrement dans les garde-fous
        guardrails_engine.register_entry_order_submitted(sym, entry_hash, nominal, currency=currency)
        proposal["status"] = "APPROVED_AND_SUBMITTED"
        proposal["t212_entry_order"] = res_entry.get("order")
        proposal["last_execution_error"] = None
        proposal["execution_error"] = None

        # 3. Placement initial du Stop-Loss sur Trading 212
        sl_order_res = place_trading212_stop_order(sym, -qty, sl_px, time_validity="GTC")
        sl_order_id = sl_order_res.get("order", {}).get("id") if sl_order_res.get("success") else None

        # 4. Placement en gestion active
        pos_id = f"POS_{sym}_{int(time.time())}"
        self.active_managed_positions[pos_id] = {
            "position_id": pos_id,
            "proposal_id": proposal_id,
            "symbol": sym,
            "strategy_type": strategy_type,
            "currency": currency,
            "entry_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "entry_price": entry_px,
            "quantity": qty,
            "initial_quantity": qty,
            "stop_loss_price": sl_px,
            "initial_sl_order_id": sl_order_id,
            "current_sl_order_id": sl_order_id,
            "tp1_price": tp1_px,
            "tp2_price": tp2_px,
            "time_stop_days": time_stop_days,
            "tp1_hit": False,
            "step_stop_be_active": False,
            "realized_pnl_tp1": 0.0,
            "days_held": 0,
            "status": "ACTIVE_TRACKING"
        }

        logger.info(f"✅ GO HUMAIN validé pour {sym} ({strategy_type}) : Ordre transmis à Trading 212. Stop-Loss initial placé à {sl_px}{currency}.")

        return {
            "success": True,
            "message": f"Ordre {sym} ({strategy_type}) exécuté avec succès.",
            "position_id": pos_id,
            "entry_order": res_entry.get("order"),
            "sl_order": sl_order_res.get("order")
        }

    def reject_trade_proposal(self, proposal_id, reason="Rejeté par l'utilisateur"):
        """Rejette une proposition."""
        proposal = self.pending_proposals.get(proposal_id)
        if not proposal:
            return {"success": False, "error": f"Proposition {proposal_id} introuvable."}
        proposal["status"] = "REJECTED_BY_USER"
        proposal["rejection_reason"] = reason
        return {"success": True, "message": f"Proposition {proposal_id} rejetée."}

    def get_pending_proposals(self):
        """Retourne les propositions en attente de Go Humain (y compris celles en échec de transmission précédente)."""
        return [p for p in self.pending_proposals.values() if p.get("status") in ["PENDING_APPROVAL", "EXECUTION_FAILED"]]

    def get_active_positions(self):
        """Retourne les positions sous gestion active."""
        return list(self.active_managed_positions.values())

    def update_positions_monitoring(self, current_prices_dict=None):
        """
        Surveillance active et exécution séquentielle des paliers :
        1. TP1 (+1.8%) : Vente de 50%, annulation de l'ancien SL et création du Step Stop BE (0.0%).
        2. TP2 (+2.5% ou MM20) : Vente des 50% restants et annulation du Step Stop.
        3. Stop-Loss ou Time-Stop : Clôture immédiate du solde et libération du capital automate.
        """
        if not self.active_managed_positions:
            return []

        prices = current_prices_dict or {}
        closed_events = []

        # Télécharger les derniers cours si non fournis
        syms = [p["symbol"] for p in self.active_managed_positions.values() if p["symbol"] not in prices]
        if syms:
            try:
                data = yf.download(" ".join(syms), period="1d", interval="1m", progress=False)
                if not data.empty and 'Close' in data:
                    c = data['Close']
                    for s in syms:
                        if s in c:
                            prices[s] = float(c[s].dropna().iloc[-1])
            except Exception as e:
                logger.warning(f"Erreur actualisation cours live: {e}")

        for pos_id, pos in list(self.active_managed_positions.items()):
            sym = pos["symbol"]
            curr_px = prices.get(sym)
            if not curr_px or curr_px <= 0:
                continue

            entry_px = pos["entry_price"]
            sl_px = pos["stop_loss_price"]
            tp1_px = pos["tp1_price"]
            tp2_px = pos["tp2_price"]
            qty = pos["quantity"]
            curr_sl_id = pos.get("current_sl_order_id")

            # 1. Étape TP1 : Vente de 50% + Remontée du Stop à Break-Even (Step Stop)
            if not pos["tp1_hit"] and curr_px >= tp1_px:
                half_qty = qty * 0.5
                # Vente au marché de 50%
                res_tp1_sell = place_trading212_market_order(sym, -half_qty)
                
                # Annulation de l'ancien Stop-Loss s'il était posé sur le carnet
                if curr_sl_id:
                    cancel_trading212_order(curr_sl_id)

                # Création du nouveau Step Stop à Break-Even (PRU = entry_price) sur les 50% restants
                new_sl_res = place_trading212_stop_order(sym, -half_qty, entry_px, time_validity="GTC")
                new_sl_id = new_sl_res.get("order", {}).get("id") if new_sl_res.get("success") else None

                pos["tp1_hit"] = True
                pos["step_stop_be_active"] = True
                pos["stop_loss_price"] = entry_px
                pos["current_sl_order_id"] = new_sl_id
                pos["quantity"] = half_qty
                pnl_tp1 = half_qty * (curr_px - entry_px)
                pos["realized_pnl_tp1"] = pnl_tp1

                logger.info(f"🎯 TP1 atteint sur {sym} ({curr_px}€) : 50% vendus (+{pnl_tp1:.2f}€), Step Stop BE placé au PRU ({entry_px}€).")

            # 2. Étape TP2 : Vente des 50% restants + Annulation finale du Stop
            elif curr_px >= tp2_px:
                place_trading212_market_order(sym, -qty)
                if curr_sl_id:
                    cancel_trading212_order(curr_sl_id)

                rem_pnl = qty * (curr_px - entry_px)
                tot_pnl = rem_pnl + pos.get("realized_pnl_tp1", 0.0)
                tot_inv = pos["initial_quantity"] * entry_px

                self._record_closed_trade(pos, curr_px, tot_pnl, tot_inv, "TP2_OPTIMAL (+2.5%)")
                closed_events.append({"position_id": pos_id, "symbol": sym, "reason": "TP2_OPTIMAL", "pnl": round(tot_pnl, 2)})
                del self.active_managed_positions[pos_id]
                guardrails_engine.register_position_closed(sym)

            # 3. Étape Stop-Loss (Initial ou Step Stop Break-Even)
            elif curr_px <= pos["stop_loss_price"]:
                place_trading212_market_order(sym, -qty)
                if curr_sl_id:
                    cancel_trading212_order(curr_sl_id)

                rem_pnl = qty * (curr_px - entry_px)
                tot_pnl = rem_pnl + pos.get("realized_pnl_tp1", 0.0)
                tot_inv = pos["initial_quantity"] * entry_px
                reason = "BREAKEVEN_SECURISE" if pos["step_stop_be_active"] else "STOP_LOSS_STRICT"

                self._record_closed_trade(pos, curr_px, tot_pnl, tot_inv, reason)
                closed_events.append({"position_id": pos_id, "symbol": sym, "reason": reason, "pnl": round(tot_pnl, 2)})
                del self.active_managed_positions[pos_id]
                guardrails_engine.register_position_closed(sym)

            # 4. Étape Time Stop
            elif pos["days_held"] >= pos.get("time_stop_days", 10):
                place_trading212_market_order(sym, -qty)
                if curr_sl_id:
                    cancel_trading212_order(curr_sl_id)

                rem_pnl = qty * (curr_px - entry_px)
                tot_pnl = rem_pnl + pos.get("realized_pnl_tp1", 0.0)
                tot_inv = pos["initial_quantity"] * entry_px

                self._record_closed_trade(pos, curr_px, tot_pnl, tot_inv, "TIME_STOP")
                closed_events.append({"position_id": pos_id, "symbol": sym, "reason": "TIME_STOP", "pnl": round(tot_pnl, 2)})
                del self.active_managed_positions[pos_id]
                guardrails_engine.register_position_closed(sym)

        return closed_events

    def _record_closed_trade(self, pos, exit_price, total_pnl, total_invested, reason):
        """Enregistre le trade clôturé dans la base Supabase."""
        try:
            pnl_pct = (total_pnl / total_invested * 100.0) if total_invested > 0 else 0.0
            trade_record = {
                "id": f"AUTO_{pos['symbol']}_{int(time.time())}",
                "symbol": pos["symbol"],
                "company_name": pos["symbol"],
                "entry_date": pos["entry_date"],
                "exit_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "pru": float(pos["entry_price"]),
                "exit_price": float(exit_price),
                "quantity": float(pos["initial_quantity"]),
                "invested_amount": float(total_invested),
                "pnl_amount": float(round(total_pnl, 2)),
                "pnl_pct": float(round(pnl_pct, 2)),
                "result": "GAIN 🟢" if total_pnl >= 0 else "PERTE 🔴",
                "account_type": f"Trading 212 ({pos.get('currency', 'EUR')})",
                "broker": "Trading 212",
                "currency": pos.get("currency", "EUR"),
                "notes": f"Automate ({pos.get('strategy_type', 'Mean Reversion')}): {reason}"
            }
            batch_save_trade_journal([trade_record])
            self.execution_history.append(trade_record)
            logger.info(f"📝 Trade clôturé archivé dans Supabase : {pos['symbol']} | P&L: {total_pnl:+.2f}{pos.get('currency', 'EUR')} ({reason})")
        except Exception as e:
            logger.warning(f"Erreur archivage Supabase: {e}")

    def kill_all_and_freeze(self, reason="Urgence Kill-Switch déclenchée"):
        """Arrêt d'urgence : annule tous les ordres et fige le système."""
        ks_res = guardrails_engine.trigger_kill_switch(reason)
        cancel_res = cancel_all_trading212_orders()
        
        for p in self.pending_proposals.values():
            if p["status"] == "PENDING_APPROVAL":
                p["status"] = "CANCELLED_BY_KILL_SWITCH"

        return {
            "success": True,
            "message": f"Système gelé et ordres annulés : {reason}",
            "guardrails_status": ks_res,
            "orders_cancellation": cancel_res
        }


# Instance singleton globale du moteur d'exécution
execution_engine = Trading212ExecutionEngine()
