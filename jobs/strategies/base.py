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


def consensus_pass(row) -> bool:
    """
    Multi-Indicator Consensus Gate (Task 6.2)
    Computes a continuous score across RSI, ADX, Volume Ratio, and DMA 50.
    Returns True if average score > 0.65 and at least 3 indicators show strength (> 0.5).
    """
    try:
        rsi = float(row.get('RSI_14', 55.0))
    except (ValueError, TypeError):
        rsi = 55.0

    try:
        adx = float(row.get('ADX_14', 20.0))
    except (ValueError, TypeError):
        adx = 20.0

    try:
        vol_ratio = float(row.get('volume_ratio', row.get('VOLUME_RATIO', 1.0)))
    except (ValueError, TypeError):
        vol_ratio = 1.0

    try:
        close = float(row.get('CLOSE', row.get('Close', 1.0)))
    except (ValueError, TypeError):
        close = 1.0

    try:
        sma_50 = float(row.get('DMA_50', close))
    except (ValueError, TypeError):
        sma_50 = close
    
    rsi_score = 1.0 - abs(rsi - 55.0) / 35.0
    adx_score = min(1.0, (adx - 10.0) / 25.0)
    vol_score = min(1.0, (vol_ratio - 0.5) / 1.5)
    dma_score = min(1.0, (close / sma_50 - 0.95) / 0.15) if sma_50 > 0 else 1.0
    
    avg = (rsi_score + adx_score + vol_score + dma_score) / 4.0
    passes_count = sum(s > 0.5 for s in [rsi_score, adx_score, vol_score, dma_score])
    
    return avg > 0.65 and passes_count >= 3

