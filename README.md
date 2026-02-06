# Archantum

Polymarket prediction market analysis agent with real-time arbitrage detection, multi-platform analysis, and Telegram bot integration.

## Features

### Arbitrage Detection
- **Yes/No Arbitrage** — Find mispriced markets where Yes + No < $1.00, with tiered alerts (ALPHA / HIGH_VALUE / STANDARD)
- **Guaranteed Profit Calculator** — Calculates net profit after fees (~2% per side) and slippage, with confidence rating (HIGH/MEDIUM/LOW)
- **Alpha Capture Filter** — Only alerts when capture ratio >= 50%, badges opportunities with >= 90% capture as "ALPHA CAPTURE"
- **Multi-Outcome Arbitrage** — Detects events with 3+ markets where outcome probabilities don't sum to 100%
- **Dependency Arbitrage** — Finds logically related markets with inconsistent pricing (time-based, subset, mutually exclusive)
- **Cross-Platform Arbitrage** — Compares Polymarket vs Kalshi prices for the same events
- **Settlement Lag Detection** — Markets where the outcome appears decided (price > 95c or < 5c) but hasn't fully converged

### Opportunity Intelligence
- **Why It Exists** — Every alert includes a reason classification: low liquidity, settlement lag, market structure, multi-outcome mispricing, dependency violation, or new information
- **Sum Deviation Tracking** — DB-backed 7-day rolling average of multi-outcome deviations, flags when current deviation exceeds 1.5x historical average
- **Liquidity Enrichment** — VWAP-based orderbook analysis with slippage estimates at $100/$500/$1000
- **Execution Risk Score** — 4-component weighted score (liquidity 35%, stability 25%, time 20%, complexity 20%)
- **Capital Efficiency Scoring** — Annualized return calculation based on resolution date
- **Speed Tracking** — Measures detection-to-alert latency and opportunity lifespan

### Market Analysis
- **Price Movements** — Significant price changes with directional tracking
- **Whale Activity** — Large volume changes indicating big trades
- **Smart Money Tracking** — Monitors top leaderboard wallets, alerts on their trades
- **Technical Analysis** — RSI, MACD, SMA/EMA, with confluence scoring
- **LP Reward Opportunities** — Identifies profitable liquidity provision opportunities with APY estimates
- **Market Scoring** — 0-100 composite score (volume, liquidity, volatility, spread, activity)

### Real-time Alerts
- **New Markets** — High-volume new markets
- **Resolution Alerts** — Markets approaching resolution (48h, 24h, 6h, 1h)
- **Alert Accuracy Tracking** — Evaluates past alerts after 24h to measure prediction accuracy
- **Price Discrepancy Warnings** — WebSocket vs REST API price divergence monitoring

### Telegram Bot Commands
| Command | Description |
|---------|-------------|
| `/markets` | Top 10 markets by volume with direct links |
| `/search <query>` | Search markets by keyword |
| `/price <id>` | Get current price for a market |
| `/watch <id>` | Add market to watchlist |
| `/watchlist` | View your watchlist |
| `/portfolio` | View your positions |
| `/pnl` | P&L summary |
| `/history <id>` | Price history and stats |
| `/chart <id>` | Mini price chart (sparkline) |
| `/buy <id> <yes/no> <shares> <price>` | Record a position |
| `/sell <id> <yes/no> [shares]` | Close a position |
| `/stats` | Alert statistics |
| `/status` | Bot status |
| `/help` | Show all commands |

## Installation

### Requirements
- Python 3.9+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

### Setup

1. Clone the repository:
```bash
git clone https://github.com/wrenwealth/Archantum.git
cd Archantum
```

2. Create virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or
.venv\Scripts\activate  # Windows
```

3. Install dependencies:
```bash
pip install -e .
```

4. Configure environment:
```bash
cp .env.example .env
# Edit .env with your Telegram credentials
```

5. Run:
```bash
python -m archantum run
```

## Configuration

Edit `.env` file:

```env
# Telegram (required for alerts)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Detection Thresholds
ARBITRAGE_THRESHOLD=0.01        # 1% spread (Yes+No < 99c)
PRICE_MOVE_THRESHOLD=0.05       # 5% movement

# Profit Guarantee
GUARANTEED_PROFIT_MIN_CENTS=5.0 # Min guaranteed profit to alert (cents)
ALPHA_CAPTURE_MIN_PCT=0.50      # Min capture ratio to alert
ALPHA_CAPTURE_GOOD_PCT=0.90     # Capture ratio for ALPHA badge

# Settlement Lag
SETTLEMENT_EXTREME_THRESHOLD=0.95  # Price threshold for extreme (>95c or <5c)
SETTLEMENT_MIN_MOVEMENT_PCT=3.0    # Min 1h price movement %

# Polling
POLL_INTERVAL=30                # seconds
BATCH_SIZE=50                   # markets per batch

# Market Filtering
MIN_VOLUME_24HR=1000            # minimum 24h volume
MAX_MARKETS=200                 # max markets to track

# WebSocket
WS_ENABLED=true                 # real-time price feed

# Technical Analysis
TA_ENABLED=true                 # enable TA indicators
TA_POLL_FREQUENCY=5             # calculate every N polls
CONFLUENCE_ALERT_THRESHOLD=60.0 # min confluence score for alerts

# Smart Money
SMART_MONEY_MIN_TRADE_USDC=500  # min trade size for alerts
SMART_MONEY_TOP_WALLETS=20      # number of top wallets to track

# Liquidity
LIQUIDITY_ENRICHMENT_MAX=5      # max arb opps to enrich per poll
```

## Docker

### Build and run:
```bash
docker-compose up -d
```

### View logs:
```bash
docker-compose logs -f
```

## CLI Commands

```bash
# Start polling engine + bot
python -m archantum run

# Run only Telegram bot
python -m archantum bot

# Show status
python -m archantum status

# Send test alert
python -m archantum test-alert

# Health check
python -m archantum health

# Show dashboard
python -m archantum dashboard
```

## Architecture

```
archantum/
├── alerts/                # Telegram bot and alerting
│   ├── bot.py             # Interactive Telegram bot
│   └── telegram.py        # Alert formatting and sending
├── analysis/              # Market analysis modules
│   ├── arbitrage.py       # Yes/No arbitrage + guaranteed profit + reason classifier
│   ├── settlement.py      # Settlement lag detection
│   ├── multi_outcome.py   # Multi-outcome arbitrage + deviation tracking
│   ├── dependency.py      # Dependency-based arbitrage
│   ├── cross_platform.py  # Polymarket vs Kalshi arbitrage
│   ├── liquidity.py       # Orderbook VWAP and slippage analysis
│   ├── risk_score.py      # Execution risk scoring
│   ├── speed_tracker.py   # Detection-to-alert latency tracking
│   ├── scoring.py         # Market scoring (0-100)
│   ├── lp_rewards.py      # LP opportunity analysis
│   ├── price.py           # Price movement detection
│   ├── whale.py           # Whale activity detection
│   ├── smartmoney.py      # Smart money wallet tracking
│   ├── indicators.py      # Technical indicators (RSI, MACD, MA)
│   ├── confluence.py      # Multi-indicator confluence signals
│   ├── resolution.py      # Resolution timing alerts
│   ├── accuracy.py        # Alert accuracy tracking
│   ├── new_market.py      # New market detection
│   ├── trends.py          # Trend analysis
│   └── historical.py      # Historical data analysis
├── api/                   # External API clients
│   ├── gamma.py           # Polymarket Gamma API
│   ├── clob.py            # Polymarket CLOB API
│   ├── kalshi.py          # Kalshi API
│   ├── data.py            # Data aggregation
│   └── websocket.py       # WebSocket real-time feed
├── data/                  # Data pipeline
│   ├── source_manager.py  # WebSocket -> REST -> Cache failover
│   └── validator.py       # Cross-source price validation
├── db/                    # Database layer
│   ├── database.py        # Async database operations
│   └── models.py          # SQLAlchemy models (16 tables)
├── cli/                   # CLI dashboard
│   └── dashboard.py       # Rich terminal dashboard
├── config.py              # Pydantic settings
└── main.py                # Polling engine and CLI entry point
```

### Data Flow

```
GammaClient → Markets → DataSourceManager (WS→REST→Cache) → Prices
    ↓                                                          ↓
  Database  ←──────────── Analyzers ←──────────────────────────┘
    ↓                        ↓
  History         AlertMessage → TelegramAlerter → Telegram / Console
```

### Polling Schedule

| Analysis | Frequency |
|----------|-----------|
| Arbitrage (Yes/No) | Every poll (30s) |
| Settlement Lag | Every poll |
| Price Movements | Every poll |
| Whale Activity | Every poll |
| Resolution Alerts | Every poll |
| Smart Money Sync | Every 5 polls |
| Technical Analysis | Every 5 polls |
| Market Scoring | Every 5 polls |
| Cross-Platform Arb | Every 5 polls |
| LP Opportunities | Every 5 polls |
| Multi-Outcome Arb | Every 5 polls |
| Dependency Arb | Every 5 polls |

## License

MIT
