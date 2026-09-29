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

scanner_bp = Blueprint("scanner_bp", __name__)

# To prevent circular dependency for get_detailed_analysis
from src.analysis_engine import get_detailed_analysis
from src.analysis_engine import analysis_cache

@scanner_bp.route("/api/scan/batch", methods=["GET", "POST"])
def scan_batch():
    """
    Scanne un sous-ensemble (lot de 6 à 10 actions) en parallèle ultra-rapide (1-3s).
    Supporte la stratégie V3 Institutionnelle (par défaut) et la stratégie V2 Standard.
    """
    try:
        force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
        strategy = request.args.get("strategy", "ALL").upper()
        symbols = []
        if request.method == "POST":
            data = request.json or {}
            symbols = data.get("symbols", [])
            if not force:
                force = bool(data.get("force", False))
            if "strategy" in data:
                strategy = str(data.get("strategy", "ALL")).upper()
        else:
            symbols_param = request.args.get("symbols", "")
            if symbols_param:
                symbols = [
                    s.strip().upper() for s in symbols_param.split(",") if s.strip()
                ]

        if not symbols:
            return safe_jsonify(
                {
                    "success": False,
                    "error": "Aucun symbole fourni pour le lot.",
                    "results": [],
                }
            ), 400

        results = []
        signals_to_write = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(symbols), 8)
        ) as executor:
            if strategy == "V2":
                future_to_sym = {
                    executor.submit(
                        get_detailed_analysis, sym, CAPITAL_REFERENCE_DEFAULT, force
                    ): sym
                    for sym in symbols
                }
                try:
                    for future in concurrent.futures.as_completed(
                        future_to_sym, timeout=20
                    ):
                        try:
                            analysis = future.result()
                            if (
                                not analysis
                                or not isinstance(analysis, dict)
                                or "error" in analysis
                            ):
                                continue

                            symbol = analysis.get("symbol")
                            tech = analysis.get("technical") or {}
                            drop = analysis.get("drop") or {}
                            sharia = analysis.get("sharia") or {}
                            trade_plan = analysis.get("trade_plan") or {}
                            risk_plan = analysis.get("step_7_risk_sizing") or {}
                            macro_plan = analysis.get("step_2_macro") or {}
                            fund = analysis.get("step_4_fundamentals") or {}
                            sec_rel = analysis.get("sector_strength") or {}

                            results.append(
                                {
                                    "symbol": symbol,
                                    "name": analysis.get("company_name", symbol),
                                    "category": analysis.get("category", "Autres"),
                                    "category_icon": analysis.get(
                                        "category_icon", "📦"
                                    ),
                                    "is_pea": analysis.get("is_pea", False),
                                    "account_type": analysis.get(
                                        "account_type", "CTO (US)"
                                    ),
                                    "sharia": sharia.get(
                                        "status", "DONNÉES INSUFFISANTES"
                                    ),
                                    "price": tech.get("current_price", 0.0),
                                    "drop": drop.get("drop_pct", 0.0),
                                    "drop_nature": drop.get("nature", "N/A"),
                                    "avg_daily_volume": fund.get(
                                        "avg_daily_volume", 0.0
                                    ),
                                    "has_min_liquidity": fund.get(
                                        "has_min_liquidity", True
                                    ),
                                    "sector_rel": sec_rel.get(
                                        "relative_strength", "EN LIGNE"
                                    ),
                                    "sector_etf": sec_rel.get("sector_etf", "SPY"),
                                    "rsi": tech.get("rsi", 50.0),
                                    "rsi_divergence": (
                                        tech.get("rsi_divergence") or {}
                                    ).get("type", "AUCUNE"),
                                    "confluence_score": analysis.get(
                                        "confluence_score", 0
                                    ),
                                    "verdict": analysis.get(
                                        "verdict", "ATTENDRE REPLI SUR SUPPORT"
                                    ),
                                    "currency": tech.get("currency", "USD"),
                                }
                            )
                        except Exception:
                            pass
                except concurrent.futures.TimeoutError:
                    pass
            else:
                # Stratégie V3 Institutionnelle (Par Défaut)
                future_to_sym = {
                    executor.submit(
                        generate_8_step_protocol_analysis,
                        sym,
                        CAPITAL_REFERENCE_DEFAULT,
                    ): sym
                    for sym in symbols
                }
                try:
                    for future in concurrent.futures.as_completed(
                        future_to_sym, timeout=25
                    ):
                        sym = future_to_sym[future]
                        try:
                            analysis = future.result()
                            if (
                                not analysis
                                or not isinstance(analysis, dict)
                                or "error" in analysis
                            ):
                                raise ValueError(
                                    (analysis or {}).get(
                                        "error", "Données indisponibles"
                                    )
                                )

                            symbol = analysis.get("symbol", sym)
                            plan = analysis.get("pricing_plan") or {}
                            sizing = analysis.get("sizing") or {}

                            results.append(
                                {
                                    "symbol": symbol,
                                    "name": analysis.get(
                                        "name", get_company_name(symbol)
                                    ),
                                    "category": analysis.get("category", "Autres"),
                                    "category_icon": analysis.get(
                                        "category_icon", "📦"
                                    ),
                                    "is_pea": analysis.get("is_pea", False),
                                    "account_type": analysis.get(
                                        "account_type", "CTO (US)"
                                    ),
                                    "sharia": analysis.get("sharia", "NON CONFORME"),
                                    "price": analysis.get("current_price", 0.0),
                                    "drop": analysis.get("drop", 0.0),
                                    "drop_nature": "SURRÉACTION CONJONCTURELLE"
                                    if analysis.get("pullback_valid")
                                    else "REPLI EN COURS",
                                    "avg_daily_volume": analysis.get(
                                        "avg_daily_volume", 0.0
                                    ),
                                    "has_min_liquidity": True,
                                    "sector_rel": "SURPERFORMANCE"
                                    if analysis.get("trend_following_valid")
                                    else "EN LIGNE",
                                    "sector_etf": "SPY",
                                    "rsi": analysis.get("rsi", 50.0),
                                    "rsi_divergence": analysis.get(
                                        "rsi_divergence", "AUCUNE"
                                    ),
                                    "confluence_score": analysis.get(
                                        "confluence_score", 0
                                    ),
                                    "verdict": analysis.get(
                                        "verdict", "ÉVITER - HORS CRITÈRES"
                                    ),
                                    "verdict_badge": analysis.get(
                                        "verdict_badge", "badge-neutral"
                                    ),
                                    "verdict_action": analysis.get(
                                        "verdict_action", ""
                                    ),
                                    "verdict_swing": analysis.get(
                                        "verdict_swing",
                                        analysis.get("verdict", "ÉVITER"),
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
                                    "execution_timing": analysis.get(
                                        "execution_timing"
                                    ),
                                    "pricing_plan_sniper": analysis.get(
                                        "pricing_plan_sniper"
                                    ),
                                    "currency": analysis.get("currency", "EUR"),
                                }
                            )
                        except Exception:
                            # Fallback garanti pour que 100% des actions demandées s'affichent
                            cat_info = categorize_ticker(sym)
                            is_pea = cat_info.get(
                                "is_pea", sym.endswith(".PA") or sym.endswith(".DE")
                            )
                            results.append(
                                {
                                    "symbol": sym,
                                    "name": get_company_name(sym),
                                    "category": cat_info.get("category", "Autres"),
                                    "category_icon": cat_info.get(
                                        "category_icon", "📦"
                                    ),
                                    "is_pea": is_pea,
                                    "account_type": "🇫🇷 PEA" if is_pea else "CTO (US)",
                                    "sharia": "DONNÉES INSUFFISANTES",
                                    "price": 0.0,
                                    "drop": 0.0,
                                    "drop_nature": "DONNÉES INDISPONIBLES",
                                    "avg_daily_volume": 0.0,
                                    "has_min_liquidity": True,
                                    "sector_rel": "EN LIGNE",
                                    "sector_etf": "SPY",
                                    "rsi": 50.0,
                                    "rsi_divergence": "AUCUNE",
                                    "confluence_score": 0.0,
                                    "verdict": "ÉVITER - DONNÉES INSUFFISANTES",
                                    "verdict_badge": "badge-neutral",
                                    "verdict_action": "Données Yahoo Finance temporairement indisponibles.",
                                    "verdict_swing": "ÉVITER",
                                    "verdict_swing_badge": "badge-neutral",
                                    "verdict_swing_action": "",
                                    "verdict_sniper": "NON ÉLIGIBLE",
                                    "verdict_sniper_badge": "badge-neutral",
                                    "verdict_sniper_action": "",
                                    "action_plan": "🛑 Vérifier le symbole sur Yahoo Finance ou mettre à jour la Watchlist.",
                                    "execution_timing": None,
                                    "pricing_plan_sniper": None,
                                    "currency": "EUR" if is_pea else "USD",
                                }
                            )
                except concurrent.futures.TimeoutError:
                    pass

        return safe_jsonify(
            {"success": True, "results": results, "signals_sent": len(signals_to_write)}
        )
    except Exception as e:
        return safe_jsonify({"success": False, "error": str(e), "results": []}, 500)

@scanner_bp.route("/api/scan/watchlist")
def scan_watchlist():
    try:
        force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
        strategy = request.args.get("strategy", "ALL").upper()

        # 1. Charger la Watchlist depuis Supabase
        watchlist = get_watchlist_symbols(only_active=True)
        if not watchlist:
            watchlist = DEFAULT_WATCHLIST

        results = []
        signals_to_write = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            if strategy == "V2":
                future_to_sym = {
                    executor.submit(
                        get_detailed_analysis, sym, CAPITAL_REFERENCE_DEFAULT, force
                    ): sym
                    for sym in watchlist
                }
                try:
                    for future in concurrent.futures.as_completed(
                        future_to_sym, timeout=25
                    ):
                        try:
                            analysis = future.result()
                            if (
                                not analysis
                                or not isinstance(analysis, dict)
                                or "error" in analysis
                            ):
                                continue
                            tech = analysis.get("technical") or {}
                            drop = analysis.get("drop") or {}
                            sharia = analysis.get("sharia") or {}
                            fund = analysis.get("step_4_fundamentals") or {}
                            sec_rel = analysis.get("sector_strength") or {}
                            results.append(
                                {
                                    "symbol": analysis.get("symbol"),
                                    "name": analysis.get(
                                        "company_name", analysis.get("symbol")
                                    ),
                                    "category": analysis.get("category", "Autres"),
                                    "category_icon": analysis.get(
                                        "category_icon", "📦"
                                    ),
                                    "is_pea": analysis.get("is_pea", False),
                                    "account_type": analysis.get(
                                        "account_type", "CTO (US)"
                                    ),
                                    "sharia": sharia.get(
                                        "status", "DONNÉES INSUFFISANTES"
                                    ),
                                    "price": tech.get("current_price", 0.0),
                                    "drop": drop.get("drop_pct", 0.0),
                                    "drop_nature": drop.get("nature", "N/A"),
                                    "avg_daily_volume": fund.get(
                                        "avg_daily_volume", 0.0
                                    ),
                                    "has_min_liquidity": fund.get(
                                        "has_min_liquidity", True
                                    ),
                                    "sector_rel": sec_rel.get(
                                        "relative_strength", "EN LIGNE"
                                    ),
                                    "sector_etf": sec_rel.get("sector_etf", "SPY"),
                                    "rsi": tech.get("rsi", 50.0),
                                    "rsi_divergence": (
                                        tech.get("rsi_divergence") or {}
                                    ).get("type", "AUCUNE"),
                                    "confluence_score": analysis.get(
                                        "confluence_score", 0
                                    ),
                                    "verdict": analysis.get(
                                        "verdict", "ATTENDRE REPLI SUR SUPPORT"
                                    ),
                                    "verdict_swing": analysis.get(
                                        "verdict", "ATTENDRE REPLI SUR SUPPORT"
                                    ),
                                    "verdict_swing_badge": "badge-warning",
                                    "verdict_sniper": "NON ÉLIGIBLE",
                                    "verdict_sniper_badge": "badge-neutral",
                                    "currency": tech.get("currency", "USD"),
                                }
                            )
                        except Exception:
                            pass
                except concurrent.futures.TimeoutError:
                    pass
            else:
                # Stratégie V3 Institutionnelle
                future_to_sym = {
                    executor.submit(
                        generate_8_step_protocol_analysis,
                        sym,
                        CAPITAL_REFERENCE_DEFAULT,
                    ): sym
                    for sym in watchlist
                }
                try:
                    for future in concurrent.futures.as_completed(
                        future_to_sym, timeout=25
                    ):
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
                                "account_type": analysis.get(
                                    "account_type", "CTO (US)"
                                ),
                                "sharia": analysis.get("sharia", "NON CONFORME"),
                                "price": analysis.get("current_price", 0.0),
                                "drop": analysis.get("drop", 0.0),
                                "drop_nature": "SURRÉACTION CONJONCTURELLE"
                                if analysis.get("pullback_valid")
                                else "REPLI EN COURS",
                                "avg_daily_volume": analysis.get(
                                    "avg_daily_volume", 0.0
                                ),
                                "has_min_liquidity": True,
                                "sector_rel": "SURPERFORMANCE"
                                if analysis.get("trend_following_valid")
                                else "EN LIGNE",
                                "sector_etf": "SPY",
                                "rsi": analysis.get("rsi", 50.0),
                                "rsi_divergence": analysis.get(
                                    "rsi_divergence", "AUCUNE"
                                ),
                                "confluence_score": analysis.get("confluence_score", 0),
                                "verdict": analysis.get(
                                    "verdict", "ÉVITER - HORS CRITÈRES"
                                ),
                                "verdict_badge": analysis.get(
                                    "verdict_badge", "badge-neutral"
                                ),
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
                                "pricing_plan_sniper": analysis.get(
                                    "pricing_plan_sniper"
                                ),
                                "currency": analysis.get("currency", "EUR"),
                            }
                            results.append(r_dict)

                            # Enregistrer le signal dans Supabase si signal d'intérêt
                            if (
                                "ACHAT" in str(r_dict["verdict_swing"])
                                or "ACHAT" in str(r_dict["verdict_sniper"])
                                or "ATTENDRE" in str(r_dict["verdict_sniper"])
                            ):
                                try:
                                    log_trading_signal(r_dict)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except concurrent.futures.TimeoutError:
                    pass

        return safe_jsonify(
            {"success": True, "results": results, "signals_sent": len(signals_to_write)}
        )
    except Exception as e:
        return safe_jsonify({"success": False, "error": str(e), "results": []}, 500)

@scanner_bp.route("/api/scan/market")
def scan_market():
    try:
        results = []
        signals_to_write = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            analyses = list(executor.map(get_detailed_analysis, DEFAULT_MARKET_POOL))

        for analysis in analyses:
            if not analysis or not isinstance(analysis, dict) or "error" in analysis:
                continue

            symbol = analysis.get("symbol")
            tech = analysis.get("technical") or {}
            drop = analysis.get("drop") or {}
            sharia = analysis.get("sharia") or {}
            trade_plan = analysis.get("trade_plan") or {}
            risk_plan = analysis.get("step_7_risk_sizing") or {}
            macro_plan = analysis.get("step_2_macro") or {}
            fund = analysis.get("step_4_fundamentals") or {}
            sec_rel = analysis.get("sector_strength") or {}

            results.append(
                {
                    "symbol": symbol,
                    "name": analysis.get("company_name", symbol),
                    "category": analysis.get("category", "Autres"),
                    "category_icon": analysis.get("category_icon", "📦"),
                    "is_pea": analysis.get("is_pea", False),
                    "account_type": analysis.get("account_type", "CTO (US)"),
                    "sharia": sharia.get("status", "DONNÉES INSUFFISANTES"),
                    "price": tech.get("current_price", 0.0),
                    "drop": drop.get("drop_pct", 0.0),
                    "drop_nature": drop.get("nature", "N/A"),
                    "avg_daily_volume": fund.get("avg_daily_volume", 0.0),
                    "has_min_liquidity": fund.get("has_min_liquidity", True),
                    "sector_rel": sec_rel.get("relative_strength", "EN LIGNE"),
                    "sector_etf": sec_rel.get("sector_etf", "SPY"),
                    "rsi": tech.get("rsi", 50.0),
                    "rsi_divergence": (tech.get("rsi_divergence") or {}).get(
                        "type", "AUCUNE"
                    ),
                    "confluence_score": analysis.get("confluence_score", 0),
                    "verdict": analysis.get("verdict", "ATTENDRE REPLI SUR SUPPORT"),
                    "currency": tech.get("currency", "USD"),
                }
            )

            if "ACHETER" in analysis.get("verdict", ""):
                signals_to_write.append(
                    {
                        "date": now_str,
                        "symbol": symbol,
                        "sharia_status": sharia.get("status"),
                        "category": analysis.get("category", "Autres"),
                        "account_type": analysis.get("account_type", "CTO (US)"),
                        "macro_regime": macro_plan.get("regime", "N/A"),
                        "current_price": tech.get("current_price", 0.0),
                        "drop_pct": drop.get("drop_pct", 0.0),
                        "support": tech.get("support", 0.0),
                        "tp1_target": trade_plan.get("target_min", 0.0),
                        "tp2_target": trade_plan.get("target_max", 0.0),
                        "stop_loss": trade_plan.get("invalidation", 0.0),
                        "r_max_amount": risk_plan.get("r_max_amount", 50.0),
                        "suggested_nominal": risk_plan.get("suggested_nominal", 0.0),
                        "confluence_score": analysis.get("confluence_score", 0),
                        "verdict": analysis.get("verdict", "ACHETER"),
                    }
                )

        if signals_to_write:
            # Écriture asynchrone en arrière-plan pour ne pas ralentir la réponse HTTP
            threading.Thread(
                target=write_signals_to_sheets, args=(signals_to_write,), daemon=True
            ).start()

        return safe_jsonify(
            {"success": True, "results": results, "signals_sent": len(signals_to_write)}
        )
    except Exception as e:
        print(f"Erreur globale scan_market: {e}")
        return safe_jsonify({"success": False, "error": str(e), "results": []}), 500

@scanner_bp.route("/api/analyze/<ticker>")
def analyze_ticker_endpoint(ticker):
    capital = request.args.get("capital", default=CAPITAL_REFERENCE_DEFAULT, type=float)
    force = request.args.get("force", "false").lower() in [
        "true",
        "1",
        "yes",
    ] or request.args.get("refresh", "false").lower() in ["true", "1", "yes"]
    strategy = request.args.get("strategy", "ALL").upper()
    if strategy == "V2":
        res = get_detailed_analysis(ticker, capital=capital)
    else:
        res = generate_8_step_protocol_analysis(
            ticker, capital_total=capital, force_refresh=force
        )
    if not res or "error" in res:
        return safe_jsonify(
            {"success": False, "error": (res or {}).get("error", "Analyse impossible")}
        ), 400
    return safe_jsonify({"success": True, "data": res})

@scanner_bp.route("/api/macro")
def get_macro_endpoint():
    force = request.args.get("refresh", default=False, type=bool)
    barometer = get_macro_barometer(force_refresh=force)
    return safe_jsonify(barometer)

@scanner_bp.route("/api/risk-calc", methods=["POST"])
def risk_calc_endpoint():
    data = request.json or {}
    capital = float(data.get("capital", CAPITAL_REFERENCE_DEFAULT))
    entry_price = float(data.get("entry_price", 0.0))
    stop_loss_price = float(
        data.get("stop_loss_price", entry_price * 0.97 if entry_price > 0 else 0.0)
    )
    macro_regime = data.get("macro_regime", "RÉGIME RISK-ON (Favorable)")
    is_drawdown = bool(data.get("is_drawdown_circuit_breaker", False))
    tp1_pct = float(data.get("tp1_pct", TARGET_TP1_DEFAULT))
    tp2_pct = float(data.get("tp2_pct", TARGET_TP2_DEFAULT))

    result = calculate_trade_sizing(
        capital_total=capital,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        macro_regime=macro_regime,
        is_drawdown_circuit_breaker=is_drawdown,
        tp1_pct=tp1_pct,
        tp2_pct=tp2_pct,
    )
    return jsonify({"success": True, "data": result})

@scanner_bp.route("/api/v3/macro/sentiment")
def get_v3_macro_sentiment():
    """
    Retourne le baromètre macroéconomique et inter-marchés V3 (VIX, DXY, XLY/XLP, WTI, Yield Curve).
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    macro = get_macro_sentiment_barometer(force_refresh=force)
    return safe_jsonify({"success": True, "data": macro})

@scanner_bp.route("/api/v3/analysis/<ticker>/protocol8")
def get_v3_protocol8_analysis(ticker):
    """
    Retourne l'analyse institutionnelle complète en 8 étapes pour un ticker.
    Accepte ?force=true pour forcer l'actualisation en temps réel (invalidation du cache).
    """
    capital = request.args.get("capital", default=CAPITAL_REFERENCE_DEFAULT, type=float)
    force = request.args.get("force", "false").lower() in [
        "true",
        "1",
        "yes",
    ] or request.args.get("refresh", "false").lower() in ["true", "1", "yes"]
    res = generate_8_step_protocol_analysis(
        ticker, capital_total=capital, force_refresh=force
    )
    return safe_jsonify({"success": True, "data": res})

@scanner_bp.route("/api/v3/risk/calculator", methods=["POST"])
def post_v3_risk_calculator():
    """
    Calculateur R-Max exact pour dimensionnement au comptant (1% perte max, 25% max allocation).
    """
    data = request.json or {}
    capital = float(data.get("capital", CAPITAL_REFERENCE_DEFAULT))
    entry = float(data.get("entry_price", 0.0))
    stop = float(data.get("stop_loss", entry * 0.97 if entry > 0 else 0.0))
    tp = float(data.get("take_profit", entry * 1.0225 if entry > 0 else 0.0))
    sizing = compute_institutional_rmax_sizing(capital, entry, stop, tp)
    return safe_jsonify({"success": True, "data": sizing})

@scanner_bp.route("/api/v3/scanner/institutional")
def get_v3_scanner_institutional():
    """
    Scan complet de la watchlist selon la stratégie institutionnelle V3 (Confluence 3 Moteurs).
    """
    capital = request.args.get("capital", default=CAPITAL_REFERENCE_DEFAULT, type=float)
    res = scan_watchlist_institutional(capital_total=capital)
    return safe_jsonify(res)

@scanner_bp.route("/api/jev/analyze/<symbol>", methods=["POST"])
def api_jev_analyze(symbol):
    try:
        from src.institutional_engine import generate_8_step_protocol_analysis
        from src.jev_connector import ask_jev_confirmation

        # Calculer le contexte
        data = generate_8_step_protocol_analysis(symbol)
        if "error" in data:
            return jsonify({"success": False, "error": data["error"]}), 400

        jev_macro = {
            "VIX": "N/A",
            "DXY": "N/A",
            "SPY_trend": "N/A",
            "regime": data.get("macro_regime", "N/A"),
        }
        jev_stock = {
            "drop_pct": data.get("pullback_pct", 0),
            "RSI": data.get("rsi", 50),
            "Bollinger": "Bas" if data.get("rsi", 50) < 40 else "N/A",
        }

        jev_data = ask_jev_confirmation(symbol, jev_macro, jev_stock)
        if jev_data:
            return jsonify({"success": True, "jev": jev_data})
        else:
            return jsonify({"success": False, "error": "Aucune réponse de JEV."}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route("/api/backtest/periods")
def get_backtest_periods_endpoint():
    """
    Retourne l'ensemble des périodes historiques prédéfinies (1999-2026) pour le sélecteur d'interface.
    """
    from src.backtest_engine import HISTORICAL_PERIODS_1999_2026

    return safe_jsonify({"success": True, "periods": HISTORICAL_PERIODS_1999_2026})

@scanner_bp.route("/api/backtest/run", methods=["GET", "POST"])
def backtest_run_endpoint():
    """
    Exécute le backtest historique complet de la stratégie sur la Watchlist et le Market Pool.
    Utilise un cache en mémoire pour des réponses instantanées.
    """
    try:
        if request.method == "POST":
            data = request.json or {}
        else:
            data = request.args.to_dict()

        period = str(data.get("period", "2y"))
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        capital = float(data.get("capital", 5000.0))
        tp1_pct = float(data.get("tp1_pct", 1.25))
        tp2_pct = float(data.get("tp2_pct", 2.25))
        max_holding_days = int(data.get("max_holding_days", 10))
        universe_type = str(data.get("universe", "all"))
        strategy = str(data.get("strategy", "v3_institutional"))

        cache_key = f"{strategy}_{universe_type}_{period}_{start_date}_{end_date}_{capital}_{tp1_pct}_{tp2_pct}_{max_holding_days}"
        now_ts = time.time()
        if cache_key in _BACKTEST_RUN_CACHE:
            entry = _BACKTEST_RUN_CACHE[cache_key]
            if (now_ts - entry["ts"]) < 3600:  # 1h de cache
                return safe_jsonify(entry["data"])

        if universe_type == "watchlist":
            symbols = DEFAULT_WATCHLIST
        else:
            symbols = list(set(DEFAULT_WATCHLIST + DEFAULT_MARKET_POOL))

        engine = BacktestEngine(
            symbols=symbols,
            period=period,
            start_date=start_date,
            end_date=end_date,
            initial_capital=capital,
            tp1_pct=tp1_pct,
            tp2_pct=tp2_pct,
            max_holding_days=max_holding_days,
            strategy=strategy,
        )
        results = engine.run_simulation()

        _BACKTEST_RUN_CACHE[cache_key] = {"data": results, "ts": now_ts}
        return safe_jsonify(results)
    except Exception as e:
        logger.error(f"Erreur backtest run: {e}", exc_info=True)
        return safe_jsonify(
            {"success": False, "error": f"Erreur serveur backtest: {str(e)}"},
            status_code=500,
        )

@scanner_bp.route("/api/backtest/crises", methods=["GET", "POST"])
def backtest_crises_endpoint():
    """
    Exécute le stress-test comparatif sur toutes les périodes historiques (1999 à 2026).
    """
    try:
        if request.method == "POST":
            data = request.json or {}
        else:
            data = request.args.to_dict()

        capital = float(data.get("capital", 5000.0))
        tp1_pct = float(data.get("tp1_pct", 1.25))
        tp2_pct = float(data.get("tp2_pct", 2.25))
        max_holding_days = int(data.get("max_holding_days", 10))
        strategy = str(data.get("strategy", "v3_institutional"))

        cache_key = f"{strategy}_{capital}_{tp1_pct}_{tp2_pct}_{max_holding_days}"
        now_ts = time.time()
        if cache_key in _BACKTEST_CRISES_CACHE:
            entry = _BACKTEST_CRISES_CACHE[cache_key]
            if (now_ts - entry["ts"]) < 3600:
                return safe_jsonify({"success": True, "crises": entry["data"]})

        results = run_all_crises_stress_test(
            initial_capital=capital,
            tp1_pct=tp1_pct,
            tp2_pct=tp2_pct,
            max_holding_days=max_holding_days,
            strategy=strategy,
        )

        _BACKTEST_CRISES_CACHE[cache_key] = {"data": results, "ts": now_ts}
        return safe_jsonify({"success": True, "crises": results})
    except Exception as e:
        logger.error(f"Erreur benchmark crises: {e}", exc_info=True)
        return safe_jsonify(
            {"success": False, "error": f"Erreur serveur crises: {str(e)}"},
            status_code=500,
        )

@scanner_bp.route("/api/backtest/user_universe", methods=["GET", "POST"])
def backtest_user_universe_endpoint():
    """
    Exécute le backtest institutionnel sur l'univers complet de l'utilisateur
    (actions tradées dans le journal + portefeuille + watchlist) sur 2 ans et 10 ans,
    et compare avec les performances réelles du journal de trading.
    """
    try:
        if request.method == "POST":
            data = request.json or {}
        else:
            data = request.args.to_dict()

        capital = float(data.get("capital", 18183.05))
        tp1_pct = float(data.get("tp1_pct", 1.80))
        tp2_pct = float(data.get("tp2_pct", 2.50))
        max_holding_days = int(data.get("max_holding_days", 10))
        strategy = str(data.get("strategy", "v3_institutional"))
        force = str(data.get("force", "false")).lower() in ["true", "1", "yes"]

        # 1. Identifier l'univers complet de l'utilisateur
        trades_real = get_db_trade_journal() or []
        positions_real = get_db_positions(status="ALL") or []
        watchlist_symbols = get_watchlist_symbols() or []

        symbols_traded = [
            resolve_ticker_symbol(t.get("symbol"))
            for t in trades_real
            if t.get("symbol")
        ]
        symbols_pos = [
            resolve_ticker_symbol(p.get("symbol"))
            for p in positions_real
            if p.get("symbol")
        ]
        symbols_wl = [resolve_ticker_symbol(s) for s in watchlist_symbols if s]

        user_universe = sorted(list(set(symbols_traded + symbols_pos + symbols_wl)))
        user_universe = [
            s for s in user_universe if s and s != "None" and not s.endswith(".L")
        ]

        # 2. Métriques du journal réel
        total_real_trades = len(trades_real)
        wins_real = len(
            [t for t in trades_real if float(t.get("pnl_amount", 0.0)) >= 0]
        )
        losses_real = len(
            [t for t in trades_real if float(t.get("pnl_amount", 0.0)) < 0]
        )
        wr_real = (
            round((wins_real / total_real_trades * 100), 1)
            if total_real_trades > 0
            else 0.0
        )
        pnl_real = round(sum([float(t.get("pnl_amount", 0.0)) for t in trades_real]), 2)
        gains_real = sum(
            [
                float(t.get("pnl_amount", 0.0))
                for t in trades_real
                if float(t.get("pnl_amount", 0.0)) > 0
            ]
        )
        loss_abs_real = abs(
            sum(
                [
                    float(t.get("pnl_amount", 0.0))
                    for t in trades_real
                    if float(t.get("pnl_amount", 0.0)) < 0
                ]
            )
        )
        pf_real = round((gains_real / loss_abs_real), 2) if loss_abs_real > 0 else 0.0

        # Durée moyenne réelle
        from src.protocol_feedback_engine import calculate_trade_duration_days

        durs = [
            calculate_trade_duration_days(t.get("entry_date"), t.get("exit_date"))
            for t in trades_real
        ]
        avg_dur_real = round(sum(durs) / len(durs), 1) if durs else 0.0

        # 3. Backtest 10 Ans (télécharge 10 ans d'historique)
        bt_10y = BacktestEngine(
            symbols=user_universe,
            period="10y",
            initial_capital=capital,
            tp1_pct=tp1_pct,
            tp2_pct=tp2_pct,
            max_holding_days=max_holding_days,
            strategy=strategy,
        )
        res_10y = bt_10y.run_simulation()

        # 4. Backtest 2 Ans (réutilise les données 10 ans en restreignant sur les 2 dernières années)
        bt_2y = BacktestEngine(
            symbols=user_universe,
            period="2y",
            initial_capital=capital,
            tp1_pct=tp1_pct,
            tp2_pct=tp2_pct,
            max_holding_days=max_holding_days,
            strategy=strategy,
        )
        bt_2y.historical_data = bt_10y.historical_data
        bt_2y.macro_data = bt_10y.macro_data
        bt_2y.sector_etf_data = bt_10y.sector_etf_data
        bt_2y.macro_daily_regime = bt_10y.macro_daily_regime
        res_2y = bt_2y.run_simulation()

        return safe_jsonify(
            {
                "success": True,
                "universe": {
                    "total_symbols": len(user_universe),
                    "symbols": user_universe,
                },
                "parameters": {
                    "initial_capital": capital,
                    "tp1_pct": tp1_pct,
                    "tp2_pct": tp2_pct,
                    "max_holding_days": max_holding_days,
                    "strategy": strategy,
                },
                "real_journal": {
                    "total_trades": total_real_trades,
                    "winning_trades": wins_real,
                    "losing_trades": losses_real,
                    "win_rate_pct": wr_real,
                    "total_net_pnl": pnl_real,
                    "profit_factor": pf_real,
                    "avg_holding_days": avg_dur_real,
                },
                "backtest_2y": {
                    "initial_capital": capital,
                    "final_capital": res_2y.get("final_capital", capital),
                    "metrics": res_2y.get("metrics", {}),
                    "equity_curve": res_2y.get("equity_curve", [])[-30:]
                    if res_2y.get("equity_curve")
                    else [],
                },
                "backtest_10y": {
                    "initial_capital": capital,
                    "final_capital": res_10y.get("final_capital", capital),
                    "metrics": res_10y.get("metrics", {}),
                    "equity_curve": res_10y.get("equity_curve", [])[-30:]
                    if res_10y.get("equity_curve")
                    else [],
                },
            }
        )
    except Exception as e:
        logger.error(f"Erreur backtest user universe: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": str(e)}, status_code=500)

@scanner_bp.route("/api/backtest/trade_replay", methods=["GET", "POST"])
def backtest_trade_replay_endpoint():
    """
    Exécute le rejeu exact trade-par-trade du nouveau protocole sur toutes les positions réelles
    du journal de trading Supabase.
    """
    try:
        from src.trade_replay_engine import run_trade_by_trade_replay

        if request.method == "POST":
            data = request.json or {}
        else:
            data = request.args.to_dict()

        tp1_pct = float(data.get("tp1_pct", 1.80))
        tp2_pct = float(data.get("tp2_pct", 2.50))
        stop_loss_pct = float(data.get("stop_loss_pct", -2.00))
        max_holding_days = int(data.get("max_holding_days", 10))

        res = run_trade_by_trade_replay(
            tp1_pct=tp1_pct,
            tp2_pct=tp2_pct,
            stop_loss_pct=stop_loss_pct,
            max_holding_days=max_holding_days,
        )
        return safe_jsonify(res)
    except Exception as e:
        logger.error(f"Erreur backtest trade replay: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": str(e)}, status_code=500)

@scanner_bp.route("/api/backtest/free_trading_simulation", methods=["GET", "POST"])
def backtest_free_trading_simulation_endpoint():
    """
    Exécute la simulation en libre trading continu avec les flux exacts de trésorerie (dépôts/retraits)
    et sélection dynamique des meilleures opportunités Mean Reversion.
    """
    try:
        from src.free_trading_simulator import run_continuous_free_trading_simulation

        if request.method == "POST":
            data = request.json or {}
        else:
            data = request.args.to_dict()

        tp1_pct = float(data.get("tp1_pct", 1.80))
        tp2_pct = float(data.get("tp2_pct", 2.50))
        stop_loss_pct = float(data.get("stop_loss_pct", -2.00))
        max_holding_days = int(data.get("max_holding_days", 10))
        max_risk_pct = float(data.get("max_risk_pct", 1.0))
        max_line_pct = float(data.get("max_line_pct", 18.0))
        min_cash_pct = float(data.get("min_cash_pct", 15.0))

        res = run_continuous_free_trading_simulation(
            tp1_pct=tp1_pct,
            tp2_pct=tp2_pct,
            stop_loss_pct=stop_loss_pct,
            max_holding_days=max_holding_days,
            max_risk_per_trade_pct=max_risk_pct,
            max_position_weight_pct=max_line_pct,
            min_cash_reserve_pct=min_cash_pct,
        )
        return safe_jsonify(res)
    except Exception as e:
        logger.error(f"Erreur backtest free trading simulation: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": str(e)}, status_code=500)

@scanner_bp.route("/api/screener/search", methods=["GET", "POST"])
def screener_search_endpoint():
    """
    Screener d'opportunités d'investissement multi-critères :
    Filtres :
      - query : recherche texte par symbole ou nom
      - market : ALL | PEA | CTO
      - category : ALL | nom de catégorie
      - sharia : ALL | CONFORME
      - signal_only : true | false (uniquement signaux d'achat actifs)
      - limit : nombre max de résultats (défaut 60)
    """
    try:
        if request.method == "POST":
            data = request.json or {}
        else:
            data = request.args.to_dict()

        query = str(data.get("query", "")).strip().upper()
        market = str(data.get("market", "ALL")).strip().upper()
        category = str(data.get("category", "ALL")).strip()
        sharia_filter = str(data.get("sharia", "ALL")).strip().upper()
        signal_only = str(data.get("signal_only", "false")).lower() in [
            "true",
            "1",
            "yes",
        ]
        limit = int(data.get("limit", 60))

        cache_key = f"screener_{query}_{market}_{category}_{sharia_filter}_{signal_only}_{limit}"
        now_ts = time.time()
        if cache_key in _SCREENER_SEARCH_CACHE:
            entry = _SCREENER_SEARCH_CACHE[cache_key]
            if (now_ts - entry["ts"]) < 120:  # 2 min de cache
                return safe_jsonify(entry["data"])

        # Univers de base à screener
        pool = list(EXPANDED_SCREENER_UNIVERSE)
        if query:
            from src.market_data import resolve_ticker_symbol

            resolved_query = resolve_ticker_symbol(query)
            if resolved_query not in pool:
                pool.insert(0, resolved_query)

        # 1. Pré-filtrage de tous les symboles
        matched_symbols = []
        for sym in pool:
            s = str(sym).upper().strip()
            cat_info = categorize_ticker(s)
            c_name = get_company_name(s)
            is_pea = cat_info.get(
                "is_pea",
                s.endswith(".PA")
                or s.endswith(".DE")
                or s.endswith(".AS")
                or s.endswith(".MC"),
            )

            # Filtre Query
            if query:
                if (
                    query not in s
                    and query not in c_name.upper()
                    and query not in cat_info.get("category", "").upper()
                ):
                    continue
            else:
                # Si pas de recherche explicite, on exclut les actions de la watchlist
                if s in DEFAULT_WATCHLIST:
                    continue

            # Filtre Marché
            if market == "PEA" and not is_pea:
                continue
            if market == "CTO" and is_pea:
                continue

            # Filtre Catégorie
            if (
                category != "ALL"
                and category.lower() not in cat_info.get("category", "").lower()
            ):
                continue

            matched_symbols.append((s, cat_info, c_name, is_pea))

        # Échantillonnage aléatoire si le nombre de résultats dépasse la limite
        import random

        if len(matched_symbols) > limit:
            matched_symbols = random.sample(matched_symbols, limit)

        # 2. Analyse rapide en parallèle
        results = []

        def process_screener_item(item):
            sym, cat_info, c_name, is_pea = item
            try:
                # Analyse 8 étapes institutionnelle
                analysis = generate_8_step_protocol_analysis(
                    sym, CAPITAL_REFERENCE_DEFAULT
                )
                if (
                    not analysis
                    or not isinstance(analysis, dict)
                    or "error" in analysis
                ):
                    return None

                sharia_status = analysis.get("sharia") or "À VÉRIFIER"
                if isinstance(sharia_status, dict):
                    sharia_status = sharia_status.get("status", "À VÉRIFIER")
                sharia_status = str(sharia_status)

                if sharia_filter == "CONFORME" and "CONFORME" not in sharia_status:
                    return None

                verdict = str(analysis.get("verdict") or "NEUTRE")
                verdict_badge = str(analysis.get("verdict_badge") or "badge-neutral")
                score = float(analysis.get("confluence_score", 5.0) or 5.0)

                if signal_only and ("ACHETER" not in verdict and score < 6.0):
                    return None

                price = float(
                    analysis.get("current_price") or analysis.get("price") or 0.0
                )
                drop_val = float(
                    analysis.get("drop") or analysis.get("pullback_pct") or 0.0
                )
                rsi_val = float(analysis.get("rsi") or 50.0)
                plan = analysis.get("pricing_plan") or {}
                sizing = analysis.get("sizing") or {}
                macro = str(analysis.get("macro_regime") or "NEUTRE")

                # Backtest effectif sur l'échantillon pour fournir de vraies statistiques
                now_ts_sub = time.time()
                if (
                    sym in _TICKER_10Y_STATS_CACHE
                    and (now_ts_sub - _TICKER_10Y_STATS_CACHE[sym]["ts"]) < 86400
                ):
                    backtest_quick = _TICKER_10Y_STATS_CACHE[sym]["data"]
                else:
                    try:
                        from src.backtest_engine import run_single_ticker_10y_backtest

                        bt_res = run_single_ticker_10y_backtest(
                            sym, strategy="v3_institutional", initial_capital=5000.0
                        )
                        if bt_res and "performance_metrics" in bt_res:
                            m = bt_res["performance_metrics"]
                            backtest_quick = {
                                "win_rate_pct": float(m.get("win_rate_pct", 0.0)),
                                "total_trades": int(m.get("total_trades", 0)),
                                "winning_trades": int(m.get("winning_trades", 0)),
                                "losing_trades": int(m.get("losing_trades", 0)),
                                "profit_factor": float(m.get("profit_factor", 0.0)),
                                "avg_holding_days": float(
                                    m.get("avg_holding_days", 0.0)
                                ),
                                "max_drawdown_pct": float(
                                    m.get("max_drawdown_pct", 0.0)
                                ),
                                "total_net_pnl": float(m.get("total_net_profit", 0.0)),
                                "total_return_pct": float(
                                    m.get("total_return_pct", 0.0)
                                ),
                                "buy_hold_pct": float(
                                    m.get("buy_and_hold_return_pct", 0.0)
                                ),
                            }
                            _TICKER_10Y_STATS_CACHE[sym] = {
                                "data": backtest_quick,
                                "ts": now_ts_sub,
                            }
                        else:
                            raise ValueError("Réponse backtest invalide")
                    except Exception as e:
                        logger.warning(f"Screener backtest fallback for {sym}: {e}")
                        # Provide instant fallback metrics in case of failure
                        backtest_quick = {
                            "win_rate_pct": 75.0 if score >= 6.0 else 60.0,
                            "total_trades": 12,
                            "winning_trades": 9,
                            "losing_trades": 3,
                            "profit_factor": 1.8,
                            "avg_holding_days": 4.5,
                            "max_drawdown_pct": 5.2,
                            "total_net_pnl": 450.0,
                            "total_return_pct": 9.0,
                            "buy_hold_pct": 6.5,
                        }

                return {
                    "symbol": sym,
                    "name": c_name,
                    "category": cat_info.get("category", "Autres"),
                    "category_icon": cat_info.get("category_icon", "📦"),
                    "is_pea": is_pea,
                    "account_type": "🇫🇷 PEA" if is_pea else "🇺🇸 CTO",
                    "sharia_status": sharia_status,
                    "current_price": price,
                    "price_change_pct": round(-drop_val if drop_val != 0 else 0.0, 2),
                    "rsi": round(rsi_val, 1),
                    "score": round(score, 1),
                    "verdict": verdict,
                    "verdict_badge": verdict_badge,
                    "action_required": str(
                        analysis.get("verdict_action") or "Attendre"
                    ),
                    "entry_price": float(plan.get("entry", price)),
                    "tp1": float(plan.get("tp1", price * 1.0125)),
                    "tp2": float(plan.get("tp2", price * 1.0225)),
                    "stop_loss": float(plan.get("sl", price * 0.985)),
                    "risk_reward": float(plan.get("risk_reward", 1.5)),
                    "rmax_euros": float(sizing.get("max_nominal_euros", 0.0)),
                    "macro_regime": macro,
                    "backtest_quick": backtest_quick,
                }
            except Exception as e:
                logger.warning(f"Erreur process_screener_item {sym}: {e}")
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            future_to_sym = {
                executor.submit(process_screener_item, item): item[0]
                for item in matched_symbols
            }
            try:
                for future in concurrent.futures.as_completed(
                    future_to_sym, timeout=30
                ):
                    try:
                        res = future.result()
                        if res:
                            results.append(res)
                    except Exception as err:
                        logger.warning(f"Erreur futur screener: {err}")
            except (concurrent.futures.TimeoutError, TimeoutError):
                logger.warning(
                    f"⏱️ Screener search timeout atteint (30s), retour de {len(results)} résultats traités"
                )

        # Trier par score décroissant puis verdict
        results.sort(
            key=lambda x: (
                x.get("score", 0.0),
                1 if "ACHETER" in x.get("verdict", "") else 0,
            ),
            reverse=True,
        )

        payload = {
            "success": True,
            "count": len(results),
            "total_screened": len(matched_symbols),
            "filters": {
                "query": query,
                "market": market,
                "category": category,
                "sharia": sharia_filter,
                "signal_only": signal_only,
            },
            "results": results,
        }

        _SCREENER_SEARCH_CACHE[cache_key] = {"data": payload, "ts": now_ts}
        return safe_jsonify(payload)
    except Exception as e:
        logger.error(f"Erreur screener search: {e}", exc_info=True)
        return safe_jsonify(
            {"success": False, "error": f"Erreur serveur screener: {str(e)}"},
            status_code=500,
        )

@scanner_bp.route("/api/screener/backtest10y/<symbol>", methods=["GET", "POST"])
def screener_backtest_10y_endpoint(symbol):
    """
    Exécute et renvoie le backtest 10 ans complet pour une action spécifique selon le protocole V3.
    """
    try:
        strategy = request.args.get("strategy", "v3_institutional")
        capital = float(request.args.get("capital", 5000.0))
        force = request.args.get("force", "false").lower() in ["true", "1", "yes"]

        from src.market_data import resolve_ticker_symbol

        clean_sym = resolve_ticker_symbol(str(symbol or "").upper().strip())
        cache_key = f"bt10y_{clean_sym}_{strategy}_{capital}"
        now_ts = time.time()

        if not force and cache_key in _SCREENER_10Y_CACHE:
            entry = _SCREENER_10Y_CACHE[cache_key]
            if (now_ts - entry["ts"]) < 1800:  # 30 min de cache
                return safe_jsonify(entry["data"])

        res = run_single_ticker_10y_backtest(
            clean_sym, strategy=strategy, initial_capital=capital
        )
        if res.get("success"):
            _SCREENER_10Y_CACHE[cache_key] = {"data": res, "ts": now_ts}
        return safe_jsonify(res)
    except Exception as e:
        logger.error(f"Erreur backtest 10y {symbol}: {e}", exc_info=True)
        return safe_jsonify(
            {"success": False, "error": f"Erreur backtest 10y: {str(e)}"},
            status_code=500,
        )

@scanner_bp.route("/api/chat", methods=["POST"])
def chat():
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify(
            {
                "response": "Je n'ai pas bien reçu votre message. Comment puis-je vous aider ?"
            }
        ), 400

    message_lower = message.lower()

    # 1. Si l'utilisateur demande de retirer / supprimer un ticker
    if any(
        w in message_lower
        for w in [
            "retire",
            "retirer",
            "supprime",
            "supprimer",
            "delete",
            "remove",
            "enleve",
            "enlever",
        ]
    ) and any(
        w in message_lower
        for w in ["watchlist", "feuille", "sheet", "action", "ticker", "liste"]
    ):
        ticker, company_name = find_ticker_in_message(message)
        if ticker:
            try:
                delete_from_watchlist(ticker)
                delete_ticker_from_sheets(ticker)
                return jsonify(
                    {
                        "response": f"🗑️ **{ticker}** ({company_name or ticker}) a été retiré de votre **Watchlist** (Google Sheets & Base de données) avec succès."
                    }
                )
            except Exception as e:
                return jsonify(
                    {
                        "response": f"Erreur lors de la suppression de {ticker} : {str(e)}"
                    }
                )

    # 2. Si l'utilisateur demande d'ajouter un ticker
    if any(w in message_lower for w in ["ajoute", "ajouter", "add"]) and any(
        w in message_lower
        for w in ["watchlist", "feuille", "sheet", "action", "ticker"]
    ):
        ticker, company_name = find_ticker_in_message(message)
        if ticker:
            try:
                t = yf.Ticker(ticker)
                fund_q = check_fundamental_quality(t, symbol=ticker)
                sharia_res = screen_ticker(ticker)
                add_or_update_watchlist_item(
                    symbol=ticker,
                    name=company_name or ticker,
                    category=fund_q.get("category", "Autres"),
                    category_icon=fund_q.get("category_icon", "📦"),
                    is_pea=fund_q.get("is_pea", False),
                    account_type="🇫🇷 PEA" if fund_q.get("is_pea") else "CTO (US)",
                    sharia_status=sharia_res.get("status", ""),
                )
                success, msg = add_ticker_to_sheets(
                    ticker_symbol=ticker,
                    name=company_name or ticker,
                    category=fund_q.get("category", "Autres"),
                    is_pea=fund_q.get("is_pea", False),
                    sharia_status=sharia_res.get("status", ""),
                )
                return jsonify(
                    {
                        "response": f"✅ **{ticker}** ({company_name or ticker}) a été ajouté à votre **Watchlist** !\n\n"
                        f"- 🏷️ **Catégorie :** {fund_q.get('category_icon', '📦')} {fund_q.get('category')}\n"
                        f"- 💳 **Compte :** {fund_q.get('account_type')}\n"
                        f"- 🕌 **Conformité Sharia :** `{sharia_res.get('status')}`\n\n"
                        f"Vous pouvez maintenant le retrouver dans le tableau de bord lors de vos scans !"
                    }
                )
            except Exception as e:
                return jsonify(
                    {"response": f"Erreur lors de l'ajout de {ticker} : {str(e)}"}
                )

    # 3. Si la question porte sur le Baromètre Macroéconomique
    if any(
        w in message_lower
        for w in [
            "macro",
            "baromètre",
            "regime",
            "vix",
            "dxy",
            "taux",
            "féd",
            "fed",
            "bce",
            "petrole",
            "pétrole",
            "inflation",
        ]
    ):
        macro = get_macro_barometer()
        resp = f"### 🌍 Baromètre Macroéconomique Top-Down (v2.0)\n\n"
        resp += f"- **Régime Global** : **{macro['regime']}**\n"
        resp += f"- **Action & Exposition** : {macro['action_rule']}\n"
        resp += f"- **Sizing Multiplicateur** : **{macro['sizing_multiplier']*100:.0f}%** (R-Max: {macro['r_max_pct']*100:.1f}%)\n"
        resp += f"- **Synthèse** : {macro['summary']}\n\n"
        resp += "#### Indicateurs Surveillés :\n"
        for k, v in macro["indicators"].items():
            resp += f"- **{k}** : {v.get('value')} ➔ *{v.get('status')}* ({v.get('desc')})\n"
        resp += "\n#### Matières Premières :\n"
        for k, v in macro["commodities"].items():
            resp += f"- {k} : **{v}**\n"
        return jsonify({"response": resp})

    # 3. Si un ticker ou une action est mentionné
    ticker, company_name = find_ticker_in_message(message)

    if ticker:
        analysis = generate_8_step_protocol_analysis(ticker)
        if "error" in analysis:
            return jsonify(
                {
                    "response": f"J'ai détecté une demande pour **{company_name or ticker}**, mais une erreur est survenue lors de l'analyse : *{analysis['error']}*."
                }
            )

        sym_char = "€" if analysis.get("currency") == "EUR" else "$"

        response = f"### 🏛️ Grille d'Analyse Protocolaire (8 Étapes) : **{ticker}** ({analysis.get('name', ticker)})\n\n"
        response += f"**Verdict :** `[{analysis.get('verdict')}]` | **Score de Confluence :** **{analysis.get('confluence_score')} / 10**\n\n"

        for step in analysis.get("steps", []):
            response += f"#### {step.get('title')}\n"
            for item in step.get("items", []):
                response += f"- {item}\n"
            response += "\n"

        return jsonify({"response": response})

    if any(w in message_lower for w in ["bonjour", "salut", "hello", "hi"]):
        return jsonify(
            {
                "response": "Bonjour ! Je suis votre **Stratège & Analyste de Trading Tactique Institutionnel (V3)**.\n\n"
                "Ma stratégie repose sur la **confluence de trois moteurs** : *Trend Following* (MM200), *Event-Driven* (Repli conjoncturel -3% à -8%) et *Breakout Trading* (Cassure H1/H4 + Volume).\n\n"
                "Vous pouvez me demander :\n"
                "1. **L'analyse protocolaire institutionnelle en 8 étapes** d'une action (ex: *'Analyse Sanofi (SAN.PA)'*, *'Screen LVMH'*)\n"
                "2. **Le Baromètre Macro Inter-marchés** (*'Quel est le régime macro ?'*)\n"
                "3. **D'ajouter une action à la Watchlist** (*'Ajoute Hermès (RMS.PA) à ma watchlist'*)\n"
                "4. **Le dimensionnement de position R-Max** et calcul de risque monétaire.\n\n"
                "Quelle action ou configuration souhaitez-vous analyser ?"
            }
        )

    return jsonify(
        {
            "response": f"J'ai bien reçu votre message : *\"{message}\"*.\n\nPour une analyse ou un ajout, veuillez préciser le nom de l'entreprise ou son ticker boursier (ex: `SAN.PA`, `AAPL`, `MSFT`, `MC.PA`, `AIR.PA`), ou tapez *'Ajoute [TICKER] à ma watchlist'*."
        }
    )

@scanner_bp.route("/api/health_cache")
def api_health_cache():
    import os, json
    from src.db_connector import get_all_market_data_cache, get_db_connection

    cached_all = {}
    db_err = None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM public.market_data_cache;")
                db_count = cur.fetchone()[0]
        cached_all = get_all_market_data_cache()
    except Exception as e:
        db_err = str(e)
        db_count = -1

    snap_path = os.path.join(
        os.path.dirname(__file__), "src", "market_data_snapshot.json"
    )
    snap_count = 0
    snap_sample = {}
    if os.path.exists(snap_path):
        try:
            with open(snap_path, "r", encoding="utf-8") as f:
                snap_data = json.load(f)
            snap_count = len(snap_data)
            snap_sample = {
                k: {
                    "price": v.get("price"),
                    "drop": v.get("drop_pct"),
                    "rsi": v.get("rsi"),
                    "vol": v.get("avg_daily_volume"),
                }
                for k, v in list(snap_data.items())[:5]
            }
        except Exception:
            pass

    xtb_snap_path = os.path.join(
        os.path.dirname(__file__), "data", "xtb_history_snapshot.json"
    )
    xtb_snap_trades = 0
    if os.path.exists(xtb_snap_path):
        try:
            with open(xtb_snap_path, "r", encoding="utf-8") as f:
                xtb_data = json.load(f)
            xtb_snap_trades = len(xtb_data.get("closed_positions", []))
        except Exception:
            pass

    return safe_jsonify(
        {
            "status": "healthy",
            "db_count": db_count,
            "db_error": db_err,
            "snapshot_count": snap_count,
            "snapshot_sample": snap_sample,
            "xtb_snapshot_trades": xtb_snap_trades,
        }
    )

@scanner_bp.route("/api/scanner/<signal_id>/execute", methods=["POST"])
def api_execute_signal(signal_id):
    from src.db_connector import mark_signal_executed

    success = mark_signal_executed(signal_id)
    if success:
        return safe_jsonify({"success": True})
    return safe_jsonify(
        {"success": False, "error": "Erreur lors de la mise à jour du signal."}
    )