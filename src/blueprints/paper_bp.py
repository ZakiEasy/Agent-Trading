import os
import json
from flask import Blueprint, jsonify
from src.paper_trading_engine import get_paper_trading_data, init_paper_trading_db
import logging

logger = logging.getLogger("agent_trading.api.paper")

paper_bp = Blueprint("paper_bp", __name__)


@paper_bp.route("/api/paper/data", methods=["GET"])
def api_paper_data():
    """Renvoie les données du simulateur (Paper Trading) ainsi que son état de santé."""
    try:
        init_paper_trading_db()
        data = get_paper_trading_data()

        health_data = {"status": "inconnu", "last_execution": None}
        health_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "paper_health.json",
        )
        if os.path.exists(health_file):
            try:
                with open(health_file, "r") as f:
                    health_data = json.load(f)
            except:
                pass
        data["health"] = health_data

        return jsonify(data)
    except Exception as e:
        logger.error(f"Erreur api_paper_trading_data: {e}")
        return jsonify({"error": str(e)}), 500
