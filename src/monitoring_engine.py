import os
import psutil
import logging
from datetime import datetime
from src.db_connector import get_db_connection

logger = logging.getLogger(__name__)


def check_db_health():
    """Vérifie la latence et la connectivité à la base PostgreSQL."""
    try:
        start_time = datetime.now()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1;")
        cursor.fetchone()
        cursor.close()
        conn.close()
        latency = (datetime.now() - start_time).total_seconds() * 1000
        return {"status": "ok", "latency_ms": round(latency, 2), "error": None}
    except Exception as e:
        logger.error(f"Erreur DB healthcheck: {e}")
        return {"status": "error", "latency_ms": None, "error": str(e)}


def get_system_metrics():
    """Récupère les métriques CPU/RAM/Disque du serveur VPS."""
    try:
        cpu_percent = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        return {
            "cpu": {"percent": cpu_percent},
            "memory": {
                "total_mb": round(memory.total / (1024 * 1024), 2),
                "used_mb": round(memory.used / (1024 * 1024), 2),
                "percent": memory.percent,
            },
            "disk": {
                "total_gb": round(disk.total / (1024 * 1024 * 1024), 2),
                "used_gb": round(disk.used / (1024 * 1024 * 1024), 2),
                "percent": disk.percent,
            },
        }
    except Exception as e:
        logger.error(f"Erreur metrics: {e}")
        return None


def run_compliance_audit():
    """Contrôle automatique du respect des règles d'ingénierie."""
    results = []

    # Règle 1: Configuration du risque
    from src.order_guardrails import OrderGuardrails

    try:
        guardrails = OrderGuardrails()
        if guardrails.max_risk_per_trade_pct <= 1.0:
            results.append(
                {
                    "rule": "R-Max <= 1.0%",
                    "status": "pass",
                    "details": f"Valeur actuelle: {guardrails.max_risk_per_trade_pct}%",
                }
            )
        else:
            results.append(
                {
                    "rule": "R-Max <= 1.0%",
                    "status": "fail",
                    "details": f"Valeur actuelle: {guardrails.max_risk_per_trade_pct}% (Violation)",
                }
            )

        if guardrails.max_portfolio_exposure_pct <= 25.0:
            results.append(
                {
                    "rule": "Exposition Max <= 25.0%",
                    "status": "pass",
                    "details": f"Valeur actuelle: {guardrails.max_portfolio_exposure_pct}%",
                }
            )
        else:
            results.append(
                {
                    "rule": "Exposition Max <= 25.0%",
                    "status": "fail",
                    "details": f"Valeur actuelle: {guardrails.max_portfolio_exposure_pct}% (Violation)",
                }
            )

    except Exception as e:
        results.append(
            {"rule": "Vérification Guardrails", "status": "error", "details": str(e)}
        )

    # Règle 2: Base de données
    db_health = check_db_health()
    if db_health["status"] == "ok":
        results.append(
            {
                "rule": "Connectivité Base de Données",
                "status": "pass",
                "details": f"Latence: {db_health['latency_ms']}ms",
            }
        )
    else:
        results.append(
            {
                "rule": "Connectivité Base de Données",
                "status": "fail",
                "details": db_health["error"],
            }
        )

    return results


def get_recent_logs(lines=100):
    """
    Tente de lire les derniers logs de l'application.
    (Par défaut lit le journal docker si disponible, ou on peut utiliser un fichier local)
    """
    try:
        # Si on est dans un conteneur avec un fichier de log local
        log_path = "/app/data/agent.log"
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                content = f.readlines()
                return "".join(content[-lines:])
        else:
            return "Aucun fichier de log trouvé dans /app/data/agent.log"
    except Exception as e:
        return f"Erreur de lecture des logs: {e}"
