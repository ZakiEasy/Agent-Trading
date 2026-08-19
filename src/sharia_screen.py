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

def check_financial_compliance(ticker_obj, info):
    """
    Vérifie la conformité des ratios financiers (Financial Screen).
    Ratios calculés par rapport à la capitalisation boursière (Market Cap).
    """
    if not isinstance(info, dict):
        info = {}
        
    market_cap = info.get("marketCap")
    if not market_cap:
        shares = info.get("sharesOutstanding")
        price = info.get("currentPrice") or info.get("previousClose") or info.get("regularMarketPrice")
        if shares and price:
            market_cap = shares * price
            
    if not market_cap:
        return False, {
            "status": "NON CONFORME",
            "reason": "Impossible de déterminer la capitalisation boursière pour le calcul des ratios.",
            "details": {}
        }

    try:
        bs = getattr(ticker_obj, 'quarterly_balance_sheet', None)
        if bs is None or bs.empty:
            bs = getattr(ticker_obj, 'balance_sheet', None)
            
        if bs is None or bs.empty:
            return False, {
                "status": "À VÉRIFIER",
                "reason": "Bilan financier indisponible pour calculer les ratios.",
                "details": {"market_cap": market_cap}
            }
            
        # 1. Dette totale
        debt_keys = ["Total Debt", "Long Term Debt", "LongTermDebt", "ShortLongTermDebt", "CurrentDebt"]
        total_debt = get_financial_metric(bs, debt_keys)
        if total_debt == 0.0:
            lt_debt = get_financial_metric(bs, ["Long Term Debt", "LongTermDebt"])
            st_debt = get_financial_metric(bs, ["Short Term Debt", "ShortLongTermDebt", "CurrentDebt"])
            total_debt = lt_debt + st_debt

        # 2. Liquidités & Placements
        cash_keys = ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "CashAndCashEquivalents", "OtherShortTermInvestments"]
        cash_investments = get_financial_metric(bs, cash_keys)

        # 3. Créances clients
        receivables_keys = ["Accounts Receivable", "Net Receivables", "Receivables", "GrossAccountsReceivable"]
        receivables = get_financial_metric(bs, receivables_keys)

        # Calcul des ratios
        debt_ratio = total_debt / market_cap if market_cap else 0.0
        cash_ratio = cash_investments / market_cap if market_cap else 0.0
        receivables_ratio = receivables / market_cap if market_cap else 0.0

        details = {
            "market_cap": market_cap,
            "total_debt": total_debt,
            "debt_ratio": debt_ratio,
            "cash_investments": cash_investments,
            "cash_ratio": cash_ratio,
            "receivables": receivables,
            "receivables_ratio": receivables_ratio
        }

        # Évaluation par rapport aux seuils de 33%
        if debt_ratio >= SHARIA_MAX_DEBT_RATIO:
            return False, {
                "status": "NON CONFORME",
                "reason": f"Dette trop élevée (Ratio: {debt_ratio:.2%} >= {SHARIA_MAX_DEBT_RATIO:.0%})",
                "details": details
            }
        if cash_ratio >= SHARIA_MAX_CASH_RATIO:
            return False, {
                "status": "NON CONFORME",
                "reason": f"Liquidités/Placements trop élevés (Ratio: {cash_ratio:.2%} >= {SHARIA_MAX_CASH_RATIO:.0%})",
                "details": details
            }
        if receivables_ratio >= SHARIA_MAX_RECEIVABLES_RATIO:
            return False, {
                "status": "NON CONFORME",
                "reason": f"Créances clients trop élevées (Ratio: {receivables_ratio:.2%} >= {SHARIA_MAX_RECEIVABLES_RATIO:.0%})",
                "details": details
            }

        return True, {
            "status": "CONFORME",
            "reason": "Tous les ratios financiers sont inférieurs à 33%.",
            "details": details
        }
    except Exception as e:
        return False, {
            "status": "À VÉRIFIER",
            "reason": f"Erreur lors du calcul des ratios : {str(e)}",
            "details": {}
        }

def screen_ticker(ticker_symbol):
    """
    Exécute le screening Sharia complet pour un ticker.
    """
    ticker_symbol = ticker_symbol.upper().strip()
    
    # 0. Tenter de lire le statut pré-défini dans Google Sheets
    try:
        from src.sheets_connector import read_sharia_statuses_from_sheets
        sheet_statuses = read_sharia_statuses_from_sheets()
        if ticker_symbol in sheet_statuses:
            status_val = str(sheet_statuses[ticker_symbol]).strip().upper()
            if status_val in ["CONFORME", "NON CONFORME", "HALAL", "HARAM", "TRUE", "FALSE"]:
                normalized_status = "CONFORME" if status_val in ["CONFORME", "HALAL", "TRUE"] else "NON CONFORME"
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

    # 1. Business Screen
    is_business_compliant, business_reason = check_business_compliance(info)
    if not is_business_compliant:
        return {
            "symbol": ticker_symbol,
            "status": "NON CONFORME",
            "reason": business_reason,
            "details": {"industry": info.get("industry", ""), "sector": info.get("sector", "")}
        }

    # 2. Financial Screen
    is_financial_compliant, financial_res = check_financial_compliance(ticker_obj, info)
    
    if not isinstance(financial_res, dict):
        financial_res = {"status": "À VÉRIFIER", "reason": "Résultat financier non disponible"}
        
    financial_res["symbol"] = ticker_symbol
    financial_res["industry"] = info.get("industry", "")
    financial_res["sector"] = info.get("sector", "")
    return financial_res
