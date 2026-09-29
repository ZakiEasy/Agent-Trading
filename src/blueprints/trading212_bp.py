import os
import re
import math
import time
import logging
import threading
import concurrent.futures
from flask import Blueprint, jsonify, request, render_template, session, redirect, url_for
from flask_cors import CORS
from authlib.integrations.flask_client import OAuth
import yfinance as yf
from datetime import datetime
import requests
from werkzeug.middleware.proxy_fix import ProxyFix

logger = logging.getLogger("agent_trading")

from src.sharia_screen import screen_ticker
from src.macro_regime import get_macro_barometer
from src.market_data import (
    fetch_market_data,
    analyze_technical_setup,
    qualify_price_drop,
    check_earnings_blackout,
    check_fundamental_quality,
    categorize_ticker,
    calculate_sector_relative_strength,
    get_company_name,
    resolve_ticker_symbol,
)
from src.risk_manager import calculate_trade_sizing, calculate_confluence_score
from src.institutional_engine import generate_8_step_protocol_analysis
from src.backtest_engine import (
    BacktestEngine,
    CRISIS_PERIODS,
    run_all_crises_stress_test,
    run_single_ticker_10y_backtest,
)
from src.db_connector import (
    get_db_watchlist,
    get_watchlist_symbols,
    get_watchlist_item,
    add_or_update_watchlist_item,
    delete_from_watchlist,
    get_db_positions,
    save_or_update_position,
    batch_save_positions,
    close_db_position,
    get_db_trade_journal,
    batch_save_trade_journal,
    get_db_treasury_operations,
    get_trade_proposals_history,
    get_trade_proposal,
    batch_save_treasury_operations,
    log_trading_signal,
    get_recent_signals,
    get_robot_logs,
    log_macro_regime,
    get_latest_macro_regime,
)
from src.monitoring_engine import (
    check_db_health,
    get_system_metrics,
    run_compliance_audit,
    get_recent_logs as get_system_recent_logs,
)
from src.config import (
    BASE_DIR,
    DEFAULT_WATCHLIST,
    DEFAULT_MARKET_POOL,
    CAPITAL_REFERENCE_DEFAULT,
    MIN_DROP_PCT,
    MAX_DROP_PCT,
    TARGET_TP1_DEFAULT,
    TARGET_TP2_DEFAULT,
)
from src.utils.api_utils import safe_jsonify

# Extra specific imports from app.py
from src.trading212_connector import (
    test_trading212_connection,
    sync_trading212_history_to_journal,
    parse_trading212_csv,
    cancel_all_trading212_orders,
    get_trading212_open_orders,
    check_trading212_api_permissions,
)
from src.order_guardrails import guardrails_engine, STRATEGY_GRID_PROFILES
from src.trading212_execution_engine import execution_engine
from src.institutional_engine import (
    get_macro_sentiment_barometer,
    compute_institutional_rmax_sizing,
    scan_watchlist_institutional,
)

trading212_bp = Blueprint("trading212_bp", __name__)

# To prevent circular dependency for get_detailed_analysis
from src.analysis_engine import get_detailed_analysis
from src.analysis_engine import analysis_cache

@trading212_bp.route("/api/trading212/status")
def get_trading212_status():
    """
    Retourne le statut de connexion et le solde de trésorerie Trading 212.
    """
    test_res = test_trading212_connection()
    cash_data = get_trading212_cash()
    return safe_jsonify({"success": True, "connection": test_res, "cash": cash_data})

@trading212_bp.route("/api/trading212/portfolio")
def get_trading212_portfolio():
    """
    Retourne les positions ouvertes en direct depuis l'API Trading 212.
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    positions = get_trading212_open_positions(force_refresh=force)
    return safe_jsonify(
        {"success": True, "total": len(positions), "positions": positions}
    )

@trading212_bp.route("/api/trading212/config", methods=["GET", "POST"])
def configure_trading212():
    """
    Enregistre, persiste sur Supabase et teste la clé API Trading 212 fournie depuis l'interface.
    """
    if request.method == "GET":
        from src.trading212_connector import _RUNTIME_CONFIG

        key = (
            _RUNTIME_CONFIG.get("api_key") or _RUNTIME_CONFIG.get("read_api_key") or ""
        )
        masked = (
            (key[:6] + "..." + key[-4:]) if len(key) > 10 else ("***" if key else "")
        )
        return safe_jsonify(
            {
                "configured": bool(key),
                "masked_key": masked,
                "environment": _RUNTIME_CONFIG.get("environment", "live"),
            }
        )

    data = request.json or {}
    api_key = data.get("api_key", "")
    api_secret = data.get("api_secret", "")
    env = data.get("environment", "live")

    set_runtime_trading212_config(
        api_key=api_key, api_secret=api_secret, environment=env
    )
    test_res = test_trading212_connection(
        api_key=api_key, api_secret=api_secret, environment=env
    )

    return safe_jsonify(
        {"success": test_res.get("connected", False), "result": test_res}
    )

@trading212_bp.route("/api/trading212/sync_history", methods=["POST", "GET"])
def sync_trading212_history_endpoint():
    """
    Récupère l'historique des ordres exécutés sur Trading 212 via l'API et les intègre au Journal de Trading Supabase.
    """
    try:
        trades = sync_trading212_history_to_journal()
        if not trades:
            return safe_jsonify(
                {
                    "success": True,
                    "message": "Aucun nouvel ordre de vente exécuté trouvé sur Trading 212.",
                    "imported": 0,
                    "total_found": 0,
                }
            )

        existing_journal = get_db_trade_journal() or []
        seen_ids = {str(t.get("id")) for t in existing_journal if t.get("id")}
        new_trades = [t for t in trades if str(t.get("id")) not in seen_ids]

        if new_trades:
            batch_save_trade_journal(new_trades)

        return safe_jsonify(
            {
                "success": True,
                "message": f"Synchronisation Trading 212 réussie : {len(new_trades)} nouveau(x) trade(s) archivé(s) dans le Journal !",
                "imported": len(new_trades),
                "total_found": len(trades),
            }
        )
    except Exception as e:
        logger.error(f"Erreur sync history Trading 212: {e}", exc_info=True)
        return safe_jsonify(
            {
                "success": False,
                "error": f"Erreur lors de la synchronisation Trading 212: {str(e)}",
            },
            status_code=500,
        )

@trading212_bp.route("/api/trading212/execution/status")
def get_trading212_execution_status():
    """Retourne l'état des garde-fous, kill-switch, et métriques du moteur d'exécution."""
    guard_status = guardrails_engine.get_status()
    pending = execution_engine.get_pending_proposals()
    active_pos = execution_engine.get_active_positions()
    open_orders = get_trading212_open_orders()
    cash_data = get_trading212_cash() or {}

    return safe_jsonify(
        {
            "success": True,
            "guardrails": guard_status,
            "pending_proposals_count": len(pending),
            "active_positions_count": len(active_pos),
            "open_broker_orders_count": len(open_orders),
            "broker_cash": cash_data,
        }
    )

@trading212_bp.route("/api/trading212/execution/pending")
def get_trading212_pending_proposals():
    """Retourne la liste des propositions de trades en attente de Go Humain."""
    proposals = execution_engine.get_pending_proposals()
    return safe_jsonify(
        {"success": True, "count": len(proposals), "proposals": proposals}
    )

@trading212_bp.route("/api/trading212/execution/proposals_history")
def get_trading212_proposals_history():
    """Retourne l'historique complet des propositions de trades (approuvées, rejetées, expirées, en échec) stockées en BDD."""
    try:
        limit = request.args.get("limit", 100)
        status_filter = request.args.get("status")
        history = get_trade_proposals_history(limit=limit, status_filter=status_filter)
        return safe_jsonify(
            {"success": True, "count": len(history), "proposals_history": history}
        )
    except Exception as e:
        return safe_jsonify(
            {"success": False, "error": str(e), "proposals_history": []}, 500
        )

@trading212_bp.route("/api/trading212/execution/active_positions")
def get_trading212_active_managed_positions():
    """Retourne la liste des positions sous gestion active avec leur statut d'exécution."""
    closed = execution_engine.update_positions_monitoring()
    positions = execution_engine.get_active_positions()

    # Check T212 status
    from src.trading212_connector import (
        get_trading212_open_positions,
        get_trading212_open_orders,
    )

    open_pos = get_trading212_open_positions(force_refresh=True)
    open_ord = get_trading212_open_orders()

    pos_map = {p.get("ticker", "").split("_")[0]: p for p in open_pos}
    ord_map = {o.get("ticker", "").split("_")[0]: o for o in open_ord}

    for p in positions:
        sym = p.get("symbol")
        if sym in pos_map:
            p["t212_status"] = "Exécuté (Position Ouverte)"
            p["t212_color"] = "text-success"
        elif sym in ord_map:
            p["t212_status"] = "Enregistré (En attente broker)"
            p["t212_color"] = "text-warning"
        else:
            p["t212_status"] = "Introuvable / Clôturé"
            p["t212_color"] = "text-secondary"

    return safe_jsonify(
        {
            "success": True,
            "count": len(positions),
            "positions": positions,
            "recently_closed": closed,
        }
    )

@trading212_bp.route("/api/trading212/execution/permissions")
def get_trading212_api_permissions():
    """Diagnostique les droits réels accordés à la clé API Trading 212 (Lecture vs Ordres)."""
    diag = check_trading212_api_permissions()
    return safe_jsonify({"success": True, "permissions": diag})

@trading212_bp.route("/api/trading212/execution/strategies")
def get_trading212_strategy_profiles():
    """Retourne la liste des profils stratégiques (Mean Reversion, Sniper, Sneak) avec leurs grilles."""
    return safe_jsonify({"success": True, "strategies": STRATEGY_GRID_PROFILES})

@trading212_bp.route("/api/trading212/execution/propose", methods=["POST"])
def propose_trading212_trade():
    """Crée une proposition de trade adaptée à la stratégie et soumise aux garde-fous."""
    data = request.get_json() or {}
    symbol = data.get("symbol")
    entry_price = float(data.get("entry_price", 0.0))
    strategy_type = data.get("strategy_type", data.get("method", "Mean Reversion"))
    custom_sl = (
        float(data.get("stop_loss_price")) if data.get("stop_loss_price") else None
    )
    custom_tp1 = float(data.get("tp1_price")) if data.get("tp1_price") else None
    custom_tp2 = float(data.get("tp2_price")) if data.get("tp2_price") else None
    quantity = float(data.get("quantity")) if data.get("quantity") else None
    nominal_capital = (
        float(data.get("nominal_capital") or data.get("capital") or 0.0)
        if (data.get("nominal_capital") or data.get("capital"))
        else None
    )
    notes = data.get("notes", "")

    if not symbol or entry_price <= 0:
        return safe_jsonify(
            {
                "success": False,
                "error": "Paramètres symbol et entry_price obligatoires.",
            },
            status_code=400,
        )

    res = execution_engine.propose_trade(
        symbol=symbol,
        entry_price=entry_price,
        strategy_type=strategy_type,
        custom_sl_price=custom_sl,
        custom_tp1_price=custom_tp1,
        custom_tp2_price=custom_tp2,
        quantity=quantity,
        nominal_capital=nominal_capital,
        notes=notes,
    )
    return safe_jsonify(res)

@trading212_bp.route("/api/trading212/execution/update_proposal", methods=["POST"])
def update_trading212_proposal():
    """Permet à l'utilisateur de modifier le capital à investir ou le nombre d'actions d'une proposition existante."""
    data = request.get_json() or {}
    proposal_id = data.get("proposal_id")
    quantity = float(data.get("quantity")) if data.get("quantity") else None
    nominal_capital = (
        float(data.get("nominal_capital") or data.get("capital") or 0.0)
        if (data.get("nominal_capital") or data.get("capital"))
        else None
    )
    entry_price = float(data.get("entry_price")) if data.get("entry_price") else None
    custom_sl = (
        float(data.get("stop_loss_price")) if data.get("stop_loss_price") else None
    )
    custom_tp1 = float(data.get("tp1_price")) if data.get("tp1_price") else None
    custom_tp2 = float(data.get("tp2_price")) if data.get("tp2_price") else None

    if not proposal_id:
        return safe_jsonify(
            {"success": False, "error": "proposal_id obligatoire."}, status_code=400
        )

    res = execution_engine.update_proposal(
        proposal_id=proposal_id,
        quantity=quantity,
        nominal_capital=nominal_capital,
        entry_price=entry_price,
        custom_sl_price=custom_sl,
        custom_tp1_price=custom_tp1,
        custom_tp2_price=custom_tp2,
    )
    return safe_jsonify(res)

@trading212_bp.route("/api/trading212/execution/approve", methods=["POST"])
def approve_trading212_trade():
    """Validation 'GO HUMAIN' explicite : envoie l'ordre à Trading 212."""
    data = request.get_json() or {}
    proposal_id = data.get("proposal_id")
    order_type = data.get("order_type", "LIMIT")
    time_validity = data.get("time_validity", "DAY")

    if not proposal_id:
        return safe_jsonify(
            {"success": False, "error": "proposal_id obligatoire."}, status_code=400
        )

    res = execution_engine.approve_and_execute_trade(
        proposal_id=proposal_id, order_type=order_type, time_validity=time_validity
    )
    return safe_jsonify(res)

@trading212_bp.route("/api/trading212/execution/reject", methods=["POST"])
def reject_trading212_trade():
    """Rejet d'une proposition par l'utilisateur."""
    data = request.get_json() or {}
    proposal_id = data.get("proposal_id")
    reason = data.get("reason", "Rejeté par l'utilisateur")

    if not proposal_id:
        return safe_jsonify(
            {"success": False, "error": "proposal_id obligatoire."}, status_code=400
        )

    res = execution_engine.reject_trade_proposal(proposal_id=proposal_id, reason=reason)
    return safe_jsonify(res)

@trading212_bp.route("/api/trading212/execution/kill_switch", methods=["POST"])
def trigger_trading212_kill_switch():
    """Bouton d'urgence : annule tous les ordres et verrouille le trading."""
    data = request.get_json() or {}
    reason = data.get("reason", "Déclenché manuellement via l'interface")
    res = execution_engine.kill_all_and_freeze(reason=reason)
    return safe_jsonify(res)

@trading212_bp.route("/proposal/<proposal_id>")
def view_proposal(proposal_id):
    """Affiche une vue dédiée pour un trade en attente."""
    if os.getenv("GOOGLE_CLIENT_ID") and "user" not in session:
        return redirect(url_for("login", next=request.url))

    proposal = execution_engine.pending_proposals.get(proposal_id)
    if not proposal:
        # Check in history in case server restarted
        proposal = get_trade_proposal(proposal_id)
        if not proposal:
            return "Proposition introuvable ou déjà traitée.", 404

    return render_template(
        "proposal_detail.html", proposal=proposal, proposal_id=proposal_id
    )

@trading212_bp.route("/api/trading212/execution/reset_kill_switch", methods=["POST"])
def reset_trading212_kill_switch():
    """Débloque le système après intervention humaine."""
    res = guardrails_engine.reset_kill_switch()
    return safe_jsonify(res)

@trading212_bp.route("/api/trading212/execution/settings", methods=["POST"])
def update_trading212_guardrails_settings():
    """Met à jour les paramètres de sécurité (Plafond EUR, Plafond USD, Toggle Marché US, R-Max, Alloc Max)."""
    data = request.get_json() or {}

    def _get_val(keys):
        for k in keys:
            if k in data and data[k] is not None:
                return data[k]
        return None

    max_capital_eur = _get_val(
        ["automate_ceiling_eur", "max_capital_eur", "max_total_capital_ceiling"]
    )
    max_capital_usd = _get_val(["automate_ceiling_usd", "max_capital_usd"])
    max_risk_pct = data.get("max_risk_per_trade_pct")
    max_alloc_pct = data.get("max_position_allocation_pct")
    us_trading_enabled = data.get("us_trading_enabled")

    res = guardrails_engine.update_settings(
        automate_ceiling_eur=max_capital_eur,
        automate_ceiling_usd=max_capital_usd,
        max_risk_pct=max_risk_pct,
        max_alloc_pct=max_alloc_pct,
        us_trading_enabled=us_trading_enabled,
    )
    return safe_jsonify({"success": True, "settings": res})

@trading212_bp.route("/api/trading212/execution/toggle_us_trading", methods=["POST"])
def toggle_trading212_us_trading():
    """Bascule l'activation/désactivation du trading sur le marché américain (USD)."""
    data = request.get_json() or {}
    enable = data.get("enable")
    if enable is None:
        # Inverse l'état actuel si non spécifié
        enable = not guardrails_engine.us_trading_enabled
    else:
        enable = bool(enable)

    res = guardrails_engine.update_settings(us_trading_enabled=enable)
    state_str = "activé 🟢" if enable else "désactivé ⏸️ (Zone Euro active)"
    return safe_jsonify(
        {
            "success": True,
            "us_trading_enabled": enable,
            "message": f"Marché US {state_str}",
            "settings": res,
        }
    )