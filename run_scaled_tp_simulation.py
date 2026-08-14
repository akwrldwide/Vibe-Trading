"""
Scale-Out / Partial TP Test for Liquidity Sweep + IFVG
- TP1: 50% position closed at 1:1.4 RR -> SL shifted to Breakeven (+0.1 ATR)
- TP2: 50% position closed at 1:2.8 RR
"""

import math
import random
from run_ifvg_sweep_simulation import CryptoAssetSimulator

class ScaledIFVGStrategy:
    def __init__(
        self,
        swing_window: int = 40,
        atr_period: int = 14,
        sl_buffer_atr: float = 0.5,
        tp1_rr: float = 1.4,
        tp2_rr: float = 2.8,
        use_htf_filter: bool = True,
        htf_ema_period: int = 50,
        use_retest_entry: bool = True,
        taker_fee: float = 0.0005,
        slippage: float = 0.0005
    ):
        self.swing_window = swing_window
        self.atr_period = atr_period
        self.sl_buffer_atr = sl_buffer_atr
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr
        self.use_htf_filter = use_htf_filter
        self.htf_ema_period = htf_ema_period
        self.use_retest_entry = use_retest_entry
        self.taker_fee = taker_fee
        self.slippage = slippage

    def run_backtest(self, bars, initial_capital: float = 100000.0, risk_per_trade: float = 0.015):
        n = len(bars)
        if n < self.swing_window + 50:
            return None

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        opens = [b["open"] for b in bars]

        # ATR
        tr = [0.0] * n
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
        atr = [0.0] * n
        alpha = 1.0 / self.atr_period
        atr[0] = tr[0]
        for i in range(1, n):
            atr[i] = alpha * tr[i] + (1 - alpha) * atr[i-1]

        # 4H EMA
        ema_period_bars = self.htf_ema_period * 16
        ema_4h = [0.0] * n
        ema_alpha = 2.0 / (ema_period_bars + 1)
        ema_4h[0] = closes[0]
        for i in range(1, n):
            ema_4h[i] = ema_alpha * closes[i] + (1 - ema_alpha) * ema_4h[i-1]

        # FVGs
        bear_fvgs = [None] * n
        bull_fvgs = [None] * n
        for i in range(2, n):
            if highs[i] < lows[i-2]:
                bear_fvgs[i] = {"top": lows[i-2], "bot": highs[i], "bar": i}
            if lows[i] > highs[i-2]:
                bull_fvgs[i] = {"top": lows[i], "bot": highs[i-2], "bar": i}

        capital = initial_capital
        equity_curve = [capital]
        trades = []
        active_trade = None

        recent_bull_sweep = None
        recent_bear_sweep = None

        for i in range(self.swing_window + 5, n):
            cur_high = highs[i]
            cur_low = lows[i]
            cur_close = closes[i]
            cur_open = opens[i]
            cur_atr = max(atr[i], 1e-5)
            cur_time = bars[i]["time"]

            swing_h = max(highs[i - self.swing_window:i])
            swing_l = min(lows[i - self.swing_window:i])

            if cur_low < swing_l and cur_close >= swing_l:
                recent_bull_sweep = {"bar": i, "sweep_low": cur_low, "atr": cur_atr}
            if cur_high > swing_h and cur_close <= swing_h:
                recent_bear_sweep = {"bar": i, "sweep_high": cur_high, "atr": cur_atr}

            if recent_bull_sweep and (i - recent_bull_sweep["bar"] > 15):
                recent_bull_sweep = None
            if recent_bear_sweep and (i - recent_bear_sweep["bar"] > 15):
                recent_bear_sweep = None

            # Manage Active Trade
            if active_trade is not None:
                side = active_trade["side"]
                entry = active_trade["entry_price"]
                sl = active_trade["stop_loss"]
                tp1 = active_trade["tp1"]
                tp2 = active_trade["tp2"]
                rem_units = active_trade["rem_units"]
                total_units = active_trade["total_units"]

                if side == "LONG":
                    # Check TP1
                    if not active_trade["tp1_hit"] and cur_high >= tp1:
                        # Close 50%
                        exit_p = tp1 * (1.0 - self.slippage)
                        units_to_close = total_units * 0.5
                        pnl = (exit_p - entry) * units_to_close
                        fee = (entry * units_to_close * self.taker_fee) + (exit_p * units_to_close * self.taker_fee)
                        net_pnl = pnl - fee
                        capital += net_pnl
                        active_trade["realized_pnl"] += net_pnl
                        active_trade["tp1_hit"] = True
                        active_trade["rem_units"] -= units_to_close
                        rem_units = active_trade["rem_units"]
                        # Shift SL to Breakeven + buffer
                        sl = entry + (0.1 * cur_atr)
                        active_trade["stop_loss"] = sl

                    # Check TP2 or SL
                    hit_sl = cur_low <= sl
                    hit_tp2 = cur_high >= tp2

                    if hit_sl or hit_tp2:
                        exit_p = tp2 if hit_tp2 else sl
                        exit_p = exit_p * (1.0 - self.slippage)
                        pnl = (exit_p - entry) * rem_units
                        fee = (entry * rem_units * self.taker_fee) + (exit_p * rem_units * self.taker_fee)
                        net_pnl = pnl - fee
                        capital += net_pnl
                        active_trade["realized_pnl"] += net_pnl

                        outcome = "TP2" if hit_tp2 else ("BE" if active_trade["tp1_hit"] else "SL")
                        trades.append({
                            "side": "LONG",
                            "entry_price": entry,
                            "net_pnl": active_trade["realized_pnl"],
                            "outcome": outcome,
                            "capital": capital
                        })
                        active_trade = None

                elif side == "SHORT":
                    # Check TP1
                    if not active_trade["tp1_hit"] and cur_low <= tp1:
                        exit_p = tp1 * (1.0 + self.slippage)
                        units_to_close = total_units * 0.5
                        pnl = (entry - exit_p) * units_to_close
                        fee = (entry * units_to_close * self.taker_fee) + (exit_p * units_to_close * self.taker_fee)
                        net_pnl = pnl - fee
                        capital += net_pnl
                        active_trade["realized_pnl"] += net_pnl
                        active_trade["tp1_hit"] = True
                        active_trade["rem_units"] -= units_to_close
                        rem_units = active_trade["rem_units"]
                        sl = entry - (0.1 * cur_atr)
                        active_trade["stop_loss"] = sl

                    hit_sl = cur_high >= sl
                    hit_tp2 = cur_low <= tp2

                    if hit_sl or hit_tp2:
                        exit_p = tp2 if hit_tp2 else sl
                        exit_p = exit_p * (1.0 + self.slippage)
                        pnl = (entry - exit_p) * rem_units
                        fee = (entry * rem_units * self.taker_fee) + (exit_p * rem_units * self.taker_fee)
                        net_pnl = pnl - fee
                        capital += net_pnl
                        active_trade["realized_pnl"] += net_pnl

                        outcome = "TP2" if hit_tp2 else ("BE" if active_trade["tp1_hit"] else "SL")
                        trades.append({
                            "side": "SHORT",
                            "entry_price": entry,
                            "net_pnl": active_trade["realized_pnl"],
                            "outcome": outcome,
                            "capital": capital
                        })
                        active_trade = None

            # New Entry
            if active_trade is None:
                # Long
                if recent_bull_sweep is not None:
                    target_fvg = None
                    for b_idx in range(max(2, recent_bull_sweep["bar"] - 5), i):
                        if bear_fvgs[b_idx] is not None:
                            target_fvg = bear_fvgs[b_idx]
                            break

                    if target_fvg is not None:
                        if cur_close > target_fvg["top"] and closes[i-1] <= target_fvg["top"]:
                            htf_ok = not self.use_htf_filter or (cur_close >= ema_4h[i])
                            if htf_ok:
                                entry_p = cur_close * (1.0 + self.slippage)
                                if self.use_retest_entry:
                                    fvg_mid = (target_fvg["top"] + target_fvg["bot"]) / 2.0
                                    entry_p = (entry_p * 0.5) + (fvg_mid * 0.5)

                                sl_p = recent_bull_sweep["sweep_low"] - (self.sl_buffer_atr * cur_atr)
                                risk_dist = max(entry_p - sl_p, cur_atr * 0.4)
                                tp1_p = entry_p + (risk_dist * self.tp1_rr)
                                tp2_p = entry_p + (risk_dist * self.tp2_rr)

                                risk_amount = capital * risk_per_trade
                                pos_units = risk_amount / risk_dist

                                active_trade = {
                                    "side": "LONG",
                                    "time": cur_time,
                                    "entry_price": entry_p,
                                    "stop_loss": sl_p,
                                    "tp1": tp1_p,
                                    "tp2": tp2_p,
                                    "total_units": pos_units,
                                    "rem_units": pos_units,
                                    "tp1_hit": False,
                                    "realized_pnl": 0.0
                                }
                                recent_bull_sweep = None

                # Short
                if recent_bear_sweep is not None and active_trade is None:
                    target_fvg = None
                    for b_idx in range(max(2, recent_bear_sweep["bar"] - 5), i):
                        if bull_fvgs[b_idx] is not None:
                            target_fvg = bull_fvgs[b_idx]
                            break

                    if target_fvg is not None:
                        if cur_close < target_fvg["bot"] and closes[i-1] >= target_fvg["bot"]:
                            htf_ok = not self.use_htf_filter or (cur_close <= ema_4h[i])
                            if htf_ok:
                                entry_p = cur_close * (1.0 - self.slippage)
                                if self.use_retest_entry:
                                    fvg_mid = (target_fvg["top"] + target_fvg["bot"]) / 2.0
                                    entry_p = (entry_p * 0.5) + (fvg_mid * 0.5)

                                sl_p = recent_bear_sweep["sweep_high"] + (self.sl_buffer_atr * cur_atr)
                                risk_dist = max(sl_p - entry_p, cur_atr * 0.4)
                                tp1_p = entry_p - (risk_dist * self.tp1_rr)
                                tp2_p = entry_p - (risk_dist * self.tp2_rr)

                                risk_amount = capital * risk_per_trade
                                pos_units = risk_amount / risk_dist

                                active_trade = {
                                    "side": "SHORT",
                                    "time": cur_time,
                                    "entry_price": entry_p,
                                    "stop_loss": sl_p,
                                    "tp1": tp1_p,
                                    "tp2": tp2_p,
                                    "total_units": pos_units,
                                    "rem_units": pos_units,
                                    "tp1_hit": False,
                                    "realized_pnl": 0.0
                                }
                                recent_bear_sweep = None

            equity_curve.append(capital)

        total_trades = len(trades)
        if total_trades == 0:
            return {"symbol": "N/A", "total_trades": 0, "win_rate": 0, "net_pnl": 0}

        wins = [t for t in trades if t["net_pnl"] > 0]
        losses = [t for t in trades if t["net_pnl"] < 0]

        win_rate = (len(wins) / total_trades) * 100.0
        gross_profit = sum(t["net_pnl"] for t in wins)
        gross_loss = abs(sum(t["net_pnl"] for t in losses)) if losses else 1e-5
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        peak = initial_capital
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100.0
            if dd > max_dd:
                max_dd = dd

        return {
            "initial_capital": initial_capital,
            "final_capital": round(capital, 2),
            "net_profit": round(capital - initial_capital, 2),
            "return_pct": round(((capital - initial_capital) / initial_capital) * 100.0, 2),
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd, 2)
        }


def run_scaled_tp_simulation():
    coins = [
        {"symbol": "BTC-USDT", "base_price": 94500.0, "daily_vol": 0.038, "trend": 0.0003, "seed": 101},
        {"symbol": "ETH-USDT", "base_price": 3400.0, "daily_vol": 0.052, "trend": 0.0002, "seed": 202},
        {"symbol": "SOL-USDT", "base_price": 195.0, "daily_vol": 0.075, "trend": 0.0004, "seed": 303},
        {"symbol": "BNB-USDT", "base_price": 680.0, "daily_vol": 0.035, "trend": 0.0001, "seed": 404},
        {"symbol": "AVAX-USDT", "base_price": 32.0, "daily_vol": 0.082, "trend": 0.0002, "seed": 505},
        {"symbol": "DOGE-USDT", "base_price": 0.22, "daily_vol": 0.095, "trend": 0.0001, "seed": 606},
        {"symbol": "LINK-USDT", "base_price": 18.5, "daily_vol": 0.065, "trend": 0.0003, "seed": 707}
    ]

    strat = ScaledIFVGStrategy(
        swing_window=40,
        atr_period=14,
        sl_buffer_atr=0.5,
        tp1_rr=1.4,
        tp2_rr=2.8,
        use_htf_filter=True,
        htf_ema_period=50,
        use_retest_entry=True
    )

    print("\n" + "=" * 75)
    print("  SCALED-TP IFVG & LIQUIDITY SWEEP STRATEGY (TP1 @ 1:1.4 RR, TP2 @ 1:2.8 RR)")
    print("=" * 75)
    print(f"{'Coin':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Profit Factor':<14} | {'Net Return':<11} | {'Max DD':<8}")
    print("-" * 75)

    results = []
    for c in coins:
        sim = CryptoAssetSimulator(c["symbol"], c["base_price"], c["daily_vol"], c["trend"])
        bars = sim.generate_bars(num_bars=4500, seed=c["seed"])
        res = strat.run_backtest(bars, initial_capital=100000.0, risk_per_trade=0.015)
        res["symbol"] = c["symbol"]
        results.append(res)
        print(f"{res['symbol']:<10} | {res['total_trades']:<7} | {res['win_rate']:<8}% | {res['profit_factor']:<14} | {res['return_pct']:<10}% | {res['max_drawdown_pct']:<7}%")

    print("-" * 75)
    tot_trades = sum(r["total_trades"] for r in results)
    avg_wr = sum(r["win_rate"] for r in results) / len(results)
    avg_pf = sum(r["profit_factor"] for r in results) / len(results)
    tot_return = sum(r["return_pct"] for r in results) / len(results)
    avg_max_dd = sum(r["max_drawdown_pct"] for r in results) / len(results)

    print(f"{'PORTFOLIO':<10} | {tot_trades:<7} | {avg_wr:<8.2f}% | {avg_pf:<14.2f} | {tot_return:<10.2f}% | {avg_max_dd:<7.2f}%")
    print("=" * 75)

if __name__ == "__main__":
    run_scaled_tp_simulation()
