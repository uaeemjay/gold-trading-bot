"""
Gold Trading Bot — Main webhook server
Listens for TradingView alerts and places trades on Capital.com
"""

import os
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from capital import CapitalClient
from telegram_notify import send_telegram

# ── Load environment variables from .env file ──────────────────
load_dotenv()

# ── Logging: writes to console AND a log file ──────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log")
    ]
)
log = logging.getLogger(__name__)

# ── Flask app (the web server that receives webhooks) ──────────
app = Flask(__name__)

# ── Settings ───────────────────────────────────────────────────
EPIC       = "GOLD"                                # XAUUSD instrument code on Capital.com
TRADE_SIZE = float(os.getenv("TRADE_SIZE", "2"))    # Quantity per trade

# ── Capital.com client ─────────────────────────────────────────
def get_capital():
    return CapitalClient(
        api_key    = os.getenv("CAPITAL_API_KEY"),
        password   = os.getenv("CAPITAL_PASSWORD"),
        account_id = os.getenv("CAPITAL_ACCOUNT_ID"),
        env        = os.getenv("CAPITAL_ENV", "demo")
    )


# ══════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    """Simple check to confirm the bot is alive."""
    return jsonify({"status": "running", "time": datetime.now().isoformat()})


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView sends alerts here.
    Expected payloads:
      {"action": "buy"}
      {"action": "sell"}
      {"action": "tp"}
    """
    data = request.get_json(silent=True)

    if not data or "action" not in data:
        log.warning(f"Invalid webhook payload received: {data}")
        return jsonify({"error": "Invalid payload — expected {\"action\": \"buy/sell/tp\"}"}), 400

    action    = data["action"].lower().strip()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"[{timestamp}] Webhook received: action={action}")

    try:
        if action == "buy":
            handle_buy()
        elif action == "sell":
            handle_sell()
        elif action == "tp":
            handle_tp()
        else:
            log.warning(f"Unknown action received: {action}")
            return jsonify({"error": f"Unknown action: {action}"}), 400

    except Exception as e:
        log.error(f"Error handling '{action}' signal: {e}")
        send_telegram(f"❌ <b>Bot Error</b>\nSignal: {action}\nError: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok", "action": action})


# ══════════════════════════════════════════════════════════════
# SIGNAL HANDLERS
# ══════════════════════════════════════════════════════════════

def handle_buy():
    """
    BUY signal logic:
    - If SELL positions open → close them all, then open BUY
    - If BUY positions open → stack another BUY on top
    - If no positions → open new BUY
    """
    capital = get_capital()
    positions      = capital.get_positions(EPIC)
    sell_positions = [p for p in positions if p["direction"] == "SELL"]
    buy_positions  = [p for p in positions if p["direction"] == "BUY"]

    # Close any opposite (SELL) positions first
    if sell_positions:
        log.info(f"BUY signal: closing {len(sell_positions)} SELL position(s) first")
        for pos in sell_positions:
            capital.close_position(pos["dealId"])
            log.info(f"  Closed SELL deal {pos['dealId']}")
        send_telegram(
            f"🔄 <b>Reversed to BUY</b>\n"
            f"Closed {len(sell_positions)} SELL position(s) on XAUUSD"
        )

    # Determine reason for Telegram message
    if buy_positions:
        reason = f"Stacking — {len(buy_positions)} BUY already open"
    elif sell_positions:
        reason = "Signal reversed from SELL → BUY"
    else:
        reason = "New BUY signal"

    # Open BUY trade
    capital.open_position(EPIC, "BUY", TRADE_SIZE)
    log.info(f"Opened BUY {TRADE_SIZE} x {EPIC} | {reason}")
    send_telegram(
        f"📈 <b>BUY opened</b>\n"
        f"Instrument: XAUUSD\n"
        f"Quantity: {TRADE_SIZE}\n"
        f"Reason: {reason}"
    )


def handle_sell():
    """
    SELL signal logic:
    - If BUY positions open → close them all, then open SELL
    - If SELL positions open → stack another SELL on top
    - If no positions → open new SELL
    """
    capital = get_capital()
    positions      = capital.get_positions(EPIC)
    buy_positions  = [p for p in positions if p["direction"] == "BUY"]
    sell_positions = [p for p in positions if p["direction"] == "SELL"]

    # Close any opposite (BUY) positions first
    if buy_positions:
        log.info(f"SELL signal: closing {len(buy_positions)} BUY position(s) first")
        for pos in buy_positions:
            capital.close_position(pos["dealId"])
            log.info(f"  Closed BUY deal {pos['dealId']}")
        send_telegram(
            f"🔄 <b>Reversed to SELL</b>\n"
            f"Closed {len(buy_positions)} BUY position(s) on XAUUSD"
        )

    # Determine reason
    if sell_positions:
        reason = f"Stacking — {len(sell_positions)} SELL already open"
    elif buy_positions:
        reason = "Signal reversed from BUY → SELL"
    else:
        reason = "New SELL signal"

    # Open SELL trade
    capital.open_position(EPIC, "SELL", TRADE_SIZE)
    log.info(f"Opened SELL {TRADE_SIZE} x {EPIC} | {reason}")
    send_telegram(
        f"📉 <b>SELL opened</b>\n"
        f"Instrument: XAUUSD\n"
        f"Quantity: {TRADE_SIZE}\n"
        f"Reason: {reason}"
    )


def handle_tp():
    """
    TP signal logic (netting account):
    - Reads the live net position size each time — no state needed
    - Opens an opposite trade for 70% of current size
    - Capital.com nets it immediately, so the next TP naturally sees only the 30% remainder
    - Whatever remains after all TPs closes on the next opposite direction signal
    """
    capital   = get_capital()
    positions = capital.get_positions(EPIC)

    if not positions:
        log.info("TP signal received — no open positions to close")
        return

    direction  = positions[0]["direction"]
    opposite   = "SELL" if direction == "BUY" else "BUY"
    current    = round(sum(float(p["size"]) for p in positions if p["direction"] == direction), 2)
    close_size = round(current * 0.7, 2)

    if close_size <= 0:
        log.info("TP signal: position size too small to close")
        return

    remaining = round(current - close_size, 2)

    log.info(f"TP: {direction} {current} → opening {opposite} {close_size}, {remaining} will remain")
    capital.open_position(EPIC, opposite, close_size)

    send_telegram(
        f"🎯 <b>TP Hit — Partial Close (70%)</b>\n"
        f"Instrument: XAUUSD\n"
        f"Position: {direction}\n"
        f"Closed via {opposite}: {close_size} (70% of {current})\n"
        f"Remaining: {remaining}\n"
        f"⏳ Next TP closes another 70% of remaining, or wait for reversal"
    )

# ══════════════════════════════════════════════════════════════
# START
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info(f"Gold Trading Bot started on port {port}")
    app.run(host="0.0.0.0", port=port)
