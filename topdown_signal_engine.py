import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

class TopDownLiquiditySignalEngine:
    """
    Top-Down Multi-Timeframe Crypto Liquidity Sweep & Market Structure Shift (MSS) Signal Engine.
    
    Architecture:
    1. 4H Macro Bias Filter (50 EMA).
    2. Dynamic Liquidity Reference Levels (PDH/PDL, Asian Session 00:00-08:00 UTC, Rolling 32-bar Swings).
    3. Liquidity Sweep Detection (wick rejection + volume absorption).
    4. Market Structure Shift (MSS) Confirmation with Fair Value Gap (FVG) Displacement.
    5. Retracement Limit Entry at 50% Consequent Encroachment (CE).
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.htf_ema_period = int(self.config.get("htf_ema_period", 50))
        self.swing_lookback = int(self.config.get("swing_lookback", 32))
        self.atr_period = int(self.config.get("atr_period", 14))
        self.sl_buffer_atr = float(self.config.get("sl_buffer_atr", 0.5))
        self.tp1_rr = float(self.config.get("tp1_rr", 1.5))
        self.tp2_rr = float(self.config.get("tp2_rr", 3.0))
        self.vol_mult = float(self.config.get("vol_mult", 1.05))
        self.sweep_wick_pct = float(self.config.get("sweep_wick_pct", 0.20))
        self.mss_lookback = int(self.config.get("mss_lookback", 12))
        self.use_htf_filter = bool(self.config.get("use_htf_filter", True))

    def generate_signals(self, df_in: pd.DataFrame) -> pd.DataFrame:
        df = df_in.copy()
        df.columns = [str(c).lower() for c in df.columns]

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # 1. Indicators
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = df["tr"].rolling(self.atr_period).mean().bfill()

        df["vol_sma"] = df["volume"].rolling(20).mean().bfill()
        df["vol_spike"] = df["volume"] >= (df["vol_sma"] * self.vol_mult)

        # 2. 4H Macro Bias
        try:
            df_4h = df["close"].resample("4h").last().dropna()
            if len(df_4h) >= self.htf_ema_period:
                ema_4h = df_4h.ewm(span=self.htf_ema_period, adjust=False).mean()
                bias_4h = np.where(df_4h >= ema_4h, 1, -1)
                s_bias = pd.Series(bias_4h, index=df_4h.index)
                df["htf_bias"] = s_bias.reindex(df.index, method="ffill").fillna(1)
            else:
                df["htf_bias"] = 1
        except Exception:
            df["htf_bias"] = 1

        # 3. Liquidity Levels
        df["date"] = df.index.date
        daily_highs = df.groupby("date")["high"].max().shift(1)
        daily_lows = df.groupby("date")["low"].min().shift(1)
        df["pdh"] = df["date"].map(daily_highs).ffill()
        df["pdl"] = df["date"].map(daily_lows).ffill()

        df["swing_high"] = df["high"].shift(1).rolling(self.swing_lookback).max()
        df["swing_low"] = df["low"].shift(1).rolling(self.swing_lookback).min()

        df["is_asian"] = (df.index.hour >= 0) & (df.index.hour < 8)
        asian_highs = df[df["is_asian"]].groupby("date")["high"].transform("max")
        asian_lows = df[df["is_asian"]].groupby("date")["low"].transform("min")
        df["asian_high"] = asian_highs.reindex(df.index, method="ffill")
        df["asian_low"] = asian_lows.reindex(df.index, method="ffill")

        # 4. Sweep Detection
        bar_range = np.maximum(df["high"] - df["low"], 1e-6)
        lower_wick = np.minimum(df["open"], df["close"]) - df["low"]
        upper_wick = df["high"] - np.maximum(df["open"], df["close"])

        swept_bull = (df["low"] < df["pdl"]) | (df["low"] < df["asian_low"]) | (df["low"] < df["swing_low"])
        closed_above_bull = (df["close"] >= df["pdl"]) | (df["close"] >= df["asian_low"]) | (df["close"] >= df["swing_low"])
        bull_wick = (lower_wick / bar_range) >= self.sweep_wick_pct
        df["bull_sweep"] = swept_bull & (closed_above_bull | bull_wick) & df["vol_spike"] & (df["htf_bias"] == 1 if self.use_htf_filter else True)

        swept_bear = (df["high"] > df["pdh"]) | (df["high"] > df["asian_high"]) | (df["high"] > df["swing_high"])
        closed_below_bear = (df["close"] <= df["pdh"]) | (df["close"] <= df["asian_high"]) | (df["close"] <= df["swing_high"])
        bear_wick = (upper_wick / bar_range) >= self.sweep_wick_pct
        df["bear_sweep"] = swept_bear & (closed_below_bear | bear_wick) & df["vol_spike"] & (df["htf_bias"] == -1 if self.use_htf_filter else True)

        # 5. FVG Detection
        df["bull_fvg"] = df["low"] > df["high"].shift(2)
        df["bull_fvg_ce"] = (df["high"].shift(2) + df["low"]) / 2.0

        df["bear_fvg"] = df["high"] < df["low"].shift(2)
        df["bear_fvg_ce"] = (df["low"].shift(2) + df["high"]) / 2.0

        signals = np.zeros(len(df), dtype=int)
        entry_prices = np.full(len(df), np.nan)
        stop_losses = np.full(len(df), np.nan)
        tp1_targets = np.full(len(df), np.nan)
        tp2_targets = np.full(len(df), np.nan)

        last_sweep_bar = -999
        last_sweep_type = None
        last_sweep_extreme = None

        for i in range(50, len(df)):
            curr_row = df.iloc[i]

            if curr_row["bull_sweep"]:
                last_sweep_bar = i
                last_sweep_type = "BULL"
                last_sweep_extreme = curr_row["low"]
            elif curr_row["bear_sweep"]:
                last_sweep_bar = i
                last_sweep_type = "BEAR"
                last_sweep_extreme = curr_row["high"]

            bars_since = i - last_sweep_bar
            if 1 <= bars_since <= self.mss_lookback:
                local_high = df["high"].iloc[i-5:i].max()
                local_low = df["low"].iloc[i-5:i].min()

                if last_sweep_type == "BULL" and curr_row["close"] > local_high and curr_row["bull_fvg"]:
                    entry = curr_row["bull_fvg_ce"]
                    sl = last_sweep_extreme - (curr_row["atr"] * self.sl_buffer_atr)
                    risk = entry - sl
                    if risk > 0:
                        signals[i] = 1
                        entry_prices[i] = entry
                        stop_losses[i] = sl
                        tp1_targets[i] = entry + (risk * self.tp1_rr)
                        tp2_targets[i] = entry + (risk * self.tp2_rr)
                        last_sweep_bar = -999

                elif last_sweep_type == "BEAR" and curr_row["close"] < local_low and curr_row["bear_fvg"]:
                    entry = curr_row["bear_fvg_ce"]
                    sl = last_sweep_extreme + (curr_row["atr"] * self.sl_buffer_atr)
                    risk = sl - entry
                    if risk > 0:
                        signals[i] = -1
                        entry_prices[i] = entry
                        stop_losses[i] = sl
                        tp1_targets[i] = entry - (risk * self.tp1_rr)
                        tp2_targets[i] = entry - (risk * self.tp2_rr)
                        last_sweep_bar = -999

        df["signal"] = signals
        df["entry_price"] = entry_prices
        df["stop_loss"] = stop_losses
        df["tp1"] = tp1_targets
        df["tp2"] = tp2_targets

        return df
