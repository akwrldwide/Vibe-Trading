import sqlite3
import os
import json
import csv
import io
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

DB_FILE = os.path.join(os.path.dirname(__file__), "trading_journal.db")

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        ny_time TEXT,
        symbol TEXT NOT NULL,
        action TEXT NOT NULL, -- BUY / SELL
        setup_type TEXT DEFAULT 'OR Breakout (Candle 2 FVG)',
        htf_bias TEXT DEFAULT 'BEARISH',
        entry_price REAL NOT NULL,
        stop_loss REAL NOT NULL,
        take_profit REAL NOT NULL,
        exit_price REAL,
        rr_ratio REAL DEFAULT 2.0,
        outcome TEXT DEFAULT 'OPEN', -- WIN, LOSS, BREAK-EVEN, OPEN
        realized_r REAL DEFAULT 0.0,
        realized_pnl REAL DEFAULT 0.0,
        or_high REAL,
        or_low REAL,
        news_guarded INTEGER DEFAULT 0,
        notes TEXT,
        screenshot_url TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)
    conn.commit()

    # Seed with initial realistic trades if database is empty
    cursor.execute("SELECT COUNT(*) FROM trades")
    if cursor.fetchone()[0] == 0:
        seed_trades = [
            {
                "trade_date": "2026-08-11",
                "ny_time": "10:20 AM",
                "symbol": "BTCUSDT",
                "action": "SELL",
                "setup_type": "OR Breakout (Candle 2 FVG)",
                "htf_bias": "BEARISH",
                "entry_price": 64012.45,
                "stop_loss": 64063.80,
                "take_profit": 63909.75,
                "exit_price": 63909.75,
                "rr_ratio": 2.0,
                "outcome": "WIN",
                "realized_r": 2.0,
                "realized_pnl": 205.40,
                "or_high": 64216.00,
                "or_low": 64012.30,
                "news_guarded": 0,
                "notes": "Textbook 15m OR breakdown with clean 3-bar FVG displacement on Candle 2. Direct hit to 2R target with zero drawdown."
            },
            {
                "trade_date": "2026-08-12",
                "ny_time": "10:15 AM",
                "symbol": "BTCUSDT",
                "action": "SELL",
                "setup_type": "OR Retest / Continuation FVG",
                "htf_bias": "BEARISH",
                "entry_price": 63806.15,
                "stop_loss": 63879.10,
                "take_profit": 63660.25,
                "exit_price": 63660.25,
                "rr_ratio": 2.0,
                "outcome": "WIN",
                "realized_r": 2.0,
                "realized_pnl": 291.80,
                "or_high": 63990.80,
                "or_low": 63834.70,
                "news_guarded": 1,
                "notes": "Waited past 09:55-10:05 news guard window. Price retested broken OR Low at 63,834.7 and formed clean bearish rejection FVG. Smashed 2R TP."
            },
            {
                "trade_date": "2026-08-13",
                "ny_time": "10:33 AM",
                "symbol": "BTCUSDT",
                "action": "BUY",
                "setup_type": "OR Breakout (Candle 2 FVG)",
                "htf_bias": "BULLISH",
                "entry_price": 63816.75,
                "stop_loss": 63738.10,
                "take_profit": 63974.05,
                "exit_price": 63974.05,
                "rr_ratio": 2.0,
                "outcome": "WIN",
                "realized_r": 2.0,
                "realized_pnl": 314.60,
                "or_high": 63830.40,
                "or_low": 63523.50,
                "news_guarded": 0,
                "notes": "Bullish Opening Range breakout at 15:33 (10:33 NY). First FVG displacement above 63,830.4 OR High. Clean expansion straight to TP."
            },
            {
                "trade_date": "2026-08-14",
                "ny_time": "10:55 AM",
                "symbol": "BTCUSDT",
                "action": "SELL",
                "setup_type": "OR Breakout (Candle 2 FVG)",
                "htf_bias": "BEARISH",
                "entry_price": 62620.15,
                "stop_loss": 62648.60,
                "take_profit": 62563.25,
                "exit_price": 62648.60,
                "rr_ratio": 2.0,
                "outcome": "LOSS",
                "realized_r": -1.0,
                "realized_pnl": -56.90,
                "or_high": 62792.70,
                "or_low": 62573.50,
                "news_guarded": 0,
                "notes": "Judas Swing / Liquidity Sweep Day. Price swept OR Low multiple times without holding, reversed back above 50% midpoint and expanded to OR High. Stopped out."
            }
        ]
        for t in seed_trades:
            add_trade(t)
    conn.close()

def add_trade(trade_data: Dict[str, Any]) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    # Auto-calculate outcome/realized_r if not set
    outcome = trade_data.get("outcome", "OPEN")
    realized_r = trade_data.get("realized_r", 0.0)
    rr_ratio = float(trade_data.get("rr_ratio", 2.0) or 2.0)
    
    if outcome == "WIN" and (realized_r == 0.0 or realized_r is None):
        realized_r = rr_ratio
    elif outcome == "LOSS" and (realized_r == 0.0 or realized_r is None):
        realized_r = -1.0
    elif outcome == "BREAK-EVEN":
        realized_r = 0.0

    cursor.execute("""
    INSERT INTO trades (
        trade_date, ny_time, symbol, action, setup_type, htf_bias,
        entry_price, stop_loss, take_profit, exit_price, rr_ratio,
        outcome, realized_r, realized_pnl, or_high, or_low,
        news_guarded, notes, screenshot_url, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade_data.get("trade_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        trade_data.get("ny_time", "09:45 AM"),
        trade_data.get("symbol", "BTCUSDT").upper(),
        trade_data.get("action", "BUY").upper(),
        trade_data.get("setup_type", "OR Breakout (Candle 2 FVG)"),
        trade_data.get("htf_bias", "BEARISH").upper(),
        float(trade_data.get("entry_price", 0.0)),
        float(trade_data.get("stop_loss", 0.0)),
        float(trade_data.get("take_profit", 0.0)),
        float(trade_data["exit_price"]) if trade_data.get("exit_price") is not None else None,
        rr_ratio,
        outcome.upper(),
        float(realized_r or 0.0),
        float(trade_data.get("realized_pnl", 0.0) or 0.0),
        float(trade_data["or_high"]) if trade_data.get("or_high") is not None else None,
        float(trade_data["or_low"]) if trade_data.get("or_low") is not None else None,
        1 if trade_data.get("news_guarded") else 0,
        trade_data.get("notes", ""),
        trade_data.get("screenshot_url", ""),
        now_ts,
        now_ts
    ))
    conn.commit()
    trade_id = cursor.lastrowid
    conn.close()
    return trade_id

def get_trades(
    symbol: Optional[str] = None,
    action: Optional[str] = None,
    outcome: Optional[str] = None,
    setup_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: str = "trade_date",
    sort_dir: str = "DESC"
) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM trades WHERE 1=1"
    params = []
    
    if symbol and symbol.strip() and symbol != "ALL":
        query += " AND symbol = ?"
        params.append(symbol.strip().upper())
        
    if action and action.strip() and action != "ALL":
        query += " AND action = ?"
        params.append(action.strip().upper())
        
    if outcome and outcome.strip() and outcome != "ALL":
        query += " AND outcome = ?"
        params.append(outcome.strip().upper())
        
    if setup_type and setup_type.strip() and setup_type != "ALL":
        query += " AND setup_type = ?"
        params.append(setup_type.strip())
        
    if start_date and start_date.strip():
        query += " AND trade_date >= ?"
        params.append(start_date.strip())
        
    if end_date and end_date.strip():
        query += " AND trade_date <= ?"
        params.append(end_date.strip())
        
    valid_sorts = {
        "trade_date": "trade_date",
        "realized_r": "realized_r",
        "realized_pnl": "realized_pnl",
        "symbol": "symbol",
        "created_at": "created_at"
    }
    col = valid_sorts.get(sort_by, "trade_date")
    direction = "ASC" if sort_dir.upper() == "ASC" else "DESC"
    query += f" ORDER BY {col} {direction}, id {direction}"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    trades = [dict(row) for row in rows]
    conn.close()
    return trades

def get_trade_by_id(trade_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_trade(trade_id: int, trade_data: Dict[str, Any]) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    outcome = trade_data.get("outcome", "OPEN").upper()
    realized_r = trade_data.get("realized_r")
    rr_ratio = float(trade_data.get("rr_ratio", 2.0) or 2.0)
    
    if outcome == "WIN" and (realized_r == 0.0 or realized_r is None):
        realized_r = rr_ratio
    elif outcome == "LOSS" and (realized_r == 0.0 or realized_r is None):
        realized_r = -1.0
    elif outcome == "BREAK-EVEN":
        realized_r = 0.0

    cursor.execute("""
    UPDATE trades SET
        trade_date = ?, ny_time = ?, symbol = ?, action = ?, setup_type = ?,
        htf_bias = ?, entry_price = ?, stop_loss = ?, take_profit = ?,
        exit_price = ?, rr_ratio = ?, outcome = ?, realized_r = ?,
        realized_pnl = ?, or_high = ?, or_low = ?, news_guarded = ?,
        notes = ?, screenshot_url = ?, updated_at = ?
    WHERE id = ?
    """, (
        trade_data.get("trade_date"),
        trade_data.get("ny_time"),
        trade_data.get("symbol", "").upper(),
        trade_data.get("action", "").upper(),
        trade_data.get("setup_type"),
        trade_data.get("htf_bias", "").upper(),
        float(trade_data.get("entry_price", 0.0)),
        float(trade_data.get("stop_loss", 0.0)),
        float(trade_data.get("take_profit", 0.0)),
        float(trade_data["exit_price"]) if trade_data.get("exit_price") is not None else None,
        rr_ratio,
        outcome,
        float(realized_r if realized_r is not None else 0.0),
        float(trade_data.get("realized_pnl", 0.0) or 0.0),
        float(trade_data["or_high"]) if trade_data.get("or_high") is not None else None,
        float(trade_data["or_low"]) if trade_data.get("or_low") is not None else None,
        1 if trade_data.get("news_guarded") else 0,
        trade_data.get("notes", ""),
        trade_data.get("screenshot_url", ""),
        now_ts,
        trade_id
    ))
    conn.commit()
    rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0

def delete_trade(trade_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()
    rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0

def get_stats() -> Dict[str, Any]:
    trades = get_trades(sort_by="trade_date", sort_dir="ASC")
    total_trades = len(trades)
    
    if total_trades == 0:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "break_evens": 0,
            "open_trades": 0,
            "win_rate": 0.0,
            "total_realized_r": 0.0,
            "total_realized_pnl": 0.0,
            "profit_factor": 0.0,
            "avg_rr": 0.0,
            "equity_curve": [],
            "pair_breakdown": {},
            "setup_breakdown": {}
        }
        
    wins = sum(1 for t in trades if t["outcome"] == "WIN")
    losses = sum(1 for t in trades if t["outcome"] == "LOSS")
    bes = sum(1 for t in trades if t["outcome"] == "BREAK-EVEN")
    opens = sum(1 for t in trades if t["outcome"] == "OPEN")
    
    completed_trades = wins + losses + bes
    win_rate = round((wins / completed_trades * 100), 1) if completed_trades > 0 else 0.0
    
    total_r = round(sum(float(t["realized_r"] or 0.0) for t in trades), 2)
    total_pnl = round(sum(float(t["realized_pnl"] or 0.0) for t in trades), 2)
    
    gross_win_r = sum(float(t["realized_r"] or 0.0) for t in trades if (t["realized_r"] or 0) > 0)
    gross_loss_r = abs(sum(float(t["realized_r"] or 0.0) for t in trades if (t["realized_r"] or 0) < 0))
    profit_factor = round((gross_win_r / gross_loss_r), 2) if gross_loss_r > 0 else (round(gross_win_r, 2) if gross_win_r > 0 else 0.0)
    
    avg_rr = round(sum(float(t["rr_ratio"] or 2.0) for t in trades) / total_trades, 2)
    
    # Cumulative Equity Curve calculation
    equity_curve = []
    cum_r = 0.0
    cum_pnl = 0.0
    for idx, t in enumerate(trades):
        cum_r += float(t["realized_r"] or 0.0)
        cum_pnl += float(t["realized_pnl"] or 0.0)
        equity_curve.append({
            "trade_num": idx + 1,
            "date": t["trade_date"],
            "symbol": t["symbol"],
            "cum_r": round(cum_r, 2),
            "cum_pnl": round(cum_pnl, 2),
            "outcome": t["outcome"]
        })
        
    # Pair Breakdown
    pair_stats: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        sym = t["symbol"]
        if sym not in pair_stats:
            pair_stats[sym] = {"trades": 0, "wins": 0, "losses": 0, "net_r": 0.0, "net_pnl": 0.0}
        pair_stats[sym]["trades"] += 1
        if t["outcome"] == "WIN":
            pair_stats[sym]["wins"] += 1
        elif t["outcome"] == "LOSS":
            pair_stats[sym]["losses"] += 1
        pair_stats[sym]["net_r"] = round(pair_stats[sym]["net_r"] + float(t["realized_r"] or 0.0), 2)
        pair_stats[sym]["net_pnl"] = round(pair_stats[sym]["net_pnl"] + float(t["realized_pnl"] or 0.0), 2)

    # Setup Breakdown
    setup_stats: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        st = t["setup_type"] or "Standard FVG"
        if st not in setup_stats:
            setup_stats[st] = {"trades": 0, "wins": 0, "losses": 0, "net_r": 0.0}
        setup_stats[st]["trades"] += 1
        if t["outcome"] == "WIN":
            setup_stats[st]["wins"] += 1
        elif t["outcome"] == "LOSS":
            setup_stats[st]["losses"] += 1
        setup_stats[st]["net_r"] = round(setup_stats[st]["net_r"] + float(t["realized_r"] or 0.0), 2)

    return {
        "total_trades": total_trades,
        "completed_trades": completed_trades,
        "wins": wins,
        "losses": losses,
        "break_evens": bes,
        "open_trades": opens,
        "win_rate": win_rate,
        "total_realized_r": total_r,
        "total_realized_pnl": total_pnl,
        "profit_factor": profit_factor,
        "avg_rr": avg_rr,
        "equity_curve": equity_curve,
        "pair_breakdown": pair_stats,
        "setup_breakdown": setup_stats
    }

def export_trades_csv() -> str:
    trades = get_trades(sort_by="trade_date", sort_dir="ASC")
    output = io.StringIO()
    if not trades:
        return ""
    fieldnames = list(trades[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in trades:
        writer.writerow(row)
    return output.getvalue()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully with sample trades.")
