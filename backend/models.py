# Pydantic models for API requests/responses
from pydantic import BaseModel
from typing import Optional

class ConfigUpdate(BaseModel):
    dhan_access_token: Optional[str] = None
    dhan_client_id: Optional[str] = None
    order_qty: Optional[int] = None
    max_trades_per_day: Optional[int] = None
    daily_max_loss: Optional[float] = None
    trail_start_profit: Optional[float] = None
    trail_step: Optional[float] = None
    trailing_sl_distance: Optional[float] = None
    target_points: Optional[float] = None  # Target profit points for exit
    selected_index: Optional[str] = None
    candle_interval: Optional[int] = None  # Timeframe in seconds
    min_trade_gap: Optional[int] = None  # Minimum seconds between trades
    trade_only_on_flip: Optional[bool] = None  # Only trade on SuperTrend flip

class BotStatus(BaseModel):
    is_running: bool
    mode: str
    market_status: str
    connection_status: str
    selected_index: str
    candle_interval: int

class Position(BaseModel):
    option_type: Optional[str] = None
    strike: Optional[int] = None
    expiry: Optional[str] = None
    entry_price: float = 0.0
    current_ltp: float = 0.0
    unrealized_pnl: float = 0.0
    trailing_sl: Optional[float] = None
    qty: int = 0
    index_name: Optional[str] = None

class Trade(BaseModel):
    trade_id: str
    entry_time: str
    exit_time: Optional[str] = None
    option_type: str
    strike: int
    expiry: str
    entry_price: float
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    exit_reason: Optional[str] = None
    index_name: Optional[str] = None

class DailySummary(BaseModel):
    total_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    daily_stop_triggered: bool = False

class LogEntry(BaseModel):
    timestamp: str
    level: str
    message: str
    tag: Optional[str] = None

class IndexInfo(BaseModel):
    name: str
    display_name: str
    lot_size: int
    strike_interval: int

class TimeframeInfo(BaseModel):
    value: int
    label: str
