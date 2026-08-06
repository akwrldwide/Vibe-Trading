import os
import json
import time
import hmac
import hashlib
import base64
import logging
from datetime import datetime
import requests
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title="9:30 NY ICT Strategy - FXOpen / Forex.Game Cloud Webhook Bridge")

FXOPEN_API_URL = os.getenv("FXOPEN_API_URL", "https://marginalttdemowebapi.fxopen.net/api/v1")
FXOPEN_WS_URL = os.getenv("FXOPEN_WS_URL", "wss://marginalttdemowebapi.fxopen.net/trade")
FXOPEN_API_ID = os.getenv("FXOPEN_API_ID", "d9232fde-b781-4107-a01a-d8c22f5eb264")
FXOPEN_API_KEY = os.getenv("FXOPEN_API_KEY", "aAYxNB8RXrDRb4PD")
FXOPEN_SECRET_KEY = os.getenv("FXOPEN_SECRET_KEY", "8AHHc2KWw59gXhzkzWwCJKdeYkXb7Sf9349qJpyN5ScPTc5PcJ9ADghxxCW3dHYM")
WEBHOOK_PASSCODE = os.getenv("WEBHOOK_PASSCODE", "MY_SECRET_PASSCODE")
LOG_FILE = "fxopen_paper_trades.json"

def record_trade_log(trade_data: dict):
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

def generate_fxopen_headers(method: str, full_url: str, body_str: str = ""):
    timestamp = str(int(time.time() * 1000))
    sig_payload = f"{timestamp}{FXOPEN_API_ID}{FXOPEN_API_KEY}{method.upper()}{full_url}{body_str}"
    
    signature = hmac.new(
        FXOPEN_SECRET_KEY.encode("ascii"),
        sig_payload.encode("ascii"),
        hashlib.sha256
    ).digest()
    
    b64_sig = base64.b64encode(signature).decode("ascii")
    auth_header = f"HMAC {FXOPEN_API_ID}:{FXOPEN_API_KEY}:{timestamp}:{b64_sig}"
    
    return {
        "Authorization": auth_header,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

@app.get("/")
def read_root():
    return {"status": "online", "service": "FXOpen / Forex.Game Cloud Webhook Bridge Active"}

@app.get("/health")
def health_check():
    """Health check endpoint to keep Render awake 24/7 via UptimeRobot"""
    return {"status": "healthy", "service": "fxopen-bridge"}

@app.get("/trades")
def get_trades():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    return []

def execute_fxopen_trade(symbol: str, action: str, limit_price: float, stop_loss: float, take_profit: float):
    # FXOpen Symbol format e.g. EURUSD, GBPUSD (no slash)
    formatted_symbol = symbol.replace("/", "").upper()
    order_type = "Limit"
    order_side = "Buy" if action == "BUY" else "Sell"
    
    payload = {
        "Symbol": formatted_symbol,
        "Type": order_type,
        "Side": order_side,
        "Price": limit_price,
        "Amount": 10000  # 0.1 Lot (10,000 units)
    }
    
    body_str = json.dumps(payload)
    url = f"{FXOPEN_API_URL}/trade"
    
    trade_record = {
        "symbol": formatted_symbol,
        "action": action,
        "limit_price": limit_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "broker": "FXOpen TickTrader Demo"
    }

    try:
        headers = generate_fxopen_headers("POST", url, body_str)
        res = requests.post(url, data=body_str, headers=headers, timeout=10)
        logging.info(f"FXOpen API Response [{res.status_code}]: {res.text}")
        
        if res.status_code in [200, 201]:
            trade_record["status"] = "EXECUTED_ON_FXOPEN"
            trade_record["response"] = res.json()
        else:
            trade_record["status"] = "REJECTED_BY_FXOPEN"
            trade_record["error"] = res.text

    except Exception as e:
        logging.error(f"Error submitting trade to FXOpen: {e}")
        trade_record["status"] = "ERROR"
        trade_record["error"] = str(e)

    record_trade_log(trade_record)

def execute_fxopen_cancel(symbol: str = None):
    trade_record = {"action": "CANCEL", "symbol": symbol if symbol else "ALL"}
    url = f"{FXOPEN_API_URL}/trade"
    try:
        headers = generate_fxopen_headers("DELETE", url)
        res = requests.delete(url, headers=headers, timeout=10)
        trade_record["status"] = "CANCELED_ON_FXOPEN"
        trade_record["response"] = res.json() if res.content else res.text
    except Exception as e:
        trade_record["status"] = "ERROR"
        trade_record["error"] = str(e)

    record_trade_log(trade_record)

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
        logging.warning(f"Passcode mismatch: {passcode}. Sending alert to Telegram.")
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
        background_tasks.add_task(execute_fxopen_cancel, symbol if symbol else None)
        return {"status": "accepted", "action": "CANCEL", "message": "Cancellation request queued"}

    if not symbol or action not in ["BUY", "SELL"] or limit_price <= 0:
        telegram.send_generic_alert(f"Alert: {data}")
        return {"status": "success", "message": "Alert sent to Telegram"}

    background_tasks.add_task(execute_fxopen_trade, symbol, action, limit_price, stop_loss, take_profit)
    telegram.send_signal_alert(symbol=symbol, action=action, price=limit_price, stop_loss=stop_loss, take_profit=take_profit)

    return {
        "status": "success",
        "action": action,
        "symbol": symbol,
        "message": "Trade queued and Telegram alert sent"
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
