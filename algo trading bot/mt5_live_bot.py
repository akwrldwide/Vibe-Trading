"""
MetaTrader 5 Option A: Pure Python Live Automated Trading Bot
Strategy: 9:30 AM NY Open 15m Range + 1m FVG Retracement Strategy
Position Sizing: Risk-Based Dynamic Sizing (% Account Equity Risk)
Broker: Deriv.com Limited
"""

import os
import sys
import time
import json
import logging
import threading
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Ensure current script directory and parent directory are in Python path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

# Load .env from script directory or parent directory
dotenv_path = os.path.join(SCRIPT_DIR, ".env")
if not os.path.exists(dotenv_path):
    dotenv_path = os.path.join(PARENT_DIR, ".env")
load_dotenv(dotenv_path)

# Optional FastAPI / Uvicorn import for webhooks
try:
    from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

from signal_engine import NYOpenICTSignalEngine
from telegram_notifier import TelegramNotifier
import journal_db

# Force UTF-8 encoding on standard output for Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Logging Configuration
file_handler = logging.FileHandler("mt5_live_bot.log", encoding="utf-8")
stream_handler = logging.StreamHandler(sys.stdout)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[file_handler, stream_handler]
)

# Environment Variables & Risk Settings
MT5_ACCOUNT = int(os.getenv("MT5_ACCOUNT", 0)) if os.getenv("MT5_ACCOUNT") else None
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
BROKER_NAME = os.getenv("BROKER_NAME", "Deriv (SVG) LLC")
MT5_PATH = os.getenv("MT5_PATH", "")
DEFAULT_LOT_SIZE = float(os.getenv("DEFAULT_LOT_SIZE", 0.01))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", 2.0))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", 2))
HTF_TIMEFRAME = os.getenv("HTF_TIMEFRAME", "15m").lower()
WATCHLIST_STR = os.getenv("WATCHLIST", "BTCUSD")
WATCHLIST = [s.strip() for s in WATCHLIST_STR.split(",") if s.strip()]
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 10.0))  # Poll every 10 seconds
MAGIC_NUMBER = 9301
WEBHOOK_PASSCODE = os.getenv("WEBHOOK_PASSCODE", "MY_SECRET_PASSCODE")
PORT = int(os.getenv("PORT", 8000))
LOG_FILE = "mt5_live_trades_log.json"

# Ensure SQLite journal database is initialized on server startup
try:
    journal_db.init_db()
except Exception as e:
    logging.warning(f"Journal DB init warning: {e}")

telegram = TelegramNotifier()

if FASTAPI_AVAILABLE:
    app = FastAPI(title="MT5 9:30 ICT Automated Bot + Webhook Bridge")

    @app.get("/")
    def read_root():
        return {"status": "online", "service": "MT5 9:30 ICT Automated Bot + Webhook Bridge"}

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

        logging.info(f"Incoming Webhook Payload: {data}")
        passcode = data.get("passcode")
        if passcode != WEBHOOK_PASSCODE:
            raise HTTPException(status_code=401, detail="Unauthorized: Passcode mismatch")

        action = data.get("action", "").upper()
        symbol = data.get("symbol", "")
        limit_price = float(data.get("limit_price", 0))
        stop_loss = float(data.get("stop_loss", 0))
        take_profit = float(data.get("take_profit", 0))
        volume = float(data.get("volume", 0)) if data.get("volume") else None

        if not symbol or action not in ["BUY", "SELL"] or limit_price <= 0:
            raise HTTPException(status_code=400, detail="Invalid trade parameters")

        background_tasks.add_task(execute_signal, symbol, action, limit_price, stop_loss, take_profit, volume)
        return {"status": "success", "action": action, "symbol": symbol, "message": "Webhook trade queued"}

    def run_webhook_server():
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")

def record_trade_log(trade_data: dict):
    trades = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except Exception:
            trades = []

    now_utc = datetime.now(timezone.utc)
    trade_data["timestamp"] = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    trades.append(trade_data)

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)

    # Persist directly into SQLite trading_journal.db
    try:
        status_str = trade_data.get("status", "SUBMITTED").upper()
        outcome = "OPEN"
        if status_str in ["SUBMITTED", "EXECUTED"]:
            outcome = "OPEN"
        elif status_str in ["FAILED", "ERROR"]:
            outcome = "FAILED"
        elif status_str in ["CANCELED"]:
            outcome = "CANCELED"

        db_trade = {
            "trade_date": now_utc.strftime("%Y-%m-%d"),
            "ny_time": now_utc.strftime("%I:%M %p"),
            "symbol": trade_data.get("symbol", "BTCUSD"),
            "action": trade_data.get("action", "BUY"),
            "setup_type": "9:30 NY ICT FVG Retracement",
            "htf_bias": "15M 50 EMA",
            "entry_price": float(trade_data.get("price", trade_data.get("entry_price", 0.0)) or 0.0),
            "stop_loss": float(trade_data.get("stop_loss", 0.0) or 0.0),
            "take_profit": float(trade_data.get("take_profit", 0.0) or 0.0),
            "rr_ratio": 2.0,
            "outcome": outcome,
            "notes": f"MT5 Ticket #{trade_data.get('ticket', 'N/A')} | Broker: {BROKER_NAME} | Status: {status_str}"
        }
        journal_db.add_trade(db_trade)
        logging.info(f"📖 Trade synced to SQLite journal database (trading_journal.db) for {trade_data.get('symbol')}")
    except Exception as e:
        logging.warning(f"Failed to record trade in SQLite journal database: {e}")

def init_mt5() -> bool:
    if not MT5_AVAILABLE:
        logging.error("MetaTrader5 python package is not installed.")
        return False

    init_kwargs = {}
    if MT5_PATH:
        init_kwargs["path"] = MT5_PATH

    if MT5_ACCOUNT and MT5_PASSWORD and MT5_SERVER:
        init_kwargs["login"] = MT5_ACCOUNT
        init_kwargs["password"] = MT5_PASSWORD
        init_kwargs["server"] = MT5_SERVER

    initialized = False
    try:
        initialized = mt5.initialize(**init_kwargs)
    except Exception as e:
        logging.warning(f"Explicit MT5 path/login init error: {e}. Trying fallback initialize()...")

    if not initialized:
        if not mt5.initialize():
            logging.error(f"MT5 initialize() failed, error code: {mt5.last_error()}")
            return False

    account_info = mt5.account_info()
    if account_info and account_info.login == MT5_ACCOUNT:
        company = account_info.company or BROKER_NAME
        logging.info(f"✅ Connected to MT5 Broker: {company} | Account #{account_info.login} ({account_info.server}) | Balance: ${account_info.balance:,.2f} | Equity: ${account_info.equity:,.2f}")
        return True

    if MT5_ACCOUNT and MT5_PASSWORD and MT5_SERVER:
        authorized = mt5.login(account=MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER)
        account_info = mt5.account_info()
        if not authorized and (account_info is None or account_info.login != MT5_ACCOUNT):
            current_login = account_info.login if account_info else "Unknown"
            err_code = mt5.last_error()
            logging.error(
                f"❌ Failed to log into requested MT5 Account #{MT5_ACCOUNT} on server '{MT5_SERVER}'. "
                f"MT5 is currently connected to Account #{current_login}. Error code: {err_code}\n"
                f"--> SOLUTION: Please open your Exness MetaTrader 5 terminal (or log into account #{MT5_ACCOUNT} on server '{MT5_SERVER}' in your MT5 terminal: File -> Login to Trade Account)."
            )
            return False

    account_info = mt5.account_info()
    if account_info is None:
        logging.error("Failed to retrieve MT5 account info.")
        return False

    if MT5_ACCOUNT and account_info.login != MT5_ACCOUNT:
        logging.error(
            f"❌ Connected MT5 account (#{account_info.login}) does not match target MT5_ACCOUNT (#{MT5_ACCOUNT}). "
            f"Please switch to account #{MT5_ACCOUNT} on server '{MT5_SERVER}' in your MetaTrader 5 desktop terminal."
        )
        return False

    company = account_info.company or BROKER_NAME
    logging.info(f"✅ Connected to MT5 Broker: {company} | Account #{account_info.login} ({account_info.server}) | Balance: ${account_info.balance:,.2f} | Equity: ${account_info.equity:,.2f}")
    return True

def get_supported_filling_mode(symbol_info) -> int:
    if symbol_info is None or not hasattr(symbol_info, "filling_mode"):
        return mt5.ORDER_FILLING_FOK

    mode = symbol_info.filling_mode
    if mode & 1:  # SYMBOL_FILLING_FOK
        return mt5.ORDER_FILLING_FOK
    elif mode & 2:  # SYMBOL_FILLING_IOC
        return mt5.ORDER_FILLING_IOC
    elif mode & 4:  # SYMBOL_FILLING_RETURN
        return mt5.ORDER_FILLING_RETURN
    return mt5.ORDER_FILLING_FOK

def calculate_dynamic_lot_size(symbol: str, entry_price: float, stop_loss: float, risk_percent: float = RISK_PERCENT) -> float:
    account_info = mt5.account_info()
    if account_info is None or account_info.equity <= 0:
        logging.warning("Account info unavailable for position sizing. Using DEFAULT_LOT_SIZE.")
        return DEFAULT_LOT_SIZE

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        logging.warning(f"Symbol info for {symbol} not found. Using DEFAULT_LOT_SIZE.")
        return DEFAULT_LOT_SIZE

    sl_distance = abs(entry_price - stop_loss)
    if sl_distance <= 0:
        logging.warning(f"Invalid SL distance ({sl_distance}) for {symbol}. Using DEFAULT_LOT_SIZE.")
        return DEFAULT_LOT_SIZE

    contract_size = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 100000.0
    equity = account_info.equity
    risk_amount = equity * (risk_percent / 100.0)

    loss_per_lot = sl_distance * contract_size
    if loss_per_lot <= 0:
        return DEFAULT_LOT_SIZE

    raw_lots = risk_amount / loss_per_lot

    volume_step = symbol_info.volume_step if symbol_info.volume_step > 0 else 0.01
    volume_min = symbol_info.volume_min if symbol_info.volume_min > 0 else 0.01
    volume_max = symbol_info.volume_max if symbol_info.volume_max > 0 else 100.0

    calculated_lots = round(raw_lots / volume_step) * volume_step
    calculated_lots = max(volume_min, min(volume_max, calculated_lots))
    calculated_lots = round(calculated_lots, 2)

    logging.info(
        f"📊 Risk Sizing [{symbol}]: Equity=${equity:,.2f} | Risk({risk_percent}%)=${risk_amount:,.2f} | "
        f"SL Distance={sl_distance:.5f} | Loss/Lot=${loss_per_lot:,.2f} => Calculated Lots: {calculated_lots}"
    )

    return calculated_lots

def get_mt5_server_offset_hours(symbol: str = "BTCUSD") -> float:
    """
    Calculates MT5 server time offset relative to UTC in hours.
    Compares latest tick timestamp returned by MT5 with current system UTC time.
    """
    env_offset = os.getenv("MT5_SERVER_OFFSET_HOURS")
    if env_offset is not None:
        try:
            return float(env_offset)
        except ValueError:
            pass

    if not MT5_AVAILABLE:
        return 0.0

    matched = match_mt5_symbol(symbol)
    tick = mt5.symbol_info_tick(matched)
    if tick and tick.time > 0:
        now_utc_ts = datetime.now(timezone.utc).timestamp()
        diff_sec = tick.time - now_utc_ts
        if abs(diff_sec) < 86400:
            offset_hrs = round(diff_sec / 3600.0)
            return float(offset_hrs)

    return 0.0

def fetch_m1_candles(symbol: str, count: int = 500, server_offset_hours: float = 0.0) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, count)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    if server_offset_hours != 0:
        df['time'] = df['time'] - int(server_offset_hours * 3600)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df.set_index('time', inplace=True)
    return df

def match_mt5_symbol(symbol: str) -> str:
    cleaned = symbol.replace("/", "").replace("USDT.P", "USD").replace("USDT", "USD")
    if mt5.symbol_info(cleaned) is not None:
        return cleaned

    for suffix in ["m", ".m", ".r", "m.r"]:
        alt = cleaned + suffix
        if mt5.symbol_info(alt) is not None:
            return alt

    alt_symbol = cleaned[:3] + "/" + cleaned[3:] if len(cleaned) == 6 else cleaned
    if mt5.symbol_info(alt_symbol) is not None:
        return alt_symbol

    return cleaned

def cancel_expired_pending_orders():
    now_ny = pd.Timestamp.now(tz="UTC").tz_convert("America/New_York")
    session_end_time = pd.to_datetime("11:30").time()

    if now_ny.time() > session_end_time:
        orders = mt5.orders_get(magic=MAGIC_NUMBER)
        if orders:
            for order in orders:
                cancel_request = {
                    "action": mt5.TRADE_ACTION_REMOVE,
                    "order": order.ticket
                }
                res = mt5.order_send(cancel_request)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    msg = f"🧹 Canceled expired pending order #{order.ticket} for {order.symbol} (Trading session ended at 11:30 AM EST)"
                    logging.info(msg)
                    if telegram.is_configured():
                        telegram.send_message(msg)
                    record_trade_log({
                        "status": "CANCELED",
                        "symbol": order.symbol,
                        "ticket": order.ticket,
                        "reason": "Session ended at 11:30 AM EST"
                    })

def execute_signal(symbol: str, action: str, entry_price: float, stop_loss: float, take_profit: float, custom_volume: float = None):
    matched_symbol = match_mt5_symbol(symbol)
    symbol_info = mt5.symbol_info(matched_symbol)
    if symbol_info is None:
        logging.error(f"Symbol {symbol} (matched: {matched_symbol}) not found in MT5 Market Watch.")
        return

    if not symbol_info.visible:
        mt5.symbol_select(matched_symbol, True)

    existing_orders = mt5.orders_get(symbol=matched_symbol, magic=MAGIC_NUMBER)
    if existing_orders:
        logging.info(f"Existing order active for {matched_symbol}. Skipping duplicate entry.")
        return

    tick = mt5.symbol_info_tick(matched_symbol)
    if tick is None:
        logging.error(f"Failed to fetch tick price for {matched_symbol}.")
        return

    # Validate Stop Loss integrity against live market price
    if action == "BUY" and stop_loss >= tick.ask:
        logging.warning(f"⚠️ Invalid BUY signal on {matched_symbol}: Stop Loss ({stop_loss}) >= Current Ask ({tick.ask}). Signal expired/invalid.")
        return
    elif action == "SELL" and stop_loss <= tick.bid:
        logging.warning(f"⚠️ Invalid SELL signal on {matched_symbol}: Stop Loss ({stop_loss}) <= Current Bid ({tick.bid}). Signal expired/invalid.")
        return

    volume = custom_volume if custom_volume and custom_volume > 0 else calculate_dynamic_lot_size(matched_symbol, entry_price, stop_loss, RISK_PERCENT)
    type_filling = get_supported_filling_mode(symbol_info)

    # Calculate MT5 minimum Stops Level distance to avoid Retcode 10015 Invalid Price
    stops_level_pts = symbol_info.trade_stops_level if symbol_info.trade_stops_level > 0 else 20
    stops_level_dist = stops_level_pts * symbol_info.point

    if action == "BUY":
        min_limit_price = tick.ask - stops_level_dist
        if entry_price >= min_limit_price:
            if tick.ask < take_profit and stop_loss < tick.ask:
                trade_action = mt5.TRADE_ACTION_DEAL
                order_type = mt5.ORDER_TYPE_BUY
                exec_price = tick.ask
                order_desc = "Market BUY"
            else:
                logging.warning(f"⚠️ Price ran away or invalid SL/TP for {matched_symbol} Market BUY fallback.")
                return
        else:
            trade_action = mt5.TRADE_ACTION_PENDING
            order_type = mt5.ORDER_TYPE_BUY_LIMIT
            exec_price = entry_price
            order_desc = "BUY LIMIT"

    elif action == "SELL":
        max_limit_price = tick.bid + stops_level_dist
        if entry_price <= max_limit_price:
            if tick.bid > take_profit and stop_loss > tick.bid:
                trade_action = mt5.TRADE_ACTION_DEAL
                order_type = mt5.ORDER_TYPE_SELL
                exec_price = tick.bid
                order_desc = "Market SELL"
            else:
                logging.warning(f"⚠️ Price ran away or invalid SL/TP for {matched_symbol} Market SELL fallback.")
                return
        else:
            trade_action = mt5.TRADE_ACTION_PENDING
            order_type = mt5.ORDER_TYPE_SELL_LIMIT
            exec_price = entry_price
            order_desc = "SELL LIMIT"
    else:
        logging.error(f"Unknown action {action}")
        return

    request = {
        "action": trade_action,
        "symbol": matched_symbol,
        "volume": volume,
        "type": order_type,
        "price": round(exec_price, symbol_info.digits),
        "sl": round(stop_loss, symbol_info.digits),
        "tp": round(take_profit, symbol_info.digits),
        "deviation": 10,
        "magic": MAGIC_NUMBER,
        "comment": "9:30 NY ICT Bot",
        "type_filling": type_filling,
    }

    if trade_action == mt5.TRADE_ACTION_PENDING:
        request["type_time"] = mt5.ORDER_TIME_GTC

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        msg = f"🚀 MT5 Order Placed! Broker: {BROKER_NAME} | {order_desc} {volume} {matched_symbol} @ {exec_price:.5f} | SL: {stop_loss:.5f} | TP: {take_profit:.5f} (Ticket #{result.order})"
        logging.info(msg)
        if telegram.is_configured():
            telegram.send_message(msg)
        record_trade_log({
            "status": "SUBMITTED",
            "broker": BROKER_NAME,
            "symbol": matched_symbol,
            "action": action,
            "order_desc": order_desc,
            "volume": volume,
            "price": exec_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "ticket": result.order
        })
    else:
        ret_code = result.retcode if result else 'None'
        err_detail = result.comment if result and hasattr(result, 'comment') else mt5.last_error()
        err_msg = f"❌ MT5 Order Failed for {matched_symbol} ({order_desc})! Retcode: {ret_code} | Error: {err_detail}"
        logging.error(err_msg)
        record_trade_log({
            "status": "FAILED",
            "broker": BROKER_NAME,
            "symbol": matched_symbol,
            "action": action,
            "order_desc": order_desc,
            "error": err_msg
        })

def run_live_bot():
    logging.info("=" * 60)
    logging.info(f"🤖 Starting MT5 Automated Trading Bot [{BROKER_NAME}]")
    logging.info(f"Target Symbols: {WATCHLIST}")
    logging.info(f"HTF Trend Filter: {HTF_TIMEFRAME.upper()} Chart 50 EMA Bias (Default: 15m)")
    logging.info(f"Risk Management: {RISK_PERCENT}% Equity Risk per Trade (Default Lot fallback: {DEFAULT_LOT_SIZE})")
    if FASTAPI_AVAILABLE:
        logging.info(f"Webhook Bridge: Active on port {PORT} (/webhook)")
    else:
        logging.info("Mode: Pure Standalone MT5 Python Bot")
    logging.info("=" * 60)

    if not init_mt5():
        logging.error("Failed to initialize MT5. Exiting bot.")
        return

    if FASTAPI_AVAILABLE:
        server_thread = threading.Thread(target=run_webhook_server, daemon=True)
        server_thread.start()

    server_offset_hours = get_mt5_server_offset_hours(WATCHLIST[0] if WATCHLIST else "BTCUSD")
    logging.info(f"Detected MT5 Broker Server Time Offset: {server_offset_hours:+.1f} hours relative to UTC")

    signal_engine = NYOpenICTSignalEngine(config={
        "rr_ratio": 2.0,
        "sl_buffer_pips": 2.0,
        "use_htf_filter": True,
        "htf_timeframe": HTF_TIMEFRAME,
        "max_trades_per_day": 2,
        "server_offset_hours": 0.0,
        "require_sl_for_second_trade": True,
    })
    last_processed_timestamps = {sym: None for sym in WATCHLIST}

    try:
        while True:
            cancel_expired_pending_orders()

            for symbol in WATCHLIST:
                matched_symbol = match_mt5_symbol(symbol)
                df_1m = fetch_m1_candles(matched_symbol, count=500, server_offset_hours=server_offset_hours)
                if df_1m.empty or len(df_1m) < 30:
                    continue

                df_signals = signal_engine.generate_signals(df_1m)
                if df_signals.empty:
                    continue

                # Process strictly the latest fully CLOSED 1-minute candle (iloc[-2]) to ensure bar is 100% completed
                if len(df_signals) < 2:
                    continue
                latest_candle = df_signals.iloc[-2]
                latest_time = df_signals.index[-2]


                if last_processed_timestamps.get(symbol) == latest_time:
                    continue

                last_processed_timestamps[symbol] = latest_time
                signal_val = latest_candle.get("signal", 0)

                if signal_val == 1:
                    action = "BUY"
                    entry_price = float(latest_candle["entry_price"])
                    stop_loss = float(latest_candle["stop_loss"])
                    take_profit = float(latest_candle["take_profit"])
                    logging.info(f"⚡ BUY Signal detected on {symbol} at {latest_time} | Entry: {entry_price:.5f} | SL: {stop_loss:.5f} | TP: {take_profit:.5f}")
                    execute_signal(symbol, action, entry_price, stop_loss, take_profit)

                elif signal_val == -1:
                    action = "SELL"
                    entry_price = float(latest_candle["entry_price"])
                    stop_loss = float(latest_candle["stop_loss"])
                    take_profit = float(latest_candle["take_profit"])
                    logging.info(f"⚡ SELL Signal detected on {symbol} at {latest_time} | Entry: {entry_price:.5f} | SL: {stop_loss:.5f} | TP: {take_profit:.5f}")
                    execute_signal(symbol, action, entry_price, stop_loss, take_profit)

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        logging.info("🛑 Stopping MT5 Live Trading Bot...")
    finally:
        mt5.shutdown()
        logging.info("MT5 connection closed.")

if __name__ == "__main__":
    run_live_bot()
