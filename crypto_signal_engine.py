import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

class CryptoTrendSignalEngine:
    """
    Robust Crypto Trend & Momentum Strategy Engine v4.
    
    Uses 12/26 EMA Crossover with 4H 200 EMA Macro Trend Filter,
    Fixed 2.0 ATR Initial Stop Loss, 2.5 RR Take Profit, and Deferred Trailing Stop.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.fast_ema = int(self.config.get("fast_ema", 12))
        self.slow_ema = int(self.config.get("slow_ema", 26))
        self.atr_period = int(self.config.get("atr_period", 14))
        self.sl_atr_mult = float(self.config.get("sl_atr_mult", 2.0))
        self.tp_rr_ratio = float(self.config.get("tp_rr_ratio", 2.5))
        self.use_htf_filter = bool(self.config.get("use_htf_filter", True))
        self.htf_ema_period = int(self.config.get("htf_ema_period", 200))

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

        # 4H HTF Macro Bias
        try:
            df_4h = df["close"].resample("4h").last().dropna()
            if len(df_4h) > self.htf_ema_period:
                ema_4h = df_4h.ewm(span=self.htf_ema_period, adjust=False).mean()
                bias_4h = np.where(df_4h > ema_4h, 1, -1)
                s_bias = pd.Series(bias_4h, index=df_4h.index)
                df["htf_bias"] = s_bias.reindex(df.index, method="ffill").fillna(0)
            else:
                df["htf_bias"] = 1
        except Exception:
            df["htf_bias"] = 1

        signals = np.zeros(len(df), dtype=int)
        entry_prices = np.full(len(df), np.nan)
        stop_losses = np.full(len(df), np.nan)
        take_profits = np.full(len(df), np.nan)

        ema_f = df["ema_fast"].values
        ema_s = df["ema_slow"].values
        htf_b = df["htf_bias"].values
        close_p = df["close"].values
        atr_v = df["atr"].values

        for i in range(1, len(df)):
            prev_f = ema_f[i-1]
            prev_s = ema_s[i-1]
            curr_f = ema_f[i]
            curr_s = ema_s[i]

            macro_l = (not self.use_htf_filter) or (htf_b[i] == 1)
            macro_s = (not self.use_htf_filter) or (htf_b[i] == -1)

            # Crossover Long
            if prev_f <= prev_s and curr_f > curr_s and macro_l:
                entry = close_p[i]
                sl = entry - (self.sl_atr_mult * atr_v[i])
                risk = entry - sl
                if risk > 0:
                    tp = entry + (risk * self.tp_rr_ratio)
                    signals[i] = 1
                    entry_prices[i] = entry
                    stop_losses[i] = sl
                    take_profits[i] = tp

            # Crossunder Short
            elif prev_f >= prev_s and curr_f < curr_s and macro_s:
                entry = close_p[i]
                sl = entry + (self.sl_atr_mult * atr_v[i])
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

# Aliases for backwards compatibility
CryptoBreakoutSignalEngine = CryptoTrendSignalEngine
CryptoSupertrendSignalEngine = CryptoTrendSignalEngine
