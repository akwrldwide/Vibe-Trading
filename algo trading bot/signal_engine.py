import numpy as np
import pandas as pd
from typing import Dict, Any

class NYOpenICTSignalEngine:
    """
    9:30 AM NY Open 15m Range + 1m FVG Retracement Strategy Engine
    Matches Pine Script logic in deriv_btcusd_930_ict_strategy.pine exactly.
    """
    def __init__(self, config: Dict[Any, Any] = None):
        self.config = config or {}
        self.rr_ratio = float(self.config.get("rr_ratio", 2.0))
        self.htf_ema_period = int(self.config.get("htf_ema_period", 50))
        self.htf_timeframe = str(self.config.get("htf_timeframe", "15m")).lower()  # '15m', '5m', or '1h'
        self.sl_mode = self.config.get("sl_mode", "fvg_struct") # 'fvg_struct' or 'session_peak'
        self.sl_buffer_pips = float(self.config.get("sl_buffer_pips", 2.0))
        self.use_htf_filter = bool(self.config.get("use_htf_filter", True))
        self.max_trades_per_day = int(self.config.get("max_trades_per_day", 2))
        self.server_offset_hours = float(self.config.get("server_offset_hours", 0.0))
        self.require_sl_or_clean_tp = bool(self.config.get("require_sl_or_clean_tp", False))
        self.require_sl_for_second_trade = bool(self.config.get("require_sl_for_second_trade", True))
        self.max_clean_tp_bars = int(self.config.get("max_clean_tp_bars", 10))
        self.max_clean_tp_drawdown_pct = float(self.config.get("max_clean_tp_drawdown_pct", 0.30))

    def generate_signals(self, df_1m: pd.DataFrame) -> pd.DataFrame:
        if df_1m.empty or len(df_1m) < 30:
            return pd.DataFrame()

        df = df_1m.copy()
        df.columns = [str(c).lower() for c in df.columns]

        if "time" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
            df.set_index("time", inplace=True)

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # Apply server time offset if specified (e.g. MT5 server time to UTC)
        if self.server_offset_hours != 0:
            df.index = df.index - pd.Timedelta(hours=self.server_offset_hours)

        if df.index.tz is None:
            df = df.tz_localize("UTC").tz_convert("America/New_York")
        else:
            df = df.tz_convert("America/New_York")

        df["time"] = df.index.time
        df["date"] = df.index.date

        # Dynamic HTF EMA Bias (Default: 15-Minute 50 EMA)
        resample_rule = "15min"
        if self.htf_timeframe == "5m":
            resample_rule = "5min"
        elif self.htf_timeframe == "1h":
            resample_rule = "1h"

        df_htf = df["close"].resample(resample_rule).last().ffill().dropna()
        ema_htf = df_htf.ewm(span=self.htf_ema_period, adjust=False).mean()
        df["htf_ema"] = ema_htf.reindex(df.index, method="ffill")
        df["htf_bias"] = np.where(df["close"] > df["htf_ema"], 1, -1)

        # 9:30 AM - 9:45 AM EST 15-Minute Opening Range Calculation (Date by Date)
        or_high_col = np.full(len(df), np.nan)
        or_low_col = np.full(len(df), np.nan)
        or_mid_col = np.full(len(df), np.nan)

        dates = df["date"].unique()
        for d in dates:
            mask_d = (df["date"] == d)
            mask_or = mask_d & (df["time"] >= pd.to_datetime("09:30").time()) & (df["time"] < pd.to_datetime("09:45").time())
            or_df = df[mask_or]
            if not or_df.empty:
                d_high = or_df["high"].max()
                d_low = or_df["low"].min()
                d_mid = (d_high + d_low) / 2.0
                
                # Lock OR levels starting from 09:45 AM for that date
                mask_post_or = mask_d & (df["time"] >= pd.to_datetime("09:45").time())
                or_high_col[mask_post_or] = d_high
                or_low_col[mask_post_or] = d_low
                or_mid_col[mask_post_or] = d_mid

        df["or_high"] = or_high_col
        df["or_low"] = or_low_col
        df["or_mid"] = or_mid_col

        # 1m FVG Detection
        df["fvg_bullish"] = df["low"] > df["high"].shift(2)
        df["fvg_bullish_ce"] = (df["high"].shift(2) + df["low"]) / 2.0

        df["fvg_bearish"] = df["high"] < df["low"].shift(2)
        df["fvg_bearish_ce"] = (df["low"].shift(2) + df["high"]) / 2.0

        # Trade window: 9:45 AM - 11:30 AM EST
        valid_time = (df["time"] >= pd.to_datetime("09:45").time()) & (df["time"] <= pd.to_datetime("11:30").time())

        signals = np.zeros(len(df))
        entry_prices = np.full(len(df), np.nan)
        stop_losses = np.full(len(df), np.nan)
        take_profits = np.full(len(df), np.nan)

        current_date = None
        daily_trade_count = 0
        breakout_state = 0 # 1 = Bullish Breakout, -1 = Bearish Breakout, 0 = Neutral
        active_trade = None
        allow_second_trade = False

        for i in range(3, len(df)):
            date_i = df["date"].iloc[i]
            if date_i != current_date:
                current_date = date_i
                daily_trade_count = 0
                breakout_state = 0
                active_trade = None
                allow_second_trade = False

            # Track active Trade 1 outcome to determine eligibility for Trade 2
            if active_trade is not None:
                entry_idx = active_trade["entry_idx"]
                bars_held = i - entry_idx
                direction = active_trade["direction"]
                entry_p = active_trade["entry"]
                sl_p = active_trade["sl"]
                tp_p = active_trade["tp"]
                risk_v = active_trade["risk"]

                if direction == 1: # Long
                    cur_low = df["low"].iloc[i]
                    cur_high = df["high"].iloc[i]
                    drawdown = max(0.0, entry_p - cur_low)
                    active_trade["max_drawdown"] = max(active_trade["max_drawdown"], drawdown)

                    if cur_low <= sl_p:
                        # Trade 1 hit SL -> Allow second trade (Loss Recovery)
                        allow_second_trade = True
                        active_trade = None
                    elif cur_high >= tp_p:
                        # Trade 1 hit TP -> Lock in profit, block second trade!
                        if self.require_sl_for_second_trade:
                            allow_second_trade = False
                        else:
                            drawdown_pct = active_trade["max_drawdown"] / risk_v if risk_v > 0 else 0.0
                            is_clean = (bars_held <= self.max_clean_tp_bars) or (drawdown_pct <= self.max_clean_tp_drawdown_pct)
                            allow_second_trade = is_clean
                        active_trade = None

                elif direction == -1: # Short
                    cur_low = df["low"].iloc[i]
                    cur_high = df["high"].iloc[i]
                    drawdown = max(0.0, cur_high - entry_p)
                    active_trade["max_drawdown"] = max(active_trade["max_drawdown"], drawdown)

                    if cur_high >= sl_p:
                        # Trade 1 hit SL -> Allow second trade (Loss Recovery)
                        allow_second_trade = True
                        active_trade = None
                    elif cur_low <= tp_p:
                        # Trade 1 hit TP -> Lock in profit, block second trade!
                        if self.require_sl_for_second_trade:
                            allow_second_trade = False
                        else:
                            drawdown_pct = active_trade["max_drawdown"] / risk_v if risk_v > 0 else 0.0
                            is_clean = (bars_held <= self.max_clean_tp_bars) or (drawdown_pct <= self.max_clean_tp_drawdown_pct)
                            allow_second_trade = is_clean
                        active_trade = None

            if not valid_time.iloc[i] or np.isnan(df["or_high"].iloc[i]):
                continue

            or_h = df["or_high"].iloc[i]
            or_l = df["or_low"].iloc[i]
            or_m = df["or_mid"].iloc[i]
            close_i = df["close"].iloc[i]

            # Range Midpoint Invalidation State Machine (Matching Pine Script)
            if breakout_state == 1 and close_i < or_m:
                breakout_state = 0
            elif breakout_state == -1 and close_i > or_m:
                breakout_state = 0

            if close_i > or_h:
                breakout_state = 1
            elif close_i < or_l:
                breakout_state = -1

            if daily_trade_count >= self.max_trades_per_day:
                continue

            if daily_trade_count == 1:
                if active_trade is not None:
                    continue # Trade 1 is still open
                if (self.require_sl_for_second_trade or self.require_sl_or_clean_tp) and not allow_second_trade:
                    continue # Trade 2 blocked: Trade 1 did not hit SL

            current_close = close_i
            if current_close > 500:
                pip_size = 1.0
            elif current_close > 50:
                pip_size = 0.01
            else:
                pip_size = 0.0001

            buffer_val = self.sl_buffer_pips * pip_size

            # Qualification Rules matching official ict_930_deriv_multi_tp_indicator.pine
            bullish_c1_breakout = (df["close"].iloc[i-2] > or_h and df["close"].iloc[i-3] <= or_h)
            bullish_c2_breakout = (df["close"].iloc[i-1] > or_h and df["close"].iloc[i-2] <= or_h)
            bullish_after_breakout = (breakout_state == 1) and (df["close"].iloc[i-2] > or_h and df["close"].iloc[i-1] > or_h)
            valid_bullish_breakout_pos = bullish_c1_breakout or bullish_c2_breakout or bullish_after_breakout
            bullish_c3_confirms = (close_i > or_h)

            bearish_c1_breakout = (df["close"].iloc[i-2] < or_l and df["close"].iloc[i-3] >= or_l)
            bearish_c2_breakout = (df["close"].iloc[i-1] < or_l and df["close"].iloc[i-2] >= or_l)
            bearish_after_breakout = (breakout_state == -1) and (df["close"].iloc[i-2] < or_l and df["close"].iloc[i-1] < or_l)
            valid_bearish_breakout_pos = bearish_c1_breakout or bearish_c2_breakout or bearish_after_breakout
            bearish_c3_confirms = (close_i < or_l)

            htf_ok_long = (not self.use_htf_filter) or (df["htf_bias"].iloc[i] == 1)
            htf_ok_short = (not self.use_htf_filter) or (df["htf_bias"].iloc[i] == -1)

            if valid_bullish_breakout_pos and bullish_c3_confirms and df["fvg_bullish"].iloc[i] and htf_ok_long:
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
                    daily_trade_count += 1
                    if daily_trade_count == 1:
                        active_trade = {
                            "direction": 1,
                            "entry": entry,
                            "sl": sl,
                            "tp": tp,
                            "risk": risk,
                            "entry_idx": i,
                            "max_drawdown": 0.0
                        }

            elif valid_bearish_breakout_pos and bearish_c3_confirms and df["fvg_bearish"].iloc[i] and htf_ok_short:
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
                    daily_trade_count += 1
                    if daily_trade_count == 1:
                        active_trade = {
                            "direction": -1,
                            "entry": entry,
                            "sl": sl,
                            "tp": tp,
                            "risk": risk,
                            "entry_idx": i,
                            "max_drawdown": 0.0
                        }

        df["signal"] = signals
        df["entry_price"] = entry_prices
        df["stop_loss"] = stop_losses
        df["take_profit"] = take_profits

        return df
