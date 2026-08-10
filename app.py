import os
import re
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import yfinance as yf
from datetime import datetime

from src.sharia_screen import screen_ticker
from src.market_data import fetch_market_data, analyze_technical_setup, qualify_price_drop, check_earnings_blackout
from src.sheets_connector import read_watchlist_from_sheets, write_signals_to_sheets
from src.config import (
    DEFAULT_WATCHLIST,
    MIN_DROP_PCT,
    MAX_DROP_PCT,
    TARGET_REBOUND_MIN,
    TARGET_REBOUND_MAX
)

app = Flask(__name__, template_folder="templates")
CORS(app)

# Cache simple en mémoire pour stocker les dernières analyses
analysis_cache = {}

def get_detailed_analysis(ticker_symbol):
    ticker_symbol = ticker_symbol.upper().strip()
    
    # 1. Conformité Sharia
    sharia_res = screen_ticker(ticker_symbol)
    
    # 2. Données de marché et technique
    ticker_obj, hist_or_err = fetch_market_data(ticker_symbol)
    if isinstance(hist_or_err, str):
        return {"error": hist_or_err}
        
    hist = hist_or_err
    tech_setup = analyze_technical_setup(hist)
    has_qualified_drop, drop_details = qualify_price_drop(hist)
    has_blackout, blackout_reason = check_earnings_blackout(ticker_obj)
    
    # Nom de l'entreprise
    company_name = ticker_symbol
    try:
        info = ticker_obj.info
        company_name = info.get("longName") or info.get("shortName") or ticker_symbol
    except:
        pass
        
    curr_price = tech_setup["current_price"]
    support = tech_setup["support"]
    target_min = curr_price * (1 + TARGET_REBOUND_MIN / 100)
    target_max = curr_price * (1 + TARGET_REBOUND_MAX / 100)
    invalidation = support * 0.99
    
    potential_gain = ((target_min - curr_price) / curr_price) * 100
    potential_loss = ((curr_price - invalidation) / curr_price) * 100
    risk_reward = potential_gain / potential_loss if potential_loss != 0 else 0
    
    # Verdict final
    is_above_sma200 = curr_price >= tech_setup["sma_200"]
    mrc_oversold = tech_setup["mrc_oversold"]
    qqe_buy = tech_setup["qqe_buy_signal"]
    volume_confirmed = tech_setup["volume_confirmed"]
    
    if sharia_res.get("status") == "NON CONFORME":
        verdict = "EXCLU (Non conforme Sharia)"
    elif has_blackout:
        verdict = "ÉVITER (Proximité des résultats)"
    elif not is_above_sma200:
        verdict = "ÉVITER (Tendance baissière - sous SMA 200)"
    elif not has_qualified_drop:
        verdict = "ATTENDRE REPLI (Baisse non qualifiée)"
    elif tech_setup["rsi"] > 70:
        verdict = "ÉVITER (Titre suracheté)"
    elif not mrc_oversold:
        verdict = "ATTENDRE (Pas encore étiré sous MRC)"
    elif not qqe_buy:
        verdict = "ATTENDRE TRIGGER (Pas de signal QQE)"
    elif not volume_confirmed:
        verdict = "ATTENDRE VOLUME (Volume faible)"
    else:
        verdict = "ACHETER REBOND"
        
    analysis = {
        "symbol": ticker_symbol,
        "company_name": company_name,
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
            "target_min": target_min,
            "target_max": target_max,
            "invalidation": invalidation,
            "potential_gain_min": potential_gain,
            "potential_gain_max": ((target_max - curr_price) / curr_price) * 100,
            "potential_loss": potential_loss,
            "risk_reward": risk_reward
        },
        "verdict": verdict,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Mettre en cache
    analysis_cache[ticker_symbol] = analysis
    return analysis

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/watchlist")
def get_watchlist():
    watchlist = read_watchlist_from_sheets()
    return jsonify({"watchlist": watchlist})

@app.route("/api/scan/watchlist")
def scan_watchlist():
    watchlist = read_watchlist_from_sheets()
    results = []
    signals_to_write = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for symbol in watchlist:
        try:
            analysis = get_detailed_analysis(symbol)
            if "error" in analysis:
                continue
                
            results.append({
                "symbol": symbol,
                "name": analysis.get("company_name", symbol),
                "sharia": analysis["sharia"].get("status"),
                "price": analysis["technical"]["current_price"],
                "drop": analysis["drop"]["drop_pct"],
                "rsi": analysis["technical"]["rsi"],
                "verdict": analysis["verdict"],
                "currency": analysis["technical"].get("currency", "USD")
            })
            
            if analysis["verdict"] == "ACHETER REBOND":
                signals_to_write.append({
                    "date": now_str,
                    "symbol": symbol,
                    "sharia_status": analysis["sharia"].get("status"),
                    "current_price": analysis["technical"]["current_price"],
                    "drop_pct": analysis["drop"]["drop_pct"],
                    "support": analysis["technical"]["support"],
                    "target_exit": analysis["technical"]["current_price"] * 1.015,
                    "rsi": analysis["technical"]["rsi"],
                    "verdict": analysis["verdict"]
                })
        except Exception as e:
            print(f"Erreur sur {symbol}: {e}")
            
    if signals_to_write:
        write_signals_to_sheets(signals_to_write)
        
    return jsonify({"results": results, "signals_sent": len(signals_to_write)})

@app.route("/api/scan/market")
def scan_market():
    MARKET_POOL = [
        "MSFT", "AAPL", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "AMD",
        "MC.PA", "OR.PA", "AIR.PA", "RMS.PA", "KER.PA", "EL.PA", "SAN.PA", "TTE.PA"
    ]
    results = []
    
    for symbol in MARKET_POOL:
        try:
            analysis = get_detailed_analysis(symbol)
            if "error" in analysis:
                continue
                
            # Ne renvoyer que les opportunités intéressantes ou notables
            results.append({
                "symbol": symbol,
                "name": analysis.get("company_name", symbol),
                "sharia": analysis["sharia"].get("status"),
                "price": analysis["technical"]["current_price"],
                "drop": analysis["drop"]["drop_pct"],
                "rsi": analysis["technical"]["rsi"],
                "verdict": analysis["verdict"],
                "currency": analysis["technical"].get("currency", "USD")
            })
        except Exception as e:
            print(f"Erreur sur {symbol}: {e}")
            
    return jsonify({"results": results})

@app.route("/api/analyze/<ticker>")
def analyze_ticker(ticker):
    res = get_detailed_analysis(ticker)
    if "error" in res:
        return jsonify({"success": False, "error": res["error"]}), 400
    return jsonify({"success": True, "data": res})

@app.route("/api/macro")
def get_macro():
    commodities = {"Pétrole Brent": "BZ=F", "Pétrole WTI": "CL=F", "Or": "GC=F"}
    comm_data = {}
    for name, ticker in commodities.items():
        try:
            t = yf.Ticker(ticker)
            price = t.history(period="1d")['Close'].values[-1]
            comm_data[name] = f"{price:.2f} $"
        except:
            comm_data[name] = "N/A"
            
    return jsonify({
        "commodities": comm_data,
        "rules": [
            "Pas d'ouverture de position si CPI, réunion FED/BCE, ou NFP sous 24-48h.",
            "Toujours vérifier le calendrier économique réel avant de lancer un ordre."
        ]
    })

import requests

def lookup_ticker_by_name(query):
    query = query.strip()
    if not query:
        return None, None
        
    # Si la requête ressemble à un ticker déjà valide (ex: STX, AAPL, MC.PA)
    if query.isupper() and len(query) <= 6 and not query.isdigit():
        try:
            t = yf.Ticker(query)
            if not t.history(period="1d").empty:
                return query, query
        except:
            pass

    # Utiliser l'API Yahoo Finance Autocomplete
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

    # 1. Expressions de type "action XXX", "ticker YYY"
    match_action = re.search(r'(?:action|ticker|cours|analyse|screen|conforme)\s+(?:de\s+|d\'\s+)?([A-Za-z0-9\.\-= ]{2,20})', message_clean, re.IGNORECASE)
    if match_action:
        candidate = match_action.group(1).strip()
        ticker, name = lookup_ticker_by_name(candidate)
        if ticker:
            return ticker, name

    # 2. Liste étendue des mots vides en français et anglais
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
        "quelle", "serait", "acheté", "acheter", "vendre", "vend", "achète", "combien"
    }

    # Séparer en mots
    words = re.findall(r'\b[A-Za-z\.\-=]{2,12}\b', message_clean)
    candidates = [w for w in words if w.lower() not in french_stop_words]

    # 3. Essayer en premier les mots en majuscule ou contenant un point (ticker potentiel)
    for cand in candidates:
        if cand.isupper() or "." in cand:
            ticker, name = lookup_ticker_by_name(cand)
            if ticker:
                return ticker, name

    # 4. Essayer les autres mots de la phrase
    for cand in candidates:
        ticker, name = lookup_ticker_by_name(cand)
        if ticker:
            return ticker, name

    # 5. Recherche globale sur le message entier nettoyé
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
    
    # Extraction intelligente du ticker / entreprise
    ticker, company_name = find_ticker_in_message(message)
    
    if ticker:
        # Effectuer ou récupérer l'analyse
        analysis = get_detailed_analysis(ticker)
        if "error" in analysis:
            return jsonify({
                "response": f"J'ai détecté une demande pour **{company_name or ticker}**, mais j'ai rencontré l'erreur suivante lors de l'analyse : *{analysis['error']}*. S'agit-il d'un ticker ou d'une entreprise valide ?"
            })
            
        sharia_status = analysis["sharia"].get("status", "À VÉRIFIER")
        sharia_reason = analysis["sharia"].get("reason", "")
        current_price = analysis["technical"]["current_price"]
        rsi = analysis["technical"]["rsi"]
        support = analysis["technical"]["support"]
        resistance = analysis["technical"]["resistance"] or (current_price * 1.03)
        verdict = analysis["verdict"]
        
        # Si la question porte spécifiquement sur le screening Sharia
        if any(w in message_lower for w in ["sharia", "conforme", "halal", "islam", "religion"]):
            response = f"### 🕌 Conformité Sharia pour **{ticker}** ({company_name})\n\n"
            response += f"- **Verdict** : **{sharia_status}**\n"
            response += f"- **Motif** : {sharia_reason}\n\n"
            if "details" in analysis["sharia"]:
                d = analysis["sharia"]["details"]
                response += "#### Ratios financiers calculés (Seuil réglementaire < 33%) :\n"
                response += f"- 💳 **Ratio de Dette** : {d.get('debt_ratio', 0)*100:.2f}%\n"
                response += f"- 💵 **Ratio de Trésorerie** : {d.get('cash_ratio', 0)*100:.2f}%\n"
                response += f"- 📝 **Ratio de Créances** : {d.get('receivables_ratio', 0)*100:.2f}%\n"
            return jsonify({"response": response})
            
        # Si la question porte sur l'analyse technique ou les indicateurs
        if any(w in message_lower for w in ["technique", "rsi", "support", "sma", "cours", "indicateur", "volatil", "volatilité", "vix", "resistance", "résistance"]):
            response = f"### 📊 Analyse Technique pour **{ticker}** ({company_name})\n\n"
            response += f"- **Cours Actuel** : {current_price:.2f} $\n"
            response += f"- **RSI (14)** : {rsi:.2f} ({'Survendu 📉' if rsi < 30 else 'Suracheté 📈' if rsi > 70 else 'Neutre ⚖️'})\n"
            response += f"- **Support Technique** : {support:.2f} $ | **Résistance** : {resistance:.2f} $\n"
            response += f"- **Volatilité (Action)** : {analysis['technical']['historical_volatility']:.2f}% (annuelle)\n"
            response += f"- **VIX Marché** : {analysis['technical']['vix']:.2f}\n"
            response += f"- **Moyenne Mobile 20 (SMA)** : {analysis['technical']['sma_20']:.2f} $\n"
            response += f"- **Moyenne Mobile 50 (SMA)** : {analysis['technical']['sma_50']:.2f} $\n"
            return jsonify({"response": response})

        # Par défaut, donner le rapport complet
        response = f"### 📋 Rapport d'analyse : **{ticker}** ({company_name})\n\n"
        response += f"**Verdict** : **{verdict}**\n\n"
        response += f"1. **Conformité Sharia** : {sharia_status} ({sharia_reason})\n"
        response += f"2. **Baisse récente** : {analysis['drop']['drop_pct']:.2f}% sur {analysis['drop']['lookback_days']} jours. (Nature: {analysis['drop']['nature']})\n"
        response += f"3. **RSI (14)** : {rsi:.2f} | **Support** : {support:.2f} $ | **Résistance** : {resistance:.2f} $\n"
        response += f"4. **Plan de Trade** :\n"
        response += f"   - 📥 **Entrée** : {current_price:.2f} $\n"
        response += f"   - 🎯 **Objectif** : {analysis['trade_plan']['target_min']:.2f} à {analysis['trade_plan']['target_max']:.2f} $ (+{analysis['trade_plan']['potential_gain_min']:.2f}% à +{analysis['trade_plan']['potential_gain_max']:.2f}%)\n"
        response += f"   - 🛑 **Invalidation (Stop-Loss)** : {analysis['trade_plan']['invalidation']:.2f} $\n"
        response += f"   - ⚖️ **Ratio R/R** : 1 : {analysis['trade_plan']['risk_reward']:.2f}\n"
        
        return jsonify({"response": response})
        
    # Si aucun ticker n'est mentionné
    if any(w in message_lower for w in ["bonjour", "salut", "hello", "hi"]):
        return jsonify({
            "response": "Bonjour ! Je suis votre assistant de Swing Trading. Vous pouvez me demander :\n"
                        "1. L'analyse d'un ticker particulier (ex: *'Analyse AAPL'* ou *'Screen MSFT'*)\n"
                        "2. De vérifier la conformité d'une action (ex: *'Est-ce que LVMH (MC.PA) est Sharia-conforme ?'*)\n"
                        "3. D'afficher l'analyse technique d'un titre (*'Quels sont les indicateurs de NVDA ?'*)\n"
                        "Comment puis-je vous aider aujourd'hui ?"
        })
        
    return jsonify({
        "response": f"J'ai bien reçu votre message : *\"{message}\"*.\n\nPour que je puisse vous aider au mieux, veuillez préciser le nom de l'entreprise ou son ticker d'action (ex: `Seagate`, `AAPL`, `MSFT`, `LVMH`)."
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
