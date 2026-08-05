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

FXOPEN_API_URL = os.getenv("FXOPEN_API_URL", "https://ttlivewebapi.fxopen.net/api/v1")
FXOPEN_API_ID = os.getenv("FXOPEN_API_ID", "3656df80-f153-479d-b614-d7746780a2a6")
FXOPEN_API_KEY = os.getenv("FXOPEN_API_KEY", "rJMdhhNPehKTk2Dy")
FXOPEN_SECRET_KEY = os.getenv("FXOPEN_SECRET_KEY", "ER5qj6Ea32CK7K4Y2eADzd2ZXtsRtTHTpXmnhZjS52BjChqnwzewSkgB3dC2MYQf")
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
    # Standardize Forex Symbol Format e.g. EURUSD -> EUR/USD
    formatted_symbol = symbol[:3] + "/" + symbol[3:] if len(symbol) == 6 and "/" not in symbol else symbol
    order_type = "Limit"
    order_side = "Buy" if action == "BUY" else "Sell"
    
    payload = {
        "Symbol": formatted_symbol,
        "Type": order_type,
        "Side": order_side,
        "Price": limit_price,
        "StopLoss": stop_loss,
        "TakeProfit": take_profit,
        "Amount": 10000  # 0.1 Lot
    }
    
    body_str = json.dumps(payload)
    url = f"{FXOPEN_API_URL}/trade"
    
    trade_record = {
        "symbol": formatted_symbol,
        "action": action,
        "limit_price": limit_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "broker": "FXOpen / Forex.Game"
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

@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception:
        body_bytes = await request.body()
        try:
            data = json.loads(body_bytes.decode())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")

    logging.info(f"Incoming FXOpen Webhook Payload: {data}")

    passcode = data.get("passcode")
    if passcode != os.getenv("WEBHOOK_PASSCODE", WEBHOOK_PASSCODE):
        raise HTTPException(status_code=401, detail="Unauthorized: Passcode mismatch")

    action = data.get("action", "").upper()

    if action == "CANCEL":
        symbol = data.get("symbol", "").replace("/", "")
        background_tasks.add_task(execute_fxopen_cancel, symbol if symbol else None)
        return {"status": "accepted", "action": "CANCEL", "message": "Cancellation request queued"}

    symbol = data.get("symbol", "").replace("/", "")
    limit_price = float(data.get("limit_price", data.get("price", 0)))
    stop_loss = float(data.get("stop_loss", 0))
    take_profit = float(data.get("take_profit", 0))

    if not symbol or action not in ["BUY", "SELL"] or limit_price <= 0:
        raise HTTPException(status_code=400, detail="Invalid trade parameters")

    background_tasks.add_task(execute_fxopen_trade, symbol, action, limit_price, stop_loss, take_profit)

    return {
        "status": "success",
        "action": action,
        "symbol": symbol,
        "message": "FXOpen trade queued for cloud execution"
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
