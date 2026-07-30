import os
import json
import requests
from fastapi import FastAPI, HTTPException, Request
import uvicorn
from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
WEBHOOK_PASSCODE = os.getenv("WEBHOOK_PASSCODE", "MY_SECRET_PASSCODE")

app = FastAPI(title="9:30 NY ICT Strategy - Alpaca Paper Trading Webhook Bridge")

def get_alpaca_headers():
    return {
        "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ALPACA_API_KEY),
        "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", ALPACA_SECRET_KEY),
        "Content-Type": "application/json"
    }

@app.get("/")
def read_root():
    return {"status": "online", "message": "Alpaca Paper Trading Webhook Bridge Active"}

@app.get("/health")
def health_check():
    """Health check endpoint to keep Render web service awake during trading hours"""
    return {"status": "healthy", "service": "alpaca-bridge"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        body_bytes = await request.body()
        try:
            data = json.loads(body_bytes.decode())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")

    # Passcode Authentication
    passcode = data.get("passcode")
    if passcode != os.getenv("WEBHOOK_PASSCODE", WEBHOOK_PASSCODE):
        raise HTTPException(status_code=401, detail="Unauthorized: Passcode mismatch")

    symbol = data.get("symbol", "").replace("/", "") # Format e.g. EURUSD
    action = data.get("action", "").upper()
    limit_price = float(data.get("limit_price", 0))
    stop_loss = float(data.get("stop_loss", 0))
    take_profit = float(data.get("take_profit", 0))

    if not symbol or action not in ["BUY", "SELL"] or limit_price <= 0:
        raise HTTPException(status_code=400, detail="Invalid trade parameters")

    order_side = "buy" if action == "BUY" else "sell"

    # Construct Alpaca Bracket Limit Order Payload
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
    res = requests.post(url, json=order_payload, headers=get_alpaca_headers())

    if res.status_code not in [200, 201]:
        return {"status": "error", "alpaca_response": res.json()}

    order_data = res.json()

    return {
        "status": "success",
        "action": action,
        "symbol": symbol,
        "order_id": order_data.get("id"),
        "alpaca_response": order_data
    }

@app.post("/cancel_all")
def cancel_all_orders():
    """Safety Guard: Cancel all pending unfilled orders (used for 11:00 AM NY killzone & TP-touch)"""
    url = f"{os.getenv('ALPACA_BASE_URL', ALPACA_BASE_URL)}/v2/orders"
    res = requests.delete(url, headers=get_alpaca_headers())
    return {"status": "success", "message": "All open orders canceled", "response": res.json()}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
