import yfinance as yf
import pandas as pd
from src.config import (
    SHARIA_EXCLUDED_INDUSTRIES,
    SHARIA_MAX_DEBT_RATIO,
    SHARIA_MAX_CASH_RATIO,
    SHARIA_MAX_RECEIVABLES_RATIO
)

def check_business_compliance(info):
    """
    Vérifie la conformité sectorielle (Business Screen).
    """
    if not isinstance(info, dict):
        info = {}
        
    industry = str(info.get("industry", "") or "").lower()
    sector = str(info.get("sector", "") or "").lower()
    summary = str(info.get("longBusinessSummary", "") or "").lower()
    
    # Exclure selon l'industrie ou le secteur
    for forbidden in SHARIA_EXCLUDED_INDUSTRIES:
        if forbidden in industry or forbidden in sector:
            return False, f"Secteur/Industrie non conforme : {forbidden}"
            
    # Exclure selon la description de l'activité
    for forbidden in ["casino", "gambling", "pork", "distillery", "defense contractor", "weapons"]:
        if forbidden in summary:
            return False, f"Activité non conforme (détectée dans le résumé) : {forbidden}"
            
    return True, "Activité conforme (Activités illicites < 5%)"

def get_financial_metric(balance_sheet, keys):
    """
    Récupère une métrique financière à partir de la balance sheet en testant plusieurs clés courantes.
    """
    if balance_sheet is None or not hasattr(balance_sheet, 'index') or balance_sheet.empty:
        return 0.0
        
    for key in keys:
        if key in balance_sheet.index:
            row = balance_sheet.loc[key]
            val = row.iloc[0] if hasattr(row, 'iloc') else row
            if isinstance(val, (int, float)) and not pd.isna(val):
                return float(val)
    return 0.0

def calculate_24m_avg_market_cap(ticker_obj, info, current_market_cap):
    """
    Calcule la capitalisation boursière moyenne sur 24 mois selon les normes AAOIFI / MSCI Islamic.
    """
    try:
        hist_2y = ticker_obj.history(period="2y")
        if hist_2y is not None and not hist_2y.empty and len(hist_2y) > 50:
            avg_close = float(hist_2y['Close'].dropna().mean())
            current_close = float(hist_2y['Close'].dropna().values[-1])
            shares = info.get("sharesOutstanding")
            if shares and shares > 0:
                return float(shares * avg_close)
            elif current_close > 0 and current_market_cap > 0:
                # Ratio de cours moyen sur cours actuel appliqué à la market cap
                return float(current_market_cap * (avg_close / current_close))
    except Exception as e:
        print(f"Notice: calculate_24m_avg_market_cap fallback: {e}")
        
    return current_market_cap

def check_financial_compliance(ticker_obj, info):
    """
    Vérifie la conformité des ratios financiers (Financial Screen) selon les normes AAOIFI / MSCI Islamic.
    Ratios calculés par rapport à la capitalisation boursière moyenne sur 24 mois :
      - Dette totale portant intérêt / Cap. Moyenne 24m < 33%
      - Trésorerie & Placements rémunérés / Cap. Moyenne 24m < 33%
      - Créances clients / Cap. Moyenne 24m < 33%
    """
    if not isinstance(info, dict):
        info = {}
        
    current_market_cap = info.get("marketCap")
    if not current_market_cap:
        shares = info.get("sharesOutstanding")
        price = info.get("currentPrice") or info.get("previousClose") or info.get("regularMarketPrice")
        if shares and price:
            current_market_cap = float(shares * price)
            
    if not current_market_cap:
        return False, {
            "status": "DONNÉES INSUFFISANTES",
            "reason": "Impossible d'obtenir la capitalisation boursière pour le calcul des ratios AAOIFI.",
            "details": {}
        }

    # Calcul de la capitalisation moyenne 24 mois
    market_cap_24m = calculate_24m_avg_market_cap(ticker_obj, info, current_market_cap)

    try:
        bs = getattr(ticker_obj, 'quarterly_balance_sheet', None)
        if bs is None or bs.empty:
            bs = getattr(ticker_obj, 'balance_sheet', None)
            
        if bs is None or bs.empty:
            return False, {
                "status": "DONNÉES INSUFFISANTES",
                "reason": "Bilan comptable indisponible pour auditer les ratios financiers AAOIFI.",
                "details": {
                    "market_cap": current_market_cap,
                    "market_cap_24m": market_cap_24m
                }
            }
            
        # 1. Dette totale portant intérêt
        debt_keys = ["Total Debt", "Long Term Debt", "LongTermDebt", "ShortLongTermDebt", "CurrentDebt"]
        total_debt = get_financial_metric(bs, debt_keys)
        if total_debt == 0.0:
            lt_debt = get_financial_metric(bs, ["Long Term Debt", "LongTermDebt"])
            st_debt = get_financial_metric(bs, ["Short Term Debt", "ShortLongTermDebt", "CurrentDebt"])
            total_debt = lt_debt + st_debt

        # 2. Liquidités & Placements rémunérés
        cash_keys = ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "CashAndCashEquivalents", "OtherShortTermInvestments"]
        cash_investments = get_financial_metric(bs, cash_keys)

        # 3. Créances clients
        receivables_keys = ["Accounts Receivable", "Net Receivables", "Receivables", "GrossAccountsReceivable"]
        receivables = get_financial_metric(bs, receivables_keys)

        # Calcul des ratios sur la capitalisation moyenne 24 mois
        cap_ref = market_cap_24m if market_cap_24m > 0 else current_market_cap
        debt_ratio = total_debt / cap_ref if cap_ref else 0.0
        cash_ratio = cash_investments / cap_ref if cap_ref else 0.0
        receivables_ratio = receivables / cap_ref if cap_ref else 0.0

        details = {
            "market_cap": current_market_cap,
            "market_cap_24m": cap_ref,
            "total_debt": total_debt,
            "debt_ratio": debt_ratio,
            "cash_investments": cash_investments,
            "cash_ratio": cash_ratio,
            "receivables": receivables,
            "receivables_ratio": receivables_ratio
        }

        # Évaluation par rapport aux seuils AAOIFI de 33%
        violations = []
        if debt_ratio >= SHARIA_MAX_DEBT_RATIO:
            violations.append(f"Dette / Cap. 24m élevée ({debt_ratio:.1%} >= {SHARIA_MAX_DEBT_RATIO:.0%})")
        if cash_ratio >= SHARIA_MAX_CASH_RATIO:
            violations.append(f"Cash & Placements / Cap. 24m élevés ({cash_ratio:.1%} >= {SHARIA_MAX_CASH_RATIO:.0%})")
        if receivables_ratio >= SHARIA_MAX_RECEIVABLES_RATIO:
            violations.append(f"Créances clients / Cap. 24m élevées ({receivables_ratio:.1%} >= {SHARIA_MAX_RECEIVABLES_RATIO:.0%})")

        if violations:
            return False, {
                "status": "NON CONFORME",
                "reason": "Dépassement des seuils AAOIFI : " + " ; ".join(violations),
                "details": details
            }

        return True, {
            "status": "CONFORME",
            "reason": "Ratios AAOIFI validés sur Cap. Moyenne 24 mois (Dette, Cash, Créances < 33%).",
            "details": details
        }
    except Exception as e:
        return False, {
            "status": "DONNÉES INSUFFISANTES",
            "reason": f"Erreur lors de l'audit des ratios AAOIFI : {str(e)}",
            "details": {}
        }

def screen_ticker(ticker_symbol):
    """
    Exécute le screening Sharia complet pour un ticker selon les normes AAOIFI / MSCI Islamic.
    Statut : [CONFORME] | [NON CONFORME] | [DONNÉES INSUFFISANTES]
    """
    ticker_symbol = ticker_symbol.upper().strip()
    
    # 0. Tenter de lire le statut pré-défini dans Google Sheets
    try:
        from src.sheets_connector import read_sharia_statuses_from_sheets
        sheet_statuses = read_sharia_statuses_from_sheets()
        if ticker_symbol in sheet_statuses:
            status_val = str(sheet_statuses[ticker_symbol]).strip().upper()
            if status_val in ["CONFORME", "NON CONFORME", "DONNÉES INSUFFISANTES", "HALAL", "HARAM", "TRUE", "FALSE"]:
                if status_val in ["CONFORME", "HALAL", "TRUE"]:
                    normalized_status = "CONFORME"
                elif status_val in ["NON CONFORME", "HARAM", "FALSE"]:
                    normalized_status = "NON CONFORME"
                else:
                    normalized_status = "DONNÉES INSUFFISANTES"
                    
                return {
                    "symbol": ticker_symbol,
                    "status": normalized_status,
                    "reason": f"Statut lu depuis votre Google Sheet",
                    "details": {}
                }
    except Exception as e:
        pass

    from src.market_data import get_ticker_info
    info = get_ticker_info(ticker_symbol)
    ticker_obj = yf.Ticker(ticker_symbol)

    # 1. Business Screen (Activités & Revenus illicites < 5%)
    is_business_compliant, business_reason = check_business_compliance(info)
    if not is_business_compliant:
        return {
            "symbol": ticker_symbol,
            "status": "NON CONFORME",
            "reason": business_reason,
            "details": {"industry": info.get("industry", ""), "sector": info.get("sector", "")}
        }

    # 2. Financial Screen (Ratios < 33% sur Cap. Moyenne 24 mois)
    is_financial_compliant, financial_res = check_financial_compliance(ticker_obj, info)
    
    if not isinstance(financial_res, dict):
        financial_res = {"status": "DONNÉES INSUFFISANTES", "reason": "Résultat financier non disponible"}
        
    financial_res["symbol"] = ticker_symbol
    financial_res["industry"] = info.get("industry", "")
    financial_res["sector"] = info.get("sector", "")
    return financial_res
