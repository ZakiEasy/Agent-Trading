import os
import re
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
    categorize_ticker
)
from src.risk_manager import calculate_trade_sizing, calculate_confluence_score
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

analysis_cache = {}

def get_detailed_analysis(ticker_symbol, capital=CAPITAL_REFERENCE_DEFAULT):
    """
    Exécute le protocole complet en 8 étapes pour un ticker spécifique (v2.0).
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
        
        # 4. Fondamentaux & Calendrier des Risques
        fund_quality = check_fundamental_quality(ticker_obj, symbol=ticker_symbol)
        has_blackout, blackout_reason = check_earnings_blackout(ticker_obj)
        
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
            trade_plan=trade_plan
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
                "indicators": macro_barometer["indicators"]
            },
            "step_3_drop": drop_details,
            "step_4_fundamentals": {
                "health_status": fund_quality["health_status"],
                "market_cap": fund_quality["market_cap"],
                "is_large_cap": fund_quality["is_large_cap"],
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
                "risk_reward_tp1": trade_plan["risk_reward_tp1"],
                "risk_reward_tp2": trade_plan["risk_reward_tp2"]
            },
            "step_8_confluence": confluence,
            
            "sharia": sharia_res,
            "technical": tech_setup,
            "drop": drop_details,
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
                "r_max_amount": trade_plan["r_max_amount"]
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
    watchlist = read_watchlist_from_sheets()
    return jsonify({"watchlist": watchlist})

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

@app.route("/api/scan/watchlist")
def scan_watchlist():
    watchlist = read_watchlist_from_sheets()
    results = []
    signals_to_write = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Exécution parallèle rapide (8 threads)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        analyses = list(executor.map(get_detailed_analysis, watchlist))
        
    for analysis in analyses:
        if not analysis or "error" in analysis:
            continue
            
        symbol = analysis.get("symbol")
        results.append({
            "symbol": symbol,
            "name": analysis.get("company_name", symbol),
            "category": analysis.get("category", "Autres"),
            "category_icon": analysis.get("category_icon", "📦"),
            "is_pea": analysis.get("is_pea", False),
            "account_type": analysis.get("account_type", "CTO (US)"),
            "sharia": analysis["sharia"].get("status"),
            "price": analysis["technical"]["current_price"],
            "drop": analysis["drop"]["drop_pct"],
            "rsi": analysis["technical"]["rsi"],
            "rsi_divergence": analysis["technical"]["rsi_divergence"]["type"],
            "confluence_score": analysis["confluence_score"],
            "verdict": analysis["verdict"],
            "currency": analysis["technical"].get("currency", "USD")
        })
        
        if "ACHETER" in analysis["verdict"]:
            signals_to_write.append({
                "date": now_str,
                "symbol": symbol,
                "sharia_status": analysis["sharia"].get("status"),
                "category": analysis.get("category", "Autres"),
                "account_type": analysis.get("account_type", "CTO (US)"),
                "macro_regime": analysis["step_2_macro"]["regime"],
                "current_price": analysis["technical"]["current_price"],
                "drop_pct": analysis["drop"]["drop_pct"],
                "support": analysis["technical"]["support"],
                "tp1_target": analysis["trade_plan"]["target_min"],
                "tp2_target": analysis["trade_plan"]["target_max"],
                "stop_loss": analysis["trade_plan"]["invalidation"],
                "r_max_amount": analysis["step_7_risk_sizing"]["r_max_amount"],
                "suggested_nominal": analysis["step_7_risk_sizing"]["suggested_nominal"],
                "confluence_score": analysis["confluence_score"],
                "verdict": analysis["verdict"]
            })
            
    if signals_to_write:
        try:
            write_signals_to_sheets(signals_to_write)
        except Exception as e:
            print(f"Erreur écriture signaux: {e}")
        
    return jsonify({"results": results, "signals_sent": len(signals_to_write)})

@app.route("/api/scan/market")
def scan_market():
    results = []
    signals_to_write = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        analyses = list(executor.map(get_detailed_analysis, DEFAULT_MARKET_POOL))
    
    for analysis in analyses:
        if not analysis or "error" in analysis:
            continue
            
        symbol = analysis.get("symbol")
        results.append({
            "symbol": symbol,
            "name": analysis.get("company_name", symbol),
            "category": analysis.get("category", "Autres"),
            "category_icon": analysis.get("category_icon", "📦"),
            "is_pea": analysis.get("is_pea", False),
            "account_type": analysis.get("account_type", "CTO (US)"),
            "sharia": analysis["sharia"].get("status"),
            "price": analysis["technical"]["current_price"],
            "drop": analysis["drop"]["drop_pct"],
            "rsi": analysis["technical"]["rsi"],
            "rsi_divergence": analysis["technical"]["rsi_divergence"]["type"],
            "confluence_score": analysis["confluence_score"],
            "verdict": analysis["verdict"],
            "currency": analysis["technical"].get("currency", "USD")
        })
        
        if "ACHETER" in analysis["verdict"]:
            signals_to_write.append({
                "date": now_str,
                "symbol": symbol,
                "sharia_status": analysis["sharia"].get("status"),
                "category": analysis.get("category", "Autres"),
                "account_type": analysis.get("account_type", "CTO (US)"),
                "macro_regime": analysis["step_2_macro"]["regime"],
                "current_price": analysis["technical"]["current_price"],
                "drop_pct": analysis["drop"]["drop_pct"],
                "support": analysis["technical"]["support"],
                "tp1_target": analysis["trade_plan"]["target_min"],
                "tp2_target": analysis["trade_plan"]["target_max"],
                "stop_loss": analysis["trade_plan"]["invalidation"],
                "r_max_amount": analysis["step_7_risk_sizing"]["r_max_amount"],
                "suggested_nominal": analysis["step_7_risk_sizing"]["suggested_nominal"],
                "confluence_score": analysis["confluence_score"],
                "verdict": analysis["verdict"]
            })
            
    if signals_to_write:
        try:
            write_signals_to_sheets(signals_to_write)
        except Exception as e:
            print(f"Erreur écriture signaux: {e}")
        
    return jsonify({"results": results})

@app.route("/api/analyze/<ticker>")
def analyze_ticker_endpoint(ticker):
    capital = request.args.get("capital", default=CAPITAL_REFERENCE_DEFAULT, type=float)
    res = get_detailed_analysis(ticker, capital=capital)
    if "error" in res:
        return jsonify({"success": False, "error": res["error"]}), 400
    return jsonify({"success": True, "data": res})

@app.route("/api/macro")
def get_macro_endpoint():
    force = request.args.get("refresh", default=False, type=bool)
    barometer = get_macro_barometer(force_refresh=force)
    return jsonify(barometer)

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

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            quotes = data.get("quotes", [])
            for q in quotes:
                qtype = q.get("quoteType", "").upper()
                symbol = q.get("symbol", "")
                name = q.get("shortname") or q.get("longname") or symbol
                if symbol and qtype in ["EQUITY", "ETF"]:
                    if len(symbol) <= 10:
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
