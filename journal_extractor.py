import os
import re
import json
import base64
import logging
from typing import Dict, Any, Optional
from PIL import Image
import io

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "screenshots")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def save_uploaded_image(file_bytes: bytes, filename: str) -> str:
    """Saves uploaded chart screenshot and returns the web-accessible URL path."""
    clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
    timestamp_prefix = os.urandom(4).hex()
    saved_filename = f"{timestamp_prefix}_{clean_name}"
    full_path = os.path.join(UPLOAD_DIR, saved_filename)
    
    with open(full_path, "wb") as f:
        f.write(file_bytes)
        
    return f"/uploads/screenshots/{saved_filename}"

def extract_trade_info_from_image(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Extracts 9:30 NY ICT setup details from a TradingView screenshot.
    Uses AI vision (Gemini / OpenAI) if API keys are available, with local heuristic fallback.
    """
    screenshot_url = save_uploaded_image(file_bytes, filename)
    
    extracted_data: Dict[str, Any] = {
        "symbol": "BTCUSDT",
        "action": "SELL",
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "rr_ratio": 2.0,
        "htf_bias": "BEARISH",
        "setup_type": "OR Breakout (Candle 2 FVG)",
        "outcome": "OPEN",
        "trade_date": None,
        "ny_time": "09:45 AM",
        "notes": "",
        "screenshot_url": screenshot_url,
        "confidence": "heuristic"
    }

    # Attempt AI Vision extraction if Gemini / OpenAI API key is present
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    if gemini_key:
        try:
            ai_data = _extract_with_gemini(file_bytes, gemini_key)
            if ai_data:
                extracted_data.update(ai_data)
                extracted_data["screenshot_url"] = screenshot_url
                extracted_data["confidence"] = "ai_high"
                return extracted_data
        except Exception as e:
            logger.warning(f"Gemini vision extraction fallback: {e}")

    if openai_key and extracted_data["confidence"] != "ai_high":
        try:
            ai_data = _extract_with_openai(file_bytes, openai_key)
            if ai_data:
                extracted_data.update(ai_data)
                extracted_data["screenshot_url"] = screenshot_url
                extracted_data["confidence"] = "ai_high"
                return extracted_data
        except Exception as e:
            logger.warning(f"OpenAI vision extraction fallback: {e}")

    # Fallback to local image metadata / standard TradingView defaults
    try:
        img = Image.open(io.BytesIO(file_bytes))
        width, height = img.size
        extracted_data["notes"] = f"Uploaded chart screenshot ({width}x{height}px). Values pre-filled for review."
    except Exception:
        pass

    return extracted_data


def _extract_with_gemini(file_bytes: bytes, api_key: str) -> Optional[Dict[str, Any]]:
    import urllib.request
    import json

    b64_img = base64.b64encode(file_bytes).decode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    prompt = """
    Analyze this TradingView chart screenshot for a 9:30 NY ICT Trading Setup.
    Extract the following details in strict JSON format:
    {
      "symbol": "Ticker symbol (e.g. BTCUSDT, EURUSD, NQ1!)",
      "action": "BUY or SELL",
      "entry_price": 0.0,
      "stop_loss": 0.0,
      "take_profit": 0.0,
      "rr_ratio": 2.0,
      "htf_bias": "BULLISH or BEARISH",
      "setup_type": "OR Breakout (Candle 2 FVG) or OR Retest / Continuation FVG or Liquidity Sweep / IFVG Reversal",
      "outcome": "WIN, LOSS, BREAK-EVEN, or OPEN",
      "trade_date": "YYYY-MM-DD",
      "ny_time": "Time string (e.g. 09:55 AM)",
      "notes": "Short concise 1-sentence analysis of the trade execution shown on chart"
    }
    Look at the top-right status table and chart badges (ENTRY, SL, TP, Risk:Reward, HTF Bias).
    Return ONLY pure JSON. No markdown code blocks.
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": b64_img
                    }
                }
            ]
        }],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        res = json.loads(response.read().decode("utf-8"))
        raw_text = res["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw_text)


def _extract_with_openai(file_bytes: bytes, api_key: str) -> Optional[Dict[str, Any]]:
    import urllib.request
    import json

    b64_img = base64.b64encode(file_bytes).decode("utf-8")
    url = "https://api.openai.com/v1/chat/completions"
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract TradingView 9:30 NY ICT setup details in JSON format: {symbol, action, entry_price, stop_loss, take_profit, rr_ratio, htf_bias, setup_type, outcome, trade_date, ny_time, notes}. Output ONLY raw JSON."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_img}"
                        }
                    }
                ]
            }
        ],
        "response_format": {"type": "json_object"}
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        res = json.loads(response.read().decode("utf-8"))
        raw_text = res["choices"][0]["message"]["content"]
        return json.loads(raw_text)
