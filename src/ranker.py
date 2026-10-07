"""
Signal Ranker — Strategy 1.3 Rev B
===================================
Composite-normalized, tiered ranking engine for Strategy 1.3.

Weights:
  - Technical Momentum (25%): RSI, Proximity to 50 DMA, Volume Ratio, MACD histogram
  - Risk-Adjusted Expectancy (35%): Z-score in pool, with negative expectancy penalty
  - Historical Win Rate (15%): Percentile rank
  - Regime Adjustment (10%): Bull/Bear/Sideways specific bonus
  - Context Score (15%): Analyst, earnings, fundamentals, news, price/volume events
"""

import logging
import math
import os
from typing import Optional
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime

from src.providers.context.aggregator import ContextAggregator
from src.scorers.context_scorer import ContextScorer
from src.data.cache_manager import get_cache_manager

logger = logging.getLogger(__name__)


# Import canonical single source of truth configuration
from src.quant_config import (
    STRATEGY_WEIGHT_VECTORS,
    REGIME_SCORE_MATRIX,
    STRATEGY_HISTORICAL_EXPECTANCY,
    SURVIVORSHIP_BIAS_HAIRCUT,
    EXPECTANCY_BASE,
    EXPECTANCY_SLOPE,
    CONTEXT_VETO_THRESHOLDS,
)

# Backward-compatibility aliases
STRATEGY_OPTIMAL_REGIME = {
    'trend_following': 100,
    '52w_high_breakout': 100,
    'pullback_recovery': 70,
    'cross_sectional_momentum': 85,
    'pead': 75,
    'sector_rotation': 80,
    'mean_reversion': 30,
}
MARKET_REGIME_SCORE = {'bull': 100.0, 'sideways': 70.0, 'bear': 20.0}


def normalize_strategy_key(strategy: str) -> str:
    """Standardize strategy names to internal dictionary keys."""
    if not strategy:
        return 'trend_following'
    s = str(strategy).strip().lower().replace('-', '_').replace(' ', '_')
    if '52' in s or 'breakout' in s or 'high' in s:
        return '52w_high_breakout'
    if 'trend' in s:
        return 'trend_following'
    if 'pullback' in s:
        return 'pullback_recovery'
    if 'cross' in s or 'momentum' in s:
        return 'cross_sectional_momentum'
    if 'pead' in s or 'earnings' in s:
        return 'pead'
    if 'sector' in s or 'rotation' in s:
        return 'sector_rotation'
    if 'mean' in s or 'reversion' in s:
        return 'mean_reversion'
    return s


def compute_expectancy_score(strategy: str, adjusted_expectancy_pct: Optional[float] = None) -> float:
    """
    Master Spec v2.3+ formula: S_exp = 30 + 20 * E_adjusted
    where E_adjusted is expressed in percentage points (e.g. +1.44% -> 1.44 -> 58.8).
    Strictly clamped to [0.0, 100.0] with null/NaN protection.
    """
    if adjusted_expectancy_pct is not None and not (isinstance(adjusted_expectancy_pct, float) and np.isnan(adjusted_expectancy_pct)):
        e_val = float(adjusted_expectancy_pct)
    else:
        strat_key = normalize_strategy_key(strategy)
        hist_exp = STRATEGY_HISTORICAL_EXPECTANCY.get(strat_key, 0.0169 * SURVIVORSHIP_BIAS_HAIRCUT)
        e_val = round(hist_exp * 100.0, 2)
    raw_score = EXPECTANCY_BASE + EXPECTANCY_SLOPE * e_val
    clamped_score = max(0.0, min(100.0, raw_score))
    return round(clamped_score, 4)


def compute_regime_alignment(strategy: str, market_regime: str) -> float:
    """
    Master Spec v2.3+ Exact Regime Score Matrix.
    Direct discrete matrix lookup across Bull, Sideways, and Bear regimes.
    """
    strat_key = normalize_strategy_key(strategy)
    regime_key = str(market_regime).strip().lower()
    if strat_key not in REGIME_SCORE_MATRIX:
        strat_key = "trend_following"
    regime_dict = REGIME_SCORE_MATRIX[strat_key]
    return float(regime_dict.get(regime_key, regime_dict.get("sideways", 70.0)))


def compute_context_score(
    analyst_pts: float = 0.0,
    earnings_pts: float = 0.0,
    fundamental_pts: float = 0.0,
    news_pts: float = 0.0,
    de_ratio: Optional[float] = None,
    current_ratio: Optional[float] = None,
    earnings_surprise_pct: Optional[float] = None,
    finbert_sentiment: Optional[float] = None,
    target_consensus: Optional[float] = None,
    price: Optional[float] = None,
    base_score: Optional[float] = None,
) -> float:
    """
    Master Spec Context Score (Earnings Decoupled):
    Calculates context score from non-earnings factors (Analyst, Fundamental, News)
    normalized to a 0-100 scale, with Veto Gates:
    - Balance Sheet Distress: D/E > 2.5 AND Current Ratio < 1.0 -> cap context at 30.0
    - Negative News Sentiment: FinBERT < -0.30 -> cap context at 40.0
    - Analyst Downside: Target < Price -> penalty -15.0

    Earnings data is completely excluded from the initial score.
    (Veto Gate 3 severe earnings miss is evaluated downstream in earnings_risk_filter).
    """
    if base_score is not None:
        raw = min(100.0, max(0.0, float(base_score)))
    else:
        raw_non_earnings = float(analyst_pts or 0.0) + float(fundamental_pts or 0.0) + float(news_pts or 0.0)
        # Normalize non-earnings components: unscaled ceiling is 80 (Analyst 40, Fundamental 20, News 20)
        # Scaled by 100 / 80 = 1.25 to map cleanly to [0, 100].
        raw = min(100.0, raw_non_earnings * 1.25) if raw_non_earnings > 0 else 0.0

    # Veto Gate 1: Dangerous leverage + poor liquidity (D/E > 2.5 AND Current Ratio < 1.0)
    if de_ratio is not None and current_ratio is not None:
        try:
            if float(de_ratio) > 2.5 and float(current_ratio) < 1.0:
                raw = min(raw, 30.0)
        except (ValueError, TypeError):
            pass

    # Veto Gate 2: Negative news sentiment (FinBERT < -0.30)
    if finbert_sentiment is not None:
        try:
            if float(finbert_sentiment) < -0.30:
                raw = min(raw, 40.0)
        except (ValueError, TypeError):
            pass

    # Veto Gate 4: Analyst downside (Target < Entry Price)
    if target_consensus is not None and price is not None:
        try:
            p = float(price)
            tc = float(target_consensus)
            if p > 0 and tc < p:
                raw = max(0.0, raw - 15.0)
        except (ValueError, TypeError):
            pass

    return max(0.0, min(100.0, float(raw)))


def assign_tier(composite_score: float, honest_rr: float = 2.0, has_strategy_setup: bool = True) -> str:
    """
    Assign recommendation tier based strictly on composite score and strategy qualification.
    R:R is preserved as an analytical output and for signature compatibility only,
    and NEVER qualifies or disqualifies a recommendation.
    If has_strategy_setup is False, returns 'NO_SETUP' (preventing unmerited Buy tiers).
    - Strong Buy: composite_score >= 80.0
    - Buy: composite_score >= 65.0
    - Rejected: all others
    """
    if not has_strategy_setup:
        return "NO_SETUP"
    score = float(composite_score)
    if score >= 80.0:
        return "Strong Buy"
    elif score >= 65.0:
        return "Buy"
    else:
        return "Rejected"


def compute_momentum_score(row: dict) -> float:
    """
    P0-2 & P1-1 & P1-2: Explicit continuous technical momentum score (0-100).
    Uses RSI, DMA 50 proximity, Volume Ratio, and MACD Histogram (normalized by ATR).
    """
    rsi = row.get("current_rsi")
    price = row.get("price") if row.get("price") is not None else row.get("entry_price")
    dma_50 = row.get("dma_50") if row.get("dma_50") is not None else row.get("ema20")
    volume_ratio = row.get("volume_ratio")
    macd_hist = row.get("macd_histogram", 0.0)

    if rsi is None or price is None or dma_50 is None or volume_ratio is None:
        raise ValueError(
            f"Missing required technical momentum features: rsi={rsi}, price={price}, dma_50={dma_50}, volume_ratio={volume_ratio}"
        )

    rsi_val = float(rsi)
    p_val = float(price)
    d_val = float(dma_50)
    v_val = float(volume_ratio)
    m_val = float(macd_hist or 0.0)

    # RSI score (P1-2: Canonical design intentionally penalizes overbought / overextended
    # deviation from the 50 median line to protect against chasing exhausted swings):
    rsi_score = max(0.0, min(100.0, 100.0 - abs(rsi_val - 50.0) * 4.0))

    # Proximity score to DMA 50
    proximity = abs(p_val / d_val - 1.0) if d_val > 0 else 0.0
    proximity_score = max(0.0, min(100.0, 100.0 - proximity * 500.0))

    # Volume score
    volume_score = max(0.0, min(100.0, v_val * 50.0))

    # MACD score normalized by ATR (P1-1):
    # Normalized by ATR (or price proxy) to eliminate dollar-price scale bias between $20 and $500 stocks.
    atr = float(row.get("atr_14") or row.get("atr") or (p_val * 0.02 if p_val > 0 else 1.0))
    if atr <= 0.0:
        atr = p_val * 0.02 if p_val > 0 else 1.0
    macd_norm = m_val / atr if atr > 0 else 0.0
    macd_score = max(0.0, min(100.0, 50.0 + macd_norm * 200.0))

    raw_momentum = (rsi_score + proximity_score + volume_score + macd_score) / 4.0

    # Sigmoid weighting around 55.0
    sigmoid_weight = 1.0 / (1.0 + math.exp(-(raw_momentum - 55.0) / 5.0))
    momentum_score = raw_momentum * (0.5 + 0.5 * sigmoid_weight)
    return round(max(0.0, min(100.0, momentum_score)), 4)


def validate_candidate_features(row: dict) -> tuple[bool, str]:
    """
    P0-2: Validate that all required production features exist before composite scoring.
    """
    ticker = row.get("ticker")
    if not ticker:
        return False, "Missing ticker"

    strategy = row.get("strategy") or row.get("strategy_name")
    if not strategy:
        return False, "Missing strategy"

    # Momentum check: must either have momentum_score or technical inputs to compute it
    if row.get("momentum_score") is None:
        has_tech = (
            row.get("current_rsi") is not None
            and (row.get("price") is not None or row.get("entry_price") is not None)
            and (row.get("dma_50") is not None or row.get("ema20") is not None)
            and row.get("volume_ratio") is not None
        )
        if not has_tech:
            return False, "Missing momentum_score and required technical momentum features"

    # Win rate check: must have winrate_score or win_rate or past_win_rate
    if row.get("winrate_score") is None and row.get("win_rate") is None and row.get("past_win_rate") is None:
        return False, "Missing winrate_score / past_win_rate"

    return True, "Valid"


class SignalRanker:
    """
    Composite-normalized ranking engine with tiered fallback.
    """

    WEIGHT_MOMENTUM = 0.25
    WEIGHT_EXPECTANCY = 0.35
    WEIGHT_WIN_RATE = 0.15
    WEIGHT_REGIME = 0.10
    WEIGHT_CONTEXT = 0.15

    def __init__(self, min_expectancy: float = 0, min_win_rate: float = 25, min_trades: int = 5):
        # Kept for backward compatibility
        self.min_expectancy = min_expectancy
        self.min_win_rate = min_win_rate
        self.min_trades = min_trades
        self.signals_strong_buy = 0
        self.signals_buy = 0
        self.signals_watch = 0
        self.signals_speculative = 0
        
        # Context Providers & Scorer
        self.context_aggregator = ContextAggregator()
        self.context_scorer = ContextScorer()

    def normalize_percentile(self, series: pd.Series) -> pd.Series:
        """Convert a numeric Series to percentile ranks in [0, 100]."""
        if len(series) <= 1:
            return pd.Series([100.0] * len(series), index=series.index)
        return series.rank(pct=True, method="average") * 100.0

    def regime_adjustment(self, score: float, regime: str, stock_metrics: dict) -> float:
        """
        Calculate regime adjustment score (0-100).
        Delegates to continuous compute_regime_alignment if strategy is provided,
        otherwise preserves original defensive/momentum heuristic for backward compatibility.
        """
        strat = stock_metrics.get("strategy_name") or stock_metrics.get("strategy")
        if strat:
            return compute_regime_alignment(strat, regime)

        rsi = stock_metrics.get("current_rsi", 50.0)
        price = stock_metrics.get("price", 0.0)
        dma_50 = stock_metrics.get("dma_50", 0.0)
        industry = stock_metrics.get("industry", "")
        beta = stock_metrics.get("beta", 1.0)

        if regime == "bull":
            if (50.0 <= rsi <= 70.0) and (price > dma_50):
                return 100.0
            return 0.0
        elif regime == "bear":
            defensive_industries = {
                "Utilities", "Consumer Staples", "Health Care", 
                "Insurance", "Telecommunication Services"
            }
            ind_clean = str(industry).strip()
            is_defensive = (ind_clean in defensive_industries) or any(
                d in ind_clean for d in defensive_industries
            )
            if is_defensive or (beta < 1.0):
                return 100.0
            return 0.0
        elif regime == "sideways":
            if abs(rsi - 50.0) < 8.0:
                return 100.0
            return 0.0
        
        return 0.0

    def compute_composite_score(self, row, regime: str, pool_stats: dict = None) -> dict:
        """
        Compute the final composite score and breakdown for a candidate.
        Uses Strategy-Specific Weights (Fix 4), Historical Expectancy (Fix 2),
        Veto-Gated Context (Fix 3), and Continuous Regime Alignment (Fix 5).
        P0-2: No silent neutral/zero defaults for missing features.
        """
        strategy = row.get("strategy_name") or row.get("strategy") or "trend_following"
        strat_key = normalize_strategy_key(strategy)

        # 1. Momentum score (P0-2: No silent 50.0 fallback)
        if "momentum_score" in row and row["momentum_score"] is not None:
            momentum_score = float(row["momentum_score"])
        else:
            momentum_score = compute_momentum_score(row)

        # 2. Historical Strategy Expectancy (Fix 2: No circular R:R)
        if "expectancy_score" in row and row["expectancy_score"] is not None:
            expectancy_score = float(row["expectancy_score"])
        else:
            exp_val = row.get("expectancy_pct") if row.get("expectancy_pct") is not None else row.get("adjusted_expectancy_pct")
            expectancy_score = compute_expectancy_score(strat_key, exp_val)

        # 3. Historical Win Rate score (P0-2: No silent 50.0 fallback)
        winrate_val = row.get("winrate_score")
        if winrate_val is None:
            winrate_val = row.get("win_rate")
        if winrate_val is None:
            winrate_val = row.get("past_win_rate")
        if winrate_val is None:
            raise ValueError(f"Missing required field 'winrate_score'/'win_rate' for {row.get('ticker', 'unknown')}")
        winrate_score = float(winrate_val)

        # 4. Continuous Strategy-Dependent Regime Alignment (Fix 5)
        if "regime_score" in row and row["regime_score"] is not None:
            regime_score = float(row["regime_score"])
        else:
            regime_score = compute_regime_alignment(strat_key, regime)

        # 5. Context Score with Veto Gates (Fix 3)
        c_analyst = float(row.get("context_analyst", 0.0) or 0.0)
        c_earnings = float(row.get("context_earnings", 0.0) or 0.0)
        c_fundamental = float(row.get("context_fundamental", 0.0) or 0.0)
        c_news = float(row.get("context_news", 0.0) or 0.0)

        has_breakdown = any(
            float(row.get(k) or 0.0) > 0
            for k in ("context_analyst", "context_fundamental", "context_news")
        )

        if not has_breakdown and "context_score" in row and row["context_score"] is not None:
            # If only raw context_score was passed, apply veto gates directly to that base score
            context_score = compute_context_score(
                base_score=float(row["context_score"]),
                de_ratio=row.get("de_ratio"),
                current_ratio=row.get("current_ratio"),
                finbert_sentiment=row.get("finbert_sentiment"),
                target_consensus=row.get("target_consensus"),
                price=row.get("price") or row.get("entry_price"),
            )
        else:
            context_score = compute_context_score(
                analyst_pts=c_analyst,
                fundamental_pts=c_fundamental,
                news_pts=c_news,
                de_ratio=row.get("de_ratio"),
                current_ratio=row.get("current_ratio"),
                finbert_sentiment=row.get("finbert_sentiment"),
                target_consensus=row.get("target_consensus"),
                price=row.get("price") or row.get("entry_price"),
            )

        # Strategy-Specific Weight Vector (Fix 4)
        w = STRATEGY_WEIGHT_VECTORS.get(strat_key, STRATEGY_WEIGHT_VECTORS["trend_following"])

        # Assert weights sum to 1.0
        assert abs(sum(w.values()) - 1.0) < 1e-9, f"Weights for {strat_key} must sum to 1.0!"

        total = (
            w["mom"] * momentum_score
            + w["exp"] * expectancy_score
            + w["wr"] * winrate_score
            + w["reg"] * regime_score
            + w["ctx"] * context_score
        )

        honest_rr = float(row.get("weighted_scaleout_rr") or row.get("weighted_rr_honest") or row.get("weighted_rr") or row.get("risk_reward") or 2.0)
        tier_label = assign_tier(total, honest_rr)

        return {
            "total": round(total, 4),
            "composite_score": round(total, 4),
            "tier_label": tier_label,
            "breakdown": {
                "momentum": round(momentum_score, 4),
                "expectancy": round(expectancy_score, 4),
                "winrate": round(winrate_score, 4),
                "regime": round(regime_score, 4),
                "context": round(context_score, 4),
            },
            "strategy": strat_key,
            "weights": w,
        }

    def composite_rank(self, df: pd.DataFrame, regime: str, top_n: int = 5) -> pd.DataFrame:
        """
        Canonical composite ranking pipeline delegating directly to compute_composite_score().
        Preserves backward compatibility while strictly enforcing the frozen quantitative specification.
        """
        if df.empty:
            return df.copy()

        result = df.copy()
        scored_rows = []

        self.signals_strong_buy = 0
        self.signals_buy = 0
        self.signals_watch = 0
        self.signals_speculative = 0

        for _, row in result.iterrows():
            row_dict = row.to_dict()
            try:
                res = self.compute_composite_score(row_dict, regime)
                tier = res["tier_label"]
                if tier == "Strong Buy":
                    self.signals_strong_buy += 1
                elif tier == "Buy":
                    self.signals_buy += 1
                elif tier == "Watch":
                    self.signals_watch += 1
                else:
                    self.signals_speculative += 1

                row_dict["composite_score"] = res["composite_score"]
                row_dict["quality_score"] = res["composite_score"]
                row_dict["tier_label"] = tier
                row_dict["tier"] = tier
                row_dict["score_breakdown"] = res["breakdown"]
                row_dict["momentum_score"] = res["breakdown"]["momentum"]
                row_dict["expectancy_score"] = res["breakdown"]["expectancy"]
                row_dict["winrate_score"] = res["breakdown"]["winrate"]
                row_dict["regime_score"] = res["breakdown"]["regime"]
                row_dict["context_score"] = res["breakdown"]["context"]
                scored_rows.append(row_dict)
            except Exception as e:
                logger.warning("Error scoring candidate %s: %s", row_dict.get("ticker", "unknown"), e)
                self.signals_speculative += 1

        if not scored_rows:
            return pd.DataFrame(columns=result.columns)

        out_df = pd.DataFrame(scored_rows)
        # Quality Filter: Only Strong Buy and Buy qualify as actionable recommendation ideas
        qualifying = out_df[out_df["tier_label"].isin(["Strong Buy", "Buy"])].copy()
        if qualifying.empty:
            logger.info("No qualifying stock ideas tonight.")
            return pd.DataFrame(columns=out_df.columns)

        qualifying = qualifying.sort_values("composite_score", ascending=False).reset_index(drop=True)
        if top_n is not None and top_n > 0 and len(qualifying) > top_n:
            return qualifying.head(top_n).reset_index(drop=True)
        return qualifying

    def _fetch_price_history(self, ticker: str) -> Optional[pd.DataFrame]:
        # Try new date-partitioned cache first (supports preloaded memory lookups)
        try:
            cache_manager = get_cache_manager()
            end_date = datetime.date.today()
            start_date = end_date - datetime.timedelta(days=120)
            ticker_data = cache_manager.get_ticker_history(ticker, start_date.isoformat(), end_date.isoformat())
            if ticker_data is not None and not ticker_data.empty:
                return ticker_data
        except Exception as e:
            logger.warning(f"Failed to fetch price history from date-partitioned cache for {ticker}: {e}")

        # Fallback to old per-ticker cache
        cache_path = os.path.join("data", "cache", f"{ticker.upper()}.parquet")
        if os.path.exists(cache_path):
            try:
                return pd.read_parquet(cache_path, engine="pyarrow")
            except Exception:
                pass
        
        # Fallback: Download via YahooProvider
        try:
            from src.providers.price.yahoo_provider import YahooProvider
            provider = YahooProvider()
            end_date = datetime.date.today().isoformat()
            start_date = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
            return provider.get_historical([ticker], start=start_date, end=end_date)
        except Exception as e:
            logger.warning("Failed to fetch price history for %s: %s", ticker, e)
            return None

    def rank(self, signals_df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
        """Backward compatibility wrapper mapping to composite_rank with default bull regime."""
        df = signals_df.copy()
        if "dma_50" not in df.columns:
            df["dma_50"] = df["price"]
        if "macd_histogram" not in df.columns:
            df["macd_histogram"] = 0.0
        return self.composite_rank(df, "bull", top_n)





if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    print("=" * 60)
    print("  SIGNAL COMPOSITE RANKER TEST (Strategy 1.3 Rev A)")
    print("=" * 60)

    # Test data mimicking actual conditions
    test_data = pd.DataFrame(
        {
            "ticker": ["ABNB", "AEE", "BALL", "TSLA", "NVDA", "INVALID", "XYZ"],
            "win_rate": [20.0, 18.5, 24.1, 45.0, 38.0, 10.0, 40.0],
            "expectancy_pct": [-3.41, -0.59, -0.60, 5.1, 4.2, -5.0, 3.5],
            "upside_pct": [32.5, 6.9, 33.2, 8.0, 15.0, 2.0, 10.0],
            "price": [140.0, 80.0, 60.0, 250.0, 120.0, 10.0, 100.0],
            "dma_50": [135.0, 82.0, 58.0, 240.0, 110.0, 15.0, 95.0],
            "current_rsi": [56.1, 53.9, 60.3, 52.0, 68.0, 30.0, 55.0],
            "volume_ratio": [1.10, 1.17, 2.33, 1.5, 2.0, 0.5, 1.2],
            "macd_histogram": [0.12, -0.05, 0.22, 0.45, 0.35, -0.20, 0.20],
            "total_trades": [15, 12, 10, 22, 30, 2, 15],
            "industry": ["Hotels, Resorts & Cruise Lines", "Multi-Utilities", "Metal, Glass & Plastic Containers", "Automobile Manufacturers", "Semiconductors", "Unknown", "Semiconductors"],
        }
    )

    print("\nInput data:")
    print(test_data[["ticker", "win_rate", "expectancy_pct", "total_trades"]].to_string(index=False))
    print()

    ranker = SignalRanker()
    
    # Test Bull regime
    result_bull = ranker.composite_rank(test_data, "bull", top_n=5)
    print("\nRanked output (BULL):")
    display_cols = ["ticker", "composite_score", "tier_label", "momentum_score", "expectancy_score", "winrate_score", "regime_score"]
    print(result_bull[display_cols].to_string(index=False))
    print()

    # Assertions
    tickers_ranked = result_bull["ticker"].tolist()
    assert len(tickers_ranked) == 3, f"Expected 3 ranked signals, got {len(tickers_ranked)}"
    
    tsla_row = result_bull[result_bull["ticker"] == "TSLA"].iloc[0]
    nvda_row = result_bull[result_bull["ticker"] == "NVDA"].iloc[0]
    assert tsla_row["tier_label"] in ("Strong Buy", "Buy"), f"TSLA expected Strong Buy or Buy, got {tsla_row['tier_label']}"
    assert nvda_row["tier_label"] in ("Strong Buy", "Buy"), f"NVDA expected Strong Buy or Buy, got {nvda_row['tier_label']}"
    
    # AEE and BALL should be filtered out since they are Watch/Speculative tier
    assert "AEE" not in tickers_ranked
    assert "BALL" not in tickers_ranked
    
    print("  All assertions passed!")
    print("=" * 60)
