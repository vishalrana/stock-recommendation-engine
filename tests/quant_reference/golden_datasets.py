"""
Fixed Golden Datasets Module
============================
Deterministic benchmark datasets for quantitative verification:
1. Dataset 1: Monotonic uptrend
2. Dataset 2: Monotonic downtrend
3. Dataset 3: Flat market
4. Dataset 4: Alternating gains/losses
5. Dataset 5: Volatile market
6. Dataset 6: Gap-up / gap-down market
7. Dataset 7: Missing / invalid data
8. Dataset 8: Realistic multi-regime market
"""

import pandas as pd
import numpy as np
from typing import Tuple


def get_dates(n: int, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n, freq="B")


def dataset_1_monotonic_uptrend(n: int = 100) -> pd.DataFrame:
    """Dataset 1: Strictly rising prices, higher highs and higher lows."""
    dates = get_dates(n)
    base = 100.0 + np.arange(n) * 1.5
    high = base + 1.0
    low = base - 0.5
    open_p = base - 0.2
    close = base + 0.8
    vol = np.full(n, 1_000_000.0)
    return pd.DataFrame({"OPEN": open_p, "HIGH": high, "LOW": low, "CLOSE": close, "VOLUME": vol}, index=dates)


def dataset_2_monotonic_downtrend(n: int = 100) -> pd.DataFrame:
    """Dataset 2: Strictly falling prices, lower highs and lower lows."""
    dates = get_dates(n)
    base = 250.0 - np.arange(n) * 1.5
    high = base + 0.5
    low = base - 1.0
    open_p = base + 0.2
    close = base - 0.8
    vol = np.full(n, 1_000_000.0)
    return pd.DataFrame({"OPEN": open_p, "HIGH": high, "LOW": low, "CLOSE": close, "VOLUME": vol}, index=dates)


def dataset_3_flat_market(n: int = 100) -> pd.DataFrame:
    """Dataset 3: Perfectly stationary prices with zero movement."""
    dates = get_dates(n)
    p = np.full(n, 100.0)
    return pd.DataFrame({"OPEN": p, "HIGH": p, "LOW": p, "CLOSE": p, "VOLUME": np.full(n, 500_000.0)}, index=dates)


def dataset_4_alternating(n: int = 100) -> pd.DataFrame:
    """Dataset 4: Alternating gain and loss bars (+2.0, -2.0)."""
    dates = get_dates(n)
    pattern = np.array([100.0, 102.0] * (n // 2 + 1))[:n]
    high = pattern + 0.5
    low = pattern - 0.5
    open_p = pattern
    close = pattern
    return pd.DataFrame({"OPEN": open_p, "HIGH": high, "LOW": low, "CLOSE": close, "VOLUME": np.full(n, 800_000.0)}, index=dates)


def dataset_5_volatile_market(n: int = 100) -> pd.DataFrame:
    """Dataset 5: Mean-reverting series with large intraday swings and high ATR."""
    dates = get_dates(n)
    np.random.seed(42)
    noise = np.sin(np.linspace(0, 20, n)) * 10.0 + 100.0
    high = noise + 5.0
    low = noise - 5.0
    open_p = noise - 1.0
    close = noise + 1.0
    return pd.DataFrame({"OPEN": open_p, "HIGH": high, "LOW": low, "CLOSE": close, "VOLUME": np.full(n, 2_000_000.0)}, index=dates)


def dataset_6_gap_market(n: int = 100) -> pd.DataFrame:
    """Dataset 6: Significant overnight gaps up and down."""
    dates = get_dates(n)
    closes = np.full(n, 100.0)
    # Inject large gaps
    closes[20:40] = 130.0  # +30% gap up
    closes[40:60] = 90.0   # -30.7% gap down
    closes[60:80] = 110.0  # +22% gap up
    high = closes + 1.0
    low = closes - 1.0
    open_p = closes
    return pd.DataFrame({"OPEN": open_p, "HIGH": high, "LOW": low, "CLOSE": closes, "VOLUME": np.full(n, 1_500_000.0)}, index=dates)


def dataset_7_invalid_anomalous_market(n: int = 100) -> pd.DataFrame:
    """Dataset 7: Data containing NaN, negative price, and geometric violations."""
    df = dataset_1_monotonic_uptrend(n).copy()
    # Inject invalid bars for invariant testing
    df.loc[df.index[10], "CLOSE"] = -10.0  # Negative price
    df.loc[df.index[20], "HIGH"] = df.loc[df.index[20], "LOW"] - 5.0  # High < Low
    df.loc[df.index[30], "VOLUME"] = -500.0  # Negative volume
    df.loc[df.index[40], "CLOSE"] = np.nan  # NaN
    return df


def dataset_8_realistic_multi_regime(n: int = 300) -> pd.DataFrame:
    """Dataset 8: 300-day realistic trajectory: Bull rally -> Sideways consolidation -> Bear drop."""
    dates = get_dates(n)
    prices = np.zeros(n)
    p = 100.0
    for i in range(n):
        if i < 100:
            # Bull regime: positive drift
            p += 0.4 + 0.1 * np.sin(i)
        elif i < 200:
            # Sideways regime: oscillation around 140
            p = 140.0 + 3.0 * np.sin(i * 0.3)
        else:
            # Bear regime: negative drift
            p -= 0.5 + 0.1 * np.cos(i)
        prices[i] = max(10.0, p)

    high = prices + 1.2
    low = prices - 1.2
    open_p = prices - 0.2
    close = prices + 0.3
    vol = 1_000_000.0 + 200_000.0 * np.sin(np.arange(n) * 0.1)
    return pd.DataFrame({"OPEN": open_p, "HIGH": high, "LOW": low, "CLOSE": close, "VOLUME": vol}, index=dates)


def dataset_9_pseudorandom_seeded(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Dataset 9: Seeded Geometric Brownian motion with deterministic seed."""
    dates = get_dates(n)
    np.random.seed(seed)
    # Drift = 0.0005, Volatility = 0.015
    returns = np.random.normal(loc=0.0005, scale=0.015, size=n)
    prices = 100.0 * np.exp(np.cumsum(returns))
    high = prices * (1.0 + np.abs(np.random.normal(0, 0.008, size=n)))
    low = prices * (1.0 - np.abs(np.random.normal(0, 0.008, size=n)))
    open_p = (prices + low) / 2.0
    vol = np.random.uniform(500_000.0, 2_000_000.0, size=n)
    return pd.DataFrame({"OPEN": open_p, "HIGH": high, "LOW": low, "CLOSE": prices, "VOLUME": vol}, index=dates)


def dataset_10_insufficient_history(n: int = 10) -> pd.DataFrame:
    """Dataset 10: Less than 20 bars of history (insufficient for standard 14/20d lookbacks)."""
    return dataset_1_monotonic_uptrend(n)


def dataset_11_sporadic_nans(n: int = 100) -> pd.DataFrame:
    """Dataset 11: Valid dataset with sporadic NaN injections for NaN immunity verification."""
    df = dataset_1_monotonic_uptrend(n).copy()
    df.loc[df.index[15], "HIGH"] = np.nan
    df.loc[df.index[35], "LOW"] = np.nan
    df.loc[df.index[55], "VOLUME"] = np.nan
    return df


def dataset_12_extreme_pathological(n: int = 100) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Dataset 12: Micro-cap penny stock ($0.05) and mega-cap stock ($500,000) for numeric stability."""
    dates = get_dates(n)
    # Penny stock
    penny_p = 0.05 + np.sin(np.linspace(0, 5, n)) * 0.01
    penny_df = pd.DataFrame({
        "OPEN": penny_p, "HIGH": penny_p + 0.005, "LOW": penny_p - 0.005,
        "CLOSE": penny_p, "VOLUME": np.full(n, 10_000_000.0)
    }, index=dates)

    # Mega-price stock (e.g., BRK.A scale)
    mega_p = 500_000.0 + np.linspace(0, 50_000, n)
    mega_df = pd.DataFrame({
        "OPEN": mega_p, "HIGH": mega_p + 1000.0, "LOW": mega_p - 1000.0,
        "CLOSE": mega_p, "VOLUME": np.full(n, 500.0)
    }, index=dates)
    return penny_df, mega_df
