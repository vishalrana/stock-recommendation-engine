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
