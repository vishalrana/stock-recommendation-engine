from abc import ABC, abstractmethod
from typing import Optional, List
import pandas as pd


class StrategyInterface(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name for UI."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line explanation for UI."""
        pass

    @abstractmethod
    def minimum_confidence(self) -> str:
        """Minimum tier to include in output. E.g., 'Buy' or 'Watch'."""
        pass

    @abstractmethod
    def scan(self, ticker: str, df: pd.DataFrame, regime: str, metrics: dict) -> Optional[dict]:
        """
        Scan a single ticker. Return standardized signal dict or None.
        Must include ALL these keys:
        ticker, company_name, price, entry_price, stop_loss,
        target_1, target_2, target_3, target_1_pct, target_2_pct, target_3_pct,
        weighted_rr, position_sizing, risk_dollar, risk_pct, composite_score,
        tier_label, quality_score, narrative, past_win_rate, total_trades,
        expectancy_pct, current_rsi, adx_value, volume_ratio, macd_histogram,
        ema20, is_blocked, blocked_reason, strategy, context_score.
        """
        pass

    @abstractmethod
    def rank_candidates(self, candidates: List[dict], regime: str) -> List[dict]:
        """
        Rank and filter candidates. Return final list for this strategy.
        Must preserve existing ranking logic (SignalRanker composite scoring).
        """
        pass
