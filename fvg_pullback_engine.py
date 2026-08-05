import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

class FVGPullbackSignalEngine:
    """
    Strategy 2: FVG & EMA Pullback Trend Continuation Engine.
    
    Captures trend continuation setups when price pulls back into Fair Value Gap (FVG)
    imbalance zones or tests the 21 EMA during strong 4H/1H trending market regimes.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.fast_ema = int(self.config.get("fast_ema", 21))
        self.slow_ema = int(self.config.get("slow_ema", 50))
        self.htf_ema_period = int(self.config.get("htf_ema_period", 50))
        self.atr_period = int(self.config.get("atr_period", 14))
        self.sl_atr_mult = float(self.config.get("sl_atr_mult", 1.2))
        self.tp_rr_ratio = float(self.config.get("tp_rr_ratio", 2.5))
        self.min_fvg_atr = float(self.config.get("min_fvg_atr", 0.05))

    def generate_signals(self, df_in: pd.DataFrame) -> pd.DataFrame:
        df = df_in.copy()
        df.columns = [str(c).lower() for c in df.columns]

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # EMAs & ATR
        df["ema_fast"] = df["close"].ewm(span=self.fast_ema, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.slow_ema, adjust=False).mean()

        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = df["tr"].ewm(span=self.atr_period, adjust=False).mean()

        # 4H HTF Macro Trend Bias
        try:
            df_4h = df["close"].resample("4h").last().dropna()
            if len(df_4h) > self.htf_ema_period:
                ema_4h = df_4h.ewm(span=self.htf_ema_period, adjust=False).mean()
                bias_4h = np.where(df_4h >= ema_4h, 1, -1)
                s_bias = pd.Series(bias_4h, index=df_4h.index)
                df["htf_bias"] = s_bias.reindex(df.index, method="ffill").fillna(1)
            else:
                df["htf_bias"] = 1
        except Exception:
            df["htf_bias"] = 1

        # Local Trend Bias
        df["local_bias"] = np.where(df["ema_fast"] >= df["ema_slow"], 1, -1)

        # Fair Value Gap (FVG) detection
        # Bullish FVG at bar i: low[i] > high[i-2]
        # Bearish FVG at bar i: high[i] < low[i-2]
        high_series = df["high"]
        low_series = df["low"]
        
        df["fvg_bull_top"] = np.where(low_series > high_series.shift(2), low_series, np.nan)
        df["fvg_bull_bot"] = np.where(low_series > high_series.shift(2), high_series.shift(2), np.nan)
        
        df["fvg_bear_top"] = np.where(high_series < low_series.shift(2), low_series.shift(2), np.nan)
        df["fvg_bear_bot"] = np.where(high_series < low_series.shift(2), high_series, np.nan)

        # Forward fill recent FVG zones over last 5 bars
        df["recent_fvg_bull_top"] = df["fvg_bull_top"].ffill(limit=5)
        df["recent_fvg_bull_bot"] = df["fvg_bull_bot"].ffill(limit=5)
        df["recent_fvg_bear_top"] = df["fvg_bear_top"].ffill(limit=5)
        df["recent_fvg_bear_bot"] = df["fvg_bear_bot"].ffill(limit=5)

        signals = np.zeros(len(df), dtype=int)
        entry_prices = np.full(len(df), np.nan)
        stop_losses = np.full(len(df), np.nan)
        take_profits = np.full(len(df), np.nan)

        high_p = df["high"].values
        low_p = df["low"].values
        close_p = df["close"].values
        open_p = df["open"].values
        ema_f = df["ema_fast"].values
        atr_v = df["atr"].values
        htf_b = df["htf_bias"].values
        local_b = df["local_bias"].values
        
        fvg_b_top = df["recent_fvg_bull_top"].values
        fvg_b_bot = df["recent_fvg_bull_bot"].values
        fvg_r_top = df["recent_fvg_bear_top"].values
        fvg_r_bot = df["recent_fvg_bear_bot"].values

        for i in range(10, len(df)):
            macro_long = (htf_b[i] == 1) and (local_b[i] == 1)
            macro_short = (htf_b[i] == -1) and (local_b[i] == -1)

            candle_range = max(high_p[i] - low_p[i], 1e-6)

            # Bullish FVG / EMA Pullback Rejection (Long Signal)
            touches_ema = low_p[i] <= ema_f[i] and close_p[i] > ema_f[i]
            touches_fvg = False
            if not np.isnan(fvg_b_top[i]) and not np.isnan(fvg_b_bot[i]):
                touches_fvg = (low_p[i] <= fvg_b_top[i]) and (close_p[i] >= fvg_b_bot[i])

            bull_rejection = (close_p[i] > open_p[i]) or ((close_p[i] - low_p[i]) / candle_range >= 0.5)

            if macro_long and (touches_ema or touches_fvg) and bull_rejection:
                entry = close_p[i]
                sl = low_p[i] - (self.sl_atr_mult * atr_v[i])
                risk = entry - sl
                if risk > 0:
                    tp = entry + (risk * self.tp_rr_ratio)
                    signals[i] = 1
                    entry_prices[i] = entry
                    stop_losses[i] = sl
                    take_profits[i] = tp
                    continue

            # Bearish FVG / EMA Pullback Rejection (Short Signal)
            touches_ema_bear = high_p[i] >= ema_f[i] and close_p[i] < ema_f[i]
            touches_fvg_bear = False
            if not np.isnan(fvg_r_top[i]) and not np.isnan(fvg_r_bot[i]):
                touches_fvg_bear = (high_p[i] >= fvg_r_bot[i]) and (close_p[i] <= fvg_r_top[i])

            bear_rejection = (close_p[i] < open_p[i]) or ((high_p[i] - close_p[i]) / candle_range >= 0.5)

            if macro_short and (touches_ema_bear or touches_fvg_bear) and bear_rejection:
                entry = close_p[i]
                sl = high_p[i] + (self.sl_atr_mult * atr_v[i])
                risk = sl - entry
                if risk > 0:
                    tp = entry - (risk * self.tp_rr_ratio)
                    signals[i] = -1
                    entry_prices[i] = entry
                    stop_losses[i] = sl
                    take_profits[i] = tp

        df["signal"] = signals
        df["entry_price"] = entry_prices
        df["stop_loss"] = stop_losses
        df["take_profit"] = take_profits

        return df
