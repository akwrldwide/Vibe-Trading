import pandas as pd
import numpy as np
from fvg_pullback_engine import FVGPullbackSignalEngine

def create_mock_trend_ohlcv(length=50):
    dates = pd.date_range("2026-01-01", periods=length, freq="1h")
    np.random.seed(42)
    base_price = 50000.0
    
    # Generate an upward trend
    closes = base_price + np.linspace(0, 3000, length) + np.random.normal(0, 50, length)
    highs = closes + np.abs(np.random.normal(60, 20, length))
    lows = closes - np.abs(np.random.normal(60, 20, length))
    opens = closes - np.random.normal(10, 20, length)
    volumes = np.random.uniform(100, 1000, length)
    
    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes
    }, index=dates)
    return df

def test_fvg_engine_initialization():
    engine = FVGPullbackSignalEngine(config={"fast_ema": 15, "tp_rr_ratio": 3.0})
    assert engine.fast_ema == 15
    assert engine.tp_rr_ratio == 3.0
    print("[OK] test_fvg_engine_initialization PASSED")

def test_bullish_fvg_signal():
    df = create_mock_trend_ohlcv(50)
    
    # At bar 25, create a pullback to 21 EMA with a green candle
    df.iloc[25, df.columns.get_loc("low")] = df.iloc[25]["close"] - 150  # Touches EMA
    df.iloc[25, df.columns.get_loc("close")] = df.iloc[25]["open"] + 200 # Strong green close
    
    engine = FVGPullbackSignalEngine(config={"fast_ema": 21, "slow_ema": 50})
    df_sig = engine.generate_signals(df)
    
    assert "signal" in df_sig.columns
    assert "entry_price" in df_sig.columns
    assert "stop_loss" in df_sig.columns
    assert "take_profit" in df_sig.columns
    print("[OK] test_bullish_fvg_signal PASSED")

if __name__ == "__main__":
    test_fvg_engine_initialization()
    test_bullish_fvg_signal()
    print("\n[SUCCESS] ALL FVG PULLBACK UNIT TESTS PASSED SUCCESSFULLY!")
