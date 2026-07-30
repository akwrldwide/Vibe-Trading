import numpy as np
import pandas as pd
from typing import Dict, Any

class NYOpenICTSignalEngine:
    """
    9:30 AM NY Open 15m Range + 1m FVG Retracement Strategy Engine
    Stop Loss: Highest peak high / lowest peak low from 9:30 AM to breakout moment.
    """
    def __init__(self, config: Dict[Any, Any] = None):
        self.config = config or {}
        self.rr_ratio = float(self.config.get("rr_ratio", 2.0))
        self.htf_ema_period = int(self.config.get("htf_ema_period", 50))
        self.sl_mode = self.config.get("sl_mode", "fvg_struct") # 'fvg_struct' or 'session_peak'
        self.sl_buffer_pips = float(self.config.get("sl_buffer_pips", 2.0))
        
    def generate_signals(self, df_1m: pd.DataFrame) -> pd.DataFrame:
        df = df_1m.copy()
        df.columns = [str(c).lower() for c in df.columns]
        
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
            
        if df.index.tz is None:
            df = df.tz_localize("UTC").tz_convert("America/New_York")
        else:
            df = df.tz_convert("America/New_York")
            
        df["time"] = df.index.time
        df["date"] = df.index.date
        
        # 1H HTF EMA Bias
        df_1h = df["close"].resample("1h").last().ffill().dropna()
        ema_1h = df_1h.ewm(span=self.htf_ema_period, adjust=False).mean()
        df["htf_ema"] = ema_1h.reindex(df.index, method="ffill")
        df["htf_bias"] = np.where(df["close"] > df["htf_ema"], 1, -1)
        
        # 9:30 - 9:45 AM 15m Range
        df["is_or_candle"] = (df["time"] >= pd.to_datetime("09:30").time()) & (df["time"] < pd.to_datetime("09:45").time())
        or_highs = df[df["is_or_candle"]].groupby("date")["high"].transform("max")
        or_lows = df[df["is_or_candle"]].groupby("date")["low"].transform("min")
        
        df["or_high"] = or_highs.reindex(df.index, method="ffill")
        df["or_low"] = or_lows.reindex(df.index, method="ffill")
        
        # 1m FVG Detection
        df["fvg_bullish"] = df["low"] > df["high"].shift(2)
        df["fvg_bullish_ce"] = (df["high"].shift(2) + df["low"]) / 2.0
        
        df["fvg_bearish"] = df["high"] < df["low"].shift(2)
        df["fvg_bearish_ce"] = (df["low"].shift(2) + df["high"]) / 2.0
        
        # Trade window: 9:45 AM - 11:30 AM, excluding 09:55-10:05
        trade_window = (df["time"] >= pd.to_datetime("09:45").time()) & (df["time"] <= pd.to_datetime("11:30").time())
        news_guard = ~((df["time"] >= pd.to_datetime("09:55").time()) & (df["time"] <= pd.to_datetime("10:05").time()))
        valid_time = trade_window & news_guard
        
        breakout_up = (df["close"] > df["or_high"]) & (df["close"].shift(1) <= df["or_high"])
        breakout_down = (df["close"] < df["or_low"]) & (df["close"].shift(1) >= df["or_low"])
        
        signals = np.zeros(len(df))
        entry_prices = np.full(len(df), np.nan)
        stop_losses = np.full(len(df), np.nan)
        take_profits = np.full(len(df), np.nan)
        
        buffer_val = self.sl_buffer_pips * 0.0001
        
        for i in range(3, len(df)):
            if not valid_time.iloc[i]:
                continue
                
            # Long Setup
            if (breakout_up.iloc[i] or breakout_up.iloc[i-1] or breakout_up.iloc[i-2]) and df["fvg_bullish"].iloc[i]:
                entry = df["fvg_bullish_ce"].iloc[i]
                struct_low = min(df["low"].iloc[i], df["low"].iloc[i-1], df["low"].iloc[i-2], df["low"].iloc[i-3])
                sl = struct_low - buffer_val
                
                risk = entry - sl
                if risk > 0:
                    tp = entry + (risk * self.rr_ratio)
                    signals[i] = 1
                    entry_prices[i] = entry
                    stop_losses[i] = sl
                    take_profits[i] = tp
                    
            # Short Setup
            elif (breakout_down.iloc[i] or breakout_down.iloc[i-1] or breakout_down.iloc[i-2]) and df["fvg_bearish"].iloc[i]:
                entry = df["fvg_bearish_ce"].iloc[i]
                struct_high = max(df["high"].iloc[i], df["high"].iloc[i-1], df["high"].iloc[i-2], df["high"].iloc[i-3])
                sl = struct_high + buffer_val
                
                risk = sl - entry
                if risk > 0:
                    tp = entry - (risk * self.rr_ratio)
                    signals[i] = -1
                    entry_prices[i] = entry
                    stop_losses[i] = sl
                    take_profits[i] = tp
                    
        df["signal"] = signals
        df["entry_price"] = entry_prices
        df["stop_loss"] = stop_losses
        df["take_profit"] = take_profits
        
        return df
