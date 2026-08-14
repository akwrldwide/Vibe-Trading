import os
import re
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
import cv2
import numpy as np
from PIL import Image, ImageEnhance
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
    Uses multi-stage detection:
    1. HSV Color-Segmented Badge Detection (Red=SL, Blue=ENTRY, Green=TP)
    2. OCR on Status Table & Header Bar (Symbol, Action, HTF Bias, Date, Time)
    3. Scale Alignment & Mathematical Validation
    4. Cloud AI fallback if API keys provided
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
        "ny_time": "09:45 AM",
        "notes": "",
        "screenshot_url": screenshot_url,
        "confidence": "ocr_local"
    }

    reader = get_ocr_reader()
    if reader:
        try:
            # 1. Convert bytes to OpenCV image
            nparr = np.frombuffer(file_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            # 2. Extract Header Text & Overall Table Text
            ocr_results_full = reader.readtext(file_bytes)
            parsed_text = _parse_header_and_table(ocr_results_full)
            for k, v in parsed_text.items():
                if v is not None:
                    extracted_data[k] = v

            # 3. Detect benchmark market price
            bench_price = parsed_text.get("benchmark_price") or (63500.0 if "BTC" in extracted_data["symbol"] else 1.1000)

            # 4. Color-Segmented Badge Extraction for exact Entry, SL, TP
            badge_prices = _extract_color_badges(img, reader, bench_price)
            if badge_prices.get("entry_price"):
                extracted_data["entry_price"] = badge_prices["entry_price"]
            if badge_prices.get("stop_loss"):
                extracted_data["stop_loss"] = badge_prices["stop_loss"]
            if badge_prices.get("take_profit"):
                extracted_data["take_profit"] = badge_prices["take_profit"]

            # 5. If badges not found, fallback to table numbers
            if not extracted_data["entry_price"] or not extracted_data["stop_loss"]:
                table_prices = _extract_table_crop_prices(img, reader, bench_price, extracted_data["action"])
                for k, v in table_prices.items():
                    if v is not None and not extracted_data.get(k):
                        extracted_data[k] = v

            # 6. Calculate & Verify Risk:Reward
            if extracted_data.get("entry_price") and extracted_data.get("stop_loss") and extracted_data.get("take_profit"):
                risk = abs(extracted_data["entry_price"] - extracted_data["stop_loss"])
                reward = abs(extracted_data["take_profit"] - extracted_data["entry_price"])
                if risk > 0:
                    extracted_data["rr_ratio"] = round(reward / risk, 1)

            # 7. Generate summary note
            ep_str = f"{extracted_data['entry_price']:.2f}" if extracted_data.get('entry_price') else "N/A"
            sl_str = f"{extracted_data['stop_loss']:.2f}" if extracted_data.get('stop_loss') else "N/A"
            tp_str = f"{extracted_data['take_profit']:.2f}" if extracted_data.get('take_profit') else "N/A"
            extracted_data["notes"] = f"Auto-extracted {extracted_data['action']} on {extracted_data['symbol']} (Entry: {ep_str}, SL: {sl_str}, TP: {tp_str})."
            extracted_data["confidence"] = "ocr_high"

            if extracted_data.get("entry_price") and extracted_data.get("stop_loss"):
                return extracted_data
        except Exception as e:
            logger.warning(f"Local OCR extraction error: {e}")

    # Cloud AI fallbacks if API keys present in .env
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
            logger.warning(f"OpenAI vision fallback: {e}")

    return extracted_data


def _align_price_scale(price: float, benchmark: float) -> float:
    """Corrects decimal placement if OCR skipped or shifted a tiny decimal point."""
    if not price or not benchmark or benchmark <= 0:
        return price
    while price > benchmark * 2.5:
        price = price / 10.0
    while price < benchmark * 0.4:
        price = price * 10.0
    return round(price, 2)


def _extract_color_badges(img: np.ndarray, reader, bench_price: float) -> Dict[str, Optional[float]]:
    """Detects Red (SL), Blue (ENTRY), and Green (TP) badge rectangles on the chart."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    img_h, img_w = img.shape[:2]

    # Red mask (SL)
    mask_red1 = cv2.inRange(hsv, np.array([0, 90, 90]), np.array([12, 255, 255]))
    mask_red2 = cv2.inRange(hsv, np.array([168, 90, 90]), np.array([180, 255, 255]))
    mask_red = mask_red1 | mask_red2

    # Blue mask (ENTRY)
    mask_blue = cv2.inRange(hsv, np.array([95, 90, 90]), np.array([135, 255, 255]))

    # Green mask (TP)
    mask_green = cv2.inRange(hsv, np.array([35, 90, 90]), np.array([85, 255, 255]))

    def read_badge_contour(mask) -> Optional[float]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            # Badges are rectangular boxes located on the right/middle of the chart
            if x > img_w * 0.35 and w > 30 and 8 < h < 50:
                x1 = max(0, x - 4)
                y1 = max(0, y - 4)
                x2 = min(img_w, x + w + 4)
                y2 = min(img_h, y + h + 4)
                crop = img[y1:y2, x1:x2]
                if crop.shape[0] > 0 and crop.shape[1] > 0:
                    crop = cv2.resize(crop, (crop.shape[1] * 4, crop.shape[0] * 4), interpolation=cv2.INTER_CUBIC)
                    res = reader.readtext(crop)
                    for _, text, conf in res:
                        clean = text.replace(",", "").replace(" ", "").replace("O", "0").replace("o", "0")
                        m = re.search(r"([0-9]{3,8}(?:\.[0-9]+)?)", clean)
                        if m:
                            raw_val = float(m.group(1))
                            aligned = _align_price_scale(raw_val, bench_price)
                            candidates.append((aligned, conf, x))
        if candidates:
            # Sort by highest x (furthest right badge label) and confidence
            candidates.sort(key=lambda item: (item[2], item[1]), reverse=True)
            return candidates[0][0]
        return None

    sl = read_badge_contour(mask_red)
    entry = read_badge_contour(mask_blue)
    tp = read_badge_contour(mask_green)

    return {"entry_price": entry, "stop_loss": sl, "take_profit": tp}


def _extract_table_crop_prices(img: np.ndarray, reader, bench_price: float, action: str) -> Dict[str, Optional[float]]:
    """Crops the top-right quadrant where the Pine Script table sits and reads candidate prices."""
    h, w = img.shape[:2]
    tr_crop = img[0:int(h * 0.35), int(w * 0.65):w]
    tr_crop = cv2.resize(tr_crop, (tr_crop.shape[1] * 3, tr_crop.shape[0] * 3), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(tr_crop, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.equalizeHist(gray)
    
    res = reader.readtext(enhanced)
    detected: List[float] = []
    
    for _, text, _ in res:
        clean = text.replace(",", "").replace("O", "0")
        for m in re.finditer(r"\b([0-9]{4,7}(?:\.[0-9]+)?)\b", clean):
            val = float(m.group(1))
            aligned = _align_price_scale(val, bench_price)
            if aligned > 0 and (aligned % 50 != 0):
                detected.append(aligned)

    detected = sorted(list(set(detected)))
    if len(detected) >= 3:
        if action == "SELL":
            return {"take_profit": detected[0], "entry_price": detected[1], "stop_loss": detected[2]}
        else:
            return {"stop_loss": detected[0], "entry_price": detected[1], "take_profit": detected[2]}
    elif len(detected) == 2:
        if action == "SELL":
            return {"entry_price": detected[0], "stop_loss": detected[1], "take_profit": round(detected[0] - (detected[1] - detected[0]) * 2.0, 2)}
        else:
            return {"stop_loss": detected[0], "entry_price": detected[1], "take_profit": round(detected[1] + (detected[1] - detected[0]) * 2.0, 2)}

    return {}


def _parse_header_and_table(results: List) -> Dict[str, Any]:
    parsed: Dict[str, Any] = {}
    lines = [text.strip() for _, text, _ in results if text and text.strip()]
    full_text = " \n ".join(lines)

    # 1. Symbol
    sym_match = re.search(r"\b(BTCUSDT|ETHUSDT|SOLUSDT|EURUSD|GBPUSD|AUDUSD|USDCAD|USDJPY|NQ1!|ES1!|NAS100|SPX500|US30)\b", full_text, re.IGNORECASE)
    if sym_match:
        parsed["symbol"] = sym_match.group(1).upper()
    elif lines and ("Perpetual" in lines[0] or "Contract" in lines[0] or "/" in lines[0]):
        first_word = lines[0].split()[0].replace("/", "").upper()
        if len(first_word) >= 3:
            parsed["symbol"] = first_word
    else:
        parsed["symbol"] = "BTCUSDT"

    # 2. Benchmark Market Price from OHLC Header (e.g. C63,827.3)
    bench_match = re.search(r"[OHLC]([0-9]{2,6}(?:,[0-9]{3})*(?:\.[0-9]+)?)", full_text)
    if bench_match:
        try:
            parsed["benchmark_price"] = float(bench_match.group(1).replace(",", ""))
        except ValueError:
            parsed["benchmark_price"] = None

    # 3. Action & Outcome
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

    # 4. HTF Bias
    bias_match = re.search(r"HTF Bias[^\n]*\n\s*(BEARISH|BULLISH)", full_text, re.IGNORECASE)
    if not bias_match:
        bias_match = re.search(r"\b(BEARISH|BULLISH)\b", full_text, re.IGNORECASE)
    if bias_match:
        parsed["htf_bias"] = bias_match.group(1).upper()

    # 5. Session Date
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

    # 6. Session Time
    time_match = re.search(r"\b(09:[3-5][0-9]|10:[0-5][0-9]|11:[0-3][0-9]|14:[0-5][0-9]|15:[0-5][0-9]|16:[0-5][0-9])\b", full_text)
    if time_match:
        parsed["ny_time"] = time_match.group(1)

    return parsed


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
    Look at the badges on the right (ENTRY, SL, TP) and the top-right status table.
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
