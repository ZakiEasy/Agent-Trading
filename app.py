import os
import re
import math
import time
import logging
import threading
import concurrent.futures
from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from flask_cors import CORS
from authlib.integrations.flask_client import OAuth
import yfinance as yf
from datetime import datetime
import requests
from werkzeug.middleware.proxy_fix import ProxyFix

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

app = Flask(__name__, template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

from src.blueprints.paper_bp import paper_bp
from src.blueprints.ui_bp import ui_bp
from src.blueprints.watchlist_bp import watchlist_bp
from src.blueprints.portfolio_bp import portfolio_bp
from src.blueprints.trading212_bp import trading212_bp
from src.blueprints.journal_bp import journal_bp
from src.blueprints.scanner_bp import scanner_bp
from src.utils.api_utils import sanitize_for_json, safe_jsonify

app.register_blueprint(paper_bp)
app.register_blueprint(ui_bp)
app.register_blueprint(watchlist_bp)
app.register_blueprint(portfolio_bp)
app.register_blueprint(trading212_bp)
app.register_blueprint(journal_bp)
app.register_blueprint(scanner_bp)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "trading-agent-secure-key-2026")
CORS(app)

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@app.route("/login")
def login():
    if not os.getenv("GOOGLE_CLIENT_ID"):
        return (
            "Le système SSO n'est pas encore configuré (Clés manquantes dans .env)",
            500,
        )

    # Stocker l'URL de redirection demandée
    next_url = request.args.get("next")
    if next_url:
        session["next_url"] = next_url

    redirect_uri = url_for("authorize", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/login/google/authorized")
def authorize():
    if not os.getenv("GOOGLE_CLIENT_ID"):
        return "Le système SSO n'est pas configuré.", 500

    token = google.authorize_access_token()
    user_info = token.get("userinfo")

    if not user_info:
        # Authlib < 1.0 or depending on the setup, userinfo might need a separate fetch
        resp = google.get("userinfo")
        user_info = resp.json()

    email = user_info.get("email")

    # Check whitelist
    allowed_email = os.getenv("ALLOWED_EMAIL", "").strip()
    if allowed_email and email != allowed_email:
        return f"Accès refusé. L'email {email} n'est pas autorisé.", 403

    session["user"] = user_info

    # Rediriger vers l'URL d'origine si elle existe
    next_url = session.pop("next_url", "/")
    return redirect(next_url)








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
    calculate_monthly_rotation_by_stock,
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
    check_trading212_api_permissions,
)
from src.order_guardrails import guardrails_engine, STRATEGY_GRID_PROFILES
from src.trading212_execution_engine import execution_engine
from src.institutional_engine import (
    get_macro_sentiment_barometer,
    generate_8_step_protocol_analysis,
    compute_institutional_rmax_sizing,
    scan_watchlist_institutional,
)










@app.before_request
def require_login():
    # Allow static files and auth routes
    if request.endpoint in ["login", "authorize", "logout", "static"]:
        return

    # Allow preflight CORS requests
    if request.method == "OPTIONS":
        return

    # Check for valid App Key (for n8n/scripts)
    admin_key = os.getenv("ADMIN_API_KEY")
    if admin_key:
        req_key = request.headers.get("X-App-Key")
        if req_key == admin_key:
            return  # Autorisé

    # Check for session
    if "user" not in session:
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "error": "Authentification requise"}), 401
        return redirect(url_for("login", next=request.url))
















_PROTOCOL_FEEDBACK_CACHE = {"data": None, "ts": 0}






































# ==============================================================================
# --- V3. Routes Stratégie Institutionnelle Tactique (Confluence 3 Moteurs) ---
# ==============================================================================














_BACKTEST_RUN_CACHE = {}
_BACKTEST_CRISES_CACHE = {}












# ==============================================================================
# --- 10. RECHERCHE D'ACTIONS, SCREENER MULTI-FILTRES & BACKTEST 10 ANS ---
# ==============================================================================

try:
    from src.large_universe import LARGE_SCREENER_POOL
except ImportError:
    LARGE_SCREENER_POOL = []

EXPANDED_SCREENER_UNIVERSE = list(
    dict.fromkeys(
        DEFAULT_WATCHLIST
        + DEFAULT_MARKET_POOL
        + LARGE_SCREENER_POOL
        + [
            # US Large & Growth Caps (Nasdaq & S&P 500)
            "ADBE",
            "INTC",
            "CSCO",
            "QCOM",
            "TXN",
            "NFLX",
            "PYPL",
            "INTU",
            "NOW",
            "AMAT",
            "MU",
            "LRCX",
            "ADI",
            "KLAC",
            "SNPS",
            "CDNS",
            "PANW",
            "CRWD",
            "FTNT",
            "DDOG",
            "ZS",
            "NET",
            "PLTR",
            "ARM",
            "DELL",
            "SMCI",
            "UBER",
            "ABNB",
            "SHOP",
            "SE",
            "MELI",
            "PDD",
            "BABA",
            "JD",
            "BIDU",
            "TSM",
            "005930.KS",
            "COST",
            "AMD",
            # Europe / CAC 40 & DAX 40 (PEA & Euronext)
            "BNP.PA",
            "CAP.PA",
            "DSY.PA",
            "GLE.PA",
            "SAF.PA",
            "SGO.PA",
            "SU.PA",
            "DG.PA",
            "VIE.PA",
            "SAN.PA",
            "TTE.PA",
            "MC.PA",
            "OR.PA",
            "AIR.PA",
            "RMS.PA",
            "KER.PA",
            "EL.PA",
            "AI.PA",
            "GTT.PA",
            "ENGI.PA",
            "LR.PA",
            "STMPA.PA",
            "SAP.DE",
            "SIE.DE",
            "ALV.DE",
            "MBG.DE",
            "BMW.DE",
            "BAYN.DE",
            "MRK.DE",
            "VOW3.DE",
            "IS3E.DE",
            "IS3R.DE",
        ]
    )
)

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
        bt_res = run_single_ticker_10y_backtest(
            sym, strategy="v3_institutional", initial_capital=5000.0
        )
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
                "buy_hold_pct": float(comp.get("buy_and_hold_return_pct", 0.0) or 0.0),
            }
            _TICKER_10Y_STATS_CACHE[sym] = {"data": stats, "ts": now}
            _SCREENER_10Y_CACHE[f"{sym}_v3_institutional_5000.0"] = {
                "data": bt_res,
                "ts": now,
            }
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
        "buy_hold_pct": 0.0,
    }






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
                if any(
                    sym.endswith(sfx)
                    for sfx in [".F", ".MU", ".BE", ".DU", ".HM", ".MX", ".SA"]
                ):
                    score -= 30
                return score

            valid_quotes = [
                q
                for q in quotes
                if q.get("symbol")
                and q.get("quoteType", "").upper() in ["EQUITY", "ETF"]
                and len(q.get("symbol", "")) <= 12
            ]
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

    match_action = re.search(
        r"(?:action|ticker|cours|analyse|screen|conforme|ajoute|ajouter)\s+(?:de\s+|d\'\s+)?([A-Za-z0-9\.\-= ]{2,20})",
        message_clean,
        re.IGNORECASE,
    )
    if match_action:
        candidate = match_action.group(1).strip()
        ticker, name = lookup_ticker_by_name(candidate)
        if ticker:
            return ticker, name

    french_stop_words = {
        "je",
        "tu",
        "il",
        "elle",
        "nous",
        "vous",
        "ils",
        "elles",
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "du",
        "de",
        "d",
        "l",
        "j",
        "m",
        "t",
        "s",
        "se",
        "ce",
        "cet",
        "cette",
        "ces",
        "qui",
        "que",
        "quoi",
        "dont",
        "ou",
        "où",
        "quand",
        "comment",
        "pourquoi",
        "quel",
        "quelle",
        "quels",
        "quelles",
        "et",
        "mais",
        "donc",
        "or",
        "ni",
        "car",
        "si",
        "en",
        "dans",
        "par",
        "pour",
        "sur",
        "avec",
        "sans",
        "sous",
        "chez",
        "vers",
        "pendant",
        "durant",
        "ai",
        "as",
        "a",
        "avons",
        "avez",
        "ont",
        "suis",
        "es",
        "est",
        "sommes",
        "êtes",
        "sont",
        "peux",
        "peut",
        "pouvez",
        "veut",
        "veux",
        "voulez",
        "cherche",
        "trouve",
        "analyse",
        "non",
        "oui",
        "acheter",
        "vendre",
        "cours",
        "prix",
        "action",
        "actions",
        "bourse",
        "halal",
        "sharia",
        "conforme",
        "indicateur",
        "indicateurs",
        "support",
        "resistance",
        "vix",
        "rsi",
        "sma",
        "ema",
        "macd",
        "trading",
        "investir",
        "investissement",
        "portefeuille",
        "bon",
        "moment",
        "macro",
        "regime",
        "barometre",
        "dxy",
        "petrole",
        "yield",
        "courbe",
        "pea",
        "pharma",
    }

    words = re.findall(r"\b[A-Za-z\.\-=]{2,12}\b", message_clean)
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

    search_query = " ".join(
        [w for w in message_clean.split() if w.lower() not in french_stop_words]
    )
    if search_query:
        ticker, name = lookup_ticker_by_name(search_query)
        if ticker:
            return ticker, name

    return None, None




# ---------------------------------------------------------------------
# --- MODULE D'EXÉCUTION SEMI-AUTOMATIQUE TRADING 212 & GARDE-FOUS ---
# ---------------------------------------------------------------------






































# ---------------------------------------------------------------------
# GESTIONNAIRES D'ERREURS HTTP GLOBAUX (GARANTIE DE RÉPONSES JSON)
# ---------------------------------------------------------------------
@app.errorhandler(400)
def handle_400(e):
    return safe_jsonify(
        {
            "success": False,
            "error": f"Requête invalide: {getattr(e, 'description', str(e))}",
        },
        status_code=400,
    )


@app.errorhandler(404)
def handle_404(e):
    return safe_jsonify(
        {
            "success": False,
            "error": f"Ressource introuvable: {getattr(e, 'description', str(e))}",
        },
        status_code=404,
    )


@app.errorhandler(405)
def handle_405(e):
    return safe_jsonify(
        {
            "success": False,
            "error": f"Méthode HTTP non autorisée: {getattr(e, 'description', str(e))}",
        },
        status_code=405,
    )


@app.errorhandler(500)
def handle_500(e):
    logger.error(f"Internal 500 error: {e}", exc_info=True)
    return safe_jsonify(
        {
            "success": False,
            "error": f"Erreur interne du serveur: {getattr(e, 'description', str(e))}",
        },
        status_code=500,
    )


@app.errorhandler(Exception)
def handle_general_exception(e):
    logger.error(f"Unhandled general exception: {e}", exc_info=True)
    return safe_jsonify(
        {"success": False, "error": f"Erreur serveur inattendue: {str(e)}"},
        status_code=500,
    )








if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
