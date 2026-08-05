import pandas as pd
import numpy as np
import yfinance as yf
from fvg_pullback_engine import FVGPullbackSignalEngine

def run_fvg_pullback_backtest(
    symbol: str = "BTC-USD",
    period: str = "60d",
    interval: str = "1h",
    initial_capital: float = 100000.0,
    risk_per_trade: float = 0.015,  # 1.5% risk per trade
    fee_rate: float = 0.0005,       # 0.05% taker fee
    slippage: float = 0.0005,       # 0.05% slippage
    config_overrides: dict = None
):
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval)

    if df.empty:
        print(f"[ERROR] No data returned for symbol {symbol}")
        return None

    df.columns = [str(c).lower() for c in df.columns]


    config = {
        "fast_ema": 21,
        "slow_ema": 50,
        "htf_ema_period": 50,
        "atr_period": 14,
        "sl_atr_mult": 1.2,
        "tp_rr_ratio": 2.5
    }
    if config_overrides:
        config.update(config_overrides)

    engine = FVGPullbackSignalEngine(config=config)
    df_signals = engine.generate_signals(df)

    balance = initial_capital
    equity_curve = [balance]
    trades = []
    active_trade = None

    for idx in range(len(df_signals)):
        row = df_signals.iloc[idx]
        timestamp = df_signals.index[idx]

        # Manage Active Trade
        if active_trade is not None:
            sig = active_trade["signal"]
            entry = active_trade["entry_price"]
            sl = active_trade["stop_loss"]
            tp = active_trade["take_profit"]
            size_units = active_trade["units"]

            high = row["high"]
            low = row["low"]

            if sig == 1: # LONG
                hit_sl = low <= sl
                hit_tp = high >= tp

                if hit_sl or hit_tp:
                    exit_price = sl if hit_sl else tp
                    exit_price = exit_price * (1.0 - slippage)
                    gross_pnl = (exit_price - entry) * size_units
                    entry_fee = (entry * size_units) * fee_rate
                    exit_fee = (exit_price * size_units) * fee_rate
                    net_pnl = gross_pnl - entry_fee - exit_fee

                    balance += net_pnl
                    trades.append({
                        "Entry Time": active_trade["entry_time"].strftime("%Y-%m-%d %H:%M"),
                        "Exit Time": timestamp.strftime("%Y-%m-%d %H:%M"),
                        "Symbol": symbol,
                        "Type": "LONG",
                        "Entry Price": round(entry, 2),
                        "Exit Price": round(exit_price, 2),
                        "SL": round(sl, 2),
                        "TP": round(tp, 2),
                        "Net PnL ($)": round(net_pnl, 2),
                        "Return (%)": round((net_pnl / (entry * size_units)) * 100, 2),
                        "Status": "TP HIT" if hit_tp and not hit_sl else "SL HIT",
                        "Balance ($)": round(balance, 2)
                    })
                    active_trade = None

            elif sig == -1: # SHORT
                hit_sl = high >= sl
                hit_tp = low <= tp

                if hit_sl or hit_tp:
                    exit_price = sl if hit_sl else tp
                    exit_price = exit_price * (1.0 + slippage)
                    gross_pnl = (entry - exit_price) * size_units
                    entry_fee = (entry * size_units) * fee_rate
                    exit_fee = (exit_price * size_units) * fee_rate
                    net_pnl = gross_pnl - entry_fee - exit_fee

                    balance += net_pnl
                    trades.append({
                        "Entry Time": active_trade["entry_time"].strftime("%Y-%m-%d %H:%M"),
                        "Exit Time": timestamp.strftime("%Y-%m-%d %H:%M"),
                        "Symbol": symbol,
                        "Type": "SHORT",
                        "Entry Price": round(entry, 2),
                        "Exit Price": round(exit_price, 2),
                        "SL": round(sl, 2),
                        "TP": round(tp, 2),
                        "Net PnL ($)": round(net_pnl, 2),
                        "Return (%)": round((net_pnl / (entry * size_units)) * 100, 2),
                        "Status": "TP HIT" if hit_tp and not hit_sl else "SL HIT",
                        "Balance ($)": round(balance, 2)
                    })
                    active_trade = None

        # Open New Trade Signal if flat
        if active_trade is None and row["signal"] != 0:
            sig = int(row["signal"])
            entry = row["entry_price"]
            sl = row["stop_loss"]
            tp = row["take_profit"]

            executed_entry = entry * (1.0 + slippage) if sig == 1 else entry * (1.0 - slippage)
            risk_dist = abs(executed_entry - sl)

            if risk_dist > 0:
                risk_dollars = balance * risk_per_trade
                units = risk_dollars / risk_dist

                active_trade = {
                    "signal": sig,
                    "entry_time": timestamp,
                    "entry_price": executed_entry,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "units": units
                }

        equity_curve.append(balance)

    df_trades = pd.DataFrame(trades)
    total_trades = len(df_trades)
    
    if total_trades > 0:
        wins = df_trades[df_trades["Net PnL ($)"] > 0]
        losses = df_trades[df_trades["Net PnL ($)"] <= 0]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades) * 100.0

        gross_profit = wins["Net PnL ($)"].sum() if win_count > 0 else 0.0
        gross_loss = abs(losses["Net PnL ($)"].sum()) if loss_count > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

        s_equity = pd.Series(equity_curve)
        peak = s_equity.cummax()
        dd = (s_equity - peak) / peak
        max_dd_pct = abs(dd.min()) * 100.0

        total_return_pct = ((balance - initial_capital) / initial_capital) * 100.0
    else:
        win_count = loss_count = 0
        win_rate = profit_factor = max_dd_pct = total_return_pct = 0.0

    return {
        "symbol": symbol,
        "interval": interval,
        "total_return_pct": total_return_pct,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_dd_pct": max_dd_pct,
        "trades_df": df_trades
    }

if __name__ == "__main__":
    symbols = [
        "BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "NEAR-USD", 
        "OP-USD", "LINK-USD", "INJ-USD", "LDO-USD", "SUI20947-USD", 
        "APT21794-USD", "ARB11841-USD", "TIA-USD"
    ]
    results = []

    print("\n==========================================================================")
    print("      STRATEGY 2: FVG & EMA PULLBACK PORTFOLIO BACKTEST (13 ASSETS - 60D)  ")
    print("==========================================================================")
    for sym in symbols:
        res = run_fvg_pullback_backtest(symbol=sym, period="60d", interval="1h")
        if res:
            results.append(res)

    print("\n" + "="*80)
    print("            STRATEGY 2 MULTI-ASSET BACKTEST SUMMARY (60 DAYS)              ")
    print("="*80)
    summary_df = pd.DataFrame([{
        "Symbol": r["symbol"],
        "Interval": r["interval"],
        "Return (%)": f"{r['total_return_pct']:+.2f}%",
        "Trades": r["total_trades"],
        "Win Rate (%)": f"{r['win_rate']:.1f}%",
        "Profit Factor": f"{r['profit_factor']:.2f}",
        "Max DD (%)": f"{r['max_dd_pct']:.2f}%"
    } for r in results])
    print(summary_df.to_string(index=False))
