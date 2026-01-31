# Pydantic models for API requests/responses
from pydantic import BaseModel
from typing import Optional

class ConfigUpdate(BaseModel):
    dhan_access_token: Optional[str] = None
    dhan_client_id: Optional[str] = None
    order_qty: Optional[int] = None
    max_trades_per_day: Optional[int] = None
    daily_max_loss: Optional[float] = None
    initial_stoploss: Optional[float] = None  # Fixed SL points below entry
    max_loss_per_trade: Optional[float] = None  # Max loss per trade (₹, 0=disabled)
    trail_start_profit: Optional[float] = None
    trail_step: Optional[float] = None
    target_points: Optional[float] = None  # Target profit points for exit
    risk_per_trade: Optional[float] = None  # Risk amount per trade for position sizing
    selected_index: Optional[str] = None
    candle_interval: Optional[int] = None  # Timeframe in seconds
    min_trade_gap: Optional[int] = None  # Minimum seconds between trades
    trade_only_on_flip: Optional[bool] = None  # Only trade on SuperTrend flip

    # Testing utilities
    # bypass_market_hours removed: bot always fetches data when running; entries are gated by time window.

    # Strategy / Agent
    strategy_mode: Optional[str] = None  # 'agent' | 'supertrend'
    agent_adx_min: Optional[float] = None
    agent_wave_reset_macd_abs: Optional[float] = None
    persist_agent_state: Optional[bool] = None

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


class BacktestRequest(BaseModel):
    index_name: str = "NIFTY"
    limit: int = 2000
    start_time: Optional[str] = None  # ISO string
    end_time: Optional[str] = None    # ISO string

    # Candle timeframe for backtest (minutes). If >1, backend will resample.
    timeframe_minutes: Optional[int] = None

    # Strategy selection
    strategy_mode: str = "agent"  # 'agent' | 'supertrend'

    # Params (optional overrides; defaults from config if omitted in server)
    agent_adx_min: Optional[float] = None
    agent_wave_reset_macd_abs: Optional[float] = None

    # Risk / exits (points-based, simulated on candle OHLC)
    # If omitted, server will use current config defaults.
    initial_stoploss: Optional[float] = None
    target_points: Optional[float] = None
    trail_start_profit: Optional[float] = None
    trail_step: Optional[float] = None

    close_open_position_at_end: bool = True


class DhanCandleImportRequest(BaseModel):
    index_name: str = "NIFTY"
    interval_minutes: int = 5  # 1,5,15,25,60 supported by Dhan
    from_date: str  # "YYYY-MM-DD HH:MM:SS"
    to_date: str    # "YYYY-MM-DD HH:MM:SS"
    replace_existing_range: bool = False
