import unittest
import numpy as np
import pandas as pd
from crypto_signal_engine import CryptoBreakoutSignalEngine

class TestCryptoBreakoutSignalEngine(unittest.TestCase):
    def setUp(self):
        # Generate synthetic 1H OHLCV dataset with a synthetic bullish breakout
        dates = pd.date_range(start="2026-01-01", periods=100, freq="1h")
        np.random.seed(42)

        base_price = 50000.0
        # Gentle sideways regime for 70 bars
        close_prices = [base_price + np.sin(i / 5.0) * 100 for i in range(70)]
        # Explosive breakout upward from bar 70 to 100
        for i in range(70, 100):
            close_prices.append(close_prices[-1] + (i - 70) * 150 + np.random.uniform(50, 150))

        df = pd.DataFrame({
            "open": close_prices,
            "high": [p + 50 for p in close_prices],
            "low": [p - 50 for p in close_prices],
            "close": close_prices,
            "volume": [1000 + (5000 if i >= 70 else 0) for i in range(100)]
        }, index=dates)

        self.df_synthetic = df

    def test_engine_initialization(self):
        engine = CryptoBreakoutSignalEngine(config={"donchian_period": 15, "atr_period": 10})
        self.assertEqual(engine.donchian_period, 15)
        self.assertEqual(engine.atr_period, 10)

    def test_signal_generation_output_shape(self):
        engine = CryptoBreakoutSignalEngine(config={"use_htf_filter": False})
        df_out = engine.generate_signals(self.df_synthetic)
        
        self.assertIn("signal", df_out.columns)
        self.assertIn("donchian_high", df_out.columns)
        self.assertIn("donchian_low", df_out.columns)
        self.assertIn("atr", df_out.columns)
        self.assertIn("atr_ratio", df_out.columns)
        self.assertIn("rsi", df_out.columns)

    def test_bullish_breakout_signal_triggered(self):
        # Configure engine to trigger without HTF restrictions on synthetic data
        engine = CryptoBreakoutSignalEngine(config={
            "use_htf_filter": False,
            "use_volume_filter": True,
            "use_rsi_filter": False,
            "atr_expansion_threshold": 1.10,
            "vol_expansion_threshold": 1.10
        })
        df_out = engine.generate_signals(self.df_synthetic)
        
        long_signals = df_out[df_out["signal"] == 1]
        self.assertGreater(len(long_signals), 0, "Engine should detect bullish breakout in synthetic data")

    def test_edge_case_empty_dataframe(self):
        df_empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        engine = CryptoBreakoutSignalEngine()
        df_out = engine.generate_signals(df_empty)
        self.assertEqual(len(df_out), 0)

if __name__ == "__main__":
    unittest.main()
