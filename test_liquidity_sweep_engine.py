import pandas as pd
import numpy as np
from liquidity_sweep_engine import LiquiditySweepSignalEngine

def create_mock_ohlcv(length=50):
    dates = pd.date_range("2026-01-01", periods=length, freq="15min")
    np.random.seed(42)
    base_price = 50000.0
    
    closes = base_price + np.cumsum(np.random.normal(0, 100, length))
    highs = closes + np.abs(np.random.normal(50, 30, length))
    lows = closes - np.abs(np.random.normal(50, 30, length))
    opens = closes + np.random.normal(0, 20, length)
    volumes = np.random.uniform(100, 1000, length)
    
    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes
    }, index=dates)
    return df

def test_engine_initialization():
    engine = LiquiditySweepSignalEngine(config={"swing_window": 15, "tp_rr_ratio": 3.0})
    assert engine.swing_window == 15
    assert engine.tp_rr_ratio == 3.0
    print("[OK] test_engine_initialization PASSED")

def test_bullish_liquidity_sweep_signal():
    df = create_mock_ohlcv(50)
    
    # Establish a clear swing low prior to bar 30
    df.iloc[10:29, df.columns.get_loc("low")] = 49000.0
    df.iloc[10:29, df.columns.get_loc("close")] = 49500.0
    df.iloc[10:29, df.columns.get_loc("high")] = 50000.0
    df.iloc[10:29, df.columns.get_loc("open")] = 49500.0
    
    # Bar 30: Wicks down to 48500 (below swing low 49000), but closes back inside at 49600
    df.iloc[30, df.columns.get_loc("low")] = 48500.0
    df.iloc[30, df.columns.get_loc("close")] = 49600.0
    df.iloc[30, df.columns.get_loc("open")] = 49100.0
    df.iloc[30, df.columns.get_loc("high")] = 49700.0
    df.iloc[30, df.columns.get_loc("volume")] = 5000.0
    
    engine = LiquiditySweepSignalEngine(config={"swing_window": 15, "use_rsi_filter": False})
    df_sig = engine.generate_signals(df)
    
    assert df_sig["signal"].iloc[30] == 1
    assert df_sig["entry_price"].iloc[30] == 49600.0
    assert df_sig["stop_loss"].iloc[30] < 48500.0
    assert df_sig["take_profit"].iloc[30] > 49600.0
    print("[OK] test_bullish_liquidity_sweep_signal PASSED")

def test_bearish_liquidity_sweep_signal():
    df = create_mock_ohlcv(50)
    
    # Establish a clear swing high prior to bar 30
    df.iloc[10:29, df.columns.get_loc("high")] = 51000.0
    df.iloc[10:29, df.columns.get_loc("close")] = 50500.0
    df.iloc[10:29, df.columns.get_loc("low")] = 50000.0
    df.iloc[10:29, df.columns.get_loc("open")] = 50500.0
    
    # Bar 30: Wicks up to 51500 (above swing high 51000), but closes back inside at 50400
    df.iloc[30, df.columns.get_loc("high")] = 51500.0
    df.iloc[30, df.columns.get_loc("close")] = 50400.0
    df.iloc[30, df.columns.get_loc("open")] = 50900.0
    df.iloc[30, df.columns.get_loc("low")] = 50300.0
    df.iloc[30, df.columns.get_loc("volume")] = 5000.0
    
    engine = LiquiditySweepSignalEngine(config={"swing_window": 15, "use_rsi_filter": False, "use_htf_filter": False})

    df_sig = engine.generate_signals(df)
    
    assert df_sig["signal"].iloc[30] == -1
    assert df_sig["entry_price"].iloc[30] == 50400.0
    assert df_sig["stop_loss"].iloc[30] > 51500.0
    assert df_sig["take_profit"].iloc[30] < 50400.0
    print("[OK] test_bearish_liquidity_sweep_signal PASSED")

if __name__ == "__main__":
    test_engine_initialization()
    test_bullish_liquidity_sweep_signal()
    test_bearish_liquidity_sweep_signal()
    print("\n[SUCCESS] ALL UNIT TESTS PASSED SUCCESSFULLY!")
