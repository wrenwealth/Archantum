"""Configuration management using Pydantic settings."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Telegram configuration (optional)
    telegram_bot_token: Optional[str] = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = Field(default=None, alias="TELEGRAM_CHAT_ID")

    # Detection thresholds
    arbitrage_threshold: float = Field(default=0.01, description="Arbitrage threshold (1% - Yes+No < 99¢)")
    price_move_threshold: float = Field(default=0.05, description="Price movement threshold (5%)")

    # Polling configuration
    poll_interval: int = Field(default=30, description="Polling interval in seconds")
    batch_size: int = Field(default=50, description="Batch size for API requests")
    batch_delay: float = Field(default=0.5, description="Delay between batches in seconds")

    # Market filtering
    min_volume_24hr: float = Field(default=1000.0, description="Minimum 24h volume to track")
    max_markets: int = Field(default=200, description="Maximum markets to track")

    # API URLs
    gamma_api_base_url: str = "https://gamma-api.polymarket.com"
    clob_api_base_url: str = "https://clob.polymarket.com"

    # Database
    database_path: Path = Field(default=Path("archantum.db"))

    # Analysis windows
    price_move_intervals: int = Field(default=120, description="Intervals to look back for price moves")

    # Accuracy tracking
    accuracy_eval_hours: int = Field(default=24, description="Hours before evaluating alert accuracy")

    # Smart money tracking
    smart_money_min_trade_usdc: float = Field(default=500.0, description="Min trade size for alerts")
    smart_money_top_wallets: int = Field(default=20, description="Number of top wallets to track")

    # WebSocket configuration
    ws_enabled: bool = Field(default=True, description="Enable WebSocket real-time data")
    ws_reconnect_max_attempts: int = Field(default=10, description="Max reconnection attempts")
    ws_reconnect_delay: float = Field(default=5.0, description="Initial reconnect delay in seconds")

    # Data source failover
    cache_max_age_seconds: float = Field(default=60.0, description="Max age for cached prices")
    price_discrepancy_threshold: float = Field(default=0.02, description="Significant discrepancy threshold (2%)")

    # Technical analysis
    ta_enabled: bool = Field(default=True, description="Enable technical analysis")
    ta_poll_frequency: int = Field(default=5, description="Calculate TA every N polls")
    confluence_alert_threshold: float = Field(default=60.0, description="Min confluence score for alerts")
    rsi_oversold: float = Field(default=30.0, description="RSI oversold threshold")
    rsi_overbought: float = Field(default=70.0, description="RSI overbought threshold")

    # Liquidity enrichment
    liquidity_enrichment_max: int = Field(default=5, description="Max arbitrage opps to enrich with liquidity per poll")

    # Profit guarantee
    guaranteed_profit_min_cents: float = Field(default=5.0, description="Min guaranteed profit to alert (cents)")
    alpha_capture_min_pct: float = Field(default=0.50, description="Min capture ratio to alert")
    alpha_capture_good_pct: float = Field(default=0.90, description="Capture ratio for ALPHA badge")

    # Settlement lag
    settlement_extreme_threshold: float = Field(default=0.95, description="Price threshold for extreme (>95¢ or <5¢)")
    settlement_min_movement_pct: float = Field(default=3.0, description="Min 1h price movement for settlement lag")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def telegram_configured(self) -> bool:
        """Check if Telegram is configured."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def database_url(self) -> str:
        """Get async SQLite database URL."""
        return f"sqlite+aiosqlite:///{self.database_path}"


# Global settings instance
settings = Settings()
