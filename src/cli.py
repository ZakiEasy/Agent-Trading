import sys
import os
from datetime import datetime
from tabulate import tabulate
import yfinance as yf

from src.config import (
    DEFAULT_WATCHLIST,
    TARGET_REBOUND_MIN,
    TARGET_REBOUND_MAX,
    MIN_DROP_PCT,
    MAX_DROP_PCT
)
from src.sharia_screen import screen_ticker
from src.market_data import fetch_market_data, analyze_technical_setup, qualify_price_drop, check_earnings_blackout
from src.sheets_connector import read_watchlist_from_sheets, write_signals_to_sheets

def run_analyze_ticker(ticker_symbol):
    """
    Exécute le protocole en 8 étapes pour un ticker spécifique.
    """
    ticker_symbol = ticker_symbol.upper().strip()
    print(f"\n==================================================")
    print(f"🔍 ANALYSE DÉTAILLÉE : {ticker_symbol}")
    print(f"==================================================\n")

    # 1. Screening Sharia
    print("⏳ Étape 1 : Screening Sharia...")
    sharia_res = screen_ticker(ticker_symbol)
    
    # 2. Données de marché et qualification
    print("⏳ Étape 2 : Récupération des cours et qualification de la baisse...")
    ticker_obj, hist_or_err = fetch_market_data(ticker_symbol)
    if isinstance(hist_or_err, str):
        print(f"❌ Erreur : {hist_or_err}")
        return
        
    hist = hist_or_err
    tech_setup = analyze_technical_setup(hist)
    has_qualified_drop, drop_details = qualify_price_drop(hist)
    
    # 3. Événements micro/macro (Earnings blackout)
    print("⏳ Étape 3 : Vérification du calendrier corporatif (Earnings)...")
    has_blackout, blackout_reason = check_earnings_blackout(ticker_obj)
    
    # Construction du rapport en 8 étapes
    print(f"\n📋 RAPPORT DE SYNTHÈSE - {ticker_symbol}\n")
    
    # --- 1. Conformité Sharia ---
    print("### 1. Conformité Sharia")
    status = sharia_res.get("status", "À VÉRIFIER")
    reason = sharia_res.get("reason", "")
    print(f"- **Verdict** : {status}")
    print(f"- **Motif** : {reason}")
    if "details" in sharia_res:
        det = sharia_res["details"]
        if "debt_ratio" in det:
            print(f"  - Ratio de Dette : {det['debt_ratio']:.2%} (Seuil < 33%)")
            print(f"  - Ratio de Cash : {det['cash_ratio']:.2%} (Seuil < 33%)")
            print(f"  - Ratio de Créances : {det['receivables_ratio']:.2%} (Seuil < 33%)")
    print()

    # Si non conforme, on le signale clairement
    if status == "NON CONFORME":
        print("⚠️ [STOP] Le titre est NON CONFORME Sharia. Analyse interrompue.")
        return

    # --- 2. Qualification de la baisse ---
    print("### 2. Qualification de la baisse")
    drop_pct = drop_details["drop_pct"]
    lookback = drop_details["lookback_days"]
    print(f"- **Variation récente** : {drop_pct:.2f}% sur les {lookback} dernières sessions.")
    if has_qualified_drop:
        print(f"- **Nature** : CONJONCTURELLE (Baisse admissible de {MIN_DROP_PCT}% à {MAX_DROP_PCT}%)")
    else:
        print(f"- **Nature** : HORS CRITÈRES (Baisse hors de la fourchette de -3% à -8%)")
    print()

    # --- 3. Analyse des Fondamentaux & Événements ---
    print("### 3. Analyse des Fondamentaux & Événements")
    print(f"- **Blackout Résultats (10j)** : {'Actif 🔴' if has_blackout else 'Inactif 🟢'}")
    print(f"- **Détails calendrier** : {blackout_reason}")
    print()

    # --- 4. Analyse Technique & Niveaux Clés ---
    print("### 4. Analyse Technique & Niveaux Clés")
    curr_price = tech_setup["current_price"]
    rsi = tech_setup["rsi"]
    support = tech_setup["support"]
    print(f"- **Cours Actuel** : {curr_price:.2f}")
    print(f"- **RSI (14)** : {rsi:.2f} ({'Survendu 📉' if rsi < 30 else 'Neutre ⚖️'})")
    print(f"- **Support Technique** : {support:.2f}")
    print(f"- **Moyenne Mobile 20 (SMA)** : {tech_setup['sma_20']:.2f}")
    print(f"- **Moyenne Mobile 50 (SMA)** : {tech_setup['sma_50']:.2f}")
    print()

    # --- 5. Plan de Trade Précis ---
    print("### 5. Plan de Trade Précis")
    # Zone d'entrée : cours actuel
    target_min = curr_price * (1 + TARGET_REBOUND_MIN / 100)
    target_max = curr_price * (1 + TARGET_REBOUND_MAX / 100)
    
    # Invalidation juste en dessous du support
    invalidation = support * 0.99
    
    print(f"- **Zone d'entrée recommandée** : {curr_price:.2f}")
    print(f"- **Objectif de rebond (+1% à +2%)** : {target_min:.2f} à {target_max:.2f}")
    print(f"- **Niveau d'invalidation (Stop-Loss)** : {invalidation:.2f} (1% sous le support)")
    print(f"- **Durée estimée de détention** : ~10 jours")
    print()

    # --- 6. Allocation & Capital ---
    print("### 6. Allocation & Capital")
    print("- **Capital suggéré** : 20% à 25% max du capital disponible par position (ex: 600€ à 2500€ pour un capital de 3k-10k€).")
    print()

    # --- 7. Ratio Risque / Rendement ---
    print("### 7. Ratio Risque / Rendement")
    potential_gain = ((target_min - curr_price) / curr_price) * 100
    potential_loss = ((curr_price - invalidation) / curr_price) * 100
    risk_reward = potential_gain / potential_loss if potential_loss != 0 else 0
    print(f"- **Gain Potentiel** : +{potential_gain:.2f}% à +{((target_max - curr_price) / curr_price) * 100:.2f}%")
    print(f"- **Risque Potentiel** : -{potential_loss:.2f}%")
    print(f"- **Ratio Risque / Rendement** : 1 : {risk_reward:.2f}")
    print()

    # --- 8. Verdict Final ---
    print("### 8. Verdict Final")
    if has_blackout:
        verdict = "ÉVITER (Proximité des résultats)"
    elif not has_qualified_drop:
        verdict = "ATTENDRE REPLI (Baisse non qualifiée)"
    elif rsi > 70:
        verdict = "ÉVITER (Titre suracheté)"
    else:
        verdict = "ACHETER REBOND"
    print(f"- **Avis** : **{verdict}**")
    print(f"==================================================\n")

def run_scan_watchlist():
    """
    Scanne les actions de la watchlist (Google Sheets ou par défaut) et identifie les opportunités.
    """
    print("\n🚀 Démarrage du scan de la watchlist...")
    watchlist = read_watchlist_from_sheets()
    
    results = []
    signals_to_write = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for symbol in watchlist:
        try:
            sharia_res = screen_ticker(symbol)
            if sharia_res.get("status") == "NON CONFORME":
                results.append({
                    "symbol": symbol,
                    "sharia": "NON CONFORME",
                    "price": "-",
                    "drop": "-",
                    "rsi": "-",
                    "verdict": "Exclu (Sharia)"
                })
                continue
                
            ticker_obj, hist = fetch_market_data(symbol)
            if hist.empty:
                continue
                
            tech = analyze_technical_setup(hist)
            has_drop, drop_details = qualify_price_drop(hist)
            has_blackout, _ = check_earnings_blackout(ticker_obj)
            
            # Établir le verdict
            if has_blackout:
                verdict = "Blackout Earnings"
            elif has_drop:
                verdict = "ACHETER REBOND"
            else:
                verdict = "Neutre"
                
            results.append({
                "symbol": symbol,
                "sharia": sharia_res.get("status"),
                "price": f"{tech['current_price']:.2f}",
                "drop": f"{drop_details['drop_pct']:.2f}%",
                "rsi": f"{tech['rsi']:.2f}",
                "verdict": verdict
            })
            
            # Si opportunité d'achat conforme détectée
            if verdict == "ACHETER REBOND":
                signals_to_write.append({
                    "date": now_str,
                    "symbol": symbol,
                    "sharia_status": sharia_res.get("status"),
                    "current_price": tech["current_price"],
                    "drop_pct": drop_details["drop_pct"],
                    "support": tech["support"],
                    "target_exit": tech["current_price"] * 1.015, # Rebond moyen +1.5%
                    "rsi": tech["rsi"],
                    "verdict": verdict
                })
        except Exception as e:
            print(f"⚠️ Erreur lors de l'analyse du ticker {symbol} : {e}")

    # Affichage CLI
    headers = ["Ticker", "Sharia", "Cours", "Variation", "RSI", "Verdict"]
    table_data = [[r["symbol"], r["sharia"], r["price"], r["drop"], r["rsi"], r["verdict"]] for r in results]
    print("\n📊 RÉSULTATS DU SCAN DE LA WATCHLIST :")
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print()
    
    # Écriture dans Google Sheets si opportunités trouvées
    if signals_to_write:
        write_signals_to_sheets(signals_to_write)
    else:
        print("ℹ️ Aucune nouvelle opportunité d'achat qualifiée détectée aujourd'hui.")

def run_scan_market():
    """
    Scanne un groupe d'actions majeures hors watchlist pour dénicher des opportunités.
    """
    # Liste d'actions Large Cap US & FR de référence
    MARKET_POOL = [
        # US Tech / Large Caps
        "MSFT", "AAPL", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "AMD",
        # CAC 40 / FR Large Caps
        "MC.PA", "OR.PA", "AIR.PA", "RMS.PA", "KER.PA", "EL.PA", "SAN.PA", "TTE.PA"
    ]
    
    print("\n🚀 Démarrage du scan de marché élargi (Market Pool)...")
    results = []
    signals_to_write = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for symbol in MARKET_POOL:
        try:
            # Screening Sharia
            sharia_res = screen_ticker(symbol)
            if sharia_res.get("status") == "NON CONFORME":
                continue
                
            ticker_obj, hist = fetch_market_data(symbol)
            if hist.empty:
                continue
                
            has_drop, drop_details = qualify_price_drop(hist)
            if not has_drop:
                continue # Ne conserver que les opportunités
                
            tech = analyze_technical_setup(hist)
            has_blackout, _ = check_earnings_blackout(ticker_obj)
            
            verdict = "Blackout Earnings" if has_blackout else "ACHETER REBOND"
            
            results.append({
                "symbol": symbol,
                "sharia": sharia_res.get("status"),
                "price": f"{tech['current_price']:.2f}",
                "drop": f"{drop_details['drop_pct']:.2f}%",
                "rsi": f"{tech['rsi']:.2f}",
                "verdict": verdict
            })
            
            if verdict == "ACHETER REBOND":
                signals_to_write.append({
                    "date": now_str,
                    "symbol": symbol,
                    "sharia_status": sharia_res.get("status"),
                    "current_price": tech["current_price"],
                    "drop_pct": drop_details["drop_pct"],
                    "support": tech["support"],
                    "target_exit": tech["current_price"] * 1.015,
                    "rsi": tech["rsi"],
                    "verdict": verdict
                })
        except Exception as e:
            pass

    if results:
        headers = ["Ticker", "Sharia", "Cours", "Variation", "RSI", "Verdict"]
        table_data = [[r["symbol"], r["sharia"], r["price"], r["drop"], r["rsi"], r["verdict"]] for r in results]
        print("\n🔥 NOUVELLES OPPORTUNITÉS DE MARCHÉ DÉTECTÉES :")
        print(tabulate(table_data, headers=headers, tablefmt="grid"))
        print()
        
        if signals_to_write:
            write_signals_to_sheets(signals_to_write)
    else:
        print("\nℹ️ Aucune opportunité qualifiée détectée sur le marché élargi.")
        print()

def run_check_macro():
    """
    Affiche un point sur les indicateurs macro (central banks, inflation, commodities).
    """
    print("\n==================================================")
    print("📅 CALENDRIER MACROÉCONOMIQUE & COMMODITIES")
    print("==================================================\n")
    print("⏳ Récupération des cours des commodités en direct...")
    
    commodities = {"Pétrole Brent": "BZ=F", "Pétrole WTI": "CL=F", "Or": "GC=F"}
    comm_data = []
    
    for name, ticker in commodities.items():
        try:
            t = yf.Ticker(ticker)
            price = t.history(period="1d")['Close'].values[-1]
            comm_data.append([name, f"{price:.2f} $"])
        except Exception as e:
            comm_data.append([name, "N/A"])
            
    print("\n🛒 COMMODITIES :")
    print(tabulate(comm_data, headers=["Actif", "Cours"], tablefmt="grid"))
    print("\n📢 RAPPEL DES GARDE-FOUS MACRO :")
    print("- Règle du Filtre Macro : Pas d'ouverture de position si CPI/Inflation, FED/BCE, ou NFP dans les 24-48h.")
    print("- Consulter le calendrier économique en ligne pour les dates exactes de la semaine courante.")
    print("==================================================\n")
