# SuperTrend Indicator
import logging

logger = logging.getLogger(__name__)

class SuperTrend:
    def __init__(self, period=7, multiplier=4):
        self.period = period
        self.multiplier = multiplier
        self.candles = []
        self.atr_values = []
        self.supertrend_values = []
        self.direction = 1  # 1 = GREEN (bullish), -1 = RED (bearish)
    
    def reset(self):
        """Reset indicator state"""
        self.candles = []
        self.atr_values = []
        self.supertrend_values = []
        self.direction = 1
    
    def add_candle(self, high, low, close):
        """Add a new candle and calculate SuperTrend"""
        self.candles.append({'high': high, 'low': low, 'close': close})
        
        if len(self.candles) < self.period:
            return None, None
        
        # Calculate True Range
        tr = max(
            high - low,
            abs(high - self.candles[-2]['close']) if len(self.candles) > 1 else 0,
            abs(low - self.candles[-2]['close']) if len(self.candles) > 1 else 0
        )
        
        # Calculate ATR
        if len(self.atr_values) == 0:
            # Initial ATR is simple average of TR
            trs = []
            for i in range(max(0, len(self.candles) - self.period), len(self.candles)):
                if i > 0:
                    prev = self.candles[i-1]
                    curr = self.candles[i]
                    tr_val = max(
                        curr['high'] - curr['low'],
                        abs(curr['high'] - prev['close']),
                        abs(curr['low'] - prev['close'])
                    )
                else:
                    tr_val = self.candles[i]['high'] - self.candles[i]['low']
                trs.append(tr_val)
            atr = sum(trs) / len(trs) if trs else 0
        else:
            atr = (self.atr_values[-1] * (self.period - 1) + tr) / self.period
        
        self.atr_values.append(atr)
        
        # Calculate basic upper and lower bands
        hl2 = (high + low) / 2
        basic_upper = hl2 + (self.multiplier * atr)
        basic_lower = hl2 - (self.multiplier * atr)
        
        # Final bands calculation
        if len(self.supertrend_values) == 0:
            final_upper = basic_upper
            final_lower = basic_lower
        else:
            prev = self.supertrend_values[-1]
            prev_close = self.candles[-2]['close']
            
            final_lower = basic_lower if basic_lower > prev['lower'] or prev_close < prev['lower'] else prev['lower']
            final_upper = basic_upper if basic_upper < prev['upper'] or prev_close > prev['upper'] else prev['upper']
        
        # Direction
        if len(self.supertrend_values) == 0:
            direction = 1 if close > final_upper else -1
        else:
            prev = self.supertrend_values[-1]
            if prev['direction'] == 1:
                direction = -1 if close < final_lower else 1
            else:
                direction = 1 if close > final_upper else -1
        
        self.direction = direction
        supertrend_value = final_lower if direction == 1 else final_upper
        
        self.supertrend_values.append({
            'upper': final_upper,
            'lower': final_lower,
            'value': supertrend_value,
            'direction': direction
        })
        
        # Keep only last 100 values
        if len(self.candles) > 100:
            self.candles = self.candles[-100:]
            self.atr_values = self.atr_values[-100:]
            self.supertrend_values = self.supertrend_values[-100:]
        
        signal = "GREEN" if direction == 1 else "RED"
        return supertrend_value, signal
