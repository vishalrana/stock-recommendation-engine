"""
Signal Ranker
=============
Percentile-normalized, gated ranking engine for Strategy 1.2.

Replaces the broken raw-value weighted sum with:
  1. Hard gates (minimum expectancy, win rate, trade count)
  2. Percentile normalization (rank within qualified universe, 0-1)
  3. Confidence factor (sample-size adjustment)

Usage:
    from ranker import SignalRanker

    ranker = SignalRanker(min_expectancy=0, min_win_rate=25, min_trades=5)
    top_signals = ranker.rank(signals_df, top_n=5)
"""

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class SignalRanker:
    """
    Gated percentile-normalized ranking engine.

    Weights:
        40% norm_expectancy   (historical avg return per trade)
        30% norm_win_rate     (historical win probability)
        20% norm_upside       (current setup's upside potential)
        10% confidence        (sqrt(trades) / sqrt(max_trades) sample-size factor)
    """

    WEIGHT_EXPECTANCY = 0.40
    WEIGHT_WIN_RATE = 0.30
    WEIGHT_UPSIDE = 0.20
    WEIGHT_CONFIDENCE = 0.10

    def __init__(
        self,
        min_expectancy: float = 0,
        min_win_rate: float = 25,
        min_trades: int = 5,
    ):
        """
        Args:
            min_expectancy: Minimum historical expectancy_pct to pass gate (exclusive >).
            min_win_rate:   Minimum historical win_rate to pass gate (inclusive >=).
            min_trades:     Minimum total_trades to pass gate (inclusive >=).
        """
        self.min_expectancy = min_expectancy
        self.min_win_rate = min_win_rate
        self.min_trades = min_trades

    def apply_gates(self, signals_df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply hard gates to filter out signals with poor historical performance.

        Filters:
            - expectancy_pct > min_expectancy
            - win_rate >= min_win_rate
            - total_trades >= min_trades

        Returns:
            Copy of filtered DataFrame. Original is not modified.
        """
        if signals_df.empty:
            return signals_df.copy()

        mask = (
            (signals_df["expectancy_pct"] > self.min_expectancy)
            & (signals_df["win_rate"] >= self.min_win_rate)
            & (signals_df["total_trades"] >= self.min_trades)
        )

        filtered = signals_df[mask].copy()

        n_removed = len(signals_df) - len(filtered)
        if n_removed > 0:
            logger.info(
                "Gates removed %d of %d signals (kept %d). "
                "Gates: expectancy > %.1f, win_rate >= %.1f, trades >= %d",
                n_removed,
                len(signals_df),
                len(filtered),
                self.min_expectancy,
                self.min_win_rate,
                self.min_trades,
            )

        return filtered

    @staticmethod
    def percentile_rank(series: pd.Series) -> pd.Series:
        """
        Convert a numeric Series to percentile ranks in [0, 1].

        Uses pandas rank(pct=True) which assigns ranks as fractions
        of the total count. Ties are averaged.

        Args:
            series: Numeric Series to rank.

        Returns:
            Series of percentile ranks (0.0 to 1.0).
        """
        return series.rank(pct=True, method="average")

    def compute_score(self, qualified_df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute the normalized composite score for qualified signals.

        New columns added:
            norm_expectancy - percentile rank of expectancy_pct
            norm_win_rate   - percentile rank of win_rate
            norm_upside     - percentile rank of upside_pct
            confidence      - sqrt(total_trades) / sqrt(max_total_trades)
            score           - weighted composite (0.0 to 1.0)

        Args:
            qualified_df: DataFrame that has already passed apply_gates().

        Returns:
            DataFrame with new columns appended.
        """
        df = qualified_df.copy()

        if df.empty:
            for col in ["norm_expectancy", "norm_win_rate", "norm_upside", "confidence", "score"]:
                df[col] = pd.Series(dtype=float)
            return df

        # Percentile normalize each component
        df["norm_expectancy"] = self.percentile_rank(df["expectancy_pct"])
        df["norm_win_rate"] = self.percentile_rank(df["win_rate"])
        df["norm_upside"] = self.percentile_rank(df["upside_pct"])

        # Confidence: sample-size adjustment
        max_trades = df["total_trades"].max()
        if max_trades > 0:
            df["confidence"] = df["total_trades"].apply(
                lambda t: math.sqrt(t) / math.sqrt(max_trades)
            )
        else:
            df["confidence"] = 0.0

        # Composite score
        df["score"] = (
            self.WEIGHT_EXPECTANCY * df["norm_expectancy"]
            + self.WEIGHT_WIN_RATE * df["norm_win_rate"]
            + self.WEIGHT_UPSIDE * df["norm_upside"]
            + self.WEIGHT_CONFIDENCE * df["confidence"]
        )

        # Round for readability
        for col in ["norm_expectancy", "norm_win_rate", "norm_upside", "confidence", "score"]:
            df[col] = df[col].round(4)

        return df

    def rank(self, signals_df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
        """
        Full ranking pipeline: gate -> score -> sort -> top_n.

        Args:
            signals_df: DataFrame with columns: ticker, win_rate, expectancy_pct,
                        upside_pct, total_trades (plus any other signal columns).
            top_n: Maximum number of top signals to return.

        Returns:
            DataFrame of top_n ranked signals sorted by score descending.
            Empty DataFrame if no signals pass gates.
        """
        logger.info("Ranking %d raw signals (top_n=%d)...", len(signals_df), top_n)

        # Step 1: Apply hard gates
        qualified = self.apply_gates(signals_df)

        if qualified.empty:
            logger.warning(
                "No signals passed gates (expectancy > %.1f, win_rate >= %.1f, trades >= %d). "
                "Returning empty DataFrame.",
                self.min_expectancy,
                self.min_win_rate,
                self.min_trades,
            )
            return qualified

        logger.info("%d signals passed gates.", len(qualified))

        # Step 2: Compute normalized scores
        scored = self.compute_score(qualified)

        # Step 3: Sort and select top N
        ranked = scored.sort_values("score", ascending=False).head(top_n)

        logger.info(
            "Top %d signals selected. Score range: %.4f to %.4f",
            len(ranked),
            ranked["score"].min(),
            ranked["score"].max(),
        )

        return ranked.reset_index(drop=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    print("=" * 60)
    print("  SIGNAL RANKER TEST")
    print("=" * 60)

    # Test data from specification
    test_data = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "TSLA", "META", "NVDA"],
            "win_rate": [35, 28, 42, 22, 38],
            "expectancy_pct": [3.5, -1.2, 5.1, 0.8, 4.2],
            "upside_pct": [12, 18, 8, 25, 15],
            "total_trades": [15, 8, 22, 4, 30],
        }
    )

    print("\nInput data:")
    print(test_data.to_string(index=False))
    print()

    ranker = SignalRanker(min_expectancy=0, min_win_rate=25, min_trades=5)
    result = ranker.rank(test_data, top_n=5)

    print("\nRanked output:")
    display_cols = ["ticker", "score", "norm_expectancy", "norm_win_rate", "norm_upside", "confidence"]
    print(result[display_cols].to_string(index=False))
    print()

    # Assertions
    tickers_ranked = result["ticker"].tolist()

    assert "MSFT" not in tickers_ranked, "MSFT should be filtered (negative expectancy)"
    assert "META" not in tickers_ranked, "META should be filtered (total_trades=4 < 5)"
    assert len(tickers_ranked) == 3, f"Expected 3 ranked signals, got {len(tickers_ranked)}"
    assert tickers_ranked[0] == "TSLA", f"Expected TSLA #1, got {tickers_ranked[0]}"
    assert tickers_ranked[1] == "NVDA", f"Expected NVDA #2, got {tickers_ranked[1]}"
    assert tickers_ranked[2] == "AAPL", f"Expected AAPL #3, got {tickers_ranked[2]}"

    # Score must be in [0, 1]
    assert result["score"].min() >= 0, "Score must be >= 0"
    assert result["score"].max() <= 1.0, "Score must be <= 1.0"

    print("  All assertions passed!")
    print(f"  TSLA: {result.iloc[0]['score']:.4f} (rank #1)")
    print(f"  NVDA: {result.iloc[1]['score']:.4f} (rank #2)")
    print(f"  AAPL: {result.iloc[2]['score']:.4f} (rank #3)")
    print(f"  MSFT: FILTERED (expectancy={test_data[test_data['ticker']=='MSFT']['expectancy_pct'].iloc[0]})")
    print(f"  META: FILTERED (total_trades={test_data[test_data['ticker']=='META']['total_trades'].iloc[0]})")
    print("=" * 60)
