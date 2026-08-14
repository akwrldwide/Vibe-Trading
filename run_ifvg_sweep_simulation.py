"""
Liquidity Sweep + Inverse Fair Value Gap (IFVG) Multi-Coin Backtest Simulator
Tests the strategy across 7 crypto assets (BTC, ETH, SOL, BNB, AVAX, DOGE, LINK)
Using institutional SMC order-flow mechanics and fee/slippage modeling.
"""

import math
import random
import json
from datetime import datetime, timedelta

class CryptoAssetSimulator:
    """Generates realistic crypto price paths with liquidity cascades and microstructure."""
    def __init__(self, symbol: str, base_price: float, daily_vol: float, trend_bias: float = 0.0002):
        self.symbol = symbol
        self.base_price = base_price
        self.vol = daily_vol / math.sqrt(96)  # 15m bar volatility (96 bars/day)
        self.trend = trend_bias

    def generate_bars(self, num_bars: int = 4000, seed: int = 42):
        random.seed(seed)
        price = self.base_price
        bars = []
        dt = datetime(2026, 1, 1, 0, 0)
        
        # State tracking for trending vs mean-reverting regimes
        regime = 1  # 1: bullish drift, -1: bearish drift, 0: range
        regime_duration = 0

        for i in range(num_bars):
            dt += timedelta(minutes=15)
            regime_duration += 1
            if regime_duration > random.randint(150, 400):
                regime = random.choice([1, -1, 0, 0])
                regime_duration = 0

            # Drift + Volatility
            drift = self.trend * regime
            shock = random.gauss(0, self.vol)
            
            # Liquidity cascade shock (fat-tail stop hunt event)
            if random.random() < 0.03:
                cascade_dir = -1 if regime == 1 else (1 if regime == -1 else random.choice([1, -1]))
                shock += cascade_dir * random.uniform(1.8, 3.2) * self.vol

            ret = drift + shock
            open_p = price
            close_p = open_p * math.exp(ret)
            
            # Candle wicks
            intra_vol = self.vol * price * random.uniform(0.6, 1.8)
            high_p = max(open_p, close_p) + abs(random.gauss(0, intra_vol * 0.5))
            low_p = min(open_p, close_p) - abs(random.gauss(0, intra_vol * 0.5))

            # Volume
            base_vol = 1000 * (self.base_price / 100.0)
            vol_spike = abs(ret) / self.vol
            volume = base_vol * (1.0 + vol_spike * random.uniform(0.8, 2.5))

            bars.append({
                "time": dt.strftime("%Y-%m-%d %H:%M"),
                "open": round(open_p, 4),
                "high": round(high_p, 4),
                "low": round(low_p, 4),
                "close": round(close_p, 4),
                "volume": round(volume, 2)
            })
            price = close_p

        return bars


class IFVGLiquiditySweepStrategy:
    def __init__(
        self,
        swing_window: int = 40,
        atr_period: int = 14,
        sl_buffer_atr: float = 0.5,
        tp_rr_ratio: float = 2.2,
        use_htf_filter: bool = True,
        htf_ema_period: int = 50,
        use_breakeven: bool = True,
        be_trigger_rr: float = 1.0,
        use_retest_entry: bool = True,
        taker_fee: float = 0.0005,
        slippage: float = 0.0005
    ):
        self.swing_window = swing_window
        self.atr_period = atr_period
        self.sl_buffer_atr = sl_buffer_atr
        self.tp_rr_ratio = tp_rr_ratio
        self.use_htf_filter = use_htf_filter
        self.htf_ema_period = htf_ema_period
        self.use_breakeven = use_breakeven
        self.be_trigger_rr = be_trigger_rr
        self.use_retest_entry = use_retest_entry
        self.taker_fee = taker_fee
        self.slippage = slippage

    def run_backtest(self, bars, initial_capital: float = 100000.0, risk_per_trade: float = 0.015):
        n = len(bars)
        if n < self.swing_window + 50:
            return None

        # Precompute indicators
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        opens = [b["open"] for b in bars]
        vols = [b["volume"] for b in bars]

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

        # 4H EMA approximation (16 15m bars per 4H)
        ema_period_bars = self.htf_ema_period * 16
        ema_4h = [0.0] * n
        ema_alpha = 2.0 / (ema_period_bars + 1)
        ema_4h[0] = closes[0]
        for i in range(1, n):
            ema_4h[i] = ema_alpha * closes[i] + (1 - ema_alpha) * ema_4h[i-1]

        # Volume SMA
        vol_sma = [0.0] * n
        for i in range(19, n):
            vol_sma[i] = sum(vols[i-19:i+1]) / 20.0

        # Fair Value Gaps (Bullish and Bearish)
        # Bearish FVG at i: highs[i] < lows[i-2] (Top: lows[i-2], Bot: highs[i])
        # Bullish FVG at i: lows[i] > highs[i-2] (Top: lows[i], Bot: highs[i-2])
        bear_fvgs = [None] * n
        bull_fvgs = [None] * n

        for i in range(2, n):
            if highs[i] < lows[i-2]:
                bear_fvgs[i] = {"top": lows[i-2], "bot": highs[i], "bar": i}
            if lows[i] > highs[i-2]:
                bull_fvgs[i] = {"top": lows[i], "bot": highs[i-2], "bar": i}

        # Strategy Simulation Loop
        capital = initial_capital
        equity_curve = [capital]
        trades = []
        active_trade = None

        # Sweep tracking state
        recent_bull_sweep = None  # swept low
        recent_bear_sweep = None  # swept high

        for i in range(self.swing_window + 5, n):
            cur_high = highs[i]
            cur_low = lows[i]
            cur_close = closes[i]
            cur_open = opens[i]
            cur_atr = max(atr[i], 1e-5)
            cur_time = bars[i]["time"]

            # Calculate Rolling Swing High & Low
            swing_h = max(highs[i - self.swing_window:i])
            swing_l = min(lows[i - self.swing_window:i])

            # Check for Liquidity Sweeps
            # Bullish Sweep: price wicks below swing_low but closes back inside or shows rejection
            if cur_low < swing_l and cur_close >= swing_l:
                recent_bull_sweep = {
                    "bar": i,
                    "sweep_low": cur_low,
                    "swing_level": swing_l,
                    "atr": cur_atr
                }

            # Bearish Sweep: price wicks above swing_high but closes back inside
            if cur_high > swing_h and cur_close <= swing_h:
                recent_bear_sweep = {
                    "bar": i,
                    "sweep_high": cur_high,
                    "swing_level": swing_h,
                    "atr": cur_atr
                }

            # Invalidate stale sweeps (> 15 bars old)
            if recent_bull_sweep and (i - recent_bull_sweep["bar"] > 15):
                recent_bull_sweep = None
            if recent_bear_sweep and (i - recent_bear_sweep["bar"] > 15):
                recent_bear_sweep = None

            # Manage Active Trade
            if active_trade is not None:
                side = active_trade["side"]
                entry = active_trade["entry_price"]
                sl = active_trade["stop_loss"]
                tp = active_trade["take_profit"]
                pos_size = active_trade["size"]
                init_risk = active_trade["initial_risk"]

                if side == "LONG":
                    # Breakeven adjustment
                    if self.use_breakeven and not active_trade.get("be_moved", False):
                        if cur_high >= entry + (init_risk * self.be_trigger_rr):
                            sl = entry + (0.1 * cur_atr)
                            active_trade["stop_loss"] = sl
                            active_trade["be_moved"] = True

                    # Exit checks
                    hit_sl = cur_low <= sl
                    hit_tp = cur_high >= tp

                    if hit_sl or hit_tp:
                        if hit_sl and hit_tp:
                            exit_p = sl
                            outcome = "SL"
                        elif hit_tp:
                            exit_p = tp
                            outcome = "TP"
                        else:
                            exit_p = sl
                            outcome = "BE" if active_trade.get("be_moved", False) else "SL"

                        # Apply exit slippage
                        exit_p = exit_p * (1.0 - self.slippage)
                        pnl = (exit_p - entry) * pos_size
                        fee = (entry * pos_size * self.taker_fee) + (exit_p * pos_size * self.taker_fee)
                        net_pnl = pnl - fee
                        capital += net_pnl

                        trades.append({
                            "side": "LONG",
                            "entry_time": active_trade["time"],
                            "exit_time": cur_time,
                            "entry_price": entry,
                            "exit_price": exit_p,
                            "sl": sl,
                            "tp": tp,
                            "net_pnl": net_pnl,
                            "ret_pct": (net_pnl / (entry * pos_size)) * 100,
                            "outcome": outcome,
                            "capital": capital
                        })
                        active_trade = None

                elif side == "SHORT":
                    # Breakeven adjustment
                    if self.use_breakeven and not active_trade.get("be_moved", False):
                        if cur_low <= entry - (init_risk * self.be_trigger_rr):
                            sl = entry - (0.1 * cur_atr)
                            active_trade["stop_loss"] = sl
                            active_trade["be_moved"] = True

                    # Exit checks
                    hit_sl = cur_high >= sl
                    hit_tp = cur_low <= tp

                    if hit_sl or hit_tp:
                        if hit_sl and hit_tp:
                            exit_p = sl
                            outcome = "SL"
                        elif hit_tp:
                            exit_p = tp
                            outcome = "TP"
                        else:
                            exit_p = sl
                            outcome = "BE" if active_trade.get("be_moved", False) else "SL"

                        exit_p = exit_p * (1.0 + self.slippage)
                        pnl = (entry - exit_p) * pos_size
                        fee = (entry * pos_size * self.taker_fee) + (exit_p * pos_size * self.taker_fee)
                        net_pnl = pnl - fee
                        capital += net_pnl

                        trades.append({
                            "side": "SHORT",
                            "entry_time": active_trade["time"],
                            "exit_time": cur_time,
                            "entry_price": entry,
                            "exit_price": exit_p,
                            "sl": sl,
                            "tp": tp,
                            "net_pnl": net_pnl,
                            "ret_pct": (net_pnl / (entry * pos_size)) * 100,
                            "outcome": outcome,
                            "capital": capital
                        })
                        active_trade = None

            # Look for New Signals if No Active Trade
            if active_trade is None:
                # 1. Long IFVG Setup:
                # Price had a bullish sweep, formed a bearish FVG during the down-push, and now breaks/closes above it.
                if recent_bull_sweep is not None:
                    # Find any bearish FVG formed around the sweep (bars between sweep-5 and current bar)
                    target_fvg = None
                    for b_idx in range(max(2, recent_bull_sweep["bar"] - 5), i):
                        if bear_fvgs[b_idx] is not None:
                            target_fvg = bear_fvgs[b_idx]
                            break

                    if target_fvg is not None:
                        # Check IFVG trigger: current candle closes ABOVE the Bearish FVG Top
                        if cur_close > target_fvg["top"] and closes[i-1] <= target_fvg["top"]:
                            # HTF Trend Filter Check
                            htf_ok = not self.use_htf_filter or (cur_close >= ema_4h[i])
                            
                            if htf_ok:
                                entry_p = cur_close * (1.0 + self.slippage)
                                if self.use_retest_entry:
                                    # Blend entry toward IFVG midpoint
                                    fvg_mid = (target_fvg["top"] + target_fvg["bot"]) / 2.0
                                    entry_p = (entry_p * 0.5) + (fvg_mid * 0.5)

                                sl_p = recent_bull_sweep["sweep_low"] - (self.sl_buffer_atr * cur_atr)
                                risk_dist = max(entry_p - sl_p, cur_atr * 0.5)
                                tp_p = entry_p + (risk_dist * self.tp_rr_ratio)

                                # Capital Risk Sizing
                                risk_amount = capital * risk_per_trade
                                pos_units = risk_amount / risk_dist

                                active_trade = {
                                    "side": "LONG",
                                    "time": cur_time,
                                    "entry_price": entry_p,
                                    "stop_loss": sl_p,
                                    "take_profit": tp_p,
                                    "size": pos_units,
                                    "initial_risk": risk_dist,
                                    "be_moved": False
                                }
                                recent_bull_sweep = None

                # 2. Short IFVG Setup:
                # Price had a bearish sweep, formed a bullish FVG during the up-push, and now breaks/closes below it.
                if recent_bear_sweep is not None and active_trade is None:
                    target_fvg = None
                    for b_idx in range(max(2, recent_bear_sweep["bar"] - 5), i):
                        if bull_fvgs[b_idx] is not None:
                            target_fvg = bull_fvgs[b_idx]
                            break

                    if target_fvg is not None:
                        # Check IFVG trigger: current candle closes BELOW the Bullish FVG Bottom
                        if cur_close < target_fvg["bot"] and closes[i-1] >= target_fvg["bot"]:
                            htf_ok = not self.use_htf_filter or (cur_close <= ema_4h[i])

                            if htf_ok:
                                entry_p = cur_close * (1.0 - self.slippage)
                                if self.use_retest_entry:
                                    fvg_mid = (target_fvg["top"] + target_fvg["bot"]) / 2.0
                                    entry_p = (entry_p * 0.5) + (fvg_mid * 0.5)

                                sl_p = recent_bear_sweep["sweep_high"] + (self.sl_buffer_atr * cur_atr)
                                risk_dist = max(sl_p - entry_p, cur_atr * 0.5)
                                tp_p = entry_p - (risk_dist * self.tp_rr_ratio)

                                risk_amount = capital * risk_per_trade
                                pos_units = risk_amount / risk_dist

                                active_trade = {
                                    "side": "SHORT",
                                    "time": cur_time,
                                    "entry_price": entry_p,
                                    "stop_loss": sl_p,
                                    "take_profit": tp_p,
                                    "size": pos_units,
                                    "initial_risk": risk_dist,
                                    "be_moved": False
                                }
                                recent_bear_sweep = None

            equity_curve.append(capital)

        # Performance Metrics Calculation
        total_trades = len(trades)
        if total_trades == 0:
            return {"symbol": "N/A", "total_trades": 0, "win_rate": 0, "net_pnl": 0}

        wins = [t for t in trades if t["outcome"] == "TP"]
        losses = [t for t in trades if t["outcome"] == "SL"]
        bes = [t for t in trades if t["outcome"] == "BE"]

        win_rate = (len(wins) / total_trades) * 100.0
        gross_profit = sum(t["net_pnl"] for t in wins)
        gross_loss = abs(sum(t["net_pnl"] for t in losses)) if losses else 1e-5
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Max Drawdown
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
            "breakevens": len(bes),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "avg_trade_pnl": round((capital - initial_capital) / total_trades, 2),
            "trades": trades
        }


def run_cross_coin_simulation():
    coins = [
        {"symbol": "BTC-USDT", "base_price": 94500.0, "daily_vol": 0.038, "trend": 0.0003, "seed": 101},
        {"symbol": "ETH-USDT", "base_price": 3400.0, "daily_vol": 0.052, "trend": 0.0002, "seed": 202},
        {"symbol": "SOL-USDT", "base_price": 195.0, "daily_vol": 0.075, "trend": 0.0004, "seed": 303},
        {"symbol": "BNB-USDT", "base_price": 680.0, "daily_vol": 0.035, "trend": 0.0001, "seed": 404},
        {"symbol": "AVAX-USDT", "base_price": 32.0, "daily_vol": 0.082, "trend": 0.0002, "seed": 505},
        {"symbol": "DOGE-USDT", "base_price": 0.22, "daily_vol": 0.095, "trend": 0.0001, "seed": 606},
        {"symbol": "LINK-USDT", "base_price": 18.5, "daily_vol": 0.065, "trend": 0.0003, "seed": 707}
    ]

    strategy = IFVGLiquiditySweepStrategy(
        swing_window=40,
        atr_period=14,
        sl_buffer_atr=0.6,
        tp_rr_ratio=2.2,
        use_htf_filter=True,
        htf_ema_period=50,
        use_breakeven=True,
        be_trigger_rr=1.0,
        use_retest_entry=True
    )

    results = []
    print("=" * 70)
    print("  LIQUIDITY SWEEP + INVERSE FAIR VALUE GAP (IFVG) MULTI-COIN STUDY")
    print("=" * 70)
    print(f"{'Coin':<10} | {'Trades':<7} | {'Win Rate':<9} | {'Profit Factor':<14} | {'Net Return':<11} | {'Max DD':<8}")
    print("-" * 70)

    for c in coins:
        sim = CryptoAssetSimulator(c["symbol"], c["base_price"], c["daily_vol"], c["trend"])
        bars = sim.generate_bars(num_bars=4500, seed=c["seed"])
        res = strategy.run_backtest(bars, initial_capital=100000.0, risk_per_trade=0.015)
        res["symbol"] = c["symbol"]
        results.append(res)

        print(f"{res['symbol']:<10} | {res['total_trades']:<7} | {res['win_rate']:<8}% | {res['profit_factor']:<14} | {res['return_pct']:<10}% | {res['max_drawdown_pct']:<7}%")

    print("-" * 70)
    
    # Portfolio aggregates
    tot_trades = sum(r["total_trades"] for r in results)
    avg_wr = sum(r["win_rate"] for r in results) / len(results)
    avg_pf = sum(r["profit_factor"] for r in results) / len(results)
    tot_return = sum(r["return_pct"] for r in results) / len(results)
    avg_max_dd = sum(r["max_drawdown_pct"] for r in results) / len(results)

    print(f"{'PORTFOLIO':<10} | {tot_trades:<7} | {avg_wr:<8.2f}% | {avg_pf:<14.2f} | {tot_return:<10.2f}% | {avg_max_dd:<7.2f}%")
    print("=" * 70)

    # Save detailed JSON summary
    with open("ifvg_backtest_results.json", "w") as f:
        json.dump({
            "summary": {
                "total_trades": tot_trades,
                "avg_win_rate": round(avg_wr, 2),
                "avg_profit_factor": round(avg_pf, 2),
                "avg_return_pct": round(tot_return, 2),
                "avg_max_drawdown_pct": round(avg_max_dd, 2)
            },
            "per_coin": [{k: v for k, v in r.items() if k != "trades"} for r in results]
        }, f, indent=2)

if __name__ == "__main__":
    run_cross_coin_simulation()
