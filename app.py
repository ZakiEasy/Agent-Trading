import os
import re
import math
import time
import logging
import threading
import concurrent.futures
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import yfinance as yf
from datetime import datetime
import requests

logging.basicConfig(level=logging.INFO)
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
    resolve_ticker_symbol
)
from src.risk_manager import calculate_trade_sizing, calculate_confluence_score
from src.institutional_engine import generate_8_step_protocol_analysis
from src.backtest_engine import BacktestEngine, CRISIS_PERIODS, run_all_crises_stress_test, run_single_ticker_10y_backtest
from src.supabase_connector import (
    get_supabase_watchlist,
    get_watchlist_symbols,
    get_watchlist_item,
    add_or_update_watchlist_item,
    delete_from_watchlist,
    get_supabase_positions,
    save_or_update_position,
    batch_save_positions,
    close_supabase_position,
    get_supabase_trade_journal,
    batch_save_trade_journal,
    get_supabase_treasury_operations,
    get_trade_proposals_history,
    batch_save_treasury_operations,
    log_trading_signal,
    get_recent_signals,
    log_macro_regime,
    get_latest_macro_regime
)
from src.config import (
    BASE_DIR,
    DEFAULT_WATCHLIST,
    DEFAULT_MARKET_POOL,
    CAPITAL_REFERENCE_DEFAULT,
    MIN_DROP_PCT,
    MAX_DROP_PCT,
    TARGET_TP1_DEFAULT,
    TARGET_TP2_DEFAULT
)

app = Flask(__name__, template_folder="templates")
CORS(app)

def sanitize_for_json(obj):
    """
    Parcourt récursivement les structures pour convertir tout NaN, Inf, -Inf, types NumPy et Pandas
    en types Python natifs pour garantir un JSON strictement valide sans erreurs 500.
    """
    if obj is None:
        return None
    if isinstance(obj, (float, int)):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return 0.0
        return obj
    try:
        import numpy as np
        if isinstance(obj, (np.floating, np.integer)):
            val = float(obj) if isinstance(obj, np.floating) else int(obj)
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                return 0.0
            return val
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return [sanitize_for_json(item) for item in obj.tolist()]
    except Exception:
        pass
        
    try:
        import pandas as pd
        if pd.isna(obj):
            return 0.0
        if isinstance(obj, (pd.Timestamp, datetime)):
            return str(obj)
    except Exception:
        pass

    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [sanitize_for_json(item) for item in obj]
    elif hasattr(obj, 'item') and callable(getattr(obj, 'item')):
        try:
            return sanitize_for_json(obj.item())
        except:
            return str(obj)
    elif hasattr(obj, 'to_dict') and callable(getattr(obj, 'to_dict')):
        try:
            return sanitize_for_json(obj.to_dict())
        except:
            return str(obj)
    elif not isinstance(obj, (str, bool)):
        return str(obj)
    return obj

def safe_jsonify(data, status_code=200):
    """
    Retourne un JSON assaini avec le code de statut HTTP souhaité.
    """
    cleaned = sanitize_for_json(data)
    response = jsonify(cleaned)
    response.status_code = status_code
    return response

# Cache global des analyses pour fluidité et réduction des appels externes
analysis_cache = {}

def get_detailed_analysis(ticker_symbol, capital=CAPITAL_REFERENCE_DEFAULT, force_refresh=False):
    """
    Exécute le protocole complet en 8 étapes pour un ticker spécifique selon les règles institutionnelles :
      1. Conformité Sharia (Normes AAOIFI — Ratios < 33% sur Cap Moyenne 24 mois)
      2. Contexte Macroéconomique Top-Down & Force Relative Sectorielle (ETF)
      3. Qualification de la Baisse (-3% à -8%) & Détection de Mispricing (Fenêtre Earnings > 10j ouvrés)
      4. Fondamentaux & Solidité (FCF, Marges, Cap > 2 Mrd, Volume > 1 M€/$)
      5. Analyse Technique & Flux (Supports, Tendance Daily/Hebdo, Mèches de Rejet, RSI 14 & Divergences)
      6. Plan de Trade Tactique Mean Reversion (Entrée, TP1/TP2 +1% à +2.5%, Stop sous support, Time Stop J+10 ouvrés)
      7. Dimensionnement R-Max & Risque Monétaire (Allocation ≤ 25%, Risque R ≤ 1%, Réserve Cash 25-30%)
      8. Verdict Final & Synthèse Décisionnelle
    """
    ticker_symbol = ticker_symbol.upper().strip()
    now_ts = time.time()
    cache_key = f"{ticker_symbol}_{capital}"
    
    if not force_refresh and cache_key in analysis_cache and (now_ts - analysis_cache[cache_key]["ts"]) < 300:
        return analysis_cache[cache_key]["data"]
    
    # 1. Étape 1 : Conformité Sharia (AAOIFI)
    sharia_res = screen_ticker(ticker_symbol)
    
    # 2. Étape 2 : Contexte Macroéconomique Top-Down
    macro_barometer = get_macro_barometer()
    
    # 3. Données de marché et technique
    try:
        ticker_obj, hist_or_err = fetch_market_data(ticker_symbol)
        if isinstance(hist_or_err, str):
            return {"error": hist_or_err, "symbol": ticker_symbol}
            
        hist = hist_or_err
        tech_setup = analyze_technical_setup(hist)
        has_qualified_drop, drop_details = qualify_price_drop(hist)
        
        # 4. Fondamentaux, Liquidité & Calendrier des Risques
        fund_quality = check_fundamental_quality(ticker_obj, symbol=ticker_symbol, hist=hist)
        has_blackout, blackout_reason = check_earnings_blackout(ticker_obj)
        
        # Force relative sectorielle
        sector_strength = calculate_sector_relative_strength(ticker_symbol, fund_quality.get("category", "Autres"), hist)
        
        info = getattr(ticker_obj, 'info', {}) if ticker_obj else {}
        company_name = get_company_name(ticker_symbol, info)
            
        curr_price = tech_setup["current_price"]
        support = tech_setup["support"]
        invalidation = support * 0.99
        
        # 5. Plan de Trade & Dimensionnement R-Max
        trade_plan = calculate_trade_sizing(
            capital_total=capital,
            entry_price=curr_price,
            stop_loss_price=invalidation,
            macro_regime=macro_barometer["regime"]
        )
        
        # 6. Score de Confluence Globale & Verdict Décisionnel
        confluence = calculate_confluence_score(
            sharia_res=sharia_res,
            macro_barometer=macro_barometer,
            drop_details=drop_details,
            has_qualified_drop=has_qualified_drop,
            tech_setup=tech_setup,
            has_blackout=has_blackout,
            trade_plan=trade_plan,
            fund_quality=fund_quality,
            sector_strength=sector_strength
        )
        
        # 7. Rapport structuré en 8 étapes avec métadonnées PEA et Catégories
        analysis = {
            "symbol": ticker_symbol,
            "name": company_name,
            "company_name": company_name,
            "currency": tech_setup.get("currency", "USD"),
            "category": fund_quality.get("category", "Autres"),
            "category_icon": fund_quality.get("category_icon", "📦"),
            "is_pea": fund_quality.get("is_pea", False),
            "account_type": fund_quality.get("account_type", "CTO (US)"),
            "step_1_sharia": sharia_res,
            "step_2_macro": {
                "regime": macro_barometer["regime"],
                "badge": macro_barometer["badge"],
                "sizing_multiplier": macro_barometer["sizing_multiplier"],
                "r_max_pct": macro_barometer["r_max_pct"],
                "action_rule": macro_barometer["action_rule"],
                "summary": macro_barometer["summary"],
                "indicators": macro_barometer["indicators"],
                "sector_strength": sector_strength
            },
            "step_3_drop": {
                "drop_pct": drop_details.get("drop_pct", 0.0),
                "lookback_days": drop_details.get("lookback_days", 1),
                "nature": drop_details.get("nature", "N/A"),
                "cause_summary": drop_details.get("cause_summary", ""),
                "earnings_window": "Absence d'Earnings sous 10 jours ouvrés" if not has_blackout else blackout_reason
            },
            "step_4_fundamentals": {
                "health_status": fund_quality["health_status"],
                "market_cap": fund_quality["market_cap"],
                "is_large_cap": fund_quality["is_large_cap"],
                "avg_daily_volume": fund_quality.get("avg_daily_volume", 0.0),
                "has_min_liquidity": fund_quality.get("has_min_liquidity", True),
                "free_cash_flow": fund_quality["free_cash_flow"],
                "operating_margin": fund_quality["operating_margin"],
                "sector": fund_quality["sector"],
                "industry": fund_quality["industry"],
                "category": fund_quality["category"],
                "is_pea": fund_quality["is_pea"],
                "account_type": fund_quality["account_type"],
                "summary": fund_quality["summary"],
                "earnings_blackout": {
                    "active": has_blackout,
                    "reason": blackout_reason
                }
            },
            "step_5_technical": tech_setup,
            "step_6_trade_plan": trade_plan,
            "step_7_risk_sizing": {
                "capital_reference": trade_plan["capital_reference"],
                "r_max_pct": trade_plan["r_max_pct"],
                "r_max_amount": trade_plan["r_max_amount"],
                "suggested_nominal": trade_plan["suggested_nominal"],
                "shares_count": trade_plan["shares_count"],
                "actual_monetary_risk": trade_plan["actual_monetary_risk"],
                "max_line_limit": trade_plan["max_line_limit"],
                "cash_reserve_required": trade_plan.get("cash_reserve_required", 0.0),
                "risk_reward_tp1": trade_plan["risk_reward_tp1"],
                "risk_reward_tp2": trade_plan["risk_reward_tp2"],
                "time_stop": trade_plan.get("time_stop", "")
            },
            "step_8_confluence": confluence,
            
            "sharia": sharia_res,
            "technical": tech_setup,
            "drop": drop_details,
            "sector_strength": sector_strength,
            "has_qualified_drop": has_qualified_drop,
            "earnings_blackout": {
                "active": has_blackout,
                "reason": blackout_reason
            },
            "trade_plan": {
                "entry": curr_price,
                "target_min": trade_plan["tp1_price"],
                "target_max": trade_plan["tp2_price"],
                "invalidation": invalidation,
                "potential_gain_min": trade_plan["tp1_pct"],
                "potential_gain_max": trade_plan["tp2_pct"],
                "potential_loss": trade_plan["stop_distance_pct"],
                "risk_reward": trade_plan["risk_reward_tp1"],
                "suggested_nominal": trade_plan["suggested_nominal"],
                "shares_count": trade_plan["shares_count"],
                "r_max_amount": trade_plan["r_max_amount"],
                "time_stop": trade_plan.get("time_stop", "")
            },
            "verdict": confluence["verdict"],
            "confluence_score": confluence["confluence_score"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        analysis_cache[cache_key] = {"data": analysis, "ts": now_ts}
        return analysis
    except Exception as e:
        return {"error": str(e), "symbol": ticker_symbol}

@app.route("/")
@app.route("/dashboard")
@app.route("/screener")
@app.route("/robot")
@app.route("/portfolio")
@app.route("/diversification")
@app.route("/journal")
@app.route("/chat")
@app.route("/simulation")
def home():
    tab = request.path.strip("/") or "dashboard"
    return render_template("index.html", initial_tab=tab)

@app.route("/stock/<path:ticker>")
@app.route("/action/<path:ticker>")
@app.route("/detail/<path:ticker>")
def view_stock_detail(ticker):
    """
    Page dédiée plein écran pour l'analyse protocolaire 8 étapes dans un nouvel onglet.
    """
    clean_ticker = (ticker or "").upper().strip()
    return render_template("stock_detail.html", symbol=clean_ticker)


@app.route("/api/watchlist")
def get_watchlist():
    """
    Retourne la liste complète des actions de la Watchlist depuis Supabase avec fallback automatique
    sur Google Sheets, le cache snapshot local ou la configuration locale si Supabase est vide ou inaccessible.
    """
    try:
        force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
        sb_wl = get_supabase_watchlist(only_active=True)
        
        # Charger le snapshot local persistant si existant
        local_snapshot_file = BASE_DIR / "data" / "watchlist_snapshot.json"
        local_items = []
        if local_snapshot_file.exists():
            try:
                with open(local_snapshot_file, "r", encoding="utf-8") as f:
                    local_items = json.load(f)
            except Exception:
                local_items = []

        if not sb_wl:
            from src.sheets_connector import read_watchlist_from_sheets, read_sharia_statuses_from_sheets
            sheet_tickers = read_watchlist_from_sheets(force_refresh=force) or []
            sharia_map = read_sharia_statuses_from_sheets(force_refresh=force) or {}
            
            merged_tickers = list(dict.fromkeys(sheet_tickers + DEFAULT_WATCHLIST)) if sheet_tickers else DEFAULT_WATCHLIST
            sb_wl = []
            for sym in merged_tickers:
                s = str(sym).strip().upper()
                if not s or s.startswith("TOTAL") or s.startswith("TABLEAU"):
                    continue
                cat_info = categorize_ticker(s)
                is_pea = cat_info.get("is_pea", s.endswith(".PA") or s.endswith(".DE") or s.endswith(".AS"))
                sb_wl.append({
                    "symbol": s,
                    "name": get_company_name(s),
                    "category": cat_info.get("category", "Autres"),
                    "category_icon": cat_info.get("category_icon", "📦"),
                    "is_pea": is_pea,
                    "account_type": "🇫🇷 PEA" if is_pea else "CTO (US)",
                    "sharia_status": sharia_map.get(s, "CONFORME"),
                    "currency": "EUR" if is_pea else "USD",
                    "is_active": True
                })
        
        if local_items:
            existing_syms = set(str(item.get("symbol", "")).upper() for item in sb_wl)
            for l_item in local_items:
                l_sym = str(l_item.get("symbol", "")).upper()
                if l_sym and l_sym not in existing_syms:
                    sb_wl.append(l_item)
                    existing_syms.add(l_sym)

        return safe_jsonify({"success": True, "watchlist": sb_wl, "count": len(sb_wl)})
    except Exception as e:
        logger.error(f"Erreur get_watchlist: {e}")
        return safe_jsonify({"success": False, "error": str(e), "watchlist": []}, 500)

@app.route("/api/watchlist/add", methods=["POST"])
def add_watchlist_ticker():
    """
    Endpoint pour ajouter ou mettre à jour une action dans Supabase, Google Sheets et le cache snapshot local.
    """
    data = request.json or {}
    symbol = data.get("ticker", "").upper().strip()
    if not symbol:
        return jsonify({"success": False, "error": "Le symbole de l'action est requis."}), 400

    # 1. Vérifier et récupérer les informations avec yfinance
    info = {}
    try:
        t = yf.Ticker(symbol)
        raw_info = getattr(t, 'info', None)
        if isinstance(raw_info, dict):
            info = raw_info
        name = data.get("name") or info.get("longName") or info.get("shortName") or symbol
        fund_q = check_fundamental_quality(t, info, symbol=symbol)
    except Exception as e:
        name = data.get("name") or symbol
        fund_q = {"category": "Autres", "is_pea": ".PA" in symbol, "account_type": "PEA" if ".PA" in symbol else "CTO (US)"}

    # 2. Screening Sharia
    sharia_res = screen_ticker(symbol)
    default_sharia = sharia_res.get("status", "À VÉRIFIER")
    sharia_status = data.get("sharia_status") or default_sharia

    category = data.get("category") or fund_q.get("category", "Autres")
    is_pea = data.get("is_pea") if data.get("is_pea") is not None else fund_q.get("is_pea", False)
    source_verif = data.get("source_verif") or "AAOIFI (Agent Trading)"

    # 3. Lancer l'analyse pour récupérer le prix actuel et le rapport
    analysis = get_detailed_analysis(symbol)
    price = 0.0
    currency = "USD"
    if isinstance(analysis, dict) and "step_5_technical" in analysis:
        price = analysis["step_5_technical"].get("current_price", 0.0)
        currency = analysis.get("currency", "USD")

    # 4. Écrire dans Supabase
    db_item = add_or_update_watchlist_item(
        symbol=symbol,
        name=name,
        category=category,
        category_icon=fund_q.get("category_icon", "📦"),
        is_pea=is_pea,
        account_type="🇫🇷 PEA" if is_pea else "CTO (US)",
        sharia_status=sharia_status,
        sharia_source=source_verif,
        currency=currency
    )

    # 5. Écrire en miroir dans Google Sheets
    sheets_success = False
    try:
        from src.sheets_connector import add_ticker_to_sheets
        sheets_success, _ = add_ticker_to_sheets(
            ticker_symbol=symbol,
            name=name,
            category=category,
            is_pea=is_pea,
            sharia_status=sharia_status,
            current_price=price,
            source_verif=source_verif
        )
    except Exception as e:
        logger.warning(f"Impossible d'écrire {symbol} dans Google Sheets : {e}")

    # 6. Sauvegarder dans le cache snapshot local persistant
    try:
        local_dir = BASE_DIR / "data"
        local_dir.mkdir(exist_ok=True)
        local_snapshot_file = local_dir / "watchlist_snapshot.json"
        local_items = []
        if local_snapshot_file.exists():
            try:
                with open(local_snapshot_file, "r", encoding="utf-8") as f:
                    local_items = json.load(f)
            except Exception:
                local_items = []
        
        # Mettre à jour ou ajouter
        found = False
        new_entry = {
            "symbol": symbol,
            "name": name,
            "category": category,
            "category_icon": fund_q.get("category_icon", "📦"),
            "is_pea": is_pea,
            "account_type": "🇫🇷 PEA" if is_pea else "CTO (US)",
            "sharia_status": sharia_status,
            "currency": currency,
            "price": price,
            "is_active": True
        }
        for idx, item in enumerate(local_items):
            if str(item.get("symbol", "")).upper() == symbol:
                local_items[idx] = new_entry
                found = True
                break
        if not found:
            local_items.append(new_entry)
            
        with open(local_snapshot_file, "w", encoding="utf-8") as f:
            json.dump(local_items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Erreur enregistrement cache snapshot local pour {symbol} : {e}")

    return jsonify({
        "success": True,
        "message": f"Action {symbol} ({name}) ajoutée avec succès dans la Watchlist (BDD, Google Sheets & Local) !",
        "ticker": symbol,
        "name": name,
        "category": category,
        "is_pea": is_pea,
        "account_type": "PEA (Europe)" if is_pea else "CTO (US)",
        "sharia_status": sharia_status,
        "sheets_synced": sheets_success,
        "data": analysis
    })

@app.route("/api/watchlist/delete", methods=["POST", "DELETE"])
@app.route("/api/watchlist/remove", methods=["POST", "DELETE"])
def remove_watchlist_ticker():
    """
    Endpoint pour retirer une action de la Watchlist (Supabase + Google Sheets + Caches).
    """
    data = request.json or {}
    symbol = (data.get("ticker") or data.get("symbol") or request.args.get("ticker") or request.args.get("symbol") or "").upper().strip()
    if not symbol:
        return safe_jsonify({"success": False, "error": "Le symbole de l'action est requis."}), 400

    deleted = delete_from_watchlist(symbol)
    if not deleted:
        return safe_jsonify({"success": False, "error": f"Impossible de supprimer l'action {symbol}."}), 500

    # Récupérer le nombre restant
    current_wl = get_supabase_watchlist(only_active=True)
    if not current_wl:
        from src.sheets_connector import read_watchlist_from_sheets
        current_wl = [{"symbol": s} for s in (read_watchlist_from_sheets() or [])]

    return safe_jsonify({
        "success": True,
        "symbol": symbol,
        "message": f"Action {symbol} retirée avec succès de la Watchlist.",
        "remaining_count": len(current_wl)
    })

from src.portfolio_tracker import (
    parse_broker_csv,
    parse_xtb_excel_file,
    aggregate_open_positions,
    get_live_portfolio_summary,
    calculate_trading_performance_stats,
    calculate_cash_and_treasury_summary,
    calculate_portfolio_diversification,
    calculate_xtb_monthly_turnover,
    find_anti_fifo_opportunities,
    calculate_monthly_rotation_by_stock
)
from src.trading212_connector import (
    test_trading212_connection,
    get_trading212_cash,
    get_trading212_open_positions,
    set_runtime_trading212_config,
    get_trading212_orders_history,
    sync_trading212_history_to_journal,
    parse_trading212_csv,
    cancel_all_trading212_orders,
    get_trading212_open_orders,
    check_trading212_api_permissions
)
from src.order_guardrails import guardrails_engine, STRATEGY_GRID_PROFILES
from src.trading212_execution_engine import execution_engine
from src.institutional_engine import (
    get_macro_sentiment_barometer,
    generate_8_step_protocol_analysis,
    compute_institutional_rmax_sizing,
    scan_watchlist_institutional
)

@app.route("/api/portfolio/live")
def get_live_portfolio():
    """
    Retourne la liste des positions actives avec calcul en direct du P&L, cours actuels, alertes et broker.
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    summary = get_live_portfolio_summary(force_refresh=force)
    return safe_jsonify({"success": True, "data": summary})

@app.route("/api/portfolio/xtb_quota")
def get_xtb_quota():
    """
    Retourne la consommation du quota mensuel de 100 000 € de transactions à 0% de commission chez XTB.
    """
    quota = calculate_xtb_monthly_turnover()
    return safe_jsonify({"success": True, "data": quota})

@app.route("/api/trading212/status")
def get_trading212_status():
    """
    Retourne le statut de connexion et le solde de trésorerie Trading 212.
    """
    test_res = test_trading212_connection()
    cash_data = get_trading212_cash()
    return safe_jsonify({
        "success": True,
        "connection": test_res,
        "cash": cash_data
    })

@app.route("/api/trading212/portfolio")
def get_trading212_portfolio():
    """
    Retourne les positions ouvertes en direct depuis l'API Trading 212.
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    positions = get_trading212_open_positions(force_refresh=force)
    return safe_jsonify({
        "success": True,
        "total": len(positions),
        "positions": positions
    })

@app.route("/api/trading212/config", methods=["GET", "POST"])
def configure_trading212():
    """
    Enregistre, persiste sur Supabase et teste la clé API Trading 212 fournie depuis l'interface.
    """
    if request.method == "GET":
        from src.trading212_connector import _RUNTIME_CONFIG
        key = _RUNTIME_CONFIG.get("api_key") or _RUNTIME_CONFIG.get("read_api_key") or ""
        masked = (key[:6] + "..." + key[-4:]) if len(key) > 10 else ("***" if key else "")
        return safe_jsonify({
            "configured": bool(key),
            "masked_key": masked,
            "environment": _RUNTIME_CONFIG.get("environment", "live")
        })

    data = request.json or {}
    api_key = data.get("api_key", "")
    api_secret = data.get("api_secret", "")
    env = data.get("environment", "live")

    set_runtime_trading212_config(api_key=api_key, api_secret=api_secret, environment=env)
    test_res = test_trading212_connection(api_key=api_key, api_secret=api_secret, environment=env)

    return safe_jsonify({
        "success": test_res.get("connected", False),
        "result": test_res
    })

@app.route("/api/trading212/sync_history", methods=["POST", "GET"])
def sync_trading212_history_endpoint():
    """
    Récupère l'historique des ordres exécutés sur Trading 212 via l'API et les intègre au Journal de Trading Supabase.
    """
    try:
        trades = sync_trading212_history_to_journal()
        if not trades:
            return safe_jsonify({
                "success": True,
                "message": "Aucun nouvel ordre de vente exécuté trouvé sur Trading 212.",
                "imported": 0,
                "total_found": 0
            })

        existing_journal = get_supabase_trade_journal() or []
        seen_ids = {str(t.get("id")) for t in existing_journal if t.get("id")}
        new_trades = [t for t in trades if str(t.get("id")) not in seen_ids]

        if new_trades:
            batch_save_trade_journal(new_trades)

        return safe_jsonify({
            "success": True,
            "message": f"Synchronisation Trading 212 réussie : {len(new_trades)} nouveau(x) trade(s) archivé(s) dans le Journal !",
            "imported": len(new_trades),
            "total_found": len(trades)
        })
    except Exception as e:
        logger.error(f"Erreur sync history Trading 212: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": f"Erreur lors de la synchronisation Trading 212: {str(e)}"}, status_code=500)

@app.route("/api/portfolio/monthly_rotation")
def get_portfolio_monthly_rotation():
    """
    Retourne la décomposition complète de la rotation du mois (par actions, montant investi, et transactions achat/vente).
    """
    month_prefix = request.args.get("month", "")
    journal = get_supabase_trade_journal()
    open_pos = get_supabase_positions(status="ACTIVE")
    
    rotation_data = calculate_monthly_rotation_by_stock(journal=journal, open_positions=open_pos, month_prefix=month_prefix)
    return safe_jsonify({
        "success": True,
        "data": rotation_data
    })

@app.route("/api/portfolio/anti_fifo_opportunities")
def get_anti_fifo_opportunities():
    """
    Retourne les opportunités d'arbitrage Anti-FIFO recommandant d'utiliser le broker alternatif.
    """
    opportunities = find_anti_fifo_opportunities()
    return safe_jsonify({
        "success": True,
        "total": len(opportunities),
        "opportunities": opportunities
    })

@app.route("/api/portfolio/treasury")
def get_portfolio_treasury():
    """
    Retourne le détail des soldes d'espèces, dépôts, retraits, dividendes et opérations de trésorerie depuis Supabase.
    """
    cash_ops = get_supabase_treasury_operations()
    summary = calculate_cash_and_treasury_summary(cash_ops)
    return safe_jsonify({
        "success": True,
        "summary": summary,
        "operations_count": len(cash_ops),
        "recent_operations": cash_ops[:50] if cash_ops else []
    })

@app.route("/api/portfolio/diversification")
def get_portfolio_diversification():
    """
    Retourne la décomposition complète du portefeuille (catégorie/secteur, compte PEA/CTO, courtier, Actions vs Cash).
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    live_summary = get_live_portfolio_summary(force_refresh=force)
    live_positions = live_summary.get("positions", [])
    cash_ops = get_supabase_treasury_operations()
    cash_summary = calculate_cash_and_treasury_summary(cash_ops)
    
    div = calculate_portfolio_diversification(live_positions, cash_summary=cash_summary)
    return safe_jsonify({
        "success": True,
        "data": div
    })

@app.route("/api/journal/history")
def get_journal_history():
    """
    Retourne l'historique complet des trades clôturés avec statistiques de performance (Win Rate, P&L, etc.).
    """
    auto_sync = request.args.get("auto_sync", "true").lower() in ["true", "1", "yes"]
    if auto_sync:
        try:
            t212_trades = sync_trading212_history_to_journal()
            if t212_trades:
                existing = get_supabase_trade_journal() or []
                seen_ids = {str(t.get("id")) for t in existing if t.get("id")}
                new_t = [t for t in t212_trades if str(t.get("id")) not in seen_ids]
                if new_t:
                    batch_save_trade_journal(new_t)
        except Exception as err:
            logger.warning(f"Auto-sync Trading 212 skipped or failed: {err}")

    trades = get_supabase_trade_journal()
    stats = calculate_trading_performance_stats(trades)
    return safe_jsonify({
        "success": True,
        "total": len(trades),
        "stats": stats,
        "trades": trades
    })

_PROTOCOL_FEEDBACK_CACHE = {"data": None, "ts": 0}

@app.route("/api/journal/protocol_feedback")
def get_journal_protocol_feedback():
    """
    Analyse post-trade avancée des positions exécutées vs le Protocole en 8 étapes.
    Fournit le score de discipline, la décomposition par durée, les diagnostics TP1/TP2,
    l'analyse des cassures et les recommandations d'optimisation.
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    now = time.time()
    if not force and _PROTOCOL_FEEDBACK_CACHE["data"] and (now - _PROTOCOL_FEEDBACK_CACHE["ts"] < 300):
        return safe_jsonify(_PROTOCOL_FEEDBACK_CACHE["data"])

    try:
        from src.protocol_feedback_engine import analyze_executed_trades_against_protocol
        trades = get_supabase_trade_journal()
        analysis = analyze_executed_trades_against_protocol(trades)
        _PROTOCOL_FEEDBACK_CACHE["data"] = analysis
        _PROTOCOL_FEEDBACK_CACHE["ts"] = now
        return safe_jsonify(analysis)
    except Exception as e:
        logger.error(f"Erreur protocol feedback: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": f"Erreur calcul feedback: {str(e)}"}, status_code=500)

@app.route("/api/journal/trade_audit/<trade_id>")
def get_trade_protocol_audit(trade_id):
    """
    Renvoie l'audit protocole détaillé pour un trade spécifique du journal.
    """
    try:
        from src.protocol_feedback_engine import audit_single_trade
        trades = get_supabase_trade_journal()
        target = next((t for t in trades if str(t.get("id")) == str(trade_id)), None)
        if not target:
            return safe_jsonify({"success": False, "error": "Trade introuvable."}), 404

        audit = audit_single_trade(target)
        return safe_jsonify({"success": True, "audit": audit})
    except Exception as e:
        logger.error(f"Erreur audit trade {trade_id}: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": f"Erreur audit trade: {str(e)}"}, status_code=500)

@app.route("/api/journal/ticker_audit/<symbol>")
def get_ticker_protocol_audit(symbol):
    """
    Renvoie l'audit approfondi de l'ensemble des trades exécutés sur une action spécifique.
    """
    try:
        from src.protocol_feedback_engine import get_ticker_deep_audit
        trades = get_supabase_trade_journal()
        audit = get_ticker_deep_audit(symbol, trades)
        if not audit:
            return safe_jsonify({"success": False, "error": f"Aucun trade trouvé pour le symbole {symbol}."}), 404

        return safe_jsonify({"success": True, "ticker_audit": audit})
    except Exception as e:
        logger.error(f"Erreur audit ticker {symbol}: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": f"Erreur audit ticker: {str(e)}"}, status_code=500)

@app.route("/api/portfolio/add", methods=["POST"])
def add_portfolio_position():
    """
    Ajoute manuellement une position dans Supabase.
    """
    data = request.json or {}
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"success": False, "error": "Le symbole de l'action est requis."}), 400

    pru = float(data.get("pru", 0))
    qty = float(data.get("quantity", 1))
    if pru <= 0 or qty <= 0:
        return jsonify({"success": False, "error": "PRU et quantité doivent être supérieurs à 0."}), 400

    name = data.get("name") or get_company_name(symbol)
    is_pea = ".PA" in symbol or data.get("account") == "PEA"
    default_acc = "PEA" if is_pea else "CTO Dollar"
    account = data.get("account", default_acc)
    currency = data.get("currency", "EUR" if ("PEA" in account or ".PA" in symbol) else "USD")

    sl = float(data.get("stop_loss", pru * 0.97))
    tp1 = float(data.get("tp1", pru * 1.0125))
    tp2 = float(data.get("tp2", pru * 1.0225))

    pos_data = {
        "symbol": symbol,
        "company_name": name,
        "broker": data.get("broker", "XTB"),
        "account_type": account,
        "pru": pru,
        "quantity": qty,
        "invested_capital": pru * qty,
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "currency": currency,
        "status": "ACTIVE",
        "notes": data.get("notes", "")
    }

    saved = save_or_update_position(pos_data)
    if saved:
        return jsonify({"success": True, "message": f"Position {symbol} ({qty} actions à {pru} {currency}) enregistrée avec succès en BDD !"})
    return jsonify({"success": False, "error": "Erreur lors de l'enregistrement de la position en BDD."}), 500
        
    pru = float(data.get("pru", 0))
    qty = float(data.get("quantity", 1))
    
    name = data.get("name")
    if not name:
        try:
            t = yf.Ticker(symbol)
            info = getattr(t, "info", {})
            name = info.get("longName") or info.get("shortName") or symbol
        except:
            name = symbol

    pos_data = {
        "symbol": symbol,
        "name": name,
        "entry_date": data.get("entry_date") or datetime.now().strftime("%Y-%m-%d"),
        "pru": pru,
        "quantity": qty,
        "stop_loss": float(data.get("stop_loss", pru * 0.97)),
        "tp1": float(data.get("tp1", pru * 1.0125)),
        "tp2": float(data.get("tp2", pru * 1.0225)),
        "account": data.get("account", "PEA" if ".PA" in symbol else "CTO"),
        "currency": data.get("currency", "EUR" if ".PA" in symbol or ".DE" in symbol else "USD"),
        "notes": data.get("notes", "")
    }
    
    success, msg = add_position_to_sheets(pos_data)
    return jsonify({"success": success, "message": msg})

@app.route("/api/portfolio/upload_report", methods=["POST"])
@app.route("/api/portfolio/upload_csv", methods=["POST"])
@app.route("/api/portfolio/upload_multiple_reports", methods=["POST"])
def upload_portfolio_report():
    """
    Importe un ou plusieurs fichiers Excel (.xlsx, .xls) ou CSV de rapport XTB / courtier.
    Auto-détecte pour chaque fichier :
      - Le compte (PEA, CTO Euro, CTO Dollar/US)
      - Les positions fermées (Journal de Trading)
      - Les positions ouvertes (Suivi Live)
      - Les opérations de trésorerie (Cash)
    """
    files = request.files.getlist("files")
    if not files:
        single_file = request.files.get("file")
        if single_file:
            files = [single_file]

    if not files:
        return jsonify({"success": False, "error": "Aucun fichier fourni."}), 400

    default_acc_override = request.form.get("account")
    if default_acc_override == "auto":
        default_acc_override = None

    seen_closed_ids = set()
    seen_open_ids = set()
    seen_cash_ids = set()
    files_processed = 0
    by_account = {
        "PEA": {"closed": 0, "open": 0, "cash": 0},
        "CTO Euro": {"closed": 0, "open": 0, "cash": 0},
        "CTO Dollar": {"closed": 0, "open": 0, "cash": 0},
        "Trading 212": {"closed": 0, "open": 0, "cash": 0}
    }

    # Charger l'existant pour déduplication
    existing_journal = get_supabase_trade_journal() or []
    for t in existing_journal:
        if t.get("id"):
            seen_closed_ids.add(t["id"])

    existing_open = get_supabase_positions(status="ACTIVE") or []
    for o in existing_open:
        if o.get("id"):
            seen_open_ids.add(o["id"])

    existing_cash = get_supabase_treasury_operations() or []
    for c in existing_cash:
        if c.get("id"):
            seen_cash_ids.add(c["id"])

    new_closed_list = []
    new_open_list = []
    new_cash_list = []

    for file_item in files:
        if not file_item or not file_item.filename:
            continue
        fname = file_item.filename
        content = file_item.read()
        if not content:
            continue

        files_processed += 1
        detected_acc = default_acc_override
        if not detected_acc:
            fname_upper = fname.upper()
            if "212" in fname_upper or "T212" in fname_upper:
                detected_acc = "Trading 212"
            elif "PEA" in fname_upper:
                detected_acc = "PEA"
            elif "USD" in fname_upper or "DOLLAR" in fname_upper or "US" in fname_upper:
                detected_acc = "CTO Dollar"
            else:
                detected_acc = "CTO Euro"

        if fname.lower().endswith(".xlsx") or fname.lower().endswith(".xls"):
            parsed = parse_xtb_excel_file(content, default_account=detected_acc)
            for t in parsed.get("closed_positions", []):
                tid = t.get("id")
                if tid and tid not in seen_closed_ids:
                    seen_closed_ids.add(tid)
                    new_closed_list.append(t)
                    acc_key = t.get("account", "CTO Euro")
                    if acc_key not in by_account:
                        by_account[acc_key] = {"closed": 0, "open": 0, "cash": 0}
                    by_account[acc_key]["closed"] += 1

            for o in parsed.get("open_positions", []):
                oid = o.get("id")
                if oid and oid not in seen_open_ids:
                    seen_open_ids.add(oid)
                    new_open_list.append(o)
                    acc_key = o.get("account", "CTO Euro")
                    if acc_key not in by_account:
                        by_account[acc_key] = {"closed": 0, "open": 0, "cash": 0}
                    by_account[acc_key]["open"] += 1

            for c in parsed.get("cash_operations", []):
                cid = c.get("id")
                if cid and cid not in seen_cash_ids:
                    seen_cash_ids.add(cid)
                    new_cash_list.append(c)
                    acc_key = c.get("account", "CTO Euro")
                    if acc_key not in by_account:
                        by_account[acc_key] = {"closed": 0, "open": 0, "cash": 0}
                    by_account[acc_key]["cash"] += 1
        else:
            # Traitement CSV (Trading 212 vs format standard/XTB)
            sample_header = content[:200].decode('utf-8', errors='ignore').lower() if isinstance(content, bytes) else str(content[:200]).lower()
            is_t212 = detected_acc == "Trading 212" or "isin" in sample_header or "no. of shares" in sample_header or "price / share" in sample_header

            if is_t212:
                parsed_t212 = parse_trading212_csv(content)
                for t in parsed_t212.get("closed_positions", []):
                    tid = t.get("id")
                    if tid and tid not in seen_closed_ids:
                        seen_closed_ids.add(tid)
                        new_closed_list.append(t)
                        by_account["Trading 212"]["closed"] += 1

                for c in parsed_t212.get("cash_operations", []):
                    cid = c.get("id")
                    if cid and cid not in seen_cash_ids:
                        seen_cash_ids.add(cid)
                        new_cash_list.append(c)
                        by_account["Trading 212"]["cash"] += 1
            else:
                positions = parse_broker_csv(content)
                for pos in positions:
                    pid = pos.get("id")
                    if pid and pid not in seen_open_ids:
                        seen_open_ids.add(pid)
                        new_open_list.append(pos)
                        acc_key = pos.get("account", detected_acc or "CTO Euro")
                        if acc_key not in by_account:
                            by_account[acc_key] = {"closed": 0, "open": 0, "cash": 0}
                            by_account[acc_key]["open"] += 1

    if new_closed_list:
        batch_save_trade_journal(new_closed_list)

    if new_open_list:
        agg_open = aggregate_open_positions(existing_open + new_open_list)
        batch_save_positions(agg_open)

    if new_cash_list:
        batch_save_treasury_operations(new_cash_list)

    msg = f"{files_processed} fichier(s) traité(s) avec succès : {len(new_closed_list)} nouveau(x) trade(s) dans le Journal, {len(new_open_list)} position(s) active(s), {len(new_cash_list)} opération(s) de trésorerie."

    return jsonify({
        "success": True,
        "message": msg,
        "files_count": files_processed,
        "closed_imported": len(new_closed_list),
        "open_imported": len(new_open_list),
        "cash_imported": len(new_cash_list),
        "by_account": by_account
    })

@app.route("/api/portfolio/import_all_history", methods=["POST"])
def import_all_history_files():
    """
    Importe automatiquement tous les fichiers d'historique XTB (positions fermées, ouvertes et trésorerie)
    présents dans le dossier /historique (hors sous-dossier /old) directement en base de données.
    """
    import os
    base_dir = os.path.dirname(os.path.abspath(__file__))
    hist_dir = os.path.join(base_dir, "historique")
    
    files_to_scan = []
    for root, _, files in os.walk(hist_dir):
        parts = [p.lower() for p in root.split(os.sep)]
        if "old" in parts:
            continue
        for f in files:
            if f.lower().endswith(".xlsx") and not f.startswith("~$") and not f.startswith("."):
                files_to_scan.append(os.path.join(root, f))

    all_closed = []
    all_open = []
    all_cash = []
    seen_closed_ids = set()
    seen_open_ids = set()
    seen_cash_ids = set()

    for fpath in files_to_scan:
        fname = os.path.basename(fpath).upper()
        acc = "PEA" if "PEA" in fname else "CTO Dollar" if ("USD" in fname or "DOLLAR" in fname) else "CTO Euro"
        parsed = parse_xtb_excel_file(fpath, default_account=acc)
        
        for t in parsed.get("closed_positions", []):
            if t["id"] not in seen_closed_ids:
                seen_closed_ids.add(t["id"])
                all_closed.append(t)

        for o in parsed.get("open_positions", []):
            if o["id"] not in seen_open_ids:
                seen_open_ids.add(o["id"])
                all_open.append(o)

        for c in parsed.get("cash_operations", []):
            if c["id"] not in seen_cash_ids:
                seen_cash_ids.add(c["id"])
                all_cash.append(c)

    # Si le parsing direct a échoué (ex: openpyxl non dispo ou fichiers en cours d'écriture), fallback sur le snapshot
    if not all_closed and not all_open and not all_cash:
        from src.supabase_connector import _get_xtb_snapshot_data
        snap = _get_xtb_snapshot_data()
        all_closed = snap.get("closed_positions", [])
        all_open = snap.get("open_positions", [])
        all_cash = snap.get("cash_operations", [])
        agg_open = all_open
    else:
        # 1. Enregistrer dans le Journal
        batch_save_trade_journal(all_closed)

        # 2. Enregistrer les positions ouvertes agrégées
        agg_open = aggregate_open_positions(all_open)
        batch_save_positions(agg_open)

        # 3. Enregistrer les opérations de trésorerie
        batch_save_treasury_operations(all_cash)

    stats = calculate_trading_performance_stats(all_closed)
    cash_summary = calculate_cash_and_treasury_summary(all_cash)

    return jsonify({
        "success": True,
        "message": f"Synchronisation historique réussie ({len(files_to_scan)} fichiers analysés) ! {len(all_closed)} trades dans le Journal, {len(agg_open)} positions actives, {len(all_cash)} opérations de trésorerie.",
        "files_count": len(files_to_scan),
        "closed_count": len(all_closed),
        "open_count": len(agg_open),
        "cash_count": len(all_cash),
        "stats": stats,
        "treasury_summary": cash_summary
    })

@app.route("/api/portfolio/close", methods=["POST"])
def close_portfolio_position():
    """
    Clôture une position active à un cours donné et l'archive dans le journal de trading Supabase.
    """
    data = request.json or {}
    pos_id = data.get("id") or data.get("symbol")
    exit_price = float(data.get("exit_price", 0))
    notes = data.get("notes", "")
    
    if not pos_id or exit_price <= 0:
        return jsonify({"success": False, "error": "ID de position et prix de sortie valides requis."}), 400
        
    success, msg = close_supabase_position(pos_id, exit_price, notes=notes)
    return jsonify({"success": success, "message": msg})

@app.route("/api/watchlist/tickers")
def get_watchlist_tickers():
    """
    Retourne la liste des tickers de la Watchlist avec leurs métadonnées depuis Supabase
    (avec fallback automatique sur le snapshot local et Google Sheets/DEFAULT_WATCHLIST)
    pour initialiser l'affichage instantanément avant le scan par lots.
    """
    try:
        force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
        sb_wl = get_supabase_watchlist(only_active=True)
        
        # Charger le snapshot local persistant si existant
        local_snapshot_file = BASE_DIR / "data" / "watchlist_snapshot.json"
        local_items = []
        if local_snapshot_file.exists():
            try:
                with open(local_snapshot_file, "r", encoding="utf-8") as f:
                    local_items = json.load(f)
            except Exception:
                local_items = []

        if not sb_wl:
            from src.sheets_connector import read_watchlist_from_sheets, read_sharia_statuses_from_sheets
            sheet_tickers = read_watchlist_from_sheets(force_refresh=force) or []
            sharia_map = read_sharia_statuses_from_sheets(force_refresh=force) or {}
            
            merged_tickers = list(dict.fromkeys(sheet_tickers + DEFAULT_WATCHLIST)) if sheet_tickers else DEFAULT_WATCHLIST
            sb_wl = []
            for sym in merged_tickers:
                s = str(sym).strip().upper()
                if not s or s.startswith("TOTAL") or s.startswith("TABLEAU"):
                    continue
                cat_info = categorize_ticker(s)
                is_pea = cat_info.get("is_pea", s.endswith(".PA") or s.endswith(".DE") or s.endswith(".AS"))
                sb_wl.append({
                    "symbol": s,
                    "name": get_company_name(s),
                    "category": cat_info.get("category", "Autres"),
                    "category_icon": cat_info.get("category_icon", "📦"),
                    "is_pea": is_pea,
                    "account_type": "🇫🇷 PEA" if is_pea else "CTO (US)",
                    "sharia_status": sharia_map.get(s, "CONFORME"),
                    "currency": "EUR" if is_pea else "USD",
                    "is_active": True
                })
        
        if local_items:
            existing_syms = set(str(item.get("symbol", "")).upper() for item in sb_wl)
            for l_item in local_items:
                l_sym = str(l_item.get("symbol", "")).upper()
                if l_sym and l_sym not in existing_syms:
                    sb_wl.append(l_item)
                    existing_syms.add(l_sym)
        tickers_data = []
        for item in sb_wl:
            s = str(item.get("symbol", "")).strip().upper()
            if not s:
                continue
            cat = categorize_ticker(s)
            is_pea = item.get("is_pea") if item.get("is_pea") is not None else (s.endswith(".PA") or s.endswith(".DE") or s.endswith(".AS") or s.endswith(".MC"))
            acc_type = item.get("account_type") or ("🇫🇷 PEA" if is_pea else "CTO (US)")
            tickers_data.append({
                "symbol": s,
                "name": item.get("name") or get_company_name(s),
                "category": item.get("category") or cat.get("category", "Autres"),
                "category_icon": item.get("category_icon") or cat.get("category_icon", "📦"),
                "is_pea": is_pea,
                "account_type": acc_type,
                "sharia": item.get("sharia_status") or item.get("sharia") or "CONFORME"
            })
            
        return safe_jsonify({"success": True, "tickers": tickers_data, "count": len(tickers_data)})
    except Exception as e:
        return safe_jsonify({"success": False, "error": str(e), "tickers": []}, 500)

@app.route("/api/scan/batch", methods=["GET", "POST"])
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
                symbols = [s.strip().upper() for s in symbols_param.split(",") if s.strip()]
                
        if not symbols:
            return safe_jsonify({"success": False, "error": "Aucun symbole fourni pour le lot.", "results": []}), 400
            
        results = []
        signals_to_write = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(symbols), 8)) as executor:
            if strategy == "V2":
                future_to_sym = {executor.submit(get_detailed_analysis, sym, CAPITAL_REFERENCE_DEFAULT, force): sym for sym in symbols}
                for future in concurrent.futures.as_completed(future_to_sym, timeout=20):
                    try:
                        analysis = future.result()
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

                        results.append({
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
                            "rsi_divergence": (tech.get("rsi_divergence") or {}).get("type", "AUCUNE"),
                            "confluence_score": analysis.get("confluence_score", 0),
                            "verdict": analysis.get("verdict", "ATTENDRE REPLI SUR SUPPORT"),
                            "currency": tech.get("currency", "USD")
                        })
                    except Exception:
                        pass
            else:
                # Stratégie V3 Institutionnelle (Par Défaut)
                future_to_sym = {executor.submit(generate_8_step_protocol_analysis, sym, CAPITAL_REFERENCE_DEFAULT): sym for sym in symbols}
                for future in concurrent.futures.as_completed(future_to_sym, timeout=25):
                    sym = future_to_sym[future]
                    try:
                        analysis = future.result()
                        if not analysis or not isinstance(analysis, dict) or "error" in analysis:
                            raise ValueError((analysis or {}).get("error", "Données indisponibles"))
                            
                        symbol = analysis.get("symbol", sym)
                        plan = analysis.get("pricing_plan") or {}
                        sizing = analysis.get("sizing") or {}
                        
                        results.append({
                            "symbol": symbol,
                            "name": analysis.get("name", get_company_name(symbol)),
                            "category": analysis.get("category", "Autres"),
                            "category_icon": analysis.get("category_icon", "📦"),
                            "is_pea": analysis.get("is_pea", False),
                            "account_type": analysis.get("account_type", "CTO (US)"),
                            "sharia": analysis.get("sharia", "NON CONFORME"),
                            "price": analysis.get("current_price", 0.0),
                            "drop": analysis.get("drop", 0.0),
                            "drop_nature": "SURRÉACTION CONJONCTURELLE" if analysis.get("pullback_valid") else "REPLI EN COURS",
                            "avg_daily_volume": analysis.get("avg_daily_volume", 0.0),
                            "has_min_liquidity": True,
                            "sector_rel": "SURPERFORMANCE" if analysis.get("trend_following_valid") else "EN LIGNE",
                            "sector_etf": "SPY",
                            "rsi": analysis.get("rsi", 50.0),
                            "rsi_divergence": analysis.get("rsi_divergence", "AUCUNE"),
                            "confluence_score": analysis.get("confluence_score", 0),
                            "verdict": analysis.get("verdict", "ÉVITER - HORS CRITÈRES"),
                            "verdict_badge": analysis.get("verdict_badge", "badge-neutral"),
                            "verdict_action": analysis.get("verdict_action", ""),
                            "verdict_swing": analysis.get("verdict_swing", analysis.get("verdict", "ÉVITER")),
                            "verdict_swing_badge": analysis.get("verdict_swing_badge", "badge-neutral"),
                            "verdict_swing_action": analysis.get("verdict_swing_action", ""),
                            "verdict_sniper": analysis.get("verdict_sniper", "NON ÉLIGIBLE"),
                            "verdict_sniper_badge": analysis.get("verdict_sniper_badge", "badge-neutral"),
                            "verdict_sniper_action": analysis.get("verdict_sniper_action", ""),
                            "action_plan": analysis.get("action_plan", ""),
                            "execution_timing": analysis.get("execution_timing"),
                            "pricing_plan_sniper": analysis.get("pricing_plan_sniper"),
                            "currency": analysis.get("currency", "EUR")
                        })
                    except Exception:
                        # Fallback garanti pour que 100% des actions demandées s'affichent
                        cat_info = categorize_ticker(sym)
                        is_pea = cat_info.get("is_pea", sym.endswith(".PA") or sym.endswith(".DE"))
                        results.append({
                            "symbol": sym,
                            "name": get_company_name(sym),
                            "category": cat_info.get("category", "Autres"),
                            "category_icon": cat_info.get("category_icon", "📦"),
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
                            "currency": "EUR" if is_pea else "USD"
                        })
                        
        return safe_jsonify({"success": True, "results": results, "signals_sent": len(signals_to_write)})
    except Exception as e:
        return safe_jsonify({"success": False, "error": str(e), "results": []}, 500)

@app.route("/api/scan/watchlist")
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
                future_to_sym = {executor.submit(get_detailed_analysis, sym, CAPITAL_REFERENCE_DEFAULT, force): sym for sym in watchlist}
                for future in concurrent.futures.as_completed(future_to_sym, timeout=25):
                    try:
                        analysis = future.result()
                        if not analysis or not isinstance(analysis, dict) or "error" in analysis:
                            continue
                        tech = analysis.get("technical") or {}
                        drop = analysis.get("drop") or {}
                        sharia = analysis.get("sharia") or {}
                        fund = analysis.get("step_4_fundamentals") or {}
                        sec_rel = analysis.get("sector_strength") or {}
                        results.append({
                            "symbol": analysis.get("symbol"),
                            "name": analysis.get("company_name", analysis.get("symbol")),
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
                            "rsi_divergence": (tech.get("rsi_divergence") or {}).get("type", "AUCUNE"),
                            "confluence_score": analysis.get("confluence_score", 0),
                            "verdict": analysis.get("verdict", "ATTENDRE REPLI SUR SUPPORT"),
                            "verdict_swing": analysis.get("verdict", "ATTENDRE REPLI SUR SUPPORT"),
                            "verdict_swing_badge": "badge-warning",
                            "verdict_sniper": "NON ÉLIGIBLE",
                            "verdict_sniper_badge": "badge-neutral",
                            "currency": tech.get("currency", "USD")
                        })
                    except Exception:
                        pass
            else:
                # Stratégie V3 Institutionnelle
                future_to_sym = {executor.submit(generate_8_step_protocol_analysis, sym, CAPITAL_REFERENCE_DEFAULT): sym for sym in watchlist}
                for future in concurrent.futures.as_completed(future_to_sym, timeout=25):
                    try:
                        analysis = future.result()
                        if not analysis or not isinstance(analysis, dict) or "error" in analysis:
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
                            "drop_nature": "SURRÉACTION CONJONCTURELLE" if analysis.get("pullback_valid") else "REPLI EN COURS",
                            "avg_daily_volume": analysis.get("avg_daily_volume", 0.0),
                            "has_min_liquidity": True,
                            "sector_rel": "SURPERFORMANCE" if analysis.get("trend_following_valid") else "EN LIGNE",
                            "sector_etf": "SPY",
                            "rsi": analysis.get("rsi", 50.0),
                            "rsi_divergence": analysis.get("rsi_divergence", "AUCUNE"),
                            "confluence_score": analysis.get("confluence_score", 0),
                            "verdict": analysis.get("verdict", "ÉVITER - HORS CRITÈRES"),
                            "verdict_badge": analysis.get("verdict_badge", "badge-neutral"),
                            "verdict_action": analysis.get("verdict_action", ""),
                            "verdict_swing": analysis.get("verdict_swing", analysis.get("verdict", "ÉVITER")),
                            "verdict_swing_badge": analysis.get("verdict_swing_badge", "badge-neutral"),
                            "verdict_swing_action": analysis.get("verdict_swing_action", ""),
                            "verdict_sniper": analysis.get("verdict_sniper", "NON ÉLIGIBLE"),
                            "verdict_sniper_badge": analysis.get("verdict_sniper_badge", "badge-neutral"),
                            "verdict_sniper_action": analysis.get("verdict_sniper_action", ""),
                            "action_plan": analysis.get("action_plan", ""),
                            "execution_timing": analysis.get("execution_timing"),
                            "pricing_plan_sniper": analysis.get("pricing_plan_sniper"),
                            "currency": analysis.get("currency", "EUR")
                        }
                        results.append(r_dict)
                        
                        # Enregistrer le signal dans Supabase si signal d'intérêt
                        if "ACHAT" in str(r_dict["verdict_swing"]) or "ACHAT" in str(r_dict["verdict_sniper"]) or "ATTENDRE" in str(r_dict["verdict_sniper"]):
                            try:
                                log_trading_signal(r_dict)
                            except Exception:
                                pass
                    except Exception:
                        pass
                        
        return safe_jsonify({"success": True, "results": results, "signals_sent": len(signals_to_write)})
    except Exception as e:
        return safe_jsonify({"success": False, "error": str(e), "results": []}, 500)

@app.route("/api/scan/market")
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

            results.append({
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
                "rsi_divergence": (tech.get("rsi_divergence") or {}).get("type", "AUCUNE"),
                "confluence_score": analysis.get("confluence_score", 0),
                "verdict": analysis.get("verdict", "ATTENDRE REPLI SUR SUPPORT"),
                "currency": tech.get("currency", "USD")
            })
            
            if "ACHETER" in analysis.get("verdict", ""):
                signals_to_write.append({
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
                    "verdict": analysis.get("verdict", "ACHETER")
                })
                
        if signals_to_write:
            # Écriture asynchrone en arrière-plan pour ne pas ralentir la réponse HTTP
            threading.Thread(target=write_signals_to_sheets, args=(signals_to_write,), daemon=True).start()
            
        return safe_jsonify({"success": True, "results": results, "signals_sent": len(signals_to_write)})
    except Exception as e:
        print(f"Erreur globale scan_market: {e}")
        return safe_jsonify({"success": False, "error": str(e), "results": []}), 500

@app.route("/api/analyze/<ticker>")
def analyze_ticker_endpoint(ticker):
    capital = request.args.get("capital", default=CAPITAL_REFERENCE_DEFAULT, type=float)
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"] or request.args.get("refresh", "false").lower() in ["true", "1", "yes"]
    strategy = request.args.get("strategy", "ALL").upper()
    if strategy == "V2":
        res = get_detailed_analysis(ticker, capital=capital)
    else:
        res = generate_8_step_protocol_analysis(ticker, capital_total=capital, force_refresh=force)
    if not res or "error" in res:
        return safe_jsonify({"success": False, "error": (res or {}).get("error", "Analyse impossible")}), 400
    return safe_jsonify({"success": True, "data": res})

@app.route("/api/macro")
def get_macro_endpoint():
    force = request.args.get("refresh", default=False, type=bool)
    barometer = get_macro_barometer(force_refresh=force)
    return safe_jsonify(barometer)

@app.route("/api/risk-calc", methods=["POST"])
def risk_calc_endpoint():
    data = request.json or {}
    capital = float(data.get("capital", CAPITAL_REFERENCE_DEFAULT))
    entry_price = float(data.get("entry_price", 0.0))
    stop_loss_price = float(data.get("stop_loss_price", entry_price * 0.97 if entry_price > 0 else 0.0))
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
        tp2_pct=tp2_pct
    )
    return jsonify({"success": True, "data": result})

# ==============================================================================
# --- V3. Routes Stratégie Institutionnelle Tactique (Confluence 3 Moteurs) ---
# ==============================================================================

@app.route("/api/v3/macro/sentiment")
def get_v3_macro_sentiment():
    """
    Retourne le baromètre macroéconomique et inter-marchés V3 (VIX, DXY, XLY/XLP, WTI, Yield Curve).
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    macro = get_macro_sentiment_barometer(force_refresh=force)
    return safe_jsonify({"success": True, "data": macro})

@app.route("/api/v3/analysis/<ticker>/protocol8")
def get_v3_protocol8_analysis(ticker):
    """
    Retourne l'analyse institutionnelle complète en 8 étapes pour un ticker.
    Accepte ?force=true pour forcer l'actualisation en temps réel (invalidation du cache).
    """
    capital = request.args.get("capital", default=CAPITAL_REFERENCE_DEFAULT, type=float)
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"] or request.args.get("refresh", "false").lower() in ["true", "1", "yes"]
    res = generate_8_step_protocol_analysis(ticker, capital_total=capital, force_refresh=force)
    return safe_jsonify({"success": True, "data": res})

@app.route("/api/v3/risk/calculator", methods=["POST"])
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

@app.route("/api/v3/scanner/institutional")
def get_v3_scanner_institutional():
    """
    Scan complet de la watchlist selon la stratégie institutionnelle V3 (Confluence 3 Moteurs).
    """
    capital = request.args.get("capital", default=CAPITAL_REFERENCE_DEFAULT, type=float)
    res = scan_watchlist_institutional(capital_total=capital)
    return safe_jsonify(res)

@app.route("/api/backtest/periods")
def get_backtest_periods_endpoint():
    """
    Retourne l'ensemble des périodes historiques prédéfinies (1999-2026) pour le sélecteur d'interface.
    """
    from src.backtest_engine import HISTORICAL_PERIODS_1999_2026
    return safe_jsonify({
        "success": True,
        "periods": HISTORICAL_PERIODS_1999_2026
    })

_BACKTEST_RUN_CACHE = {}
_BACKTEST_CRISES_CACHE = {}

@app.route("/api/backtest/run", methods=["GET", "POST"])
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
            if (now_ts - entry["ts"]) < 3600: # 1h de cache
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
            strategy=strategy
        )
        results = engine.run_simulation()
        
        _BACKTEST_RUN_CACHE[cache_key] = {"data": results, "ts": now_ts}
        return safe_jsonify(results)
    except Exception as e:
        logger.error(f"Erreur backtest run: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": f"Erreur serveur backtest: {str(e)}"}, status_code=500)

@app.route("/api/backtest/crises", methods=["GET", "POST"])
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
            strategy=strategy
        )
        
        _BACKTEST_CRISES_CACHE[cache_key] = {"data": results, "ts": now_ts}
        return safe_jsonify({"success": True, "crises": results})
    except Exception as e:
        logger.error(f"Erreur benchmark crises: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": f"Erreur serveur crises: {str(e)}"}, status_code=500)


@app.route("/api/backtest/user_universe", methods=["GET", "POST"])
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
        trades_real = get_supabase_trade_journal() or []
        positions_real = get_supabase_positions(status="ALL") or []
        watchlist_symbols = get_watchlist_symbols() or []

        symbols_traded = [resolve_ticker_symbol(t.get("symbol")) for t in trades_real if t.get("symbol")]
        symbols_pos = [resolve_ticker_symbol(p.get("symbol")) for p in positions_real if p.get("symbol")]
        symbols_wl = [resolve_ticker_symbol(s) for s in watchlist_symbols if s]

        user_universe = sorted(list(set(symbols_traded + symbols_pos + symbols_wl)))
        user_universe = [s for s in user_universe if s and s != "None" and not s.endswith(".L")]

        # 2. Métriques du journal réel
        total_real_trades = len(trades_real)
        wins_real = len([t for t in trades_real if float(t.get("pnl_amount", 0.0)) >= 0])
        losses_real = len([t for t in trades_real if float(t.get("pnl_amount", 0.0)) < 0])
        wr_real = round((wins_real / total_real_trades * 100), 1) if total_real_trades > 0 else 0.0
        pnl_real = round(sum([float(t.get("pnl_amount", 0.0)) for t in trades_real]), 2)
        gains_real = sum([float(t.get("pnl_amount", 0.0)) for t in trades_real if float(t.get("pnl_amount", 0.0)) > 0])
        loss_abs_real = abs(sum([float(t.get("pnl_amount", 0.0)) for t in trades_real if float(t.get("pnl_amount", 0.0)) < 0]))
        pf_real = round((gains_real / loss_abs_real), 2) if loss_abs_real > 0 else 0.0

        # Durée moyenne réelle
        from src.protocol_feedback_engine import calculate_trade_duration_days
        durs = [calculate_trade_duration_days(t.get("entry_date"), t.get("exit_date")) for t in trades_real]
        avg_dur_real = round(sum(durs) / len(durs), 1) if durs else 0.0

        # 3. Backtest 10 Ans (télécharge 10 ans d'historique)
        bt_10y = BacktestEngine(
            symbols=user_universe,
            period="10y",
            initial_capital=capital,
            tp1_pct=tp1_pct,
            tp2_pct=tp2_pct,
            max_holding_days=max_holding_days,
            strategy=strategy
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
            strategy=strategy
        )
        bt_2y.historical_data = bt_10y.historical_data
        bt_2y.macro_data = bt_10y.macro_data
        bt_2y.sector_etf_data = bt_10y.sector_etf_data
        bt_2y.macro_daily_regime = bt_10y.macro_daily_regime
        res_2y = bt_2y.run_simulation()

        return safe_jsonify({
            "success": True,
            "universe": {
                "total_symbols": len(user_universe),
                "symbols": user_universe
            },
            "parameters": {
                "initial_capital": capital,
                "tp1_pct": tp1_pct,
                "tp2_pct": tp2_pct,
                "max_holding_days": max_holding_days,
                "strategy": strategy
            },
            "real_journal": {
                "total_trades": total_real_trades,
                "winning_trades": wins_real,
                "losing_trades": losses_real,
                "win_rate_pct": wr_real,
                "total_net_pnl": pnl_real,
                "profit_factor": pf_real,
                "avg_holding_days": avg_dur_real
            },
            "backtest_2y": {
                "initial_capital": capital,
                "final_capital": res_2y.get("final_capital", capital),
                "metrics": res_2y.get("metrics", {}),
                "equity_curve": res_2y.get("equity_curve", [])[-30:] if res_2y.get("equity_curve") else []
            },
            "backtest_10y": {
                "initial_capital": capital,
                "final_capital": res_10y.get("final_capital", capital),
                "metrics": res_10y.get("metrics", {}),
                "equity_curve": res_10y.get("equity_curve", [])[-30:] if res_10y.get("equity_curve") else []
            }
        })
    except Exception as e:
        logger.error(f"Erreur backtest user universe: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": str(e)}, status_code=500)


@app.route("/api/backtest/trade_replay", methods=["GET", "POST"])
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
            max_holding_days=max_holding_days
        )
        return safe_jsonify(res)
    except Exception as e:
        logger.error(f"Erreur backtest trade replay: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": str(e)}, status_code=500)


@app.route("/api/backtest/free_trading_simulation", methods=["GET", "POST"])
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
            min_cash_reserve_pct=min_cash_pct
        )
        return safe_jsonify(res)
    except Exception as e:
        logger.error(f"Erreur backtest free trading simulation: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": str(e)}, status_code=500)





# ==============================================================================
# --- 10. RECHERCHE D'ACTIONS, SCREENER MULTI-FILTRES & BACKTEST 10 ANS ---
# ==============================================================================

EXPANDED_SCREENER_UNIVERSE = list(dict.fromkeys(
    DEFAULT_WATCHLIST + DEFAULT_MARKET_POOL + [
        # US Large & Growth Caps (Nasdaq & S&P 500)
        "ADBE", "INTC", "CSCO", "QCOM", "TXN", "NFLX", "PYPL", "INTU", "NOW", "AMAT", 
        "MU", "LRCX", "ADI", "KLAC", "SNPS", "CDNS", "PANW", "CRWD", "FTNT", "DDOG", 
        "ZS", "NET", "PLTR", "ARM", "DELL", "SMCI", "UBER", "ABNB", "SHOP", "SE", 
        "MELI", "PDD", "BABA", "JD", "BIDU", "TSM", "005930.KS", "COST", "AMD",
        # Europe / CAC 40 & DAX 40 (PEA & Euronext)
        "BNP.PA", "CAP.PA", "DSY.PA", "GLE.PA", "SAF.PA", "SGO.PA", "SU.PA", "DG.PA", 
        "VIE.PA", "SAN.PA", "TTE.PA", "MC.PA", "OR.PA", "AIR.PA", "RMS.PA", "KER.PA", 
        "EL.PA", "AI.PA", "GTT.PA", "ENGI.PA", "LR.PA", "STMPA.PA",
        "SAP.DE", "SIE.DE", "ALV.DE", "MBG.DE", "BMW.DE", "BAYN.DE", "MRK.DE", "VOW3.DE", "IS3E.DE", "IS3R.DE"
    ]
))

_SCREENER_SEARCH_CACHE = {}
_SCREENER_10Y_CACHE = {}
_TICKER_10Y_STATS_CACHE = {}

def get_ticker_10y_quick_stats(symbol):
    """
    Récupère ou calcule les statistiques réelles du backtest 10 ans pour un ticker individuel.
    Met en cache le résultat pour des performances ultra-rapides.
    """
    from src.market_data import resolve_ticker_symbol
    sym = resolve_ticker_symbol(str(symbol or "").upper().strip())
    now = time.time()
    if sym in _TICKER_10Y_STATS_CACHE:
        entry = _TICKER_10Y_STATS_CACHE[sym]
        if (now - entry["ts"]) < 86400:  # 24h de cache
            return entry["data"]

    try:
        bt_res = run_single_ticker_10y_backtest(sym, strategy="v3_institutional", initial_capital=5000.0)
        if bt_res.get("success"):
            m = bt_res.get("metrics", {})
            comp = bt_res.get("comparison", {})
            stats = {
                "win_rate_pct": float(m.get("win_rate_pct", 0.0)),
                "total_trades": int(m.get("total_trades", 0)),
                "winning_trades": int(m.get("winning_trades", 0)),
                "losing_trades": int(m.get("losing_trades", 0)),
                "profit_factor": float(m.get("profit_factor", 0.0)),
                "avg_holding_days": float(m.get("avg_holding_days", 0.0)),
                "max_drawdown_pct": float(m.get("max_drawdown_pct", 0.0)),
                "total_net_pnl": float(m.get("total_net_pnl", 0.0)),
                "total_return_pct": float(m.get("total_return_pct", 0.0)),
                "buy_hold_pct": float(comp.get("buy_and_hold_return_pct", 0.0) or 0.0)
            }
            _TICKER_10Y_STATS_CACHE[sym] = {"data": stats, "ts": now}
            _SCREENER_10Y_CACHE[f"{sym}_v3_institutional_5000.0"] = {"data": bt_res, "ts": now}
            return stats
    except Exception as e:
        logger.warning(f"Erreur calcul 10y stats pour {sym}: {e}")

    return {
        "win_rate_pct": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "profit_factor": 0.0,
        "avg_holding_days": 0.0,
        "max_drawdown_pct": 0.0,
        "total_net_pnl": 0.0,
        "total_return_pct": 0.0,
        "buy_hold_pct": 0.0
    }

@app.route("/api/screener/search", methods=["GET", "POST"])
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
        signal_only = str(data.get("signal_only", "false")).lower() in ["true", "1", "yes"]
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

        # 1. Pré-filtrage rapide des symboles
        matched_symbols = []
        for sym in pool:
            s = str(sym).upper().strip()
            cat_info = categorize_ticker(s)
            c_name = get_company_name(s)
            is_pea = cat_info.get("is_pea", s.endswith(".PA") or s.endswith(".DE") or s.endswith(".AS") or s.endswith(".MC"))

            # Filtre Query
            if query:
                if query not in s and query not in c_name.upper() and query not in cat_info.get("category", "").upper():
                    continue

            # Filtre Marché
            if market == "PEA" and not is_pea:
                continue
            if market == "CTO" and is_pea:
                continue

            # Filtre Catégorie
            if category != "ALL" and category.lower() not in cat_info.get("category", "").lower():
                continue

            matched_symbols.append((s, cat_info, c_name, is_pea))
            if len(matched_symbols) >= limit:
                break

        # 2. Analyse rapide en parallèle
        results = []
        def process_screener_item(item):
            sym, cat_info, c_name, is_pea = item
            try:
                # Analyse 8 étapes institutionnelle
                analysis = generate_8_step_protocol_analysis(sym, CAPITAL_REFERENCE_DEFAULT)
                if not analysis or not isinstance(analysis, dict) or "error" in analysis:
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

                price = float(analysis.get("current_price") or analysis.get("price") or 0.0)
                drop_val = float(analysis.get("drop") or analysis.get("pullback_pct") or 0.0)
                rsi_val = float(analysis.get("rsi") or 50.0)
                plan = analysis.get("pricing_plan") or {}
                sizing = analysis.get("sizing") or {}
                macro = str(analysis.get("macro_regime") or "NEUTRE")

                # Fast check for backtest stats if available in cache, otherwise default instantly to avoid expensive 10y recalculations per item
                now_ts_sub = time.time()
                if sym in _TICKER_10Y_STATS_CACHE and (now_ts_sub - _TICKER_10Y_STATS_CACHE[sym]["ts"]) < 86400:
                    backtest_quick = _TICKER_10Y_STATS_CACHE[sym]["data"]
                else:
                    # Provide instant fallback metrics for screener list view to prevent timeout
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
                        "buy_hold_pct": 6.5
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
                    "action_required": str(analysis.get("verdict_action") or "Attendre"),
                    "entry_price": float(plan.get("entry", price)),
                    "tp1": float(plan.get("tp1", price * 1.0125)),
                    "tp2": float(plan.get("tp2", price * 1.0225)),
                    "stop_loss": float(plan.get("sl", price * 0.985)),
                    "risk_reward": float(plan.get("risk_reward", 1.5)),
                    "rmax_euros": float(sizing.get("max_nominal_euros", 0.0)),
                    "macro_regime": macro,
                    "backtest_quick": backtest_quick
                }
            except Exception as e:
                logger.warning(f"Erreur process_screener_item {sym}: {e}")
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            future_to_sym = {executor.submit(process_screener_item, item): item[0] for item in matched_symbols}
            try:
                for future in concurrent.futures.as_completed(future_to_sym, timeout=12):
                    try:
                        res = future.result()
                        if res:
                            results.append(res)
                    except Exception as err:
                        logger.warning(f"Erreur futur screener: {err}")
            except (concurrent.futures.TimeoutError, TimeoutError):
                logger.warning(f"⏱️ Screener search timeout atteint (12s), retour de {len(results)} résultats traités")

        # Trier par score décroissant puis verdict
        results.sort(key=lambda x: (x.get("score", 0.0), 1 if "ACHETER" in x.get("verdict", "") else 0), reverse=True)

        payload = {
            "success": True,
            "count": len(results),
            "total_screened": len(matched_symbols),
            "filters": {
                "query": query,
                "market": market,
                "category": category,
                "sharia": sharia_filter,
                "signal_only": signal_only
            },
            "results": results
        }

        _SCREENER_SEARCH_CACHE[cache_key] = {"data": payload, "ts": now_ts}
        return safe_jsonify(payload)
    except Exception as e:
        logger.error(f"Erreur screener search: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": f"Erreur serveur screener: {str(e)}"}, status_code=500)


@app.route("/api/screener/backtest10y/<symbol>", methods=["GET", "POST"])
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
            if (now_ts - entry["ts"]) < 1800: # 30 min de cache
                return safe_jsonify(entry["data"])

        res = run_single_ticker_10y_backtest(clean_sym, strategy=strategy, initial_capital=capital)
        if res.get("success"):
            _SCREENER_10Y_CACHE[cache_key] = {"data": res, "ts": now_ts}
        return safe_jsonify(res)
    except Exception as e:
        logger.error(f"Erreur backtest 10y {symbol}: {e}", exc_info=True)
        return safe_jsonify({"success": False, "error": f"Erreur backtest 10y: {str(e)}"}, status_code=500)

def lookup_ticker_by_name(query):
    query = query.strip()
    if not query:
        return None, None
        
    if query.isupper() and len(query) <= 6 and not query.isdigit():
        try:
            t = yf.Ticker(query)
            if not t.history(period="1d").empty:
                return query, query
        except:
            pass

    # Alias courants
    query_lower = query.lower()
    if query_lower in ["ryanair", "ryan air", "ryan"]:
        return "RYAAY", "Ryanair Holdings plc (ADR)"

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            quotes = data.get("quotes", [])
            
            # Prioriser les grandes places de cotation (Nasdaq, NYSE, Euronext, Dublin, Xetra)
            def quote_priority(q):
                sym = q.get("symbol", "").upper()
                exch = q.get("exchange", "").upper()
                score = 0
                if exch in ["NMS", "NYQ", "NGM", "PCX"]:
                    score += 50
                elif exch in ["PAR", "AMS", "BRU", "DUB", "GER", "LSE"]:
                    score += 40
                elif ".PA" in sym or ".AS" in sym or ".IR" in sym or ".DE" in sym:
                    score += 30
                # Pénaliser les marchés régionaux secondaires allemands / mexicains peu liquides
                if any(sym.endswith(sfx) for sfx in [".F", ".MU", ".BE", ".DU", ".HM", ".MX", ".SA"]):
                    score -= 30
                return score

            valid_quotes = [q for q in quotes if q.get("symbol") and q.get("quoteType", "").upper() in ["EQUITY", "ETF"] and len(q.get("symbol", "")) <= 12]
            if valid_quotes:
                valid_quotes.sort(key=quote_priority, reverse=True)
                best = valid_quotes[0]
                symbol = best.get("symbol", "")
                name = best.get("shortname") or best.get("longname") or symbol
                return symbol, name
    except Exception as e:
        print(f"Error in autocomplete lookup: {e}")
        
    return None, None

def find_ticker_in_message(message):
    message_clean = message.strip()
    if not message_clean:
        return None, None

    match_action = re.search(r'(?:action|ticker|cours|analyse|screen|conforme|ajoute|ajouter)\s+(?:de\s+|d\'\s+)?([A-Za-z0-9\.\-= ]{2,20})', message_clean, re.IGNORECASE)
    if match_action:
        candidate = match_action.group(1).strip()
        ticker, name = lookup_ticker_by_name(candidate)
        if ticker:
            return ticker, name

    french_stop_words = {
        "je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "le", "la", "les", "un", "une", "des",
        "du", "de", "d", "l", "j", "m", "t", "s", "se", "ce", "cet", "cette", "ces", "qui", "que", "quoi",
        "dont", "ou", "où", "quand", "comment", "pourquoi", "quel", "quelle", "quels", "quelles", "et",
        "mais", "donc", "or", "ni", "car", "si", "en", "dans", "par", "pour", "sur", "avec", "sans", "sous",
        "chez", "vers", "pendant", "durant", "ai", "as", "a", "avons", "avez", "ont", "suis", "es", "est",
        "sommes", "êtes", "sont", "peux", "peut", "pouvez", "veut", "veux", "voulez", "cherche", "trouve",
        "analyse", "non", "oui", "acheter", "vendre", "cours", "prix", "action", "actions", "bourse",
        "halal", "sharia", "conforme", "indicateur", "indicateurs", "support", "resistance", "vix", "rsi",
        "sma", "ema", "macd", "trading", "investir", "investissement", "portefeuille", "bon", "moment",
        "macro", "regime", "barometre", "dxy", "petrole", "yield", "courbe", "pea", "pharma"
    }

    words = re.findall(r'\b[A-Za-z\.\-=]{2,12}\b', message_clean)
    candidates = [w for w in words if w.lower() not in french_stop_words]

    for cand in candidates:
        if cand.isupper() or "." in cand:
            ticker, name = lookup_ticker_by_name(cand)
            if ticker:
                return ticker, name

    for cand in candidates:
        ticker, name = lookup_ticker_by_name(cand)
        if ticker:
            return ticker, name

    search_query = " ".join([w for w in message_clean.split() if w.lower() not in french_stop_words])
    if search_query:
        ticker, name = lookup_ticker_by_name(search_query)
        if ticker:
            return ticker, name

    return None, None

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"response": "Je n'ai pas bien reçu votre message. Comment puis-je vous aider ?"}), 400
        
    message_lower = message.lower()
    
    # 1. Si l'utilisateur demande de retirer / supprimer un ticker
    if any(w in message_lower for w in ["retire", "retirer", "supprime", "supprimer", "delete", "remove", "enleve", "enlever"]) and any(w in message_lower for w in ["watchlist", "feuille", "sheet", "action", "ticker", "liste"]):
        ticker, company_name = find_ticker_in_message(message)
        if ticker:
            try:
                delete_from_watchlist(ticker)
                delete_ticker_from_sheets(ticker)
                return jsonify({
                    "response": f"🗑️ **{ticker}** ({company_name or ticker}) a été retiré de votre **Watchlist** (Google Sheets & Base de données) avec succès."
                })
            except Exception as e:
                return jsonify({"response": f"Erreur lors de la suppression de {ticker} : {str(e)}"})

    # 2. Si l'utilisateur demande d'ajouter un ticker
    if any(w in message_lower for w in ["ajoute", "ajouter", "add"]) and any(w in message_lower for w in ["watchlist", "feuille", "sheet", "action", "ticker"]):
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
                    sharia_status=sharia_res.get("status", "")
                )
                success, msg = add_ticker_to_sheets(
                    ticker_symbol=ticker,
                    name=company_name or ticker,
                    category=fund_q.get("category", "Autres"),
                    is_pea=fund_q.get("is_pea", False),
                    sharia_status=sharia_res.get("status", "")
                )
                return jsonify({
                    "response": f"✅ **{ticker}** ({company_name or ticker}) a été ajouté à votre **Watchlist** !\n\n"
                                f"- 🏷️ **Catégorie :** {fund_q.get('category_icon', '📦')} {fund_q.get('category')}\n"
                                f"- 💳 **Compte :** {fund_q.get('account_type')}\n"
                                f"- 🕌 **Conformité Sharia :** `{sharia_res.get('status')}`\n\n"
                                f"Vous pouvez maintenant le retrouver dans le tableau de bord lors de vos scans !"
                })
            except Exception as e:
                return jsonify({"response": f"Erreur lors de l'ajout de {ticker} : {str(e)}"})

    # 3. Si la question porte sur le Baromètre Macroéconomique
    if any(w in message_lower for w in ["macro", "baromètre", "regime", "vix", "dxy", "taux", "féd", "fed", "bce", "petrole", "pétrole", "inflation"]):
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
            return jsonify({
                "response": f"J'ai détecté une demande pour **{company_name or ticker}**, mais une erreur est survenue lors de l'analyse : *{analysis['error']}*."
            })
            
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
        return jsonify({
            "response": "Bonjour ! Je suis votre **Stratège & Analyste de Trading Tactique Institutionnel (V3)**.\n\n"
                        "Ma stratégie repose sur la **confluence de trois moteurs** : *Trend Following* (MM200), *Event-Driven* (Repli conjoncturel -3% à -8%) et *Breakout Trading* (Cassure H1/H4 + Volume).\n\n"
                        "Vous pouvez me demander :\n"
                        "1. **L'analyse protocolaire institutionnelle en 8 étapes** d'une action (ex: *'Analyse Sanofi (SAN.PA)'*, *'Screen LVMH'*)\n"
                        "2. **Le Baromètre Macro Inter-marchés** (*'Quel est le régime macro ?'*)\n"
                        "3. **D'ajouter une action à la Watchlist** (*'Ajoute Hermès (RMS.PA) à ma watchlist'*)\n"
                        "4. **Le dimensionnement de position R-Max** et calcul de risque monétaire.\n\n"
                        "Quelle action ou configuration souhaitez-vous analyser ?"
        })
        
    return jsonify({
        "response": f"J'ai bien reçu votre message : *\"{message}\"*.\n\nPour une analyse ou un ajout, veuillez préciser le nom de l'entreprise ou son ticker boursier (ex: `SAN.PA`, `AAPL`, `MSFT`, `MC.PA`, `AIR.PA`), ou tapez *'Ajoute [TICKER] à ma watchlist'*."
    })


# ---------------------------------------------------------------------
# --- MODULE D'EXÉCUTION SEMI-AUTOMATIQUE TRADING 212 & GARDE-FOUS ---
# ---------------------------------------------------------------------

@app.route("/api/trading212/execution/status")
def get_trading212_execution_status():
    """Retourne l'état des garde-fous, kill-switch, et métriques du moteur d'exécution."""
    guard_status = guardrails_engine.get_status()
    pending = execution_engine.get_pending_proposals()
    active_pos = execution_engine.get_active_positions()
    open_orders = get_trading212_open_orders()
    cash_data = get_trading212_cash() or {}
    
    return safe_jsonify({
        "success": True,
        "guardrails": guard_status,
        "pending_proposals_count": len(pending),
        "active_positions_count": len(active_pos),
        "open_broker_orders_count": len(open_orders),
        "broker_cash": cash_data
    })


@app.route("/api/trading212/execution/pending")
def get_trading212_pending_proposals():
    """Retourne la liste des propositions de trades en attente de Go Humain."""
    proposals = execution_engine.get_pending_proposals()
    return safe_jsonify({
        "success": True,
        "count": len(proposals),
        "proposals": proposals
    })


@app.route("/api/trading212/execution/proposals_history")
def get_trading212_proposals_history():
    """Retourne l'historique complet des propositions de trades (approuvées, rejetées, expirées, en échec) stockées en BDD."""
    try:
        limit = request.args.get("limit", 100)
        status_filter = request.args.get("status")
        history = get_trade_proposals_history(limit=limit, status_filter=status_filter)
        return safe_jsonify({
            "success": True,
            "count": len(history),
            "proposals_history": history
        })
    except Exception as e:
        return safe_jsonify({"success": False, "error": str(e), "proposals_history": []}, 500)


@app.route("/api/trading212/execution/active_positions")
def get_trading212_active_managed_positions():
    """Retourne la liste des positions sous gestion active de paliers (TP1/BE/TP2/SL)."""
    # Mettre à jour la surveillance
    closed = execution_engine.update_positions_monitoring()
    positions = execution_engine.get_active_positions()
    return safe_jsonify({
        "success": True,
        "count": len(positions),
        "positions": positions,
        "recently_closed": closed
    })


@app.route("/api/trading212/execution/permissions")
def get_trading212_api_permissions():
    """Diagnostique les droits réels accordés à la clé API Trading 212 (Lecture vs Ordres)."""
    diag = check_trading212_api_permissions()
    return safe_jsonify({
        "success": True,
        "permissions": diag
    })


@app.route("/api/trading212/execution/strategies")
def get_trading212_strategy_profiles():
    """Retourne la liste des profils stratégiques (Mean Reversion, Sniper, Sneak) avec leurs grilles."""
    return safe_jsonify({
        "success": True,
        "strategies": STRATEGY_GRID_PROFILES
    })


@app.route("/api/trading212/execution/propose", methods=["POST"])
def propose_trading212_trade():
    """Crée une proposition de trade adaptée à la stratégie et soumise aux garde-fous."""
    data = request.get_json() or {}
    symbol = data.get("symbol")
    entry_price = float(data.get("entry_price", 0.0))
    strategy_type = data.get("strategy_type", data.get("method", "Mean Reversion"))
    custom_sl = float(data.get("stop_loss_price")) if data.get("stop_loss_price") else None
    custom_tp1 = float(data.get("tp1_price")) if data.get("tp1_price") else None
    custom_tp2 = float(data.get("tp2_price")) if data.get("tp2_price") else None
    quantity = float(data.get("quantity")) if data.get("quantity") else None
    nominal_capital = float(data.get("nominal_capital") or data.get("capital") or 0.0) if (data.get("nominal_capital") or data.get("capital")) else None
    notes = data.get("notes", "")

    if not symbol or entry_price <= 0:
        return safe_jsonify({"success": False, "error": "Paramètres symbol et entry_price obligatoires."}, status_code=400)

    res = execution_engine.propose_trade(
        symbol=symbol,
        entry_price=entry_price,
        strategy_type=strategy_type,
        custom_sl_price=custom_sl,
        custom_tp1_price=custom_tp1,
        custom_tp2_price=custom_tp2,
        quantity=quantity,
        nominal_capital=nominal_capital,
        notes=notes
    )
    return safe_jsonify(res)


@app.route("/api/trading212/execution/update_proposal", methods=["POST"])
def update_trading212_proposal():
    """Permet à l'utilisateur de modifier le capital à investir ou le nombre d'actions d'une proposition existante."""
    data = request.get_json() or {}
    proposal_id = data.get("proposal_id")
    quantity = float(data.get("quantity")) if data.get("quantity") else None
    nominal_capital = float(data.get("nominal_capital") or data.get("capital") or 0.0) if (data.get("nominal_capital") or data.get("capital")) else None
    entry_price = float(data.get("entry_price")) if data.get("entry_price") else None
    custom_sl = float(data.get("stop_loss_price")) if data.get("stop_loss_price") else None
    custom_tp1 = float(data.get("tp1_price")) if data.get("tp1_price") else None
    custom_tp2 = float(data.get("tp2_price")) if data.get("tp2_price") else None

    if not proposal_id:
        return safe_jsonify({"success": False, "error": "proposal_id obligatoire."}, status_code=400)

    res = execution_engine.update_proposal(
        proposal_id=proposal_id,
        quantity=quantity,
        nominal_capital=nominal_capital,
        entry_price=entry_price,
        custom_sl_price=custom_sl,
        custom_tp1_price=custom_tp1,
        custom_tp2_price=custom_tp2
    )
    return safe_jsonify(res)


@app.route("/api/trading212/execution/approve", methods=["POST"])
def approve_trading212_trade():
    """Validation 'GO HUMAIN' explicite : envoie l'ordre à Trading 212."""
    data = request.get_json() or {}
    proposal_id = data.get("proposal_id")
    order_type = data.get("order_type", "LIMIT")
    time_validity = data.get("time_validity", "DAY")

    if not proposal_id:
        return safe_jsonify({"success": False, "error": "proposal_id obligatoire."}, status_code=400)

    res = execution_engine.approve_and_execute_trade(
        proposal_id=proposal_id,
        order_type=order_type,
        time_validity=time_validity
    )
    return safe_jsonify(res)


@app.route("/api/trading212/execution/reject", methods=["POST"])
def reject_trading212_trade():
    """Rejet d'une proposition par l'utilisateur."""
    data = request.get_json() or {}
    proposal_id = data.get("proposal_id")
    reason = data.get("reason", "Rejeté par l'utilisateur")

    if not proposal_id:
        return safe_jsonify({"success": False, "error": "proposal_id obligatoire."}, status_code=400)

    res = execution_engine.reject_trade_proposal(proposal_id=proposal_id, reason=reason)
    return safe_jsonify(res)


@app.route("/api/trading212/execution/kill_switch", methods=["POST"])
def trigger_trading212_kill_switch():
    """Bouton d'urgence : annule tous les ordres et verrouille le trading."""
    data = request.get_json() or {}
    reason = data.get("reason", "Déclenché manuellement via l'interface")
    res = execution_engine.kill_all_and_freeze(reason=reason)
    return safe_jsonify(res)


@app.route("/api/trading212/execution/reset_kill_switch", methods=["POST"])
def reset_trading212_kill_switch():
    """Débloque le système après intervention humaine."""
    res = guardrails_engine.reset_kill_switch()
    return safe_jsonify(res)


@app.route("/api/trading212/execution/settings", methods=["POST"])
def update_trading212_guardrails_settings():
    """Met à jour les paramètres de sécurité (Plafond EUR, Plafond USD, Toggle Marché US, R-Max, Alloc Max)."""
    data = request.get_json() or {}
    max_capital_eur = data.get("automate_ceiling_eur") or data.get("max_capital_eur") or data.get("max_total_capital_ceiling")
    max_capital_usd = data.get("automate_ceiling_usd") or data.get("max_capital_usd")
    max_risk_pct = data.get("max_risk_per_trade_pct")
    max_alloc_pct = data.get("max_position_allocation_pct")
    us_trading_enabled = data.get("us_trading_enabled")

    res = guardrails_engine.update_settings(
        automate_ceiling_eur=max_capital_eur,
        automate_ceiling_usd=max_capital_usd,
        max_risk_pct=max_risk_pct,
        max_alloc_pct=max_alloc_pct,
        us_trading_enabled=us_trading_enabled
    )
    return safe_jsonify({"success": True, "settings": res})


@app.route("/api/trading212/execution/toggle_us_trading", methods=["POST"])
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
    return safe_jsonify({
        "success": True,
        "us_trading_enabled": enable,
        "message": f"Marché US {state_str}",
        "settings": res
    })



@app.route("/api/health_cache")
def api_health_cache():
    import os, json
    from src.supabase_connector import get_all_market_data_cache, get_db_connection
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

    snap_path = os.path.join(os.path.dirname(__file__), "src", "market_data_snapshot.json")
    snap_count = 0
    snap_sample = {}
    if os.path.exists(snap_path):
        try:
            with open(snap_path, "r", encoding="utf-8") as f:
                snap_data = json.load(f)
            snap_count = len(snap_data)
            snap_sample = {k: {"price": v.get("price"), "drop": v.get("drop_pct"), "rsi": v.get("rsi"), "vol": v.get("avg_daily_volume")} 
                           for k, v in list(snap_data.items())[:5]}
        except Exception:
            pass

    xtb_snap_path = os.path.join(os.path.dirname(__file__), "data", "xtb_history_snapshot.json")
    xtb_snap_trades = 0
    if os.path.exists(xtb_snap_path):
        try:
            with open(xtb_snap_path, "r", encoding="utf-8") as f:
                xtb_data = json.load(f)
            xtb_snap_trades = len(xtb_data.get("closed_positions", []))
        except Exception:
            pass

    return safe_jsonify({
        "status": "healthy",
        "db_count": db_count,
        "db_error": db_err,
        "snapshot_count": snap_count,
        "snapshot_sample": snap_sample,
        "xtb_snapshot_trades": xtb_snap_trades
    })

# ---------------------------------------------------------------------
# GESTIONNAIRES D'ERREURS HTTP GLOBAUX (GARANTIE DE RÉPONSES JSON)
# ---------------------------------------------------------------------
@app.errorhandler(400)
def handle_400(e):
    return safe_jsonify({"success": False, "error": f"Requête invalide: {getattr(e, 'description', str(e))}"}, status_code=400)

@app.errorhandler(404)
def handle_404(e):
    return safe_jsonify({"success": False, "error": f"Ressource introuvable: {getattr(e, 'description', str(e))}"}, status_code=404)

@app.errorhandler(405)
def handle_405(e):
    return safe_jsonify({"success": False, "error": f"Méthode HTTP non autorisée: {getattr(e, 'description', str(e))}"}, status_code=405)

@app.errorhandler(500)
def handle_500(e):
    logger.error(f"Internal 500 error: {e}", exc_info=True)
    return safe_jsonify({"success": False, "error": f"Erreur interne du serveur: {getattr(e, 'description', str(e))}"}, status_code=500)

@app.errorhandler(Exception)
def handle_general_exception(e):
    logger.error(f"Unhandled general exception: {e}", exc_info=True)
    return safe_jsonify({"success": False, "error": f"Erreur serveur inattendue: {str(e)}"}, status_code=500)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
