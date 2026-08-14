import pandas as pd
import numpy as np
import datetime
from topdown_signal_engine import TopDownLiquiditySignalEngine

def test_topdown_signal_engine_initialization():
    engine = TopDownLiquiditySignalEngine(config={
        "htf_ema_period": 50,
        "swing_lookback": 32,
        "tp1_rr": 1.5,
        "tp2_rr": 3.0
    })
    assert engine.htf_ema_period == 50
    assert engine.swing_lookback == 32
    assert engine.tp1_rr == 1.5
    assert engine.tp2_rr == 3.0

def test_topdown_signal_engine_synthetic_data():
    engine = TopDownLiquiditySignalEngine()
    
    # Generate synthetic 15m OHLCV data
    dates = pd.date_range(start="2026-01-01", periods=300, freq="15min", tz="UTC")
    np.random.seed(42)
    base = 100.0 + np.cumsum(np.random.randn(300) * 0.5)
    highs = base + np.random.uniform(0.1, 1.0, 300)
    lows = base - np.random.uniform(0.1, 1.0, 300)
    opens = base + np.random.uniform(-0.2, 0.2, 300)
    closes = base + np.random.uniform(-0.2, 0.2, 300)
    volumes = np.random.uniform(1000, 5000, 300)
    
    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes
    }, index=dates)
    
    res = engine.generate_signals(df)
    assert "signal" in res.columns
    assert "entry_price" in res.columns
    assert "stop_loss" in res.columns
    assert "tp1" in res.columns
    assert "tp2" in res.columns
    assert len(res) == 300
    print("Test passed successfully!")

if __name__ == "__main__":
    test_topdown_signal_engine_initialization()
    test_topdown_signal_engine_synthetic_data()
