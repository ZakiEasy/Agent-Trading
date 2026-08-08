import yfinance as yf
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
    industry = info.get("industry", "").lower()
    sector = info.get("sector", "").lower()
    summary = info.get("longBusinessSummary", "").lower()
    
    # Exclure selon l'industrie ou le secteur
    for forbidden in SHARIA_EXCLUDED_INDUSTRIES:
        if forbidden in industry or forbidden in sector:
            return False, f"Secteur/Industrie non conforme : {forbidden}"
            
    # Exclure selon la description de l'activité
    # (par exemple, si le résumé contient des mentions évidentes de casino, armement, etc.)
    for forbidden in ["casino", "gambling", "pork", "distillery", "defense contractor"]:
        if forbidden in summary:
            return False, f"Activité non conforme (détectée dans le résumé) : {forbidden}"
            
    return True, "Activité conforme"

def get_financial_metric(balance_sheet, keys):
    """
    Récupère une métrique financière à partir de la balance sheet en testant plusieurs clés courantes.
    """
    for key in keys:
        if key in balance_sheet.index:
            row = balance_sheet.loc[key]
            # Prendre la valeur la plus récente (première colonne)
            val = row.iloc[0] if hasattr(row, 'iloc') else row
            if isinstance(val, (int, float)) and not pd.isna(val):
                return float(val)
    return 0.0

import pandas as pd

def check_financial_compliance(ticker_obj, info):
    """
    Vérifie la conformité des ratios financiers (Financial Screen).
    Ratios calculés par rapport à la capitalisation boursière (Market Cap).
    """
    market_cap = info.get("marketCap")
    if not market_cap:
        # Fallback si marketCap n'est pas dans info : essayer de calculer
        # cours actuel * actions en circulation
        shares = info.get("sharesOutstanding")
        price = info.get("currentPrice") or info.get("previousClose")
        if shares and price:
            market_cap = shares * price
            
    if not market_cap:
        return False, {
            "status": "NON CONFORME",
            "reason": "Impossible de déterminer la capitalisation boursière pour le calcul des ratios."
        }

    try:
        # Récupérer le bilan le plus récent (annuel ou trimestriel si disponible)
        bs = ticker_obj.quarterly_balance_sheet
        if bs.empty:
            bs = ticker_obj.balance_sheet
            
        if bs.empty:
            return False, {
                "status": "À VÉRIFIER",
                "reason": "Bilan financier indisponible pour calculer les ratios."
            }
            
        # 1. Dette totale
        # Clés possibles dans yfinance
        debt_keys = ["Total Debt", "Long Term Debt", "LongTermDebt", "ShortLongTermDebt"]
        total_debt = get_financial_metric(bs, debt_keys)
        
        # Si Total Debt n'est pas trouvé, on peut essayer d'additionner Long Term + Short Term Debt
        if total_debt == 0.0:
            lt_debt = get_financial_metric(bs, ["Long Term Debt", "LongTermDebt"])
            st_debt = get_financial_metric(bs, ["Short Term Debt", "ShortLongTermDebt", "CurrentDebt"])
            total_debt = lt_debt + st_debt

        # 2. Liquidités & Placements
        cash_keys = ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "CashAndCashEquivalents"]
        cash_investments = get_financial_metric(bs, cash_keys)

        # 3. Créances clients
        receivables_keys = ["Accounts Receivable", "Net Receivables", "Receivables"]
        receivables = get_financial_metric(bs, receivables_keys)

        # Calcul des ratios
        debt_ratio = total_debt / market_cap
        cash_ratio = cash_investments / market_cap
        receivables_ratio = receivables / market_cap

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
            "reason": f"Erreur lors du calcul des ratios : {str(e)}"
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
            status_val = sheet_statuses[ticker_symbol].strip().upper()
            if status_val in ["CONFORME", "NON CONFORME", "HALAL", "HARAM"]:
                normalized_status = "CONFORME" if status_val in ["CONFORME", "HALAL"] else "NON CONFORME"
                return {
                    "symbol": ticker_symbol,
                    "status": normalized_status,
                    "reason": f"Statut lu depuis votre Google Sheet (Source : Zoya ou utilisateur)"
                }
    except Exception as e:
        pass

    ticker_obj = yf.Ticker(ticker_symbol)
    try:
        info = ticker_obj.info
    except Exception as e:
        return {
            "symbol": ticker_symbol,
            "status": "À VÉRIFIER",
            "reason": f"Impossible de récupérer les informations de l'entreprise : {str(e)}"
        }

    # 1. Business Screen
    is_business_compliant, business_reason = check_business_compliance(info)
    if not is_business_compliant:
        return {
            "symbol": ticker_symbol,
            "status": "NON CONFORME",
            "reason": business_reason,
            "details": {"industry": info.get("industry"), "sector": info.get("sector")}
        }

    # 2. Financial Screen
    is_financial_compliant, financial_res = check_financial_compliance(ticker_obj, info)
    
    financial_res["symbol"] = ticker_symbol
    financial_res["industry"] = info.get("industry")
    financial_res["sector"] = info.get("sector")
    return financial_res
