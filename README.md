# Archantum

Polymarket prediction market analysis agent with Telegram bot integration.

## Features

### Real-time Alerts
- **Arbitrage Detection** - Find mispriced markets (Yes + No != $1.00)
- **Volume Spikes** - Detect unusual trading activity
- **Price Movements** - Track significant price changes
- **Whale Activity** - Large volume changes indicating big trades
- **New Markets** - Interesting new markets with high volume
- **Resolution Alerts** - Markets about to resolve (48h, 24h, 6h, 1h)
- **Liquidity Changes** - Significant liquidity additions/removals

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

### Portfolio Tracking
- Record buy/sell positions
- Track average entry price
- Calculate real-time P&L
- View position summaries

### Historical Analysis
- Price history with high/low/change stats
- Sparkline charts in Telegram
- Alert statistics

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
# Telegram (required)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Detection Thresholds
ARBITRAGE_THRESHOLD=0.01        # 1% spread
VOLUME_SPIKE_MULTIPLIER=1.5     # 1.5x average
PRICE_MOVE_THRESHOLD=0.03       # 3% movement

# Polling
POLL_INTERVAL=30                # seconds
BATCH_SIZE=50                   # markets per batch

# Market Filtering
MIN_VOLUME_24HR=1000            # minimum 24h volume
MAX_MARKETS=200                 # max markets to track
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
├── alerts/          # Telegram bot and alerting
│   ├── bot.py       # Interactive Telegram bot
│   └── telegram.py  # Alert formatting and sending
├── analysis/        # Market analysis modules
│   ├── arbitrage.py # Arbitrage detection
│   ├── volume.py    # Volume spike detection
│   ├── price.py     # Price movement detection
│   ├── whale.py     # Whale activity detection
│   ├── liquidity.py # Liquidity change detection
│   ├── resolution.py# Resolution timing alerts
│   └── historical.py# Historical analysis
├── api/             # External API clients
│   ├── gamma.py     # Polymarket Gamma API
│   └── clob.py      # Polymarket CLOB API
├── db/              # Database layer
│   ├── database.py  # Database operations
│   └── models.py    # SQLAlchemy models
├── cli/             # CLI dashboard
└── main.py          # Entry point and CLI
```

## License

MIT
