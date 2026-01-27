# NiftyAlgo Terminal - Automated Options Trading Bot

A full-stack automated options trading bot for Nifty Index using Dhan Trading API with a real-time dashboard.

## Features

- **SuperTrend Strategy**: SuperTrend(7,4) on 5-second candles
- **ATM Strike Selection**: Nifty spot rounded to nearest 50
- **Risk Management**: Max trades/day, daily loss limit, time-based exits
- **Trailing Stop Loss**: Configurable parameters
- **Paper/Live Modes**: Test without real money first
- **Real-time Dashboard**: Live updates via WebSocket

---

## 🐳 Docker Deployment (Recommended)

### Prerequisites
- Docker & Docker Compose installed
- EC2 instance with ports 80 and 8001 open

### Quick Start

```bash
# Clone the repository
git clone <your-repo-url>
cd niftyalgo-terminal

# Create environment file
cp .env.example .env

# Edit .env with your EC2 public IP
nano .env
# Set: REACT_APP_BACKEND_URL=http://YOUR_EC2_PUBLIC_IP:8001

# Build and run
docker-compose up -d --build

# Check status
docker-compose ps

# View logs
docker-compose logs -f
```

### Access the Application
- **Frontend**: `http://YOUR_EC2_IP` (port 80)
- **Backend API**: `http://YOUR_EC2_IP:8001/api`

### Useful Commands

```bash
# Stop containers
docker-compose down

# Restart containers
docker-compose restart

# Rebuild after code changes
docker-compose up -d --build

# View backend logs
docker-compose logs -f backend

# View frontend logs
docker-compose logs -f frontend

# Access backend container shell
docker-compose exec backend bash

# Backup database
docker cp niftyalgo-backend:/app/data/trading.db ./backup_trading.db
```

---

## 🖥️ EC2 Ubuntu Setup (Complete Guide)

### 1. Launch EC2 Instance
- **AMI**: Ubuntu 22.04 LTS
- **Instance Type**: t3.small or higher
- **Storage**: 20GB minimum
- **Security Group**: Open ports 22, 80, 8001

### 2. Connect and Install Docker

```bash
# Connect to EC2
ssh -i your-key.pem ubuntu@YOUR_EC2_IP

# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker ubuntu

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Logout and login again for docker group to take effect
exit
```

### 3. Deploy Application

```bash
# Clone repo
git clone <your-repo-url>
cd niftyalgo-terminal

# Configure environment
cp .env.example .env
nano .env
# Set REACT_APP_BACKEND_URL to your EC2 public IP

# Build and run
docker-compose up -d --build

# Verify
docker-compose ps
curl http://localhost:8001/api/status
```

### 4. Configure Security Group
Ensure these inbound rules:
| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 22 | TCP | Your IP | SSH |
| 80 | TCP | 0.0.0.0/0 | Frontend |
| 8001 | TCP | 0.0.0.0/0 | Backend API |

---

## 🔧 Configuration

### Update Dhan Credentials
1. Open `http://YOUR_EC2_IP` in browser
2. Click Settings (⚙️ icon)
3. Enter Client ID and Access Token from [web.dhan.co](https://web.dhan.co)
4. Click "Save Credentials"

**Note**: Dhan access token expires daily. Update it each morning before market opens.

### Risk Parameters
Configurable via Settings → Risk Parameters:
- Order Quantity (default: 50 = 1 lot)
- Max Trades/Day (default: 5)
- Daily Max Loss (default: ₹2000)
- Trail Start Profit (default: 10 points)
- Trail Step (default: 5 points)
- Trailing SL Distance (default: 10 points)

---

## 📊 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/status | Bot status |
| GET | /api/market/nifty | Nifty LTP & SuperTrend |
| GET | /api/position | Current position |
| GET | /api/trades | Trade history |
| GET | /api/summary | Daily summary |
| GET | /api/logs | Bot logs |
| GET | /api/config | Configuration |
| POST | /api/bot/start | Start trading |
| POST | /api/bot/stop | Stop trading |
| POST | /api/bot/squareoff | Force exit |
| POST | /api/config/update | Update settings |
| WS | /ws | Real-time updates |

---

## 🚀 Production Tips

### Enable HTTPS with Let's Encrypt

```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx -y

# Get certificate (replace with your domain)
sudo certbot --nginx -d yourdomain.com

# Auto-renewal is configured automatically
```

### Setup Auto-restart on Boot

```bash
# Enable Docker service
sudo systemctl enable docker

# The containers will auto-restart due to restart: unless-stopped policy
```

### Monitor Resources

```bash
# View container stats
docker stats

# View disk usage
docker system df
```

---

## ⚠️ Disclaimer

This is for educational purposes only. Trading in derivatives involves substantial risk of loss. Past performance is not indicative of future results. Use at your own risk.

---

## 📝 License

MIT License
