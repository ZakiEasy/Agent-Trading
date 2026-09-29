from flask import Blueprint, request, jsonify
from src.utils.api_utils import safe_jsonify
from src.db_connector import (
    get_db_watchlist,
    add_or_update_watchlist_item,
    delete_from_watchlist,
)
from src.sharia_screen import screen_ticker
from src.market_data import check_fundamental_quality, categorize_ticker, get_company_name
from src.config import BASE_DIR, DEFAULT_WATCHLIST
import yfinance as yf
import json
import logging
import os

logger = logging.getLogger(__name__)
watchlist_bp = Blueprint("watchlist_bp", __name__)

@watchlist_bp.route("/api/watchlist")
def get_watchlist():
    """
    Retourne la liste complète des actions de la Watchlist depuis Supabase avec fallback automatique
    sur Google Sheets, le cache snapshot local ou la configuration locale si Supabase est vide ou inaccessible.
    """
    try:
        force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
        sb_wl = get_db_watchlist(only_active=True)

        # Charger le snapshot local persistant si existant
        local_snapshot_file = BASE_DIR / "data" / "watchlist_snapshot.json"
        local_items = []
        if local_snapshot_file.exists():
            try:
                with open(local_snapshot_file, "r", encoding="utf-8") as f:
                    local_items = json.load(f)
            except Exception:
                local_items = []

        if not sb_wl:
            from src.sheets_connector import (
                read_watchlist_from_sheets,
                read_sharia_statuses_from_sheets,
            )

            sheet_tickers = read_watchlist_from_sheets(force_refresh=force) or []
            sharia_map = read_sharia_statuses_from_sheets(force_refresh=force) or {}

            merged_tickers = (
                list(dict.fromkeys(sheet_tickers + DEFAULT_WATCHLIST))
                if sheet_tickers
                else DEFAULT_WATCHLIST
            )
            sb_wl = []
            for sym in merged_tickers:
                s = str(sym).strip().upper()
                if not s or s.startswith("TOTAL") or s.startswith("TABLEAU"):
                    continue
                cat_info = categorize_ticker(s)
                is_pea = cat_info.get(
                    "is_pea",
                    s.endswith(".PA") or s.endswith(".DE") or s.endswith(".AS"),
                )
                sb_wl.append(
                    {
                        "symbol": s,
                        "name": get_company_name(s),
                        "category": cat_info.get("category", "Autres"),
                        "category_icon": cat_info.get("category_icon", "📦"),
                        "is_pea": is_pea,
                        "account_type": "🇫🇷 PEA" if is_pea else "CTO (US)",
                        "sharia_status": sharia_map.get(s, "CONFORME"),
                        "currency": "EUR" if is_pea else "USD",
                        "is_active": True,
                    }
                )

        if local_items:
            existing_syms = set(str(item.get("symbol", "")).upper() for item in sb_wl)
            for l_item in local_items:
                l_sym = str(l_item.get("symbol", "")).upper()
                if l_sym and l_sym not in existing_syms:
                    sb_wl.append(l_item)
                    existing_syms.add(l_sym)

        return safe_jsonify({"success": True, "watchlist": sb_wl, "count": len(sb_wl)})
    except Exception as e:
        logger.error(f"Erreur get_watchlist: {e}")
        return safe_jsonify({"success": False, "error": str(e), "watchlist": []}, 500)

@watchlist_bp.route("/api/watchlist/add", methods=["POST"])
def add_watchlist_ticker():
    """
    Endpoint pour ajouter ou mettre à jour une action dans Supabase, Google Sheets et le cache snapshot local.
    """
    data = request.json or {}
    symbol = data.get("ticker", "").upper().strip()
    if not symbol:
        return jsonify(
            {"success": False, "error": "Le symbole de l'action est requis."}
        ), 400

    # 1. Vérifier et récupérer les informations avec yfinance
    info = {}
    try:
        t = yf.Ticker(symbol)
        raw_info = getattr(t, "info", None)
        if isinstance(raw_info, dict):
            info = raw_info
        name = (
            data.get("name") or info.get("longName") or info.get("shortName") or symbol
        )
        fund_q = check_fundamental_quality(t, info, symbol=symbol)
    except Exception as e:
        name = data.get("name") or symbol
        fund_q = {
            "category": "Autres",
            "is_pea": ".PA" in symbol,
            "account_type": "PEA" if ".PA" in symbol else "CTO (US)",
        }

    # 2. Screening Sharia
    sharia_res = screen_ticker(symbol)
    default_sharia = sharia_res.get("status", "À VÉRIFIER")
    sharia_status = data.get("sharia_status") or default_sharia

    category = data.get("category") or fund_q.get("category", "Autres")
    is_pea = (
        data.get("is_pea")
        if data.get("is_pea") is not None
        else fund_q.get("is_pea", False)
    )
    source_verif = data.get("source_verif") or "AAOIFI (Agent Trading)"

    # 3. Lancer l'analyse pour récupérer le prix actuel et le rapport
    from app import get_detailed_analysis
    analysis = get_detailed_analysis(symbol)
    price = 0.0
    currency = "USD"
    if isinstance(analysis, dict) and "step_5_technical" in analysis:
        price = analysis["step_5_technical"].get("current_price", 0.0)
        currency = analysis.get("currency", "USD")

    # 4. Écrire dans Supabase
    db_item = add_or_update_watchlist_item(
        symbol=symbol,
        name=name,
        category=category,
        category_icon=fund_q.get("category_icon", "📦"),
        is_pea=is_pea,
        account_type="🇫🇷 PEA" if is_pea else "CTO (US)",
        sharia_status=sharia_status,
        sharia_source=source_verif,
        currency=currency,
    )

    # 5. Écrire en miroir dans Google Sheets
    sheets_success = False
    try:
        from src.sheets_connector import add_ticker_to_sheets

        sheets_success, _ = add_ticker_to_sheets(
            ticker_symbol=symbol,
            name=name,
            category=category,
            is_pea=is_pea,
            sharia_status=sharia_status,
            current_price=price,
            source_verif=source_verif,
        )
    except Exception as e:
        logger.warning(f"Impossible d'écrire {symbol} dans Google Sheets : {e}")

    # 6. Sauvegarder dans le cache snapshot local persistant
    try:
        local_dir = BASE_DIR / "data"
        local_dir.mkdir(exist_ok=True)
        local_snapshot_file = local_dir / "watchlist_snapshot.json"
        local_items = []
        if local_snapshot_file.exists():
            try:
                with open(local_snapshot_file, "r", encoding="utf-8") as f:
                    local_items = json.load(f)
            except Exception:
                local_items = []

        # Mettre à jour ou ajouter
        found = False
        new_entry = {
            "symbol": symbol,
            "name": name,
            "category": category,
            "category_icon": fund_q.get("category_icon", "📦"),
            "is_pea": is_pea,
            "account_type": "🇫🇷 PEA" if is_pea else "CTO (US)",
            "sharia_status": sharia_status,
            "currency": currency,
            "price": price,
            "is_active": True,
        }
        for idx, item in enumerate(local_items):
            if str(item.get("symbol", "")).upper() == symbol:
                local_items[idx] = new_entry
                found = True
                break
        if not found:
            local_items.append(new_entry)

        with open(local_snapshot_file, "w", encoding="utf-8") as f:
            json.dump(local_items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(
            f"Erreur enregistrement cache snapshot local pour {symbol} : {e}"
        )

    return jsonify(
        {
            "success": True,
            "message": f"Action {symbol} ({name}) ajoutée avec succès dans la Watchlist (BDD, Google Sheets & Local) !",
            "ticker": symbol,
            "name": name,
            "category": category,
            "is_pea": is_pea,
            "account_type": "PEA (Europe)" if is_pea else "CTO (US)",
            "sharia_status": sharia_status,
            "sheets_synced": sheets_success,
            "data": analysis,
        }
    )

@watchlist_bp.route("/api/watchlist/delete", methods=["POST", "DELETE"])
@watchlist_bp.route("/api/watchlist/remove", methods=["POST", "DELETE"])
def remove_watchlist_ticker():
    """
    Endpoint pour retirer une action de la Watchlist (Supabase + Google Sheets + Caches).
    """
    data = request.json or {}
    symbol = (
        (
            data.get("ticker")
            or data.get("symbol")
            or request.args.get("ticker")
            or request.args.get("symbol")
            or ""
        )
        .upper()
        .strip()
    )
    if not symbol:
        return safe_jsonify(
            {"success": False, "error": "Le symbole de l'action est requis."}
        ), 400

    deleted = delete_from_watchlist(symbol)
    if not deleted:
        return safe_jsonify(
            {"success": False, "error": f"Impossible de supprimer l'action {symbol}."}
        ), 500

    # Récupérer le nombre restant
    current_wl = get_db_watchlist(only_active=True)
    if not current_wl:
        from src.sheets_connector import read_watchlist_from_sheets

        current_wl = [{"symbol": s} for s in (read_watchlist_from_sheets() or [])]

    return safe_jsonify(
        {
            "success": True,
            "symbol": symbol,
            "message": f"Action {symbol} retirée avec succès de la Watchlist.",
            "remaining_count": len(current_wl),
        }
    )

@watchlist_bp.route("/api/watchlist/tickers")
def get_watchlist_tickers():
    """
    Retourne la liste des tickers de la Watchlist avec leurs métadonnées depuis Supabase
    (avec fallback automatique sur le snapshot local et Google Sheets/DEFAULT_WATCHLIST)
    pour initialiser l'affichage instantanément avant le scan par lots.
    """
    try:
        force = request.args.get("force", "false").lower() in ["true", "1", "yes"]

        try:
            sb_wl = get_db_watchlist(only_active=True)
        except Exception as db_err:
            return safe_jsonify(
                {
                    "success": False,
                    "error": f"Erreur de connexion Supabase: {str(db_err)}",
                    "tickers": [],
                },
                500,
            )

        # Charger le snapshot local persistant si existant
        local_snapshot_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data",
            "watchlist_snapshot.json",
        )
        local_items = []
        if os.path.exists(local_snapshot_file):
            try:
                with open(local_snapshot_file, "r", encoding="utf-8") as f:
                    local_items = json.load(f)
            except Exception:
                local_items = []

        if not sb_wl:
            from src.sheets_connector import (
                read_watchlist_from_sheets,
                read_sharia_statuses_from_sheets,
            )

            sheet_tickers = read_watchlist_from_sheets(force_refresh=force) or []
            sharia_map = read_sharia_statuses_from_sheets(force_refresh=force) or {}

            merged_tickers = (
                list(dict.fromkeys(sheet_tickers + DEFAULT_WATCHLIST))
                if sheet_tickers
                else DEFAULT_WATCHLIST
            )
            sb_wl = []
            for sym in merged_tickers:
                s = str(sym).strip().upper()
                if not s or s.startswith("TOTAL") or s.startswith("TABLEAU"):
                    continue
                cat_info = categorize_ticker(s)
                is_pea = cat_info.get(
                    "is_pea",
                    s.endswith(".PA") or s.endswith(".DE") or s.endswith(".AS"),
                )
                sb_wl.append(
                    {
                        "symbol": s,
                        "name": get_company_name(s),
                        "category": cat_info.get("category", "Autres"),
                        "category_icon": cat_info.get("category_icon", "📦"),
                        "is_pea": is_pea,
                        "account_type": "🇫🇷 PEA" if is_pea else "CTO (US)",
                        "sharia_status": sharia_map.get(s, "CONFORME"),
                        "currency": "EUR" if is_pea else "USD",
                        "is_active": True,
                    }
                )

        if local_items:
            existing_syms = set(str(item.get("symbol", "")).upper() for item in sb_wl)
            for l_item in local_items:
                l_sym = str(l_item.get("symbol", "")).upper()
                if l_sym and l_sym not in existing_syms:
                    sb_wl.append(l_item)
                    existing_syms.add(l_sym)
        tickers_data = []
        for item in sb_wl:
            s = str(item.get("symbol", "")).strip().upper()
            if not s:
                continue
            cat = categorize_ticker(s)
            is_pea = (
                item.get("is_pea")
                if item.get("is_pea") is not None
                else (
                    s.endswith(".PA")
                    or s.endswith(".DE")
                    or s.endswith(".AS")
                    or s.endswith(".MC")
                )
            )
            acc_type = item.get("account_type") or ("🇫🇷 PEA" if is_pea else "CTO (US)")
            tickers_data.append(
                {
                    "symbol": s,
                    "name": item.get("name") or get_company_name(s),
                    "category": item.get("category") or cat.get("category", "Autres"),
                    "category_icon": item.get("category_icon")
                    or cat.get("category_icon", "📦"),
                    "is_pea": is_pea,
                    "account_type": acc_type,
                    "sharia": item.get("sharia_status")
                    or item.get("sharia")
                    or "CONFORME",
                }
            )

        return safe_jsonify(
            {"success": True, "tickers": tickers_data, "count": len(tickers_data)}
        )
    except Exception as e:
        return safe_jsonify({"success": False, "error": str(e), "tickers": []}, 500)