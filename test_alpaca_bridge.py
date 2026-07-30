import json
from fastapi.testclient import TestClient
from alpaca_bridge import app

client = TestClient(app)

def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "online"

def test_webhook_unauthorized():
    payload = {
        "passcode": "WRONG_PASSCODE",
        "symbol": "EURUSD",
        "action": "BUY",
        "limit_price": 1.13880,
        "stop_loss": 1.13855,
        "take_profit": 1.13940
    }
    response = client.post("/webhook", json=payload)
    assert response.status_code == 401

def test_webhook_invalid_params():
    payload = {
        "passcode": "MY_SECRET_PASSCODE",
        "symbol": "EURUSD",
        "action": "INVALID_ACTION",
        "limit_price": 0,
        "stop_loss": 1.13855,
        "take_profit": 1.13940
    }
    response = client.post("/webhook", json=payload)
    assert response.status_code == 400

if __name__ == "__main__":
    test_root()
    test_webhook_unauthorized()
    test_webhook_invalid_params()
    print("All Webhook Bridge Unit Tests Passed Successfully!")
