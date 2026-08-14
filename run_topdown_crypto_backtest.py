import requests
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import time
import json
import os

def fetch_crypto_data(symbol="BTCUSDT", days=60, interval="15m"):
    """
    Fetch multi-timeframe candle data for crypto.
    Attempts Binance API first, with automatic fallback to Yahoo Finance.
    """
    url = "https://api.binance.com/api/v3/klines"
    end_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)
    
    all_candles = []
    curr_start = start_time
    
    print(f"Fetching {days} days of {interval} data for {symbol}...")
    try:
        while curr_start < end_time:
            params = {
                "symbol": symbol.replace("-", ""),
                "interval": interval,
                "startTime": curr_start,
                "limit": 1000
            }
            r = requests.get(url, params=params, timeout=6)
            data = r.json()
            if not data or not isinstance(data, list) or len(data) == 0:
                break
            all_candles.extend(data)
            curr_start = data[-1][6] + 1
            if len(data) < 1000:
                break
            time.sleep(0.04)
            
        if all_candles:
            df = pd.DataFrame(all_candles, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_asset_volume", "num_trades",
                "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
            ])
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df.set_index("open_time", inplace=True)
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            print(f"[+] Downloaded {len(df)} candles for {symbol} from Binance")
            return df
    except Exception as e:
        print(f"[*] Binance API unavailable, falling back to Yahoo Finance...")

    # Fallback to yfinance
    try:
        yf_symbol = symbol if "-USD" in symbol else f"{symbol.replace('USDT', '')}-USD"
        ticker = yf.Ticker(yf_symbol)
        period_str = f"{min(days, 59)}d"
        df = ticker.history(period=period_str, interval=interval)
        if not df.empty:
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
            })
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")
            print(f"[+] Downloaded {len(df)} candles for {yf_symbol} from Yahoo Finance")
            return df
    except Exception as ex:
        print(f"[-] Failed to fetch data for {symbol}: {ex}")
        
    return pd.DataFrame()

def run_topdown_liquidity_backtest(
    df: pd.DataFrame,
    symbol: str,
    initial_capital: float = 100000.0,
    risk_per_trade: float = 0.015,   # 1.5% risk
    tp1_rr: float = 1.5,             # 1:1.5 RR for TP1 (50% close)
    tp2_rr: float = 3.0,             # 1:3.0 RR for TP2 (50% close)
    sl_buffer_atr: float = 0.5,      # 0.5 ATR buffer beyond sweep extreme
    swing_lookback: int = 32,        # 32 bars on 15m = 8-hour rolling swing high/low
    entry_mode: str = "immediate_close", # 'immediate_close' or 'fvg_retest'
    fee_rate: float = 0.0005,        # 0.05% taker fee
    slippage: float = 0.0005         # 0.05% slippage
):
    if df.empty or len(df) < 200:
        return None

    df = df.copy()
    
    # 1. Calculate Technical Indicators
    # ATR (14)
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"] - df["close"].shift(1))
        )
    )
    df["atr"] = tr.rolling(14).mean().bfill()
    
    # Volume SMA (20)
    df["vol_sma"] = df["volume"].rolling(20).mean().bfill()
    df["vol_spike"] = df["volume"] >= (df["vol_sma"] * 1.05)
    
    # 2. Higher Timeframe (4H) Trend Bias via Resampling
    df_4h = df["close"].resample("4h").last().dropna()
    ema_50_4h = df_4h.ewm(span=50, adjust=False).mean()
    df["htf_ema_50"] = ema_50_4h.reindex(df.index, method="ffill").bfill()
    df["htf_bias"] = np.where(df["close"] >= df["htf_ema_50"], 1, -1) # 1 = Bullish, -1 = Bearish
    
    # 3. Dynamic Liquidity Reference Levels
    # Prior Day High (PDH) & Prior Day Low (PDL)
    df["date"] = df.index.date
    daily_highs = df.groupby("date")["high"].max().shift(1)
    daily_lows = df.groupby("date")["low"].min().shift(1)
    df["pdh"] = df["date"].map(daily_highs).ffill()
    df["pdl"] = df["date"].map(daily_lows).ffill()
    
    # Rolling Local Swing High / Low (Liquidity Pools)
    df["swing_high"] = df["high"].shift(1).rolling(swing_lookback).max()
    df["swing_low"] = df["low"].shift(1).rolling(swing_lookback).min()
    
    # Asian Session Range (00:00 - 08:00 UTC)
    df["is_asian"] = (df.index.hour >= 0) & (df.index.hour < 8)
    asian_highs = df[df["is_asian"]].groupby("date")["high"].transform("max")
    asian_lows = df[df["is_asian"]].groupby("date")["low"].transform("min")
    df["asian_high"] = asian_highs.reindex(df.index, method="ffill")
    df["asian_low"] = asian_lows.reindex(df.index, method="ffill")
    
    # 4. Liquidity Sweep Detection
    # Candle geometry
    bar_range = np.maximum(df["high"] - df["low"], 1e-6)
    lower_wick = np.minimum(df["open"], df["close"]) - df["low"]
    upper_wick = df["high"] - np.maximum(df["open"], df["close"])
    
    # Bullish Sweep: Sweeps PDL, Asian Low, or Swing Low
    swept_bull_level = (df["low"] < df["pdl"]) | (df["low"] < df["asian_low"]) | (df["low"] < df["swing_low"])
    closed_above_bull = (df["close"] >= df["pdl"]) | (df["close"] >= df["asian_low"]) | (df["close"] >= df["swing_low"])
    rejection_wick_bull = (lower_wick / bar_range) >= 0.20
    df["bull_sweep"] = swept_bull_level & (closed_above_bull | rejection_wick_bull) & df["vol_spike"]
    
    # Bearish Sweep: Sweeps PDH, Asian High, or Swing High
    swept_bear_level = (df["high"] > df["pdh"]) | (df["high"] > df["asian_high"]) | (df["high"] > df["swing_high"])
    closed_below_bear = (df["close"] <= df["pdh"]) | (df["close"] <= df["asian_high"]) | (df["close"] <= df["swing_high"])
    rejection_wick_bear = (upper_wick / bar_range) >= 0.20
    df["bear_sweep"] = swept_bear_level & (closed_below_bear | rejection_wick_bear) & df["vol_spike"]
    
    # 5. Fair Value Gap (FVG) Detection
    # Bullish FVG: High of bar i-2 < Low of bar i (3-candle formation)
    df["bull_fvg"] = df["low"] > df["high"].shift(2)
    df["bull_fvg_ce"] = (df["high"].shift(2) + df["low"]) / 2.0 # 50% Consequent Encroachment
    
    # Bearish FVG: Low of bar i-2 > High of bar i
    df["bear_fvg"] = df["high"] < df["low"].shift(2)
    df["bear_fvg_ce"] = (df["low"].shift(2) + df["high"]) / 2.0
    
    # 6. Backtest Simulation Loop
    balance = initial_capital
    equity_curve = [balance]
    trades = []
    
    active_trade = None
    pending_order = None
    last_sweep_bar = -999
    last_sweep_type = None
    last_sweep_extreme = None
    
    for i in range(50, len(df)):
        current_time = df.index[i]
        curr_row = df.iloc[i]
        
        # 1. Manage Active Trade
        if active_trade is not None:
            sig = active_trade["signal"]
            entry = active_trade["entry_price"]
            sl = active_trade["current_sl"]
            tp1 = active_trade["tp1"]
            tp2 = active_trade["tp2"]
            qty = active_trade["qty"]
            tp1_hit = active_trade["tp1_hit"]
            
            exit_trade = False
            exit_reason = None
            exit_price = None
            
            if sig == 1: # LONG
                if curr_row["low"] <= sl:
                    exit_trade = True
                    exit_price = sl * (1.0 - slippage)
                    exit_reason = "STOP_LOSS" if not tp1_hit else "BREAKEVEN_SL"
                elif not tp1_hit and curr_row["high"] >= tp1:
                    active_trade["tp1_hit"] = True
                    closed_qty = qty * 0.5
                    active_trade["qty"] = qty * 0.5
                    pnl_tp1 = closed_qty * (tp1 * (1.0 - slippage) - entry) - (closed_qty * tp1 * fee_rate)
                    balance += pnl_tp1
                    active_trade["realized_pnl"] += pnl_tp1
                    active_trade["current_sl"] = entry * (1.0 + fee_rate)
                    
                    if curr_row["high"] >= tp2:
                        exit_trade = True
                        exit_price = tp2 * (1.0 - slippage)
                        exit_reason = "TP2_RUNNER"
                elif tp1_hit and curr_row["high"] >= tp2:
                    exit_trade = True
                    exit_price = tp2 * (1.0 - slippage)
                    exit_reason = "TP2_RUNNER"
                    
            elif sig == -1: # SHORT
                if curr_row["high"] >= sl:
                    exit_trade = True
                    exit_price = sl * (1.0 + slippage)
                    exit_reason = "STOP_LOSS" if not tp1_hit else "BREAKEVEN_SL"
                elif not tp1_hit and curr_row["low"] <= tp1:
                    active_trade["tp1_hit"] = True
                    closed_qty = qty * 0.5
                    active_trade["qty"] = qty * 0.5
                    pnl_tp1 = closed_qty * (entry - tp1 * (1.0 + slippage)) - (closed_qty * tp1 * fee_rate)
                    balance += pnl_tp1
                    active_trade["realized_pnl"] += pnl_tp1
                    active_trade["current_sl"] = entry * (1.0 - fee_rate)
                    
                    if curr_row["low"] <= tp2:
                        exit_trade = True
                        exit_price = tp2 * (1.0 + slippage)
                        exit_reason = "TP2_RUNNER"
                elif tp1_hit and curr_row["low"] <= tp2:
                    exit_trade = True
                    exit_price = tp2 * (1.0 + slippage)
                    exit_reason = "TP2_RUNNER"

            if exit_trade:
                rem_qty = active_trade["qty"]
                if sig == 1:
                    rem_pnl = rem_qty * (exit_price - entry) - (rem_qty * exit_price * fee_rate)
                else:
                    rem_pnl = rem_qty * (entry - exit_price) - (rem_qty * exit_price * fee_rate)
                    
                balance += rem_pnl
                total_trade_pnl = active_trade["realized_pnl"] + rem_pnl
                
                trades.append({
                    "symbol": symbol,
                    "entry_time": active_trade["entry_time"],
                    "exit_time": current_time,
                    "side": "BUY" if sig == 1 else "SELL",
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "initial_sl": active_trade["initial_sl"],
                    "tp1": active_trade["tp1"],
                    "tp2": active_trade["tp2"],
                    "tp1_hit": active_trade["tp1_hit"],
                    "exit_reason": exit_reason,
                    "pnl": total_trade_pnl,
                    "pnl_pct": (total_trade_pnl / active_trade["account_balance_at_entry"]) * 100,
                    "pnl_r": total_trade_pnl / active_trade["risk_amount"],
                    "balance_after": balance
                })
                active_trade = None
                
            equity_curve.append(balance)
            continue

        # 2. Check Pending Limit Order Retest Fills
        if pending_order is not None:
            p_sig = pending_order["signal"]
            p_entry = pending_order["entry_price"]
            p_sl = pending_order["sl"]
            p_bars = pending_order["bars_active"]
            
            # Check order fill
            filled = False
            cancelled = False
            
            if p_sig == 1: # Buy Limit
                if curr_row["low"] <= p_sl:
                    cancelled = True # Invalidated before fill
                elif curr_row["low"] <= p_entry:
                    filled = True
            elif p_sig == -1: # Sell Limit
                if curr_row["high"] >= p_sl:
                    cancelled = True
                elif curr_row["high"] >= p_entry:
                    filled = True
                    
            if p_bars > 8:
                cancelled = True # Expired
                
            if filled:
                risk_dist = abs(p_entry - p_sl)
                if risk_dist > 0:
                    risk_amt = balance * risk_per_trade
                    qty = risk_amt / risk_dist
                    tp1 = p_entry + (risk_dist * tp1_rr) if p_sig == 1 else p_entry - (risk_dist * tp1_rr)
                    tp2 = p_entry + (risk_dist * tp2_rr) if p_sig == 1 else p_entry - (risk_dist * tp2_rr)
                    entry_cost = qty * p_entry * (fee_rate + slippage)
                    balance -= entry_cost
                    
                    active_trade = {
                        "signal": p_sig,
                        "entry_time": current_time,
                        "entry_price": p_entry,
                        "initial_sl": p_sl,
                        "current_sl": p_sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "qty": qty,
                        "risk_amount": risk_amt,
                        "account_balance_at_entry": balance,
                        "realized_pnl": -entry_cost,
                        "tp1_hit": False
                    }
                pending_order = None
            elif cancelled:
                pending_order = None
            else:
                pending_order["bars_active"] += 1
            
        # 3. Record new sweeps
        if curr_row["bull_sweep"] and curr_row["htf_bias"] == 1:
            last_sweep_bar = i
            last_sweep_type = "BULL"
            last_sweep_extreme = curr_row["low"]
        elif curr_row["bear_sweep"] and curr_row["htf_bias"] == -1:
            last_sweep_bar = i
            last_sweep_type = "BEAR"
            last_sweep_extreme = curr_row["high"]
            
        # 4. Check for Market Structure Shift (MSS) within 12 bars of a sweep
        bars_since_sweep = i - last_sweep_bar
        if 1 <= bars_since_sweep <= 12 and pending_order is None and active_trade is None:
            local_swing_high = df["high"].iloc[i-5:i].max()
            local_swing_low = df["low"].iloc[i-5:i].min()
            
            if last_sweep_type == "BULL" and curr_row["close"] > local_swing_high and curr_row["bull_fvg"]:
                entry_price = curr_row["close"] if entry_mode == "immediate_close" else curr_row["bull_fvg_ce"]
                sl = last_sweep_extreme - (curr_row["atr"] * sl_buffer_atr)
                risk_dist = entry_price - sl
                
                if risk_dist > 0:
                    if entry_mode == "immediate_close":
                        risk_amt = balance * risk_per_trade
                        qty = risk_amt / risk_dist
                        tp1 = entry_price + (risk_dist * tp1_rr)
                        tp2 = entry_price + (risk_dist * tp2_rr)
                        entry_cost = qty * entry_price * (fee_rate + slippage)
                        balance -= entry_cost
                        active_trade = {
                            "signal": 1,
                            "entry_time": current_time,
                            "entry_price": entry_price,
                            "initial_sl": sl,
                            "current_sl": sl,
                            "tp1": tp1,
                            "tp2": tp2,
                            "qty": qty,
                            "risk_amount": risk_amt,
                            "account_balance_at_entry": balance,
                            "realized_pnl": -entry_cost,
                            "tp1_hit": False
                        }
                    else:
                        pending_order = {
                            "signal": 1,
                            "entry_price": entry_price,
                            "sl": sl,
                            "bars_active": 0
                        }
                    last_sweep_bar = -999
                    
            elif last_sweep_type == "BEAR" and curr_row["close"] < local_swing_low and curr_row["bear_fvg"]:
                entry_price = curr_row["close"] if entry_mode == "immediate_close" else curr_row["bear_fvg_ce"]
                sl = last_sweep_extreme + (curr_row["atr"] * sl_buffer_atr)
                risk_dist = sl - entry_price
                
                if risk_dist > 0:
                    if entry_mode == "immediate_close":
                        risk_amt = balance * risk_per_trade
                        qty = risk_amt / risk_dist
                        tp1 = entry_price - (risk_dist * tp1_rr)
                        tp2 = entry_price - (risk_dist * tp2_rr)
                        entry_cost = qty * entry_price * (fee_rate + slippage)
                        balance -= entry_cost
                        active_trade = {
                            "signal": -1,
                            "entry_time": current_time,
                            "entry_price": entry_price,
                            "initial_sl": sl,
                            "current_sl": sl,
                            "tp1": tp1,
                            "tp2": tp2,
                            "qty": qty,
                            "risk_amount": risk_amt,
                            "account_balance_at_entry": balance,
                            "realized_pnl": -entry_cost,
                            "tp1_hit": False
                        }
                    else:
                        pending_order = {
                            "signal": -1,
                            "entry_price": entry_price,
                            "sl": sl,
                            "bars_active": 0
                        }
                    last_sweep_bar = -999
                        
        equity_curve.append(balance)
        
    # Compile performance stats
    if not trades:
        return {
            "symbol": symbol,
            "total_trades": 0,
            "net_profit": 0.0,
            "return_pct": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "total_r_gain": 0.0,
            "trades": []
        }
        
    trades_df = pd.DataFrame(trades)
    winning_trades = trades_df[trades_df["pnl"] > 0]
    losing_trades = trades_df[trades_df["pnl"] < 0]
    breakeven_trades = trades_df[(trades_df["pnl"] >= -50) & (trades_df["pnl"] <= 50)]
    
    gross_profit = winning_trades["pnl"].sum()
    gross_loss = abs(losing_trades["pnl"].sum())
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
    
    # Calculate Max Drawdown
    equity_series = pd.Series(equity_curve)
    peak = equity_series.cummax()
    drawdown = (equity_series - peak) / peak * 100
    max_drawdown_pct = abs(drawdown.min())
    
    win_rate = (len(winning_trades) / len(trades_df)) * 100
    net_profit = balance - initial_capital
    return_pct = (net_profit / initial_capital) * 100
    
    avg_win = winning_trades["pnl"].mean() if len(winning_trades) > 0 else 0.0
    avg_loss = losing_trades["pnl"].mean() if len(losing_trades) > 0 else 0.0
    
    tp1_conversions = trades_df["tp1_hit"].sum()
    tp2_full_runners = (trades_df["exit_reason"] == "TP2_RUNNER").sum()
    
    return {
        "symbol": symbol,
        "initial_capital": initial_capital,
        "final_capital": round(balance, 2),
        "net_profit": round(net_profit, 2),
        "return_pct": round(return_pct, 2),
        "total_trades": len(trades_df),
        "wins": len(winning_trades),
        "losses": len(losing_trades),
        "breakevens": len(breakeven_trades),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "tp1_conversions": int(tp1_conversions),
        "tp2_full_runners": int(tp2_full_runners),
        "total_r_gain": round(trades_df["pnl_r"].sum(), 2),
        "trades": trades
    }

if __name__ == "__main__":
    test_coins = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "NEARUSDT"]
    results = []
    
    print("\n" + "="*80)
    print("   TOP-DOWN CRYPTO LIQUIDITY SWEEP & MSS STRATEGY BACKTEST (60 DAYS)")
    print("   Timeframe: 15m Execution | 4H Macro Bias | Scaled TP (1:1.5 + 1:3.0 RR)")
    print("="*80)
    
    for coin in test_coins:
        df = fetch_crypto_data(coin, days=60, interval="15m")
        if not df.empty:
            res = run_topdown_liquidity_backtest(df, coin)
            if res:
                results.append(res)
                
    # Summary Table
    print("\n" + "="*95)
    print(f"{'Coin':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Net Profit':<12} | {'Return %':<9} | {'Profit Factor':<14} | {'Max DD %':<9} | {'Net R':<8}")
    print("-"*95)
    
    total_trades = sum(r["total_trades"] for r in results)
    total_net = sum(r["net_profit"] for r in results)
    avg_wr = np.mean([r["win_rate"] for r in results]) if results else 0
    avg_pf = np.mean([r["profit_factor"] for r in results]) if results else 0
    max_dd = max([r["max_drawdown_pct"] for r in results]) if results else 0
    total_r = sum(r["total_r_gain"] for r in results)
    
    for r in results:
        print(f"{r['symbol']:<10} | {r['total_trades']:<7} | {r['win_rate']:>6.2f}%  | ${r['net_profit']:>10.2f} | {r['return_pct']:>6.2f}%  | {r['profit_factor']:>12.2f} | {r['max_drawdown_pct']:>6.2f}%  | {r['total_r_gain']:>+6.2f}R")
        
    print("-"*95)
    print(f"{'PORTFOLIO':<10} | {total_trades:<7} | {avg_wr:>6.2f}%  | ${total_net:>10.2f} | {total_net/1000:>6.2f}%  | {avg_pf:>12.2f} | {max_dd:>6.2f}%  | {total_r:>+6.2f}R")
    print("="*95 + "\n")
    
    # Save backtest results to JSON
    with open("topdown_backtest_results.json", "w") as f:
        json.dump({
            "portfolio_summary": {
                "total_trades": total_trades,
                "total_net_profit": round(total_net, 2),
                "avg_win_rate": round(avg_wr, 2),
                "avg_profit_factor": round(avg_pf, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "total_r_gain": round(total_r, 2)
            },
            "coins": results
        }, f, indent=2, default=str)
