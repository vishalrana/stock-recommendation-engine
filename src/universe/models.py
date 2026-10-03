"""
Universe Data Models
====================
Defines structured records for securities across markets.
Designed for US equities today and extensible to global/India markets.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any


@dataclass
class SecurityRecord:
    """Represents a single security in a market universe."""
    ticker: str                         # Canonical ticker symbol (e.g., 'AAPL', 'BRK.B')
    company_name: str                   # Clean company title (e.g., 'Apple Inc.')
    exchange: str                       # Primary exchange (e.g., 'NASDAQ', 'NYSE', 'AMEX')
    sector: str = "Unknown"             # GICS / Market sector (e.g., 'Technology')
    industry: str = "Unknown"           # Industry / Sub-industry
    instrument_type: str = "COMMON"     # 'COMMON', 'ADR', 'ETF', 'PREFERRED', 'WARRANT', etc.
    market: str = "US"                  # Market identifier (e.g., 'US', 'IN')
    country: str = "US"                 # Domicile country
    currency: str = "USD"               # Trading currency
    is_active: bool = True              # Active trading status
    is_etf: bool = False                # Explicit ETF flag
    is_test: bool = False               # Test issue flag
    cik: Optional[int] = None           # SEC Central Index Key (if available)
    data_provider_ticker: Optional[str] = None  # Provider-specific format (e.g., 'BRK-B' for yfinance)

    def __post_init__(self):
        self.ticker = self.ticker.strip().upper()
        if not self.data_provider_ticker:
            # Default provider ticker: replace dot with hyphen for Yahoo Finance compatibility
            self.data_provider_ticker = self.ticker.replace(".", "-")
        else:
            self.data_provider_ticker = self.data_provider_ticker.strip().upper()

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SecurityRecord":
        """Instantiate record from dictionary."""
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)
