from flask import Blueprint, request, jsonify
from src.utils.api_utils import safe_jsonify
from src.db_connector import (
    get_db_connection,
)
import logging
import io

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

logger = logging.getLogger(__name__)
portfolio_bp = Blueprint("portfolio_bp", __name__)

@portfolio_bp.route("/api/portfolio/live")
def get_live_portfolio():
    """
    Retourne la liste des positions actives avec calcul en direct du P&L, cours actuels, alertes et broker.
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    summary = get_live_portfolio_summary(force_refresh=force)
    return safe_jsonify({"success": True, "data": summary})

@portfolio_bp.route("/api/portfolio/xtb_quota")
def get_xtb_quota():
    """
    Retourne la consommation du quota mensuel de 100 000 € de transactions à 0% de commission chez XTB.
    """
    quota = calculate_xtb_monthly_turnover()
    return safe_jsonify({"success": True, "data": quota})

@portfolio_bp.route("/api/portfolio/monthly_rotation")
def get_portfolio_monthly_rotation():
    """
    Retourne la décomposition complète de la rotation du mois (par actions, montant investi, et transactions achat/vente).
    """
    month_prefix = request.args.get("month", "")
    journal = get_db_trade_journal()
    open_pos = get_db_positions(status="ACTIVE")

    rotation_data = calculate_monthly_rotation_by_stock(
        journal=journal, open_positions=open_pos, month_prefix=month_prefix
    )
    return safe_jsonify({"success": True, "data": rotation_data})

@portfolio_bp.route("/api/portfolio/anti_fifo_opportunities")
def get_anti_fifo_opportunities():
    """
    Retourne les opportunités d'arbitrage Anti-FIFO recommandant d'utiliser le broker alternatif.
    """
    opportunities = find_anti_fifo_opportunities()
    return safe_jsonify(
        {"success": True, "total": len(opportunities), "opportunities": opportunities}
    )

@portfolio_bp.route("/api/portfolio/treasury")
def get_portfolio_treasury():
    """
    Retourne le détail des soldes d'espèces, dépôts, retraits, dividendes et opérations de trésorerie depuis Supabase.
    """
    cash_ops = get_db_treasury_operations()
    summary = calculate_cash_and_treasury_summary(cash_ops)
    return safe_jsonify(
        {
            "success": True,
            "summary": summary,
            "operations_count": len(cash_ops),
            "recent_operations": cash_ops[:50] if cash_ops else [],
        }
    )

@portfolio_bp.route("/api/portfolio/diversification")
def get_portfolio_diversification():
    """
    Retourne la décomposition complète du portefeuille (catégorie/secteur, compte PEA/CTO, courtier, Actions vs Cash).
    """
    force = request.args.get("force", "false").lower() in ["true", "1", "yes"]
    live_summary = get_live_portfolio_summary(force_refresh=force)
    live_positions = live_summary.get("positions", [])
    cash_ops = get_db_treasury_operations()
    cash_summary = calculate_cash_and_treasury_summary(cash_ops)

    div = calculate_portfolio_diversification(live_positions, cash_summary=cash_summary)
    return safe_jsonify({"success": True, "data": div})

@portfolio_bp.route("/api/portfolio/add", methods=["POST"])
def add_portfolio_position():
    """
    Ajoute manuellement une position dans Supabase.
    """
    data = request.json or {}
    symbol = data.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify(
            {"success": False, "error": "Le symbole de l'action est requis."}
        ), 400

    pru = float(data.get("pru", 0))
    qty = float(data.get("quantity", 1))
    if pru <= 0 or qty <= 0:
        return jsonify(
            {"success": False, "error": "PRU et quantité doivent être supérieurs à 0."}
        ), 400

    name = data.get("name") or get_company_name(symbol)
    is_pea = ".PA" in symbol or data.get("account") == "PEA"
    default_acc = "PEA" if is_pea else "CTO Dollar"
    account = data.get("account", default_acc)
    currency = data.get(
        "currency", "EUR" if ("PEA" in account or ".PA" in symbol) else "USD"
    )

    sl = float(data.get("stop_loss", pru * 0.97))
    tp1 = float(data.get("tp1", pru * 1.0125))
    tp2 = float(data.get("tp2", pru * 1.0225))

    pos_data = {
        "symbol": symbol,
        "company_name": name,
        "broker": data.get("broker", "XTB"),
        "account_type": account,
        "pru": pru,
        "quantity": qty,
        "invested_capital": pru * qty,
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "currency": currency,
        "status": "ACTIVE",
        "notes": data.get("notes", ""),
    }

    saved = save_or_update_position(pos_data)
    if saved:
        return jsonify(
            {
                "success": True,
                "message": f"Position {symbol} ({qty} actions à {pru} {currency}) enregistrée avec succès en BDD !",
            }
        )
    return jsonify(
        {
            "success": False,
            "error": "Erreur lors de l'enregistrement de la position en BDD.",
        }
    ), 500

    pru = float(data.get("pru", 0))
    qty = float(data.get("quantity", 1))

    name = data.get("name")
    if not name:
        try:
            t = yf.Ticker(symbol)
            info = getattr(t, "info", {})
            name = info.get("longName") or info.get("shortName") or symbol
        except:
            name = symbol

    pos_data = {
        "symbol": symbol,
        "name": name,
        "entry_date": data.get("entry_date") or datetime.now().strftime("%Y-%m-%d"),
        "pru": pru,
        "quantity": qty,
        "stop_loss": float(data.get("stop_loss", pru * 0.97)),
        "tp1": float(data.get("tp1", pru * 1.0125)),
        "tp2": float(data.get("tp2", pru * 1.0225)),
        "account": data.get("account", "PEA" if ".PA" in symbol else "CTO"),
        "currency": data.get(
            "currency", "EUR" if ".PA" in symbol or ".DE" in symbol else "USD"
        ),
        "notes": data.get("notes", ""),
    }

    success, msg = add_position_to_sheets(pos_data)
    return jsonify({"success": success, "message": msg})

@portfolio_bp.route("/api/portfolio/upload_report", methods=["POST"])
@portfolio_bp.route("/api/portfolio/upload_csv", methods=["POST"])
@portfolio_bp.route("/api/portfolio/upload_multiple_reports", methods=["POST"])
def upload_portfolio_report():
    """
    Importe un ou plusieurs fichiers Excel (.xlsx, .xls) ou CSV de rapport XTB / courtier.
    Auto-détecte pour chaque fichier :
      - Le compte (PEA, CTO Euro, CTO Dollar/US)
      - Les positions fermées (Journal de Trading)
      - Les positions ouvertes (Suivi Live)
      - Les opérations de trésorerie (Cash)
    """
    files = request.files.getlist("files")
    if not files:
        single_file = request.files.get("file")
        if single_file:
            files = [single_file]

    if not files:
        return jsonify({"success": False, "error": "Aucun fichier fourni."}), 400

    default_acc_override = request.form.get("account")
    if default_acc_override == "auto":
        default_acc_override = None

    seen_closed_ids = set()
    seen_open_ids = set()
    seen_cash_ids = set()
    files_processed = 0
    by_account = {
        "PEA": {"closed": 0, "open": 0, "cash": 0},
        "CTO Euro": {"closed": 0, "open": 0, "cash": 0},
        "CTO Dollar": {"closed": 0, "open": 0, "cash": 0},
        "Trading 212": {"closed": 0, "open": 0, "cash": 0},
    }

    # Charger l'existant pour déduplication
    existing_journal = get_db_trade_journal() or []
    for t in existing_journal:
        if t.get("id"):
            seen_closed_ids.add(t["id"])

    existing_open = get_db_positions(status="ACTIVE") or []
    for o in existing_open:
        if o.get("id"):
            seen_open_ids.add(o["id"])

    existing_cash = get_db_treasury_operations() or []
    for c in existing_cash:
        if c.get("id"):
            seen_cash_ids.add(c["id"])

    new_closed_list = []
    new_open_list = []
    new_cash_list = []

    for file_item in files:
        if not file_item or not file_item.filename:
            continue
        fname = file_item.filename
        content = file_item.read()
        if not content:
            continue

        files_processed += 1
        detected_acc = default_acc_override
        if not detected_acc:
            fname_upper = fname.upper()
            if "212" in fname_upper or "T212" in fname_upper:
                detected_acc = "Trading 212"
            elif "PEA" in fname_upper:
                detected_acc = "PEA"
            elif "USD" in fname_upper or "DOLLAR" in fname_upper or "US" in fname_upper:
                detected_acc = "CTO Dollar"
            else:
                detected_acc = "CTO Euro"

        if fname.lower().endswith(".xlsx") or fname.lower().endswith(".xls"):
            parsed = parse_xtb_excel_file(content, default_account=detected_acc)
            for t in parsed.get("closed_positions", []):
                tid = t.get("id")
                if tid and tid not in seen_closed_ids:
                    seen_closed_ids.add(tid)
                    new_closed_list.append(t)
                    acc_key = t.get("account", "CTO Euro")
                    if acc_key not in by_account:
                        by_account[acc_key] = {"closed": 0, "open": 0, "cash": 0}
                    by_account[acc_key]["closed"] += 1

            for o in parsed.get("open_positions", []):
                oid = o.get("id")
                if oid and oid not in seen_open_ids:
                    seen_open_ids.add(oid)
                    new_open_list.append(o)
                    acc_key = o.get("account", "CTO Euro")
                    if acc_key not in by_account:
                        by_account[acc_key] = {"closed": 0, "open": 0, "cash": 0}
                    by_account[acc_key]["open"] += 1

            for c in parsed.get("cash_operations", []):
                cid = c.get("id")
                if cid and cid not in seen_cash_ids:
                    seen_cash_ids.add(cid)
                    new_cash_list.append(c)
                    acc_key = c.get("account", "CTO Euro")
                    if acc_key not in by_account:
                        by_account[acc_key] = {"closed": 0, "open": 0, "cash": 0}
                    by_account[acc_key]["cash"] += 1
        else:
            # Traitement CSV (Trading 212 vs format standard/XTB)
            sample_header = (
                content[:200].decode("utf-8", errors="ignore").lower()
                if isinstance(content, bytes)
                else str(content[:200]).lower()
            )
            is_t212 = (
                detected_acc == "Trading 212"
                or "isin" in sample_header
                or "no. of shares" in sample_header
                or "price / share" in sample_header
            )

            if is_t212:
                parsed_t212 = parse_trading212_csv(content)
                for t in parsed_t212.get("closed_positions", []):
                    tid = t.get("id")
                    if tid and tid not in seen_closed_ids:
                        seen_closed_ids.add(tid)
                        new_closed_list.append(t)
                        by_account["Trading 212"]["closed"] += 1

                for c in parsed_t212.get("cash_operations", []):
                    cid = c.get("id")
                    if cid and cid not in seen_cash_ids:
                        seen_cash_ids.add(cid)
                        new_cash_list.append(c)
                        by_account["Trading 212"]["cash"] += 1
            else:
                positions = parse_broker_csv(content)
                for pos in positions:
                    pid = pos.get("id")
                    if pid and pid not in seen_open_ids:
                        seen_open_ids.add(pid)
                        new_open_list.append(pos)
                        acc_key = pos.get("account", detected_acc or "CTO Euro")
                        if acc_key not in by_account:
                            by_account[acc_key] = {"closed": 0, "open": 0, "cash": 0}
                            by_account[acc_key]["open"] += 1

    if new_closed_list:
        batch_save_trade_journal(new_closed_list)

    if new_open_list:
        agg_open = aggregate_open_positions(existing_open + new_open_list)
        batch_save_positions(agg_open)

    if new_cash_list:
        batch_save_treasury_operations(new_cash_list)

    msg = f"{files_processed} fichier(s) traité(s) avec succès : {len(new_closed_list)} nouveau(x) trade(s) dans le Journal, {len(new_open_list)} position(s) active(s), {len(new_cash_list)} opération(s) de trésorerie."

    return jsonify(
        {
            "success": True,
            "message": msg,
            "files_count": files_processed,
            "closed_imported": len(new_closed_list),
            "open_imported": len(new_open_list),
            "cash_imported": len(new_cash_list),
            "by_account": by_account,
        }
    )

@portfolio_bp.route("/api/portfolio/import_all_history", methods=["POST"])
def import_all_history_files():
    """
    Importe automatiquement tous les fichiers d'historique XTB (positions fermées, ouvertes et trésorerie)
    présents dans le dossier /historique (hors sous-dossier /old) directement en base de données.
    """
    import os

    base_dir = os.path.dirname(os.path.abspath(__file__))
    hist_dir = os.path.join(base_dir, "historique")

    files_to_scan = []
    for root, _, files in os.walk(hist_dir):
        parts = [p.lower() for p in root.split(os.sep)]
        if "old" in parts:
            continue
        for f in files:
            if (
                f.lower().endswith(".xlsx")
                and not f.startswith("~$")
                and not f.startswith(".")
            ):
                files_to_scan.append(os.path.join(root, f))

    all_closed = []
    all_open = []
    all_cash = []
    seen_closed_ids = set()
    seen_open_ids = set()
    seen_cash_ids = set()

    for fpath in files_to_scan:
        fname = os.path.basename(fpath).upper()
        acc = (
            "PEA"
            if "PEA" in fname
            else "CTO Dollar"
            if ("USD" in fname or "DOLLAR" in fname)
            else "CTO Euro"
        )
        parsed = parse_xtb_excel_file(fpath, default_account=acc)

        for t in parsed.get("closed_positions", []):
            if t["id"] not in seen_closed_ids:
                seen_closed_ids.add(t["id"])
                all_closed.append(t)

        for o in parsed.get("open_positions", []):
            if o["id"] not in seen_open_ids:
                seen_open_ids.add(o["id"])
                all_open.append(o)

        for c in parsed.get("cash_operations", []):
            if c["id"] not in seen_cash_ids:
                seen_cash_ids.add(c["id"])
                all_cash.append(c)

    # Si le parsing direct a échoué (ex: openpyxl non dispo ou fichiers en cours d'écriture), fallback sur le snapshot
    if not all_closed and not all_open and not all_cash:
        from src.db_connector import _get_xtb_snapshot_data

        snap = _get_xtb_snapshot_data()
        all_closed = snap.get("closed_positions", [])
        all_open = snap.get("open_positions", [])
        all_cash = snap.get("cash_operations", [])
        agg_open = all_open
    else:
        # 1. Enregistrer dans le Journal
        batch_save_trade_journal(all_closed)

        # 2. Enregistrer les positions ouvertes agrégées
        agg_open = aggregate_open_positions(all_open)
        batch_save_positions(agg_open)

        # 3. Enregistrer les opérations de trésorerie
        batch_save_treasury_operations(all_cash)

    stats = calculate_trading_performance_stats(all_closed)
    cash_summary = calculate_cash_and_treasury_summary(all_cash)

    return jsonify(
        {
            "success": True,
            "message": f"Synchronisation historique réussie ({len(files_to_scan)} fichiers analysés) ! {len(all_closed)} trades dans le Journal, {len(agg_open)} positions actives, {len(all_cash)} opérations de trésorerie.",
            "files_count": len(files_to_scan),
            "closed_count": len(all_closed),
            "open_count": len(agg_open),
            "cash_count": len(all_cash),
            "stats": stats,
            "treasury_summary": cash_summary,
        }
    )

@portfolio_bp.route("/api/portfolio/close", methods=["POST"])
def close_portfolio_position():
    """
    Clôture une position active à un cours donné et l'archive dans le journal de trading Supabase.
    """
    data = request.json or {}
    pos_id = data.get("id") or data.get("symbol")
    exit_price = float(data.get("exit_price", 0))
    notes = data.get("notes", "")

    if not pos_id or exit_price <= 0:
        return jsonify(
            {
                "success": False,
                "error": "ID de position et prix de sortie valides requis.",
            }
        ), 400

    success, msg = close_db_position(pos_id, exit_price, notes=notes)
    return jsonify({"success": success, "message": msg})