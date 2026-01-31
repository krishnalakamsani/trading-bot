import React, { useContext, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppContext } from "@/App";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Key, ShieldCheck, Eye, EyeOff, Save, ArrowLeft } from "lucide-react";

const Settings = () => {
  const navigate = useNavigate();
  const { config, updateConfig, botStatus, position } = useContext(AppContext);

  // API Credentials
  const [accessToken, setAccessToken] = useState("");
  const [clientId, setClientId] = useState("");
  const [showToken, setShowToken] = useState(false);

  // Risk Parameters
  const [orderQty, setOrderQty] = useState(config.order_qty);
  const [maxTrades, setMaxTrades] = useState(config.max_trades_per_day);
  const [maxLoss, setMaxLoss] = useState(config.daily_max_loss);
  const [maxLossPerTrade, setMaxLossPerTrade] = useState(config.max_loss_per_trade || 0);
  const [initialSL, setInitialSL] = useState(config.initial_stoploss || 0);
  const [trailStart, setTrailStart] = useState(config.trail_start_profit);
  const [trailStep, setTrailStep] = useState(config.trail_step);
  const [targetPoints, setTargetPoints] = useState(config.target_points || 0);
  const [riskPerTrade, setRiskPerTrade] = useState(config.risk_per_trade || 0);

  // Strategy / Agent
  const strategyMode = "st_macd_hist";
  const [bypassMarketHours, setBypassMarketHours] = useState(
    config.bypass_market_hours ?? false
  );

  const [saving, setSaving] = useState(false);
  const isFirstRender = React.useRef(true);

  // Get index info
  const indexInfo = {
    NIFTY: { lot_size: 50, strike_interval: 100 },
    BANKNIFTY: { lot_size: 15, strike_interval: 100 },
    FINNIFTY: { lot_size: 40, strike_interval: 50 },
    MIDCPNIFTY: { lot_size: 75, strike_interval: 50 },
  };
  const currentIndexInfo = indexInfo[config?.selected_index] || { lot_size: 50, strike_interval: 100 };

  React.useEffect(() => {
    if (isFirstRender.current) {
      setOrderQty(config?.order_qty || 1);
      setMaxTrades(config?.max_trades_per_day || 5);
      setMaxLoss(config?.daily_max_loss || 2000);
      setMaxLossPerTrade(config?.max_loss_per_trade || 0);
      setInitialSL(config?.initial_stoploss || 50);
      setTrailStart(config?.trail_start_profit || 10);
      setTrailStep(config?.trail_step || 5);
      setTargetPoints(config?.target_points || 0);
      setRiskPerTrade(config?.risk_per_trade || 0);

      setBypassMarketHours(config?.bypass_market_hours ?? false);
      isFirstRender.current = false;
    }
  }, []);

  const handleSaveCredentials = async () => {
    if (!accessToken || !clientId) {
      return;
    }
    setSaving(true);
    await updateConfig({
      dhan_access_token: accessToken,
      dhan_client_id: clientId,
    });
    setAccessToken("");
    setClientId("");
    setSaving(false);
  };

  const handleSaveRiskParams = async () => {
    setSaving(true);
    await updateConfig({
      order_qty: orderQty,
      max_trades_per_day: maxTrades,
      daily_max_loss: maxLoss,
      max_loss_per_trade: maxLossPerTrade,
      initial_stoploss: initialSL,
      trail_start_profit: trailStart,
      trail_step: trailStep,
      target_points: targetPoints,
      risk_per_trade: riskPerTrade,
    });
    setSaving(false);
  };

  const handleSaveStrategy = async () => {
    setSaving(true);
    await updateConfig({
      strategy_mode: strategyMode,
      bypass_market_hours: bypassMarketHours,
    });
    setSaving(false);
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 p-4 lg:p-6">
        <div className="max-w-6xl mx-auto flex items-center gap-4">
          <Button
            onClick={() => navigate("/")}
            variant="ghost"
            size="sm"
            className="rounded-sm"
          >
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back
          </Button>
          <h1 className="text-2xl font-bold">Settings</h1>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-6xl mx-auto p-4 lg:p-6">
        <Tabs defaultValue="risk" className="w-full">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="credentials" className="text-xs">
              <Key className="w-3 h-3 mr-1" />
              API Keys
            </TabsTrigger>
            <TabsTrigger value="risk" className="text-xs">
              <ShieldCheck className="w-3 h-3 mr-1" />
              Risk
            </TabsTrigger>
            <TabsTrigger value="strategy" className="text-xs">
              <ShieldCheck className="w-3 h-3 mr-1" />
              Strategy
            </TabsTrigger>
          </TabsList>

          {/* API Credentials Tab */}
          <TabsContent value="credentials" className="space-y-4 mt-6 bg-white p-6 rounded-lg border border-gray-200">
            <div className="p-3 bg-amber-50 border border-amber-200 rounded-sm text-xs text-amber-800">
              <strong>Note:</strong> Dhan access token expires daily. Update it
              here each morning before trading.
            </div>

            <div className="space-y-3">
              <div>
                <Label htmlFor="client-id">Client ID</Label>
                <Input
                  id="client-id"
                  placeholder="Enter your Dhan Client ID"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  className="mt-1 rounded-sm"
                  data-testid="client-id-input"
                />
              </div>

              <div>
                <Label htmlFor="access-token">Access Token</Label>
                <div className="relative mt-1">
                  <Input
                    id="access-token"
                    type={showToken ? "text" : "password"}
                    placeholder="Enter your Dhan Access Token"
                    value={accessToken}
                    onChange={(e) => setAccessToken(e.target.value)}
                    className="pr-10 rounded-sm"
                    data-testid="access-token-input"
                  />
                  <button
                    type="button"
                    onClick={() => setShowToken(!showToken)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                  >
                    {showToken ? (
                      <EyeOff className="w-4 h-4" />
                    ) : (
                      <Eye className="w-4 h-4" />
                    )}
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between pt-2">
                <span
                  className={`text-xs ${
                    config.has_credentials
                      ? "text-emerald-600"
                      : "text-amber-600"
                  }`}
                >
                  {config.has_credentials
                    ? "✓ Credentials configured"
                    : "⚠ No credentials set"}
                </span>
                <Button
                  onClick={handleSaveCredentials}
                  disabled={saving || !accessToken || !clientId}
                  size="sm"
                  className="rounded-sm btn-active"
                  data-testid="save-credentials-btn"
                >
                  <Save className="w-3 h-3 mr-1" />
                  {saving ? "Saving..." : "Save Credentials"}
                </Button>
              </div>
            </div>

            <div className="text-xs text-gray-500 pt-2 border-t border-gray-100">
              Get your credentials from{" "}
              <a
                href="https://web.dhan.co"
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline"
              >
                web.dhan.co
              </a>{" "}
              → My Profile → DhanHQ Trading APIs
            </div>
          </TabsContent>

          {/* Risk Parameters Tab */}
          <TabsContent value="risk" className="space-y-4 mt-6 bg-white p-6 rounded-lg border border-gray-200">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <Label htmlFor="order-qty">Number of Lots</Label>
                <Input
                  id="order-qty"
                  type="number"
                  min="1"
                  max="10"
                  value={orderQty}
                  onChange={(e) => {
                    const val = parseInt(e.target.value) || 1;
                    setOrderQty(Math.min(10, Math.max(1, val)));
                  }}
                  className="mt-1 rounded-sm"
                  data-testid="order-qty-input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  {orderQty} lot = {orderQty * currentIndexInfo.lot_size} qty
                </p>
              </div>

              <div>
                <Label htmlFor="max-trades">Max Trades/Day</Label>
                <Input
                  id="max-trades"
                  type="number"
                  value={maxTrades}
                  onChange={(e) => setMaxTrades(parseInt(e.target.value))}
                  className="mt-1 rounded-sm"
                  data-testid="max-trades-input"
                />
              </div>

              <div>
                <Label htmlFor="max-loss">Daily Max Loss (₹)</Label>
                <Input
                  id="max-loss"
                  type="number"
                  value={maxLoss}
                  onChange={(e) => setMaxLoss(parseFloat(e.target.value))}
                  className="mt-1 rounded-sm"
                  data-testid="max-loss-input"
                />
              </div>

              <div>
                <Label htmlFor="max-loss-per-trade">Max Loss Per Trade (₹)</Label>
                <Input
                  id="max-loss-per-trade"
                  type="number"
                  min="0"
                  value={maxLossPerTrade}
                  onChange={(e) => setMaxLossPerTrade(parseFloat(e.target.value))}
                  className="mt-1 rounded-sm"
                  data-testid="max-loss-per-trade-input"
                />
                <p className="text-xs text-gray-500 mt-1">0 = disabled</p>
              </div>

              <div>
                <Label htmlFor="initial-sl">Initial Stop Loss (points)</Label>
                <Input
                  id="initial-sl"
                  type="number"
                  min="0"
                  value={initialSL}
                  onChange={(e) => setInitialSL(parseFloat(e.target.value))}
                  className="mt-1 rounded-sm"
                  data-testid="initial-sl-input"
                />
                <p className="text-xs text-gray-500 mt-1">0 = disabled</p>
              </div>

              <div>
                <Label htmlFor="risk-per-trade">Risk Per Trade (₹)</Label>
                <Input
                  id="risk-per-trade"
                  type="number"
                  min="0"
                  value={riskPerTrade}
                  onChange={(e) => setRiskPerTrade(parseFloat(e.target.value))}
                  className="mt-1 rounded-sm"
                  data-testid="risk-per-trade-input"
                />
                <p className="text-xs text-gray-500 mt-1">0 = uses fixed qty, else auto-sizes position</p>
              </div>

              <div>
                <Label htmlFor="trail-start">Trail Start Profit</Label>
                <Input
                  id="trail-start"
                  type="number"
                  value={trailStart}
                  onChange={(e) => setTrailStart(parseFloat(e.target.value))}
                  className="mt-1 rounded-sm"
                  data-testid="trail-start-input"
                />
                <p className="text-xs text-gray-500 mt-1">Points</p>
              </div>

              <div>
                <Label htmlFor="trail-step">Trail Step</Label>
                <Input
                  id="trail-step"
                  type="number"
                  value={trailStep}
                  onChange={(e) => setTrailStep(parseFloat(e.target.value))}
                  className="mt-1 rounded-sm"
                  data-testid="trail-step-input"
                />
                <p className="text-xs text-gray-500 mt-1">Points</p>
              </div>

              <div>
                <Label htmlFor="target-points">Target Points</Label>
                <Input
                  id="target-points"
                  type="number"
                  min="0"
                  value={targetPoints}
                  onChange={(e) => setTargetPoints(parseFloat(e.target.value))}
                  className="mt-1 rounded-sm"
                  data-testid="target-points-input"
                />
                <p className="text-xs text-gray-500 mt-1">0 = disabled</p>
              </div>
            </div>

            <div className="flex justify-end pt-4 border-t border-gray-100">
              <Button
                onClick={handleSaveRiskParams}
                disabled={saving}
                size="sm"
                className="rounded-sm btn-active"
                data-testid="save-risk-params-btn"
              >
                <Save className="w-3 h-3 mr-1" />
                {saving ? "Saving..." : "Save Risk Parameters"}
              </Button>
            </div>
          </TabsContent>

          {/* Strategy Tab */}
          <TabsContent value="strategy" className="space-y-4 mt-6 bg-white p-6 rounded-lg border border-gray-200">
            <div className="space-y-4">
              <div className="p-3 bg-blue-50 border border-blue-200 rounded-sm text-xs text-blue-800">
                <strong>Strategy</strong>: Fixed to <strong>SuperTrend + MACD Histogram</strong> on fixed ATM option candles (5s).
              </div>

              <div className="text-xs text-gray-600 space-y-1">
                <div>
                  Mode: <span className="font-mono">ST + MACD Histogram</span>
                </div>
                <div>
                  Entry: <span className="font-mono">ST BUY</span> + <span className="font-mono">Hist &gt; 0.5</span> + <span className="font-mono">Hist increasing</span>
                </div>
                <div>
                  Exit: <span className="font-mono">ST reversal</span> OR <span className="font-mono">Hist &lt; 0</span> OR <span className="font-mono">Trailing SL</span> OR <span className="font-mono">Target</span>
                </div>
              </div>

              <div className="flex items-center justify-between p-3 bg-gray-50 border border-gray-200 rounded-sm">
                <div>
                  <p className="text-sm font-medium">Bypass Market Hours (Test Mode)</p>
                  <p className="text-xs text-gray-500">
                    Runs the bot after-hours using simulated LTPs (use Paper mode).
                  </p>
                </div>
                <input
                  type="checkbox"
                  checked={bypassMarketHours}
                  onChange={(e) => setBypassMarketHours(e.target.checked)}
                  className="h-4 w-4"
                />
              </div>

              <div className="flex justify-end">
                <Button
                  onClick={handleSaveStrategy}
                  disabled={saving}
                  size="sm"
                  className="rounded-sm btn-active"
                >
                  <Save className="w-3 h-3 mr-1" />
                  {saving ? "Saving..." : "Save Strategy"}
                </Button>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
};

export default Settings;
