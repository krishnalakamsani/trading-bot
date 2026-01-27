# NiftyAlgo Terminal - Product Requirements Document

## Original Problem Statement
Build a full-stack automated options trading bot for Nifty Index using Dhan Trading API with a frontend dashboard to track everything in realtime.

## Architecture
- **Backend**: Python FastAPI with WebSocket support
- **Frontend**: React + Tailwind CSS + shadcn/ui
- **Database**: SQLite (trades, daily stats, config)
- **Real-time**: WebSocket for live updates

## User Personas
1. **Active Day Trader**: Uses the bot for automated intraday options trading
2. **Algo Trading Enthusiast**: Wants to test SuperTrend-based strategies

## Core Requirements (Static)
- SuperTrend(7,4) indicator on 5-second candles
- ATM strike selection (round to nearest 50)
- Paper and Live trading modes
- Daily token update from frontend
- Risk management (max trades, max loss, time-based exits)
- Trailing stop loss with configurable parameters

## What's Been Implemented (January 19, 2026)

### Backend
- [x] FastAPI server with all required endpoints
- [x] SQLite database for trades and config
- [x] SuperTrend indicator calculation
- [x] Trading bot engine with entry/exit logic
- [x] WebSocket for real-time updates
- [x] Dhan API integration structure
- [x] Risk management controls
- [x] Trailing SL logic
- [x] Force square-off at 3:25 PM
- [x] Daily reset at 9:15 AM

### Frontend  
- [x] Dashboard with Bento Grid layout
- [x] TopBar with status badges
- [x] Live Position panel
- [x] Controls panel (Start/Stop/Square-off)
- [x] Nifty Tracker with chart
- [x] Trades History table
- [x] Daily Summary panel
- [x] Bot Logs viewer with filtering
- [x] Settings modal (API Credentials + Risk Parameters)
- [x] Paper/Live mode toggle
- [x] WebSocket connection status

### APIs
- [x] GET /api/status
- [x] GET /api/market/nifty
- [x] GET /api/position
- [x] GET /api/trades
- [x] GET /api/summary
- [x] GET /api/logs
- [x] GET /api/config
- [x] POST /api/bot/start
- [x] POST /api/bot/stop
- [x] POST /api/bot/squareoff
- [x] POST /api/config/update
- [x] POST /api/config/mode
- [x] WS /ws (WebSocket)

## Prioritized Backlog

### P0 (Critical) - Done
- All core trading functionality
- Dashboard UI
- API endpoints
- Settings panel

### P1 (High Priority) - Future
- CSV export for trades
- Historical performance charts
- Mobile responsive optimization
- Multi-strategy support

### P2 (Medium Priority) - Future
- Telegram/Discord notifications
- Email alerts
- Advanced analytics
- Backtesting module

## Next Tasks
1. User to configure Dhan API credentials via Settings
2. Test with paper trading during market hours
3. Verify SuperTrend signals in live market
4. Test trailing SL functionality
5. Consider adding CSV export feature
