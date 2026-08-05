import os
import json
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
import uvicorn
from dotenv import load_dotenv

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

WEBHOOK_PASSCODE = os.getenv("WEBHOOK_PASSCODE", "MY_SECRET_PASSCODE")
MT5_ACCOUNT = int(os.getenv("MT5_ACCOUNT", 0)) if os.getenv("MT5_ACCOUNT") else None
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
DEFAULT_LOT_SIZE = float(os.getenv("DEFAULT_LOT_SIZE", 0.1))
LOG_FILE = "mt5_trades_log.json"

app = FastAPI(title="9:30 NY ICT Strategy - MetaTrader 5 Webhook Bridge")

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

def init_mt5():
    if not MT5_AVAILABLE:
        logging.error("MetaTrader5 package is not installed.")
        return False

    if not mt5.initialize():
        logging.error(f"MT5 initialize() failed, error code: {mt5.last_error()}")
        return False

    if MT5_ACCOUNT and MT5_PASSWORD and MT5_SERVER:
        authorized = mt5.login(account=MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER)
        if not authorized:
            logging.error(f"Failed to log into MT5 account {MT5_ACCOUNT}, error code: {mt5.last_error()}")
            return False

    logging.info("✅ MetaTrader 5 initialized successfully!")
    return True

def execute_mt5_trade(symbol: str, action: str, limit_price: float, stop_loss: float, take_profit: float, volume: float = DEFAULT_LOT_SIZE):
    if not init_mt5():
        record_trade_log({"status": "ERROR", "error": "MT5 Terminal initialization failed", "symbol": symbol, "action": action})
        return

    # Check if symbol exists in Market Watch
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        # Try adding slash or suffix if broker uses special naming e.g. EURUSD.r or EUR/USD
        alt_symbol = symbol[:3] + "/" + symbol[3:] if len(symbol) == 6 else symbol
        symbol_info = mt5.symbol_info(alt_symbol)
        if symbol_info:
            symbol = alt_symbol
        else:
            logging.error(f"Symbol {symbol} not found in MT5 Market Watch.")
            record_trade_log({"status": "ERROR", "error": f"Symbol {symbol} not found in MT5", "symbol": symbol})
            mt5.shutdown()
            return

    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)

    order_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": limit_price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": 10,
        "magic": 9301,
        "comment": "9:30 NY ICT Signal",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    logging.info(f"MT5 Order Send Result: {result}")

    trade_log = {
        "symbol": symbol,
        "action": action,
        "limit_price": limit_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "volume": volume,
        "mt5_retcode": result.retcode if result else None,
        "mt5_order": result.order if result and result.retcode == mt5.TRADE_RETCODE_DONE else None,
        "status": "SUBMITTED" if result and result.retcode == mt5.TRADE_RETCODE_DONE else "FAILED"
    }

    record_trade_log(trade_log)
    mt5.shutdown()

def execute_mt5_cancel(symbol: str = None):
    if not init_mt5():
        return

    orders = mt5.orders_get(magic=9301)
    if orders:
        for order in orders:
            if symbol is None or order.symbol == symbol:
                cancel_request = {
                    "action": mt5.TRADE_ACTION_REMOVE,
                    "order": order.ticket
                }
                res = mt5.order_send(cancel_request)
                logging.info(f"Canceled MT5 order #{order.ticket}: {res}")

    mt5.shutdown()

@app.get("/")
def read_root():
    return {"status": "online", "service": "MetaTrader 5 Webhook Bridge Active"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "mt5-bridge"}

@app.get("/trades")
def get_trades():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    return []

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

    logging.info(f"Incoming MT5 Webhook Payload: {data}")

    passcode = data.get("passcode")
    if passcode != os.getenv("WEBHOOK_PASSCODE", WEBHOOK_PASSCODE):
        raise HTTPException(status_code=401, detail="Unauthorized: Passcode mismatch")

    action = data.get("action", "").upper()

    if action == "CANCEL":
        symbol = data.get("symbol", "").replace("/", "")
        background_tasks.add_task(execute_mt5_cancel, symbol if symbol else None)
        return {"status": "accepted", "action": "CANCEL", "message": "MT5 cancellation queued"}

    symbol = data.get("symbol", "").replace("/", "")
    limit_price = float(data.get("limit_price", 0))
    stop_loss = float(data.get("stop_loss", 0))
    take_profit = float(data.get("take_profit", 0))
    volume = float(data.get("volume", DEFAULT_LOT_SIZE))

    if not symbol or action not in ["BUY", "SELL"] or limit_price <= 0:
        raise HTTPException(status_code=400, detail="Invalid trade parameters")

    background_tasks.add_task(execute_mt5_trade, symbol, action, limit_price, stop_loss, take_profit, volume)

    return {
        "status": "success",
        "action": action,
        "symbol": symbol,
        "message": "MT5 trade queued for execution"
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
