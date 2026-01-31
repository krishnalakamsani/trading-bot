from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class AgentAction(str, Enum):
    ENTER_CE = "ENTER_CE"
    ENTER_PE = "ENTER_PE"
    EXIT = "EXIT"
    HOLD = "HOLD"


PositionSide = Literal["CE", "PE"]


@dataclass(frozen=True)
class AgentInputs:
    timestamp: str
    open: float
    high: float
    low: float
    close: float

    supertrend_direction: Optional[int]  # +1 / -1
    supertrend_flipped: bool

    adx_value: Optional[float]

    macd_current: Optional[float]
    macd_previous: Optional[float]

    in_position: bool
    current_position_side: Optional[PositionSide]


class STAdxMacdAgent:
    """Strategy-only agent.

    Responsibilities:
    - Decide ENTER / EXIT / HOLD using SuperTrend + ADX + MACD.

    Non-responsibilities:
    - Fetching data
    - Placing orders
    - Managing SL/targets
    """

    def __init__(
        self,
        *,
        adx_min: float = 20.0,
        wave_reset_macd_abs: float = 0.05,
    ):
        self.adx_min = float(adx_min)
        self.wave_reset_macd_abs = float(wave_reset_macd_abs)

        self.wave_lock: bool = False
        self.last_trade_side: Optional[PositionSide] = None

    def reset_session(self, reason: str | None = None) -> None:
        self.wave_lock = False
        self.last_trade_side = None

    def decide(self, inputs: AgentInputs) -> AgentAction:
        # ---- WAVE-LOCK RESET (MANDATORY, runs every candle) ----
        if self.wave_lock and inputs.macd_current is not None:
            if abs(inputs.macd_current) < self.wave_reset_macd_abs:
                self.wave_lock = False

        # Guard: don’t trade until indicators are available
        if inputs.supertrend_direction not in (1, -1):
            return AgentAction.HOLD
        if inputs.adx_value is None or inputs.macd_current is None or inputs.macd_previous is None:
            return AgentAction.HOLD

        if not inputs.in_position:
            # ---------- ENTRY LOGIC ----------
            if self.wave_lock:
                return AgentAction.HOLD

            if not inputs.supertrend_flipped:
                return AgentAction.HOLD

            if inputs.adx_value < self.adx_min:
                return AgentAction.HOLD

            # ----- BUY CE -----
            if inputs.supertrend_direction == 1:
                if inputs.macd_current > 0 and inputs.macd_current > inputs.macd_previous:
                    self.wave_lock = True
                    self.last_trade_side = "CE"
                    return AgentAction.ENTER_CE
                return AgentAction.HOLD

            # ----- BUY PE -----
            if inputs.supertrend_direction == -1:
                if inputs.macd_current < 0 and inputs.macd_current < inputs.macd_previous:
                    self.wave_lock = True
                    self.last_trade_side = "PE"
                    return AgentAction.ENTER_PE
                return AgentAction.HOLD

            return AgentAction.HOLD

        # ---------- EXIT LOGIC ----------
        if inputs.supertrend_flipped:
            return AgentAction.EXIT

        trade_side = self.last_trade_side or inputs.current_position_side

        # Momentum decay exit
        if trade_side == "CE":
            if inputs.macd_current < inputs.macd_previous:
                return AgentAction.EXIT

        if trade_side == "PE":
            if inputs.macd_current > inputs.macd_previous:
                return AgentAction.EXIT

        return AgentAction.HOLD
