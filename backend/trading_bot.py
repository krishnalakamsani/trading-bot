"""Trading Bot Engine
Handles all trading logic, signal processing, and order execution.
Uses structured logging with tags for easy troubleshooting.
"""
import asyncio
from datetime import datetime, timezone, timedelta
import logging
import random

from config import bot_state, config, DB_PATH
from indices import get_index_config, round_to_strike
from utils import get_ist_time, is_market_open, can_take_new_trade, should_force_squareoff, format_timeframe
from indicators import SuperTrend, MACD, SuperTrendMACD
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
        self.indicator = None  # Will hold selected indicator
        self.last_exit_candle_time = None
        self.last_trade_time = None  # For min_trade_gap protection
        self.last_signal = None  # For trade_only_on_flip protection
        self._initialize_indicator()
    
    def initialize_dhan(self):
        """Initialize Dhan API connection"""
        if config['dhan_access_token'] and config['dhan_client_id']:
            self.dhan = DhanAPI(config['dhan_access_token'], config['dhan_client_id'])
            logger.info("[MARKET] Dhan API initialized")
            return True
        logger.warning("[ERROR] Dhan API credentials not configured")
        return False
    
    def _initialize_indicator(self):
        """Initialize SuperTrend + MACD indicator"""
        try:
            self.indicator = SuperTrendMACD(
                supertrend_period=config['supertrend_period'],
                supertrend_mult=config['supertrend_multiplier'],
                macd_fast=config['macd_fast'],
                macd_slow=config['macd_slow'],
                macd_signal=config['macd_signal']
            )
            logger.info(f"[SIGNAL] SuperTrend + MACD initialized")
        except Exception as e:
            logger.error(f"[ERROR] Failed to initialize indicator: {e}")
            # Fallback
            self.indicator = SuperTrendMACD(
                supertrend_period=7,
                supertrend_mult=4,
                macd_fast=12,
                macd_slow=26,
                macd_signal=9
            )
    
    def reset_indicator(self):
        """Reset the selected indicator"""
        if self.indicator:
            self.indicator.reset()
            logger.info(f"[SIGNAL] Indicator reset: {config.get('indicator_type', 'supertrend')}")
    
    def is_within_trading_hours(self) -> bool:
        """Check if current time allows new entries
        
        Returns:
            bool: True if within allowed trading hours, False otherwise
        """
        ist = get_ist_time()
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
        self.last_signal = None
        self.task = asyncio.create_task(self.run_loop())
        
        index_name = config['selected_index']
        interval = format_timeframe(config['candle_interval'])
        indicator_name = config.get('indicator_type', 'supertrend')
        logger.info(f"[BOT] Started - Index: {index_name}, Timeframe: {interval}, Indicator: {indicator_name}, Mode: {bot_state['mode']}")
        
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
        
        # Send exit order to Dhan (only in live mode)
        if bot_state['mode'] != 'paper' and self.dhan and security_id:
            index_config = get_index_config(index_name)
            qty = config['order_qty'] * index_config['lot_size']
            
            try:
                result = await self.dhan.place_order(security_id, "SELL", qty)
                
                if result.get('status') == 'success' and result.get('orderId'):
                    order_id = result.get('orderId')
                    logger.info(f"[ORDER] Exit order placed | OrderID: {order_id} | Security: {security_id} | Qty: {qty}")
                    
                    # CRITICAL: Verify exit order was actually filled
                    fill_status = await self.dhan.verify_order_filled(order_id, security_id, qty, timeout_seconds=15)
                    
                    if fill_status.get('filled'):
                        logger.info(f"[ORDER] Exit order FILLED | Average Price: {fill_status.get('average_price')} | Message: {fill_status.get('message')}")
                        # Use actual filled price
                        actual_exit_price = fill_status.get('average_price', 0)
                        if actual_exit_price > 0:
                            exit_price = actual_exit_price
                    else:
                        logger.warning(f"[ORDER] Exit order NOT filled | Status: {fill_status.get('status')} | Message: {fill_status.get('message')}")
                else:
                    logger.warning(f"[ORDER] Exit order may have failed or not confirmed: {result}")
            except Exception as e:
                logger.error(f"[ORDER] Error sending exit order: {e}", exc_info=True)
        elif not security_id:
            logger.warning(f"[WARNING] Cannot send exit order - security_id missing for {index_name} {option_type}")
        elif bot_state['mode'] == 'paper':
            logger.debug(f"[ORDER] Paper mode - skipping exit order to Dhan")
        
        # Update database
        await update_trade_exit(
            trade_id=trade_id,
            exit_time=datetime.now(timezone.utc).isoformat(),
            exit_price=exit_price,
            pnl=pnl,
            exit_reason=reason
        )
        
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
        
        # Track the signal at exit - require signal change before next entry
        # If we exited CE position, last signal was GREEN
        # If we exited PE position, last signal was RED
        if option_type == 'CE':
            self.last_signal = 'GREEN'
        elif option_type == 'PE':
            self.last_signal = 'RED'
        
        self.current_position = None
        self.entry_price = 0
        self.trailing_sl = None
        self.highest_profit = 0
        
        logger.info(f"[EXIT] {index_name} {option_type} {strike} | Reason: {reason} | PnL: {pnl:.2f} | Wait for signal flip")
    
    async def run_loop(self):
        """Main trading loop"""
        logger.info("[BOT] Trading loop started")
        candle_start_time = datetime.now()
        high, low, close = 0, float('inf'), 0
        candle_number = 0
        
        while self.running:
            try:
                index_name = config['selected_index']
                candle_interval = config['candle_interval']
                
                # Check daily reset (9:15 AM IST)
                ist = get_ist_time()
                if ist.hour == 9 and ist.minute == 15:
                    bot_state['daily_trades'] = 0
                    bot_state['daily_pnl'] = 0.0
                    bot_state['daily_max_loss_triggered'] = False
                    bot_state['max_drawdown'] = 0.0
                    self.last_exit_candle_time = None
                    self.last_trade_time = None
                    self.last_signal = None
                    candle_number = 0
                    logger.info("[BOT] Daily reset at 9:15 AM")
                
                # Force square-off at 3:25 PM
                if should_force_squareoff() and self.current_position:
                    logger.info("[EXIT] Auto squareoff at 3:25 PM")
                    await self.squareoff()
                
                # Check if trading is allowed
                if not is_market_open():
                    await asyncio.sleep(5)
                    continue
                
                if bot_state['daily_max_loss_triggered']:
                    await asyncio.sleep(5)
                    continue
                
                # Fetch market data
                if self.dhan:
                    has_position = self.current_position is not None
                    option_security_id = None
                    
                    if has_position:
                        security_id = self.current_position.get('security_id', '')
                        if security_id and not security_id.startswith('SIM_'):
                            option_security_id = int(security_id)
                    
                    # Fetch Index + Option LTP
                    if option_security_id:
                        index_ltp, option_ltp = self.dhan.get_index_and_option_ltp(index_name, option_security_id)
                        if index_ltp > 0:
                            bot_state['index_ltp'] = index_ltp
                        if option_ltp > 0:
                            option_ltp = round(option_ltp / 0.05) * 0.05
                            bot_state['current_option_ltp'] = round(option_ltp, 2)
                    else:
                        index_ltp = self.dhan.get_index_ltp(index_name)
                        if index_ltp > 0:
                            bot_state['index_ltp'] = index_ltp
                    
                    # Update candle data
                    index_ltp = bot_state['index_ltp']
                    if index_ltp > 0:
                        if index_ltp > high:
                            high = index_ltp
                        if index_ltp < low:
                            low = index_ltp
                        close = index_ltp
                    
                    # Check SL/Target on EVERY TICK (responsive protection)
                    if self.current_position and bot_state['current_option_ltp'] > 0:
                        option_ltp = bot_state['current_option_ltp']
                        tick_exit = await self.check_tick_sl(option_ltp)
                        if tick_exit:
                            # Position exited on tick, reset candle for next entry
                            candle_start_time = datetime.now()
                            high, low, close = 0, float('inf'), 0
                            candle_number = 0
                            await asyncio.sleep(1)
                            continue
                
                # Check if candle is complete
                elapsed = (datetime.now() - candle_start_time).total_seconds()
                if elapsed >= candle_interval:
                    current_candle_time = datetime.now()
                    candle_number += 1
                    
                    if high > 0 and low < float('inf'):
                        indicator_value, signal = self.indicator.add_candle(high, low, close)
                        
                        if indicator_value and signal:
                            bot_state['supertrend_value'] = indicator_value if isinstance(indicator_value, (int, float)) else str(indicator_value)
                            bot_state['last_supertrend_signal'] = signal
                            
                            # Detailed candle close log
                            indicator_name = config.get('indicator_type', 'supertrend')
                            logger.info(
                                f"[CANDLE CLOSE #{candle_number}] {index_name} | "
                                f"H={high:.2f} L={low:.2f} C={close:.2f} | "
                                f"{indicator_name.upper()}={indicator_value} | "
                                f"Signal={signal} | "
                                f"Interval={format_timeframe(candle_interval)}"
                            )
                            
                            # Check trailing SL/Target on candle close ONLY
                            if self.current_position:
                                option_ltp = bot_state['current_option_ltp']
                                sl_hit = await self.check_trailing_sl_on_close(option_ltp)
                                
                                if sl_hit:
                                    self.last_exit_candle_time = current_candle_time
                            
                            # Trading logic - entries/exits based on SuperTrend signal
                            can_trade = True
                            if self.last_exit_candle_time:
                                time_since_exit = (current_candle_time - self.last_exit_candle_time).total_seconds()
                                if time_since_exit < candle_interval:
                                    can_trade = False
                            
                            if can_trade:
                                exited = await self.process_signal_on_close(signal, close)
                                if exited:
                                    self.last_exit_candle_time = current_candle_time
                    
                    # Reset candle for next period
                    candle_start_time = datetime.now()
                    high, low, close = 0, float('inf'), 0
                
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
                "position": bot_state['current_position'],
                "entry_price": bot_state['entry_price'],
                "current_option_ltp": bot_state['current_option_ltp'],
                "trailing_sl": bot_state['trailing_sl'],
                "daily_pnl": bot_state['daily_pnl'],
                "daily_trades": bot_state['daily_trades'],
                "is_running": bot_state['is_running'],
                "mode": bot_state['mode'],
                "selected_index": config['selected_index'],
                "candle_interval": config['candle_interval'],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        })
    
    async def check_trailing_sl(self, current_ltp: float):
        """Update SL values - initial fixed SL then trails profit using step-based method"""
        if not self.current_position:
            return
        
        profit_points = current_ltp - self.entry_price
        
        # Track highest profit reached
        if profit_points > self.highest_profit:
            self.highest_profit = profit_points
        
        # Step 1: Set initial fixed stoploss (if enabled)
        initial_sl = config.get('initial_stoploss', 0)
        if initial_sl > 0 and self.trailing_sl is None:
            self.trailing_sl = self.entry_price - initial_sl
            bot_state['trailing_sl'] = self.trailing_sl
            logger.info(f"[SL] Initial SL set: {self.trailing_sl:.2f} ({initial_sl} pts below entry)")
            return
        
        # Step 2: Start trailing SL after reaching trail_start_profit
        trail_start = config.get('trail_start_profit', 0)
        trail_step = config.get('trail_step', 0)
        
        if trail_start <= 0 or trail_step <= 0:
            return  # Trailing disabled
        
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
                "[EXIT] Target hit | LTP=%.2f | Entry=%.2f | Profit=%.2f pts | Target=%.2f pts",
                current_ltp, self.entry_price, profit_points, target_points
            )
            await self.close_position(current_ltp, pnl, "Target Hit")
            return True
        
        # Update trailing SL
        await self.check_trailing_sl(current_ltp)
        
        # Check trailing SL
        if self.trailing_sl and current_ltp <= self.trailing_sl:
            pnl = (current_ltp - self.entry_price) * qty
            logger.info("[EXIT] Trailing SL hit | LTP=%.2f | SL=%.2f", current_ltp, self.trailing_sl)
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
        
        # Check max loss per trade (if enabled)
        max_loss_per_trade = config.get('max_loss_per_trade', 0)
        if max_loss_per_trade > 0 and pnl < -max_loss_per_trade:
            logger.info(
                "[EXIT] Max loss per trade hit | LTP=%.2f | Entry=%.2f | Loss=₹%.2f | Limit=₹%.2f",
                current_ltp, self.entry_price, abs(pnl), max_loss_per_trade
            )
            await self.close_position(current_ltp, pnl, "Max Loss Per Trade")
            return True
        
        # Check target (if enabled)
        target_points = config.get('target_points', 0)
        if target_points > 0 and profit_points >= target_points:
            logger.info(
                "[EXIT] Target hit (tick) | LTP=%.2f | Entry=%.2f | Profit=%.2f pts | Target=%.2f pts",
                current_ltp, self.entry_price, profit_points, target_points
            )
            await self.close_position(current_ltp, pnl, "Target Hit")
            return True
        
        # Update trailing SL values
        await self.check_trailing_sl(current_ltp)
        
        # Check if trailing SL is breached
        if self.trailing_sl and current_ltp <= self.trailing_sl:
            pnl = (current_ltp - self.entry_price) * qty
            logger.info("[EXIT] Trailing SL hit (tick) | LTP=%.2f | SL=%.2f", current_ltp, self.trailing_sl)
            await self.close_position(current_ltp, pnl, "Trailing SL Hit")
            return True
        
        return False
    
    async def process_signal_on_close(self, signal: str, index_ltp: float) -> bool:
        """Process SuperTrend signal on candle close"""
        exited = False
        index_name = config['selected_index']
        index_config = get_index_config(index_name)
        qty = config['order_qty'] * index_config['lot_size']
        
        # Check for exit on signal reversal
        if self.current_position:
            position_type = self.current_position.get('option_type', '')
            
            if position_type == 'CE' and signal == 'RED':
                exit_price = bot_state['current_option_ltp']
                pnl = (exit_price - self.entry_price) * qty
                logger.info("[SIGNAL] SuperTrend flip RED - Exiting CE")
                await self.close_position(exit_price, pnl, "SuperTrend Reversal")
                return True
            
            if position_type == 'PE' and signal == 'GREEN':
                exit_price = bot_state['current_option_ltp']
                pnl = (exit_price - self.entry_price) * qty
                logger.info("[SIGNAL] SuperTrend flip GREEN - Exiting PE")
                await self.close_position(exit_price, pnl, "SuperTrend Reversal")
                return True
        
        # Check if new trade allowed
        if self.current_position:
            return exited
        
        if not can_take_new_trade():
            return exited
        
        if bot_state['daily_trades'] >= config['max_trades_per_day']:
            logger.info("[SIGNAL] Max daily trades reached (%d)", config['max_trades_per_day'])
            return exited
        
        # ALWAYS require signal change before new entry (after any exit)
        if self.last_signal == signal:
            logger.info(f"[SIGNAL] Waiting for signal flip - Current: {signal}, Last: {self.last_signal}")
            return exited
        
        # Check min_trade_gap protection (optional)
        min_gap = config.get('min_trade_gap', 0)
        if min_gap > 0 and self.last_trade_time:
            time_since_last = (datetime.now() - self.last_trade_time).total_seconds()
            if time_since_last < min_gap:
                logger.debug("[SIGNAL] Skipping - min trade gap not met (%.1fs < %ds)", time_since_last, min_gap)
                return exited
        
        # Enter new position
        option_type = 'PE' if signal == 'RED' else 'CE'
        atm_strike = round_to_strike(index_ltp, index_name)
        
        # Log signal details
        logger.info(
            f"[SIGNAL] {signal} | "
            f"Index: {index_name} | "
            f"LTP: {index_ltp:.2f} | "
            f"ATM Strike: {atm_strike} | "
            f"Option: {option_type} | "
            f"SuperTrend: {bot_state['supertrend_value']:.2f}"
        )
        
        await self.enter_position(option_type, atm_strike, index_ltp)
        self.last_signal = signal
        self.last_trade_time = datetime.now()
        
        return exited
    
    async def enter_position(self, option_type: str, strike: int, index_ltp: float):
        """Enter a new position"""
        # CHECK: Trading hours protection
        if not self.is_within_trading_hours():
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
        expiry = await self.dhan.get_nearest_expiry(index_name) if self.dhan else None
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
                security_id = await self.dhan.get_atm_option_security_id(index_name, strike, option_type, expiry)
                
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
                "[ENTRY] PAPER | %s %s %s | Expiry: %s | Price: %s | Qty: %s",
                index_name, option_type, strike, expiry, entry_price, qty
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
            
            # CRITICAL: Verify order was actually filled (not just placed)
            order_id = result.get('orderId')
            fill_status = await self.dhan.verify_order_filled(order_id, security_id, qty, timeout_seconds=15)
            
            if not fill_status.get('filled'):
                logger.error(f"[ERROR] Entry order NOT filled | Status: {fill_status.get('status')} | Message: {fill_status.get('message')}")
                return
            
            # Order was filled! Use actual fill price
            filled_price = fill_status.get('average_price', 0)
            if filled_price <= 0:
                # Fallback to quoted price if fill price not available
                filled_price = await self.dhan.get_option_ltp(
                    security_id=security_id,
                    strike=strike,
                    option_type=option_type,
                    expiry=expiry,
                    index_name=index_name
                )
            
            entry_price = filled_price or entry_price or 0
            
            logger.info(
                "[ENTRY] LIVE | %s %s %s | Expiry: %s | OrderID: %s | Fill Price: %s | Qty: %s",
                index_name, option_type, strike, expiry, order_id, entry_price, qty
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
        
        # Save to database
        await save_trade({
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
        })


# Global bot instance
trading_bot = TradingBot()
