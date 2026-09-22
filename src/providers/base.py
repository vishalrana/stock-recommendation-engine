from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional, Dict
import pandas as pd
from dataclasses import dataclass, field

# --- Data Quality Enum (P1-4) ---
class DataQuality(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    MALFORMED = "MALFORMED"

# --- Price Data Structures ---
@dataclass
class PriceData:
    open: float
    high: float
    low: float
    close: float
    volume: int

class PriceProvider(ABC):
    @abstractmethod
    def get_historical(self, tickers: List[str], start: str, end: str) -> pd.DataFrame:
        """
        Returns MultiIndex DataFrame (Ticker, Date) with columns: Open, High, Low, Close, Volume.
        """
        pass

# --- Context Data Structures ---
@dataclass
class AnalystContext:
    target_mean_price: Optional[float] = None
    recommendation: Optional[str] = None  # "buy", "hold", "sell", "strong_buy", etc.
    num_analysts: Optional[int] = None
    quality: DataQuality = DataQuality.UNAVAILABLE

@dataclass
class FundamentalContext:
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    trailing_pe: Optional[float] = None
    quality: DataQuality = DataQuality.UNAVAILABLE

@dataclass
class EarningsContext:
    surprise_percent: Optional[float] = None  # Positive if beat, negative if miss
    is_recent: bool = False  # Within the last 60 days
    quality: DataQuality = DataQuality.UNAVAILABLE

@dataclass
class NewsContext:
    headline_sentiment: float = 0.0  # Range -1 (very bad) to +1 (very good)
    article_count: int = 0
    source_reliability: float = 0.5  # 0.5 default
    quality: DataQuality = DataQuality.UNAVAILABLE

@dataclass
class AggregatedContext:
    analyst: AnalystContext = field(default_factory=AnalystContext)
    fundamental: FundamentalContext = field(default_factory=FundamentalContext)
    earnings: EarningsContext = field(default_factory=EarningsContext)
    news: NewsContext = field(default_factory=NewsContext)
    price_volume_signal: float = 0.0  # 0.0 to 1.0 proxy
    cached_score: Optional[float] = None
    quality: DataQuality = DataQuality.UNAVAILABLE

    @property
    def de_ratio(self) -> Optional[float]:
        return self.fundamental.debt_to_equity if self.fundamental else None

    @property
    def current_ratio(self) -> Optional[float]:
        return self.fundamental.current_ratio if self.fundamental else None

    @property
    def earnings_surprise_pct(self) -> Optional[float]:
        return self.earnings.surprise_percent if self.earnings else None

    @property
    def finbert_sentiment(self) -> Optional[float]:
        return self.news.headline_sentiment if self.news else None

    @property
    def target_consensus(self) -> Optional[float]:
        return self.analyst.target_mean_price if self.analyst else None

class ContextProvider(ABC):
    @abstractmethod
    def get_context(self, ticker: str, price_df: pd.DataFrame) -> AggregatedContext:
        """Fetch all non-price data for a single ticker."""
        pass
