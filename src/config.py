"""
Configuration Module
=====================
Purpose: Centralized settings and constants for the Phase 2 signal scanner.
         Includes test mode setup for development/testing.

Single Responsibility: Define all global configuration in one place.
"""

import os
from pathlib import Path
from datetime import datetime, timedelta

# ==================== ENVIRONMENT & PATHS ====================
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"

# Create directories if they don't exist
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ==================== TEST MODE CONFIGURATION ====================
TEST_MODE = True  # Set to False to use full S&P 500

TEST_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "JPM",
    "XOM",
    "UNH",
    "LLY",
]

# ==================== DATA DOWNLOAD SETTINGS ====================
# Historical data period for downloading
LOOKBACK_DAYS = 365  # ~1.5 years for robust 200 DMA calculation
END_DATE = datetime.now().date()
START_DATE = END_DATE - timedelta(days=LOOKBACK_DAYS)

# yfinance download timeout (seconds)
YFINANCE_TIMEOUT = 30

# ==================== INDICATOR PARAMETERS ====================
# Moving Averages
SHORT_MA_PERIOD = 50  # 50-day moving average
LONG_MA_PERIOD = 200  # 200-day moving average

# Relative Strength Index
RSI_PERIOD = 14  # RSI(14)

# Volume
VOLUME_MA_PERIOD = 20  # 20-day average volume
VOLUME_MULTIPLIER = 1.5  # Volume filter threshold

# ==================== FILTER CONDITIONS (LAYER 1) ====================
# Price filters
MIN_PRICE_THRESHOLD = None  # No min price filter (all prices OK)

# RSI filters
RSI_MIN_THRESHOLD = 45  # Minimum RSI in last 10 days must be < 45
RSI_CURRENT_MIN = 45  # Current RSI must be >= 45
RSI_CURRENT_MAX = 65  # Current RSI must be <= 65

# Volume filter
LOOKBACK_RSI_MIN_DAYS = 10  # Check minimum RSI over last 10 days

# ==================== OUTPUT SETTINGS ====================
OUTPUT_CSV = OUTPUT_DIR / "signals.csv"

# CSV columns for export
CSV_COLUMNS = [
    "ticker",
    "date",
    "price",
    "dma_50",
    "dma_200",
    "rsi_14",
    "current_rsi",
    "min_rsi_10d",
    "volume_20d_avg",
    "volume_current",
    "volume_ratio",
    "pattern",
    "entry_price",
    "stop_loss",
    "exit_price",
    "upside_pct",
    "risk_reward",
    "target_r_multiple",
]

TARGET_R_MULTIPLE = 3.0

DEBUG_CSV = OUTPUT_DIR / "debug_signals.csv"

# ==================== LOGGING & ERRORS ====================
LOG_LEVEL = "INFO"

# Expected minimum data points for calculations
MIN_DATA_POINTS = max(LONG_MA_PERIOD, LOOKBACK_RSI_MIN_DAYS) + 5


def get_config_summary() -> dict:
    """Return current configuration as dictionary for logging."""
    return {
        "test_mode": TEST_MODE,
        "tickers_count": len(TEST_TICKERS) if TEST_MODE else "Full S&P 500",
        "lookback_days": LOOKBACK_DAYS,
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE.isoformat(),
        "short_ma": SHORT_MA_PERIOD,
        "long_ma": LONG_MA_PERIOD,
        "rsi_period": RSI_PERIOD,
        "output_file": str(OUTPUT_CSV),
    }
