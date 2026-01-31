import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

from indicators import ADX, MACD, SuperTrend
from strategy_agent import AgentAction, AgentInputs, STAdxMacdAgent
from time_utils import iso_to_ist_iso

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    side: str  # 'CE' or 'PE'
    entry_time: str
    exit_time: Optional[str]
    entry_price: float
    exit_price: Optional[float]
    pnl_points: Optional[float]
    exit_reason: Optional[str]


def _calc_points_pnl(side: str, entry: float, exit_: float) -> float:
    # CE profits when index rises, PE profits when index falls
    if side == "CE":
        return exit_ - entry
    return entry - exit_


def _equity_max_drawdown(equity: List[float]) -> float:
    running_max = float("-inf")
    max_dd = 0.0
    for x in equity:
        running_max = max(running_max, x)
        max_dd = max(max_dd, running_max - x)
    return max_dd


def _parse_iso_to_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    s = value.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _floor_to_timeframe_ist(dt_utc: datetime, timeframe_minutes: int) -> datetime:
    """Bucket a UTC datetime into IST timeframe boundaries."""
    # Convert to IST by adding +05:30 (keeps offset-aware semantics simple)
    ist = _parse_iso_to_dt(iso_to_ist_iso(dt_utc.astimezone(timezone.utc).isoformat()) or "")
    if ist is None:
        # fallback: treat as UTC
        ist = dt_utc

    minute = (ist.minute // timeframe_minutes) * timeframe_minutes
    return ist.replace(minute=minute, second=0, microsecond=0)


def _resample_candles(candles: List[Dict[str, Any]], timeframe_minutes: int) -> List[Dict[str, Any]]:
    if timeframe_minutes <= 1:
        return candles
    if not candles:
        return candles

    out: List[Dict[str, Any]] = []
    bucket_key: Optional[datetime] = None
    bucket: Optional[Dict[str, Any]] = None

    for row in candles:
        ts_raw = str(row.get("timestamp") or row.get("created_at") or "")
        dt = _parse_iso_to_dt(ts_raw)
        if dt is None:
            # If timestamp is unparseable, skip (keeps ordering safe)
            continue

        key = _floor_to_timeframe_ist(dt.astimezone(timezone.utc), timeframe_minutes)

        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        open_ = float(row.get("open", close))

        if bucket_key is None or key != bucket_key:
            if bucket is not None:
                out.append(bucket)

            bucket_key = key
            bucket = {
                "timestamp": key.isoformat(),
                "high": high,
                "low": low,
                "close": close,
                "open": open_,
            }
            continue

        # Update existing bucket
        bucket["high"] = max(float(bucket["high"]), high)
        bucket["low"] = min(float(bucket["low"]), low)
        bucket["close"] = close

    if bucket is not None:
        out.append(bucket)

    return out


def run_backtest(
    candles: List[Dict[str, Any]],
    *,
    strategy_mode: str = "agent",
    timeframe_minutes: int = 0,
    supertrend_period: int = 7,
    supertrend_multiplier: float = 4.0,
    agent_adx_min: float = 20.0,
    agent_wave_reset_macd_abs: float = 0.05,
    initial_stoploss: float = 0.0,
    target_points: float = 0.0,
    trail_start_profit: float = 0.0,
    trail_step: float = 0.0,
    close_open_position_at_end: bool = True,
) -> Dict[str, Any]:
    """Replay historical candles and simulate entries/exits.

    Notes:
    - Uses index close as the fill price (points-PnL, not options rupees-PnL).
    - Does NOT touch live trading state or place any orders.
    """

    mode = (strategy_mode or "agent").strip().lower()
    if mode not in ("agent", "supertrend"):
        raise ValueError("strategy_mode must be 'agent' or 'supertrend'")

    tf = int(timeframe_minutes or 0)
    if tf < 0:
        raise ValueError("timeframe_minutes must be >= 0")

    base_candle_count = len(candles)
    candles = _resample_candles(candles, tf)

    st = SuperTrend(period=supertrend_period, multiplier=supertrend_multiplier)
    macd = MACD()
    adx = ADX()

    agent = STAdxMacdAgent(adx_min=float(agent_adx_min), wave_reset_macd_abs=float(agent_wave_reset_macd_abs))
    agent.reset_session("BACKTEST")

    last_st_dir: Optional[int] = None
    last_macd: Optional[float] = None

    position_side: Optional[str] = None
    entry_time: Optional[str] = None
    entry_price: Optional[float] = None
    best_favorable_points: float = 0.0

    trades: List[BacktestTrade] = []
    equity_curve: List[float] = [0.0]
    equity = 0.0

    for row in candles:
        ts_raw = str(row.get("timestamp") or row.get("created_at") or "")
        ts = iso_to_ist_iso(ts_raw) or ts_raw
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        st_val, st_signal = st.add_candle(high, low, close)
        macd_val, _ = macd.add_candle(high, low, close)
        adx_val, _ = adx.add_candle(high, low, close)

        # Need enough warmup for indicators
        if st_val is None or macd_val is None or adx_val is None:
            last_macd = macd_val if macd_val is not None else last_macd
            last_st_dir = st.direction if st_val is not None else last_st_dir
            continue

        st_dir = st.direction
        supertrend_flipped = bool(last_st_dir in (1, -1) and st_dir in (1, -1) and last_st_dir != st_dir)

        # ---------- RISK EXITS (simulated on OHLC, before strategy decision) ----------
        # Backtest uses index OHLC as proxy; this is points-based and not rupee-accurate.
        if position_side and entry_price is not None:
            stoploss_points = float(initial_stoploss or 0.0)
            tgt_points = float(target_points or 0.0)
            trail_start = float(trail_start_profit or 0.0)
            trail_step_points = float(trail_step or 0.0)

            exit_reason: Optional[str] = None
            exit_price: Optional[float] = None

            if position_side == "CE":
                best_favorable_points = max(best_favorable_points, high - entry_price)

                # Stop level(s)
                initial_stop_level = (entry_price - stoploss_points) if stoploss_points > 0 else None

                trailing_level = None
                if trail_start > 0 and trail_step_points > 0 and best_favorable_points >= trail_start:
                    steps = int((best_favorable_points - trail_start) / trail_step_points)
                    trailing_level = entry_price + (steps * trail_step_points)

                effective_stop = None
                if initial_stop_level is not None and trailing_level is not None:
                    effective_stop = max(initial_stop_level, trailing_level)
                else:
                    effective_stop = trailing_level if trailing_level is not None else initial_stop_level

                # Worst-case priority: stop first, then target
                if effective_stop is not None and low <= effective_stop:
                    exit_price = float(effective_stop)
                    exit_reason = "Trailing SL Hit" if trailing_level is not None and effective_stop == trailing_level else "Stoploss Hit"
                elif tgt_points > 0 and high >= (entry_price + tgt_points):
                    exit_price = float(entry_price + tgt_points)
                    exit_reason = "Target Hit"

            elif position_side == "PE":
                best_favorable_points = max(best_favorable_points, entry_price - low)

                initial_stop_level = (entry_price + stoploss_points) if stoploss_points > 0 else None

                trailing_level = None
                if trail_start > 0 and trail_step_points > 0 and best_favorable_points >= trail_start:
                    steps = int((best_favorable_points - trail_start) / trail_step_points)
                    trailing_level = entry_price - (steps * trail_step_points)

                effective_stop = None
                if initial_stop_level is not None and trailing_level is not None:
                    effective_stop = min(initial_stop_level, trailing_level)
                else:
                    effective_stop = trailing_level if trailing_level is not None else initial_stop_level

                if effective_stop is not None and high >= effective_stop:
                    exit_price = float(effective_stop)
                    exit_reason = "Trailing SL Hit" if trailing_level is not None and effective_stop == trailing_level else "Stoploss Hit"
                elif tgt_points > 0 and low <= (entry_price - tgt_points):
                    exit_price = float(entry_price - tgt_points)
                    exit_reason = "Target Hit"

            if exit_reason and exit_price is not None:
                pnl = _calc_points_pnl(position_side, entry_price, exit_price)
                equity += pnl
                equity_curve.append(equity)

                trades[-1].exit_time = ts
                trades[-1].exit_price = exit_price
                trades[-1].pnl_points = pnl
                trades[-1].exit_reason = exit_reason

                position_side = None
                entry_time = None
                entry_price = None
                best_favorable_points = 0.0

                last_macd = float(macd_val)
                last_st_dir = st_dir
                continue

        action = AgentAction.HOLD
        if mode == "agent":
            inputs = AgentInputs(
                timestamp=ts,
                open=float(row.get("open", close)),
                high=high,
                low=low,
                close=close,
                supertrend_direction=st_dir if st_dir in (1, -1) else None,
                supertrend_flipped=supertrend_flipped,
                adx_value=float(adx_val) if isinstance(adx_val, (int, float)) else None,
                macd_current=float(macd_val) if isinstance(macd_val, (int, float)) else None,
                macd_previous=float(last_macd) if isinstance(last_macd, (int, float)) else None,
                in_position=bool(position_side),
                current_position_side=position_side,
            )
            action = agent.decide(inputs)
        else:
            # SuperTrend flip baseline
            if not position_side:
                if supertrend_flipped and st_dir == 1:
                    action = AgentAction.ENTER_CE
                elif supertrend_flipped and st_dir == -1:
                    action = AgentAction.ENTER_PE
            else:
                # Exit on opposite flip
                if supertrend_flipped:
                    action = AgentAction.EXIT

        # Apply action at close
        if action in (AgentAction.ENTER_CE, AgentAction.ENTER_PE) and not position_side:
            position_side = "CE" if action == AgentAction.ENTER_CE else "PE"
            entry_time = ts
            entry_price = close
            best_favorable_points = 0.0
            trades.append(
                BacktestTrade(
                    side=position_side,
                    entry_time=entry_time,
                    exit_time=None,
                    entry_price=entry_price,
                    exit_price=None,
                    pnl_points=None,
                    exit_reason=None,
                )
            )

        elif action == AgentAction.EXIT and position_side and entry_price is not None:
            exit_price = close
            pnl = _calc_points_pnl(position_side, entry_price, exit_price)
            equity += pnl
            equity_curve.append(equity)

            # close last trade
            trades[-1].exit_time = ts
            trades[-1].exit_price = exit_price
            trades[-1].pnl_points = pnl
            trades[-1].exit_reason = "Agent Exit" if mode == "agent" else "Flip Exit"

            position_side = None
            entry_time = None
            entry_price = None
            best_favorable_points = 0.0

        last_macd = float(macd_val)
        last_st_dir = st_dir

    if close_open_position_at_end and position_side and entry_price is not None and candles:
        last = candles[-1]
        ts_raw = str(last.get("timestamp") or last.get("created_at") or "")
        ts = iso_to_ist_iso(ts_raw) or ts_raw
        close = float(last["close"])
        pnl = _calc_points_pnl(position_side, entry_price, close)
        equity += pnl
        equity_curve.append(equity)

        trades[-1].exit_time = ts
        trades[-1].exit_price = close
        trades[-1].pnl_points = pnl
        trades[-1].exit_reason = "EOD"

    closed_trades = [t for t in trades if t.pnl_points is not None]
    total_trades = len(closed_trades)
    total_pnl = sum(t.pnl_points or 0.0 for t in closed_trades)
    wins = [t for t in closed_trades if (t.pnl_points or 0.0) > 0]
    losses = [t for t in closed_trades if (t.pnl_points or 0.0) < 0]

    win_rate = (len(wins) / total_trades * 100.0) if total_trades else 0.0
    avg_win = (sum(t.pnl_points for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (abs(sum(t.pnl_points for t in losses)) / len(losses)) if losses else 0.0
    max_dd = _equity_max_drawdown(equity_curve)

    return {
        "meta": {
            "strategy_mode": mode,
            "candles": len(candles),
            "base_candles": base_candle_count,
            "timeframe_minutes": tf,
            "close_open_position_at_end": close_open_position_at_end,
        },
        "params": {
            "supertrend_period": supertrend_period,
            "supertrend_multiplier": supertrend_multiplier,
            "agent_adx_min": agent_adx_min,
            "agent_wave_reset_macd_abs": agent_wave_reset_macd_abs,
            "initial_stoploss": float(initial_stoploss or 0.0),
            "target_points": float(target_points or 0.0),
            "trail_start_profit": float(trail_start_profit or 0.0),
            "trail_step": float(trail_step or 0.0),
        },
        "metrics": {
            "total_trades": total_trades,
            "total_pnl_points": round(total_pnl, 2),
            "win_rate": round(win_rate, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_drawdown": round(max_dd, 2),
        },
        "trades": [
            {
                "side": t.side,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": round(float(t.entry_price), 2) if t.entry_price is not None else None,
                "exit_price": round(float(t.exit_price), 2) if t.exit_price is not None else None,
                "pnl_points": round(float(t.pnl_points), 2) if t.pnl_points is not None else None,
                "exit_reason": t.exit_reason,
            }
            for t in closed_trades
        ],
    }
