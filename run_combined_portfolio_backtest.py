import pandas as pd
import numpy as np
import yfinance as yf
from liquidity_sweep_engine import LiquiditySweepSignalEngine
from fvg_pullback_engine import FVGPullbackSignalEngine

def run_combined_strategy_backtest(
    symbol: str = "BTC-USD",
    period: str = "60d",
    interval: str = "1h",
    initial_capital: float = 100000.0,
    risk_per_trade: float = 0.015,  # 1.5% risk per trade
    fee_rate: float = 0.0005,       # 0.05% taker fee
    slippage: float = 0.0005        # 0.05% slippage
):
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval)

    if df.empty:
        print(f"[ERROR] No data returned for symbol {symbol}")
        return None

    df.columns = [str(c).lower() for c in df.columns]


    # Instantiate Strategy 1 (Liquidity Sweep) & Strategy 2 (FVG Pullback)
    engine_sweep = LiquiditySweepSignalEngine(config={
        "swing_window": 48,
        "atr_period": 14,
        "sl_buffer_atr": 0.2,
        "tp_rr_ratio": 2.5,
        "use_htf_filter": True,
        "htf_ema_period": 50,
        "use_volume_filter": True,
        "volume_sma_mult": 1.05,
        "use_rsi_filter": True,
        "use_mss_filter": True
    })

    engine_pullback = FVGPullbackSignalEngine(config={
        "fast_ema": 21,
        "slow_ema": 50,
        "htf_ema_period": 50,
        "atr_period": 14,
        "sl_atr_mult": 1.2,
        "tp_rr_ratio": 2.5
    })

    df_sweep = engine_sweep.generate_signals(df)
    df_pullback = engine_pullback.generate_signals(df)

    # Combine signals (Strategy 1 sweep takes priority if both trigger simultaneously)
    combined_signals = np.zeros(len(df), dtype=int)
    entry_prices = np.full(len(df), np.nan)
    stop_losses = np.full(len(df), np.nan)
    take_profits = np.full(len(df), np.nan)
    strategy_source = [""] * len(df)

    for i in range(len(df)):
        sig_s = df_sweep["signal"].iloc[i]
        sig_p = df_pullback["signal"].iloc[i]

        if sig_s != 0:
            combined_signals[i] = sig_s
            entry_prices[i] = df_sweep["entry_price"].iloc[i]
            stop_losses[i] = df_sweep["stop_loss"].iloc[i]
            take_profits[i] = df_sweep["take_profit"].iloc[i]
            strategy_source[i] = "Strategy 1 (Sweep)"
        elif sig_p != 0:
            combined_signals[i] = sig_p
            entry_prices[i] = df_pullback["entry_price"].iloc[i]
            stop_losses[i] = df_pullback["stop_loss"].iloc[i]
            take_profits[i] = df_pullback["take_profit"].iloc[i]
            strategy_source[i] = "Strategy 2 (FVG Pullback)"

    df["signal"] = combined_signals
    df["entry_price"] = entry_prices
    df["stop_loss"] = stop_losses
    df["take_profit"] = take_profits
    df["strategy_source"] = strategy_source

    balance = initial_capital
    equity_curve = [balance]
    trades = []
    active_trade = None

    for idx in range(len(df)):
        row = df.iloc[idx]
        timestamp = df.index[idx]

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
                        "Strategy": active_trade["strategy"],
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
                        "Strategy": active_trade["strategy"],
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
                    "units": units,
                    "strategy": row["strategy_source"]
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
    print("      COMBINED STRATEGY 1 & 2 PORTFOLIO BACKTEST (13 ASSETS - 60 DAYS)   ")
    print("==========================================================================")
    for sym in symbols:
        res = run_combined_strategy_backtest(symbol=sym, period="60d", interval="1h")
        if res:
            results.append(res)

    print("\n" + "="*80)
    print("            COMBINED MULTI-STRATEGY BACKTEST SUMMARY (60 DAYS)           ")
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

    all_trades = pd.concat([r["trades_df"] for r in results if not r["trades_df"].empty], ignore_index=True) if results else pd.DataFrame()
    if not all_trades.empty:
        total_p_trades = len(all_trades)
        wins = all_trades[all_trades["Net PnL ($)"] > 0]
        losses = all_trades[all_trades["Net PnL ($)"] <= 0]
        p_win_rate = (len(wins) / total_p_trades) * 100.0
        p_gross_profit = wins["Net PnL ($)"].sum()
        p_gross_loss = abs(losses["Net PnL ($)"].sum())
        p_profit_factor = (p_gross_profit / p_gross_loss) if p_gross_loss > 0 else 0.0
        avg_trades_per_day = total_p_trades / 60.0

        print("\n" + "="*80)
        print("              COMBINED SYSTEM PORTFOLIO AGGREGATE STATS                   ")
        print("="*80)
        print(f" Total Portfolio Trades:   {total_p_trades} trades across 13 assets")
        print(f" Average Occurrence Rate:  {avg_trades_per_day:.2f} trades / day (High Opportunity Rate!)")
        print(f" Combined System Win Rate: {p_win_rate:.1f}% ({len(wins)} W / {len(losses)} L)")
        print(f" Combined Profit Factor:   {p_profit_factor:.2f}")
        print("="*80 + "\n")
