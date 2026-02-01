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
    """Get current bot status with market hour validation"""
    from utils import is_market_open, get_ist_time
    
    ist = get_ist_time()
    market_is_open = is_market_open()
    is_weekday = ist.weekday() < 5  # 0-4 = Mon-Fri, 5-6 = Sat-Sun
    
    logger.debug(f"[STATUS] Market check: Weekday={is_weekday}, Time={ist.strftime('%H:%M')}, Open={market_is_open}")
    
    return {
        "is_running": bot_state['is_running'],
        "mode": bot_state['mode'],
        "market_status": "open" if market_is_open else "closed",
        "market_details": {
            "is_weekday": is_weekday,
            "current_time_ist": ist.strftime('%H:%M:%S'),
            "trading_hours": "09:15 - 15:30 IST",
            "allow_weekend_trading": bool(config.get('allow_weekend_trading', False)),
        },
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
        "macd_value": bot_state['macd_value'],
        "macd_hist": bot_state.get('macd_hist', 0.0),
        # Fixed-contract option signal prices (CE/PE)
        "signal_ce_ltp": bot_state.get('signal_ce_ltp', 0.0),
        "signal_pe_ltp": bot_state.get('signal_pe_ltp', 0.0),
        "signal_ce_supertrend_signal": bot_state.get('signal_ce_supertrend_signal'),
        "signal_pe_supertrend_signal": bot_state.get('signal_pe_supertrend_signal'),
        "signal_ce_supertrend_value": bot_state.get('signal_ce_supertrend_value', 0.0),
        "signal_pe_supertrend_value": bot_state.get('signal_pe_supertrend_value', 0.0),
        "signal_ce_macd_hist": bot_state.get('signal_ce_macd_hist', 0.0),
        "signal_pe_macd_hist": bot_state.get('signal_pe_macd_hist', 0.0),
        # Fixed-contract metadata
        "fixed_option_strike": bot_state.get('fixed_option_strike'),
        "fixed_option_expiry": bot_state.get('fixed_option_expiry'),
        "fixed_ce_security_id": bot_state.get('fixed_ce_security_id'),
        "fixed_pe_security_id": bot_state.get('fixed_pe_security_id'),
        # Kept for backward compatibility with older UI builds.
        "adx_value": 0.0,
        "signal_status": bot_state['signal_status'],
        "strategy_mode": bot_state.get('strategy_mode', 'st_macd_hist'),
        "selected_index": config['selected_index'],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


async def debug_quotes() -> dict:
    """One-shot quote fetch for troubleshooting live option prices.

    Returns a filtered snapshot:
    - Index + option security IDs used
    - Parsed last_price mapping for CE/PE
    - Raw (filtered) broker payload for those IDs
    """
    from utils import get_ist_time, is_market_open

    index_name = str(config.get('selected_index') or 'NIFTY').upper()
    ist = get_ist_time()

    bot = get_trading_bot()
    if not getattr(bot, 'dhan', None):
        try:
            ok = bot.initialize_dhan()
        except Exception:
            ok = False
        if not ok:
            return {
                "status": "error",
                "message": "Dhan API not initialized. Please set dhan_access_token and dhan_client_id.",
                "meta": {
                    "index_name": index_name,
                    "current_time_ist": ist.strftime('%H:%M:%S'),
                    "market_open": bool(is_market_open()),
                },
            }

    try:
        idx_cfg = get_index_config(index_name)
        segment = str(idx_cfg.get('exchange_segment') or 'IDX_I')
        fno_segment = str(idx_cfg.get('fno_segment') or 'NSE_FNO')
        index_security_id = int(idx_cfg.get('security_id') or 0)

        # Read any existing fixed contract IDs first (these may already be available even
        # if index last_price is temporarily 0 on special sessions).
        ce_sid = bot_state.get('fixed_ce_security_id')
        pe_sid = bot_state.get('fixed_pe_security_id')
        ids: list[int] = []
        for x in (ce_sid, pe_sid):
            if x:
                try:
                    ids.append(int(x))
                except Exception:
                    pass

        # If fixed IDs are not present, attempt to derive them (requires a valid index LTP).
        fixed_ready = bool(ids)
        index_ltp = float(bot_state.get('index_ltp') or 0.0)
        if not fixed_ready:
            try:
                derived_index_ltp = float(bot.dhan.get_index_ltp(index_name) or 0.0)
            except Exception:
                derived_index_ltp = 0.0
            if derived_index_ltp and derived_index_ltp > 0:
                index_ltp = float(derived_index_ltp)

            if not index_ltp or index_ltp <= 0:
                return {
                    "status": "error",
                    "message": "Index LTP returned 0; cannot select fixed ATM contracts.",
                    "meta": {
                        "index_name": index_name,
                        "segment": segment,
                        "index_security_id": index_security_id,
                        "current_time_ist": ist.strftime('%H:%M:%S'),
                        "market_open": bool(is_market_open()),
                        "allow_weekend_trading": bool(config.get('allow_weekend_trading', False)),
                        "bot_state_index_ltp": float(bot_state.get('index_ltp') or 0.0),
                    },
                }

            try:
                fixed_ready = await bot._ensure_fixed_option_contract(index_name, float(index_ltp))
            except Exception:
                fixed_ready = False

            ce_sid = bot_state.get('fixed_ce_security_id')
            pe_sid = bot_state.get('fixed_pe_security_id')
            ids = []
            for x in (ce_sid, pe_sid):
                if x:
                    try:
                        ids.append(int(x))
                    except Exception:
                        pass

            if not fixed_ready or not ids:
                return {
                    "status": "error",
                    "message": "Fixed option contracts are not ready (missing CE/PE security IDs).",
                    "meta": {
                        "index_name": index_name,
                        "index_ltp": float(index_ltp or 0.0),
                        "fixed_ready": bool(fixed_ready),
                        "fixed_option_strike": bot_state.get('fixed_option_strike'),
                        "fixed_option_expiry": bot_state.get('fixed_option_expiry'),
                        "fixed_ce_security_id": ce_sid,
                        "fixed_pe_security_id": pe_sid,
                        "current_time_ist": ist.strftime('%H:%M:%S'),
                        "market_open": bool(is_market_open()),
                        "allow_weekend_trading": bool(config.get('allow_weekend_trading', False)),
                        "bot_state_index_ltp": float(bot_state.get('index_ltp') or 0.0),
                    },
                }

        # Perform a single broker quote call and parse only required fields.
        # Perform a single broker quote call.
        # Some special sessions may not provide index last_price in IDX_I; option quotes are still useful.
        payload = {fno_segment: ids}
        if index_security_id and index_security_id > 0:
            payload[segment] = [index_security_id]
        response = bot.dhan.dhan.quote_data(payload)

        # Also try an options-only quote (helps when combined segment+FNO is rejected)
        response_options_only = bot.dhan.dhan.quote_data({fno_segment: ids})

        data = (response or {}).get('data', {}) if isinstance(response, dict) else {}
        if isinstance(data, dict) and 'data' in data:
            data = data.get('data', {})

        idx_data = data.get(segment, {}).get(str(index_security_id), {}) if isinstance(data, dict) else {}
        parsed_index_ltp = float((idx_data or {}).get('last_price', 0) or 0)
        if parsed_index_ltp > 0:
            index_ltp = parsed_index_ltp

        fno_map = data.get(fno_segment, {}) if isinstance(data, dict) else {}
        option_ltps: dict[int, float] = {}
        raw_options: dict[str, dict] = {}
        if isinstance(fno_map, dict):
            for sid in ids:
                entry = fno_map.get(str(sid), {}) or {}
                if isinstance(entry, dict):
                    option_ltps[sid] = float(entry.get('last_price', 0) or 0)
                    raw_options[str(sid)] = {
                        "last_price": entry.get('last_price', 0),
                        "ohlc": entry.get('ohlc'),
                        "volume": entry.get('volume'),
                        "oi": entry.get('oi'),
                        "timestamp": entry.get('timestamp') or entry.get('last_traded_time'),
                    }

        missing = [sid for sid in ids if sid not in option_ltps]
        zero = [sid for sid, v in option_ltps.items() if not v or v <= 0]

        # Parse the options-only response too
        data2 = (response_options_only or {}).get('data', {}) if isinstance(response_options_only, dict) else {}
        if isinstance(data2, dict) and 'data' in data2:
            data2 = data2.get('data', {})
        fno_map2 = data2.get(fno_segment, {}) if isinstance(data2, dict) else {}
        option_ltps_options_only: dict[int, float] = {}
        if isinstance(fno_map2, dict):
            for sid in ids:
                entry2 = fno_map2.get(str(sid), {}) or {}
                if isinstance(entry2, dict):
                    option_ltps_options_only[sid] = float(entry2.get('last_price', 0) or 0)

        return {
            "status": "success",
            "meta": {
                "index_name": index_name,
                "current_time_ist": ist.strftime('%H:%M:%S'),
                "market_open": bool(is_market_open()),
                "allow_weekend_trading": bool(config.get('allow_weekend_trading', False)),
                "segment": segment,
                "fno_segment": fno_segment,
                "index_security_id": index_security_id,
                "fixed_option_strike": bot_state.get('fixed_option_strike'),
                "fixed_option_expiry": bot_state.get('fixed_option_expiry'),
                "fixed_ce_security_id": ce_sid,
                "fixed_pe_security_id": pe_sid,
                "missing": missing,
                "zero": zero,
                "broker_status": (response or {}).get('status') if isinstance(response, dict) else None,
                "broker_remarks": (response or {}).get('remarks') if isinstance(response, dict) else None,
                "broker_error_code": (response or {}).get('error_code') if isinstance(response, dict) else None,
                "broker_message": (response or {}).get('message') if isinstance(response, dict) else None,
                "broker_status_options_only": (response_options_only or {}).get('status') if isinstance(response_options_only, dict) else None,
                "broker_remarks_options_only": (response_options_only or {}).get('remarks') if isinstance(response_options_only, dict) else None,
            },
            "quotes": {
                "index_ltp": float(index_ltp or 0.0),
                "option_ltps": option_ltps,
                "option_ltps_options_only": option_ltps_options_only,
                "bot_state_index_ltp": float(bot_state.get('index_ltp') or 0.0),
                "bot_state_signal_ce_ltp": bot_state.get('signal_ce_ltp', 0.0),
                "bot_state_signal_pe_ltp": bot_state.get('signal_pe_ltp', 0.0),
            },
            "raw": {
                "options": raw_options,
            },
        }
    except Exception as e:
        logger.exception("[DEBUG] debug_quotes failed")
        return {
            "status": "error",
            "message": f"debug_quotes failed: {e}",
            "meta": {
                "index_name": index_name,
                "current_time_ist": ist.strftime('%H:%M:%S'),
                "market_open": bool(is_market_open()),
            },
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


def get_strategy_status() -> dict:
    """Get live strategy/agent state for debugging (read-only)."""
    return {
        "strategy_mode": bot_state.get('strategy_mode', 'st_macd_hist'),
        "rules": {
            "entry": "ST BUY + MACD hist in (0.5, 1.25) + last 3 candles increasing",
            "exit": "ST reversal OR trailing SL OR target",
            "candle_interval_seconds": int(bot_state.get('candle_interval', 5) or 5),
        },
        "indicators": {
            "supertrend_value": bot_state.get('supertrend_value', 0.0),
            "macd_value": bot_state.get('macd_value', 0.0),
            "macd_hist": bot_state.get('macd_hist', 0.0),
        },
        "position": {
            "in_position": bool(bot_state.get('current_position')),
            "current_position_side": bot_state.get('current_position', {}).get('option_type') if bot_state.get('current_position') else None,
        },
    }


def get_config() -> dict:
    """Get current configuration"""
    index_config = get_index_config(config['selected_index'])
    
    return {
        # API Settings
        "has_credentials": bool(config['dhan_access_token'] and config['dhan_client_id']),
        "mode": bot_state['mode'],
        # Index & Timeframe
        "selected_index": config['selected_index'],
        "candle_interval": int(bot_state.get('candle_interval', 5) or 5),
        "lot_size": index_config['lot_size'],
        "strike_interval": index_config['strike_interval'],
        "expiry_type": index_config.get('expiry_type', 'weekly'),
        # Risk Parameters
        "order_qty": config['order_qty'],
        "max_trades_per_day": config['max_trades_per_day'],
        "daily_max_loss": config['daily_max_loss'],
        "max_loss_per_trade": config.get('max_loss_per_trade', 0),
        "initial_stoploss": config.get('initial_stoploss', 50),
        "trail_start_profit": config['trail_start_profit'],
        "trail_step": config['trail_step'],
        "target_points": config['target_points'],
        "risk_per_trade": config.get('risk_per_trade', 0),

        # Market-hours overrides
        "allow_weekend_trading": bool(config.get('allow_weekend_trading', False)),
        # Indicator Settings (SuperTrend only)
        "supertrend_period": config.get('supertrend_period', 7),
        "supertrend_multiplier": config.get('supertrend_multiplier', 4),

        # Strategy / Agent
        "strategy_mode": config.get('strategy_mode', 'agent'),
        "agent_adx_min": config.get('agent_adx_min', 20.0),
        "agent_wave_reset_macd_abs": config.get('agent_wave_reset_macd_abs', 0.05),
        "persist_agent_state": config.get('persist_agent_state', True),

        # Entry window is enforced server-side (09:25–15:10 IST on weekdays)
    }


async def update_config_values(updates: dict) -> dict:
    """Update configuration values"""
    logger.info(f"[CONFIG] Received updates: {list(updates.keys())}")
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
        
    if updates.get('initial_stoploss') is not None:
        config['initial_stoploss'] = float(updates['initial_stoploss'])
        updated_fields.append('initial_stoploss')
        logger.info(f"[CONFIG] Initial stoploss changed to: {config['initial_stoploss']} pts")
        
    if updates.get('max_loss_per_trade') is not None:
        config['max_loss_per_trade'] = float(updates['max_loss_per_trade'])
        updated_fields.append('max_loss_per_trade')
        logger.info(f"[CONFIG] Max loss per trade changed to: ₹{config['max_loss_per_trade']}")
        
    if updates.get('trail_start_profit') is not None:
        config['trail_start_profit'] = float(updates['trail_start_profit'])
        updated_fields.append('trail_start_profit')
        logger.info(f"[CONFIG] Trail start profit changed to: {config['trail_start_profit']} pts")

    if updates.get('allow_weekend_trading') is not None:
        config['allow_weekend_trading'] = bool(updates['allow_weekend_trading'])
        updated_fields.append('allow_weekend_trading')
        logger.warning(f"[CONFIG] allow_weekend_trading set to: {config['allow_weekend_trading']}")
        
    if updates.get('trail_step') is not None:
        config['trail_step'] = float(updates['trail_step'])
        updated_fields.append('trail_step')
        logger.info(f"[CONFIG] Trail step changed to: {config['trail_step']} pts")
    
    if updates.get('target_points') is not None:
        config['target_points'] = float(updates['target_points'])
        updated_fields.append('target_points')
        logger.info(f"[CONFIG] Target points changed to: {config['target_points']}")
        
    if updates.get('risk_per_trade') is not None:
        config['risk_per_trade'] = float(updates['risk_per_trade'])
        updated_fields.append('risk_per_trade')
        logger.info(f"[CONFIG] Risk per trade changed to: ₹{config['risk_per_trade']}")

    # Strategy / Agent
    if updates.get('strategy_mode') is not None:
        requested = updates['strategy_mode']
        normalized = str(requested).strip().lower()
        mode_map = {
            # Agent
            'agent': 'agent',
            'st+adx+macd': 'agent',
            'st_adx_macd': 'agent',
            # SuperTrend flip
            'supertrend': 'supertrend',
            'supertrend_flip': 'supertrend',
            'supertrend flip': 'supertrend',
            'flip': 'supertrend',
            # ST + MACD histogram
            'st_macd_hist': 'st_macd_hist',
            'st+macd_hist': 'st_macd_hist',
            'st + macd hist': 'st_macd_hist',
            'st + macd histogram': 'st_macd_hist',
            'st_macd_histogram': 'st_macd_hist',
        }
        mode = mode_map.get(normalized)
        if mode in ('agent', 'supertrend', 'st_macd_hist'):
            config['strategy_mode'] = mode
            bot_state['strategy_mode'] = mode
            updated_fields.append('strategy_mode')
            logger.info(f"[CONFIG] Strategy mode changed to: {mode} (requested: {requested})")
        else:
            logger.warning(
                f"[CONFIG] Invalid strategy_mode: {requested} (normalized: {normalized}). Allowed: agent|supertrend|st_macd_hist"
            )

    if updates.get('agent_adx_min') is not None:
        val = float(updates['agent_adx_min'])
        config['agent_adx_min'] = max(0.0, min(100.0, val))
        updated_fields.append('agent_adx_min')
        logger.info(f"[CONFIG] Agent ADX min set to: {config['agent_adx_min']}")

    if updates.get('agent_wave_reset_macd_abs') is not None:
        val = float(updates['agent_wave_reset_macd_abs'])
        config['agent_wave_reset_macd_abs'] = max(0.0, min(10.0, val))
        updated_fields.append('agent_wave_reset_macd_abs')
        logger.info(f"[CONFIG] Agent wave reset | abs(MACD) < {config['agent_wave_reset_macd_abs']}")

    if updates.get('persist_agent_state') is not None:
        config['persist_agent_state'] = bool(updates['persist_agent_state'])
        updated_fields.append('persist_agent_state')
        logger.info(f"[CONFIG] Persist agent state: {config['persist_agent_state']}")



    # Apply strategy/agent config live (no restart required)
    if any(k in updates for k in ('strategy_mode', 'agent_adx_min', 'agent_wave_reset_macd_abs', 'persist_agent_state')):
        bot = get_trading_bot()
        bot.apply_strategy_config()
        
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
            # Reset indicator when interval changes
            bot = get_trading_bot()
            bot.reset_indicator()
        else:
            logger.warning(f"[CONFIG] Invalid interval: {new_interval}. Valid: {valid_intervals}")
    
    if updates.get('indicator_type') is not None:
        new_indicator = updates['indicator_type'].lower()
        if new_indicator == 'supertrend_macd':
            config['indicator_type'] = new_indicator
            updated_fields.append('indicator_type')
            logger.info(f"[CONFIG] Indicator changed to: SuperTrend + MACD")
            # Initialize the new indicator
            bot = get_trading_bot()
            bot._initialize_indicator()
        else:
            logger.warning(f"[CONFIG] Invalid indicator: {new_indicator}. Only 'supertrend_macd' is supported")
    
    # Update indicator parameters if provided
    indicator_params = {
        'supertrend_period': int,
        'supertrend_multiplier': float,
        'macd_fast': int,
        'macd_slow': int,
        'macd_signal': int,
    }
    
    for param, param_type in indicator_params.items():
        if updates.get(param) is not None:
            try:
                config[param] = param_type(updates[param])
                updated_fields.append(param)
                logger.info(f"[CONFIG] {param} changed to: {config[param]}")
            except (ValueError, TypeError) as e:
                logger.warning(f"[CONFIG] Invalid value for {param}: {e}")
    
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
