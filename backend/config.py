# Configuration and state management
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Valid timeframe options (in seconds)
VALID_TIMEFRAMES = [5, 15, 30, 60, 300, 900]  # 5s, 15s, 30s, 1m, 5m, 15m

# Global bot state
bot_state = {
    "is_running": False,
    "mode": "paper",  # paper or live (default to paper for safety)
    "current_position": None,
    "daily_trades": 0,
    "daily_pnl": 0.0,
    "daily_max_loss_triggered": False,
    "last_supertrend_signal": None,
    "index_ltp": 0.0,
    "supertrend_value": 0.0,
    "macd_value": 0.0,  # MACD line value
    "adx_value": 0.0,
    "signal_status": "waiting",  # waiting, buy (GREEN), sell (RED)
    "trailing_sl": None,
    "entry_price": 0.0,
    "current_option_ltp": 0.0,
    "max_drawdown": 0.0,
    "selected_index": "NIFTY",  # Current selected index
    "strategy_mode": "agent",
    # Signal source: 'index' (default) or 'option_fixed'
    "signal_source": "index",
    # Fixed-contract option signal metadata (best-effort)
    "fixed_option_strike": None,
    "fixed_option_expiry": None,
    "fixed_ce_security_id": None,
    "fixed_pe_security_id": None,
    "signal_ce_ltp": 0.0,
    "signal_pe_ltp": 0.0,
}

# Configuration (can be updated from frontend)
config = {
    "dhan_access_token": "",
    "dhan_client_id": "",
    "order_qty": 1,  # Number of lots (will be multiplied by lot_size)
    "max_trades_per_day": 5,
    "daily_max_loss": 2000,
    # Stop Loss Parameters
    "initial_stoploss": 50,  # Fixed SL points below entry (0 = disabled)
    "max_loss_per_trade": 0,  # Max loss amount per trade (₹, 0 = disabled)
    "trail_start_profit": 0,  # Profit points to start trailing (0 = disabled)
    "trail_step": 0,  # Trailing step size (0 = disabled)
    # Profit Taking
    "target_points": 0,  # Target profit points (0 = disabled)
    # Signal & Indicator Settings (SuperTrend only)
    "supertrend_period": 7,
    "supertrend_multiplier": 4,
    # Strategy selection
    # - agent: ST + ADX + MACD agent decides
    # - supertrend: simple ST flip logic (fallback)
    "strategy_mode": "agent",
    # Signal source for strategy indicators
    # - index: indicators computed on index OHLC (current behavior)
    # - option_fixed: indicators computed on a fixed CE+PE contract's OHLC
    "signal_source": "index",
    # Agent tuning
    "agent_adx_min": 20.0,
    "agent_wave_reset_macd_abs": 0.05,
    # Persist agent state (wave_lock/last_trade_side) across container restarts
    "persist_agent_state": True,
    "candle_interval": 5,  # seconds (default 5s)
    "selected_index": "NIFTY",  # Default index
    # Trade protection settings
    "min_trade_gap": 0,  # Minimum seconds between trades (0 = disabled)
    "trade_only_on_flip": False,  # Only trade on SuperTrend direction change
    "risk_per_trade": 0,  # Risk amount per trade (0 = disabled, uses fixed qty)

    # Testing utilities
    # When enabled, the bot loop will run outside market hours and simulate LTPs.
    "bypass_market_hours": False,
}

# SQLite Database path
DB_PATH = ROOT_DIR / 'data' / 'trading.db'

# Ensure directories exist
(ROOT_DIR / 'logs').mkdir(exist_ok=True)
(ROOT_DIR / 'data').mkdir(exist_ok=True)
