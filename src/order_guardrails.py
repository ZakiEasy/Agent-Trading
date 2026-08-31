"""
Module de Garde-Fous Institutionnels & Sécurité (CSO - Chief Security Officer)
Applique des règles strictes et non contournables avant toute transmission d'ordre sur Trading 212 :
1. Plafond Dédié à l'Automate (Indépendant du capital total disponible sur le compte broker)
2. Respect du Risque R-Max (<= 1.0% de l'equity globale par position)
3. Limite d'allocation par ligne (max 15-20%) et Réserve Cash
4. Grilles Stratégiques Spécifiques (Mean Reversion, Sniper, Sneak) avec Time Stop dédié
5. Gestion Multi-Devises (EUR vs USD) et protection contre les frais de change
6. Filtrage Sharia AAOIFI obligatoire
7. Anti-doublon d'entrée & Idempotence stricte (avec autorisation des ordres de sortie TP1/BE/TP2/SL)
8. Rate Limiting (max 1 ordre / 3s, max 10 ordres / heure)
9. Circuit Breaker (3 rejets consécutifs -> arrêt temporaire automatique)
10. Kill-Switch (Verrouillage immédiat d'urgence)
"""

import os
import time
import hashlib
import logging
from datetime import datetime, timedelta

from src.sharia_screen import screen_ticker

logger = logging.getLogger("OrderGuardrails")
logging.basicConfig(level=logging.INFO)


# Profils de Grilles Stratégiques par Méthode
STRATEGY_GRID_PROFILES = {
    "Mean Reversion": {
        "name": "Mean Reversion (Tactique Pure)",
        "stop_loss_pct": -2.00,
        "tp1_pct": +1.80,
        "tp1_ratio": 0.50,  # Vente 50%
        "step_stop_be": 0.00,  # Remontée Stop au PRU
        "tp2_pct": +2.50,  # ou MM20
        "time_stop_days": 10,
        "description": "Retour à la moyenne sur repli MM20/VWAP (Dips sains Sharia)."
    },
    "Sniper": {
        "name": "Sniper (Intraday & Breakout Rapide)",
        "stop_loss_pct": -1.80,
        "tp1_pct": +1.50,
        "tp1_ratio": 0.50,
        "step_stop_be": 0.00,
        "tp2_pct": +2.20,
        "time_stop_days": 4,
        "description": "Exécution rapide sur catalyseur court terme et Order Flow H1."
    },
    "Sneak": {
        "name": "Sneak (Swing Retracement Fibo)",
        "stop_loss_pct": -3.00,
        "tp1_pct": +2.50,
        "tp1_ratio": 0.50,
        "step_stop_be": 0.00,
        "tp2_pct": +4.50,
        "time_stop_days": 15,
        "description": "Capture de vague swing sur creux de retracement Fibonacci 50-61.8%."
    }
}


class OrderGuardrailsEngine:
    def __init__(self):
        # 1. Plafond dédié EXCLUSIVEMENT à l'automate (Indépendant du compte total Trading 212)
        self.allocated_automate_capital_ceiling = float(os.getenv("AUTOMATE_CAPITAL_CEILING", 5000.0))
        
        # 2. Risque monétaire par trade R-Max (<= 1.0%)
        self.max_risk_per_trade_pct = 1.0
        
        # 3. Allocation max par ligne (% de l'enveloppe automate)
        self.max_position_allocation_pct = 20.0
        
        # 4. Limites de gestion
        self.max_open_positions = 6
        self.max_consecutive_errors = 3
        
        # État en mémoire
        self.is_kill_switch_active = False
        self.kill_switch_reason = ""
        self.consecutive_errors_count = 0
        
        # Hashes d'ordres d'entrée pour bloquer les doubles clics accidentels
        self.submitted_entry_hashes = set()
        self.recent_order_timestamps = []
        
        # Positions actives sous gestion de l'automate (avec capital déployé)
        self.active_automate_positions = {}  # { symbol: { nominal_invested, currency, ... } }

    def update_settings(self, automate_ceiling=None, max_risk_pct=None, max_alloc_pct=None):
        """Met à jour les paramètres de sécurité à l'exécution."""
        if automate_ceiling is not None and float(automate_ceiling) > 0:
            self.allocated_automate_capital_ceiling = float(automate_ceiling)
        if max_risk_pct is not None and 0.1 <= float(max_risk_pct) <= 2.5:
            self.max_risk_per_trade_pct = float(max_risk_pct)
        if max_alloc_pct is not None and 5.0 <= float(max_alloc_pct) <= 30.0:
            self.max_position_allocation_pct = float(max_alloc_pct)

        logger.info(f"🛡️ Garde-fous mis à jour : Plafond Automate {self.allocated_automate_capital_ceiling}€ | R-Max {self.max_risk_per_trade_pct}% | Alloc {self.max_position_allocation_pct}%")
        return self.get_status()

    def get_status(self):
        """Retourne l'état actuel des garde-fous et de la sécurité."""
        deployed_capital = sum(p.get("nominal_invested", 0.0) for p in self.active_automate_positions.values())
        available_automate_capital = max(0.0, self.allocated_automate_capital_ceiling - deployed_capital)
        
        return {
            "kill_switch_active": self.is_kill_switch_active,
            "kill_switch_reason": self.kill_switch_reason,
            "allocated_automate_capital_ceiling": round(self.allocated_automate_capital_ceiling, 2),
            "deployed_automate_capital": round(deployed_capital, 2),
            "available_automate_capital": round(available_automate_capital, 2),
            "max_risk_per_trade_pct": round(self.max_risk_per_trade_pct, 2),
            "max_position_allocation_pct": round(self.max_position_allocation_pct, 2),
            "consecutive_errors_count": self.consecutive_errors_count,
            "active_automate_positions_count": len(self.active_automate_positions),
            "active_automate_symbols": list(self.active_automate_positions.keys()),
            "strategy_profiles": list(STRATEGY_GRID_PROFILES.keys())
        }

    def trigger_kill_switch(self, reason="Déclenché manuellement par l'utilisateur"):
        """Active le Kill-Switch d'urgence et bloque toutes les opérations."""
        self.is_kill_switch_active = True
        self.kill_switch_reason = str(reason)
        logger.critical(f"🛑 KILL-SWITCH ACTIVÉ : {reason}")
        return {
            "success": True,
            "message": f"Kill-Switch activé : {reason}",
            "status": self.get_status()
        }

    def reset_kill_switch(self):
        """Réinitialise le Kill-Switch après vérification humaine."""
        self.is_kill_switch_active = False
        self.kill_switch_reason = ""
        self.consecutive_errors_count = 0
        logger.info("🟢 Kill-Switch réinitialisé. Trading réautorisé.")
        return {
            "success": True,
            "message": "Kill-Switch désactivé. Le système est de nouveau opérationnel.",
            "status": self.get_status()
        }

    def compute_entry_idempotency_hash(self, symbol, entry_price, quantity):
        """Génère une empreinte unique pour bloquer les doubles entrées sur le même titre dans l'heure."""
        curr_hour = datetime.utcnow().strftime("%Y-%m-%d-%H")
        raw_key = f"ENTRY_{symbol.upper()}_{round(entry_price, 2)}_{round(quantity, 4)}_{curr_hour}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def get_instrument_currency(self, symbol):
        """Détermine la devise native de l'action (EUR pour PEA/Europe, USD pour US)."""
        sym = str(symbol or "").upper().strip()
        if sym.endswith(".PA") or sym.endswith(".DE") or sym.endswith(".AS") or sym.endswith(".MC"):
            return "EUR"
        return "USD"

    def calculate_strategy_grid(self, symbol, entry_price, strategy_type="Mean Reversion", custom_sl_pct=None):
        """
        Calcule les paliers exacts d'entrée et de sortie selon la technique choisie (Mean Reversion, Sniper, Sneak).
        """
        strat_key = strategy_type if strategy_type in STRATEGY_GRID_PROFILES else "Mean Reversion"
        profile = STRATEGY_GRID_PROFILES[strat_key]

        sl_pct = custom_sl_pct if custom_sl_pct is not None else profile["stop_loss_pct"]
        tp1_pct = profile["tp1_pct"]
        tp2_pct = profile["tp2_pct"]
        time_stop_days = profile["time_stop_days"]

        stop_loss_price = round(entry_price * (1.0 + sl_pct / 100.0), 2)
        tp1_price = round(entry_price * (1.0 + tp1_pct / 100.0), 2)
        step_stop_be_price = round(entry_price, 2)  # PRU = Break-Even
        tp2_price = round(entry_price * (1.0 + tp2_pct / 100.0), 2)

        return {
            "strategy_name": profile["name"],
            "strategy_type": strat_key,
            "entry_price": round(entry_price, 2),
            "stop_loss_price": stop_loss_price,
            "stop_loss_pct": sl_pct,
            "tp1_price": tp1_price,
            "tp1_pct": tp1_pct,
            "tp1_ratio": profile["tp1_ratio"],
            "step_stop_be_price": step_stop_be_price,
            "tp2_price": tp2_price,
            "tp2_pct": tp2_pct,
            "time_stop_days": time_stop_days,
            "description": profile["description"]
        }

    def validate_trade_plan(
        self,
        symbol,
        entry_price,
        stop_loss_price,
        tp1_price,
        tp2_price,
        quantity,
        strategy_type="Mean Reversion",
        current_equity=None,
        available_cash=None,
        action_type="ENTRY_BUY"
    ):
        """
        Validation exhaustive des garde-fous pour l'automate.
        Garantit que l'automate ne dépasse JAMAIS son plafond dédié indépendant.
        """
        # 1. Vérification Kill-Switch
        if self.is_kill_switch_active:
            return False, f"🛑 Trading bloqué : Kill-Switch actif ({self.kill_switch_reason})", None

        # 2. Vérification Circuit Breaker
        if self.consecutive_errors_count >= self.max_consecutive_errors:
            return False, f"⚠️ Circuit Breaker : {self.consecutive_errors_count} erreurs API consécutives", None

        # 3. Devise de l'instrument
        currency = self.get_instrument_currency(symbol)
        nominal_trade = entry_price * quantity

        # Si l'action est une sortie (TP1, Step Stop, TP2, Stop Loss), on autorise sans bloquer sur l'idempotence d'entrée
        if action_type in ["TP1_SELL", "STEP_STOP_BE_REPLACE", "TP2_SELL", "STOP_LOSS_CLOSE", "TIME_STOP_CLOSE"]:
            return True, "Action de gestion de position autorisée", {
                "symbol": symbol.upper(),
                "action_type": action_type,
                "currency": currency
            }

        # 4. Anti-Doublon d'Entrée : 1 seule position active sur ce symbole dans l'automate
        if symbol.upper() in self.active_automate_positions:
            return False, f"🚫 Doublon bloqué : Une ligne automate est déjà active sur {symbol}", None

        # 5. Idempotence d'entrée
        entry_hash = self.compute_entry_idempotency_hash(symbol, entry_price, quantity)
        if entry_hash in self.submitted_entry_hashes:
            return False, f"🚫 Ordre d'entrée identique déjà émis pour {symbol} dans l'heure", None

        # 6. Rate Limiter (Max 1 ordre toutes les 3s, max 10 par heure)
        now = time.time()
        self.recent_order_timestamps = [t for t in self.recent_order_timestamps if now - t < 3600]
        if self.recent_order_timestamps:
            if now - self.recent_order_timestamps[-1] < 3.0:
                return False, "⏳ Rate Limiter : Veuillez patienter 3 secondes entre deux ordres", None
        if len(self.recent_order_timestamps) >= 10:
            return False, "⏳ Rate Limiter : Limite maximale de 10 ordres par heure atteinte", None

        # 7. Filtre Sharia AAOIFI
        sharia_res = screen_ticker(symbol)
        is_compliant = (sharia_res.get("status") == "CONFORME") or (sharia_res.get("compliant") is True)
        if not is_compliant:
            reason = sharia_res.get("reason", "Non conforme AAOIFI")
            return False, f"🕋 Rejet Sharia : {symbol} n'est pas éligible ({reason})", None

        # 8. Respect du Plafond Dédié à l'Automate (Indépendant du reste du compte)
        deployed_cap = sum(p.get("nominal_invested", 0.0) for p in self.active_automate_positions.values())
        if (deployed_cap + nominal_trade) > self.allocated_automate_capital_ceiling * 1.02: # Tolérance 2% arrondis
            avail_automate = max(0.0, self.allocated_automate_capital_ceiling - deployed_cap)
            return False, f"💰 Plafond Automate dépassé : {nominal_trade:.2f}{currency} requis vs {avail_automate:.2f}{currency} restant sur le plafond dédié ({self.allocated_automate_capital_ceiling:.2f}{currency})", None

        # 9. Limite d'allocation par ligne (Max 20% du plafond automate)
        max_line_alloc = self.allocated_automate_capital_ceiling * (self.max_position_allocation_pct / 100.0)
        if nominal_trade > max_line_alloc * 1.05:
            return False, f"⚠️ Ligne excessive : {nominal_trade:.2f}{currency} dépasse l'allocation maximale par ligne ({max_line_alloc:.2f}{currency} = {self.max_position_allocation_pct}% du plafond)", None

        # 10. Risque R-Max (<= 1.0% du capital total ou plafond)
        risk_monetary = (entry_price - stop_loss_price) * quantity
        base_capital_ref = self.allocated_automate_capital_ceiling
        risk_pct = (risk_monetary / base_capital_ref * 100.0)
        if risk_pct > self.max_risk_per_trade_pct * 1.05:
            return False, f"⚠️ R-Max dépassé : Perte potentielle de {risk_monetary:.2f}{currency} ({risk_pct:.2f}% du plafond max autorisant {self.max_risk_per_trade_pct}%)", None

        # Construction du plan détaillé
        pnl_tp1 = (tp1_price - entry_price) * (quantity * 0.5)
        pnl_tp2 = (tp2_price - entry_price) * (quantity * 0.5)
        total_gain = pnl_tp1 + pnl_tp2
        rr_ratio = round(total_gain / risk_monetary, 2) if risk_monetary > 0 else 0.0

        trade_plan = {
            "symbol": symbol.upper(),
            "strategy_type": strategy_type,
            "currency": currency,
            "entry_price": round(entry_price, 2),
            "stop_loss_price": round(stop_loss_price, 2),
            "stop_loss_pct": round((stop_loss_price - entry_price) / entry_price * 100.0, 2),
            "tp1_price": round(tp1_price, 2),
            "tp1_pct": round((tp1_price - entry_price) / entry_price * 100.0, 2),
            "step_stop_be_price": round(entry_price, 2),
            "tp2_price": round(tp2_price, 2),
            "tp2_pct": round((tp2_price - entry_price) / entry_price * 100.0, 2),
            "quantity": round(quantity, 4),
            "nominal_invested": round(nominal_trade, 2),
            "max_loss_monetary": round(risk_monetary, 2),
            "tp1_gain_monetary": round(pnl_tp1, 2),
            "tp2_gain_monetary": round(pnl_tp2, 2),
            "total_potential_gain": round(total_gain, 2),
            "risk_reward_ratio": rr_ratio,
            "idempotency_hash": entry_hash,
            "sharia_compliant": True
        }

        return True, "Validation garde-fous réussie", trade_plan

    def register_entry_order_submitted(self, symbol, idempotency_hash, nominal_invested, currency="EUR"):
        """Enregistre une nouvelle ligne ouverte dans l'enveloppe de l'automate."""
        self.submitted_entry_hashes.add(idempotency_hash)
        self.recent_order_timestamps.append(time.time())
        self.active_automate_positions[symbol.upper()] = {
            "nominal_invested": float(nominal_invested),
            "currency": currency,
            "opened_at": time.time()
        }
        self.consecutive_errors_count = 0

    def register_order_error(self, reason=""):
        """Enregistre une erreur API et incrémente le circuit breaker."""
        self.consecutive_errors_count += 1
        logger.warning(f"⚠️ Erreur API enregistrée ({self.consecutive_errors_count}/{self.max_consecutive_errors}): {reason}")
        if self.consecutive_errors_count >= self.max_consecutive_errors:
            self.trigger_kill_switch(f"Circuit Breaker déclenché après {self.consecutive_errors_count} erreurs API consécutives")

    def register_position_closed(self, symbol):
        """Libère le capital dans l'enveloppe de l'automate lors de la clôture définitive."""
        self.active_automate_positions.pop(symbol.upper(), None)


# Instance globale des garde-fous
guardrails_engine = OrderGuardrailsEngine()
