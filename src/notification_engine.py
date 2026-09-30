import requests
import logging
from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_telegram_message(message: str, parse_mode="HTML"):
    """
    Envoie un message via l'API Telegram.
    Ne fait rien si le token ou le chat ID n'est pas configuré.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.debug("Notifications Telegram désactivées (Token ou Chat ID manquant).")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": parse_mode}

    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            logging.info("✅ Notification Telegram envoyée avec succès.")
            return True
        else:
            logging.error(
                f"⚠️ Échec de l'envoi Telegram : {response.status_code} - {response.text}"
            )
            return False
    except Exception as e:
        logging.error(f"⚠️ Erreur lors de l'envoi de la notification Telegram : {e}")
        return False
