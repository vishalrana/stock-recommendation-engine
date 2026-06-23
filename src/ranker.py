"""
Signal Ranker — Strategy 1.2 Rev B
===================================
Composite-normalized, tiered ranking engine for Strategy 1.2.

Replaces hard gates with a composite scoring system:
  - Technical Momentum (40%): RSI, Proximity to 50 DMA, Volume Ratio
  - Risk-Adjusted Expectancy (30%): Z-score in pool, with negative expectancy penalty
  - Historical Win Rate (20%): Percentile rank (min 3 trades required)
  - Regime Adjustment (10%): Bull/Bear/Sideways specific bonus
"""

import logging
import math
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class SignalRanker:
    """
    Composite-normalized ranking engine with tiered fallback.
    """

    WEIGHT_MOMENTUM = 0.40
    WEIGHT_EXPECTANCY = 0.30
    WEIGHT_WIN_RATE = 0.20
    WEIGHT_REGIME = 0.10

    def __init__(self, min_expectancy: float = 0, min_win_rate: float = 25, min_trades: int = 5):
        # Kept for backward compatibility
        self.min_expectancy = min_expectancy
        self.min_win_rate = min_win_rate
        self.min_trades = min_trades

    def normalize_percentile(self, series: pd.Series) -> pd.Series:
        """Convert a numeric Series to percentile ranks in [0, 100]."""
        if len(series) <= 1:
            return pd.Series([100.0] * len(series), index=series.index)
        return series.rank(pct=True, method="average") * 100.0

    def regime_adjustment(self, score: float, regime: str, stock_metrics: dict) -> float:
        """
        Calculate regime adjustment score (0-100).
        - Bull: 100.0 if RSI 50-70 AND price > 50DMA else 0.0
        - Bear: 100.0 if industry IN defensive list OR beta < 1.0 else 0.0
        - Sideways: 100.0 if abs(RSI - 50) < 8 else 0.0
        """
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
        Expects row to have pre-calculated component scores.
        """
        momentum_score = row.get("momentum_score", 50.0)
        expectancy_score = row.get("expectancy_score", 50.0)
        winrate_score = row.get("winrate_score", 50.0)
        
        regime_score = self.regime_adjustment(momentum_score, regime, row)
        
        total = (
            self.WEIGHT_MOMENTUM * momentum_score
            + self.WEIGHT_EXPECTANCY * expectancy_score
            + self.WEIGHT_WIN_RATE * winrate_score
            + self.WEIGHT_REGIME * regime_score
        )
        
        return {
            "total": round(total, 4),
            "breakdown": {
                "momentum": round(momentum_score, 4),
                "expectancy": round(expectancy_score, 4),
                "winrate": round(winrate_score, 4),
                "regime": round(regime_score, 4),
            }
        }

    def composite_rank(self, df: pd.DataFrame, regime: str, top_n: int = 5) -> pd.DataFrame:
        """
        Full composite ranking pipeline with tiered fallback.
        """
        if df.empty:
            return df.copy()

        # Hard minimum trades gate: total_trades >= 3
        df_filtered = df[df["total_trades"] >= 3].copy()
        if df_filtered.empty:
            logger.warning("No signals passed the minimum trades gate (total_trades >= 3).")
            return df_filtered

        # 1. Compute Technical Momentum
        # RSI score (peaks at 50, decreases as it moves away)
        rsi_vals = df_filtered["current_rsi"]
        rsi_score = 100.0 - (rsi_vals - 50.0).abs() * 4.0
        rsi_score = rsi_score.clip(lower=0.0, upper=100.0)

        # 50 DMA Proximity score
        price_vals = df_filtered["price"]
        dma_50_vals = df_filtered["dma_50"]
        proximity = (price_vals / dma_50_vals - 1.0).abs()
        proximity_score = 100.0 - proximity * 500.0
        proximity_score = proximity_score.clip(lower=0.0, upper=100.0)

        # Volume score
        vol_ratio_vals = df_filtered["volume_ratio"]
        volume_score = vol_ratio_vals * 50.0
        volume_score = volume_score.clip(lower=0.0, upper=100.0)

        raw_momentum = (rsi_score + proximity_score + volume_score) / 3.0
        df_filtered["momentum_score"] = self.normalize_percentile(raw_momentum)

        # 2. Risk-Adjusted Expectancy
        mean_exp = df_filtered["expectancy_pct"].mean()
        std_exp = df_filtered["expectancy_pct"].std()
        if pd.isna(std_exp) or std_exp < 0.0001:
            z_scores = pd.Series(0.0, index=df_filtered.index)
        else:
            z_scores = (df_filtered["expectancy_pct"] - mean_exp) / std_exp

        # Map to 0-100 using sigmoid
        exp_score = 100.0 / (1.0 + np.exp(-z_scores))

        # Soften negative expectancy penalty: raw = max(15, raw - 20)
        neg_mask = df_filtered["expectancy_pct"] < 0
        exp_score[neg_mask] = (exp_score[neg_mask] - 20.0).clip(lower=15.0)
        df_filtered["expectancy_score"] = exp_score

        # 3. Historical Win Rate
        df_filtered["winrate_score"] = self.normalize_percentile(df_filtered["win_rate"])

        # 4. Regime Adjustment & 5. Composite Score
        regime_scores = []
        composite_scores = []
        score_breakdowns = []
        for _, row in df_filtered.iterrows():
            # Pass pre-calculated scores in row dict
            reg_score = self.regime_adjustment(row["momentum_score"], regime, row)
            regime_scores.append(reg_score)
            
            row_dict = row.to_dict()
            row_dict["regime_score"] = reg_score
            
            res = self.compute_composite_score(row_dict, regime)
            composite_scores.append(res["total"])
            score_breakdowns.append(res["breakdown"])

        df_filtered["regime_score"] = regime_scores
        df_filtered["composite_score"] = composite_scores
        df_filtered["score_breakdown"] = score_breakdowns

        # 6. Assign Tier Labels
        # Tier 1 "Strong Buy": score >= 70, all raw metrics positive (exp > 0, win > 0)
        # Tier 2 "Buy": score >= 55, expectancy >= -2.0, win_rate >= 20
        # Tier 3 "Watch": score >= 40
        # Tier 4 "Speculative": score < 40
        tiers = []
        for _, row in df_filtered.iterrows():
            score = row["composite_score"]
            exp = row["expectancy_pct"]
            wr = row["win_rate"]

            is_t1 = (score >= 70) and (exp > 0) and (wr > 0)
            is_t2 = (score >= 55) and (exp >= -2.0) and (wr >= 20)
            is_t3 = (score >= 40)

            if is_t1:
                tiers.append(1)
            elif is_t2:
                tiers.append(2)
            elif is_t3:
                tiers.append(3)
            else:
                tiers.append(4)

        df_filtered["temp_tier"] = tiers

        # Map temp_tier to tier_label
        tier_map = {1: "Strong Buy", 2: "Buy", 3: "Watch", 4: "Speculative"}
        df_filtered["tier_label"] = df_filtered["temp_tier"].map(tier_map)

        # Split by tier
        t1_eligible = df_filtered[df_filtered["temp_tier"] == 1]
        t2_eligible = df_filtered[df_filtered["temp_tier"] == 2]
        t3_eligible = df_filtered[df_filtered["temp_tier"] == 3]
        t4_eligible = df_filtered[df_filtered["temp_tier"] == 4]

        # Auto-relax selection
        if len(t1_eligible) >= 3:
            t1_sorted = t1_eligible.sort_values("composite_score", ascending=False)
            t2_sorted = t2_eligible.sort_values("composite_score", ascending=False)
            t3_sorted = t3_eligible.sort_values("composite_score", ascending=False)
            t4_sorted = t4_eligible.sort_values("composite_score", ascending=False)
            selected = pd.concat([t1_sorted, t2_sorted, t3_sorted, t4_sorted])
        elif len(t1_eligible) + len(t2_eligible) >= 3:
            t12_sorted = pd.concat([t1_eligible, t2_eligible]).sort_values("composite_score", ascending=False)
            t3_sorted = t3_eligible.sort_values("composite_score", ascending=False)
            t4_sorted = t4_eligible.sort_values("composite_score", ascending=False)
            selected = pd.concat([t12_sorted, t3_sorted, t4_sorted])
        else:
            t123_sorted = pd.concat([t1_eligible, t2_eligible, t3_eligible]).sort_values("composite_score", ascending=False)
            t4_sorted = t4_eligible.sort_values("composite_score", ascending=False)
            selected = pd.concat([t123_sorted, t4_sorted])

        result = selected.head(top_n).copy()
        result = result.drop(columns=["temp_tier"])
        return result.reset_index(drop=True)

    def rank(self, signals_df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
        """Backward compatibility wrapper mapping to composite_rank with default bull regime."""
        # Add default dma_50 to input if missing
        df = signals_df.copy()
        if "dma_50" not in df.columns:
            df["dma_50"] = df["price"]
        return self.composite_rank(df, "bull", top_n)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    print("=" * 60)
    print("  SIGNAL COMPOSITE RANKER TEST (Strategy 1.2 Rev B)")
    print("=" * 60)

    # Test data mimicking actual conditions
    test_data = pd.DataFrame(
        {
            "ticker": ["ABNB", "AEE", "BALL", "TSLA", "NVDA", "INVALID"],
            "win_rate": [20.0, 18.5, 24.1, 45.0, 38.0, 10.0],
            "expectancy_pct": [-3.41, -0.59, -0.60, 5.1, 4.2, -5.0],
            "upside_pct": [32.5, 6.9, 33.2, 8.0, 15.0, 2.0],
            "price": [140.0, 80.0, 60.0, 250.0, 120.0, 10.0],
            "dma_50": [135.0, 82.0, 58.0, 240.0, 110.0, 15.0],
            "current_rsi": [56.1, 53.9, 60.3, 52.0, 68.0, 30.0],
            "volume_ratio": [1.10, 1.17, 2.33, 1.5, 2.0, 0.5],
            "total_trades": [15, 12, 10, 22, 30, 2],  # INVALID has 2 trades (< 3)
            "industry": ["Hotels, Resorts & Cruise Lines", "Multi-Utilities", "Metal, Glass & Plastic Containers", "Automobile Manufacturers", "Semiconductors", "Unknown"],
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
    assert "INVALID" not in tickers_ranked, "INVALID should be filtered due to total_trades = 2 < 3"
    assert len(tickers_ranked) == 5, f"Expected 5 ranked signals, got {len(tickers_ranked)}"
    
    # TSLA should be Tier 1 (Strong Buy) because scores are high and expectancy/winrate positive
    tsla_row = result_bull[result_bull["ticker"] == "TSLA"].iloc[0]
    nvda_row = result_bull[result_bull["ticker"] == "NVDA"].iloc[0]
    assert tsla_row["tier_label"] == "Strong Buy", f"TSLA expected Strong Buy, got {tsla_row['tier_label']}"
    assert nvda_row["tier_label"] == "Buy", f"NVDA expected Buy, got {nvda_row['tier_label']}"
    
    # BALL should be Tier 2 (Buy) because exp is -0.6 >= -2.0, winrate is 24.1 >= 20
    ball_row = result_bull[result_bull["ticker"] == "BALL"].iloc[0]
    assert ball_row["tier_label"] == "Buy", f"BALL expected Buy, got {ball_row['tier_label']}"
    
    print("  All assertions passed!")
    print("=" * 60)
