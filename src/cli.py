import sys
import os
import concurrent.futures
from datetime import datetime
from tabulate import tabulate
import yfinance as yf

from src.config import (
    DEFAULT_WATCHLIST,
    DEFAULT_MARKET_POOL,
    TARGET_TP1_DEFAULT,
    TARGET_TP2_DEFAULT,
    MIN_DROP_PCT,
    MAX_DROP_PCT,
    CAPITAL_REFERENCE_DEFAULT
)
from src.sharia_screen import screen_ticker
from src.macro_regime import get_macro_barometer
from src.market_data import (
    fetch_market_data,
    analyze_technical_setup,
    qualify_price_drop,
    check_earnings_blackout,
    check_fundamental_quality
)
from src.risk_manager import calculate_trade_sizing, calculate_confluence_score
from src.sheets_connector import read_watchlist_from_sheets, write_signals_to_sheets, add_ticker_to_sheets

def analyze_single_ticker_data(symbol, capital=CAPITAL_REFERENCE_DEFAULT, macro_barometer=None):
    """
    Fonction utilitaire d'analyse unitaire pour exécution séquentielle ou parallèle.
    """
    try:
        if macro_barometer is None:
            macro_barometer = get_macro_barometer()
            
        sharia_res = screen_ticker(symbol)
        ticker_obj, hist = fetch_market_data(symbol)
        if isinstance(hist, str) or hist is None or hist.empty:
            return None
            
        fund_q = check_fundamental_quality(ticker_obj, symbol=symbol)
        tech = analyze_technical_setup(hist)
        has_drop, drop_details = qualify_price_drop(hist)
        has_blackout, blackout_reason = check_earnings_blackout(ticker_obj)
        
        trade_plan = calculate_trade_sizing(
            capital_total=capital,
            entry_price=tech["current_price"],
            stop_loss_price=tech["support"] * 0.99,
            macro_regime=macro_barometer["regime"]
        )
        
        confluence = calculate_confluence_score(
            sharia_res=sharia_res,
            macro_barometer=macro_barometer,
            drop_details=drop_details,
            has_qualified_drop=has_drop,
            tech_setup=tech,
            has_blackout=has_blackout,
            trade_plan=trade_plan
        )
        
        return {
            "symbol": symbol,
            "category": f"{fund_q.get('category_icon', '📦')} {fund_q.get('category', 'Autres')}",
            "category_raw": fund_q.get("category", "Autres"),
            "account": "PEA" if fund_q.get("is_pea") else "CTO",
            "account_type": fund_q.get("account_type", "CTO (US)"),
            "sharia": sharia_res.get("status"),
            "price": f"{tech['current_price']:.2f}",
            "price_raw": tech["current_price"],
            "drop": f"{drop_details['drop_pct']:.2f}%",
            "drop_raw": drop_details["drop_pct"],
            "rsi": f"{tech['rsi']:.1f}",
            "div": "OUI 🔥" if tech["rsi_divergence"]["has_divergence"] else "-",
            "score": f"{confluence['confluence_score']}/10",
            "score_raw": confluence["confluence_score"],
            "verdict": confluence["verdict"],
            "support": tech["support"],
            "tp1_target": trade_plan["tp1_price"],
            "tp2_target": trade_plan["tp2_price"],
            "stop_loss": trade_plan["stop_loss_price"],
            "r_max_amount": trade_plan["r_max_amount"],
            "suggested_nominal": trade_plan["suggested_nominal"]
        }
    except Exception as e:
        return None

def run_analyze_ticker(ticker_symbol, capital=CAPITAL_REFERENCE_DEFAULT):
    """
    Exécute le protocole standardisé en 8 étapes pour un ticker spécifique (Section 7).
    """
    ticker_symbol = ticker_symbol.upper().strip()
    print(f"\n" + "="*70)
    print(f"📊 PROTOCOLE D'ANALYSE EN 8 ÉTAPES : {ticker_symbol}")
    print(f"="*70 + "\n")

    print("⏳ [1/8] Évaluation de la conformité Sharia (Normes AAOIFI)...")
    sharia_res = screen_ticker(ticker_symbol)
    
    print("⏳ [2/8] Analyse du contexte macroéconomique et du régime global...")
    macro_barometer = get_macro_barometer()
    
    print("⏳ [3/8] Récupération des cours et diagnostic du dip...")
    ticker_obj, hist_or_err = fetch_market_data(ticker_symbol)
    if isinstance(hist_or_err, str):
        print(f"❌ Erreur lors de la récupération des données : {hist_or_err}")
        return
        
    hist = hist_or_err
    tech_setup = analyze_technical_setup(hist)
    has_qualified_drop, drop_details = qualify_price_drop(hist)
    
    print("⏳ [4/8] Contrôle des fondamentaux et du calendrier corporatif...")
    fund_quality = check_fundamental_quality(ticker_obj, symbol=ticker_symbol)
    has_blackout, blackout_reason = check_earnings_blackout(ticker_obj)
    
    print("⏳ [5/8] Calcul du plan de trade tactique et du dimensionnement R-Max...")
    curr_price = tech_setup["current_price"]
    support = tech_setup["support"]
    invalidation = support * 0.99
    
    trade_plan = calculate_trade_sizing(
        capital_total=capital,
        entry_price=curr_price,
        stop_loss_price=invalidation,
        macro_regime=macro_barometer["regime"]
    )
    
    print("⏳ [6/8] Calcul du score de confluence et verdict décisionnel...")
    confluence = calculate_confluence_score(
        sharia_res=sharia_res,
        macro_barometer=macro_barometer,
        drop_details=drop_details,
        has_qualified_drop=has_qualified_drop,
        tech_setup=tech_setup,
        has_blackout=has_blackout,
        trade_plan=trade_plan
    )

    currency = tech_setup.get("currency", "USD")
    sym_char = "€" if currency == "EUR" else "$"

    print("\n" + "#"*70)
    print(f"📋 RAPPORT D'ANALYSE DÉTAILLÉ - {ticker_symbol}")
    print("#"*70 + "\n")

    # --- Étape 1 ---
    print("### 1. Étape 1 : Conformité Sharia (Normes AAOIFI)")
    status = sharia_res.get("status", "À VÉRIFIER")
    reason = sharia_res.get("reason", "")
    print(f"- **Activité / Catégorie :** {fund_quality.get('category_icon', '📦')} {fund_quality.get('category', 'N/A')} [{fund_quality.get('account_type', 'CTO')}] (Secteur: {fund_quality.get('sector', 'N/A')})")
    print(f"- **Business Screen :** {'Conforme (Activités illicites < 5%)' if status == 'CONFORME' else reason}")
    if "details" in sharia_res and isinstance(sharia_res["details"], dict):
        det = sharia_res["details"]
        print(f"- **Ratios Financiers :**")
        print(f"  * Dette Totale / Market Cap : {det.get('debt_ratio', 0)*100:.2f} % (Seuil < 33 %)")
        print(f"  * Trésorerie & Placements / Market Cap : {det.get('cash_ratio', 0)*100:.2f} % (Seuil < 33 %)")
        print(f"  * Créances Clients / Market Cap : {det.get('receivables_ratio', 0)*100:.2f} % (Seuil < 33 %)")
    print(f"- **Statut Sharia :** `[{status}]`\n")

    # --- Étape 2 ---
    print("### 2. Étape 2 : Contexte Macro & Sentiment de Marché")
    print(f"- **Régime Global :** {macro_barometer['regime']}")
    print(f"- **Action Recommandée :** {macro_barometer['action_rule']}")
    print(f"- **Baromètre Indicateurs :** VIX={macro_barometer['indicators'].get('VIX', {}).get('value', 'N/A')} | DXY={macro_barometer['indicators'].get('DXY', {}).get('value', 'N/A')} | Courbe Taux={macro_barometer['indicators'].get('YIELD_CURVE', {}).get('value', 'N/A')}")
    print(f"- **Impact sur le Titre :** {'Porteur / Neutre' if macro_barometer['allowed_to_trade'] else 'Vents contraires (Risk-Off)'}\n")

    # --- Étape 3 ---
    print("### 3. Étape 3 : Qualification de la Baisse Récente")
    print(f"- **Ampleur de la baisse :** {drop_details['drop_pct']:.2f} % sur {drop_details['lookback_days']} séance(s)")
    print(f"- **Cause identifiée :** {drop_details.get('cause_summary', 'Repli technique court terme')}")
    print(f"- **Nature du Dip :** `[{drop_details['nature']}]`\n")

    # --- Étape 4 ---
    print("### 4. Étape 4 : Fondamentaux & Calendrier des Risques")
    print(f"- **Solidité Fondamentale :** {fund_quality['health_status']} ({fund_quality['summary']})")
    print(f"- **Prochains Résultats (Earnings) :** {'🔴 ' + blackout_reason if has_blackout else '🟢 Aucune publication sous 10j'}\n")

    # --- Étape 5 ---
    print("### 5. Étape 5 : Analyse Technique & Dynamique des Flux")
    print(f"- **Tendance de Fond (Daily) :** {tech_setup['trend_daily']} (SMA 200: {tech_setup['sma_200']:.2f} {sym_char})")
    print(f"- **Niveau de Support Majeur :** {support:.2f} {sym_char} | **Résistance :** {tech_setup['resistance']:.2f} {sym_char}")
    div_info = tech_setup.get("rsi_divergence", {})
    print(f"- **Indicateur RSI (14) :** {tech_setup['rsi']:.2f} | **Divergence :** `[{div_info.get('type', 'AUCUNE')}]`")
    if div_info.get("has_divergence"):
        print(f"  * {div_info.get('description')}")
    print(f"- **Signaux de Flux :** QQE Buy={'OUI 🔥' if tech_setup['qqe_buy_signal'] else 'NON'} | Volume 20={'Confirmé (>MA20)' if tech_setup['volume_confirmed'] else 'Faible'}\n")

    # --- Étape 6 ---
    print("### 6. Étape 6 : Plan de Trade Tactique (Mean Reversion)")
    print(f"- **Zone d'Entrée Recommandée :** {curr_price:.2f} {sym_char}")
    print(f"- **Take Profit 1 (+1,0 % à +1,5 %) :** {trade_plan['tp1_price']:.2f} {sym_char} (+{trade_plan['tp1_pct']:.2f} %)")
    print(f"- **Take Profit 2 (+2,0 % à +2,5 %) :** {trade_plan['tp2_price']:.2f} {sym_char} (+{trade_plan['tp2_pct']:.2f} %)")
    print(f"- **Stop-Loss d'Invalidation :** {invalidation:.2f} {sym_char} (1% sous le support technique)")
    print(f"- **Distance au Stop :** -{trade_plan['stop_distance_pct']:.2f} %")
    print(f"- **Horizon de Détention Estimé :** {trade_plan['holding_range']}\n")

    # --- Étape 7 ---
    print("### 7. Étape 7 : Dimensionnement, Allocation & Risque (R-Max)")
    print(f"- **Capital de Référence :** {trade_plan['capital_reference']:,.2f} €")
    print(f"- **Taille de Position Suggérée :** {trade_plan['suggested_nominal']:,.2f} € ({trade_plan['shares_count']} actions)")
    print(f"- **Risque Monétaire Engagé (R) :** {trade_plan['actual_monetary_risk']:.2f} € (R-Max autorisé : {trade_plan['r_max_amount']:.2f} € / {trade_plan['r_max_pct']:.1f} %)")
    print(f"- **Ratio Rendement / Risque (R:R) :** TP1 = 1 : {trade_plan['risk_reward_tp1']:.2f} | TP2 = 1 : {trade_plan['risk_reward_tp2']:.2f}")
    print(f"- **Contrôle de Corrélation :** Maximum 2 positions simultanées autorisées dans le secteur [{fund_quality.get('sector', 'N/A')}]\n")

    # --- Étape 8 ---
    print("### 8. Étape 8 : Verdict Final & Score de Confluence")
    print(f"- **Score de Confluence Globale :** **{confluence['confluence_score']} / {confluence['score_max']}**")
    print(f"- **Avis Définitif :** `[{confluence['verdict']}]`")
    print(f"- **Synthèse Décisionnelle :** {confluence['synthesis']}")
    print("="*70 + "\n")

def run_add_ticker_to_watchlist(ticker_symbol):
    """
    Ajoute une action à la Watchlist Google Sheets et affiche sa fiche descriptive.
    """
    ticker_symbol = ticker_symbol.upper().strip()
    print(f"\n➕ Ajout de l'action {ticker_symbol} à la Watchlist...")
    
    try:
        t = yf.Ticker(ticker_symbol)
        info = t.info
        name = info.get("longName") or info.get("shortName") or ticker_symbol
        fund_q = check_fundamental_quality(t, info, symbol=ticker_symbol)
    except Exception as e:
        print(f"⚠️ Impossible de récupérer les informations de l'entreprise : {e}")
        name = ticker_symbol
        fund_q = {"category": "Autres", "is_pea": ".PA" in ticker_symbol, "account_type": "PEA" if ".PA" in ticker_symbol else "CTO"}

    sharia_res = screen_ticker(ticker_symbol)
    sharia_status = sharia_res.get("status", "À VÉRIFIER")

    success, msg = add_ticker_to_sheets(
        ticker_symbol=ticker_symbol,
        name=name,
        category=fund_q.get("category", ""),
        is_pea=fund_q.get("is_pea", False),
        sharia_status=sharia_status
    )
    
    if success:
        print(f"✅ {msg}")
        print(f"📌 Nom : {name} | Catégorie : {fund_q.get('category')} | Compte : {fund_q.get('account_type')} | Sharia : {sharia_status}")
        run_analyze_ticker(ticker_symbol)
    else:
        print(f"❌ {msg}")

def run_scan_watchlist():
    """
    Scanne la watchlist avec le moteur de confluence v2.0 en multi-threading et enregistre les signaux.
    """
    print("\n🚀 Démarrage du scan de la watchlist (Moteur v2.0 - Parallélisé)...")
    watchlist = read_watchlist_from_sheets()
    macro_barometer = get_macro_barometer()
    
    print(f"🌍 Régime Macro Global : {macro_barometer['regime']} (Sizing: {macro_barometer['sizing_multiplier']*100:.0f}%)")
    print(f"⏳ Analyse simultanée de {len(watchlist)} actions...")
    
    results = []
    signals_to_write = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(analyze_single_ticker_data, sym, CAPITAL_REFERENCE_DEFAULT, macro_barometer): sym for sym in watchlist}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                if "ACHETER" in res["verdict"]:
                    signals_to_write.append({
                        "date": now_str,
                        "symbol": res["symbol"],
                        "sharia_status": res["sharia"],
                        "category": res["category_raw"],
                        "account_type": res["account_type"],
                        "macro_regime": macro_barometer["regime"],
                        "current_price": res["price_raw"],
                        "drop_pct": res["drop_raw"],
                        "support": res["support"],
                        "tp1_target": res["tp1_target"],
                        "tp2_target": res["tp2_target"],
                        "stop_loss": res["stop_loss"],
                        "r_max_amount": res["r_max_amount"],
                        "suggested_nominal": res["suggested_nominal"],
                        "confluence_score": res["score_raw"],
                        "verdict": res["verdict"]
                    })

    # Tri par score de confluence décroissant
    results.sort(key=lambda x: x["score_raw"], reverse=True)

    headers = ["Ticker", "Catégorie", "Compte", "Sharia", "Cours", "Dip (%)", "RSI", "Divergence", "Score", "Verdict"]
    table_data = [[r["symbol"], r["category"], r["account"], r["sharia"], r["price"], r["drop"], r["rsi"], r["div"], r["score"], r["verdict"]] for r in results]
    print(f"\n📊 RÉSULTATS DU SCAN WATCHLIST ({len(results)} actions qualifiées) :")
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print()
    
    if signals_to_write:
        write_signals_to_sheets(signals_to_write)
        print(f"✅ {len(signals_to_write)} signal(aux) d'achat enregistré(s) dans Google Sheets.")
    else:
        print("ℹ️ Aucun nouveau signal d'achat qualifié détecté sur la watchlist.")

def run_scan_market():
    """
    Scanne le Market Pool élargi pour dénicher de nouvelles opportunités à forte confluence.
    """
    print("\n🚀 Démarrage du scan de marché élargi (Market Pool Large/Mid Caps - Parallélisé)...")
    macro_barometer = get_macro_barometer()
    results = []
    signals_to_write = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(analyze_single_ticker_data, sym, CAPITAL_REFERENCE_DEFAULT, macro_barometer): sym for sym in DEFAULT_MARKET_POOL}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res and (res["score_raw"] >= 4 or "ACHETER" in res["verdict"]):
                results.append(res)
                if "ACHETER" in res["verdict"]:
                    signals_to_write.append({
                        "date": now_str,
                        "symbol": res["symbol"],
                        "sharia_status": res["sharia"],
                        "category": res["category_raw"],
                        "account_type": res["account_type"],
                        "macro_regime": macro_barometer["regime"],
                        "current_price": res["price_raw"],
                        "drop_pct": res["drop_raw"],
                        "support": res["support"],
                        "tp1_target": res["tp1_target"],
                        "tp2_target": res["tp2_target"],
                        "stop_loss": res["stop_loss"],
                        "r_max_amount": res["r_max_amount"],
                        "suggested_nominal": res["suggested_nominal"],
                        "confluence_score": res["score_raw"],
                        "verdict": res["verdict"]
                    })

    results.sort(key=lambda x: x["score_raw"], reverse=True)

    if results:
        headers = ["Ticker", "Catégorie", "Compte", "Sharia", "Cours", "Dip (%)", "RSI", "Divergence", "Score", "Verdict"]
        table_data = [[r["symbol"], r["category"], r["account"], r["sharia"], r["price"], r["drop"], r["rsi"], r["div"], r["score"], r["verdict"]] for r in results]
        print(f"\n🔥 OPPORTUNITÉS QUALIFIÉES SUR LE MARCHÉ ÉLARGI ({len(results)} actions) :")
        print(tabulate(table_data, headers=headers, tablefmt="grid"))
        print()
        
        if signals_to_write:
            write_signals_to_sheets(signals_to_write)
            print(f"✅ {len(signals_to_write)} signal(aux) d'achat enregistré(s) dans Google Sheets.")
    else:
        print("\nℹ️ Aucune opportunité qualifiée détectée sur le marché élargi actuellement.")

def run_check_macro():
    """
    Affiche le tableau de bord du Baromètre Macroéconomique institutionnel (Section 2).
    """
    print("\n" + "="*70)
    print("🌍 BAROMÈTRE MACROÉCONOMIQUE TOP-DOWN & RÉGIME DE MARCHÉ")
    print("="*70 + "\n")
    
    b = get_macro_barometer(force_refresh=True)
    
    print(f"📌 RÉGIME GLOBAL ACTUEL : {b['regime']}")
    print(f"🎯 RÈGLE D'EXPOSITION   : {b['action_rule']}")
    print(f"💰 MULTIPLICATEUR SIZING: {b['sizing_multiplier']*100:.0f} % (R-Max: {b['r_max_pct']*100:.1f} %)")
    print(f"📝 SYNTHÈSE CONTEXTUELLE: {b['summary']}\n")
    
    table_indicators = []
    for k, v in b["indicators"].items():
        table_indicators.append([k, v.get("value", ""), v.get("status", ""), v.get("desc", "")])
        
    print("📊 INDICATEURS INSTITUTIONNELS SURVEILLÉS :")
    print(tabulate(table_indicators, headers=["Indicateur", "Valeur / Variation", "Statut Régime", "Mécanisme & Règle"], tablefmt="grid"))
    print()
    
    comm_data = [[k, v] for k, v in b["commodities"].items()]
    print("🛒 COMMODITIES & MATIÈRES PREMIÈRES :")
    print(tabulate(comm_data, headers=["Actif", "Cours Direct"], tablefmt="grid"))
    print()
    
    print("📢 RAPPEL DES GARDE-FOUS DE DISCIPLINE :")
    for r in b["rules"]:
        print(f"  • {r}")
    print("="*70 + "\n")
