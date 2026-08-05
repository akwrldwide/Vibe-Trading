import pandas as pd
import numpy as np
import yfinance as yf
from crypto_signal_engine import CryptoBreakoutSignalEngine

def run_crypto_backtest(
    symbol: str = "BTC-USD",
    period: str = "60d",
    interval: str = "1h",
    initial_capital: float = 100000.0,
    risk_per_trade: float = 0.015,  # 1.5% risk per trade
    fee_rate: float = 0.0005,       # 0.05% taker fee
    slippage: float = 0.0005,       # 0.05% slippage
    config_overrides: dict = None
):
    print(f"\n==================================================")
    print(f"   Crypto Breakout Backtest Engine: {symbol}")
    print(f"   Period: {period} | Interval: {interval}")
    print(f"==================================================")
    
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval)

    if df.empty:
        print(f"❌ Error: No data returned for symbol {symbol}")
        return None

    print(f"Fetched {len(df)} candles for {symbol} ({df.index[0].strftime('%Y-%m-%d %H:%M')} to {df.index[-1].strftime('%Y-%m-%d %H:%M')})")

    config = {
        "atr_period": 10,
        "factor": 3.0,
        "tp_rr_ratio": 3.0,
    }
    if config_overrides:
        config.update(config_overrides)

    engine = CryptoBreakoutSignalEngine(config=config)
    df_signals = engine.generate_signals(df)

    trade_signals = df_signals[df_signals["signal"] != 0]
    print(f"Generated {len(trade_signals)} initial breakout signals")

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
            atr = active_trade["atr"]
            size_units = active_trade["units"]

            high = row["high"]
            low = row["low"]
            close = row["close"]

            # Update Trailing Stop (Chandelier exit)
            if sig == 1:
                new_sl = max(sl, high - (2.5 * atr))
                active_trade["stop_loss"] = new_sl
                sl = new_sl

                # Check Exit Conditions (Stop Loss or Take Profit)
                hit_sl = low <= sl
                hit_tp = high >= tp

                if hit_sl or hit_tp:
                    exit_price = sl if hit_sl else tp
                    # Adjust exit price for slippage
                    exit_price = exit_price * (1.0 - slippage) if hit_sl else exit_price * (1.0 - slippage)
                    
                    gross_pnl = (exit_price - entry) * size_units
                    entry_fee = (entry * size_units) * fee_rate
                    exit_fee = (exit_price * size_units) * fee_rate
                    net_pnl = gross_pnl - entry_fee - exit_fee

                    balance += net_pnl
                    status = "TP HIT" if hit_tp else "SL HIT"

                    trades.append({
                        "Entry Time": active_trade["entry_time"].strftime("%Y-%m-%d %H:%M"),
                        "Exit Time": timestamp.strftime("%Y-%m-%d %H:%M"),
                        "Symbol": symbol,
                        "Type": "LONG",
                        "Entry Price": round(entry, 2),
                        "Exit Price": round(exit_price, 2),
                        "Initial SL": round(active_trade["initial_sl"], 2),
                        "Net PnL ($)": round(net_pnl, 2),
                        "Return (%)": round((net_pnl / (entry * size_units)) * 100, 2),
                        "Status": status,
                        "Balance ($)": round(balance, 2)
                    })
                    active_trade = None

            elif sig == -1:
                new_sl = min(sl, low + (2.5 * atr))
                active_trade["stop_loss"] = new_sl
                sl = new_sl

                hit_sl = high >= sl
                hit_tp = low <= tp

                if hit_sl or hit_tp:
                    exit_price = sl if hit_sl else tp
                    exit_price = exit_price * (1.0 + slippage) if hit_sl else exit_price * (1.0 + slippage)

                    gross_pnl = (entry - exit_price) * size_units
                    entry_fee = (entry * size_units) * fee_rate
                    exit_fee = (exit_price * size_units) * fee_rate
                    net_pnl = gross_pnl - entry_fee - exit_fee

                    balance += net_pnl
                    status = "TP HIT" if hit_tp else "SL HIT"

                    trades.append({
                        "Entry Time": active_trade["entry_time"].strftime("%Y-%m-%d %H:%M"),
                        "Exit Time": timestamp.strftime("%Y-%m-%d %H:%M"),
                        "Symbol": symbol,
                        "Type": "SHORT",
                        "Entry Price": round(entry, 2),
                        "Exit Price": round(exit_price, 2),
                        "Initial SL": round(active_trade["initial_sl"], 2),
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
            atr = row["atr"]

            # Apply entry slippage
            executed_entry = entry * (1.0 + slippage) if sig == 1 else entry * (1.0 - slippage)
            risk_dist = abs(executed_entry - sl)

            if risk_dist > 0:
                risk_dollars = balance * risk_per_trade
                units = risk_dollars / risk_dist

                active_trade = {
                    "signal": sig,
                    "entry_time": timestamp,
                    "entry_price": executed_entry,
                    "initial_sl": sl,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "atr": atr,
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

        # Max Drawdown
        s_equity = pd.Series(equity_curve)
        peak = s_equity.cummax()
        dd = (s_equity - peak) / peak
        max_dd_pct = abs(dd.min()) * 100.0

        total_return_pct = ((balance - initial_capital) / initial_capital) * 100.0
    else:
        win_count = loss_count = 0
        win_rate = profit_factor = max_dd_pct = total_return_pct = 0.0

    print(f"\n--- Trade Journal Output ---")
    if total_trades > 0:
        print(df_trades.to_string(index=False))
    else:
        print("No completed trades in backtest window.")

    print(f"\n================ PERFORMANCE SUMMARY ({symbol}) ================")
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
        "total_return_pct": total_return_pct,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_dd_pct": max_dd_pct,
        "trades_df": df_trades
    }

if __name__ == "__main__":
    for sym in ["BTC-USD", "ETH-USD", "SOL-USD"]:
        run_crypto_backtest(symbol=sym, period="60d", interval="1h")
