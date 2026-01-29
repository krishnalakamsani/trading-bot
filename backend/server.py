"""FastAPI Server - Thin Controller Layer
Only handles API routes, request validation, and responses.
All business logic is delegated to bot_service and other modules.
"""
from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List

# Local imports
from config import ROOT_DIR, bot_state, config
from models import ConfigUpdate
from database import init_db, load_config, get_trades, get_trade_analytics
import bot_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT_DIR / 'logs' / 'bot.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

# Ensure directories exist
(ROOT_DIR / 'logs').mkdir(exist_ok=True)
(ROOT_DIR / 'data').mkdir(exist_ok=True)


# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"[WS] Broadcast error: {e}")


manager = ConnectionManager()


# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await load_config()
    logger.info(f"[STARTUP] Database initialized, config loaded. indicator_type={config.get('indicator_type')}")
    yield
    logger.info("[SHUTDOWN] Server shutting down")


app = FastAPI(lifespan=lifespan)
api_router = APIRouter(prefix="/api")


# ==================== API Routes ====================

@api_router.get("/")
async def root():
    """Health check endpoint"""
    return {"message": "Trading Bot API", "status": "running"}


@api_router.get("/status")
async def get_status():
    """Get bot status"""
    return bot_service.get_bot_status()


@api_router.get("/market/nifty")
async def get_market_data():
    """Get market data (index LTP, SuperTrend)"""
    return bot_service.get_market_data()


@api_router.get("/position")
async def get_position():
    """Get current position"""
    return bot_service.get_position()


@api_router.get("/trades")
async def get_trades_list(limit: int = Query(default=None, le=10000)):
    """Get trade history. Pass limit=None to get all trades"""
    return await get_trades(limit)


@api_router.get("/analytics")
async def get_analytics():
    """Get comprehensive trade analytics and statistics"""
    return await get_trade_analytics()


@api_router.get("/summary")
async def get_summary():
    """Get daily summary"""
    return bot_service.get_daily_summary()


@api_router.get("/logs")
async def get_logs(level: str = Query(default="all"), limit: int = Query(default=100, le=500)):
    """Get bot logs"""
    logs = []
    log_file = ROOT_DIR / 'logs' / 'bot.log'
    
    if log_file.exists():
        with open(log_file, 'r') as f:
            lines = f.readlines()[-limit:]
            for line in lines:
                try:
                    parts = line.strip().split(' - ')
                    if len(parts) >= 4:
                        timestamp = parts[0]
                        log_level = parts[2]
                        message = ' - '.join(parts[3:])
                        
                        if level == "all" or level.upper() == log_level:
                            logs.append({
                                "timestamp": timestamp,
                                "level": log_level,
                                "message": message
                            })
                except Exception:
                    pass
    
    return logs


@api_router.get("/config")
async def get_config():
    """Get current configuration"""
    return bot_service.get_config()


@api_router.get("/indices")
async def get_indices():
    """Get available indices"""
    return bot_service.get_available_indices_list()


@api_router.get("/candles")
async def get_candles(limit: int = Query(default=1000, le=10000), index_name: str = Query(default=None)):
    """Get historical candle data for analysis"""
    from database import get_candle_data
    return await get_candle_data(limit=limit, index_name=index_name)


@api_router.get("/timeframes")
async def get_timeframes():
    """Get available timeframes"""
    return bot_service.get_available_timeframes()


@api_router.post("/config/update")
async def update_config(update: ConfigUpdate):
    """Update configuration"""
    return await bot_service.update_config_values(update.model_dump(exclude_none=True))


@api_router.post("/config/mode")
async def set_mode(mode: str = Query(..., regex="^(paper|live)$")):
    """Set trading mode"""
    result = await bot_service.set_trading_mode(mode)
    if result.get('status') == 'error':
        raise HTTPException(status_code=400, detail=result['message'])
    return result


@api_router.post("/bot/start")
async def start_bot():
    """Start the trading bot"""
    return await bot_service.start_bot()


@api_router.post("/bot/stop")
async def stop_bot():
    """Stop the trading bot"""
    return await bot_service.stop_bot()


@api_router.post("/bot/squareoff")
async def squareoff():
    """Force square off position"""
    return await bot_service.squareoff_position()


# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await manager.connect(websocket)
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "type": "heartbeat", 
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"[WS] Error: {e}")
        manager.disconnect(websocket)


# Include router and middleware
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
