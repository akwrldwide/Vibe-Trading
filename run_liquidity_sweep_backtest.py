import pandas as pd
import numpy as np
import yfinance as yf
from liquidity_sweep_engine import LiquiditySweepSignalEngine

def run_liquidity_sweep_backtest(
    symbol: str = "BTC-USD",
    period: str = "60d",
    interval: str = "15m",
    initial_capital: float = 100000.0,
    risk_per_trade: float = 0.015,  # 1.5% risk per trade
    fee_rate: float = 0.0005,       # 0.05% perp taker fee
    slippage: float = 0.0005,       # 0.05% slippage
    config_overrides: dict = None
):
    print(f"\n==================================================")
    print(f"   Strategy 1: Liquidity Sweep & MSS Backtest Engine")
    print(f"   Symbol: {symbol} | Period: {period} | Interval: {interval}")
    print(f"==================================================")
    
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval)

    if df.empty:
        print(f"[ERROR] No data returned for symbol {symbol}")
        return None


    print(f"Fetched {len(df)} candles for {symbol} ({df.index[0].strftime('%Y-%m-%d %H:%M')} to {df.index[-1].strftime('%Y-%m-%d %H:%M')})")

    config = {
        "swing_window": 48,
        "atr_period": 14,
        "sl_buffer_atr": 0.5,
        "tp_rr_ratio": 2.0,
        "use_htf_filter": True,
        "htf_ema_period": 50,
        "use_volume_filter": True,
        "volume_sma_mult": 1.05,
        "use_rsi_filter": True,
        "use_mss_filter": True
    }


    if config_overrides:
        config.update(config_overrides)

    engine = LiquiditySweepSignalEngine(config=config)
    df_signals = engine.generate_signals(df)

    trade_signals = df_signals[df_signals["signal"] != 0]
    print(f"Generated {len(trade_signals)} liquidity sweep signals")

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
                # Automated Breakeven adjustment once price hits 1:1 RR
                risk_dist = active_trade["initial_risk"]
                if high >= (entry + risk_dist * 1.0):
                    new_sl = entry + (0.1 * row["atr"])
                    if new_sl > sl:
                        sl = new_sl
                        active_trade["stop_loss"] = sl
                        active_trade["be_shifted"] = True

                hit_sl = low <= sl
                hit_tp = high >= tp

                if hit_sl or hit_tp:
                    if hit_sl and hit_tp:
                        exit_price = sl
                        status = "SL HIT (Intra-candle clash)"
                    elif hit_tp:
                        exit_price = tp
                        status = "TP HIT"
                    else:
                        exit_price = sl
                        status = "BE HIT" if active_trade.get("be_shifted", False) else "SL HIT"

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
                        "Status": status,
                        "Balance ($)": round(balance, 2)
                    })
                    active_trade = None

            elif sig == -1: # SHORT
                risk_dist = active_trade["initial_risk"]
                if low <= (entry - risk_dist * 1.0):
                    new_sl = entry - (0.1 * row["atr"])
                    if new_sl < sl:
                        sl = new_sl
                        active_trade["stop_loss"] = sl
                        active_trade["be_shifted"] = True

                hit_sl = high >= sl
                hit_tp = low <= tp

                if hit_sl or hit_tp:
                    if hit_sl and hit_tp:
                        exit_price = sl
                        status = "SL HIT (Intra-candle clash)"
                    elif hit_tp:
                        exit_price = tp
                        status = "TP HIT"
                    else:
                        exit_price = sl
                        status = "BE HIT" if active_trade.get("be_shifted", False) else "SL HIT"

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
                        "Status": status,
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
                    "initial_risk": risk_dist,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "units": units
                }


        equity_curve.append(balance)

    # Performance Evaluation Metrics
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

    print(f"\n================ PERFORMANCE SUMMARY ({symbol} - {interval}) ================")
    print(f" Initial Capital:     ${initial_capital:,.2f}")
    print(f" Ending Capital:      ${balance:,.2f}")
    print(f" Total Return:        {total_return_pct:+.2f}%")
    print(f" Total Trades:        {total_trades} ({win_count} W / {loss_count} L)")
    print(f" Win Rate:            {win_rate:.1f}%")
    print(f" Profit Factor:       {profit_factor:.2f}")
    print(f" Max Drawdown:        {max_dd_pct:.2f}%")
    print(f"===============================================================\n")

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
    print("      RUNNING 1H TIME FRAME PORTFOLIO BACKTEST (12 PERP ASSETS - 60 DAYS)  ")
    print("==========================================================================")
    for sym in symbols:
        res = run_liquidity_sweep_backtest(symbol=sym, period="60d", interval="1h")
        if res:
            results.append(res)

    print("\n" + "="*80)
    print("             1H MULTI-ASSET PORTFOLIO BACKTEST SUMMARY (60 DAYS)           ")
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

    # Portfolio Aggregates
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
        print("                  COMBINED PORTFOLIO AGGREGATE STATS                      ")
        print("="*80)
        print(f" Total Portfolio Trades:   {total_p_trades} trades across 12 assets")
        print(f" Average Occurrence Rate:  {avg_trades_per_day:.2f} trades / day (High Opportunity Rate!)")
        print(f" Combined Win Rate:        {p_win_rate:.1f}% ({len(wins)} W / {len(losses)} L)")
        print(f" Combined Profit Factor:   {p_profit_factor:.2f}")
        print("="*80 + "\n")


