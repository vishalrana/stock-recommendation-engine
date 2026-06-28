from abc import ABC, abstractmethod
from typing import List, Optional, Dict
import pandas as pd
from dataclasses import dataclass, field

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

@dataclass
class FundamentalContext:
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    trailing_pe: Optional[float] = None

@dataclass
class EarningsContext:
    surprise_percent: Optional[float] = None  # Positive if beat, negative if miss
    is_recent: bool = False  # Within the last 60 days

@dataclass
class NewsContext:
    headline_sentiment: float = 0.0  # Range -1 (very bad) to +1 (very good)
    article_count: int = 0
    source_reliability: float = 0.5  # 0.5 default

@dataclass
class AggregatedContext:
    analyst: AnalystContext = field(default_factory=AnalystContext)
    fundamental: FundamentalContext = field(default_factory=FundamentalContext)
    earnings: EarningsContext = field(default_factory=EarningsContext)
    news: NewsContext = field(default_factory=NewsContext)
    price_volume_signal: float = 0.0  # 0.0 to 1.0 proxy

class ContextProvider(ABC):
    @abstractmethod
    def get_context(self, ticker: str, price_df: pd.DataFrame) -> AggregatedContext:
        """Fetch all non-price data for a single ticker."""
        pass
