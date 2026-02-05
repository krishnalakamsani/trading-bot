"""Trading Bot Engine
Handles all trading logic, signal processing, and order execution.
Uses structured logging with tags for easy troubleshooting.
"""
import asyncio
import copy
from collections import deque
from datetime import datetime, timezone, timedelta
import logging
import random
import json
import time
from pathlib import Path

from config import bot_state, config, DB_PATH
from indices import get_index_config, round_to_strike
from utils import get_ist_time, is_market_open, can_take_new_trade, should_force_squareoff, format_timeframe
from indicators import SuperTrend, MACD
from dhan_api import DhanAPI
from database import save_trade, update_trade_exit

logger = logging.getLogger(__name__)


class TradingBot:
    """Main trading bot engine"""
    
    def __init__(self):
        self.running = False
        self.task = None
        self.dhan = None
        self.current_position = None
        self.entry_price = 0.0
        self.trailing_sl = None
        self.highest_profit = 0.0

        self.fixed_option_strike = None
        self.fixed_option_expiry = None
        self.fixed_ce_security_id = None
        self.fixed_pe_security_id = None

        # Optional multi-strike option universe (strike band around ATM)
        self.option_universe_center_strike = None
        self.option_universe_expiry = None
        # {strike: {'CE': sid, 'PE': sid}}
        self.option_universe_contracts: dict[int, dict[str, str]] = {}

        # Per-contract indicator + candle state (keyed by "{strike}:{CE|PE}")
        self._opt_trackers: dict[str, dict] = {}
        # Throttle option-chain fallback calls per contract
        self._opt_fallback_last_ts: dict[str, float] = {}

        # Separate option-candle indicators
        self.opt_ce_st = None
        self.opt_ce_macd = None
        self.opt_pe_st = None
        self.opt_pe_macd = None

        self._opt_ce_last_macd_value = None
        self._opt_pe_last_macd_value = None
        self._opt_ce_hist_window = deque(maxlen=3)
        self._opt_pe_hist_window = deque(maxlen=3)
        self._opt_ce_last_st_direction = None
        self._opt_pe_last_st_direction = None

        # Option candle builder state
        self._ce_open = 0.0
        self._ce_high = 0.0
        self._ce_low = float('inf')
        self._ce_close = 0.0

        self._pe_open = 0.0
        self._pe_high = 0.0
        self._pe_low = float('inf')
        self._pe_close = 0.0
        self.last_exit_candle_time = None
        self.last_trade_time = None  # For min_trade_gap protection
        self._last_daily_reset_date = None  # IST date when daily reset last ran
        self.apply_strategy_config()

        # Keep bot_state strategy_mode in sync for API/WS
        bot_state['strategy_mode'] = 'st_macd_hist'
    
    def initialize_dhan(self):
        """Initialize Dhan API connection"""
        if config['dhan_access_token'] and config['dhan_client_id']:
            self.dhan = DhanAPI(config['dhan_access_token'], config['dhan_client_id'])
            logger.info("[MARKET] Dhan API initialized")
            return True
        logger.warning("[ERROR] Dhan API credentials not configured")
        return False
    
    def reset_indicator(self):
        """Reset signal indicators and strategy state."""

        if self.opt_ce_st:
            self.opt_ce_st.reset()
        if self.opt_ce_macd:
            self.opt_ce_macd.reset()
        if self.opt_pe_st:
            self.opt_pe_st.reset()
        if self.opt_pe_macd:
            self.opt_pe_macd.reset()

        self.apply_strategy_config()

        self._opt_ce_last_macd_value = None
        self._opt_pe_last_macd_value = None
        self._opt_ce_hist_window.clear()
        self._opt_pe_hist_window.clear()
        self._opt_ce_last_st_direction = None
        self._opt_pe_last_st_direction = None

        # Clear fixed-contract metadata (will be re-initialized lazily)
        self.fixed_option_strike = None
        self.fixed_option_expiry = None
        self.fixed_ce_security_id = None
        self.fixed_pe_security_id = None
        bot_state['fixed_option_strike'] = None
        bot_state['fixed_option_expiry'] = None
        bot_state['fixed_ce_security_id'] = None
        bot_state['fixed_pe_security_id'] = None

        # Clear option universe
        self.option_universe_center_strike = None
        self.option_universe_expiry = None
        self.option_universe_contracts = {}
        self._opt_trackers = {}
        self._opt_fallback_last_ts = {}
        bot_state['option_universe_enabled'] = False
        bot_state['option_universe_center_strike'] = None
        bot_state['option_universe_expiry'] = None
        bot_state['option_universe_strikes'] = []
        bot_state['option_universe_contracts'] = {}

        # Reset option candle builder
        self._ce_open = 0.0
        self._ce_high = 0.0
        self._ce_low = float('inf')
        self._ce_close = 0.0
        self._pe_open = 0.0
        self._pe_high = 0.0
        self._pe_low = float('inf')
        self._pe_close = 0.0

        logger.info(
            "[SIGNAL] Indicators reset (Strategy: ST+MACD Histogram)"
        )

    def apply_strategy_config(self) -> None:
        """Apply config-driven indicator settings."""
        # Keep bot_state updated for API/WS
        bot_state['strategy_mode'] = 'st_macd_hist'

        # Ensure option signal indicators exist
        if self.opt_ce_st is None or self.opt_pe_st is None:
            self.opt_ce_st = SuperTrend(period=config['supertrend_period'], multiplier=config['supertrend_multiplier'])
            self.opt_ce_macd = MACD(
                fast=int(config.get('macd_fast', 12)),
                slow=int(config.get('macd_slow', 26)),
                signal=int(config.get('macd_signal', 9)),
            )
            self.opt_pe_st = SuperTrend(period=config['supertrend_period'], multiplier=config['supertrend_multiplier'])
            self.opt_pe_macd = MACD(
                fast=int(config.get('macd_fast', 12)),
                slow=int(config.get('macd_slow', 26)),
                signal=int(config.get('macd_signal', 9)),
            )

    def _entry_conditions_met(self, *, st_direction: int | None, hist_window: deque) -> bool:
        # Requirements (entry):
        # 1) SuperTrend BUY on option candle (direction == 1)
        # 2) MACD histogram is > +0.5 and < +1.25
        # 3) MACD histogram is increasing for the last 3 candles
        if st_direction != 1:
            return False
        if len(hist_window) < 3:
            return False

        h1, h2, h3 = list(hist_window)[-3:]
        if not (isinstance(h1, (int, float)) and isinstance(h2, (int, float)) and isinstance(h3, (int, float))):
            return False

        if not (h1 < h2 < h3):
            return False

        # Current histogram bounds
        if h3 <= 0.5 or h3 >= 1.25:
            return False
        return True

    async def _ensure_fixed_option_contract(self, index_name: str, index_ltp: float) -> bool:
        """Pick and cache a fixed CE+PE contract for signal generation."""
        if self.fixed_ce_security_id and self.fixed_pe_security_id and self.fixed_option_strike and self.fixed_option_expiry:
            return True

        if not self.dhan:
            return False

        if not index_ltp or index_ltp <= 0:
            return False

        try:
            strike = round_to_strike(index_ltp, index_name)
            expiry = await self.dhan.get_nearest_expiry(index_name)
            ce_sid = await self.dhan.get_atm_option_security_id(index_name, strike, 'CE', expiry)
            pe_sid = await self.dhan.get_atm_option_security_id(index_name, strike, 'PE', expiry)
            if not ce_sid or not pe_sid:
                return False

            self.fixed_option_strike = int(strike)
            self.fixed_option_expiry = str(expiry)
            self.fixed_ce_security_id = str(ce_sid)
            self.fixed_pe_security_id = str(pe_sid)

            bot_state['fixed_option_strike'] = self.fixed_option_strike
            bot_state['fixed_option_expiry'] = self.fixed_option_expiry
            bot_state['fixed_ce_security_id'] = self.fixed_ce_security_id
            bot_state['fixed_pe_security_id'] = self.fixed_pe_security_id

            logger.info(
                f"[SIGNAL] Fixed option contract set | {index_name} {strike} | Expiry={expiry} | CE={ce_sid} PE={pe_sid}"
            )
            return True
        except Exception as e:
            logger.error(f"[SIGNAL] Failed to set fixed option contract: {e}")
            return False

    def _universe_tracker_key(self, strike: int, option_type: str) -> str:
        return f"{int(strike)}:{str(option_type).upper()}"

    def _build_strike_universe(self, *, center_strike: int, index_name: str, steps: int) -> list[int]:
        """Return a symmetric strike list around center_strike."""
        cfg = get_index_config(index_name)
        interval = int(cfg.get('strike_interval', 50) or 50)
        steps = max(0, int(steps))
        strikes = [int(center_strike + (i * interval)) for i in range(-steps, steps + 1)]
        strikes = sorted(set(strikes))
        return strikes

    def _get_or_create_option_tracker(self, *, strike: int, option_type: str, expiry: str) -> dict:
        """Create per-contract ST+MACD indicator + candle builder state."""
        key = self._universe_tracker_key(strike, option_type)
        tracker = self._opt_trackers.get(key)
        if tracker is None:
            tracker = {
                'strike': int(strike),
                'option_type': str(option_type).upper(),
                'expiry': str(expiry),
                'security_id': None,
                'st': SuperTrend(period=config['supertrend_period'], multiplier=config['supertrend_multiplier']),
                'macd': MACD(
                    fast=int(config.get('macd_fast', 12)),
                    slow=int(config.get('macd_slow', 26)),
                    signal=int(config.get('macd_signal', 9)),
                ),
                'last_macd_value': None,
                'hist_window': deque(maxlen=3),
                'last_st_value': None,
                'last_st_dir': None,
                'last_hist': None,
                'open': 0.0,
                'high': 0.0,
                'low': float('inf'),
                'close': 0.0,
            }
            self._opt_trackers[key] = tracker
        return tracker

    def _reset_tracker_state(self, tracker: dict, *, expiry: str) -> None:
        try:
            tracker['expiry'] = str(expiry)
            if tracker.get('st'):
                tracker['st'].reset()
            if tracker.get('macd'):
                tracker['macd'].reset()
        except Exception:
            pass
        try:
            hw = tracker.get('hist_window')
            if hw is not None:
                hw.clear()
        except Exception:
            pass
        tracker['last_macd_value'] = None
        tracker['last_st_value'] = None
        tracker['last_st_dir'] = None
        tracker['last_hist'] = None
        tracker['open'] = 0.0
        tracker['high'] = 0.0
        tracker['low'] = float('inf')
        tracker['close'] = 0.0

    async def _ensure_option_universe(self, index_name: str, index_ltp: float) -> bool:
        """Ensure CE/PE contracts for strikes around the current ATM strike.

        This keeps bot_state fixed_* fields pointing to the current ATM strike for backward compatibility.
        """
        steps = int(config.get('option_universe_strike_steps', 0) or 0)
        if steps <= 0:
            bot_state['option_universe_enabled'] = False
            return await self._ensure_fixed_option_contract(index_name, index_ltp)

        if not self.dhan:
            return False
        if not index_ltp or index_ltp <= 0:
            return False

        try:
            center_strike = int(round_to_strike(index_ltp, index_name))
            expiry = await self.dhan.get_nearest_expiry(index_name)
            strikes = self._build_strike_universe(center_strike=center_strike, index_name=index_name, steps=steps)

            # Short-circuit if unchanged
            if (
                self.option_universe_center_strike == center_strike
                and str(self.option_universe_expiry or '') == str(expiry)
                and sorted(self.option_universe_contracts.keys()) == strikes
            ):
                bot_state['option_universe_enabled'] = True
                return True

            new_contracts: dict[int, dict[str, str]] = {}
            for s in strikes:
                ce_sid = await self.dhan.get_atm_option_security_id(index_name, int(s), 'CE', str(expiry))
                pe_sid = await self.dhan.get_atm_option_security_id(index_name, int(s), 'PE', str(expiry))
                if ce_sid or pe_sid:
                    new_contracts[int(s)] = {
                        'CE': str(ce_sid) if ce_sid else '',
                        'PE': str(pe_sid) if pe_sid else '',
                    }

            if not new_contracts:
                return False

            # Remove trackers that are no longer in the universe
            expected_keys: set[str] = set()
            for s, d in new_contracts.items():
                for ot in ('CE', 'PE'):
                    if d.get(ot):
                        expected_keys.add(self._universe_tracker_key(s, ot))
            for k in list(self._opt_trackers.keys()):
                if k not in expected_keys:
                    try:
                        del self._opt_trackers[k]
                    except Exception:
                        pass

            # Ensure trackers exist and are bound to the correct security IDs
            for s, d in new_contracts.items():
                for ot in ('CE', 'PE'):
                    sid = d.get(ot) or ''
                    if not sid:
                        continue
                    tracker = self._get_or_create_option_tracker(strike=int(s), option_type=ot, expiry=str(expiry))
                    sid_changed = (str(tracker.get('security_id') or '') != str(sid))
                    expiry_changed = (str(tracker.get('expiry') or '') != str(expiry))
                    tracker['security_id'] = str(sid)
                    if sid_changed or expiry_changed:
                        self._reset_tracker_state(tracker, expiry=str(expiry))

            self.option_universe_center_strike = int(center_strike)
            self.option_universe_expiry = str(expiry)
            self.option_universe_contracts = new_contracts

            # Back-compat fixed_* fields -> ATM center strike
            atm = new_contracts.get(int(center_strike), {})
            self.fixed_option_strike = int(center_strike)
            self.fixed_option_expiry = str(expiry)
            self.fixed_ce_security_id = str(atm.get('CE') or '') or None
            self.fixed_pe_security_id = str(atm.get('PE') or '') or None

            bot_state['fixed_option_strike'] = self.fixed_option_strike
            bot_state['fixed_option_expiry'] = self.fixed_option_expiry
            bot_state['fixed_ce_security_id'] = self.fixed_ce_security_id
            bot_state['fixed_pe_security_id'] = self.fixed_pe_security_id

            bot_state['option_universe_enabled'] = True
            bot_state['option_universe_center_strike'] = int(center_strike)
            bot_state['option_universe_expiry'] = str(expiry)
            bot_state['option_universe_strikes'] = strikes
            # JSON-friendly map
            bot_state['option_universe_contracts'] = {str(k): v for k, v in new_contracts.items()}

            logger.info(
                f"[SIGNAL] Option universe set | {index_name} Center={center_strike} Steps={steps} | Expiry={expiry} | Contracts={len(expected_keys)}"
            )
            return True
        except Exception as e:
            logger.error(f"[SIGNAL] Failed to set option universe: {e}")
            return False

    def _try_load_agent_state(self) -> None:
        # Deprecated: previous agent-based persistence.
        return

    def _try_persist_agent_state(self) -> None:
        # Deprecated: previous agent-based persistence.
        return
    
    def is_within_trading_hours(self) -> bool:
        """Check if current time allows new entries
        
        Returns:
            bool: True if within allowed trading hours, False otherwise
        """
        ist = get_ist_time()

        # Weekday-only entries
        if ist.weekday() >= 5 and not bool(config.get('allow_weekend_trading', False)):
            return False

        current_time = ist.time()
        
        # Define trading hours
        NO_ENTRY_BEFORE = datetime.strptime("09:25", "%H:%M").time()  # No entry before 9:25 AM
        NO_ENTRY_AFTER = datetime.strptime("15:10", "%H:%M").time()   # No entry after 3:10 PM
        
        # Check if within allowed hours
        if current_time < NO_ENTRY_BEFORE:
            logger.info(f"[HOURS] Entry blocked - market not open yet (Current: {current_time.strftime('%H:%M')}, Opens: 09:25)")
            return False
        
        if current_time > NO_ENTRY_AFTER:
            logger.info(f"[HOURS] Entry blocked - market closing soon (Current: {current_time.strftime('%H:%M')}, Cutoff: 15:10)")
            return False
        
        logger.debug(f"[HOURS] Trading hours OK (Current: {current_time.strftime('%H:%M')})")
        return True
    
    async def start(self):
        """Start the trading bot"""
        if self.running:
            return {"status": "error", "message": "Bot already running"}
        
        if not self.initialize_dhan():
            return {"status": "error", "message": "Dhan API credentials not configured"}
        
        self.running = True
        bot_state['is_running'] = True
        self.reset_indicator()
        self.task = asyncio.create_task(self.run_loop())
        
        index_name = config['selected_index']
        interval = format_timeframe(config['candle_interval'])
        indicator_name = config.get('indicator_type', 'supertrend')
        strategy_mode = config.get('strategy_mode', 'agent')
        logger.info(
            f"[BOT] Started - Index: {index_name}, Timeframe: {interval}, Strategy: {strategy_mode}, Indicator: {indicator_name}, Mode: {bot_state['mode']}"
        )
        
        return {"status": "success", "message": f"Bot started for {index_name} ({interval})"}
    
    async def stop(self):
        """Stop the trading bot"""
        self.running = False
        bot_state['is_running'] = False
        if self.task:
            self.task.cancel()
        logger.info("[BOT] Stopped")
        return {"status": "success", "message": "Bot stopped"}
    
    async def squareoff(self):
        """Force square off current position"""
        if not self.current_position:
            return {"status": "error", "message": "No open position"}
        
        index_name = config['selected_index']
        index_config = get_index_config(index_name)
        qty = config['order_qty'] * index_config['lot_size']
        
        logger.info(f"[ORDER] Force squareoff initiated for {index_name}")
        
        if bot_state['mode'] == 'paper':
            exit_price = bot_state['current_option_ltp']
            pnl = (exit_price - self.entry_price) * qty
            await self.close_position(exit_price, pnl, "Force Square-off")
            return {"status": "success", "message": f"Position squared off (Paper). PnL: {pnl:.2f}"}
        else:
            if self.dhan:
                security_id = self.current_position.get('security_id', '')
                result = await self.dhan.place_order(security_id, "SELL", qty)
                logger.info(f"[ORDER] Squareoff result: {result}")
                if result.get('orderId') or result.get('status') == 'success':
                    exit_price = bot_state['current_option_ltp']
                    pnl = (exit_price - self.entry_price) * qty
                    await self.close_position(exit_price, pnl, "Force Square-off")
                    return {"status": "success", "message": f"Position squared off. PnL: {pnl:.2f}"}
        
        return {"status": "error", "message": "Failed to square off"}
    
    async def close_position(self, exit_price: float, pnl: float, reason: str):
        """Close current position and save trade"""
        if not self.current_position:
            return
        
        trade_id = self.current_position.get('trade_id', '')
        index_name = self.current_position.get('index_name', config['selected_index'])
        option_type = self.current_position.get('option_type', '')
        strike = self.current_position.get('strike', 0)
        security_id = self.current_position.get('security_id', '')
        
        # Send exit order to Dhan - MUST place order before updating DB
        exit_order_placed = False
        if bot_state['mode'] != 'paper' and self.dhan and security_id:
            index_config = get_index_config(index_name)
            qty = config['order_qty'] * index_config['lot_size']
            
            try:
                logger.info(f"[ORDER] Placing EXIT SELL order | Trade ID: {trade_id} | Security: {security_id} | Qty: {qty}")
                result = await self.dhan.place_order(security_id, "SELL", qty)
                
                if result.get('status') == 'success' and result.get('orderId'):
                    order_id = result.get('orderId')
                    exit_order_placed = True
                    logger.info(f"[ORDER] ✓ EXIT order PLACED | OrderID: {order_id} | Security: {security_id} | Qty: {qty}")
                    
                    logger.info(f"[EXIT] ✓ Position closed | {index_name} {option_type} {strike} | Reason: {reason} | PnL: {pnl} | Order Placed: True")
                    
                    # Update database in background - don't wait
                    asyncio.create_task(update_trade_exit(
                        trade_id=trade_id,
                        exit_time=datetime.now(timezone.utc).isoformat(),
                        exit_price=exit_price,
                        pnl=pnl,
                        exit_reason=reason
                    ))
                else:
                    logger.error(f"[ORDER] ✗ EXIT order FAILED | Trade: {trade_id} | Result: {result}")
                    return
            except Exception as e:
                logger.error(f"[ORDER] ✗ Error placing EXIT order: {e} | Trade: {trade_id}", exc_info=True)
                return
        elif not security_id:
            logger.warning(f"[WARNING] Cannot send exit order - security_id missing for {index_name} {option_type} | Trade: {trade_id}")
            logger.info(f"[EXIT] ✓ Position closed | {index_name} {option_type} {strike} | Reason: {reason} | PnL: {pnl} | Order Placed: False")
            # Update DB in background - don't wait
            asyncio.create_task(update_trade_exit(
                trade_id=trade_id,
                exit_time=datetime.now(timezone.utc).isoformat(),
                exit_price=exit_price,
                pnl=pnl,
                exit_reason=reason
            ))
        elif bot_state['mode'] == 'paper':
            logger.info(f"[ORDER] Paper mode - EXIT order not placed to Dhan (simulated) | Trade: {trade_id}")
            logger.info(f"[EXIT] ✓ Position closed | {index_name} {option_type} {strike} | Reason: {reason} | PnL: {pnl} | Order Placed: False")
            # Update DB in background - don't wait
            asyncio.create_task(update_trade_exit(
                trade_id=trade_id,
                exit_time=datetime.now(timezone.utc).isoformat(),
                exit_price=exit_price,
                pnl=pnl,
                exit_reason=reason
            ))
        
        # Update state
        bot_state['daily_pnl'] += pnl
        bot_state['current_position'] = None
        bot_state['trailing_sl'] = None
        bot_state['entry_price'] = 0
        
        if bot_state['daily_pnl'] < -config['daily_max_loss']:
            bot_state['daily_max_loss_triggered'] = True
            logger.warning(f"[EXIT] Daily max loss triggered! PnL: {bot_state['daily_pnl']:.2f}")
        
        if pnl < 0 and abs(pnl) > bot_state['max_drawdown']:
            bot_state['max_drawdown'] = abs(pnl)
        
        self.current_position = None
        self.entry_price = 0
        self.trailing_sl = None
        self.highest_profit = 0
        
        logger.info(f"[EXIT] ✓ Position closed | {index_name} {option_type} {strike} | Reason: {reason} | PnL: {pnl:.2f} | Order Placed: {exit_order_placed}")
    
    async def run_loop(self):
        """Main trading loop"""
        logger.info("[BOT] Trading loop started")
        candle_start_time = datetime.now()
        close = 0.0
        candle_number = 0
        
        while self.running:
            try:
                index_name = config['selected_index']
                # Build option candles using the user-selected timeframe.
                candle_interval = int(config.get('candle_interval', 5) or 5)
                bot_state['candle_interval'] = candle_interval
                
                # Check daily reset (9:15 AM IST)
                ist = get_ist_time()
                if ist.hour == 9 and ist.minute == 15 and self._last_daily_reset_date != ist.date():
                    bot_state['daily_trades'] = 0
                    bot_state['daily_pnl'] = 0.0
                    bot_state['daily_max_loss_triggered'] = False
                    bot_state['max_drawdown'] = 0.0
                    self.last_exit_candle_time = None
                    self.last_trade_time = None
                    candle_number = 0
                    self.reset_indicator()
                    self._last_daily_reset_date = ist.date()
                    logger.info("[BOT] Daily reset at 9:15 AM")
                
                # Force square-off at 3:25 PM
                if should_force_squareoff() and self.current_position:
                    logger.info("[EXIT] Auto squareoff at 3:25 PM")
                    await self.squareoff()
                
                # Check if trading is allowed
                market_open = is_market_open()
                # Always keep the bot loop running once started; market_open is still used for UI/status only.
                
                if bot_state['daily_max_loss_triggered']:
                    await asyncio.sleep(5)
                    continue
                
                # Fetch market data
                if self.dhan:
                    # Always keep index_ltp updated (used for UI + contract selection)
                    idx = self.dhan.get_index_ltp(index_name)
                    if idx > 0:
                        bot_state['index_ltp'] = float(idx)

                    steps = int(config.get('option_universe_strike_steps', 0) or 0)
                    fixed_strike = None
                    fixed_expiry = None
                    option_ltps = {}

                    if steps > 0:
                        universe_ready = await self._ensure_option_universe(index_name, float(bot_state.get('index_ltp', 0.0)))
                        if universe_ready:
                            ids: list[int] = []
                            for s, d in (self.option_universe_contracts or {}).items():
                                for ot in ('CE', 'PE'):
                                    sid = d.get(ot) or ''
                                    if not sid:
                                        continue
                                    try:
                                        ids.append(int(sid))
                                    except Exception:
                                        pass

                            # Also include current position sid (improves LTP accuracy for exits)
                            if self.current_position:
                                pos_sid = str(self.current_position.get('security_id', ''))
                                if pos_sid and not pos_sid.startswith('SIM_'):
                                    try:
                                        ids.append(int(pos_sid))
                                    except Exception:
                                        pass

                            if ids:
                                idx2, option_ltps = self.dhan.get_index_and_options_ltp(index_name, ids)
                                if idx2 and idx2 > 0:
                                    bot_state['index_ltp'] = float(idx2)

                            fixed_strike = bot_state.get('fixed_option_strike')
                            fixed_expiry = bot_state.get('fixed_option_expiry')

                            # Keep center (ATM) CE/PE LTPs in the legacy fields for UI
                            if fixed_strike and fixed_expiry:
                                atm_contracts = (self.option_universe_contracts or {}).get(int(fixed_strike), {})
                                for ot, field in (('CE', 'signal_ce_ltp'), ('PE', 'signal_pe_ltp')):
                                    sid = atm_contracts.get(ot) or ''
                                    ltp = 0.0
                                    if sid:
                                        try:
                                            ltp = float(option_ltps.get(int(sid), 0.0) or 0.0)
                                        except Exception:
                                            ltp = 0.0

                                    # Chain fallback for the ATM contracts (throttled)
                                    if (not ltp or ltp <= 0) and sid:
                                        k = self._universe_tracker_key(int(fixed_strike), ot)
                                        now_ts = time.time()
                                        last_ts = float(self._opt_fallback_last_ts.get(k, 0.0) or 0.0)
                                        if now_ts - last_ts >= 2.0:
                                            self._opt_fallback_last_ts[k] = now_ts
                                            try:
                                                ltp = float(
                                                    await self.dhan.get_option_ltp(
                                                        str(sid),
                                                        strike=int(fixed_strike),
                                                        option_type=ot,
                                                        expiry=str(fixed_expiry),
                                                        index_name=index_name,
                                                    )
                                                    or 0.0
                                                )
                                            except Exception:
                                                ltp = 0.0

                                    if ltp and ltp > 0:
                                        ltp = round(float(ltp) / 0.05) * 0.05
                                        bot_state[field] = round(float(ltp), 2)

                            # Keep current position LTP in sync (for SL/target checks)
                            if self.current_position:
                                pos_sid = str(self.current_position.get('security_id', ''))
                                if pos_sid and not pos_sid.startswith('SIM_'):
                                    try:
                                        pos_ltp = float(option_ltps.get(int(pos_sid), 0.0) or 0.0)
                                    except Exception:
                                        pos_ltp = 0.0
                                    if (not pos_ltp or pos_ltp <= 0) and fixed_strike and fixed_expiry:
                                        pos_type = str(self.current_position.get('option_type') or '').upper()
                                        pos_strike = self.current_position.get('strike')
                                        if pos_type in ('CE', 'PE') and pos_strike:
                                            k = self._universe_tracker_key(int(pos_strike), pos_type)
                                            now_ts = time.time()
                                            last_ts = float(self._opt_fallback_last_ts.get(k, 0.0) or 0.0)
                                            if now_ts - last_ts >= 2.0:
                                                self._opt_fallback_last_ts[k] = now_ts
                                                try:
                                                    pos_ltp = float(
                                                        await self.dhan.get_option_ltp(
                                                            str(pos_sid),
                                                            strike=int(pos_strike),
                                                            option_type=pos_type,
                                                            expiry=str(fixed_expiry),
                                                            index_name=index_name,
                                                        )
                                                        or 0.0
                                                    )
                                                except Exception:
                                                    pos_ltp = 0.0
                                    if pos_ltp and pos_ltp > 0:
                                        pos_ltp = round(float(pos_ltp) / 0.05) * 0.05
                                        bot_state['current_option_ltp'] = round(float(pos_ltp), 2)

                            # Feed universe trackers with tick LTPs for candle building
                            if fixed_expiry:
                                for s, d in (self.option_universe_contracts or {}).items():
                                    for ot in ('CE', 'PE'):
                                        sid = d.get(ot) or ''
                                        if not sid:
                                            continue
                                        try:
                                            ltp = float(option_ltps.get(int(sid), 0.0) or 0.0)
                                        except Exception:
                                            ltp = 0.0
                                        if ltp and ltp > 0:
                                            ltp = round(float(ltp) / 0.05) * 0.05
                                            tracker = self._get_or_create_option_tracker(strike=int(s), option_type=ot, expiry=str(fixed_expiry))
                                            if tracker.get('open') == 0.0:
                                                tracker['open'] = float(ltp)
                                            if tracker.get('high') == 0.0 or float(ltp) > float(tracker.get('high') or 0.0):
                                                tracker['high'] = float(ltp)
                                            if float(ltp) < float(tracker.get('low') or float('inf')):
                                                tracker['low'] = float(ltp)
                                            tracker['close'] = float(ltp)

                    else:
                        # Ensure the fixed contract exists (strike/expiry/security IDs)
                        fixed_ready = await self._ensure_fixed_option_contract(index_name, float(bot_state.get('index_ltp', 0.0)))
                        if fixed_ready:
                            ce_sid = bot_state.get('fixed_ce_security_id')
                            pe_sid = bot_state.get('fixed_pe_security_id')

                            ids: list[int] = []
                            if ce_sid:
                                try:
                                    ids.append(int(ce_sid))
                                except Exception:
                                    pass
                            if pe_sid:
                                try:
                                    ids.append(int(pe_sid))
                                except Exception:
                                    pass

                            if ids:
                                idx2, option_ltps = self.dhan.get_index_and_options_ltp(index_name, ids)
                                if idx2 and idx2 > 0:
                                    bot_state['index_ltp'] = float(idx2)

                                fixed_strike = bot_state.get('fixed_option_strike')
                                fixed_expiry = bot_state.get('fixed_option_expiry')

                                if ce_sid:
                                    ce_val = option_ltps.get(int(ce_sid), 0.0)
                                    if (not ce_val or ce_val <= 0) and fixed_strike and fixed_expiry:
                                        try:
                                            ce_val = await self.dhan.get_option_ltp(
                                                ce_sid,
                                                strike=int(fixed_strike),
                                                option_type='CE',
                                                expiry=str(fixed_expiry),
                                                index_name=index_name,
                                            )
                                        except Exception:
                                            pass
                                    if ce_val and ce_val > 0:
                                        ce_val = round(float(ce_val) / 0.05) * 0.05
                                        bot_state['signal_ce_ltp'] = round(float(ce_val), 2)
                                if pe_sid:
                                    pe_val = option_ltps.get(int(pe_sid), 0.0)
                                    if (not pe_val or pe_val <= 0) and fixed_strike and fixed_expiry:
                                        try:
                                            pe_val = await self.dhan.get_option_ltp(
                                                pe_sid,
                                                strike=int(fixed_strike),
                                                option_type='PE',
                                                expiry=str(fixed_expiry),
                                                index_name=index_name,
                                            )
                                        except Exception:
                                            pass
                                    if pe_val and pe_val > 0:
                                        pe_val = round(float(pe_val) / 0.05) * 0.05
                                        bot_state['signal_pe_ltp'] = round(float(pe_val), 2)

                                # Keep current position LTP in sync (for SL/target checks)
                                if self.current_position:
                                    pos_sid = str(self.current_position.get('security_id', ''))
                                    if pos_sid and not pos_sid.startswith('SIM_'):
                                        try:
                                            pos_sid_int = int(pos_sid)
                                            pos_ltp = option_ltps.get(pos_sid_int, 0.0)
                                            if (not pos_ltp or pos_ltp <= 0) and fixed_strike and fixed_expiry:
                                                pos_type = str(self.current_position.get('option_type') or '').upper()
                                                if pos_type in ('CE', 'PE'):
                                                    try:
                                                        pos_ltp = await self.dhan.get_option_ltp(
                                                            pos_sid,
                                                            strike=int(fixed_strike),
                                                            option_type=pos_type,
                                                            expiry=str(fixed_expiry),
                                                            index_name=index_name,
                                                        )
                                                    except Exception:
                                                        pass
                                            if pos_ltp and pos_ltp > 0:
                                                pos_ltp = round(float(pos_ltp) / 0.05) * 0.05
                                                bot_state['current_option_ltp'] = round(float(pos_ltp), 2)
                                        except Exception:
                                            pass

                # If market is closed, broker may return 0 LTPs; loop continues and will resume when data is available.
                
                # Keep a local copy of index LTP (used for contract selection + exit helper)
                close = float(bot_state.get('index_ltp', 0.0) or 0.0)

                # Build option candles (single-ATM or multi-universe)
                steps = int(config.get('option_universe_strike_steps', 0) or 0)
                if steps <= 0:
                    ce_ltp = float(bot_state.get('signal_ce_ltp', 0.0) or 0.0)
                    pe_ltp = float(bot_state.get('signal_pe_ltp', 0.0) or 0.0)

                    if ce_ltp > 0:
                        if self._ce_open == 0.0:
                            self._ce_open = float(ce_ltp)
                        if self._ce_high == 0.0 or ce_ltp > self._ce_high:
                            self._ce_high = float(ce_ltp)
                        if ce_ltp < self._ce_low:
                            self._ce_low = float(ce_ltp)
                        self._ce_close = float(ce_ltp)

                    if pe_ltp > 0:
                        if self._pe_open == 0.0:
                            self._pe_open = float(pe_ltp)
                        if self._pe_high == 0.0 or pe_ltp > self._pe_high:
                            self._pe_high = float(pe_ltp)
                        if pe_ltp < self._pe_low:
                            self._pe_low = float(pe_ltp)
                        self._pe_close = float(pe_ltp)
                
                # Check SL/Target on EVERY TICK (responsive protection)
                if self.current_position and bot_state['current_option_ltp'] > 0:
                    option_ltp = bot_state['current_option_ltp']
                    tick_exit = await self.check_tick_sl(option_ltp)
                    if tick_exit:
                        # Position exited on tick, reset candle for next entry
                        candle_start_time = datetime.now()
                        close = 0.0
                        candle_number = 0
                        await asyncio.sleep(1)
                        continue
                
                # Check if candle is complete
                elapsed = (datetime.now() - candle_start_time).total_seconds()
                if elapsed >= candle_interval:
                    current_candle_time = datetime.now()
                    candle_number += 1
                    steps = int(config.get('option_universe_strike_steps', 0) or 0)
                    if steps > 0:
                        # -------- Multi-contract candle close --------
                        if self.current_position:
                            option_ltp = bot_state['current_option_ltp']
                            sl_hit = await self.check_trailing_sl_on_close(option_ltp)
                            if sl_hit:
                                self.last_exit_candle_time = current_candle_time

                        # Compute indicators for all trackers with a ready candle
                        eligible_ce: list[tuple[int, dict]] = []
                        eligible_pe: list[tuple[int, dict]] = []

                        for k, tracker in list(self._opt_trackers.items()):
                            ready = float(tracker.get('high') or 0.0) > 0 and float(tracker.get('low') or float('inf')) < float('inf') and float(tracker.get('close') or 0.0) > 0
                            if not ready:
                                continue

                            try:
                                st_value, _ = tracker['st'].add_candle(float(tracker['high']), float(tracker['low']), float(tracker['close']))
                            except Exception:
                                st_value = None
                            try:
                                st_dir = getattr(tracker['st'], 'direction', None)
                            except Exception:
                                st_dir = None

                            try:
                                macd_val, _ = tracker['macd'].add_candle(float(tracker['high']), float(tracker['low']), float(tracker['close']))
                            except Exception:
                                macd_val = None
                            if isinstance(macd_val, (int, float)):
                                tracker['last_macd_value'] = float(macd_val)
                            hist = getattr(tracker['macd'], 'last_histogram', None)
                            if isinstance(hist, (int, float)):
                                tracker['hist_window'].append(float(hist))

                            tracker['last_st_value'] = float(st_value) if isinstance(st_value, (int, float)) else None
                            tracker['last_st_dir'] = st_dir if st_dir in (1, -1) else None
                            tracker['last_hist'] = float(hist) if isinstance(hist, (int, float)) else None

                            # Backward-compatible UI fields (prefer center strike)
                            center = bot_state.get('option_universe_center_strike')
                            if center and int(tracker.get('strike') or 0) == int(center):
                                if tracker.get('option_type') == 'CE':
                                    bot_state['signal_ce_supertrend_value'] = float(tracker['last_st_value'] or 0.0)
                                    bot_state['signal_ce_supertrend_signal'] = 'GREEN' if tracker['last_st_dir'] == 1 else ('RED' if tracker['last_st_dir'] == -1 else bot_state.get('signal_ce_supertrend_signal'))
                                    bot_state['signal_ce_macd_value'] = float(tracker['last_macd_value'] or 0.0) if isinstance(tracker.get('last_macd_value'), (int, float)) else bot_state.get('signal_ce_macd_value', 0.0)
                                    bot_state['signal_ce_macd_hist'] = float(tracker['last_hist'] or 0.0) if isinstance(tracker.get('last_hist'), (int, float)) else bot_state.get('signal_ce_macd_hist', 0.0)
                                else:
                                    bot_state['signal_pe_supertrend_value'] = float(tracker['last_st_value'] or 0.0)
                                    bot_state['signal_pe_supertrend_signal'] = 'GREEN' if tracker['last_st_dir'] == 1 else ('RED' if tracker['last_st_dir'] == -1 else bot_state.get('signal_pe_supertrend_signal'))
                                    bot_state['signal_pe_macd_value'] = float(tracker['last_macd_value'] or 0.0) if isinstance(tracker.get('last_macd_value'), (int, float)) else bot_state.get('signal_pe_macd_value', 0.0)
                                    bot_state['signal_pe_macd_hist'] = float(tracker['last_hist'] or 0.0) if isinstance(tracker.get('last_hist'), (int, float)) else bot_state.get('signal_pe_macd_hist', 0.0)

                            # Entry eligibility
                            ok = self._entry_conditions_met(
                                st_direction=tracker['last_st_dir'],
                                hist_window=tracker['hist_window'],
                            )
                            if ok:
                                st = int(tracker.get('strike') or 0)
                                if tracker.get('option_type') == 'CE':
                                    eligible_ce.append((st, tracker))
                                else:
                                    eligible_pe.append((st, tracker))

                        # Update global "latest" fields (prefer held contract side)
                        active_side = (self.current_position or {}).get('option_type')
                        active_strike = (self.current_position or {}).get('strike')
                        active_tracker = None
                        if active_side and active_strike:
                            active_tracker = self._opt_trackers.get(self._universe_tracker_key(int(active_strike), str(active_side)))
                        if active_tracker:
                            bot_state['supertrend_value'] = float(active_tracker.get('last_st_value') or 0.0)
                            bot_state['last_supertrend_signal'] = 'GREEN' if active_tracker.get('last_st_dir') == 1 else ('RED' if active_tracker.get('last_st_dir') == -1 else bot_state.get('last_supertrend_signal'))
                            bot_state['macd_value'] = float(active_tracker.get('last_macd_value') or 0.0) if isinstance(active_tracker.get('last_macd_value'), (int, float)) else bot_state.get('macd_value', 0.0)
                            bot_state['macd_hist'] = float(active_tracker.get('last_hist') or 0.0) if isinstance(active_tracker.get('last_hist'), (int, float)) else bot_state.get('macd_hist', 0.0)

                        # EXIT: evaluate on held contract only
                        exit_reason = None
                        if self.current_position and active_tracker:
                            st_dir = active_tracker.get('last_st_dir')
                            st_value = active_tracker.get('last_st_value')
                            if st_dir == -1:
                                exit_reason = 'SuperTrend Reversal'
                            if exit_reason is None and isinstance(st_value, (int, float)):
                                if self.trailing_sl is None:
                                    self.trailing_sl = float(st_value)
                                else:
                                    self.trailing_sl = max(float(self.trailing_sl), float(st_value))
                                current_ltp = float(bot_state.get('current_option_ltp') or 0.0)
                                self._apply_profit_lock_and_step_trailing(current_ltp)
                                bot_state['trailing_sl'] = self.trailing_sl

                        if exit_reason is not None and self.current_position:
                            index_cfg = get_index_config(config['selected_index'])
                            qty = config['order_qty'] * index_cfg['lot_size']
                            exit_price = float(bot_state.get('current_option_ltp') or 0.0)
                            pnl = (exit_price - self.entry_price) * qty
                            logger.info(f"[EXIT] {exit_reason} | LTP={exit_price:.2f} | P&L=₹{pnl:.2f}")
                            await self.close_position(exit_price, pnl, exit_reason)
                            self.last_exit_candle_time = current_candle_time

                        # ENTRY: choose eligible contract nearest to current ATM
                        if can_take_new_trade() and bot_state['daily_trades'] < config['max_trades_per_day']:
                            index_ltp = float(bot_state.get('index_ltp') or 0.0)
                            center = int(round_to_strike(index_ltp, index_name)) if index_ltp > 0 else int(bot_state.get('option_universe_center_strike') or 0)

                            def _pick_nearest(cands: list[tuple[int, dict]]) -> tuple[int | None, dict | None]:
                                if not cands:
                                    return None, None
                                cands = sorted(cands, key=lambda x: (abs(int(x[0]) - int(center)), int(x[0])))
                                return int(cands[0][0]), cands[0][1]

                            chosen_type = None
                            chosen_strike = None
                            chosen_tracker = None
                            cs, ct = _pick_nearest(eligible_ce)
                            if cs is not None and ct is not None:
                                chosen_type = 'CE'
                                chosen_strike = int(cs)
                                chosen_tracker = ct
                            else:
                                ps, pt = _pick_nearest(eligible_pe)
                                if ps is not None and pt is not None:
                                    chosen_type = 'PE'
                                    chosen_strike = int(ps)
                                    chosen_tracker = pt

                            if chosen_type and chosen_strike and chosen_tracker and chosen_tracker.get('security_id'):
                                # If a trade is active on the opposite side, close it first.
                                if self.current_position and (self.current_position.get('option_type') != chosen_type):
                                    index_cfg = get_index_config(config['selected_index'])
                                    qty = config['order_qty'] * index_cfg['lot_size']
                                    exit_price = float(bot_state.get('current_option_ltp') or 0.0)
                                    if exit_price > 0:
                                        pnl = (exit_price - self.entry_price) * qty
                                        logger.info(
                                            f"[EXIT] Reverse Entry | Closing {self.current_position.get('option_type')} before entering {chosen_type} | "
                                            f"LTP={exit_price:.2f} | P&L=₹{pnl:.2f}"
                                        )
                                        await self.close_position(exit_price, pnl, "Reverse Entry")

                                if not self.current_position:
                                    expiry = str(bot_state.get('option_universe_expiry') or bot_state.get('fixed_option_expiry') or '')
                                    logger.info(
                                        f"[ENTRY] {chosen_type} | {index_name} Strike {chosen_strike} (Center={center}) | Expiry={expiry} | "
                                        f"Hist={chosen_tracker.get('last_hist')} STDir={chosen_tracker.get('last_st_dir')}"
                                    )
                                    await self.enter_position(
                                        str(chosen_type),
                                        int(chosen_strike),
                                        float(index_ltp),
                                        expiry_override=expiry if expiry else None,
                                        security_id_override=str(chosen_tracker.get('security_id')),
                                    )

                                    if isinstance(chosen_tracker.get('last_st_value'), (int, float)):
                                        self.trailing_sl = float(chosen_tracker.get('last_st_value'))
                                        bot_state['trailing_sl'] = self.trailing_sl
                                    self.last_trade_time = datetime.now()

                        # Reset all tracker candle builders for next period
                        for tracker in self._opt_trackers.values():
                            tracker['open'] = 0.0
                            tracker['high'] = 0.0
                            tracker['low'] = float('inf')
                            tracker['close'] = 0.0

                    else:
                        # -------- Single-contract candle close (existing behavior) --------
                        ce_ready = self._ce_high > 0 and self._ce_low < float('inf') and self._ce_close > 0
                        pe_ready = self._pe_high > 0 and self._pe_low < float('inf') and self._pe_close > 0
                        if ce_ready or pe_ready:
                        # Check trailing SL/Target on candle close (additional safety)
                        if self.current_position:
                            option_ltp = bot_state['current_option_ltp']
                            sl_hit = await self.check_trailing_sl_on_close(option_ltp)
                            if sl_hit:
                                self.last_exit_candle_time = current_candle_time

                        # Fixed-contract option-candle signals: ST + MACD Histogram
                        self.apply_strategy_config()

                        strike = bot_state.get('fixed_option_strike')
                        expiry = bot_state.get('fixed_option_expiry')
                        ce_sid = bot_state.get('fixed_ce_security_id')
                        pe_sid = bot_state.get('fixed_pe_security_id')

                        # Compute CE indicators (if candle ready)
                        ce_st_value = None
                        ce_st_dir = None
                        ce_hist = None
                        if ce_ready:
                            ce_st_value, _ = self.opt_ce_st.add_candle(self._ce_high, self._ce_low, self._ce_close)
                            ce_st_dir = getattr(self.opt_ce_st, 'direction', None)
                            ce_macd, _ = self.opt_ce_macd.add_candle(self._ce_high, self._ce_low, self._ce_close)
                            if ce_macd is not None:
                                self._opt_ce_last_macd_value = ce_macd
                            ce_hist = getattr(self.opt_ce_macd, 'last_histogram', None)

                            if isinstance(ce_hist, (int, float)):
                                self._opt_ce_hist_window.append(float(ce_hist))

                            # Publish per-option indicator values for UI/debug
                            bot_state['signal_ce_supertrend_value'] = float(ce_st_value) if isinstance(ce_st_value, (int, float)) else bot_state.get('signal_ce_supertrend_value', 0.0)
                            bot_state['signal_ce_supertrend_signal'] = 'GREEN' if ce_st_dir == 1 else ('RED' if ce_st_dir == -1 else bot_state.get('signal_ce_supertrend_signal'))
                            bot_state['signal_ce_macd_value'] = float(ce_macd) if isinstance(ce_macd, (int, float)) else bot_state.get('signal_ce_macd_value', 0.0)
                            bot_state['signal_ce_macd_hist'] = float(ce_hist) if isinstance(ce_hist, (int, float)) else bot_state.get('signal_ce_macd_hist', 0.0)

                        # Compute PE indicators (if candle ready)
                        pe_st_value = None
                        pe_st_dir = None
                        pe_hist = None
                        if pe_ready:
                            pe_st_value, _ = self.opt_pe_st.add_candle(self._pe_high, self._pe_low, self._pe_close)
                            pe_st_dir = getattr(self.opt_pe_st, 'direction', None)
                            pe_macd, _ = self.opt_pe_macd.add_candle(self._pe_high, self._pe_low, self._pe_close)
                            if pe_macd is not None:
                                self._opt_pe_last_macd_value = pe_macd
                            pe_hist = getattr(self.opt_pe_macd, 'last_histogram', None)

                            if isinstance(pe_hist, (int, float)):
                                self._opt_pe_hist_window.append(float(pe_hist))

                            bot_state['signal_pe_supertrend_value'] = float(pe_st_value) if isinstance(pe_st_value, (int, float)) else bot_state.get('signal_pe_supertrend_value', 0.0)
                            bot_state['signal_pe_supertrend_signal'] = 'GREEN' if pe_st_dir == 1 else ('RED' if pe_st_dir == -1 else bot_state.get('signal_pe_supertrend_signal'))
                            bot_state['signal_pe_macd_value'] = float(pe_macd) if isinstance(pe_macd, (int, float)) else bot_state.get('signal_pe_macd_value', 0.0)
                            bot_state['signal_pe_macd_hist'] = float(pe_hist) if isinstance(pe_hist, (int, float)) else bot_state.get('signal_pe_macd_hist', 0.0)

                        # Update global "latest" indicator values for UI/debug (prefer active position side)
                        active_side = (self.current_position or {}).get('option_type')
                        if active_side == 'PE':
                            bot_state['supertrend_value'] = float(pe_st_value) if isinstance(pe_st_value, (int, float)) else bot_state.get('supertrend_value', 0.0)
                            bot_state['last_supertrend_signal'] = 'GREEN' if pe_st_dir == 1 else ('RED' if pe_st_dir == -1 else bot_state.get('last_supertrend_signal'))
                            bot_state['macd_value'] = float(self._opt_pe_last_macd_value) if isinstance(self._opt_pe_last_macd_value, (int, float)) else bot_state.get('macd_value', 0.0)
                            bot_state['macd_hist'] = float(pe_hist) if isinstance(pe_hist, (int, float)) else bot_state.get('macd_hist', 0.0)
                        else:
                            bot_state['supertrend_value'] = float(ce_st_value) if isinstance(ce_st_value, (int, float)) else bot_state.get('supertrend_value', 0.0)
                            bot_state['last_supertrend_signal'] = 'GREEN' if ce_st_dir == 1 else ('RED' if ce_st_dir == -1 else bot_state.get('last_supertrend_signal'))
                            bot_state['macd_value'] = float(self._opt_ce_last_macd_value) if isinstance(self._opt_ce_last_macd_value, (int, float)) else bot_state.get('macd_value', 0.0)
                            bot_state['macd_hist'] = float(ce_hist) if isinstance(ce_hist, (int, float)) else bot_state.get('macd_hist', 0.0)

                        # EXIT conditions (only for the held contract)
                        exit_reason = None
                        if self.current_position and self.current_position.get('option_type') == 'CE':
                            # 1) SuperTrend reverses (BUY->SELL)
                            if ce_st_dir == -1:
                                exit_reason = 'SuperTrend Reversal'
                            # Trail stop to SuperTrend value (initial + trailing)
                            if exit_reason is None and isinstance(ce_st_value, (int, float)):
                                if self.trailing_sl is None:
                                    self.trailing_sl = float(ce_st_value)
                                else:
                                    self.trailing_sl = max(float(self.trailing_sl), float(ce_st_value))

                                # Apply profit lock + optional step trailing (never reduces SL)
                                current_ltp = float(bot_state.get('current_option_ltp') or 0.0)
                                self._apply_profit_lock_and_step_trailing(current_ltp)
                                bot_state['trailing_sl'] = self.trailing_sl

                        elif self.current_position and self.current_position.get('option_type') == 'PE':
                            if pe_st_dir == -1:
                                exit_reason = 'SuperTrend Reversal'
                            if exit_reason is None and isinstance(pe_st_value, (int, float)):
                                if self.trailing_sl is None:
                                    self.trailing_sl = float(pe_st_value)
                                else:
                                    self.trailing_sl = max(float(self.trailing_sl), float(pe_st_value))

                                current_ltp = float(bot_state.get('current_option_ltp') or 0.0)
                                self._apply_profit_lock_and_step_trailing(current_ltp)
                                bot_state['trailing_sl'] = self.trailing_sl

                        if exit_reason is not None and self.current_position:
                            index_cfg = get_index_config(config['selected_index'])
                            qty = config['order_qty'] * index_cfg['lot_size']
                            exit_price = float(bot_state.get('current_option_ltp') or 0.0)
                            pnl = (exit_price - self.entry_price) * qty
                            logger.info(f"[EXIT] {exit_reason} | LTP={exit_price:.2f} | P&L=₹{pnl:.2f}")
                            await self.close_position(exit_price, pnl, exit_reason)
                            self.last_exit_candle_time = current_candle_time

                        # ENTRY conditions (reverse allowed; still single-position bot)
                        if strike and expiry:
                            ce_ok = False
                            pe_ok = False

                            if ce_ready and ce_sid:
                                ce_ok = self._entry_conditions_met(
                                    st_direction=ce_st_dir if ce_st_dir in (1, -1) else None,
                                    hist_window=self._opt_ce_hist_window,
                                )
                            if pe_ready and pe_sid:
                                pe_ok = self._entry_conditions_met(
                                    st_direction=pe_st_dir if pe_st_dir in (1, -1) else None,
                                    hist_window=self._opt_pe_hist_window,
                                )

                            chosen = None
                            chosen_sid = None
                            chosen_st_value = None
                            if ce_ok:
                                chosen = 'CE'
                                chosen_sid = str(ce_sid)
                                chosen_st_value = ce_st_value
                            elif pe_ok:
                                chosen = 'PE'
                                chosen_sid = str(pe_sid)
                                chosen_st_value = pe_st_value

                            if chosen and chosen_sid:
                                if can_take_new_trade() and bot_state['daily_trades'] < config['max_trades_per_day']:
                                    index_ltp = float(bot_state.get('index_ltp') or 0.0)
                                    # If a trade is active on the opposite side, close it first.
                                    if self.current_position and (self.current_position.get('option_type') != chosen):
                                        index_cfg = get_index_config(config['selected_index'])
                                        qty = config['order_qty'] * index_cfg['lot_size']
                                        exit_price = float(bot_state.get('current_option_ltp') or 0.0)
                                        if exit_price > 0:
                                            pnl = (exit_price - self.entry_price) * qty
                                            logger.info(
                                                f"[EXIT] Reverse Entry | Closing {self.current_position.get('option_type')} before entering {chosen} | "
                                                f"LTP={exit_price:.2f} | P&L=₹{pnl:.2f}"
                                            )
                                            await self.close_position(exit_price, pnl, "Reverse Entry")

                                    # Enter only if flat (close succeeded or we were flat already)
                                    if not self.current_position:
                                        logger.info(
                                            f"[ENTRY] {chosen} | {index_name} ATM {strike} | Expiry={expiry} | "
                                            f"CE Hist={ce_hist} STDir={ce_st_dir} | PE Hist={pe_hist} STDir={pe_st_dir}"
                                        )
                                        await self.enter_position(
                                            chosen,
                                            int(strike),
                                            index_ltp,
                                            expiry_override=str(expiry),
                                            security_id_override=str(chosen_sid),
                                        )

                                        # Initial SL = SuperTrend value
                                        if isinstance(chosen_st_value, (int, float)):
                                            self.trailing_sl = float(chosen_st_value)
                                            bot_state['trailing_sl'] = self.trailing_sl
                                        self.last_trade_time = datetime.now()

                        # Prevent immediate re-trade within same candle after exit
                        can_trade = True
                        if self.last_exit_candle_time:
                            time_since_exit = (current_candle_time - self.last_exit_candle_time).total_seconds()
                            if time_since_exit < candle_interval:
                                can_trade = False
                    
                    # Reset candle for next period
                    candle_start_time = datetime.now()
                    close = 0.0

                    # Reset single-contract option candle builders
                    self._ce_open = 0.0
                    self._ce_high = 0.0
                    self._ce_low = float('inf')
                    self._ce_close = 0.0
                    self._pe_open = 0.0
                    self._pe_high = 0.0
                    self._pe_low = float('inf')
                    self._pe_close = 0.0
                
                # Handle paper mode simulation
                if self.current_position:
                    security_id = self.current_position.get('security_id', '')
                    
                    if security_id.startswith('SIM_'):
                        strike = self.current_position.get('strike', 0)
                        option_type = self.current_position.get('option_type', '')
                        index_ltp = bot_state['index_ltp']
                        
                        if strike and index_ltp:
                            distance_from_atm = abs(index_ltp - strike)
                            
                            if option_type == 'CE':
                                intrinsic = max(0, index_ltp - strike)
                            else:
                                intrinsic = max(0, strike - index_ltp)
                            
                            atm_time_value = 150
                            time_decay_factor = max(0, 1 - (distance_from_atm / 500))
                            time_value = atm_time_value * time_decay_factor
                            
                            simulated_ltp = intrinsic + time_value
                            tick_movement = random.choice([-0.10, -0.05, 0, 0.05, 0.10])
                            simulated_ltp += tick_movement
                            
                            simulated_ltp = round(simulated_ltp / 0.05) * 0.05
                            simulated_ltp = max(0.05, round(simulated_ltp, 2))
                            
                            bot_state['current_option_ltp'] = simulated_ltp

                # Live indicator preview (updates every loop using the forming candle)
                self._update_live_indicator_preview()
                
                # Broadcast state update
                await self.broadcast_state()
                
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ERROR] Trading loop exception: {e}")
                await asyncio.sleep(5)
    
    async def broadcast_state(self):
        """Broadcast current state to WebSocket clients"""
        from server import manager
        
        await manager.broadcast({
            "type": "state_update",
            "data": {
                "index_ltp": bot_state['index_ltp'],
                "supertrend_signal": bot_state['last_supertrend_signal'],
                "supertrend_value": bot_state['supertrend_value'],
                "macd_value": bot_state.get('macd_value', 0.0),
                "macd_hist": bot_state.get('macd_hist', 0.0),
                "position": bot_state['current_position'],
                "entry_price": bot_state['entry_price'],
                "current_option_ltp": bot_state['current_option_ltp'],
                "trailing_sl": bot_state['trailing_sl'],
                "daily_pnl": bot_state['daily_pnl'],
                "daily_trades": bot_state['daily_trades'],
                "is_running": bot_state['is_running'],
                "mode": bot_state['mode'],
                "selected_index": config['selected_index'],
                "candle_interval": bot_state.get('candle_interval', 5),
                "strategy_mode": bot_state.get('strategy_mode', config.get('strategy_mode', 'agent')),
                "fixed_option_strike": bot_state.get('fixed_option_strike'),
                "fixed_option_expiry": bot_state.get('fixed_option_expiry'),
                "fixed_ce_security_id": bot_state.get('fixed_ce_security_id'),
                "fixed_pe_security_id": bot_state.get('fixed_pe_security_id'),
                "signal_ce_ltp": bot_state.get('signal_ce_ltp', 0.0),
                "signal_pe_ltp": bot_state.get('signal_pe_ltp', 0.0),
                "signal_ce_supertrend_signal": bot_state.get('signal_ce_supertrend_signal'),
                "signal_pe_supertrend_signal": bot_state.get('signal_pe_supertrend_signal'),
                "signal_ce_supertrend_value": bot_state.get('signal_ce_supertrend_value', 0.0),
                "signal_pe_supertrend_value": bot_state.get('signal_pe_supertrend_value', 0.0),
                "signal_ce_macd_value": bot_state.get('signal_ce_macd_value', 0.0),
                "signal_pe_macd_value": bot_state.get('signal_pe_macd_value', 0.0),
                "signal_ce_macd_hist": bot_state.get('signal_ce_macd_hist', 0.0),
                "signal_pe_macd_hist": bot_state.get('signal_pe_macd_hist', 0.0),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        })
    
    async def check_trailing_sl(self, current_ltp: float):
        """Update SL values - initial fixed SL then trails profit using step-based method"""
        if not self.current_position:
            return

        # In ST+MACD histogram mode, trailing SL is primarily SuperTrend-driven on candle-close.
        # However, we also support profit-lock + step trailing to progressively lock more profit.
        if bot_state.get('strategy_mode') == 'st_macd_hist':
            # Fallback safety: if trailing_sl wasn't initialized for some reason, use configured initial_stoploss.
            if self.trailing_sl is None:
                initial_sl = config.get('initial_stoploss', 0)
                if initial_sl > 0:
                    self.trailing_sl = self.entry_price - initial_sl
                    bot_state['trailing_sl'] = self.trailing_sl
            return

        # Always set initial fixed stoploss if enabled, even if trailing is disabled.
        initial_sl = config.get('initial_stoploss', 0)
        if initial_sl > 0 and self.trailing_sl is None:
            self.trailing_sl = self.entry_price - initial_sl
            bot_state['trailing_sl'] = self.trailing_sl
            logger.info(f"[SL] Initial SL set: {self.trailing_sl:.2f} ({initial_sl} pts below entry)")
        
        # Check if trailing is completely disabled
        trail_start = config.get('trail_start_profit', 0)
        trail_step = config.get('trail_step', 0)
        
        if trail_start == 0 or trail_step == 0:
            # Trailing disabled - initial SL may still be active
            return
        
        profit_points = current_ltp - self.entry_price
        
        # Track highest profit reached
        if profit_points > self.highest_profit:
            self.highest_profit = profit_points
        
        # Start trailing SL after reaching trail_start_profit
        
        # Only start trailing after profit reaches trail_start_profit
        if profit_points < trail_start:
            return
        
        # Calculate trailing SL level: Entry + (steps × trail_step)
        # Steps = (highest_profit - trail_start) / trail_step
        trail_levels = int((self.highest_profit - trail_start) / trail_step)
        new_sl = self.entry_price + (trail_levels * trail_step)
        
        # Always move SL up, never down (protect profit)
        if self.trailing_sl is None or new_sl > self.trailing_sl:
            old_sl = self.trailing_sl
            self.trailing_sl = new_sl
            bot_state['trailing_sl'] = self.trailing_sl
            
            if old_sl and old_sl > (self.entry_price - initial_sl):
                # This is a trailing update (not initial trigger)
                logger.info(f"[SL] Trailing SL updated: {old_sl:.2f} → {new_sl:.2f} (Profit: {profit_points:.2f} pts)")
            else:
                # This is the first trailing activation
                logger.info(f"[SL] Trailing started: {new_sl:.2f} (Profit: {profit_points:.2f} pts)")

    def _apply_profit_lock_and_step_trailing(self, current_ltp: float) -> None:
        """Apply profit lock (trail_start_profit) and optional step trailing (trail_step).

        Designed for ST+MACD mode so that:
        - Profit is locked once price moves favorably by `trail_start_profit` points.
        - Thereafter, SL can be raised in steps without fighting SuperTrend (we only ever raise SL).

                Behavior when `trail_step` > 0:
                - SL starts at entry+lock.
                - Then SL “steps up” to the current stepped profit level (no buffer).
        """
        if not self.current_position:
            return
        if self.entry_price <= 0:
            return

        try:
            current_ltp = float(current_ltp or 0.0)
        except Exception:
            return

        if current_ltp <= 0:
            return

        lock_points = float(config.get('trail_start_profit', 0) or 0)
        if lock_points <= 0:
            return

        profit_points = current_ltp - float(self.entry_price)
        if profit_points < lock_points:
            return

        # Track highest profit reached (used for step-based trailing)
        if profit_points > float(self.highest_profit or 0.0):
            self.highest_profit = float(profit_points)

        lock_sl = float(self.entry_price) + float(lock_points)
        new_sl = lock_sl

        trail_step = float(config.get('trail_step', 0) or 0)
        if trail_step > 0:
            # Step logic: after lock activates, raise SL to match the stepped profit level.
            # Example: lock=10, step=5
            # profit 10..14 => SL = entry+10
            # profit 15..19 => SL = entry+15
            # profit 20..24 => SL = entry+20
            levels = int((float(self.highest_profit) - float(lock_points)) / float(trail_step))
            step_sl = float(self.entry_price) + float(lock_points) + (max(0, levels) * float(trail_step))
            new_sl = max(new_sl, step_sl)

        if self.trailing_sl is None:
            self.trailing_sl = float(new_sl)
        else:
            self.trailing_sl = max(float(self.trailing_sl), float(new_sl))

        bot_state['trailing_sl'] = self.trailing_sl

    def _update_live_indicator_preview(self) -> None:
        """Update UI-facing indicator values every loop using the *current forming* option candle.

        This computes a non-mutating preview (clone + add_candle) so indicators update every second
        without double-counting candles or impacting trading decisions (which remain candle-close).
        """
        if bot_state.get('strategy_mode') != 'st_macd_hist':
            return

        # Ensure indicators exist
        self.apply_strategy_config()

        # CE preview
        if self._ce_high > 0 and self._ce_low < float('inf') and self._ce_close > 0:
            try:
                st = copy.deepcopy(self.opt_ce_st)
                macd = copy.deepcopy(self.opt_ce_macd)
                st_value, _ = st.add_candle(float(self._ce_high), float(self._ce_low), float(self._ce_close))
                st_dir = getattr(st, 'direction', None)
                macd_value, _ = macd.add_candle(float(self._ce_high), float(self._ce_low), float(self._ce_close))
                hist = getattr(macd, 'last_histogram', None)

                if isinstance(st_value, (int, float)):
                    bot_state['signal_ce_supertrend_value'] = float(st_value)
                if st_dir in (1, -1):
                    bot_state['signal_ce_supertrend_signal'] = 'GREEN' if st_dir == 1 else 'RED'
                if isinstance(macd_value, (int, float)):
                    bot_state['signal_ce_macd_value'] = float(macd_value)
                if isinstance(hist, (int, float)):
                    bot_state['signal_ce_macd_hist'] = float(hist)
            except Exception:
                pass

        # PE preview
        if self._pe_high > 0 and self._pe_low < float('inf') and self._pe_close > 0:
            try:
                st = copy.deepcopy(self.opt_pe_st)
                macd = copy.deepcopy(self.opt_pe_macd)
                st_value, _ = st.add_candle(float(self._pe_high), float(self._pe_low), float(self._pe_close))
                st_dir = getattr(st, 'direction', None)
                macd_value, _ = macd.add_candle(float(self._pe_high), float(self._pe_low), float(self._pe_close))
                hist = getattr(macd, 'last_histogram', None)

                if isinstance(st_value, (int, float)):
                    bot_state['signal_pe_supertrend_value'] = float(st_value)
                if st_dir in (1, -1):
                    bot_state['signal_pe_supertrend_signal'] = 'GREEN' if st_dir == 1 else 'RED'
                if isinstance(macd_value, (int, float)):
                    bot_state['signal_pe_macd_value'] = float(macd_value)
                if isinstance(hist, (int, float)):
                    bot_state['signal_pe_macd_hist'] = float(hist)
            except Exception:
                pass

    
    async def check_trailing_sl_on_close(self, current_ltp: float) -> bool:
        """Check if trailing SL or target is hit on candle close"""
        if not self.current_position:
            return False
        
        index_config = get_index_config(config['selected_index'])
        qty = config['order_qty'] * index_config['lot_size']
        profit_points = current_ltp - self.entry_price
        
        # Check target first (if enabled)
        target_points = config.get('target_points', 0)
        if target_points > 0 and profit_points >= target_points:
            pnl = profit_points * qty
            logger.info(
                f"[EXIT] Target hit | LTP={current_ltp:.2f} | Entry={self.entry_price:.2f} | Profit={profit_points:.2f} pts | Target={target_points:.2f} pts"
            )
            await self.close_position(current_ltp, pnl, "Target Hit")
            return True
        
        # Update trailing SL
        # In ST+MACD histogram mode, trailing is SuperTrend-driven, but we still
        # want profit-lock + optional step trailing (trail_start_profit + trail_step) to apply.
        if bot_state.get('strategy_mode') == 'st_macd_hist':
            self._apply_profit_lock_and_step_trailing(current_ltp)
        else:
            await self.check_trailing_sl(current_ltp)
        
        # Check trailing SL
        if self.trailing_sl and current_ltp <= self.trailing_sl:
            pnl = (current_ltp - self.entry_price) * qty
            logger.info(f"[EXIT] Trailing SL hit | LTP={current_ltp:.2f} | SL={self.trailing_sl:.2f}")
            await self.close_position(current_ltp, pnl, "Trailing SL Hit")
            return True
        
        return False
    
    async def check_tick_sl(self, current_ltp: float) -> bool:
        """Check SL/Target on every tick (more responsive than candle close)"""
        if not self.current_position:
            return False
        
        index_config = get_index_config(config['selected_index'])
        qty = config['order_qty'] * index_config['lot_size']
        profit_points = current_ltp - self.entry_price
        pnl = profit_points * qty
        
        # Check DAILY max loss FIRST (highest priority)
        daily_max_loss = config.get('daily_max_loss', 0)
        if daily_max_loss > 0 and bot_state['daily_pnl'] + pnl < -daily_max_loss:
            logger.warning(
                f"[EXIT] ✗ Daily max loss BREACHED! | Current Daily P&L=₹{bot_state['daily_pnl']:.2f} | This trade P&L=₹{pnl:.2f} | Limit=₹{-daily_max_loss:.2f} | FORCE SQUAREOFF"
            )
            await self.close_position(current_ltp, pnl, "Daily Max Loss")
            bot_state['daily_max_loss_triggered'] = True
            return True
        
        # Check max loss per trade (if enabled)
        max_loss_per_trade = config.get('max_loss_per_trade', 0)
        if max_loss_per_trade > 0 and pnl < -max_loss_per_trade:
            logger.info(
                f"[EXIT] Max loss per trade hit | LTP={current_ltp:.2f} | Entry={self.entry_price:.2f} | Loss=₹{abs(pnl):.2f} | Limit=₹{max_loss_per_trade:.2f}"
            )
            await self.close_position(current_ltp, pnl, "Max Loss Per Trade")
            return True
        
        # Check target (if enabled)
        target_points = config.get('target_points', 0)
        if target_points > 0 and profit_points >= target_points:
            logger.info(
                f"[EXIT] Target hit (tick) | LTP={current_ltp:.2f} | Entry={self.entry_price:.2f} | Profit={profit_points:.2f} pts | Target={target_points:.2f} pts"
            )
            await self.close_position(current_ltp, pnl, "Target Hit")
            return True
        
        # Update trailing SL values
        if bot_state.get('strategy_mode') == 'st_macd_hist':
            self._apply_profit_lock_and_step_trailing(current_ltp)
        else:
            await self.check_trailing_sl(current_ltp)
        
        # Check if trailing SL is breached
        if self.trailing_sl and current_ltp <= self.trailing_sl:
            pnl = (current_ltp - self.entry_price) * qty
            logger.info(f"[EXIT] Trailing SL hit (tick) | LTP={current_ltp:.2f} | SL={self.trailing_sl:.2f}")
            await self.close_position(current_ltp, pnl, "Trailing SL Hit")
            return True
        
        return False
    
    async def enter_position(self, option_type: str, strike: int, index_ltp: float, *, expiry_override: str | None = None, security_id_override: str | None = None):
        """Enter a new position with market validation"""
        # Entry window protection (09:25–15:10 IST, weekday only)
        if not self.is_within_trading_hours():
            logger.warning(f"[ENTRY] ✗ BLOCKED - Outside entry window (09:25–15:10 IST) | Cannot enter {option_type} position")
            return
        
        index_name = config['selected_index']
        index_config = get_index_config(index_name)
        
        # Calculate position size based on risk (if enabled)
        risk_per_trade = config.get('risk_per_trade', 0)
        if risk_per_trade > 0 and config.get('initial_stoploss', 0) > 0:
            # Position size = Risk Amount / (SL points * lot size * point value)
            # For options: 1 point = 1 rupee per lot
            sl_points = config['initial_stoploss']
            max_qty = int(risk_per_trade / (sl_points * index_config['lot_size']))
            qty = max(1, min(max_qty, config['order_qty']))  # Between 1 and order_qty
            logger.info(f"[POSITION] Size adjusted for risk: {qty} lots (Risk: ₹{risk_per_trade}, SL: {sl_points}pts)")
        else:
            qty = config['order_qty'] * index_config['lot_size']
        
        trade_id = f"T{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Get expiry
        expiry = str(expiry_override) if expiry_override else (await self.dhan.get_nearest_expiry(index_name) if self.dhan else None)
        if not expiry:
            ist = get_ist_time()
            expiry_day = index_config['expiry_day']
            days_until_expiry = (expiry_day - ist.weekday()) % 7
            if days_until_expiry == 0 and ist.hour >= 15:
                days_until_expiry = 7
            expiry_date = ist + timedelta(days=days_until_expiry)
            expiry = expiry_date.strftime("%Y-%m-%d")
        
        entry_price = 0
        security_id = ""
        
        # Get real entry price
        if self.dhan:
            try:
                security_id = str(security_id_override) if security_id_override else await self.dhan.get_atm_option_security_id(index_name, strike, option_type, expiry)
                
                if security_id:
                    option_ltp = await self.dhan.get_option_ltp(
                        security_id=security_id,
                        strike=strike,
                        option_type=option_type,
                        expiry=expiry,
                        index_name=index_name
                    )
                    if option_ltp > 0:
                        entry_price = round(option_ltp / 0.05) * 0.05
                        entry_price = round(entry_price, 2)
            except Exception as e:
                logger.error("[ERROR] Failed to get entry price: %s", e)
        
        # Paper mode
        if bot_state['mode'] == 'paper':
            if not security_id:
                security_id = f"SIM_{index_name}_{strike}_{option_type}"
            
            if entry_price <= 0:
                distance = abs(index_ltp - strike)
                intrinsic = max(0, index_ltp - strike) if option_type == 'CE' else max(0, strike - index_ltp)
                time_value = 150 * max(0, 1 - (distance / 500))
                entry_price = round((intrinsic + time_value) / 0.05) * 0.05
                entry_price = round(entry_price, 2)
            
            logger.info(
                f"[ENTRY] PAPER | {index_name} {option_type} {strike} | Expiry: {expiry} | Price: {entry_price} | Qty: {qty}"
            )
        
        # Live mode
        else:
            if not self.dhan:
                logger.error("[ERROR] Dhan API not initialized")
                return
            
            if not security_id:
                logger.error(f"[ERROR] Could not find security ID for {index_name} {strike} {option_type}")
                return
            
            result = await self.dhan.place_order(security_id, "BUY", qty)
            logger.info(f"[ORDER] Entry order result: {result}")
            
            # Check if order was successfully placed
            if result.get('status') != 'success' or not result.get('orderId'):
                logger.error(f"[ERROR] Failed to place entry order: {result}")
                return
            
            # Order placed successfully - save to DB immediately
            order_id = result.get('orderId')
            
            logger.info(
                f"[ENTRY] LIVE | {index_name} {option_type} {strike} | Expiry: {expiry} | OrderID: {order_id} | Fill Price: {entry_price} | Qty: {qty}"
            )
        
        # Save position
        self.current_position = {
            'trade_id': trade_id,
            'option_type': option_type,
            'strike': strike,
            'expiry': expiry,
            'security_id': security_id,
            'index_name': index_name,
            'entry_time': datetime.now(timezone.utc).isoformat()
        }
        self.entry_price = entry_price
        self.trailing_sl = None
        self.highest_profit = 0
        
        bot_state['current_position'] = self.current_position
        bot_state['entry_price'] = self.entry_price
        bot_state['daily_trades'] += 1
        bot_state['current_option_ltp'] = entry_price

        # Save to database in background - don't wait for DB commit
        asyncio.create_task(save_trade({
            'trade_id': trade_id,
            'entry_time': datetime.now(timezone.utc).isoformat(),
            'option_type': option_type,
            'strike': strike,
            'expiry': expiry,
            'entry_price': self.entry_price,
            'qty': qty,
            'mode': bot_state['mode'],
            'index_name': index_name,
            'created_at': datetime.now(timezone.utc).isoformat()
        }))


# Global bot instance
trading_bot = TradingBot()
