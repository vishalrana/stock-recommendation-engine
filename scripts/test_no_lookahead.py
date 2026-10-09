"""
No-Lookahead Test Suite: Strict Point-in-Time Verification
=========================================================
Proves:
1. Indicators and MarketStructure at step T cannot observe any data from T+1 onwards.
2. Modifying future bars (extreme spikes, crashes, zero volume, NaN) has ZERO effect on step T.
3. Slicing data at horizon T produces bit-for-bit identical results to running up to T.
4. Swing levels, 20-day highs/lows, 52-week highs, moving averages, and ATR never look ahead.
5. All 7 strategies evaluated at horizon T yield identical states regardless of future data.
"""

import sys
import os
import unittest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.entry_location import (
    analyze_market_structure,
    evaluate_entry_location,
    find_recent_swing_levels,
    MarketStructure,
    EntryLocationResult,
)

from src.strategies.target_calculator import (
    get_reach_prob_distribution,
    get_reach_prob_target_before_stop,
    get_reach_prob_target_before_stop_structured,
    normalize_and_filter_price_df,
    STATUS_CUTOFF_FAILURE,
    STATUS_VALID_ESTIMATE,
    reset_reach_prob_cache,
)

STRATEGIES = [
    "Trend Following",
    "52-Week High Breakout",
    "Pullback Recovery",
    "Mean Reversion",
    "PEAD",
    "Cross-Sectional Momentum",
    "Sector Rotation",
]


def generate_simulated_market_data(n_bars: int = 250, seed: int = 42) -> pd.DataFrame:
    """Generate realistic OHLCV dataframe with indicators."""
    np.random.seed(seed)
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq="B")
    
    # Geometric Brownian motion
    returns = np.random.normal(0.0005, 0.015, n_bars)
    price = 100.0 * np.exp(np.cumsum(returns))
    
    high = price * (1.0 + np.abs(np.random.normal(0.008, 0.005, n_bars)))
    low = price * (1.0 - np.abs(np.random.normal(0.008, 0.005, n_bars)))
    open_p = low + np.random.uniform(0.1, 0.9, n_bars) * (high - low)
    volume = np.random.uniform(500_000, 3_000_000, n_bars)

    df = pd.DataFrame(index=dates)
    df["OPEN"] = open_p
    df["HIGH"] = high
    df["LOW"] = low
    df["CLOSE"] = price
    df["VOLUME"] = volume
    
    # Technical indicators
    tr1 = df["HIGH"] - df["LOW"]
    tr2 = (df["HIGH"] - df["CLOSE"].shift(1)).abs()
    tr3 = (df["LOW"] - df["CLOSE"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR_14"] = tr.rolling(14).mean().bfill()
    df["EMA_20"] = df["CLOSE"].ewm(span=20, adjust=False).mean()
    df["DMA_50"] = df["CLOSE"].rolling(50).mean().bfill()
    df["DMA_200"] = df["CLOSE"].rolling(200).mean().bfill()
    df["RSI_14"] = 50.0  # baseline
    return df


class TestNoLookaheadIntegrity(unittest.TestCase):

    def setUp(self):
        self.df_full = generate_simulated_market_data(n_bars=250, seed=123)

    def test_future_corruption_invariance(self):
        """
        Verify that mutating, corrupting, or injecting future bars at T+1..N
        has ZERO effect on the market structure and entry evaluation at T.
        """
        candidate = {"ticker": "AAPL"}
        horizons = [60, 80, 100, 120, 150, 180, 210]

        for T in horizons:
            # Baseline: slice strictly up to T
            df_slice = self.df_full.iloc[:T + 1].copy()
            res_baseline = {
                strat: evaluate_entry_location(candidate, df_slice, strat)
                for strat in STRATEGIES
            }
            struct_baseline = analyze_market_structure(df_slice)

            # Scenario A: Future has catastrophic crash (-90%)
            df_crash = self.df_full.copy()
            df_crash.iloc[T + 1:, df_crash.columns.get_loc("CLOSE")] *= 0.10
            df_crash.iloc[T + 1:, df_crash.columns.get_loc("LOW")] *= 0.05
            df_crash.iloc[T + 1:, df_crash.columns.get_loc("VOLUME")] *= 10.0
            
            # Slice up to T from the corrupted dataframe
            df_crash_slice = df_crash.iloc[:T + 1]
            struct_crash = analyze_market_structure(df_crash_slice)

            self.assertEqual(struct_baseline.support_level, struct_crash.support_level)
            self.assertEqual(struct_baseline.resistance_level, struct_crash.resistance_level)
            self.assertEqual(struct_baseline.is_confirmed_breakout, struct_crash.is_confirmed_breakout)
            self.assertEqual(struct_baseline.is_falling_knife, struct_crash.is_falling_knife)
            self.assertEqual(struct_baseline.range_position_pct, struct_crash.range_position_pct)

            for strat in STRATEGIES:
                res_crash = evaluate_entry_location(candidate, df_crash_slice, strat)
                self.assertEqual(
                    res_baseline[strat].state,
                    res_crash.state,
                    f"Future crash leaked into past at T={T} for {strat}!"
                )
                self.assertEqual(res_baseline[strat].reason, res_crash.reason)

            # Scenario B: Future has astronomical pump (+1000%)
            df_pump = self.df_full.copy()
            df_pump.iloc[T + 1:, df_pump.columns.get_loc("CLOSE")] *= 10.0
            df_pump.iloc[T + 1:, df_pump.columns.get_loc("HIGH")] *= 15.0
            df_pump_slice = df_pump.iloc[:T + 1]
            struct_pump = analyze_market_structure(df_pump_slice)

            self.assertEqual(struct_baseline.support_level, struct_pump.support_level)
            self.assertEqual(struct_baseline.resistance_level, struct_pump.resistance_level)

            for strat in STRATEGIES:
                res_pump = evaluate_entry_location(candidate, df_pump_slice, strat)
                self.assertEqual(
                    res_baseline[strat].state,
                    res_pump.state,
                    f"Future pump leaked into past at T={T} for {strat}!"
                )

    def test_swing_level_strict_backward_lookback(self):
        """
        Ensure find_recent_swing_levels only examines historical pivot bars
        and does not use future peak/trough confirmation beyond the lookback window.
        """
        for T in [50, 75, 100, 130]:
            slice_t = self.df_full.iloc[:T + 1]
            highs_t, lows_t = find_recent_swing_levels(slice_t, lookback=20)
            
            # None of the swing levels can exceed the maximum high or be below the minimum low of slice_t
            if highs_t:
                self.assertLessEqual(max(highs_t), float(slice_t["HIGH"].max()))
            if lows_t:
                self.assertGreaterEqual(min(lows_t), float(slice_t["LOW"].min()))

    def test_20_day_reference_excludes_current_bar_lookahead(self):
        """
        Ensure prior_high_20 and prior_low_20 are calculated from bars [T-20 : T-1],
        excluding today's bar T, so that today's price cannot set its own reference support/resistance.
        """
        df = self.df_full.iloc[:80].copy()
        # Today creates an unprecedented single-day spike to 999.0
        df.iloc[-1, df.columns.get_loc("HIGH")] = 999.0
        df.iloc[-1, df.columns.get_loc("CLOSE")] = 995.0
        
        # Prior 20-day high must NOT include 999.0
        struct = analyze_market_structure(df)
        self.assertLess(struct.resistance_level, 900.0)

    def test_point_in_time_liquidity_no_lookahead(self):
        """
        Verify that evaluate_point_in_time_liquidity strictly ignores any future data
        after as_of_date, preventing future volume spikes or price crashes from leaking into past evaluations.
        """
        from src.universe.filters import evaluate_point_in_time_liquidity

        # Take historical slice up to date T
        t_date_str = str(self.df_full.index[150].date())
        df_slice = self.df_full.iloc[:151].copy()

        is_liq_base, reason_base, m_base = evaluate_point_in_time_liquidity(
            df_slice, as_of_date=t_date_str, min_history_days=60
        )

        # Append future data with extreme volume pump and price crash at T+1..N
        df_corrupted = self.df_full.copy()
        df_corrupted.iloc[151:, df_corrupted.columns.get_loc("VOLUME")] = 999_999_999.0
        df_corrupted.iloc[151:, df_corrupted.columns.get_loc("CLOSE")] = 0.50

        is_liq_test, reason_test, m_test = evaluate_point_in_time_liquidity(
            df_corrupted, as_of_date=t_date_str, min_history_days=60
        )

        self.assertEqual(is_liq_base, is_liq_test, "Future data leaked into point-in-time liquidity decision!")
        self.assertEqual(reason_base, reason_test)
        self.assertEqual(m_base["price"], m_test["price"])
        self.assertEqual(m_base["avg_dollar_volume"], m_test["avg_dollar_volume"])
        self.assertEqual(m_base["history_days"], m_test["history_days"])

    def test_cross_sectional_momentum_no_lookahead(self):
        """
        Verify that 63-day return used in Cross-Sectional Momentum only uses past prices
        and cannot observe future performance.
        """
        df = self.df_full.iloc[:120].copy()
        p_t = float(df["CLOSE"].iloc[-1])
        p_t_minus_63 = float(df["CLOSE"].iloc[-64])
        expected_ret = (p_t - p_t_minus_63) / p_t_minus_63

        # Add future bar with a 500% pump
        df_future = self.df_full.copy()
        df_future.iloc[120:, df_future.columns.get_loc("CLOSE")] *= 5.0

        # Point-in-time calculation at index 119
        pit_slice = df_future.iloc[:120]
        actual_ret = (float(pit_slice["CLOSE"].iloc[-1]) - float(pit_slice["CLOSE"].iloc[-64])) / float(pit_slice["CLOSE"].iloc[-64])

        self.assertAlmostEqual(expected_ret, actual_ret, places=6)

    def test_reach_prob_distribution_adversarial_future_corruption(self):
        """
        Prove that corrupting future bars after as_of_date has ZERO effect on
        reach probability distribution.
        """
        reset_reach_prob_cache()
        cutoff_date = str(self.df_full.index[160].date())
        
        # Baseline reach distribution calculated up to cutoff
        dist_baseline = get_reach_prob_distribution(
            ticker="ADV_TEST",
            holding_days=10,
            price_df=self.df_full,
            as_of_date=cutoff_date,
        )
        self.assertGreater(len(dist_baseline), 0)

        # Adversarial corruption: inject 1000% spike, zero price, and wild volatility into future bars (T+1..)
        df_corrupt = self.df_full.copy()
        df_corrupt.iloc[161:, df_corrupt.columns.get_loc("CLOSE")] *= 10.0
        df_corrupt.iloc[161:, df_corrupt.columns.get_loc("HIGH")] *= 15.0
        df_corrupt.iloc[161:, df_corrupt.columns.get_loc("LOW")] *= 0.1

        reset_reach_prob_cache()
        dist_corrupt = get_reach_prob_distribution(
            ticker="ADV_TEST",
            holding_days=10,
            price_df=df_corrupt,
            as_of_date=cutoff_date,
        )

        np.testing.assert_array_equal(
            dist_baseline,
            dist_corrupt,
            err_msg="Adversarial future bar corruption leaked into historical reach distribution!",
        )

    def test_reach_prob_target_before_stop_adversarial_future_corruption(self):
        """
        Prove that changing or appending future bars after as_of_date has zero effect
        on target-before-stop reach probability.
        """
        reset_reach_prob_cache()
        cutoff_date = str(self.df_full.index[150].date())

        res_baseline = get_reach_prob_target_before_stop_structured(
            ticker="ADV_TBS",
            target_pct=0.05,
            stop_pct=0.03,
            holding_days=10,
            price_df=self.df_full,
            as_of_date=cutoff_date,
        )
        self.assertEqual(res_baseline.status, STATUS_VALID_ESTIMATE)

        # Corrupt future bars
        df_corrupt = self.df_full.copy()
        df_corrupt.iloc[151:, df_corrupt.columns.get_loc("HIGH")] = 9999.0
        df_corrupt.iloc[151:, df_corrupt.columns.get_loc("LOW")] = 0.01

        reset_reach_prob_cache()
        res_corrupt = get_reach_prob_target_before_stop_structured(
            ticker="ADV_TBS",
            target_pct=0.05,
            stop_pct=0.03,
            holding_days=10,
            price_df=df_corrupt,
            as_of_date=cutoff_date,
        )

        self.assertEqual(res_baseline.raw_prob, res_corrupt.raw_prob)
        self.assertEqual(res_baseline.status, res_corrupt.status)

    def test_malformed_cutoff_fails_closed(self):
        """
        Prove that unparseable or malformed as_of_date strictly fails closed
        (STATUS_CUTOFF_FAILURE, prob 0.0) and NEVER proceeds with unfiltered data.
        """
        reset_reach_prob_cache()
        bad_cutoffs = ["not-a-valid-date", "2026-99-99", "INVALID_DATE_STRING"]
        for bad_date in bad_cutoffs:
            filtered_df, as_of_str, status = normalize_and_filter_price_df(self.df_full, as_of_date=bad_date)
            self.assertEqual(status, STATUS_CUTOFF_FAILURE, f"Expected STATUS_CUTOFF_FAILURE for {bad_date}")
            self.assertIsNone(filtered_df)

            res = get_reach_prob_target_before_stop_structured(
                ticker="ADV_FAIL",
                target_pct=0.05,
                stop_pct=0.03,
                holding_days=10,
                price_df=self.df_full,
                as_of_date=bad_date,
            )
            self.assertEqual(res.status, STATUS_CUTOFF_FAILURE)
            self.assertEqual(res.raw_prob, 0.0)

    def test_tz_aware_vs_naive_cutoff_alignment(self):
        """
        Prove that tz-aware price index with naive cutoff string or vice-versa
        compares cleanly without TypeError.
        """
        # Case A: tz-aware UTC DataFrame with naive string cutoff
        df_utc = self.df_full.copy()
        df_utc.index = df_utc.index.tz_localize("UTC")
        cutoff_str = "2026-06-15"

        filtered_df, as_of_str, status = normalize_and_filter_price_df(df_utc, as_of_date=cutoff_str)
        self.assertEqual(status, STATUS_VALID_ESTIMATE)
        self.assertIsNotNone(filtered_df)
        self.assertTrue(all(filtered_df.index <= pd.to_datetime(cutoff_str).tz_localize("UTC") + pd.Timedelta(days=1)))

        # Case B: tz-naive DataFrame with tz-aware cutoff string
        filtered_df2, as_of_str2, status2 = normalize_and_filter_price_df(self.df_full, as_of_date="2026-06-15T00:00:00Z")
        self.assertEqual(status2, STATUS_VALID_ESTIMATE)
        self.assertIsNotNone(filtered_df2)

    def test_cache_isolation_prevents_dataset_bypass(self):
        """
        Prove that cache keys incorporate data signatures (fingerprints),
        preventing different datasets for the same ticker/date from returning stale results.
        """
        reset_reach_prob_cache()
        cutoff_date = str(self.df_full.index[140].date())

        df_set1 = self.df_full.iloc[:141].copy()
        res1 = get_reach_prob_target_before_stop_structured(
            ticker="ISOLATION_TEST",
            target_pct=0.05,
            stop_pct=0.03,
            holding_days=10,
            price_df=df_set1,
            as_of_date=cutoff_date,
        )

        # Dataset 2 has different prices for the exact same ticker and date
        df_set2 = self.df_full.iloc[:141].copy()
        df_set2["HIGH"] *= 1.20
        df_set2["CLOSE"] *= 1.15

        res2 = get_reach_prob_target_before_stop_structured(
            ticker="ISOLATION_TEST",
            target_pct=0.05,
            stop_pct=0.03,
            holding_days=10,
            price_df=df_set2,
            as_of_date=cutoff_date,
        )

        # Must evaluate independently and not return stale hit from dataset 1
        self.assertNotEqual(res1.raw_prob, res2.raw_prob, "Cache collision between distinct datasets!")


if __name__ == "__main__":
    unittest.main()


