import os
import io
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import journal_db as db
from journal_extractor import extract_trade_info_from_image, UPLOAD_DIR

app = FastAPI(title="9:30 NY ICT Trading Journal API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database on startup
db.init_db()

# Ensure directories exist
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static", "journal")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mount uploaded screenshots directory
app.mount("/uploads/screenshots", StaticFiles(directory=UPLOAD_DIR), name="screenshots")

class TradeCreate(BaseModel):
    trade_date: str
    ny_time: Optional[str] = "09:45 AM"
    symbol: str
    action: str
    setup_type: Optional[str] = "OR Breakout (Candle 2 FVG)"
    htf_bias: Optional[str] = "BEARISH"
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_price: Optional[float] = None
    rr_ratio: Optional[float] = 2.0
    outcome: Optional[str] = "OPEN"
    realized_r: Optional[float] = 0.0
    realized_pnl: Optional[float] = 0.0
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    news_guarded: Optional[bool] = False
    notes: Optional[str] = ""
    screenshot_url: Optional[str] = ""

class TradeUpdate(BaseModel):
    trade_date: Optional[str] = None
    ny_time: Optional[str] = None
    symbol: Optional[str] = None
    action: Optional[str] = None
    setup_type: Optional[str] = None
    htf_bias: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    exit_price: Optional[float] = None
    rr_ratio: Optional[float] = None
    outcome: Optional[str] = None
    realized_r: Optional[float] = None
    realized_pnl: Optional[float] = None
    or_high: Optional[float] = None
    or_low: Optional[float] = None
    news_guarded: Optional[bool] = None
    notes: Optional[str] = None
    screenshot_url: Optional[str] = None

@app.get("/api/health")
def health_check():
    return {"status": "ok", "app": "9:30 NY ICT Trading Journal"}

@app.get("/api/trades")
def get_trades_endpoint(
    symbol: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    setup_type: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    sort_by: str = Query("trade_date"),
    sort_dir: str = Query("DESC")
):
    return db.get_trades(
        symbol=symbol,
        action=action,
        outcome=outcome,
        setup_type=setup_type,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
        sort_dir=sort_dir
    )

@app.get("/api/trades/{trade_id}")
def get_trade_endpoint(trade_id: int):
    trade = db.get_trade_by_id(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade

@app.post("/api/trades")
def create_trade_endpoint(trade: TradeCreate):
    trade_id = db.add_trade(trade.model_dump())
    return {"status": "created", "id": trade_id, "message": "Trade logged successfully"}

@app.put("/api/trades/{trade_id}")
def update_trade_endpoint(trade_id: int, trade: TradeUpdate):
    existing = db.get_trade_by_id(trade_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Trade not found")
    
    # Merge updates into existing
    update_data = {**existing, **{k: v for k, v in trade.model_dump().items() if v is not None}}
    success = db.update_trade(trade_id, update_data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update trade")
    return {"status": "updated", "id": trade_id}

@app.delete("/api/trades/{trade_id}")
def delete_trade_endpoint(trade_id: int):
    success = db.delete_trade(trade_id)
    if not success:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"status": "deleted", "id": trade_id}

@app.get("/api/stats")
def get_stats_endpoint():
    return db.get_stats()

@app.post("/api/extract-screenshot")
async def extract_screenshot_endpoint(file: UploadFile = File(...)):
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    
    extracted = extract_trade_info_from_image(contents, file.filename or "chart.png")
    return {
        "status": "extracted",
        "data": extracted
    }

@app.get("/api/export/csv")
def export_csv_endpoint():
    csv_content = db.export_trades_csv()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=930_ict_trades_journal.csv"}
    )

# Serve the static UI files at root
@app.get("/")
def serve_ui():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return HTMLResponse("<h2>9:30 NY ICT Trading Journal GUI Initializing...</h2>")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
