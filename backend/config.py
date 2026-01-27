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
    "trailing_sl": None,
    "entry_price": 0.0,
    "current_option_ltp": 0.0,
    "max_drawdown": 0.0,
    "selected_index": "NIFTY",  # Current selected index
}

# Configuration (can be updated from frontend)
config = {
    "dhan_access_token": "",
    "dhan_client_id": "",
    "order_qty": 1,  # Number of lots (will be multiplied by lot_size)
    "max_trades_per_day": 5,
    "daily_max_loss": 2000,
    "trail_start_profit": 10,
    "trail_step": 5,
    "trailing_sl_distance": 10,
    "target_points": 0,  # Target profit points (0 = disabled)
    "supertrend_period": 7,
    "supertrend_multiplier": 4,
    "candle_interval": 5,  # seconds (default 5s)
    "selected_index": "NIFTY",  # Default index
    # Trade protection settings
    "min_trade_gap": 0,  # Minimum seconds between trades (0 = disabled)
    "trade_only_on_flip": False,  # Only trade on SuperTrend direction change
}

# SQLite Database path
DB_PATH = ROOT_DIR / 'data' / 'trading.db'

# Ensure directories exist
(ROOT_DIR / 'logs').mkdir(exist_ok=True)
(ROOT_DIR / 'data').mkdir(exist_ok=True)
