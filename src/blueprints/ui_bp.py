from flask import Blueprint, render_template, request, session, redirect

ui_bp = Blueprint("ui_bp", __name__)

@ui_bp.route("/")
@ui_bp.route("/dashboard")
@ui_bp.route("/screener")
@ui_bp.route("/robot")
@ui_bp.route("/portfolio")
@ui_bp.route("/diversification")
@ui_bp.route("/journal")
@ui_bp.route("/chat")
@ui_bp.route("/simulation")
@ui_bp.route("/paper")
def home():
    tab = request.path.strip("/") or "dashboard"
    return render_template("index.html", initial_tab=tab)

@ui_bp.route("/stock/<path:ticker>")
@ui_bp.route("/action/<path:ticker>")
@ui_bp.route("/detail/<path:ticker>")
def view_stock_detail(ticker):
    """
    Page dédiée plein écran pour l'analyse protocolaire 8 étapes dans un nouvel onglet.
    """
    clean_ticker = (ticker or "").upper().strip()
    return render_template("stock_detail.html", symbol=clean_ticker)

@ui_bp.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")
