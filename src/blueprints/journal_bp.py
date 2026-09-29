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

journal_bp = Blueprint("journal_bp", __name__)

# To prevent circular dependency for get_detailed_analysis
from src.analysis_engine import get_detailed_analysis
from src.analysis_engine import analysis_cache

@journal_bp.route("/api/journal/history")
def get_journal_history():
    """
    Retourne l'historique complet des trades clôturés avec statistiques de performance (Win Rate, P&L, etc.).
    """
    auto_sync = request.args.get("auto_sync", "true").lower() in ["true", "1", "yes"]
    if auto_sync:
        try:
            t212_trades = sync_trading212_history_to_journal()
            if t212_trades:
                existing = get_db_trade_journal() or []
                seen_ids = {str(t.get("id")) for t in existing if t.get("id")}
                new_t = [t for t in t212_trades if str(t.get("id")) not in seen_ids]
                if new_t:
                    batch_save_trade_journal(new_t)
        except Exception as err:
            logger.warning(f"Auto-sync Trading 212 skipped or failed: {err}")

    trades = get_db_trade_journal()
    stats = calculate_trading_performance_stats(trades)
    return safe_jsonify(
        {"success": True, "total": len(trades), "stats": stats, "trades": trades}
    )

@journal_bp.route("/api/journal/protocol_feedback")
def get_journal_protocol_feedback():
    """
    Analyse post-trade avancée des positions exécutées vs le Protocole en 8 étapes.
    Fournit le score de discipline, la décomposition par durée, les diagnostics TP1/TP2,
    l'analyse des cassures et les recommandations d'optimisation.
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    now = time.time()
    if (
        not force
        and _PROTOCOL_FEEDBACK_CACHE["data"]
        and (now - _PROTOCOL_FEEDBACK_CACHE["ts"] < 300)
    ):
        return safe_jsonify(_PROTOCOL_FEEDBACK_CACHE["data"])

    try:
        from src.protocol_feedback_engine import (
            analyze_executed_trades_against_protocol,
        )

        trades = get_db_trade_journal()
        analysis = analyze_executed_trades_against_protocol(trades)
        _PROTOCOL_FEEDBACK_CACHE["data"] = analysis
        _PROTOCOL_FEEDBACK_CACHE["ts"] = now
        return safe_jsonify(analysis)
    except Exception as e:
        logger.error(f"Erreur protocol feedback: {e}", exc_info=True)
        return safe_jsonify(
            {"success": False, "error": f"Erreur calcul feedback: {str(e)}"},
            status_code=500,
        )

@journal_bp.route("/api/journal/trade_audit/<trade_id>")
def get_trade_protocol_audit(trade_id):
    """
    Renvoie l'audit protocole détaillé pour un trade spécifique du journal.
    """
    try:
        from src.protocol_feedback_engine import audit_single_trade

        trades = get_db_trade_journal()
        target = next((t for t in trades if str(t.get("id")) == str(trade_id)), None)
        if not target:
            return safe_jsonify({"success": False, "error": "Trade introuvable."}), 404

        audit = audit_single_trade(target)
        return safe_jsonify({"success": True, "audit": audit})
    except Exception as e:
        logger.error(f"Erreur audit trade {trade_id}: {e}", exc_info=True)
        return safe_jsonify(
            {"success": False, "error": f"Erreur audit trade: {str(e)}"},
            status_code=500,
        )

@journal_bp.route("/api/journal/ticker_audit/<symbol>")
def get_ticker_protocol_audit(symbol):
    """
    Renvoie l'audit approfondi de l'ensemble des trades exécutés sur une action spécifique.
    """
    try:
        from src.protocol_feedback_engine import get_ticker_deep_audit

        trades = get_db_trade_journal()
        audit = get_ticker_deep_audit(symbol, trades)
        if not audit:
            return safe_jsonify(
                {
                    "success": False,
                    "error": f"Aucun trade trouvé pour le symbole {symbol}.",
                }
            ), 404

        return safe_jsonify({"success": True, "ticker_audit": audit})
    except Exception as e:
        logger.error(f"Erreur audit ticker {symbol}: {e}", exc_info=True)
        return safe_jsonify(
            {"success": False, "error": f"Erreur audit ticker: {str(e)}"},
            status_code=500,
        )

@journal_bp.route("/api/robot/logs", methods=["GET"])
def api_robot_logs():
    try:
        limit = int(request.args.get("limit", 500))
        logs = get_robot_logs(limit=limit)
        return jsonify({"status": "success", "data": logs}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@journal_bp.route("/api/monitoring/health", methods=["GET"])
def api_monitoring_health():
    try:
        db = check_db_health()
        metrics = get_system_metrics()
        return jsonify({"status": "success", "db": db, "metrics": metrics}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@journal_bp.route("/api/monitoring/compliance", methods=["GET"])
def api_monitoring_compliance():
    try:
        results = run_compliance_audit()
        return jsonify({"status": "success", "data": results}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@journal_bp.route("/api/monitoring/logs", methods=["GET"])
def api_monitoring_logs():
    try:
        lines = int(request.args.get("lines", 100))
        logs = get_system_recent_logs(lines=lines)
        return jsonify({"status": "success", "data": logs}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@journal_bp.route("/api/robot/mode", methods=["GET"])
def get_robot_mode():
    import json
    import os

    state_file = os.path.join(os.path.dirname(__file__), "robot_state.json")
    mode = "normal"
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                mode = json.load(f).get("mode", "normal")
        except:
            pass
    return safe_jsonify({"mode": mode})

@journal_bp.route("/api/robot/mode", methods=["POST"])
def set_robot_mode():
    import json
    import os

    state_file = os.path.join(os.path.dirname(__file__), "robot_state.json")
    data = request.json or {}
    mode = data.get("mode", "normal")
    try:
        with open(state_file, "w") as f:
            json.dump({"mode": mode}, f)
        return safe_jsonify({"success": True, "mode": mode})
    except Exception as e:
        return safe_jsonify({"success": False, "error": str(e)})

@journal_bp.route("/api/reviews/save", methods=["POST"])
def api_save_review():
    try:
        data = request.json
        if not data or not data.get("reference_id") or not data.get("reference_type"):
            return safe_jsonify({"success": False, "error": "Données invalides."})

        from src.db_connector import save_trade_review

        success = save_trade_review(data)
        if success:
            return safe_jsonify({"success": True})
        else:
            return safe_jsonify(
                {"success": False, "error": "Erreur BDD lors de l'enregistrement."}
            )
    except Exception as e:
        logger.error(f"Erreur API save review: {e}")
        return safe_jsonify({"success": False, "error": str(e)})

@journal_bp.route("/api/reviews/<reference_type>/<reference_id>", methods=["GET"])
def api_get_review(reference_type, reference_id):
    from src.db_connector import get_trade_review

    review = get_trade_review(reference_id, reference_type.upper())
    return safe_jsonify({"success": True, "review": review})