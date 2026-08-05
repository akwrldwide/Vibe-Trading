import time
import requests
import logging
from dotenv import load_dotenv

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Default Render URL (Can be changed in .env or via input)
RENDER_URL = "https://vibe-trading-crypto-bridge.onrender.com"
DEFAULT_LOT_SIZE = 0.1
POLL_INTERVAL = 2.0  # Poll Render every 2 seconds

def init_mt5():
    if not MT5_AVAILABLE:
        logging.error("MetaTrader5 python package is not installed.")
        return False
    if not mt5.initialize():
        logging.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False
    return True

def process_signal(signal: dict):
    signal_id = signal.get("id")
    action = signal.get("action")
    symbol = signal.get("symbol")

    logging.info(f"⚡ Processing Signal {signal_id}: {action} {symbol}")

    if not init_mt5():
        return

    if action == "CANCEL":
        orders = mt5.orders_get(magic=9301)
        canceled_count = 0
        if orders:
            for order in orders:
                if symbol == "ALL" or order.symbol == symbol or order.symbol.replace("/", "") == symbol:
                    cancel_req = {"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket}
                    res = mt5.order_send(cancel_req)
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        canceled_count += 1

        mt5.shutdown()
        # Confirm cancellation back to Render
        try:
            requests.post(f"{RENDER_URL}/signals/confirm", json={"signal_id": signal_id, "status": "CANCELED_IN_MT5", "mt5_ticket": canceled_count})
        except Exception as e:
            logging.error(f"Failed to confirm cancellation to Render: {e}")
        return

    # Handle BUY or SELL
    limit_price = float(signal.get("limit_price", 0))
    stop_loss = float(signal.get("stop_loss", 0))
    take_profit = float(signal.get("take_profit", 0))

    # Match MT5 Symbol format (e.g. EURUSD, EUR/USD, or EURUSD.r)
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        alt_sym = symbol[:3] + "/" + symbol[3:] if len(symbol) == 6 else symbol
        symbol_info = mt5.symbol_info(alt_sym)
        if symbol_info:
            symbol = alt_sym

    if symbol_info is None:
        logging.error(f"Symbol {symbol} not found in MT5!")
        mt5.shutdown()
        try:
            requests.post(f"{RENDER_URL}/signals/confirm", json={"signal_id": signal_id, "status": "FAILED_SYMBOL_NOT_FOUND", "error": f"Symbol {symbol} not in MT5"})
        except Exception as e:
            logging.error(f"Failed to confirm error to Render: {e}")
        return

    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)

    order_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": DEFAULT_LOT_SIZE,
        "type": order_type,
        "price": limit_price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": 10,
        "magic": 9301,
        "comment": "9:30 NY ICT Render Signal",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    logging.info(f"MT5 Order Execution Result: {result}")

    status = "EXECUTED_IN_MT5" if result and result.retcode == mt5.TRADE_RETCODE_DONE else "FAILED_IN_MT5"
    ticket = result.order if result and result.retcode == mt5.TRADE_RETCODE_DONE else None
    error_msg = str(result.comment) if result and result.retcode != mt5.TRADE_RETCODE_DONE else None

    mt5.shutdown()

    # Confirm status back to Render server
    try:
        requests.post(f"{RENDER_URL}/signals/confirm", json={"signal_id": signal_id, "status": status, "mt5_ticket": ticket, "error": error_msg})
        logging.info(f"✅ Signal {signal_id} confirmed back to Render as {status}")
    except Exception as e:
        logging.error(f"Failed to confirm status to Render: {e}")

def main():
    logging.info("🚀 Starting MT5 Render Sync Runner...")
    logging.info(f"Polling Render server at: {RENDER_URL}/signals/pending every {POLL_INTERVAL}s")
    
    while True:
        try:
            res = requests.get(f"{RENDER_URL}/signals/pending", timeout=5)
            if res.status_code == 200:
                pending_signals = res.json()
                if pending_signals:
                    logging.info(f"Found {len(pending_signals)} pending signal(s)!")
                    for sig in pending_signals:
                        process_signal(sig)
        except Exception as e:
            logging.warning(f"Connection check to Render: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
