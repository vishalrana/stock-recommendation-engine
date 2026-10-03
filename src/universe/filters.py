"""
Universe Eligibility and Liquidity Filters
==========================================
Implements multi-stage security filtering:
1. Instrument Eligibility: rejects ETFs, preferreds, warrants, rights, units, notes, test issues.
2. Point-in-Time Liquidity: price >= min_price, 20-day avg dollar volume >= min_dollar_vol.
3. Historical Coverage: minimum trading days, valid prices, positive volume.
"""

import re
import logging
from typing import Tuple, Dict, Any, Optional, Set
import pandas as pd
from src.universe.models import SecurityRecord
from src.universe.normalization import is_valid_us_ticker

logger = logging.getLogger(__name__)

# Patterns matching non-common equity securities
NON_COMMON_PATTERNS = [
    r"\bWarrant\b", r"\bWarrants\b", r"\bWT\b", r"\bWts\b",
    r"\bUnit\b", r"\bUnits\b",
    r"\bPreferred\b", r"\bPreferred Stock\b", r"\bPFD\b",
    r"\bRight\b", r"\bRts\b", r"\bRights\b",
    r"\bDepositary Shares\b",
    r"\bNote\b", r"\bNotes\b", r"\bDebenture\b", r"\bDebentures\b",
    r"\bClosed-End\b", r"\bTerm Trust\b", r"\bIncome Trust\b",
]
NON_COMMON_REGEX = re.compile("|".join(NON_COMMON_PATTERNS), re.IGNORECASE)

DEFAULT_BLACKLIST = {"XYZ", "TEST", "PLACEHOLDER", "TEMP"}


def is_eligible_equity(
    record: SecurityRecord,
    delisted_tickers: Optional[Set[str]] = None,
) -> Tuple[bool, str]:
    """
    Evaluate whether a security record qualifies as an eligible US common equity.
    Returns:
        (is_eligible: bool, rejection_reason: str)
    """
    ticker = record.ticker.strip().upper()

    # 1. Basic ticker syntax check
    if not is_valid_us_ticker(ticker):
        return False, f"Invalid ticker syntax or test symbol: {ticker}"

    # 2. Blacklist check
    if ticker in DEFAULT_BLACKLIST:
        return False, f"Ticker {ticker} is in blacklist"

    # 3. Delisted / inactive check
    if not record.is_active:
        return False, f"Ticker {ticker} is marked inactive"

    if delisted_tickers and ticker in delisted_tickers:
        return False, f"Ticker {ticker} is in historical delisted list"

    # 4. Explicit test issue flag
    if record.is_test:
        return False, f"Ticker {ticker} is a test issue"

    # 5. Explicit ETF flag or instrument type
    if record.is_etf or record.instrument_type.upper() == "ETF":
        return False, f"Security is an ETF: {record.company_name}"

    # 6. Instrument type classification
    inst_type = record.instrument_type.upper()
    if inst_type in {"PREFERRED", "WARRANT", "RIGHT", "UNIT", "MUTUAL_FUND", "CEF", "NOTE", "DEBENTURE"}:
        return False, f"Security instrument type is {inst_type}"

    # 7. Name regex check for warrants, preferreds, units, rights, notes
    if NON_COMMON_REGEX.search(record.company_name):
        match = NON_COMMON_REGEX.search(record.company_name)
        return False, f"Security name indicates non-common equity: matched '{match.group(0)}'"

    # 8. Ticker suffixes indicating warrants, units, rights on certain exchanges
    if ticker.endswith(".WS") or ticker.endswith(".WT") or ticker.endswith(".W"):
        return False, f"Ticker suffix indicates warrant: {ticker}"
    if ticker.endswith(".U") or ticker.endswith(".UN"):
        return False, f"Ticker suffix indicates unit: {ticker}"
    if ticker.endswith(".RT") or ticker.endswith(".R"):
        return False, f"Ticker suffix indicates right: {ticker}"
    if ticker.endswith(".P") or "$" in ticker:
        return False, f"Ticker indicates preferred share: {ticker}"

    return True, "Eligible US common equity"


def evaluate_point_in_time_liquidity(
    df: pd.DataFrame,
    as_of_date: Optional[str] = None,
    min_price: float = 5.0,
    min_dollar_volume: float = 5_000_000.0,
    min_history_days: int = 252,
    dollar_volume_window: int = 20,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Evaluate point-in-time liquidity and data integrity for a ticker's historical OHLCV data.
    Strictly applies NO LOOKAHEAD by filtering out any data after as_of_date.

    Returns:
        (is_eligible: bool, reason: str, metrics: dict)
    """
    empty_metrics = {
        "price": 0.0,
        "avg_dollar_volume": 0.0,
        "history_days": 0,
        "latest_date": None,
    }

    if df is None or df.empty:
        return False, "No market data available", empty_metrics

    # Work on a copy to prevent accidental mutation
    work_df = df.copy()

    # Normalize column names to uppercase
    work_df.columns = [str(c).upper() for c in work_df.columns]

    if "CLOSE" not in work_df.columns or "VOLUME" not in work_df.columns:
        return False, "Missing required CLOSE or VOLUME columns", empty_metrics

    # Strict point-in-time filter (Zero lookahead)
    if as_of_date:
        work_df = work_df.loc[work_df.index <= as_of_date]

    history_days = len(work_df)
    if history_days < min_history_days:
        return False, f"Insufficient history: {history_days} days < {min_history_days} required", {
            "price": float(work_df["CLOSE"].iloc[-1]) if history_days > 0 else 0.0,
            "avg_dollar_volume": 0.0,
            "history_days": history_days,
            "latest_date": str(work_df.index[-1]) if history_days > 0 else None,
        }

    # Data integrity checks
    close_series = work_df["CLOSE"]
    volume_series = work_df["VOLUME"]

    if close_series.isna().any() or volume_series.isna().any():
        # Check if missing recent rows
        if close_series.tail(dollar_volume_window).isna().any():
            return False, "Missing or NaN price data in recent observation window", empty_metrics

    # Get most recent valid bar
    latest_close = float(close_series.iloc[-1])
    latest_volume = float(volume_series.iloc[-1])
    latest_date_str = str(work_df.index[-1])

    if latest_close <= 0.0 or pd.isna(latest_close):
        return False, f"Invalid non-positive price: {latest_close}", empty_metrics

    if latest_volume < 0.0:
        return False, f"Invalid negative volume: {latest_volume}", empty_metrics

    # Calculate rolling 20-day dollar volume: mean(close * volume)
    recent_closes = close_series.tail(dollar_volume_window)
    recent_volumes = volume_series.tail(dollar_volume_window)
    avg_dollar_volume = float((recent_closes * recent_volumes).mean())

    metrics = {
        "price": latest_close,
        "avg_dollar_volume": avg_dollar_volume,
        "history_days": history_days,
        "latest_date": latest_date_str,
    }

    # Price filter
    if latest_close < min_price:
        return False, f"Price ${latest_close:.2f} is below minimum ${min_price:.2f}", metrics

    # Dollar volume filter
    if avg_dollar_volume < min_dollar_volume:
        return (
            False,
            f"20-day avg dollar volume ${avg_dollar_volume:,.0f} is below minimum ${min_dollar_volume:,.0f}",
            metrics,
        )

    return True, "Passed point-in-time liquidity and data integrity filters", metrics
