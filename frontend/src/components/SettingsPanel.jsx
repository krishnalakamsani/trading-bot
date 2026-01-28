import React, { useContext, useState } from "react";
import { AppContext } from "@/App";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Key, Settings, ShieldCheck, Eye, EyeOff, Save, BarChart3 } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const SettingsPanel = ({ onClose }) => {
  const { config, updateConfig, indices, timeframes } = useContext(AppContext);

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

  // Strategy Parameters
  const [selectedIndex, setSelectedIndex] = useState(config.selected_index || "NIFTY");
  const [indicatorType, setIndicatorType] = useState(config.indicator_type || "supertrend");

  const [saving, setSaving] = useState(false);
  const isFirstRender = React.useRef(true);

  // Only sync on first mount, not on every config change to avoid overwriting user edits
  React.useEffect(() => {
    if (isFirstRender.current) {
      setOrderQty(config.order_qty);
      setMaxTrades(config.max_trades_per_day);
      setMaxLoss(config.daily_max_loss);
      setMaxLossPerTrade(config.max_loss_per_trade || 0);
      setInitialSL(config.initial_stoploss || 0);
      setTrailStart(config.trail_start_profit);
      setTrailStep(config.trail_step);
      setTargetPoints(config.target_points || 0);
      setRiskPerTrade(config.risk_per_trade || 0);
      setSelectedIndex(config.selected_index || "NIFTY");
      setIndicatorType(config.indicator_type || "supertrend");
      isFirstRender.current = false;
    }
  }, []); // Empty dependency array - only run once on mount

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
      selected_index: selectedIndex,
      indicator_type: indicatorType,
    });
    setSaving(false);
  };

  // Get lot size for selected index
  const getSelectedIndexInfo = () => {
    const index = indices.find(i => i.name === selectedIndex);
    return index || { lot_size: 50, strike_interval: 50 };
  };

  const indexInfo = getSelectedIndexInfo();

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="sm:max-w-[550px]" data-testid="settings-modal">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-[Manrope]">
            <Settings className="w-5 h-5" />
            Settings
          </DialogTitle>
          <DialogDescription>
            Configure API credentials, strategy, and risk parameters
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="strategy" className="w-full overflow-visible">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="strategy" className="text-xs">
              <BarChart3 className="w-3 h-3 mr-1" />
              Strategy
            </TabsTrigger>
            <TabsTrigger value="credentials" className="text-xs">
              <Key className="w-3 h-3 mr-1" />
              API Keys
            </TabsTrigger>
            <TabsTrigger value="risk" className="text-xs">
              <ShieldCheck className="w-3 h-3 mr-1" />
              Risk
            </TabsTrigger>
          </TabsList>

          {/* Strategy Tab */}
          <TabsContent value="strategy" className="space-y-4 mt-4 overflow-visible">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="index-select">Index</Label>
                <Select
                  value={selectedIndex}
                  onValueChange={setSelectedIndex}
                >
                  <SelectTrigger className="mt-1 rounded-sm" data-testid="settings-index-select">
                    <SelectValue placeholder="Select Index" />
                  </SelectTrigger>
                  <SelectContent>
                    {indices.map((index) => (
                      <SelectItem key={index.name} value={index.name}>
                        {index.display_name || index.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-gray-500 mt-1">
                  Lot size: {indexInfo.lot_size}, Strike: {indexInfo.strike_interval}
                </p>
              </div>

              <div>
                <Label htmlFor="indicator-select">Indicator</Label>
                <Select
                  value={indicatorType}
                  onValueChange={setIndicatorType}
                >
                  <SelectTrigger className="mt-1 rounded-sm" data-testid="settings-indicator-select">
                    <SelectValue placeholder="Select Indicator" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="supertrend">SuperTrend</SelectItem>
                    <SelectItem value="supertrend_macd">SuperTrend + MACD</SelectItem>
                    <SelectItem value="rsi">RSI</SelectItem>
                    <SelectItem value="macd">MACD</SelectItem>
                    <SelectItem value="ma">Moving Average</SelectItem>
                    <SelectItem value="bollinger">Bollinger Bands</SelectItem>
                    <SelectItem value="stochastic">Stochastic</SelectItem>
                    <SelectItem value="adx">ADX</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-gray-500 mt-1">
                  Select trading strategy
                </p>
              </div>
            </div>

            <div className="p-3 bg-blue-50 border border-blue-200 rounded-sm text-xs text-blue-800">
              <strong>Strategy:</strong> {indicatorType === 'supertrend_macd' 
                ? 'SuperTrend (trigger) + MACD (confirmation) - only enters when MACD aligns with SuperTrend direction' 
                : indicatorType.charAt(0).toUpperCase() + indicatorType.slice(1) + ' based entry/exit'}
              <br />
              GREEN signal = Buy CE, RED signal = Buy PE.
              <br />
              <em className="text-xs mt-1">Tip: Select timeframe from Controls panel</em>
            </div>

            <div className="flex justify-end pt-2">
              <Button
                onClick={handleSaveStrategy}
                disabled={saving}
                size="sm"
                className="rounded-sm btn-active"
                data-testid="save-strategy-btn"
              >
                <Save className="w-3 h-3 mr-1" />
                {saving ? "Saving..." : "Save Strategy"}
              </Button>
            </div>
          </TabsContent>

          {/* API Credentials Tab */}
          <TabsContent value="credentials" className="space-y-4 mt-4 overflow-visible">
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
          <TabsContent value="risk" className="space-y-4 mt-4 overflow-visible">
            <div className="grid grid-cols-2 gap-4">
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
                  {orderQty} lot = {orderQty * indexInfo.lot_size} qty
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
                  value={targetPoints}
                  onChange={(e) => setTargetPoints(parseFloat(e.target.value))}
                  className="mt-1 rounded-sm"
                  data-testid="target-points-input"
                />
                <p className="text-xs text-gray-500 mt-1">0 = disabled</p>
              </div>
            </div>

            <div className="flex justify-end pt-2">
              <Button
                onClick={handleSaveRiskParams}
                disabled={saving}
                size="sm"
                className="rounded-sm btn-active"
                data-testid="save-risk-params-btn"
              >
                <Save className="w-3 h-3 mr-1" />
                {saving ? "Saving..." : "Save Parameters"}
              </Button>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
};

export default SettingsPanel;
