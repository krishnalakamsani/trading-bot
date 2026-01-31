import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from indicators import ADX, MACD, SuperTrend
from strategy_agent import AgentAction, AgentInputs, STAdxMacdAgent

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


def run_backtest(
    candles: List[Dict[str, Any]],
    *,
    strategy_mode: str = "agent",
    supertrend_period: int = 7,
    supertrend_multiplier: float = 4.0,
    agent_adx_min: float = 20.0,
    agent_wave_reset_macd_abs: float = 0.05,
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

    trades: List[BacktestTrade] = []
    equity_curve: List[float] = [0.0]
    equity = 0.0

    for row in candles:
        ts = str(row.get("timestamp") or row.get("created_at") or "")
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
            trades[-1].exit_reason = "EXIT"

            position_side = None
            entry_time = None
            entry_price = None

        last_macd = float(macd_val)
        last_st_dir = st_dir

    if close_open_position_at_end and position_side and entry_price is not None and candles:
        last = candles[-1]
        ts = str(last.get("timestamp") or last.get("created_at") or "")
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
            "close_open_position_at_end": close_open_position_at_end,
        },
        "params": {
            "supertrend_period": supertrend_period,
            "supertrend_multiplier": supertrend_multiplier,
            "agent_adx_min": agent_adx_min,
            "agent_wave_reset_macd_abs": agent_wave_reset_macd_abs,
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
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl_points": t.pnl_points,
                "exit_reason": t.exit_reason,
            }
            for t in closed_trades
        ],
    }
