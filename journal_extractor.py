import os
import re
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from PIL import Image, ImageEnhance, ImageOps
import io

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "screenshots")
os.makedirs(UPLOAD_DIR, exist_ok=True)

_reader = None

def get_ocr_reader():
    global _reader
    if _reader is None:
        try:
            import easyocr
            _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        except Exception as e:
            logger.warning(f"Could not load EasyOCR: {e}")
            _reader = None
    return _reader


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
    Uses multi-pass Local EasyOCR with enhanced table crop & mathematical price level resolution.
    """
    screenshot_url = save_uploaded_image(file_bytes, filename)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    extracted_data: Dict[str, Any] = {
        "symbol": "BTCUSDT",
        "action": "SELL",
        "entry_price": None,
        "stop_loss": None,
        "take_profit": None,
        "rr_ratio": 2.0,
        "htf_bias": "BEARISH",
        "setup_type": "OR Breakout (Candle 2 FVG)",
        "outcome": "WIN",
        "trade_date": today_str,
        "ny_time": "09:55 AM",
        "notes": "",
        "screenshot_url": screenshot_url,
        "confidence": "ocr_local"
    }

    # 1. Multi-pass Local EasyOCR
    reader = get_ocr_reader()
    if reader:
        try:
            # Pass A: Full image OCR
            ocr_results_full = reader.readtext(file_bytes)
            
            # Pass B: Top-right quadrant crop with contrast enhancement
            ocr_results_crop = []
            try:
                img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
                w, h = img.size
                tr_crop = img.crop((int(w * 0.55), 0, w, int(h * 0.45)))
                tr_crop = tr_crop.resize((int(tr_crop.width * 2.5), int(tr_crop.height * 2.5)), Image.Resampling.LANCZOS)
                tr_crop = ImageEnhance.Contrast(tr_crop).enhance(2.0)
                
                buf = io.BytesIO()
                tr_crop.save(buf, format="PNG")
                ocr_results_crop = reader.readtext(buf.getvalue())
            except Exception as ce:
                logger.warning(f"Crop OCR enhancement error: {ce}")

            combined_results = ocr_results_full + ocr_results_crop
            parsed = _parse_all_ocr_results(combined_results, ocr_results_full)
            
            for k, v in parsed.items():
                if v is not None:
                    extracted_data[k] = v
            extracted_data["confidence"] = "ocr_high"

            if extracted_data.get("entry_price") and extracted_data.get("stop_loss"):
                return extracted_data
        except Exception as e:
            logger.warning(f"EasyOCR extraction error: {e}")

    # 2. Cloud AI Vision Fallbacks if keys available
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    if gemini_key:
        try:
            ai_data = _extract_with_gemini(file_bytes, gemini_key)
            if ai_data:
                extracted_data.update({k: v for k, v in ai_data.items() if v is not None})
                extracted_data["screenshot_url"] = screenshot_url
                extracted_data["confidence"] = "ai_high"
                return extracted_data
        except Exception as e:
            logger.warning(f"Gemini vision extraction fallback: {e}")

    if openai_key:
        try:
            ai_data = _extract_with_openai(file_bytes, openai_key)
            if ai_data:
                extracted_data.update({k: v for k, v in ai_data.items() if v is not None})
                extracted_data["screenshot_url"] = screenshot_url
                extracted_data["confidence"] = "ai_high"
                return extracted_data
        except Exception as e:
            logger.warning(f"OpenAI vision extraction fallback: {e}")

    return extracted_data


def _parse_all_ocr_results(combined_results: List, full_results: List) -> Dict[str, Any]:
    parsed: Dict[str, Any] = {}
    lines = [text.strip() for _, text, _ in combined_results if text and text.strip()]
    full_text = " \n ".join(lines)

    # 1. Symbol Detection
    sym_match = re.search(r"\b(BTCUSDT|ETHUSDT|SOLUSDT|EURUSD|GBPUSD|AUDUSD|USDCAD|USDJPY|NQ1!|ES1!|NAS100|SPX500|US30)\b", full_text, re.IGNORECASE)
    if sym_match:
        parsed["symbol"] = sym_match.group(1).upper()
    elif lines and ("Perpetual" in lines[0] or "Contract" in lines[0] or "/" in lines[0]):
        first_word = lines[0].split()[0].replace("/", "").upper()
        if len(first_word) >= 3:
            parsed["symbol"] = first_word

    # 2. Action & Outcome Detection
    if re.search(r"SELL\s*\(\s*DONE\s*\)", full_text, re.IGNORECASE):
        parsed["action"] = "SELL"
        parsed["outcome"] = "WIN"
    elif re.search(r"BUY\s*\(\s*DONE\s*\)", full_text, re.IGNORECASE):
        parsed["action"] = "BUY"
        parsed["outcome"] = "WIN"
    elif "SELL" in full_text:
        parsed["action"] = "SELL"
    elif "BUY" in full_text:
        parsed["action"] = "BUY"
    else:
        parsed["action"] = "SELL"

    action = parsed.get("action", "SELL")

    # 3. HTF Bias Detection
    bias_match = re.search(r"HTF Bias[^\n]*\n\s*(BEARISH|BULLISH)", full_text, re.IGNORECASE)
    if not bias_match:
        bias_match = re.search(r"\b(BEARISH|BULLISH)\b", full_text, re.IGNORECASE)
    if bias_match:
        parsed["htf_bias"] = bias_match.group(1).upper()

    # 4. Extract Price Numbers
    detected_prices: List[float] = []
    
    # Check explicit labeled lines first
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(k in line_lower for k in ["entry", "stop", "loss", "take", "profit", "sl", "tp", "target"]):
            val = _extract_number_from_str(line)
            if not val and i + 1 < len(lines):
                val = _extract_number_from_str(lines[i + 1])
            if val and 10.0 <= val <= 200000.0:
                detected_prices.append(val)
                if "entry" in line_lower:
                    parsed["entry_price"] = val
                elif "stop" in line_lower or "loss" in line_lower or line_lower.startswith("sl"):
                    parsed["stop_loss"] = val
                elif "take" in line_lower or "profit" in line_lower or line_lower.startswith("tp"):
                    parsed["take_profit"] = val

    # Extract all candidate price-formatted numbers
    for line in lines:
        for match in re.finditer(r"\b([1-9][0-9]{2,5}(?:\.[0-9]{1,4})?)\b", line):
            val = float(match.group(1))
            # Filter out round axis grid numbers (e.g. 64000.0, 63000.0) if they end with 00.0
            is_axis_round = (val % 100 == 0 or val % 50 == 0) and val > 1000
            if not is_axis_round and 100.0 <= val <= 150000.0:
                detected_prices.append(val)

    # 5. Resolve Entry, SL, TP using mathematical constraints if missing
    if (not parsed.get("entry_price") or not parsed.get("stop_loss") or not parsed.get("take_profit")) and len(detected_prices) >= 2:
        unique_prices = sorted(list(set(detected_prices)))
        if len(unique_prices) >= 3:
            if action == "SELL":
                # For SELL: Take Profit (Lowest) < Entry (Middle) < Stop Loss (Highest)
                parsed["take_profit"] = unique_prices[0]
                parsed["entry_price"] = unique_prices[1]
                parsed["stop_loss"] = unique_prices[2]
            else:
                # For BUY: Stop Loss (Lowest) < Entry (Middle) < Take Profit (Highest)
                parsed["stop_loss"] = unique_prices[0]
                parsed["entry_price"] = unique_prices[1]
                parsed["take_profit"] = unique_prices[2]
        elif len(unique_prices) == 2:
            # Two prices found: calculate the third from standard 1:2 R:R
            if action == "SELL":
                # Assuming entry and SL
                entry = unique_prices[0]
                sl = unique_prices[1]
                parsed["entry_price"] = entry
                parsed["stop_loss"] = sl
                parsed["take_profit"] = round(entry - (sl - entry) * 2.0, 2)
            else:
                sl = unique_prices[0]
                entry = unique_prices[1]
                parsed["stop_loss"] = sl
                parsed["entry_price"] = entry
                parsed["take_profit"] = round(entry + (entry - sl) * 2.0, 2)

    # Auto-calculate Risk:Reward
    if parsed.get("entry_price") and parsed.get("stop_loss") and parsed.get("take_profit"):
        risk = abs(parsed["entry_price"] - parsed["stop_loss"])
        reward = abs(parsed["take_profit"] - parsed["entry_price"])
        if risk > 0:
            parsed["rr_ratio"] = round(reward / risk, 1)

    # 6. Extract Session Date & Time
    date_match = re.search(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)?\s*([0-9]{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?:\s*'?([0-9]{2,4}))?", full_text, re.IGNORECASE)
    if date_match:
        try:
            day = int(date_match.group(2))
            month_str = date_match.group(3).capitalize()
            raw_yr = date_match.group(4)
            year = int(raw_yr) if raw_yr else datetime.now().year
            if year < 100: year += 2000
            months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
            month = months.get(month_str, datetime.now().month)
            parsed["trade_date"] = f"{year:04d}-{month:02d}-{day:02d}"
        except Exception:
            pass

    time_match = re.search(r"\b([0-1]?[0-9]|2[0-3]):([0-5][0-9])\b", full_text)
    if time_match:
        parsed["ny_time"] = f"{time_match.group(1)}:{time_match.group(2)}"

    # 7. Summary Note
    ep_str = f"{parsed['entry_price']:.2f}" if parsed.get('entry_price') else "N/A"
    sl_str = f"{parsed['stop_loss']:.2f}" if parsed.get('stop_loss') else "N/A"
    tp_str = f"{parsed['take_profit']:.2f}" if parsed.get('take_profit') else "N/A"
    parsed["notes"] = f"Auto-extracted {parsed.get('action', 'TRADE')} on {parsed.get('symbol', 'BTCUSDT')} (Entry: {ep_str}, SL: {sl_str}, TP: {tp_str})."

    return parsed


def _extract_number_from_str(s: str) -> Optional[float]:
    clean = s.replace(",", "")
    match = re.search(r"([0-9]{2,6}(?:\.[0-9]+)?)", clean)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _extract_with_gemini(file_bytes: bytes, api_key: str) -> Optional[Dict[str, Any]]:
    import urllib.request
    import base64
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
    import base64
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
