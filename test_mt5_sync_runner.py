import mt5_sync_runner

def test_imports():
    assert mt5_sync_runner.MT5_AVAILABLE is True
    print("MT5 Sync Runner module imported cleanly!")

if __name__ == "__main__":
    test_imports()
