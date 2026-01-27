# Bot Service - Interface layer between API routes and TradingBot
import logging
from typing import Optional
from config import bot_state, config
from indices import get_index_config, get_available_indices
from database import save_config, load_config

logger = logging.getLogger(__name__)

# Lazy import to avoid circular imports
_trading_bot = None

def get_trading_bot():
    """Get or create the trading bot instance"""
    global _trading_bot
    if _trading_bot is None:
        from trading_bot import TradingBot
        _trading_bot = TradingBot()
    return _trading_bot


async def start_bot() -> dict:
    """Start the trading bot"""
    bot = get_trading_bot()
    result = await bot.start()
    logger.info(f"[BOT] Start requested: {result}")
    return result


async def stop_bot() -> dict:
    """Stop the trading bot"""
    bot = get_trading_bot()
    result = await bot.stop()
    logger.info(f"[BOT] Stop requested: {result}")
    return result


async def squareoff_position() -> dict:
    """Force square off current position"""
    bot = get_trading_bot()
    result = await bot.squareoff()
    logger.info(f"[BOT] Squareoff requested: {result}")
    return result


def get_bot_status() -> dict:
    """Get current bot status"""
    from utils import is_market_open
    
    return {
        "is_running": bot_state['is_running'],
        "mode": bot_state['mode'],
        "market_status": "open" if is_market_open() else "closed",
        "connection_status": "connected" if config['dhan_access_token'] else "disconnected",
        "daily_max_loss_triggered": bot_state['daily_max_loss_triggered'],
        "selected_index": config['selected_index'],
        "candle_interval": config['candle_interval']
    }


def get_market_data() -> dict:
    """Get current market data"""
    from datetime import datetime, timezone
    
    return {
        "ltp": bot_state['index_ltp'],
        "supertrend_signal": bot_state['last_supertrend_signal'],
        "supertrend_value": bot_state['supertrend_value'],
        "selected_index": config['selected_index'],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


def get_position() -> dict:
    """Get current position info"""
    if not bot_state['current_position']:
        return {"has_position": False}
    
    index_config = get_index_config(config['selected_index'])
    qty = config['order_qty'] * index_config['lot_size']
    unrealized_pnl = (bot_state['current_option_ltp'] - bot_state['entry_price']) * qty
    
    return {
        "has_position": True,
        "option_type": bot_state['current_position'].get('option_type'),
        "strike": bot_state['current_position'].get('strike'),
        "expiry": bot_state['current_position'].get('expiry'),
        "index_name": bot_state['current_position'].get('index_name', config['selected_index']),
        "entry_price": bot_state['entry_price'],
        "current_ltp": bot_state['current_option_ltp'],
        "unrealized_pnl": unrealized_pnl,
        "trailing_sl": bot_state['trailing_sl'],
        "qty": qty
    }


def get_daily_summary() -> dict:
    """Get daily trading summary"""
    return {
        "total_trades": bot_state['daily_trades'],
        "total_pnl": bot_state['daily_pnl'],
        "max_drawdown": bot_state['max_drawdown'],
        "daily_stop_triggered": bot_state['daily_max_loss_triggered']
    }


def get_config() -> dict:
    """Get current configuration"""
    index_config = get_index_config(config['selected_index'])
    
    return {
        "order_qty": config['order_qty'],
        "max_trades_per_day": config['max_trades_per_day'],
        "daily_max_loss": config['daily_max_loss'],
        "trail_start_profit": config['trail_start_profit'],
        "trail_step": config['trail_step'],
        "trailing_sl_distance": config['trailing_sl_distance'],
        "target_points": config['target_points'],
        "has_credentials": bool(config['dhan_access_token'] and config['dhan_client_id']),
        "mode": bot_state['mode'],
        "selected_index": config['selected_index'],
        "candle_interval": config['candle_interval'],
        "lot_size": index_config['lot_size'],
        "strike_interval": index_config['strike_interval'],
        "expiry_type": index_config.get('expiry_type', 'weekly')
    }


async def update_config_values(updates: dict) -> dict:
    """Update configuration values"""
    updated_fields = []
    
    if updates.get('dhan_access_token') is not None:
        config['dhan_access_token'] = str(updates['dhan_access_token'])
        updated_fields.append('dhan_access_token')
        
    if updates.get('dhan_client_id') is not None:
        config['dhan_client_id'] = str(updates['dhan_client_id'])
        updated_fields.append('dhan_client_id')
        
    if updates.get('order_qty') is not None:
        qty = int(updates['order_qty'])
        # Limit to 1-10 lots for safety
        config['order_qty'] = max(1, min(10, qty))
        updated_fields.append('order_qty')
        if qty != config['order_qty']:
            logger.warning(f"[CONFIG] order_qty capped from {qty} to {config['order_qty']} (max 10 lots)")
        
    if updates.get('max_trades_per_day') is not None:
        config['max_trades_per_day'] = int(updates['max_trades_per_day'])
        updated_fields.append('max_trades_per_day')
        
    if updates.get('daily_max_loss') is not None:
        config['daily_max_loss'] = float(updates['daily_max_loss'])
        updated_fields.append('daily_max_loss')
        
    if updates.get('trail_start_profit') is not None:
        config['trail_start_profit'] = float(updates['trail_start_profit'])
        updated_fields.append('trail_start_profit')
        
    if updates.get('trail_step') is not None:
        config['trail_step'] = float(updates['trail_step'])
        updated_fields.append('trail_step')
        
    if updates.get('trailing_sl_distance') is not None:
        config['trailing_sl_distance'] = float(updates['trailing_sl_distance'])
        updated_fields.append('trailing_sl_distance')
    
    if updates.get('target_points') is not None:
        config['target_points'] = float(updates['target_points'])
        updated_fields.append('target_points')
        logger.info(f"[CONFIG] Target points changed to: {config['target_points']}")
        
    if updates.get('selected_index') is not None:
        new_index = updates['selected_index'].upper()
        available = get_available_indices()
        if new_index in available:
            config['selected_index'] = new_index
            bot_state['selected_index'] = new_index
            updated_fields.append('selected_index')
            logger.info(f"[CONFIG] Index changed to: {new_index}")
        else:
            logger.warning(f"[CONFIG] Invalid index: {new_index}. Available: {available}")
            
    if updates.get('candle_interval') is not None:
        valid_intervals = [5, 15, 30, 60, 300, 900]  # 5s, 15s, 30s, 1m, 5m, 15m
        new_interval = int(updates['candle_interval'])
        if new_interval in valid_intervals:
            config['candle_interval'] = new_interval
            updated_fields.append('candle_interval')
            logger.info(f"[CONFIG] Candle interval changed to: {new_interval}s")
            # Reset SuperTrend when interval changes
            bot = get_trading_bot()
            bot.reset_supertrend()
        else:
            logger.warning(f"[CONFIG] Invalid interval: {new_interval}. Valid: {valid_intervals}")
    
    await save_config()
    logger.info(f"[CONFIG] Updated: {updated_fields}")
    
    return {"status": "success", "message": "Configuration updated", "updated": updated_fields}


async def set_trading_mode(mode: str) -> dict:
    """Set trading mode (paper/live)"""
    if bot_state['current_position']:
        return {"status": "error", "message": "Cannot change mode with open position"}
    
    if mode not in ['paper', 'live']:
        return {"status": "error", "message": "Invalid mode. Use 'paper' or 'live'"}
    
    bot_state['mode'] = mode
    logger.info(f"[CONFIG] Trading mode changed to: {mode}")
    
    return {"status": "success", "mode": mode}


def get_available_indices_list() -> list:
    """Get list of available indices with their config"""
    from indices import INDICES
    
    result = []
    for name, cfg in INDICES.items():
        result.append({
            "name": name,
            "display_name": cfg['name'],
            "lot_size": cfg['lot_size'],
            "strike_interval": cfg['strike_interval'],
            "expiry_type": cfg.get('expiry_type', 'weekly'),
            "expiry_day": cfg.get('expiry_day', 1)
        })
    return result


def get_available_timeframes() -> list:
    """Get list of available timeframes"""
    return [
        {"value": 5, "label": "5 seconds"},
        {"value": 15, "label": "15 seconds"},
        {"value": 30, "label": "30 seconds"},
        {"value": 60, "label": "1 minute"},
        {"value": 300, "label": "5 minutes"},
        {"value": 900, "label": "15 minutes"}
    ]
