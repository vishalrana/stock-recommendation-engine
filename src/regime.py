"""
Regime Detector
===============
Determines the current market regime (bull/bear) using SPY vs its 200-day SMA.

Usage:
    from regime import get_regime, should_trade

    regime = get_regime()
    # {'regime': 'bull', 'spy_price': 543.21, 'spy_200dma': 510.50, 'date': '2026-06-22'}

    if should_trade(regime['regime']):
        # proceed with swing momentum signals
"""

import os
import sys
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# Ensure src/ is on sys.path for bare imports
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SRC_DIR)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from downloader import fetch_ohlcv_data
from indicators import calculate_indicators

logger = logging.getLogger(__name__)


def get_regime() -> dict:
    """
    Detect the current market regime by comparing SPY price to its 200-day SMA.

    Returns:
        dict with keys:
            regime   - "bull" or "bear"
            spy_price - latest SPY close price
            spy_200dma - SPY 200-day simple moving average
            date     - date string of latest data point (YYYY-MM-DD)

    On failure (e.g. SPY data unavailable), returns a safe default
    of "bull" regime with zeroed prices and a warning log.
    """
    try:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=500)  # ~1.5 years for 200 DMA

        logger.info("Fetching SPY data for regime detection...")
        spy_data = fetch_ohlcv_data("SPY", start_date=start_date, end_date=end_date)

        if spy_data is None or spy_data.empty:
            logger.warning("SPY data fetch returned empty. Defaulting to BULL regime.")
            return {
                "regime": "bull",
                "spy_price": 0.0,
                "spy_200dma": 0.0,
                "date": end_date.isoformat(),
            }

        # Calculate indicators (includes DMA_200)
        df = calculate_indicators(spy_data).sort_index()

        if len(df) < 200:
            logger.warning(
                "SPY data has only %d rows (need 200+). Defaulting to BULL regime.",
                len(df),
            )
            return {
                "regime": "bull",
                "spy_price": 0.0,
                "spy_200dma": 0.0,
                "date": end_date.isoformat(),
            }

        latest = df.iloc[-1]
        spy_price = float(latest["CLOSE"])
        spy_200dma = float(latest["DMA_200"])
        latest_date = df.index[-1]

        if hasattr(latest_date, "date"):
            date_str = latest_date.date().isoformat()
        else:
            date_str = str(latest_date)[:10]

        if np.isnan(spy_price) or np.isnan(spy_200dma):
            logger.warning("SPY indicators contain NaN. Defaulting to BULL regime.")
            return {
                "regime": "bull",
                "spy_price": 0.0,
                "spy_200dma": 0.0,
                "date": date_str,
            }

        pct_above_200dma = (spy_price / spy_200dma - 1) * 100
        if pct_above_200dma > 2.0:
            regime = "bull"
        elif pct_above_200dma < -2.0:
            regime = "bear"
        else:
            regime = "sideways"

        logger.info(
            "Regime detected: %s | SPY: $%.2f | 200 DMA: $%.2f | Date: %s",
            regime.upper(),
            spy_price,
            spy_200dma,
            date_str,
        )

        return {
            "regime": regime,
            "spy_price": round(spy_price, 2),
            "spy_200dma": round(spy_200dma, 2),
            "date": date_str,
        }

    except Exception as e:
        logger.warning("Regime detection failed: %s. Defaulting to BULL regime.", e)
        return {
            "regime": "bull",
            "spy_price": 0.0,
            "spy_200dma": 0.0,
            "date": datetime.now().date().isoformat(),
        }


def should_trade(regime: str, strategy_type: str = "swing_momentum") -> bool:
    """
    Determine whether trading is appropriate given the current regime.

    Args:
        regime: "bull" or "bear"
        strategy_type: Strategy identifier. Currently only "swing_momentum" is supported.

    Returns:
        True if the regime favors the given strategy type.
    """
    if strategy_type == "swing_momentum":
        return regime == "bull"

    # Unknown strategy types default to True (permissive)
    logger.warning("Unknown strategy_type '%s'. Defaulting to allow trade.", strategy_type)
    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    print("=" * 50)
    print("  REGIME DETECTOR TEST")
    print("=" * 50)

    result = get_regime()
    print(f"  Regime:    {result['regime'].upper()}")
    print(f"  SPY Price: ${result['spy_price']:.2f}")
    print(f"  SPY 200D:  ${result['spy_200dma']:.2f}")
    print(f"  Date:      {result['date']}")
    print()

    # Assertions
    assert result["regime"] in ("bull", "bear", "sideways"), f"Invalid regime: {result['regime']}"
    assert isinstance(result["spy_price"], float), "spy_price must be float"
    assert isinstance(result["spy_200dma"], float), "spy_200dma must be float"
    assert isinstance(result["date"], str), "date must be string"

    # Test should_trade
    assert should_trade("bull", "swing_momentum") is True
    assert should_trade("bear", "swing_momentum") is False
    print("  All assertions passed.")
    print(f"  should_trade('bull')  = {should_trade('bull')}")
    print(f"  should_trade('bear')  = {should_trade('bear')}")
    print("=" * 50)
