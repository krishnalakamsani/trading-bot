import React, { useContext } from "react";
import { AppContext } from "@/App";
import { Brain } from "lucide-react";

const StrategyStatus = () => {
  const { strategyStatus } = useContext(AppContext);

  const mode = strategyStatus?.strategy_mode || "—";
  const rules = strategyStatus?.rules;
  const ceSt = strategyStatus?.indicators?.signal_ce_supertrend_value;
  const peSt = strategyStatus?.indicators?.signal_pe_supertrend_value;
  const ceMacd = strategyStatus?.indicators?.signal_ce_macd_value;
  const peMacd = strategyStatus?.indicators?.signal_pe_macd_value;
  const ceHist = strategyStatus?.indicators?.signal_ce_macd_hist;
  const peHist = strategyStatus?.indicators?.signal_pe_macd_hist;

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
            <div className="space-y-0.5">
              <p className="value-text font-mono">CE: {typeof ceSt === "number" && ceSt > 0 ? ceSt.toFixed(2) : "—"}</p>
              <p className="value-text font-mono">PE: {typeof peSt === "number" && peSt > 0 ? peSt.toFixed(2) : "—"}</p>
            </div>
          </div>

          <div className="bg-gray-50 rounded-sm p-3 border border-gray-100">
            <p className="label-text mb-1">MACD</p>
            <div className="space-y-0.5">
              <p className="value-text font-mono">CE: {typeof ceMacd === "number" && ceMacd !== 0 ? ceMacd.toFixed(4) : "—"}</p>
              <p className="value-text font-mono">PE: {typeof peMacd === "number" && peMacd !== 0 ? peMacd.toFixed(4) : "—"}</p>
            </div>
          </div>

          <div className="bg-gray-50 rounded-sm p-3 border border-gray-100">
            <p className="label-text mb-1">MACD Hist</p>
            <div className="space-y-0.5">
              <p className="value-text font-mono">CE: {typeof ceHist === "number" && ceHist !== 0 ? ceHist.toFixed(4) : "—"}</p>
              <p className="value-text font-mono">PE: {typeof peHist === "number" && peHist !== 0 ? peHist.toFixed(4) : "—"}</p>
            </div>
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
