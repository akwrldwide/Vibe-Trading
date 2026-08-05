import os
import json
import logging
from datetime import datetime
import requests
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
WEBHOOK_PASSCODE = os.getenv("WEBHOOK_PASSCODE", "MY_SECRET_PASSCODE")
LOG_FILE = "paper_trades_log.json"

app = FastAPI(title="9:30 NY ICT Strategy - Webhook & Paper Trading Bridge")

def get_alpaca_headers():
    return {
        "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ALPACA_API_KEY),
        "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", ALPACA_SECRET_KEY),
        "Content-Type": "application/json"
    }

def record_local_paper_trade(trade_data: dict):
    trades = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                trades = json.load(f)
        except Exception:
            trades = []

    trade_data["timestamp"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    trades.append(trade_data)

    with open(LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2)

@app.get("/")
def read_root():
    return {"status": "online", "message": "Paper Trading Webhook Bridge Active"}

@app.get("/health")
def health_check():
    """Health check endpoint to keep Render web service awake during trading hours"""
    return {"status": "healthy", "service": "alpaca-bridge"}

@app.get("/trades")
def get_recorded_trades():
    """Endpoint to inspect recorded paper trades & Alpaca response logs"""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    return []

def execute_alpaca_trade(symbol: str, action: str, limit_price: float, stop_loss: float, take_profit: float):
    order_side = "buy" if action == "BUY" else "sell"
    order_payload = {
        "symbol": symbol,
        "qty": 1,
        "side": order_side,
        "type": "limit",
        "time_in_force": "gtc",
        "limit_price": str(limit_price),
        "order_class": "bracket",
        "take_profit": {
            "limit_price": str(take_profit)
        },
        "stop_loss": {
            "stop_price": str(stop_loss)
        }
    }
    url = f"{os.getenv('ALPACA_BASE_URL', ALPACA_BASE_URL)}/v2/orders"
    
    trade_record = {
        "symbol": symbol,
        "action": action,
        "limit_price": limit_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "alpaca_submitted": False
    }

    try:
        res = requests.post(url, json=order_payload, headers=get_alpaca_headers(), timeout=10)
        logging.info(f"Alpaca Order Response [{res.status_code}]: {res.text}")
        
        if res.status_code in [200, 201]:
            trade_record["alpaca_submitted"] = True
            trade_record["alpaca_order_id"] = res.json().get("id")
            trade_record["status"] = "SUBMITTED_TO_ALPACA"
        else:
            trade_record["status"] = "REJECTED_BY_ALPACA"
            trade_record["error"] = res.json() if res.content else res.text
            logging.warning(f"⚠️ Alpaca Order Rejected for {symbol}: {res.text}")

    except Exception as e:
        logging.error(f"Error submitting order to Alpaca: {e}")
        trade_record["status"] = "ERROR"
        trade_record["error"] = str(e)

    record_local_paper_trade(trade_record)

def execute_alpaca_cancel():
    url = f"{os.getenv('ALPACA_BASE_URL', ALPACA_BASE_URL)}/v2/orders"
    try:
        res = requests.delete(url, headers=get_alpaca_headers(), timeout=10)
        logging.info(f"Alpaca Cancel All Response [{res.status_code}]: {res.text}")
        record_local_paper_trade({"action": "CANCEL_ALL", "response": res.json() if res.content else {}})
    except Exception as e:
        logging.error(f"Error canceling orders on Alpaca: {e}")

from telegram_notifier import TelegramNotifier
telegram = TelegramNotifier()

@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = ""
    data = {}

    try:
        body_bytes = await request.body()
        raw_body = body_bytes.decode("utf-8", errors="ignore").strip()
        if raw_body.startswith("{") or raw_body.startswith("["):
            data = json.loads(raw_body)
    except Exception as e:
        logging.warning(f"Non-JSON raw body received: {raw_body}")

    logging.info(f"Incoming Webhook Raw: {raw_body}")

    # Plain text / generic alert handling
    if not isinstance(data, dict) or not data:
        if raw_body:
            telegram.send_generic_alert(raw_body)
            return {"status": "success", "message": "Plain text alert sent to Telegram", "raw": raw_body}
        raise HTTPException(status_code=400, detail="Empty request payload")

    action = data.get("action", "").upper()
    if not action:
        text_content = data.get("text", data.get("message", str(data)))
        telegram.send_generic_alert(text_content)
        return {"status": "success", "message": "Generic alert sent to Telegram", "data": data}

    passcode = data.get("passcode")
    if passcode != os.getenv("WEBHOOK_PASSCODE", WEBHOOK_PASSCODE):
        logging.warning(f"Passcode mismatch: {passcode}. Forwarding alert to Telegram.")
        telegram.send_generic_alert(str(data))
        return {"status": "success", "message": "Alert sent to Telegram (Passcode unauthenticated for auto-trading)"}

    symbol = data.get("symbol", "").replace("/", "")
    limit_price = float(data.get("limit_price", data.get("price", 0)))
    stop_loss = float(data.get("stop_loss", 0))
    take_profit = float(data.get("take_profit", 0))

    if action == "BREAKEVEN":
        telegram.send_breakeven_alert(symbol=symbol, entry_price=limit_price, new_sl=stop_loss)
        return {"status": "success", "message": "Breakeven alert sent to Telegram"}

    if action == "CANCEL":
        background_tasks.add_task(execute_alpaca_cancel)
        return {"status": "accepted", "action": "CANCEL", "message": "Cancellation request queued"}

    if not symbol or action not in ["BUY", "SELL"] or limit_price <= 0:
        telegram.send_generic_alert(f"Alert: {data}")
        return {"status": "success", "message": "Alert sent to Telegram"}

    background_tasks.add_task(execute_alpaca_trade, symbol, action, limit_price, stop_loss, take_profit)
    telegram.send_signal_alert(symbol=symbol, action=action, price=limit_price, stop_loss=stop_loss, take_profit=take_profit)

    return {
        "status": "success",
        "action": action,
        "symbol": symbol,
        "message": "Trade payload accepted and queued"
    }


@app.post("/cancel_all")
def cancel_all_orders():
    """Safety Guard: Cancel all pending unfilled orders"""
    execute_alpaca_cancel()
    return {"status": "success", "message": "All open orders canceled"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
