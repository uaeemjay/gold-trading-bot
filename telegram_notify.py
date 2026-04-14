"""
Telegram notifications
Sends messages to your Telegram account when trades happen or errors occur
"""

import os
import logging
import requests

log = logging.getLogger(__name__)


def send_telegram(message):
    """Send a message to your Telegram chat."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        log.warning("Telegram credentials not set — skipping notification")
        return

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "HTML"
            },
            timeout=10
        )
        resp.raise_for_status()
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
