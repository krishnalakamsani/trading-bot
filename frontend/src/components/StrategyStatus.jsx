import React, { useContext } from "react";
import { AppContext } from "@/App";
import { Brain } from "lucide-react";

const StrategyStatus = () => {
  const { strategyStatus } = useContext(AppContext);

  const mode = strategyStatus?.strategy_mode || "—";
  const rules = strategyStatus?.rules;
  const st = strategyStatus?.indicators?.supertrend_value;
  const macd = strategyStatus?.indicators?.macd_value;
  const hist = strategyStatus?.indicators?.macd_hist;

  return (
    <div className="terminal-card" data-testid="strategy-status">
      <div className="terminal-card-header">
        <div className="flex items-center gap-2">
          <Brain className="w-4 h-4 text-blue-600" />
          <h2 className="text-sm font-semibold text-gray-900 font-[Manrope]">
            Strategy Status
          </h2>
        </div>

        <span className="text-xs text-gray-500 font-mono">{mode}</span>
      </div>

      <div className="p-4 space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-gray-50 rounded-sm p-3 border border-gray-100">
            <p className="label-text mb-1">SuperTrend</p>
            <p className="value-text font-mono">
              {typeof st === "number" && st > 0 ? st.toFixed(2) : "—"}
            </p>
          </div>

          <div className="bg-gray-50 rounded-sm p-3 border border-gray-100">
            <p className="label-text mb-1">MACD</p>
            <p className="value-text font-mono">
              {typeof macd === "number" && macd !== 0 ? macd.toFixed(4) : "—"}
            </p>
          </div>

          <div className="bg-gray-50 rounded-sm p-3 border border-gray-100">
            <p className="label-text mb-1">MACD Hist</p>
            <p className="value-text font-mono">
              {typeof hist === "number" && hist !== 0 ? hist.toFixed(4) : "—"}
            </p>
          </div>
        </div>

        <div className="border-t border-gray-100 pt-3 text-xs text-gray-700 space-y-1">
          <div>
            <span className="label-text">Entry</span>: <span className="font-mono">{rules?.entry || "—"}</span>
          </div>
          <div>
            <span className="label-text">Exit</span>: <span className="font-mono">{rules?.exit || "—"}</span>
          </div>
          <div>
            <span className="label-text">Candle</span>: <span className="font-mono">{rules?.candle_interval_seconds ? `${rules.candle_interval_seconds}s` : "—"}</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default StrategyStatus;
