"""
Ticker Normalization Module
===========================
Handles bi-directional mapping between canonical exchange tickers (e.g., 'BRK.B')
and data provider formats (e.g., 'BRK-B' for yfinance and cache storage).
"""

import re
from typing import Optional


def to_canonical_ticker(symbol: str) -> str:
    """
    Convert any ticker format to canonical exchange format.
    Examples:
        'BRK-B' -> 'BRK.B'
        'brk.b' -> 'BRK.B'
        'AAPL'  -> 'AAPL'
        'BF-A'  -> 'BF.A'
    """
    if not symbol:
        return ""
    s = symbol.strip().upper()
    
    # Handle common share-class suffixes separated by hyphen
    # If pattern is TICKER-A or TICKER-B (single letter class), map to dot
    match = re.match(r"^([A-Z]{1,5})-([A-Z])$", s)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
        
    return s


def to_provider_ticker(symbol: str) -> str:
    """
    Convert canonical ticker to data provider (yfinance / parquet cache) format.
    Yahoo Finance uses hyphens for share classes instead of dots.
    Examples:
        'BRK.B' -> 'BRK-B'
        'BF.A'  -> 'BF-A'
        'AAPL'  -> 'AAPL'
    """
    if not symbol:
        return ""
    s = symbol.strip().upper()
    return s.replace(".", "-").replace("/", "-")


def is_valid_us_ticker(symbol: str) -> bool:
    """
    Validate that a symbol conforms to standard US equity ticker syntax.
    Allows 1-5 letters, optionally followed by .A-.Z or -A--Z for share classes.
    Rejects test symbols, control characters, empty strings, and special characters like $.
    """
    if not symbol or not isinstance(symbol, str):
        return False
    s = symbol.strip().upper()
    if len(s) == 0 or len(s) > 8:
        return False
    # Reject test tickers
    if s in {"XYZ", "TEST", "PLACEHOLDER", "TEMP"}:
        return False
    # Disallow preferred share notations containing $ or = or ^
    if any(c in s for c in ["$", "^", "=", "@", "*", "+"]):
        return False
    # Standard format: 1-5 alpha chars, optionally followed by .X or -X
    return bool(re.match(r"^[A-Z]{1,5}([.\-][A-Z])?$", s))
