import math
from datetime import datetime
from flask import jsonify
import logging

logger = logging.getLogger(__name__)

def sanitize_for_json(obj, key_path=""):
    """
    Parcourt récursivement les structures pour convertir tout NaN, Inf, -Inf, types NumPy et Pandas
    en types Python natifs pour garantir un JSON strictement valide sans erreurs 500.
    """
    CRITICAL_RISK_FIELDS = {
        "stop_loss_price",
        "entry_price",
        "r_max_amount",
        "capital_reference",
        "shares_count",
        "suggested_nominal",
        "allocation_pct_of_capital",
        "actual_monetary_risk",
    }

    if obj is None:
        return None
    if isinstance(obj, (float, int)):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            field_name = key_path.rsplit(".", 1)[-1] if key_path else ""
            if field_name in CRITICAL_RISK_FIELDS:
                raise ValueError(
                    f"Donnée de risque critique invalide (NaN/Inf) : {key_path}"
                )
            return 0.0
        return obj
    try:
        import numpy as np

        if isinstance(obj, (np.floating, np.integer)):
            val = float(obj) if isinstance(obj, np.floating) else int(obj)
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                field_name = key_path.rsplit(".", 1)[-1] if key_path else ""
                if field_name in CRITICAL_RISK_FIELDS:
                    raise ValueError(
                        f"Donnée de risque critique invalide (NaN/Inf) : {key_path}"
                    )
                return 0.0
            return val
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return [
                sanitize_for_json(item, f"{key_path}[{i}]")
                for i, item in enumerate(obj.tolist())
            ]
    except Exception:
        pass

    try:
        import pandas as pd

        if pd.isna(obj):
            field_name = key_path.rsplit(".", 1)[-1] if key_path else ""
            if field_name in CRITICAL_RISK_FIELDS:
                raise ValueError(
                    f"Donnée de risque critique invalide (NaN/Inf) : {key_path}"
                )
            return 0.0
        if isinstance(obj, (pd.Timestamp, datetime)):
            return str(obj)
    except Exception:
        pass

    if isinstance(obj, dict):
        return {
            str(k): sanitize_for_json(v, f"{key_path}.{k}" if key_path else str(k))
            for k, v in obj.items()
        }
    elif isinstance(obj, (list, tuple, set)):
        return [
            sanitize_for_json(item, f"{key_path}[{i}]") for i, item in enumerate(obj)
        ]
    elif hasattr(obj, "item") and callable(getattr(obj, "item")):
        try:
            return sanitize_for_json(obj.item(), key_path)
        except:
            return str(obj)
    elif hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
        try:
            return sanitize_for_json(obj.to_dict(), key_path)
        except:
            return str(obj)
    elif not isinstance(obj, (str, bool)):
        return str(obj)
    return obj


def safe_jsonify(data, status_code=200):
    """
    Retourne un JSON assaini avec le code de statut HTTP souhaité.
    Gère gracieusement les erreurs de données critiques (ValueError).
    """
    try:
        cleaned = sanitize_for_json(data)
        response = jsonify(cleaned)
        response.status_code = status_code
        return response
    except ValueError as ve:
        logger.error(f"Erreur de sanitization JSON : {ve}")
        response = jsonify({"success": False, "error": str(ve)})
        response.status_code = 422
        return response
