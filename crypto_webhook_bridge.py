import os
import json
import logging
from datetime import datetime
import uuid
from fastapi import FastAPI, HTTPException, Request
import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title="9:30 NY ICT Strategy - Render Webhook & MT5 Relay Bridge")

WEBHOOK_PASSCODE = os.getenv("WEBHOOK_PASSCODE", "MY_SECRET_PASSCODE")
LOG_FILE = "mt5_relay_signals.json"

def load_signals():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_signals(signals):
    with open(LOG_FILE, "w") as f:
        json.dump(signals, f, indent=2)

@app.get("/")
def read_root():
    return {"status": "online", "service": "Render Webhook to MT5 Relay Bridge"}

@app.get("/health")
def health_check():
    """Health check endpoint to keep Render awake via UptimeRobot"""
    return {"status": "healthy", "service": "mt5-relay"}

@app.get("/signals/pending")
def get_pending_signals():
    """Endpoint polled by local MT5 runner to fetch unexecuted signals"""
    signals = load_signals()
    pending = [s for s in signals if s.get("status") == "PENDING"]
    return pending

@app.post("/signals/confirm")
async def confirm_signal_execution(request: Request):
    """Called by local MT5 runner to confirm an order was placed in MT5"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON format")

    signal_id = data.get("signal_id")
    mt5_ticket = data.get("mt5_ticket")
    status = data.get("status", "EXECUTED_IN_MT5")
    error = data.get("error")

    signals = load_signals()
    updated = False
    for s in signals:
        if s.get("id") == signal_id:
            s["status"] = status
            s["mt5_ticket"] = mt5_ticket
            s["mt5_error"] = error
            s["executed_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            updated = True
            break

    if updated:
        save_signals(signals)
        return {"status": "success", "message": f"Signal {signal_id} marked as {status}"}
    
    raise HTTPException(status_code=404, detail="Signal ID not found")

@app.get("/trades")
def get_trades():
    return load_signals()

@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        body_bytes = await request.body()
        try:
            data = json.loads(body_bytes.decode())
        except Exception as e:
            logging.error(f"Failed to parse JSON: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON format")

    logging.info(f"Incoming Webhook Payload: {data}")

    # Passcode Authentication
    passcode = data.get("passcode")
    if passcode != WEBHOOK_PASSCODE:
        logging.warning(f"Unauthorized passcode: {passcode}")
        raise HTTPException(status_code=401, detail="Unauthorized passcode mismatch")

    action = data.get("action", "").upper()

    if action == "CANCEL":
        symbol = data.get("symbol", "").replace("/", "").replace("PERP", "").replace(".P", "")
        signal_entry = {
            "id": str(uuid.uuid4()),
            "action": "CANCEL",
            "symbol": symbol if symbol else "ALL",
            "status": "PENDING",
            "received_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        }
        signals = load_signals()
        signals.append(signal_entry)
        save_signals(signals)
        return {"status": "success", "message": "Cancellation signal queued for MT5", "signal_id": signal_entry["id"]}

    symbol = data.get("symbol", "").replace("/", "").replace("PERP", "").replace(".P", "")
    limit_price = float(data.get("limit_price", data.get("price", 0)))
    stop_loss = float(data.get("stop_loss", 0))
    take_profit = float(data.get("take_profit", 0))

    if not symbol or action not in ["BUY", "SELL"] or limit_price <= 0:
        raise HTTPException(status_code=400, detail="Invalid trade parameters in payload")

    signal_entry = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "action": action,
        "limit_price": limit_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "status": "PENDING",
        "received_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    }

    signals = load_signals()
    signals.append(signal_entry)
    save_signals(signals)

    logging.info(f"✅ Webhook Signal Received & Queued for MT5: {action} {symbol} @ {limit_price}")

    return {
        "status": "success",
        "message": "Signal queued for MT5 execution",
        "signal": signal_entry
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
