import json
from fastapi.testclient import TestClient
from fxopen_bridge import app

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
        "limit_price": 1.15232,
        "stop_loss": 1.15193,
        "take_profit": 1.15310
    }
    response = client.post("/webhook", json=payload)
    assert response.status_code == 401

def test_webhook_valid_payload():
    payload = {
        "passcode": "MY_SECRET_PASSCODE",
        "symbol": "EURUSD",
        "action": "BUY",
        "limit_price": 1.15232,
        "stop_loss": 1.15193,
        "take_profit": 1.15310
    }
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

if __name__ == "__main__":
    test_root()
    test_webhook_unauthorized()
    test_webhook_valid_payload()
    print("All FXOpen Webhook Bridge Unit Tests Passed Successfully!")
