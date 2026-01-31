"""Trading Bot Engine
Handles all trading logic, signal processing, and order execution.
Uses structured logging with tags for easy troubleshooting.
"""
import asyncio
from datetime import datetime, timezone, timedelta
import logging
import random
import json
from pathlib import Path

from config import bot_state, config, DB_PATH
from indices import get_index_config, round_to_strike
from utils import get_ist_time, is_market_open, can_take_new_trade, should_force_squareoff, format_timeframe
from indicators import SuperTrend, MACD, ADX
from strategy_agent import AgentAction, AgentInputs, STAdxMacdAgent
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
        self.macd_indicator = None
        self.adx_indicator = None
        self.strategy_agent = STAdxMacdAgent(
            adx_min=float(config.get('agent_adx_min', 20.0)),
            wave_reset_macd_abs=float(config.get('agent_wave_reset_macd_abs', 0.05)),
        )

        # Option-fixed signal engines (long-only per contract)
        self.option_ce_agent = STAdxMacdAgent(
            adx_min=float(config.get('agent_adx_min', 20.0)),
            wave_reset_macd_abs=float(config.get('agent_wave_reset_macd_abs', 0.05)),
        )
        self.option_pe_agent = STAdxMacdAgent(
            adx_min=float(config.get('agent_adx_min', 20.0)),
            wave_reset_macd_abs=float(config.get('agent_wave_reset_macd_abs', 0.05)),
        )

        self.fixed_option_strike = None
        self.fixed_option_expiry = None
        self.fixed_ce_security_id = None
        self.fixed_pe_security_id = None

        # Separate option-candle indicators
        self.opt_ce_st = None
        self.opt_ce_macd = None
        self.opt_ce_adx = None
        self.opt_pe_st = None
        self.opt_pe_macd = None
        self.opt_pe_adx = None

        self._opt_ce_last_macd_value = None
        self._opt_pe_last_macd_value = None
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
        self._last_macd_value = None
        self._last_st_direction = None
        self._agent_state_path = Path(DB_PATH).parent / 'agent_state.json'
        self._last_persisted_agent_state = None
        self.last_exit_candle_time = None
        self.last_trade_time = None  # For min_trade_gap protection
        self._initialize_indicator()

        # Keep bot_state strategy_mode in sync for API/WS
        bot_state['strategy_mode'] = config.get('strategy_mode', 'agent')
        bot_state['signal_source'] = config.get('signal_source', 'index')
    
    def initialize_dhan(self):
        """Initialize Dhan API connection"""
        if config['dhan_access_token'] and config['dhan_client_id']:
            self.dhan = DhanAPI(config['dhan_access_token'], config['dhan_client_id'])
            logger.info("[MARKET] Dhan API initialized")
            return True
        logger.warning("[ERROR] Dhan API credentials not configured")
        return False
    
    def _initialize_indicator(self):
        """Initialize SuperTrend indicator"""
        try:
            self.indicator = SuperTrend(
                period=config['supertrend_period'],
                multiplier=config['supertrend_multiplier']
            )
            self.macd_indicator = MACD()  # Defaults: 12/26/9
            self.adx_indicator = ADX()    # Default: 14
            logger.info(f"[SIGNAL] SuperTrend initialized")
        except Exception as e:
            logger.error(f"[ERROR] Failed to initialize indicator: {e}")
            # Fallback to SuperTrend
            self.indicator = SuperTrend(period=7, multiplier=4)
            self.macd_indicator = MACD()
            self.adx_indicator = ADX()
            logger.info(f"[SIGNAL] SuperTrend (fallback) initialized")
    
    def reset_indicator(self):
        """Reset the selected indicator"""
        if self.indicator:
            self.indicator.reset()
        if self.macd_indicator:
            self.macd_indicator.reset()
        if self.adx_indicator:
            self.adx_indicator.reset()

        if self.opt_ce_st:
            self.opt_ce_st.reset()
        if self.opt_ce_macd:
            self.opt_ce_macd.reset()
        if self.opt_ce_adx:
            self.opt_ce_adx.reset()
        if self.opt_pe_st:
            self.opt_pe_st.reset()
        if self.opt_pe_macd:
            self.opt_pe_macd.reset()
        if self.opt_pe_adx:
            self.opt_pe_adx.reset()

        self.apply_strategy_config()
        self.strategy_agent.reset_session("RESET")
        self.option_ce_agent.reset_session("RESET")
        self.option_pe_agent.reset_session("RESET")
        self._try_load_agent_state()
        self._last_macd_value = None
        self._last_st_direction = None

        self._opt_ce_last_macd_value = None
        self._opt_pe_last_macd_value = None
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
            f"[SIGNAL] Indicator reset: {config.get('indicator_type', 'supertrend')} (Strategy: {config.get('strategy_mode', 'agent')})"
        )

    def apply_strategy_config(self) -> None:
        """Apply config-driven strategy settings to the agent instance."""
        # Keep bot_state updated for API/WS
        bot_state['strategy_mode'] = config.get('strategy_mode', 'agent')
        bot_state['signal_source'] = config.get('signal_source', 'index')

        self.strategy_agent.adx_min = float(config.get('agent_adx_min', self.strategy_agent.adx_min))
        self.strategy_agent.wave_reset_macd_abs = float(
            config.get('agent_wave_reset_macd_abs', self.strategy_agent.wave_reset_macd_abs)
        )

        self.option_ce_agent.adx_min = float(config.get('agent_adx_min', self.option_ce_agent.adx_min))
        self.option_ce_agent.wave_reset_macd_abs = float(
            config.get('agent_wave_reset_macd_abs', self.option_ce_agent.wave_reset_macd_abs)
        )
        self.option_pe_agent.adx_min = float(config.get('agent_adx_min', self.option_pe_agent.adx_min))
        self.option_pe_agent.wave_reset_macd_abs = float(
            config.get('agent_wave_reset_macd_abs', self.option_pe_agent.wave_reset_macd_abs)
        )

        # Ensure option signal indicators exist
        if self.opt_ce_st is None or self.opt_pe_st is None:
            self.opt_ce_st = SuperTrend(period=config['supertrend_period'], multiplier=config['supertrend_multiplier'])
            self.opt_ce_macd = MACD()
            self.opt_ce_adx = ADX()
            self.opt_pe_st = SuperTrend(period=config['supertrend_period'], multiplier=config['supertrend_multiplier'])
            self.opt_pe_macd = MACD()
            self.opt_pe_adx = ADX()

    async def _ensure_fixed_option_contract(self, index_name: str, index_ltp: float) -> bool:
        """Pick and cache a fixed CE+PE contract for signal generation."""
        if self.fixed_ce_security_id and self.fixed_pe_security_id and self.fixed_option_strike and self.fixed_option_expiry:
            return True

        # Simulation support: allow fixed-contract mode without broker connectivity.
        if config.get('bypass_market_hours', False) and (not self.dhan) and index_ltp and index_ltp > 0:
            strike = int(round_to_strike(index_ltp, index_name))
            expiry = "SIM"
            self.fixed_option_strike = strike
            self.fixed_option_expiry = expiry
            self.fixed_ce_security_id = f"SIM_{index_name}_{strike}_CE"
            self.fixed_pe_security_id = f"SIM_{index_name}_{strike}_PE"
            bot_state['fixed_option_strike'] = self.fixed_option_strike
            bot_state['fixed_option_expiry'] = self.fixed_option_expiry
            bot_state['fixed_ce_security_id'] = self.fixed_ce_security_id
            bot_state['fixed_pe_security_id'] = self.fixed_pe_security_id
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

    def _try_load_agent_state(self) -> None:
        if not config.get('persist_agent_state', True):
            return
        try:
            if not self._agent_state_path.exists():
                return
            raw = self._agent_state_path.read_text(encoding='utf-8')
            state = json.loads(raw)
            self.strategy_agent.load_state_dict(state)
            self._last_persisted_agent_state = self.strategy_agent.to_state_dict()
            logger.info(f"[AGENT] State restored from {self._agent_state_path}")
        except Exception as e:
            logger.warning(f"[AGENT] Failed to restore agent state: {e}")

    def _try_persist_agent_state(self) -> None:
        if not config.get('persist_agent_state', True):
            return
        try:
            state = self.strategy_agent.to_state_dict()
            if state == self._last_persisted_agent_state:
                return

            tmp_path = self._agent_state_path.with_suffix('.tmp')
            tmp_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
            tmp_path.replace(self._agent_state_path)
            self._last_persisted_agent_state = state
        except Exception as e:
            logger.warning(f"[AGENT] Failed to persist agent state: {e}")
    
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
        open_price, high, low, close = 0.0, 0, float('inf'), 0
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
                    candle_number = 0
                    self.reset_indicator()
                    logger.info("[BOT] Daily reset at 9:15 AM")
                
                # Force square-off at 3:25 PM
                if should_force_squareoff() and self.current_position:
                    logger.info("[EXIT] Auto squareoff at 3:25 PM")
                    await self.squareoff()
                
                # Check if trading is allowed
                market_open = is_market_open()
                if not market_open and not config.get('bypass_market_hours', False):
                    await asyncio.sleep(5)
                    continue
                
                if bot_state['daily_max_loss_triggered']:
                    await asyncio.sleep(5)
                    continue
                
                # Fetch market data
                if self.dhan:
                    signal_source = config.get('signal_source', 'index')
                    bot_state['signal_source'] = signal_source

                    if signal_source == 'option_fixed':
                        # Always keep index_ltp updated (used for UI + contract selection)
                        idx = self.dhan.get_index_ltp(index_name)
                        if idx > 0:
                            bot_state['index_ltp'] = float(idx)

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

                                if ce_sid:
                                    ce_val = option_ltps.get(int(ce_sid), 0.0)
                                    if ce_val and ce_val > 0:
                                        ce_val = round(float(ce_val) / 0.05) * 0.05
                                        bot_state['signal_ce_ltp'] = round(float(ce_val), 2)
                                if pe_sid:
                                    pe_val = option_ltps.get(int(pe_sid), 0.0)
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
                                            if pos_ltp and pos_ltp > 0:
                                                pos_ltp = round(float(pos_ltp) / 0.05) * 0.05
                                                bot_state['current_option_ltp'] = round(float(pos_ltp), 2)
                                        except Exception:
                                            pass

                    else:
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

                # If no data from API (market closed or no credentials), simulate movement for testing
                if (not market_open or not self.dhan) and config.get('bypass_market_hours', False):
                    # Initialize with realistic base value for index
                    if bot_state.get('simulated_base_price') is None:
                        if index_name == 'NIFTY':
                            bot_state['simulated_base_price'] = 23500.0
                        elif index_name == 'BANKNIFTY':
                            bot_state['simulated_base_price'] = 51500.0
                        elif index_name == 'FINNIFTY':
                            bot_state['simulated_base_price'] = 22000.0
                        elif index_name == 'MIDCPNIFTY':
                            bot_state['simulated_base_price'] = 12500.0
                        else:
                            bot_state['simulated_base_price'] = 70000.0

                    # Generate realistic tick movements
                    tick_change = random.choice([-15, -10, -5, -2, 0, 2, 5, 10, 15])
                    bot_state['simulated_base_price'] += tick_change
                    bot_state['index_ltp'] = round(float(bot_state['simulated_base_price']), 2)

                    # In option_fixed mode, simulate CE/PE LTPs too (so candles + signals can be tested)
                    if config.get('signal_source', 'index') == 'option_fixed':
                        idx_ltp = float(bot_state['index_ltp'])
                        await self._ensure_fixed_option_contract(index_name, idx_ltp)
                        strike = self.fixed_option_strike or int(round_to_strike(idx_ltp, index_name))
                        bot_state['fixed_option_strike'] = strike
                        bot_state['fixed_option_expiry'] = self.fixed_option_expiry or "SIM"

                        distance_from_atm = abs(idx_ltp - strike)
                        time_decay_factor = max(0.0, 1.0 - (distance_from_atm / 500.0))
                        time_value = 150.0 * time_decay_factor
                        noise = random.choice([-0.20, -0.10, -0.05, 0, 0.05, 0.10, 0.20])

                        ce_intrinsic = max(0.0, idx_ltp - strike)
                        pe_intrinsic = max(0.0, strike - idx_ltp)
                        ce_ltp = max(0.05, ce_intrinsic + time_value + noise)
                        pe_ltp = max(0.05, pe_intrinsic + time_value + noise)

                        ce_ltp = round(ce_ltp / 0.05) * 0.05
                        pe_ltp = round(pe_ltp / 0.05) * 0.05
                        bot_state['signal_ce_ltp'] = round(float(ce_ltp), 2)
                        bot_state['signal_pe_ltp'] = round(float(pe_ltp), 2)

                        # Keep position LTP in sync with the simulated signal LTP
                        if self.current_position:
                            pos_type = self.current_position.get('option_type')
                            if pos_type == 'CE':
                                bot_state['current_option_ltp'] = bot_state['signal_ce_ltp']
                            elif pos_type == 'PE':
                                bot_state['current_option_ltp'] = bot_state['signal_pe_ltp']
                
                # Note: simulation is handled above when bypass_market_hours is enabled.
                
                # Update candle data
                index_ltp = bot_state['index_ltp']
                if index_ltp > 0:
                    if open_price == 0.0:
                        open_price = float(index_ltp)
                    if index_ltp > high:
                        high = index_ltp
                    if index_ltp < low:
                        low = index_ltp
                    close = index_ltp

                # Build option candles for fixed-contract signals (from option LTP ticks)
                if config.get('signal_source', 'index') == 'option_fixed':
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
                        prev_st_direction = self._last_st_direction

                        indicator_value, _ = self.indicator.add_candle(high, low, close)
                        st_direction = getattr(self.indicator, 'direction', None)

                        supertrend_flipped = (
                            prev_st_direction is not None
                            and st_direction in (1, -1)
                            and st_direction != prev_st_direction
                        )
                        if st_direction in (1, -1):
                            self._last_st_direction = st_direction

                        macd_previous = self._last_macd_value
                        macd_current, _ = self.macd_indicator.add_candle(high, low, close) if self.macd_indicator else (None, None)
                        if macd_current is not None:
                            self._last_macd_value = macd_current

                        adx_value, _ = self.adx_indicator.add_candle(high, low, close) if self.adx_indicator else (None, None)

                        if isinstance(adx_value, (int, float)):
                            bot_state['adx_value'] = float(adx_value)

                        # Update state for UI/analytics (safe even if indicators not ready)
                        if isinstance(indicator_value, (int, float)):
                            bot_state['supertrend_value'] = float(indicator_value)
                        if isinstance(macd_current, (int, float)):
                            bot_state['macd_value'] = float(macd_current)

                        bot_state['strategy_mode'] = config.get('strategy_mode', 'agent')

                        if st_direction == 1:
                            bot_state['last_supertrend_signal'] = "GREEN"
                            bot_state['signal_status'] = "buy"
                        elif st_direction == -1:
                            bot_state['last_supertrend_signal'] = "RED"
                            bot_state['signal_status'] = "sell"
                        else:
                            bot_state['signal_status'] = "waiting"

                        st_dir_label = "GREEN" if st_direction == 1 else "RED" if st_direction == -1 else "NA"
                        flip_label = "FLIP" if supertrend_flipped else "NOFLIP"
                        macd_log = f"{macd_current:.4f}" if isinstance(macd_current, (int, float)) else "NA"
                        adx_log = f"{adx_value:.2f}" if isinstance(adx_value, (int, float)) else "NA"
                        st_log = f"{indicator_value:.2f}" if isinstance(indicator_value, (int, float)) else "NA"

                        logger.info(
                            f"[CANDLE CLOSE #{candle_number}] {index_name} | "
                            f"O={open_price:.2f} H={high:.2f} L={low:.2f} C={close:.2f} | "
                            f"ST={st_log}({st_dir_label},{flip_label}) | ADX={adx_log} | MACD={macd_log}"
                        )

                        # Save candle data for analysis
                        from database import save_candle_data
                        await save_candle_data(
                            candle_number=candle_number,
                            index_name=index_name,
                            high=high,
                            low=low,
                            close=close,
                            supertrend_value=indicator_value if isinstance(indicator_value, (int, float)) else 0.0,
                            macd_value=macd_current if isinstance(macd_current, (int, float)) else 0.0,
                            signal_status=bot_state['signal_status']
                        )

                        # Check trailing SL/Target on candle close (additional safety)
                        if self.current_position:
                            option_ltp = bot_state['current_option_ltp']
                            sl_hit = await self.check_trailing_sl_on_close(option_ltp)
                            if sl_hit:
                                self.last_exit_candle_time = current_candle_time

                        # Strategy agent decision (strategy-only)
                        strategy_mode = config.get('strategy_mode', 'agent')
                        signal_source = config.get('signal_source', 'index')
                        action = AgentAction.HOLD

                        if signal_source == 'option_fixed':
                            # Fixed-contract option-candle signals
                            self.apply_strategy_config()

                            strike = bot_state.get('fixed_option_strike')
                            expiry = bot_state.get('fixed_option_expiry')
                            ce_sid = bot_state.get('fixed_ce_security_id')
                            pe_sid = bot_state.get('fixed_pe_security_id')

                            ce_ready = self._ce_high > 0 and self._ce_low < float('inf') and self._ce_close > 0
                            pe_ready = self._pe_high > 0 and self._pe_low < float('inf') and self._pe_close > 0

                            ce_action = AgentAction.HOLD
                            pe_action = AgentAction.HOLD

                            ce_macd_previous = self._opt_ce_last_macd_value
                            ce_prev_st_direction = self._opt_ce_last_st_direction
                            pe_macd_previous = self._opt_pe_last_macd_value
                            pe_prev_st_direction = self._opt_pe_last_st_direction

                            # Decide exits first (only for the held contract)
                            if self.current_position and self.current_position.get('option_type') == 'CE' and ce_ready:
                                ce_st_val, _ = self.opt_ce_st.add_candle(self._ce_high, self._ce_low, self._ce_close)
                                ce_st_direction = getattr(self.opt_ce_st, 'direction', None)
                                ce_flipped = (
                                    ce_prev_st_direction is not None
                                    and ce_st_direction in (1, -1)
                                    and ce_st_direction != ce_prev_st_direction
                                )
                                if ce_st_direction in (1, -1):
                                    self._opt_ce_last_st_direction = ce_st_direction

                                ce_macd_current, _ = self.opt_ce_macd.add_candle(self._ce_high, self._ce_low, self._ce_close)
                                if ce_macd_current is not None:
                                    self._opt_ce_last_macd_value = ce_macd_current
                                ce_adx_value, _ = self.opt_ce_adx.add_candle(self._ce_high, self._ce_low, self._ce_close)

                                if strategy_mode == 'supertrend':
                                    if ce_flipped:
                                        action = AgentAction.EXIT
                                else:
                                    inputs_ce = AgentInputs(
                                        timestamp=datetime.now(timezone.utc).isoformat(),
                                        open=float(self._ce_open),
                                        high=float(self._ce_high),
                                        low=float(self._ce_low),
                                        close=float(self._ce_close),
                                        supertrend_direction=ce_st_direction if ce_st_direction in (1, -1) else None,
                                        supertrend_flipped=bool(ce_flipped),
                                        adx_value=float(ce_adx_value) if isinstance(ce_adx_value, (int, float)) else None,
                                        macd_current=float(ce_macd_current) if isinstance(ce_macd_current, (int, float)) else None,
                                        macd_previous=float(ce_macd_previous) if isinstance(ce_macd_previous, (int, float)) else None,
                                        in_position=True,
                                        current_position_side='CE',
                                    )
                                    action = self.option_ce_agent.decide(inputs_ce)
                                    if action != AgentAction.EXIT:
                                        action = AgentAction.HOLD

                            elif self.current_position and self.current_position.get('option_type') == 'PE' and pe_ready:
                                pe_st_val, _ = self.opt_pe_st.add_candle(self._pe_high, self._pe_low, self._pe_close)
                                pe_st_direction = getattr(self.opt_pe_st, 'direction', None)
                                pe_flipped = (
                                    pe_prev_st_direction is not None
                                    and pe_st_direction in (1, -1)
                                    and pe_st_direction != pe_prev_st_direction
                                )
                                if pe_st_direction in (1, -1):
                                    self._opt_pe_last_st_direction = pe_st_direction

                                pe_macd_current, _ = self.opt_pe_macd.add_candle(self._pe_high, self._pe_low, self._pe_close)
                                if pe_macd_current is not None:
                                    self._opt_pe_last_macd_value = pe_macd_current
                                pe_adx_value, _ = self.opt_pe_adx.add_candle(self._pe_high, self._pe_low, self._pe_close)

                                if strategy_mode == 'supertrend':
                                    if pe_flipped:
                                        action = AgentAction.EXIT
                                else:
                                    inputs_pe = AgentInputs(
                                        timestamp=datetime.now(timezone.utc).isoformat(),
                                        open=float(self._pe_open),
                                        high=float(self._pe_high),
                                        low=float(self._pe_low),
                                        close=float(self._pe_close),
                                        supertrend_direction=pe_st_direction if pe_st_direction in (1, -1) else None,
                                        supertrend_flipped=bool(pe_flipped),
                                        adx_value=float(pe_adx_value) if isinstance(pe_adx_value, (int, float)) else None,
                                        macd_current=float(pe_macd_current) if isinstance(pe_macd_current, (int, float)) else None,
                                        macd_previous=float(pe_macd_previous) if isinstance(pe_macd_previous, (int, float)) else None,
                                        in_position=True,
                                        current_position_side='CE',
                                    )
                                    action = self.option_pe_agent.decide(inputs_pe)
                                    if action != AgentAction.EXIT:
                                        action = AgentAction.HOLD

                            # Entries (only if flat)
                            if not self.current_position and action == AgentAction.HOLD and ce_ready and pe_ready and strike and expiry and ce_sid and pe_sid:
                                # Compute CE candle indicators
                                ce_prev_dir = self._opt_ce_last_st_direction
                                ce_st_val, _ = self.opt_ce_st.add_candle(self._ce_high, self._ce_low, self._ce_close)
                                ce_dir = getattr(self.opt_ce_st, 'direction', None)
                                ce_flip = (
                                    ce_prev_dir is not None
                                    and ce_dir in (1, -1)
                                    and ce_dir != ce_prev_dir
                                )
                                if ce_dir in (1, -1):
                                    self._opt_ce_last_st_direction = ce_dir

                                ce_macd_curr, _ = self.opt_ce_macd.add_candle(self._ce_high, self._ce_low, self._ce_close)
                                if ce_macd_curr is not None:
                                    self._opt_ce_last_macd_value = ce_macd_curr
                                ce_adx_val, _ = self.opt_ce_adx.add_candle(self._ce_high, self._ce_low, self._ce_close)

                                # Compute PE candle indicators
                                pe_prev_dir = self._opt_pe_last_st_direction
                                pe_st_val, _ = self.opt_pe_st.add_candle(self._pe_high, self._pe_low, self._pe_close)
                                pe_dir = getattr(self.opt_pe_st, 'direction', None)
                                pe_flip = (
                                    pe_prev_dir is not None
                                    and pe_dir in (1, -1)
                                    and pe_dir != pe_prev_dir
                                )
                                if pe_dir in (1, -1):
                                    self._opt_pe_last_st_direction = pe_dir

                                pe_macd_curr, _ = self.opt_pe_macd.add_candle(self._pe_high, self._pe_low, self._pe_close)
                                if pe_macd_curr is not None:
                                    self._opt_pe_last_macd_value = pe_macd_curr
                                pe_adx_val, _ = self.opt_pe_adx.add_candle(self._pe_high, self._pe_low, self._pe_close)

                                if strategy_mode == 'supertrend':
                                    if ce_flip and ce_dir == 1:
                                        ce_action = AgentAction.ENTER_CE
                                    if pe_flip and pe_dir == 1:
                                        pe_action = AgentAction.ENTER_CE
                                else:
                                    # Long-only per contract: treat ENTER_CE as "enter long" and ignore ENTER_PE.
                                    inputs_ce = AgentInputs(
                                        timestamp=datetime.now(timezone.utc).isoformat(),
                                        open=float(self._ce_open),
                                        high=float(self._ce_high),
                                        low=float(self._ce_low),
                                        close=float(self._ce_close),
                                        supertrend_direction=ce_dir if ce_dir in (1, -1) else None,
                                        supertrend_flipped=bool(ce_flip),
                                        adx_value=float(ce_adx_val) if isinstance(ce_adx_val, (int, float)) else None,
                                        macd_current=float(ce_macd_curr) if isinstance(ce_macd_curr, (int, float)) else None,
                                        macd_previous=float(ce_macd_previous) if isinstance(ce_macd_previous, (int, float)) else None,
                                        in_position=False,
                                        current_position_side=None,
                                    )
                                    ce_action = self.option_ce_agent.decide(inputs_ce)

                                    inputs_pe = AgentInputs(
                                        timestamp=datetime.now(timezone.utc).isoformat(),
                                        open=float(self._pe_open),
                                        high=float(self._pe_high),
                                        low=float(self._pe_low),
                                        close=float(self._pe_close),
                                        supertrend_direction=pe_dir if pe_dir in (1, -1) else None,
                                        supertrend_flipped=bool(pe_flip),
                                        adx_value=float(pe_adx_val) if isinstance(pe_adx_val, (int, float)) else None,
                                        macd_current=float(pe_macd_curr) if isinstance(pe_macd_curr, (int, float)) else None,
                                        macd_previous=float(pe_macd_previous) if isinstance(pe_macd_previous, (int, float)) else None,
                                        in_position=False,
                                        current_position_side=None,
                                    )
                                    pe_action = self.option_pe_agent.decide(inputs_pe)

                                # Map long-entry signals to ENTER_CE/ENTER_PE.
                                ce_enter = ce_action == AgentAction.ENTER_CE
                                pe_enter = pe_action == AgentAction.ENTER_CE

                                if ce_enter and pe_enter:
                                    # Tie-break: prefer stronger momentum (abs(MACD)), else CE.
                                    if isinstance(pe_macd_curr, (int, float)) and isinstance(ce_macd_curr, (int, float)):
                                        if abs(pe_macd_curr) > abs(ce_macd_curr):
                                            action = AgentAction.ENTER_PE
                                        else:
                                            action = AgentAction.ENTER_CE
                                    else:
                                        action = AgentAction.ENTER_CE
                                elif ce_enter:
                                    action = AgentAction.ENTER_CE
                                elif pe_enter:
                                    action = AgentAction.ENTER_PE

                                if action != AgentAction.HOLD:
                                    logger.info(
                                        f"[OPT CANDLE CLOSE #{candle_number}] Fixed {index_name} | Strike={strike} Expiry={expiry} | "
                                        f"CE O={self._ce_open:.2f} H={self._ce_high:.2f} L={self._ce_low:.2f} C={self._ce_close:.2f} | "
                                        f"PE O={self._pe_open:.2f} H={self._pe_high:.2f} L={self._pe_low:.2f} C={self._pe_close:.2f}"
                                    )

                        else:
                            # Index-candle signals (current behavior)
                            if strategy_mode == 'supertrend':
                                # Simple fallback: trade only on ST flip
                                if self.current_position is None:
                                    if supertrend_flipped and st_direction == 1:
                                        action = AgentAction.ENTER_CE
                                    elif supertrend_flipped and st_direction == -1:
                                        action = AgentAction.ENTER_PE
                                else:
                                    if supertrend_flipped:
                                        action = AgentAction.EXIT
                            else:
                                # Default: ST + ADX + MACD agent
                                self.apply_strategy_config()
                                inputs = AgentInputs(
                                    timestamp=datetime.now(timezone.utc).isoformat(),
                                    open=float(open_price),
                                    high=float(high),
                                    low=float(low),
                                    close=float(close),
                                    supertrend_direction=st_direction if st_direction in (1, -1) else None,
                                    supertrend_flipped=bool(supertrend_flipped),
                                    adx_value=float(adx_value) if isinstance(adx_value, (int, float)) else None,
                                    macd_current=float(macd_current) if isinstance(macd_current, (int, float)) else None,
                                    macd_previous=float(macd_previous) if isinstance(macd_previous, (int, float)) else None,
                                    in_position=self.current_position is not None,
                                    current_position_side=(self.current_position.get('option_type') if self.current_position else None),
                                )
                                action = self.strategy_agent.decide(inputs)

                        self._try_persist_agent_state()

                        # Prevent immediate re-trade within same candle after exit
                        can_trade = True
                        if self.last_exit_candle_time:
                            time_since_exit = (current_candle_time - self.last_exit_candle_time).total_seconds()
                            if time_since_exit < candle_interval:
                                can_trade = False

                        if can_trade:
                            if signal_source == 'option_fixed' and action in (AgentAction.ENTER_CE, AgentAction.ENTER_PE):
                                strike = bot_state.get('fixed_option_strike')
                                expiry = bot_state.get('fixed_option_expiry')
                                ce_sid = bot_state.get('fixed_ce_security_id')
                                pe_sid = bot_state.get('fixed_pe_security_id')

                                security_id = ce_sid if action == AgentAction.ENTER_CE else pe_sid
                                if strike and expiry and security_id:
                                    await self.process_agent_action_on_close_fixed_contract(
                                        action,
                                        close,
                                        strike=int(strike),
                                        expiry=str(expiry),
                                        security_id=str(security_id),
                                    )
                                    self.last_trade_time = datetime.now()
                            else:
                                exited = await self.process_agent_action_on_close(action, close)
                                if exited:
                                    self.last_exit_candle_time = current_candle_time
                    
                    # Reset candle for next period
                    candle_start_time = datetime.now()
                    open_price, high, low, close = 0.0, 0, float('inf'), 0

                    # Reset option candle builders
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
                "adx_value": bot_state.get('adx_value', 0.0),
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
                "strategy_mode": bot_state.get('strategy_mode', config.get('strategy_mode', 'agent')),
                "signal_source": bot_state.get('signal_source', config.get('signal_source', 'index')),
                "fixed_option_strike": bot_state.get('fixed_option_strike'),
                "fixed_option_expiry": bot_state.get('fixed_option_expiry'),
                "fixed_ce_security_id": bot_state.get('fixed_ce_security_id'),
                "fixed_pe_security_id": bot_state.get('fixed_pe_security_id'),
                "signal_ce_ltp": bot_state.get('signal_ce_ltp', 0.0),
                "signal_pe_ltp": bot_state.get('signal_pe_ltp', 0.0),
                "agent_wave_lock": getattr(getattr(self, 'strategy_agent', None), 'wave_lock', None),
                "agent_last_trade_side": getattr(getattr(self, 'strategy_agent', None), 'last_trade_side', None),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        })
    
    async def check_trailing_sl(self, current_ltp: float):
        """Update SL values - initial fixed SL then trails profit using step-based method"""
        if not self.current_position:
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
        await self.check_trailing_sl(current_ltp)
        
        # Check if trailing SL is breached
        if self.trailing_sl and current_ltp <= self.trailing_sl:
            pnl = (current_ltp - self.entry_price) * qty
            logger.info(f"[EXIT] Trailing SL hit (tick) | LTP={current_ltp:.2f} | SL={self.trailing_sl:.2f}")
            await self.close_position(current_ltp, pnl, "Trailing SL Hit")
            return True
        
        return False
    
    async def process_agent_action_on_close(self, action: AgentAction, index_ltp: float) -> bool:
        """Map strategy-agent action to existing enter/exit plumbing."""
        if action == AgentAction.HOLD:
            return False

        exited = False
        index_name = config['selected_index']
        index_config = get_index_config(index_name)
        qty = config['order_qty'] * index_config['lot_size']

        if action == AgentAction.EXIT:
            if not self.current_position:
                return False

            exit_price = bot_state['current_option_ltp']
            pnl = (exit_price - self.entry_price) * qty
            logger.info(f"[AGENT] EXIT | Reason=AgentDecision | P&L=₹{pnl:.2f}")
            await self.close_position(exit_price, pnl, "Agent Exit")
            return True

        # ENTER actions
        if self.current_position:
            return False

        if not can_take_new_trade():
            return False

        if bot_state['daily_trades'] >= config['max_trades_per_day']:
            logger.info(f"[AGENT] Entry blocked - max daily trades reached ({config['max_trades_per_day']})")
            return False

        min_gap = config.get('min_trade_gap', 0)
        if min_gap > 0 and self.last_trade_time:
            time_since_last = (datetime.now() - self.last_trade_time).total_seconds()
            if time_since_last < min_gap:
                logger.debug(f"[AGENT] Entry blocked - min trade gap not met ({time_since_last:.1f}s < {min_gap}s)")
                return False

        option_type = None
        if action == AgentAction.ENTER_CE:
            option_type = 'CE'
        elif action == AgentAction.ENTER_PE:
            option_type = 'PE'
        else:
            return False

        atm_strike = round_to_strike(index_ltp, index_name)
        logger.info(
            f"[AGENT] ENTER {option_type} | Index={index_name} | LTP={index_ltp:.2f} | ATM={atm_strike} | "
            f"ST={bot_state['supertrend_value']:.2f} | MACD={bot_state['macd_value']:.4f}"
        )

        await self.enter_position(option_type, atm_strike, index_ltp)
        self.last_trade_time = datetime.now()
        return exited

    async def process_agent_action_on_close_fixed_contract(
        self,
        action: AgentAction,
        index_ltp: float,
        *,
        strike: int,
        expiry: str,
        security_id: str,
    ) -> bool:
        """Same as process_agent_action_on_close, but uses a fixed contract."""
        if action == AgentAction.HOLD:
            return False

        index_name = config['selected_index']
        index_config = get_index_config(index_name)
        qty = config['order_qty'] * index_config['lot_size']

        if action == AgentAction.EXIT:
            if not self.current_position:
                return False
            exit_price = bot_state['current_option_ltp']
            pnl = (exit_price - self.entry_price) * qty
            logger.info(f"[AGENT] EXIT | Reason=AgentDecision | P&L=₹{pnl:.2f}")
            await self.close_position(exit_price, pnl, "Agent Exit")
            return True

        if self.current_position:
            return False

        if not can_take_new_trade():
            return False

        if bot_state['daily_trades'] >= config['max_trades_per_day']:
            logger.info(f"[AGENT] Entry blocked - max daily trades reached ({config['max_trades_per_day']})")
            return False

        option_type = None
        if action == AgentAction.ENTER_CE:
            option_type = 'CE'
        elif action == AgentAction.ENTER_PE:
            option_type = 'PE'
        else:
            return False

        logger.info(
            f"[AGENT] ENTER {option_type} (fixed contract) | Index={index_name} | IndexLTP={index_ltp:.2f} | Strike={strike} | Expiry={expiry}"
        )
        await self.enter_position(option_type, int(strike), index_ltp, expiry_override=str(expiry), security_id_override=str(security_id))
        self.last_trade_time = datetime.now()
        return False
    
    async def enter_position(self, option_type: str, strike: int, index_ltp: float, *, expiry_override: str | None = None, security_id_override: str | None = None):
        """Enter a new position with market validation"""
        # CRITICAL: Double-check market is open before entering
        if not is_market_open():
            logger.warning(f"[ENTRY] ✗ BLOCKED - Market is CLOSED | Cannot enter {option_type} position")
            return
        
        # CHECK: Trading hours protection
        if not self.is_within_trading_hours():
            logger.warning(f"[ENTRY] ✗ BLOCKED - Outside trading hours | Cannot enter {option_type} position")
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
