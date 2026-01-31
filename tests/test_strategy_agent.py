import unittest

import os
import sys

# Repo layout: Docker/runtime executes from backend/ where modules are flat.
# For local unittest runs from repo root, add backend/ to sys.path.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from strategy_agent import AgentAction, AgentInputs, STAdxMacdAgent


class TestSTAdxMacdAgent(unittest.TestCase):
    def setUp(self):
        self.agent = STAdxMacdAgent(adx_min=20.0, wave_reset_macd_abs=0.05)

    def test_hold_until_indicators_ready(self):
        inputs = AgentInputs(
            timestamp="2026-01-31T00:00:00Z",
            open=1,
            high=1,
            low=1,
            close=1,
            supertrend_direction=1,
            supertrend_flipped=True,
            adx_value=None,
            macd_current=None,
            macd_previous=None,
            in_position=False,
            current_position_side=None,
        )
        self.assertEqual(self.agent.decide(inputs), AgentAction.HOLD)

    def test_entry_ce_on_flip_adx_ok_macd_rising_positive(self):
        inputs = AgentInputs(
            timestamp="2026-01-31T00:00:00Z",
            open=1,
            high=1,
            low=1,
            close=1,
            supertrend_direction=1,
            supertrend_flipped=True,
            adx_value=25.0,
            macd_current=0.10,
            macd_previous=0.05,
            in_position=False,
            current_position_side=None,
        )
        self.assertEqual(self.agent.decide(inputs), AgentAction.ENTER_CE)
        self.assertTrue(self.agent.wave_lock)
        self.assertEqual(self.agent.last_trade_side, "CE")

    def test_wave_lock_blocks_reentry(self):
        self.agent.wave_lock = True
        inputs = AgentInputs(
            timestamp="2026-01-31T00:00:00Z",
            open=1,
            high=1,
            low=1,
            close=1,
            supertrend_direction=1,
            supertrend_flipped=True,
            adx_value=30.0,
            macd_current=0.20,
            macd_previous=0.10,
            in_position=False,
            current_position_side=None,
        )
        self.assertEqual(self.agent.decide(inputs), AgentAction.HOLD)

    def test_exit_on_supertrend_flip_while_in_position(self):
        self.agent.last_trade_side = "CE"
        inputs = AgentInputs(
            timestamp="2026-01-31T00:00:00Z",
            open=1,
            high=1,
            low=1,
            close=1,
            supertrend_direction=-1,
            supertrend_flipped=True,
            adx_value=30.0,
            macd_current=0.10,
            macd_previous=0.20,
            in_position=True,
            current_position_side="CE",
        )
        self.assertEqual(self.agent.decide(inputs), AgentAction.EXIT)

    def test_wave_lock_resets_when_macd_small(self):
        self.agent.wave_lock = True
        inputs = AgentInputs(
            timestamp="2026-01-31T00:00:00Z",
            open=1,
            high=1,
            low=1,
            close=1,
            supertrend_direction=1,
            supertrend_flipped=False,
            adx_value=30.0,
            macd_current=0.01,
            macd_previous=0.02,
            in_position=False,
            current_position_side=None,
        )
        _ = self.agent.decide(inputs)
        self.assertFalse(self.agent.wave_lock)


if __name__ == "__main__":
    unittest.main()
