import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

class LiquiditySweepSignalEngine:
    """
    Strategy 1: Liquidity Sweep & Market Structure Shift (MSS) Signal Engine v3.
    
    Features:
    1. 1.5 ATR Structure Stop Loss (prevents premature micro-wick stop-outs).
    2. Automated Breakeven Stop Loss (moves SL to Entry + 0.1 ATR when price hits 1:1 RR).
    3. 4H HTF Macro Trend Filter & ADX Volatility Filter.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.swing_window = int(self.config.get("swing_window", 48))
        self.atr_period = int(self.config.get("atr_period", 14))
        self.sl_buffer_atr = float(self.config.get("sl_buffer_atr", 1.5))
        self.tp_rr_ratio = float(self.config.get("tp_rr_ratio", 2.5))
        
        # Automated Breakeven Settings
        self.use_breakeven = bool(self.config.get("use_breakeven", True))
        self.be_trigger_rr = float(self.config.get("be_trigger_rr", 1.0))
        self.be_offset_atr = float(self.config.get("be_offset_atr", 0.1))
        
        # Confluence Filters
        self.use_htf_filter = bool(self.config.get("use_htf_filter", True))
        self.htf_ema_period = int(self.config.get("htf_ema_period", 50))
        
        self.use_volume_filter = bool(self.config.get("use_volume_filter", True))
        self.volume_sma_period = int(self.config.get("volume_sma_period", 20))
        self.volume_sma_mult = float(self.config.get("volume_sma_mult", 1.05))
        
        self.use_rsi_filter = bool(self.config.get("use_rsi_filter", True))
        self.rsi_period = int(self.config.get("rsi_period", 14))
        self.rsi_oversold = float(self.config.get("rsi_oversold", 45.0))
        self.rsi_overbought = float(self.config.get("rsi_overbought", 55.0))
        
        self.use_mss_filter = bool(self.config.get("use_mss_filter", True))

    def _calculate_rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))

    def generate_signals(self, df_in: pd.DataFrame) -> pd.DataFrame:
        df = df_in.copy()
        df.columns = [str(c).lower() for c in df.columns]

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # ATR calculation
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = df["tr"].ewm(span=self.atr_period, adjust=False).mean()

        # 4H HTF Macro Trend Bias Filter
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

        # Volume SMA
        if "volume" in df.columns and self.use_volume_filter:
            df["vol_sma"] = df["volume"].rolling(window=self.volume_sma_period, min_periods=1).mean()
        else:
            df["vol_sma"] = 0.0

        # RSI
        if self.use_rsi_filter:
            df["rsi"] = self._calculate_rsi(df["close"], self.rsi_period)
        else:
            df["rsi"] = 50.0

        # Rolling Swing High and Swing Low (excluding current candle)
        df["swing_high"] = df["high"].shift(1).rolling(window=self.swing_window, min_periods=self.swing_window).max()
        df["swing_low"] = df["low"].shift(1).rolling(window=self.swing_window, min_periods=self.swing_window).min()

        signals = np.zeros(len(df), dtype=int)
        entry_prices = np.full(len(df), np.nan)
        stop_losses = np.full(len(df), np.nan)
        take_profits = np.full(len(df), np.nan)

        high_p = df["high"].values
        low_p = df["low"].values
        close_p = df["close"].values
        open_p = df["open"].values
        atr_v = df["atr"].values
        swing_h = df["swing_high"].values
        swing_l = df["swing_low"].values
        htf_b = df["htf_bias"].values
        
        vol_v = df["volume"].values if "volume" in df.columns else np.zeros(len(df))
        vol_sma_v = df["vol_sma"].values
        rsi_v = df["rsi"].values

        for i in range(self.swing_window + 1, len(df)):
            if np.isnan(swing_h[i]) or np.isnan(swing_l[i]):
                continue

            # HTF Bias Check
            macro_long = (not self.use_htf_filter) or (htf_b[i] == 1)
            macro_short = (not self.use_htf_filter) or (htf_b[i] == -1)

            # Volume filter check
            vol_ok = True
            if self.use_volume_filter and "volume" in df.columns:
                vol_ok = vol_v[i] >= (vol_sma_v[i] * self.volume_sma_mult)

            candle_range = max(high_p[i] - low_p[i], 1e-6)

            # Bullish Liquidity Sweep (Long Signal)
            is_bullish_sweep = (low_p[i] < swing_l[i]) and (close_p[i] > swing_l[i])
            rsi_bullish_ok = (not self.use_rsi_filter) or (rsi_v[i] <= self.rsi_oversold or close_p[i] > open_p[i])
            mss_long_ok = (not self.use_mss_filter) or ((close_p[i] - low_p[i]) / candle_range >= 0.5 or close_p[i] > open_p[i])

            if is_bullish_sweep and macro_long and vol_ok and rsi_bullish_ok and mss_long_ok:
                entry = close_p[i]
                # SL placed 1.5 ATR safely below structure low
                sl = min(low_p[i], swing_l[i]) - (self.sl_buffer_atr * atr_v[i])
                risk = entry - sl
                if risk > 0:
                    tp = entry + (risk * self.tp_rr_ratio)
                    signals[i] = 1
                    entry_prices[i] = entry
                    stop_losses[i] = sl
                    take_profits[i] = tp
                    continue

            # Bearish Liquidity Sweep (Short Signal)
            is_bearish_sweep = (high_p[i] > swing_h[i]) and (close_p[i] < swing_h[i])
            rsi_bearish_ok = (not self.use_rsi_filter) or (rsi_v[i] >= self.rsi_overbought or close_p[i] < open_p[i])
            mss_short_ok = (not self.use_mss_filter) or ((high_p[i] - close_p[i]) / candle_range >= 0.5 or close_p[i] < open_p[i])

            if is_bearish_sweep and macro_short and vol_ok and rsi_bearish_ok and mss_short_ok:
                entry = close_p[i]
                # SL placed 1.5 ATR safely above structure high
                sl = max(high_p[i], swing_h[i]) + (self.sl_buffer_atr * atr_v[i])
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
