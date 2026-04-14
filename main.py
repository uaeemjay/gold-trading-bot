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
EPIC       = "GOLD"    # XAUUSD instrument code on Capital.com
TRADE_SIZE = 2         # Quantity per trade

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

def calc_pnl(direction, entry_price, exit_price, size):
    """Calculate P&L and return a formatted string."""
    if direction == "BUY":
        pnl = (exit_price - entry_price) * size
    else:
        pnl = (entry_price - exit_price) * size
    sign = "+" if pnl >= 0 else ""
    return pnl, f"{sign}{pnl:.2f}"


def handle_buy():
    """
    BUY signal logic:
    - If SELL positions open → close them all, then open BUY
    - If BUY positions open → stack another BUY on top
    - If no positions → open new BUY
    """
    capital = get_capital()
    positions     = capital.get_positions(EPIC)
    sell_positions = [p for p in positions if p["direction"] == "SELL"]
    buy_positions  = [p for p in positions if p["direction"] == "BUY"]

    # Close any opposite (SELL) positions first
    if sell_positions:
        exit_price = capital.get_price(EPIC)
        log.info(f"BUY signal: closing {len(sell_positions)} SELL position(s) first")
        pnl_lines = []
        for pos in sell_positions:
            capital.close_position(pos["dealId"])
            _, pnl_str = calc_pnl("SELL", pos["level"], exit_price, pos["size"])
            pnl_lines.append(f"  Entry: {pos['level']} | Exit: {exit_price:.2f} | P&L: {pnl_str}")
            log.info(f"  Closed SELL deal {pos['dealId']} | P&L: {pnl_str}")
        send_telegram(
            f"🔄 <b>Reversed to BUY</b>\n"
            f"Closed {len(sell_positions)} SELL position(s) on XAUUSD\n"
            + "\n".join(pnl_lines)
        )

    # Determine reason for Telegram message
    if buy_positions:
        reason = f"Stacking — {len(buy_positions)} BUY already open"
    elif sell_positions:
        reason = "Signal reversed from SELL → BUY"
    else:
        reason = "New BUY signal"

    # Open BUY trade
    result = capital.open_position(EPIC, "BUY", TRADE_SIZE)
    price  = result.get("level", "market price")
    log.info(f"Opened BUY {TRADE_SIZE} x {EPIC} | {reason}")
    send_telegram(
        f"📈 <b>BUY opened</b>\n"
        f"Instrument: XAUUSD\n"
        f"Quantity: {TRADE_SIZE}\n"
        f"Price: {price}\n"
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
    positions     = capital.get_positions(EPIC)
    buy_positions  = [p for p in positions if p["direction"] == "BUY"]
    sell_positions = [p for p in positions if p["direction"] == "SELL"]

    # Close any opposite (BUY) positions first
    if buy_positions:
        exit_price = capital.get_price(EPIC)
        log.info(f"SELL signal: closing {len(buy_positions)} BUY position(s) first")
        pnl_lines = []
        for pos in buy_positions:
            capital.close_position(pos["dealId"])
            _, pnl_str = calc_pnl("BUY", pos["level"], exit_price, pos["size"])
            pnl_lines.append(f"  Entry: {pos['level']} | Exit: {exit_price:.2f} | P&L: {pnl_str}")
            log.info(f"  Closed BUY deal {pos['dealId']} | P&L: {pnl_str}")
        send_telegram(
            f"🔄 <b>Reversed to SELL</b>\n"
            f"Closed {len(buy_positions)} BUY position(s) on XAUUSD\n"
            + "\n".join(pnl_lines)
        )

    # Determine reason
    if sell_positions:
        reason = f"Stacking — {len(sell_positions)} SELL already open"
    elif buy_positions:
        reason = "Signal reversed from BUY → SELL"
    else:
        reason = "New SELL signal"

    # Open SELL trade
    result = capital.open_position(EPIC, "SELL", TRADE_SIZE)
    price  = result.get("level", "market price")
    log.info(f"Opened SELL {TRADE_SIZE} x {EPIC} | {reason}")
    send_telegram(
        f"📉 <b>SELL opened</b>\n"
        f"Instrument: XAUUSD\n"
        f"Quantity: {TRADE_SIZE}\n"
        f"Price: {price}\n"
        f"Reason: {reason}"
    )


def handle_tp():
    """
    TP signal: close ALL open positions immediately, then wait.
    """
    capital = get_capital()
    positions = capital.get_positions(EPIC)

    if not positions:
        log.info("TP signal received — no open positions to close")
        return

    exit_price = capital.get_price(EPIC)
    log.info(f"TP signal: closing all {len(positions)} open position(s)")
    pnl_lines = []
    total_pnl = 0
    for pos in positions:
        capital.close_position(pos["dealId"])
        pnl, pnl_str = calc_pnl(pos["direction"], pos["level"], exit_price, pos["size"])
        total_pnl += pnl
        pnl_lines.append(f"  {pos['direction']} | Entry: {pos['level']} | Exit: {exit_price:.2f} | P&L: {pnl_str}")
        log.info(f"  Closed {pos['direction']} deal {pos['dealId']} | P&L: {pnl_str}")

    total_sign = "+" if total_pnl >= 0 else ""
    send_telegram(
        f"🎯 <b>TP Hit — All positions closed</b>\n"
        f"Instrument: XAUUSD\n"
        + "\n".join(pnl_lines) + "\n"
        f"<b>Total P&L: {total_sign}{total_pnl:.2f}</b>\n"
        f"⏳ Waiting for next signal..."
    )


# ══════════════════════════════════════════════════════════════
# START
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info(f"Gold Trading Bot started on port {port}")
    app.run(host="0.0.0.0", port=port)
