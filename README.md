# SuperTrend Trading Bot 🤖

An automated options trading bot for NSE indices (NIFTY, BANKNIFTY, SENSEX, FINNIFTY) using the SuperTrend indicator and Dhan Trading API. Paper and live trading modes with real-time dashboard.

---

## 🚀 Quick Features

✅ **SuperTrend Strategy** - Period 7, Multiplier 4 on 5-second candles  
✅ **Multiple Indices** - NIFTY, BANKNIFTY, SENSEX, FINNIFTY support  
✅ **Risk Management** - Daily loss limits, per-trade loss caps, position sizing  
✅ **Trailing Stop Loss** - Dynamic SL that follows profits  
✅ **Order Fill Verification** - Confirms orders are actually filled  
✅ **Trading Hours Protection** - No entries before 9:25 AM or after 3:10 PM  
✅ **Paper & Live Modes** - Test safely before going live  
✅ **Trade Analysis** - Post-market analytics with filters and statistics  
✅ **Real-time Dashboard** - Live updates via WebSocket

---

## 📋 System Requirements

- **OS**: Linux/Mac/Windows
- **Python**: 3.9+
- **Node.js**: 16+ (for frontend)
- **Docker**: (optional, for containerized deployment)

---

## 🏗️ Architecture

```
Trading-Bot/
├── backend/                 # Python FastAPI server
│   ├── dhan_api.py         # Dhan broker API wrapper
│   ├── trading_bot.py      # Core trading engine
│   ├── indicators.py       # SuperTrend indicator
│   ├── database.py         # SQLite operations
│   ├── config.py           # Configuration & state
│   └── server.py           # FastAPI routes
├── frontend/                # React dashboard
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx    # Main trading UI
│   │   │   └── TradesAnalysis.jsx  # Trade statistics
│   │   ├── components/
│   │   │   ├── ControlsPanel.jsx   # Start/Stop/Mode
│   │   │   ├── SettingsPanel.jsx   # Configuration
│   │   │   └── TradesTable.jsx     # Trade history
│   │   └── App.js
│   └── package.json
└── README.md
```

---

## � Deployment (Docker - Recommended)

**One-Command Deployment**:

```bash
# 1. Clone the repository
git clone <your-repo-url>
cd Trading-bot

# 2. Create environment file
cp .env.example .env

# 3. Update .env with your server IP/domain
# REACT_APP_BACKEND_URL=http://your-server-ip:8000

# 4. Start the application
docker-compose up -d --build

# 5. Verify it's running
docker-compose ps
```

**Access the Application**:
- Frontend: `http://your-server-ip`
- Backend API: `http://your-server-ip:8000/api`

**Docker Commands**:
```bash
docker-compose logs -f              # View logs in real-time
docker-compose stop                 # Stop containers
docker-compose restart              # Restart containers
docker-compose down                 # Stop and remove containers
```

---

## 🔧 Installation & Setup

### Backend Setup

```bash
# Navigate to project root
cd Trading-bot

# Install Python dependencies
pip install -r backend/requirements.txt

# Create logs directory
mkdir -p backend/logs
mkdir -p backend/data
```

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Create .env file
echo "REACT_APP_BACKEND_URL=http://localhost:8000" > .env
```

---

## 🚀 Local Development (Without Docker)

**Terminal 1 - Backend**:
```bash
cd backend
python -m uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 - Frontend**:
```bash
cd frontend
npm start
```

Access at: `http://localhost:3000`

---

## 📖 How to Use

### 1. Initial Setup

**Step 1**: Open dashboard at `http://localhost:3000`

**Step 2**: Go to **Settings** → **Credentials**
- Enter Dhan API Token
- Enter Dhan Client ID
- Click **Save**

**Step 3**: Go to **Settings** → **Risk** to configure:
- **Initial Stop Loss**: Points (e.g., 50)
- **Max Loss Per Trade**: ₹ (e.g., 500, 0=disabled)
- **Trail Start Profit**: Points to start trailing (e.g., 10)
- **Trail Step**: How much to move SL per step (e.g., 5)
- **Target Points**: Exit at profit (e.g., 100, 0=disabled)
- **Risk Per Trade**: Rupees to risk (e.g., 1000, 0=disabled)
- **Daily Max Loss**: ₹ (e.g., 2000)
- **Max Trades/Day**: Limit entries (e.g., 5)

### 2. Start Trading

**Click "Start Bot"** button to begin:
- Select **Paper** mode first (highly recommended!)
- Monitor **Top Bar** status indicators
- Watch **Market Data** section for SuperTrend signals
- Monitor **Position Panel** for open positions

### 3. Monitor Trading

**Dashboard shows**:
- Current index LTP (NIFTY/BANKNIFTY/etc)
- SuperTrend signal (GREEN=Buy CE, RED=Buy PE)
- Current open position (strike, entry, P&L)
- Daily summary (trades, P&L, max drawdown)
- Recent trade logs

### 4. Manual Exits

**Click "Square Off"** button to close position:
- Closes at current market price
- Saves trade with actual exit
- No confirmation dialog

### 5. View Trade Analysis

**Click "Analysis"** button in top bar:
- **Overview**: Statistics, Win Rate, Profit Factor
- **All Trades Tab**: Filter by type, exit reason, strike, P&L range

---

## 📊 Features Explained

### SuperTrend Strategy
- **Indicator**: SuperTrend(Period=7, Multiplier=4)
- **Timeframe**: 5-second candles
- **Entry**: 
  - GREEN = Buy CE (Call)
  - RED = Buy PE (Put)
- **Strike**: ATM (spot rounded to nearest 50)

#### Multi-strike Contract Universe (Optional)
By default the bot tracks a single ATM CE/PE contract for signals.

You can enable a *strike band* around ATM (example: 5 below + ATM + 5 above) so the bot:
- Fetches CE/PE for each strike in the band
- Computes the same indicators per contract (same entry rules)
- When multiple contracts qualify, enters the contract whose strike is **nearest to the current index LTP** (ATM)

Config key:
- `option_universe_strike_steps`
  - `0` = single ATM (default)
  - `5` = 11 strikes (5 below + ATM + 5 above) → 22 option contracts (CE+PE)

Note: enabling this increases quote/option-chain load.
- **Exit Conditions** (priority order):
  1. Max Loss Per Trade exceeded ⚠️
  2. Target Points hit ✓
  3. Trailing Stop Loss hit
  4. Daily Max Loss triggered

### Risk Management

**Daily Max Loss**: 
- Once triggered, no new entries allowed
- Existing positions can still exit

**Max Loss Per Trade**: 
- Individual trade risk limit
- Auto-closes if exceeded

**Risk Per Trade**: 
- Auto-calculates position size
- Formula: `Qty = RiskAmount / (SL_Points × Lot_Size)`

**Trailing Stop Loss**:
- Activates after `Trail Start Profit`
- Moves up by `Trail Step` on each high
- Locks in profits

### Trading Hours Protection
- **No entries before**: 9:25 AM
- **No entries after**: 3:10 PM
- Prevents overnight position risk
- Existing positions can exit anytime

### Order Fill Verification
- Every order verified filled
- Checks status every 0.5 seconds
- Waits max 15 seconds
- Ensures accuracy with broker

---

## ⚙️ Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Initial SL | 50 | Points below entry |
| Max Loss/Trade | 0 | ₹ per trade (0=disabled) |
| Trail Start | 10 | Profit points to start trailing |
| Trail Step | 5 | SL increment points |
| Target Points | 0 | Profit exit (0=disabled) |
| Risk/Trade | 0 | ₹ for auto-sizing (0=disabled) |
| Daily Max Loss | 2000 | ₹ daily limit |
| Max Trades/Day | 5 | Entry limit |

---

## 🐛 Troubleshooting

**Docker containers not starting**:
```bash
# Check logs
docker-compose logs backend
docker-compose logs frontend

# Rebuild containers
docker-compose down
docker-compose up -d --build
```

**Frontend says "Cannot connect to backend"**:
- Verify backend is running: `docker-compose ps`
- Check `REACT_APP_BACKEND_URL` in .env file
- Ensure backend port 8000 is open in firewall
- Restart: `docker-compose restart backend`

**Bot won't connect to Dhan**:
- Check credentials in Settings
- Verify Dhan token hasn't expired (refresh daily)
- Check internet connection
- View logs: `docker-compose logs -f backend`

**Orders not placed**:
- Are you in Paper or Live mode?
- Is bot running (shows "Running" in top bar)?
- Is it between 9:25 AM - 3:10 PM?
- Check daily max loss triggered

**Positions not closing**:
- Check exit conditions being met
- Use "Square Off" to force close
- Check bot still running: `docker-compose ps`

**Database errors**:
```bash
# Backup existing database
docker cp trading-bot-backend:/app/data/trading.db ./backup.db

# Restart to reinitialize
docker-compose restart backend
```

---

## 📝 Trading Mode Differences

| Feature | Paper Mode | Live Mode |
|---------|-----------|-----------|
| Orders | Simulated | Real orders |
| Money | Play money | Actual rupees |
| Risk | None | Real losses possible |

**Always test in Paper mode first!**

---

## 🔐 Security & Safety

- **Local Credentials**: Stored in SQLite on your machine
- **No Cloud**: Everything runs locally
- **Order Validation**: Every order verified filled
- **Circuit Breakers**: Daily loss limits prevent catastrophic losses
- **HTTPS**: Use HTTPS in production

---

## 📈 Post-Deployment Checklist

- [ ] Test in Paper mode for 1-2 days
- [ ] Review trades daily in Analytics page
- [ ] Fine-tune SL/Target based on results
- [ ] Monitor logs for errors
- [ ] Start Live with 1 lot
- [ ] Increase gradually based on confidence

---

## 📄 Logs & Debugging

**View live logs**:
```bash
# All containers
docker-compose logs -f

# Backend only
docker-compose logs -f backend

# Frontend only
docker-compose logs -f frontend
```

**Access logs inside container**:
```bash
docker-compose exec backend cat logs/bot.log
```

**Copy logs from container**:
```bash
docker cp trading-bot-backend:/app/logs/bot.log ./bot.log
```

Each log entry includes:
- Timestamp
- Component tag [ORDER], [SIGNAL], [ENTRY], etc.
- Detailed message for debugging

---

## ⚠️ Disclaimer

**This bot makes REAL trades with REAL money in Live mode.**

- Past performance ≠ future results
- Options trading is RISKY - you can lose everything
- **Start with Paper Trading only**
- Use only capital you can afford to lose
- SuperTrend is NOT a guaranteed winning strategy
- Market gaps can cause losses beyond SL
- Monitor the bot regularly

**Use entirely at your own risk.**

---

## 📞 Quick Start Summary

```bash
# 1. Clone & Setup
git clone <your-repo-url>
cd Trading-bot
cp .env.example .env
# Edit .env: REACT_APP_BACKEND_URL=http://your-server-ip:8000

# 2. Deploy with Docker
docker-compose up -d --build

# 3. Verify
docker-compose ps

# 4. Open http://your-server-ip

# 5. Settings → Add Dhan credentials

# 6. Settings → Configure Risk parameters

# 7. Click "Start Bot" → Select "Paper"

# 8. Monitor trades on Dashboard

# 9. Check "Analysis" page for statistics
```

---

**Last Updated**: January 2026  
**Version**: 1.0  
**Status**: Production Ready ✅
