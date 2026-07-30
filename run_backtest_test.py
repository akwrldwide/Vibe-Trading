import yfinance as yf
import pandas as pd
import numpy as np
from signal_engine import NYOpenICTSignalEngine

def run_simulation(symbol: str, rr_ratio: float = 3.0):
    print(f"--- Downloading 1-minute intraday data for {symbol} (Testing 1:{rr_ratio:.0f} RR) ---")
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="7d", interval="1m")
    
    if df.empty:
        print(f"No data returned for {symbol}")
        return
        
    print(f"Downloaded {len(df)} 1m candles for {symbol} from {df.index[0]} to {df.index[-1]}")
    
    # Initialize Signal Engine with requested RR ratio
    engine = NYOpenICTSignalEngine(config={"rr_ratio": rr_ratio, "htf_ema_period": 50})
    
    # Generate Signals
    df_signals = engine.generate_signals(df)
    
    # Analyze Generated Signals
    trades = df_signals[df_signals["signal"] != 0]
    print(f"\nTotal Trade Signals Generated: {len(trades)}")
    
    if len(trades) == 0:
        print("No trade setups triggered in the last 7 days window.")
        return
        
    # Simulate Trade Execution and Performance
    results = []
    initial_balance = 100000.0
    balance = initial_balance
    risk_per_trade = 0.01  # 1% risk per trade
    
    wins = 0
    losses = 0
    
    for idx, row in trades.iterrows():
        sig = row["signal"]
        entry = row["entry_price"]
        sl = row["stop_loss"]
        tp = row["take_profit"]
        
        # Get future candles after entry
        future_candles = df_signals.loc[idx:].iloc[1:]
        
        hit_sl = False
        hit_tp = False
        
        for f_idx, f_row in future_candles.iterrows():
            if sig == 1: # Long
                if f_row["low"] <= sl:
                    hit_sl = True
                    break
                elif f_row["high"] >= tp:
                    hit_tp = True
                    break
            elif sig == -1: # Short
                if f_row["high"] >= sl:
                    hit_sl = True
                    break
                elif f_row["low"] <= tp:
                    hit_tp = True
                    break
                    
        pnl = 0.0
        trade_risk_amt = balance * risk_per_trade
        
        if hit_tp:
            pnl = trade_risk_amt * rr_ratio # 1:3 RR
            wins += 1
            status = "WIN (TP Hit)"
        elif hit_sl:
            pnl = -trade_risk_amt
            losses += 1
            status = "LOSS (SL Hit)"
        else:
            status = "OPEN (Session Ended)"
            
        balance += pnl
        
        results.append({
            "Time": idx.strftime("%Y-%m-%d %H:%M EST"),
            "Symbol": symbol,
            "Type": "BUY" if sig == 1 else "SELL",
            "Entry": round(entry, 5),
            "SL": round(sl, 5),
            "TP": round(tp, 5),
            "Result": status,
            "PnL": round(pnl, 2),
            "Balance": round(balance, 2)
        })
        
    res_df = pd.DataFrame(results)
    print(f"\n--- Trade Journal & Execution Results (1:{rr_ratio:.0f} RR) ---")
    print(res_df.to_string(index=False))
    
    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    ret_pct = ((balance - initial_balance) / initial_balance) * 100
    
    print(f"\n=== PERFORMANCE SUMMARY (1:{rr_ratio:.0f} RR) ===")
    print(f"Initial Capital: ${initial_balance:,.2f}")
    print(f"Ending Capital:  ${balance:,.2f}")
    print(f"Total Return:    {ret_pct:+.2f}%")
    print(f"Total Closed:    {total_trades}")
    print(f"Win / Loss:      {wins} W / {losses} L")
    print(f"Win Rate:        {win_rate:.1f}%")
    print("===========================\n")

if __name__ == "__main__":
    run_simulation("EURUSD=X", rr_ratio=2.0)
    run_simulation("GBPUSD=X", rr_ratio=2.0)
