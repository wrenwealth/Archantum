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
    arbitrage_threshold: float = Field(default=0.02, description="Arbitrage threshold (2%)")
    volume_spike_multiplier: float = Field(default=2.0, description="Volume spike multiplier")
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
    volume_rolling_days: int = Field(default=7, description="Days for volume rolling average")
    price_move_intervals: int = Field(default=120, description="Intervals to look back for price moves")

    # Accuracy tracking
    accuracy_eval_hours: int = Field(default=24, description="Hours before evaluating alert accuracy")

    # Market scoring
    score_spike_threshold: float = Field(default=15.0, description="Score points increase to trigger alert")

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
