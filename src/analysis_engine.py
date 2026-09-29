import time
import logging
from src.sharia_screen import screen_ticker
from src.macro_regime import get_macro_barometer
from src.market_data import (
    fetch_market_data,
    analyze_technical_setup,
    qualify_price_drop,
    check_earnings_blackout,
    check_fundamental_quality,
    calculate_sector_relative_strength,
    get_company_name,
)
from src.risk_manager import calculate_trade_sizing, calculate_confluence_score
from src.config import CAPITAL_REFERENCE_DEFAULT

logger = logging.getLogger(__name__)

# Cache global des analyses pour fluidité et réduction des appels externes
analysis_cache = {}


def get_detailed_analysis(
    ticker_symbol, capital=CAPITAL_REFERENCE_DEFAULT, force_refresh=False
):
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

    if (
        not force_refresh
        and cache_key in analysis_cache
        and (now_ts - analysis_cache[cache_key]["ts"]) < 300
    ):
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
        fund_quality = check_fundamental_quality(
            ticker_obj, symbol=ticker_symbol, hist=hist
        )
        has_blackout, blackout_reason = check_earnings_blackout(ticker_obj)

        # Force relative sectorielle
        sector_strength = calculate_sector_relative_strength(
            ticker_symbol, fund_quality.get("category", "Autres"), hist
        )

        info = getattr(ticker_obj, "info", {}) if ticker_obj else {}
        company_name = get_company_name(ticker_symbol, info)

        curr_price = tech_setup["current_price"]
        support = tech_setup["support"]
        invalidation = support * 0.99

        # 5. Plan de Trade & Dimensionnement R-Max
        trade_plan = calculate_trade_sizing(
            capital_total=capital,
            entry_price=curr_price,
            stop_loss_price=invalidation,
            macro_regime=macro_barometer["regime"],
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
            sector_strength=sector_strength,
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
                "sector_strength": sector_strength,
            },
            "step_3_drop": {
                "drop_pct": drop_details.get("drop_pct", 0.0),
                "lookback_days": drop_details.get("lookback_days", 1),
                "nature": drop_details.get("nature", "N/A"),
                "cause_summary": drop_details.get("cause_summary", ""),
                "earnings_window": "Absence d'Earnings sous 10 jours ouvrés"
                if not has_blackout
                else blackout_reason,
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
                    "reason": blackout_reason,
                },
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
                "time_stop": trade_plan.get("time_stop", ""),
            },
            "step_8_confluence": confluence,
            "sharia": sharia_res,
            "technical": tech_setup,
            "drop": drop_details,
            "sector_strength": sector_strength,
            "has_qualified_drop": has_qualified_drop,
            "earnings_blackout": {"active": has_blackout, "reason": blackout_reason},
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
                "time_stop": trade_plan.get("time_stop", ""),
            },
            "verdict": confluence["verdict"],
            "confluence_score": confluence["confluence_score"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        analysis_cache[cache_key] = {"data": analysis, "ts": now_ts}
        return analysis
    except Exception as e:
        return {"error": str(e), "symbol": ticker_symbol}


# ==========================================
# AUTHENTICATION ROUTES (Google SSO)
# ==========================================


