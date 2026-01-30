# Dhan API wrapper
from dhanhq import dhanhq
from datetime import datetime, timezone, timedelta
import logging
from config import bot_state
from indices import get_index_config

logger = logging.getLogger(__name__)

class DhanAPI:
    def __init__(self, access_token: str, client_id: str):
        self.access_token = access_token
        self.client_id = client_id
        self.dhan = dhanhq(client_id, access_token)
        # Cache for option chain to avoid rate limiting
        self._option_chain_cache = {}
        self._option_chain_cache_time = {}
        self._cache_duration = 60  # Default cache for 60 seconds
        self._position_cache_duration = 10  # Shorter cache when position is open
    
    def get_index_ltp(self, index_name: str = "NIFTY") -> float:
        """Get index spot LTP"""
        try:
            index_config = get_index_config(index_name)
            security_id = index_config["security_id"]
            segment = index_config["exchange_segment"]
            
            # For SENSEX, try multiple segments as Dhan API may vary
            segments_to_try = [segment]
            if index_name == "SENSEX":
                segments_to_try = ["IDX_I", "BSE_INDEX", "BSE"]
            
            for seg in segments_to_try:
                response = self.dhan.quote_data({
                    seg: [security_id]
                })
                
                if response and response.get('status') == 'success':
                    data = response.get('data', {})
                    if isinstance(data, dict) and 'data' in data:
                        data = data.get('data', {})
                    
                    idx_data = data.get(seg, {}).get(str(security_id), {})
                    if idx_data:
                        ltp = idx_data.get('last_price')
                        if ltp and ltp > 0:
                            logger.debug(f"Got {index_name} LTP: {ltp} from segment {seg}")
                            return float(ltp)
                        ohlc = idx_data.get('ohlc', {})
                        if ohlc and ohlc.get('close'):
                            return float(ohlc.get('close'))
                    
        except Exception as e:
            logger.error(f"Error fetching {index_name} LTP: {e}")
        return 0
    
    def get_index_and_option_ltp(self, index_name: str, option_security_id: int) -> tuple:
        """Get both Index and Option LTP in a single API call"""
        index_ltp = 0
        option_ltp = 0
        
        try:
            index_config = get_index_config(index_name)
            security_id = index_config["security_id"]
            segment = index_config["exchange_segment"]
            fno_segment = index_config.get("fno_segment", "NSE_FNO")
            
            # Fetch both in single call to avoid rate limits
            response = self.dhan.quote_data({
                segment: [security_id],
                fno_segment: [option_security_id]
            })
            
            if response and response.get('status') == 'success':
                data = response.get('data', {})
                if isinstance(data, dict) and 'data' in data:
                    data = data.get('data', {})
                
                # Get Index LTP
                idx_data = data.get(segment, {}).get(str(security_id), {})
                if idx_data:
                    index_ltp = float(idx_data.get('last_price', 0))
                
                # Get Option LTP
                fno_data = data.get(fno_segment, {}).get(str(option_security_id), {})
                if fno_data:
                    option_ltp = float(fno_data.get('last_price', 0))
                    
                logger.info(f"Quote: {index_name}={index_ltp}, Option {option_security_id}={option_ltp}")
                    
        except Exception as e:
            logger.error(f"Error fetching combined quote: {e}")
        
        return index_ltp, option_ltp
    
    async def get_option_chain(self, index_name: str = "NIFTY", expiry: str = None, force_refresh: bool = False) -> dict:
        """Get option chain with caching"""
        try:
            index_config = get_index_config(index_name)
            security_id = index_config["security_id"]
            
            if not expiry:
                expiry = await self.get_nearest_expiry(index_name)
            
            if not expiry:
                logger.error("Could not determine expiry date")
                return {}
            
            # Check cache
            cache_key = f"{index_name}_{expiry}"
            now = datetime.now()
            
            cache_duration = self._position_cache_duration if bot_state.get('current_position') else self._cache_duration
            
            cache_time = self._option_chain_cache_time.get(cache_key)
            if (not force_refresh and 
                self._option_chain_cache.get(cache_key) and 
                cache_time and 
                (now - cache_time).seconds < cache_duration):
                return self._option_chain_cache[cache_key]
            
            logger.info(f"Fetching fresh option chain: {index_name}, expiry={expiry}")
            
            response = self.dhan.option_chain(
                under_security_id=security_id,
                under_exchange_segment='IDX_I',
                expiry=expiry
            )
            
            if response and response.get('status') == 'success':
                self._option_chain_cache[cache_key] = response
                self._option_chain_cache_time[cache_key] = now
                logger.info(f"Option chain cached at {now.strftime('%H:%M:%S')}")
            
            return response if response else {}
        except Exception as e:
            logger.error(f"Error fetching option chain: {e}")
        return {}
    
    async def get_nearest_expiry(self, index_name: str = "NIFTY") -> str:
        """Get nearest expiry date"""
        try:
            index_config = get_index_config(index_name)
            security_id = index_config["security_id"]
            
            for segment in ['IDX_I', 'NSE_FNO', 'INDEX']:
                logger.info(f"Trying expiry_list for {index_name} with segment: {segment}")
                response = self.dhan.expiry_list(
                    under_security_id=security_id,
                    under_exchange_segment=segment
                )
                logger.info(f"Expiry list response: {response}")
                
                if response and response.get('status') == 'success':
                    data = response.get('data', {})
                    if isinstance(data, dict) and 'data' in data:
                        expiries = data.get('data', [])
                    elif isinstance(data, list):
                        expiries = data
                    else:
                        expiries = []
                    
                    if expiries and isinstance(expiries, list):
                        today = datetime.now().date()
                        
                        valid_expiries = []
                        for exp in expiries:
                            try:
                                if isinstance(exp, str):
                                    if '-' in exp:
                                        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                                    elif '/' in exp:
                                        exp_date = datetime.strptime(exp, "%d/%m/%Y").date()
                                    else:
                                        continue
                                    
                                    if exp_date >= today:
                                        valid_expiries.append((exp_date, exp))
                            except ValueError:
                                continue
                        
                        if valid_expiries:
                            valid_expiries.sort(key=lambda x: x[0])
                            nearest = valid_expiries[0][1]
                            logger.info(f"Nearest expiry for {index_name}: {nearest}")
                            return nearest
            
            logger.warning(f"Could not get expiry list from API for {index_name}")
        except Exception as e:
            logger.error(f"Error getting expiry list: {e}")
        
        # Fallback: calculate based on index expiry day
        index_config = get_index_config(index_name)
        expiry_day = index_config["expiry_day"]
        
        ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        days_until_expiry = (expiry_day - ist.weekday()) % 7
        if days_until_expiry == 0:
            if ist.hour >= 15 and ist.minute >= 30:
                days_until_expiry = 7
        expiry_date = ist + timedelta(days=days_until_expiry)
        calculated_expiry = expiry_date.strftime("%Y-%m-%d")
        logger.info(f"Using calculated expiry for {index_name}: {calculated_expiry}")
        return calculated_expiry
    
    async def get_atm_option_security_id(self, index_name: str, strike: int, option_type: str, expiry: str = None) -> str:
        """Get security ID for ATM option"""
        try:
            if not expiry:
                expiry = await self.get_nearest_expiry(index_name)
            
            chain = await self.get_option_chain(index_name=index_name, expiry=expiry)
            
            if chain and chain.get('status') == 'success':
                data = chain.get('data', {})
                if isinstance(data, dict) and 'data' in data:
                    data = data.get('data', {})
                
                oc_data = data.get('oc', {})
                
                strike_keys = [
                    f"{strike}.000000",
                    f"{strike}.0",
                    str(strike),
                    float(strike),
                ]
                
                for strike_key in strike_keys:
                    strike_data = oc_data.get(str(strike_key), {})
                    if strike_data:
                        opt_key = 'ce' if option_type.upper() == 'CE' else 'pe'
                        opt_data = strike_data.get(opt_key, {})
                        
                        if opt_data:
                            security_id = opt_data.get('security_id')
                            if security_id:
                                logger.info(f"Found security ID {security_id} for {index_name} {strike} {option_type}")
                                return str(security_id)
                
                available_strikes = list(oc_data.keys())[:10]
                logger.warning(f"Strike {strike} not found. Available: {available_strikes}")
            
            logger.warning(f"Could not find security ID for {index_name} {strike} {option_type}")
        except Exception as e:
            logger.error(f"Error getting ATM option security ID: {e}")
        return ""
    
    async def get_option_ltp(self, security_id: str, strike: int = None, option_type: str = None, expiry: str = None, index_name: str = "NIFTY") -> float:
        """Get option LTP from cache or API"""
        try:
            index_config = get_index_config(index_name)
            fno_segment = index_config.get("fno_segment", "NSE_FNO")
            
            # First try from cached option chain
            if strike and option_type:
                cache_key = f"{index_name}_{expiry}" if expiry else None
                if cache_key and self._option_chain_cache.get(cache_key):
                    chain = self._option_chain_cache[cache_key]
                    data = chain.get('data', {})
                    if isinstance(data, dict) and 'data' in data:
                        data = data.get('data', {})
                    
                    oc_data = data.get('oc', {})
                    strike_key = f"{strike}.000000"
                    strike_data = oc_data.get(strike_key, {})
                    
                    if strike_data:
                        opt_key = 'ce' if option_type.upper() == 'CE' else 'pe'
                        opt_data = strike_data.get(opt_key, {})
                        ltp = opt_data.get('last_price', 0)
                        if ltp and ltp > 0:
                            logger.info(f"Got option LTP from cache: {index_name} {strike} {option_type} = {ltp}")
                            return float(ltp)
            
            # Fallback: Make API call
            logger.info(f"Fetching option LTP for security_id: {security_id}")
            response = self.dhan.quote_data({
                fno_segment: [int(security_id)]
            })
            
            if response and response.get('status') == 'success':
                data = response.get('data', {})
                if isinstance(data, dict) and 'data' in data:
                    data = data.get('data', {})
                
                fno_data = data.get(fno_segment, {}).get(str(security_id), {})
                if fno_data:
                    ltp = fno_data.get('last_price')
                    if ltp and ltp > 0:
                        return float(ltp)
                        
        except Exception as e:
            logger.error(f"Error fetching option LTP: {e}")
        return 0
    
    async def place_order(self, security_id: str, transaction_type: str, qty: int) -> dict:
        """Place a market order synchronously (Dhan API is synchronous)"""
        try:
            # Dhan API is synchronous, call it directly
            response = self.dhan.place_order(
                security_id=security_id,
                exchange_segment=self.dhan.NSE_FNO,
                transaction_type=self.dhan.BUY if transaction_type == "BUY" else self.dhan.SELL,
                quantity=qty,
                order_type=self.dhan.MARKET,
                product_type=self.dhan.INTRA,
                price=0
            )
            
            # Validate response
            if not response:
                logger.error(f"[ORDER] Empty response from Dhan API for {transaction_type} order, qty={qty}")
                return {"status": "error", "message": "Empty response from Dhan", "orderId": None}
            
            logger.debug(f"[ORDER] Raw Dhan {transaction_type} response: {response}")
            
            # Dhan API returns order details in response
            # Check for success indicators
            if isinstance(response, dict):
                # Check if it's a success response (has order_id or status=success)
                order_id = response.get('orderId') or response.get('order_id') or response.get('id')
                status = response.get('status')
                
                if order_id:
                    logger.info(f"[ORDER] {transaction_type} order placed successfully | Order ID: {order_id} | Security: {security_id} | Qty: {qty}")
                    return {
                        "status": "success",
                        "orderId": order_id,
                        "price": response.get('price') or response.get('averagePrice') or 0,
                        "quantity": response.get('quantity') or qty,
                        "data": response
                    }
                elif status == 'success':
                    logger.info(f"[ORDER] {transaction_type} order placed successfully | Response: {response}")
                    return {
                        "status": "success",
                        "orderId": response.get('data', {}).get('orderId', 'UNKNOWN'),
                        "price": response.get('data', {}).get('price') or 0,
                        "quantity": qty,
                        "data": response
                    }
            
            logger.error(f"[ORDER] Unexpected response format for {transaction_type}: {response}")
            return {"status": "error", "message": f"Unexpected response: {response}", "orderId": None}
            
        except Exception as e:
            logger.error(f"[ORDER] Error placing {transaction_type} order: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "orderId": None}
    
    async def get_positions(self) -> list:
        """Get current positions"""
        try:
            response = self.dhan.get_positions()
            if response and 'data' in response:
                return response.get('data', [])
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
        return []    
    async def verify_order_filled(self, order_id: str, security_id: str, expected_qty: int, timeout_seconds: int = 30) -> dict:
        """Verify if an order was actually filled
        
        For LIVE mode: Dhan API takes time to update order list, so retry with longer timeout
        For PAPER mode: Returns quickly
        
        Returns:
            {
                "filled": bool,
                "order_id": str,
                "status": str,
                "filled_qty": int,
                "average_price": float,
                "message": str
            }
        """
        try:
            import asyncio
            start_time = datetime.now(timezone.utc)
            retry_count = 0
            
            while True:
                retry_count += 1
                # Check order status
                try:
                    orders = self.dhan.get_order_list()
                    if orders and 'data' in orders:
                        for order in orders['data']:
                            if str(order.get('orderId')) == str(order_id):
                                status = order.get('orderStatus', '').upper()
                                filled_qty = int(order.get('filledQty', 0))
                                average_price = float(order.get('averagePrice', 0))
                                
                                if status == 'FILLED':
                                    logger.info(f"[ORDER] ✓ Order {order_id} FILLED (attempt #{retry_count}) | Qty: {filled_qty} | Avg Price: {average_price}")
                                    return {
                                        "filled": True,
                                        "order_id": order_id,
                                        "status": "FILLED",
                                        "filled_qty": filled_qty,
                                        "average_price": average_price,
                                        "message": f"Order filled at {average_price}"
                                    }
                                elif status in ['PENDING', 'OPEN']:
                                    # Still pending, wait and retry
                                    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                                    logger.debug(f"[ORDER] Waiting for {order_id} to fill... Status: {status} (attempt #{retry_count}, {elapsed:.1f}s elapsed)")
                                    if elapsed > timeout_seconds:
                                        logger.warning(f"[ORDER] Order {order_id} timeout after {timeout_seconds}s | Status: {status} (attempt #{retry_count})")
                                        return {
                                            "filled": False,
                                            "order_id": order_id,
                                            "status": status,
                                            "filled_qty": filled_qty,
                                            "average_price": average_price,
                                            "message": f"Order pending after {timeout_seconds}s - may not fill"
                                        }
                                    await asyncio.sleep(0.5)
                                    continue
                                elif status == 'REJECTED':
                                    logger.error(f"[ORDER] ✗ Order {order_id} REJECTED (attempt #{retry_count}) | Reason: {order.get('reason', 'Unknown')}")
                                    return {
                                        "filled": False,
                                        "order_id": order_id,
                                        "status": "REJECTED",
                                        "filled_qty": 0,
                                        "average_price": 0,
                                        "message": f"Order rejected: {order.get('reason', 'Unknown')}"
                                    }
                                elif status == 'CANCELLED':
                                    logger.warning(f"[ORDER] ✗ Order {order_id} CANCELLED (attempt #{retry_count})")
                                    return {
                                        "filled": False,
                                        "order_id": order_id,
                                        "status": "CANCELLED",
                                        "filled_qty": filled_qty,
                                        "average_price": average_price,
                                        "message": "Order was cancelled"
                                    }
                except Exception as e:
                    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                    logger.debug(f"[ORDER] Error checking order status: {e} (attempt #{retry_count}, {elapsed:.1f}s elapsed)")
                    await asyncio.sleep(0.5)
                    continue
                
                # Order not found in list yet (might be too recent in live mode)
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                if elapsed > timeout_seconds:
                    logger.warning(f"[ORDER] ✗ Order {order_id} not found in system after {timeout_seconds}s (attempt #{retry_count}) - may still be pending")
                    return {
                        "filled": False,
                        "order_id": order_id,
                        "status": "NOT_FOUND",
                        "filled_qty": 0,
                        "average_price": 0,
                        "message": "Order not found in order list"
                    }
                
                await asyncio.sleep(0.5)
        
        except Exception as e:
            logger.error(f"[ORDER] Error verifying order fill: {e}", exc_info=True)
            return {
                "filled": False,
                "order_id": order_id,
                "status": "ERROR",
                "filled_qty": 0,
                "average_price": 0,
                "message": str(e)
            }