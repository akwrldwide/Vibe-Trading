# Root ASGI Entry Point for Render.com Webhook Bridge
import os
from dotenv import load_dotenv

load_dotenv()

bridge_type = os.getenv("BRIDGE_TYPE", "fxopen").lower()

if bridge_type == "alpaca":
    from alpaca_bridge import app
elif bridge_type in ["crypto", "mt5"]:
    from crypto_webhook_bridge import app
else:
    from fxopen_bridge import app

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)

