import os
import re
import math
import concurrent.futures
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import yfinance as yf
from datetime import datetime
import requests

from src.sharia_screen import screen_ticker
from src.macro_regime import get_macro_barometer
from src.market_data import (
    fetch_market_data,
    analyze_technical_setup,
    qualify_price_drop,
    check_earnings_blackout,
    check_fundamental_quality,
    categorize_ticker,
    calculate_sector_relative_strength
)
from src.risk_manager import calculate_trade_sizing, calculate_confluence_score
from src.backtest_engine import BacktestEngine, CRISIS_PERIODS, run_all_crises_stress_test
from src.sheets_connector import read_watchlist_from_sheets, write_signals_to_sheets, add_ticker_to_sheets
from src.config import (
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
    Parcourt récursivement les structures pour convertir tout NaN, Inf, -Inf en 0.0
    et garantir un JSON strictement valide sans erreurs de parsing JavaScript.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return obj
    elif isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_for_json(item) for item in obj]
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

def get_detailed_analysis(ticker_symbol, capital=CAPITAL_REFERENCE_DEFAULT):
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
        
        company_name = ticker_symbol
        try:
            info = ticker_obj.info
            company_name = info.get("longName") or info.get("shortName") or ticker_symbol
        except:
            pass
            
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
        
        analysis_cache[ticker_symbol] = analysis
        return analysis
    except Exception as e:
        return {"error": str(e), "symbol": ticker_symbol}

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/watchlist")
def get_watchlist():
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    watchlist = read_watchlist_from_sheets(force_refresh=force)
    return safe_jsonify({"watchlist": watchlist})

@app.route("/api/watchlist/add", methods=["POST"])
def add_watchlist_ticker():
    """
    Endpoint pour ajouter ou mettre à jour une action dans Google Sheets et la Watchlist active.
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

    price_str = f"{price:.2f} €" if currency == "EUR" else f"{price:.2f} $" if price > 0 else ""

    # 4. Écrire dans Google Sheets
    success, msg = add_ticker_to_sheets(
        ticker_symbol=symbol,
        name=name,
        category=category,
        is_pea=is_pea,
        sharia_status=sharia_status,
        source_verif=source_verif,
        current_price_str=price_str
    )

    return jsonify({
        "success": True,
        "message": msg,
        "ticker": symbol,
        "name": name,
        "category": category,
        "is_pea": is_pea,
        "account_type": "PEA (Europe)" if is_pea else "CTO (US)",
        "sharia_status": sharia_status,
        "data": analysis
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
    find_anti_fifo_opportunities
)
from src.trading212_connector import (
    test_trading212_connection,
    get_trading212_cash,
    get_trading212_open_positions,
    set_runtime_trading212_config
)
from src.sheets_connector import (
    add_position_to_sheets,
    close_position_in_sheets,
    batch_import_journal_to_sheets,
    read_journal_from_sheets,
    batch_import_positions_to_sheets,
    read_treasury_from_sheets,
    batch_import_treasury_to_sheets
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

@app.route("/api/trading212/config", methods=["POST"])
def configure_trading212():
    """
    Enregistre et teste la clé API Trading 212 fournie depuis l'interface.
    """
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
    Retourne le détail des soldes d'espèces, dépôts, retraits, dividendes et opérations de trésorerie.
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    cash_ops = read_treasury_from_sheets(force_refresh=force)
    summary = calculate_cash_and_treasury_summary(cash_ops)
    return safe_jsonify({
        "success": True,
        "summary": summary,
        "operations_count": len(cash_ops),
        "recent_operations": cash_ops[-50:] if cash_ops else []
    })

@app.route("/api/portfolio/diversification")
def get_portfolio_diversification():
    """
    Retourne la décomposition complète du portefeuille (catégorie/secteur, compte PEA/CTO, courtier, Actions vs Cash).
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    live_summary = get_live_portfolio_summary(force_refresh=force)
    live_positions = live_summary.get("positions", [])
    cash_ops = read_treasury_from_sheets(force_refresh=force)
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
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    trades = read_journal_from_sheets(force_refresh=force)
    stats = calculate_trading_performance_stats(trades)
    return safe_jsonify({
        "success": True,
        "total": len(trades),
        "stats": stats,
        "trades": trades
    })

@app.route("/api/portfolio/add", methods=["POST"])
def add_portfolio_position():
    """
    Ajoute manuellement une position dans Google Sheets et le suivi en direct.
    """
    data = request.json or {}
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"success": False, "error": "Le symbole de l'action est requis."}), 400
        
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
def upload_portfolio_report():
    """
    Importe un fichier Excel (.xlsx) ou CSV de rapport XTB / courtier.
    Auto-détecte les positions fermées, les positions ouvertes et les opérations de trésorerie.
    """
    file = request.files.get("file")
    if not file:
        return jsonify({"success": False, "error": "Aucun fichier fourni."}), 400
        
    filename = file.filename or ""
    content = file.read()
    
    closed_imported = 0
    open_imported = 0
    cash_imported = 0
    msg = ""

    if filename.lower().endswith(".xlsx") or filename.lower().endswith(".xls"):
        parsed = parse_xtb_excel_file(content, default_account=request.form.get("account"))
        closed_trades = parsed.get("closed_positions", [])
        open_positions = parsed.get("open_positions", [])
        cash_ops = parsed.get("cash_operations", [])

        if closed_trades:
            existing_journal = read_journal_from_sheets()
            existing_ids = {t.get("id") for t in existing_journal if t.get("id")}
            new_closed = [t for t in closed_trades if t.get("id") not in existing_ids]
            
            combined_journal = existing_journal + new_closed
            batch_import_journal_to_sheets(combined_journal)
            closed_imported = len(new_closed)

        if open_positions:
            agg_open = aggregate_open_positions(open_positions)
            for pos in agg_open:
                add_position_to_sheets(pos)
            open_imported = len(agg_open)

        if cash_ops:
            existing_cash = read_treasury_from_sheets()
            existing_cash_ids = {c.get("id") for c in existing_cash if c.get("id")}
            new_cash = [c for c in cash_ops if c.get("id") not in existing_cash_ids]
            combined_cash = existing_cash + new_cash
            batch_import_treasury_to_sheets(combined_cash)
            cash_imported = len(new_cash)

        msg = f"Rapport Excel traité : {closed_imported} trade(s) archivé(s), {open_imported} position(s) active(s), {cash_imported} opération(s) de trésorerie synchronisée(s) !"
    else:
        positions = parse_broker_csv(content)
        if not positions:
            return jsonify({"success": False, "error": "Aucune position valide trouvée dans le fichier CSV."}), 400
            
        for pos in positions:
            success, _ = add_position_to_sheets(pos)
            if success:
                open_imported += 1
        msg = f"{open_imported} position(s) active(s) importée(s) depuis le CSV !"

    return jsonify({
        "success": True,
        "message": msg,
        "closed_imported": closed_imported,
        "open_imported": open_imported,
        "cash_imported": cash_imported
    })

@app.route("/api/portfolio/import_all_history", methods=["POST"])
def import_all_history_files():
    """
    Importe automatiquement tous les fichiers d'historique XTB (positions fermées, ouvertes et trésorerie)
    présents dans le dossier /historique.
    """
    import os
    base_dir = os.path.dirname(os.path.abspath(__file__))
    hist_dir = os.path.join(base_dir, "historique")
    
    files_to_scan = []
    for root, _, files in os.walk(hist_dir):
        for f in files:
            if f.lower().endswith(".xlsx") and not f.startswith("~$"):
                files_to_scan.append(os.path.join(root, f))

    all_closed = []
    all_open = []
    all_cash = []
    seen_closed_ids = set()
    seen_open_ids = set()
    seen_cash_ids = set()

    # Prioriser les fichiers complets
    files_to_scan.sort(key=lambda x: ("tresorerie" not in x.lower() and "position fermee" not in x.lower(), x))

    for fpath in files_to_scan:
        acc = "PEA" if "PEA" in fpath else "CTO Dollar" if "USD" in fpath else "CTO Euro"
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

    # 1. Enregistrer dans le Journal
    batch_import_journal_to_sheets(all_closed)

    # 2. Enregistrer les positions ouvertes agrégées
    agg_open = aggregate_open_positions(all_open)
    batch_import_positions_to_sheets(agg_open)

    # 3. Enregistrer les opérations de trésorerie
    batch_import_treasury_to_sheets(all_cash)

    stats = calculate_trading_performance_stats(all_closed)
    cash_summary = calculate_cash_and_treasury_summary(all_cash)

    return jsonify({
        "success": True,
        "message": f"Synchronisation historique réussie ! {len(all_closed)} trades dans le Journal, {len(agg_open)} positions actives, {len(all_cash)} opérations de trésorerie.",
        "closed_count": len(all_closed),
        "open_count": len(agg_open),
        "cash_count": len(all_cash),
        "stats": stats,
        "treasury_summary": cash_summary
    })

@app.route("/api/portfolio/close", methods=["POST"])
def close_portfolio_position():
    """
    Clôture une position active à un cours donné et l'enregistre dans le journal de trading.
    """
    data = request.json or {}
    pos_id = data.get("id") or data.get("symbol")
    exit_price = float(data.get("exit_price", 0))
    notes = data.get("notes", "")
    
    if not pos_id or exit_price <= 0:
        return jsonify({"success": False, "error": "ID de position et prix de sortie valides requis."}), 400
        
    success, msg = close_position_in_sheets(pos_id, exit_price, notes=notes)
    return jsonify({"success": success, "message": msg})

@app.route("/api/scan/watchlist")
def scan_watchlist():
    try:
        watchlist = read_watchlist_from_sheets()
        results = []
        signals_to_write = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Exécution parallèle modérée (4 workers) avec cache TTL
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            analyses = list(executor.map(get_detailed_analysis, watchlist))
            
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
                    "verdict": analysis.get("verdict", "ACHETER LE REBOND")
                })
                
        if signals_to_write:
            try:
                write_signals_to_sheets(signals_to_write)
            except Exception as e:
                print(f"Erreur écriture signaux: {e}")
            
        return safe_jsonify({"success": True, "results": results, "signals_sent": len(signals_to_write)})
    except Exception as e:
        print(f"Erreur globale scan_watchlist: {e}")
        return safe_jsonify({"success": False, "error": str(e), "results": []}), 500

@app.route("/api/scan/market")
def scan_market():
    try:
        results = []
        signals_to_write = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
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
            try:
                write_signals_to_sheets(signals_to_write)
            except Exception as e:
                print(f"Erreur écriture signaux: {e}")
            
        return safe_jsonify({"success": True, "results": results, "signals_sent": len(signals_to_write)})
    except Exception as e:
        print(f"Erreur globale scan_market: {e}")
        return safe_jsonify({"success": False, "error": str(e), "results": []}), 500

@app.route("/api/analyze/<ticker>")
def analyze_ticker_endpoint(ticker):
    capital = request.args.get("capital", default=CAPITAL_REFERENCE_DEFAULT, type=float)
    res = get_detailed_analysis(ticker, capital=capital)
    if "error" in res:
        return safe_jsonify({"success": False, "error": res["error"]}), 400
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
    entry_price = float(data.get("entry_price", 100.0))
    stop_loss_price = float(data.get("stop_loss_price", entry_price * 0.97))
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

@app.route("/api/backtest/run", methods=["GET", "POST"])
def backtest_run_endpoint():
    """
    Exécute le backtest historique complet de la stratégie sur la Watchlist et le Market Pool.
    """
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
        max_holding_days=max_holding_days
    )
    results = engine.run_simulation()
    return safe_jsonify(results)

@app.route("/api/backtest/crises", methods=["GET", "POST"])
def backtest_crises_endpoint():
    """
    Exécute le stress-test comparatif sur toutes les grandes crises (1999, 2008, 2020, 2022 et 27 ans).
    """
    if request.method == "POST":
        data = request.json or {}
    else:
        data = request.args.to_dict()

    capital = float(data.get("capital", 5000.0))
    tp1_pct = float(data.get("tp1_pct", 1.25))
    tp2_pct = float(data.get("tp2_pct", 2.25))
    max_holding_days = int(data.get("max_holding_days", 10))

    results = run_all_crises_stress_test(
        initial_capital=capital,
        tp1_pct=tp1_pct,
        tp2_pct=tp2_pct,
        max_holding_days=max_holding_days
    )
    return safe_jsonify({"success": True, "crises": results})

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
    
    # 1. Si l'utilisateur demande d'ajouter un ticker
    if any(w in message_lower for w in ["ajoute", "ajouter", "add"]) and any(w in message_lower for w in ["watchlist", "feuille", "sheet", "action", "ticker"]):
        ticker, company_name = find_ticker_in_message(message)
        if ticker:
            try:
                t = yf.Ticker(ticker)
                fund_q = check_fundamental_quality(t, symbol=ticker)
                sharia_res = screen_ticker(ticker)
                success, msg = add_ticker_to_sheets(
                    ticker_symbol=ticker,
                    name=company_name or ticker,
                    category=fund_q.get("category", "Autres"),
                    is_pea=fund_q.get("is_pea", False),
                    sharia_status=sharia_res.get("status", "")
                )
                return jsonify({
                    "response": f"✅ **{ticker}** ({company_name or ticker}) a été ajouté à votre **Watchlist Google Sheet** !\n\n"
                                f"- 🏷️ **Catégorie :** {fund_q.get('category_icon', '📦')} {fund_q.get('category')}\n"
                                f"- 💳 **Compte :** {fund_q.get('account_type')}\n"
                                f"- 🕌 **Conformité Sharia :** `{sharia_res.get('status')}`\n\n"
                                f"Vous pouvez maintenant le retrouver dans le tableau de bord lors de vos scans !"
                })
            except Exception as e:
                return jsonify({"response": f"Erreur lors de l'ajout de {ticker} : {str(e)}"})

    # 2. Si la question porte sur le Baromètre Macroéconomique
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
        analysis = get_detailed_analysis(ticker)
        if "error" in analysis:
            return jsonify({
                "response": f"J'ai détecté une demande pour **{company_name or ticker}**, mais une erreur est survenue lors de l'analyse : *{analysis['error']}*."
            })
            
        sharia_status = analysis["sharia"].get("status", "À VÉRIFIER")
        sharia_reason = analysis["sharia"].get("reason", "")
        tech = analysis["step_5_technical"]
        plan = analysis["step_6_trade_plan"]
        risk = analysis["step_7_risk_sizing"]
        confluence = analysis["step_8_confluence"]
        sym_char = "€" if tech.get("currency") == "EUR" else "$"
        
        response = f"### 📋 Rapport Protocolaire en 8 Étapes : **{ticker}** ({company_name})\n\n"
        response += f"- **Catégorie :** {analysis.get('category_icon', '📦')} {analysis.get('category')} | **Compte :** {analysis.get('account_type')}\n"
        response += f"- **Verdict de l'Agent** : `[{confluence['verdict']}]` | **Score de Confluence** : **{confluence['confluence_score']} / 10**\n\n"
        
        response += f"#### 1. Conformité Sharia (AAOIFI)\n"
        response += f"- Statut : **{sharia_status}** ({sharia_reason})\n"
        if "details" in analysis["sharia"] and isinstance(analysis["sharia"]["details"], dict):
            d = analysis["sharia"]["details"]
            response += f"- Ratios : Dette: {d.get('debt_ratio', 0)*100:.2f}% | Cash: {d.get('cash_ratio', 0)*100:.2f}% | Créances: {d.get('receivables_ratio', 0)*100:.2f}% (< 33%)\n\n"
            
        response += f"#### 2. Contexte Macroéconomique\n"
        response += f"- Régime Global : **{analysis['step_2_macro']['regime']}** ({analysis['step_2_macro']['action_rule']})\n\n"
        
        response += f"#### 3. Diagnostic de la Baisse (-3% à -8%)\n"
        response += f"- Baisse : **{analysis['step_3_drop']['drop_pct']:.2f}%** sur {analysis['step_3_drop']['lookback_days']}j. Nature : **{analysis['step_3_drop']['nature']}**\n\n"
        
        response += f"#### 4. Fondamentaux & Calendrier\n"
        response += f"- Santé : {analysis['step_4_fundamentals']['health_status']} ({analysis['step_4_fundamentals']['summary']})\n"
        response += f"- Blackout Résultats : {'🔴 Actif' if analysis['step_4_fundamentals']['earnings_blackout']['active'] else '🟢 Inactif (Fenêtre sécurisée)'}\n\n"
        
        response += f"#### 5. Analyse Technique & Flux\n"
        response += f"- Cours Actuel : **{tech['current_price']:.2f} {sym_char}** | Tendance SMA 200 : **{tech['trend_daily']}**\n"
        response += f"- Support Majeur : **{tech['support']:.2f} {sym_char}** | Résistance : **{tech['resistance']:.2f} {sym_char}**\n"
        response += f"- RSI (14) : **{tech['rsi']:.1f}** | Divergence : **{tech['rsi_divergence']['type']}**\n\n"
        
        response += f"#### 6. Plan de Trade Tactique\n"
        response += f"- 📥 **Zone d'Entrée** : {plan['entry_price']:.2f} {sym_char}\n"
        response += f"- 🎯 **Take Profit 1 (+1,0% à +1,5%)** : {plan['tp1_price']:.2f} {sym_char} (+{plan['tp1_pct']:.2f}%)\n"
        response += f"- 🎯 **Take Profit 2 (+2,0% à +2,5%)** : {plan['tp2_price']:.2f} {sym_char} (+{plan['tp2_pct']:.2f}%)\n"
        response += f"- 🛑 **Stop-Loss Invalidation** : {plan['stop_loss_price']:.2f} {sym_char} (-{plan['stop_distance_pct']:.2f}%)\n"
        response += f"- ⏱️ **Horizon de Détention** : {plan['holding_range']}\n\n"
        
        response += f"#### 7. Dimensionnement R-Max (Capital Réf: {risk['capital_reference']:,.0f} €)\n"
        response += f"- Allocation Nominale Suggérée : **{risk['suggested_nominal']:,.2f} €** ({risk['shares_count']} actions)\n"
        response += f"- Risque Monétaire Engagé : **{risk['actual_monetary_risk']:.2f} €** (R-Max max : {risk['r_max_amount']:.2f} € / {risk['r_max_pct']:.1f}%)\n"
        response += f"- Ratios R:R : TP1 = 1:{risk['risk_reward_tp1']:.2f} | TP2 = 1:{risk['risk_reward_tp2']:.2f}\n\n"
        
        response += f"#### 8. Verdict & Synthèse Décisionnelle\n"
        response += f"- **Avis** : `[{confluence['verdict']}]`\n"
        response += f"- **Thèse** : *{confluence['synthesis']}*\n"
        
        return jsonify({"response": response})
        
    if any(w in message_lower for w in ["bonjour", "salut", "hello", "hi"]):
        return jsonify({
            "response": "Bonjour ! Je suis votre **Macro & Sharia Mean Reversion Trading Assistant (v2.0)**.\n\n"
                        "Vous pouvez me demander :\n"
                        "1. **L'analyse protocolaire en 8 étapes** d'une action (ex: *'Analyse Sanofi (SAN.PA)'*, *'Screen LVMH'*)\n"
                        "2. **D'ajouter une action au Google Sheet** (*'Ajoute Sanofi (SAN.PA) à ma watchlist'*)\n"
                        "3. **De filtrer par éligibilité PEA ou Catégorie (Pharma, Tech, Luxe, etc.)**\n"
                        "4. **Le point Macroéconomique Top-Down** (*'Quel est le régime macro ?'*)\n"
                        "5. **Le dimensionnement de risque R-Max** pour votre portefeuille.\n\n"
                        "Que souhaitez-vous faire ?"
        })
        
    return jsonify({
        "response": f"J'ai bien reçu votre message : *\"{message}\"*.\n\nPour une analyse ou un ajout, veuillez préciser le nom de l'entreprise ou son ticker boursier (ex: `SAN.PA`, `AAPL`, `MSFT`, `MC.PA`, `AIR.PA`), ou tapez *'Ajoute [TICKER] à ma watchlist'*."
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
