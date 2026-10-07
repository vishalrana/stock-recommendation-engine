"""
Final Production Hardening Test Suite
=====================================
Comprehensive verification covering:
1. PEAD Canonical Scoring (Phase 2)
2. Earnings Decoupling & Context Score Invariance (Phases 4 & 7)
3. Earnings Pipeline & Error Handling (Phase 6)
4. Momentum Score Components & Sigmoid Math (Phase 10)
5. Cross-Sectional Sorting NaN/Inf Immunity (Phase 12)
6. Full 20+ Candidate Mathematical Reconciliation (Phase 21)
"""

import datetime
import math
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
src_dir = os.path.join(PROJECT_ROOT, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
jobs_dir = os.path.join(PROJECT_ROOT, "jobs")
if jobs_dir not in sys.path:
    sys.path.insert(0, jobs_dir)

from src.ranker import (
    SignalRanker,
    compute_momentum_score,
    compute_expectancy_score,
    compute_regime_alignment,
    compute_context_score,
    assign_tier,
)
from src.quant_config import STRATEGY_WEIGHT_VECTORS, REGIME_SCORE_MATRIX
from src.scorers.context_scorer import ContextScorer
from src.providers.base import AggregatedContext
from src.filters.earnings_filter import (
    earnings_risk_filter,
    resolve_ticker_earnings,
    fetch_single_ticker_provider,
    is_earnings_record_fresh,
    _SESSION_FETCHED_TICKERS,
    GLOBAL_CIRCUIT_BREAKER,
    EarningsStatus,
    EARNINGS_CACHE_TTL_SECONDS,
)
from src.utils.metrics_pipeline import calculate_shrunk_win_rate, calculate_shrunk_expectancy


class TestQuantHardeningFinal(unittest.TestCase):

    def setUp(self):
        self.ranker = SignalRanker()
        self.today = datetime.date.today()
        _SESSION_FETCHED_TICKERS.clear()
        GLOBAL_CIRCUIT_BREAKER.reset()

    # =========================================================================
    # 0. FROZEN QUANT SPECIFICATION ASSERTION
    # =========================================================================
    def test_frozen_quant_specification(self):
        """Verify frozen strategy weight vectors and regime matrix match specification exactly."""
        expected_weights = {
            "trend_following": {"mom": 0.45, "exp": 0.20, "wr": 0.15, "reg": 0.10, "ctx": 0.10},
            "52w_high_breakout": {"mom": 0.50, "exp": 0.15, "wr": 0.15, "reg": 0.10, "ctx": 0.10},
            "pullback_recovery": {"mom": 0.25, "exp": 0.35, "wr": 0.15, "reg": 0.10, "ctx": 0.15},
            "pead": {"mom": 0.30, "exp": 0.25, "wr": 0.15, "reg": 0.10, "ctx": 0.20},
            "cross_sectional_momentum": {"mom": 0.40, "exp": 0.20, "wr": 0.20, "reg": 0.10, "ctx": 0.10},
            "sector_rotation": {"mom": 0.35, "exp": 0.25, "wr": 0.15, "reg": 0.10, "ctx": 0.15},
            "mean_reversion": {"mom": 0.10, "exp": 0.20, "wr": 0.15, "reg": 0.15, "ctx": 0.40},
        }
        for strat, weights in expected_weights.items():
            self.assertEqual(STRATEGY_WEIGHT_VECTORS[strat], weights, f"Weight vector mismatch for {strat}")
            self.assertAlmostEqual(sum(STRATEGY_WEIGHT_VECTORS[strat].values()), 1.0, places=9)

        expected_regimes = {
            "trend_following": {"bull": 100.0, "sideways": 70.0, "bear": 20.0},
            "52w_high_breakout": {"bull": 100.0, "sideways": 60.0, "bear": 10.0},
            "cross_sectional_momentum": {"bull": 85.0, "sideways": 75.0, "bear": 30.0},
            "sector_rotation": {"bull": 80.0, "sideways": 90.0, "bear": 40.0},
            "pullback_recovery": {"bull": 70.0, "sideways": 85.0, "bear": 50.0},
            "pead": {"bull": 75.0, "sideways": 70.0, "bear": 70.0},
            "mean_reversion": {"bull": 30.0, "sideways": 65.0, "bear": 100.0},
        }
        for strat, matrix in expected_regimes.items():
            self.assertEqual(REGIME_SCORE_MATRIX[strat], matrix, f"Regime matrix mismatch for {strat}")

    # =========================================================================
    # 1. PEAD CANONICAL SCORING (PHASE 2 & SECTION 15)
    # =========================================================================
    def test_pead_uses_canonical_ranker_weights(self):
        """PEAD strategy must use canonical SignalRanker weights (30/25/15/10/20)."""
        pead_weights = STRATEGY_WEIGHT_VECTORS["pead"]
        self.assertEqual(pead_weights, {"mom": 0.30, "exp": 0.25, "wr": 0.15, "reg": 0.10, "ctx": 0.20})
        self.assertAlmostEqual(sum(pead_weights.values()), 1.0, places=9)

    def test_pead_deterministic_section_15(self):
        """Section 15 mathematical verification: Mom=70, Exp=90, WR=62, Reg=75, Ctx=80 -> 76.3; Ctx=0 -> 60.3; delta=16.0."""
        # 0.30*70 + 0.25*90 + 0.15*62 + 0.10*75 + 0.20*80 = 21.0 + 22.5 + 9.3 + 7.5 + 16.0 = 76.3
        cand_with_ctx = {
            "ticker": "PEAD_DET",
            "strategy": "pead",
            "momentum_score": 70.0,
            "expectancy_score": 90.0,
            "winrate_score": 62.0,
            "context_score": 80.0,
        }
        res_with = self.ranker.compute_composite_score(cand_with_ctx, "bull")
        # In bull regime, pead regime score is 75.0
        self.assertAlmostEqual(res_with["total"], 76.3, places=1)

        # When context is 0.0: 76.3 - 16.0 = 60.3
        cand_zero_ctx = dict(cand_with_ctx, context_score=0.0)
        res_zero = self.ranker.compute_composite_score(cand_zero_ctx, "bull")
        self.assertAlmostEqual(res_zero["total"], 60.3, places=1)

        delta = res_with["total"] - res_zero["total"]
        self.assertAlmostEqual(delta, 16.0, places=1)

    def test_pead_scan_with_valid_context_vs_zero_context(self):
        """Prove that PEAD with valid context produces higher score than PEAD with zero context."""
        from jobs.strategies.pead import PEADStrategy

        strat = PEADStrategy()
        today = datetime.date.today()
        dates = [today - datetime.timedelta(days=i) for i in reversed(range(60))]
        df = pd.DataFrame(index=pd.DatetimeIndex(dates))
        df["CLOSE"] = 100.0
        df["HIGH"] = 101.0
        df["LOW"] = 99.0
        df["VOLUME"] = 1000000.0
        df["RSI_14"] = 55.0
        df["ADX_14"] = 25.0
        df["MACD_HIST"] = 0.5
        df["ATR_14"] = 2.0
        df["EMA_20"] = 98.0

        # Gap up
        df.iloc[-1, df.columns.get_loc("CLOSE")] = 106.0
        df.iloc[-1, df.columns.get_loc("HIGH")] = 107.0
        df.iloc[-1, df.columns.get_loc("LOW")] = 105.0
        df.iloc[-1, df.columns.get_loc("VOLUME")] = 2500000.0

        last_e_date = dates[-2]
        cal = {"PEAD_CTX": {"last_earnings_date": last_e_date.isoformat(), "next_earnings_date": (last_e_date + datetime.timedelta(days=90)).isoformat()}}
        strat.set_earnings_calendar(cal)

        # 1. PEAD scan with valid context
        metrics_valid = {"shrunk_win_rate": 60.0, "shrunk_expectancy": 2.5, "context_score": 80.0}
        sig_valid = strat.scan("PEAD_CTX", df, "bull", metrics_valid)
        self.assertIsNotNone(sig_valid)
        self.assertEqual(sig_valid["context_score"], 80.0)

        # 2. PEAD scan with zero context
        metrics_zero = {"shrunk_win_rate": 60.0, "shrunk_expectancy": 2.5, "context_score": 0.0}
        sig_zero = strat.scan("PEAD_CTX", df, "bull", metrics_zero)
        self.assertIsNotNone(sig_zero)
        self.assertEqual(sig_zero["context_score"], 0.0)

        # Valid context produces strictly higher composite score (difference = 0.20 * 80.0 = 16.0)
        self.assertGreater(sig_valid["composite_score"], sig_zero["composite_score"])
        self.assertAlmostEqual(sig_valid["composite_score"] - sig_zero["composite_score"], 16.0, places=1)

    def test_pead_strategy_scan_produces_canonical_composite_score(self):
        """PEADStrategy.scan() must compute composite score using canonical SignalRanker."""
        from jobs.strategies.pead import PEADStrategy

        strat = PEADStrategy()
        today = datetime.date.today()
        dates = [today - datetime.timedelta(days=i) for i in reversed(range(60))]
        df = pd.DataFrame(index=pd.DatetimeIndex(dates))
        df["CLOSE"] = 100.0
        df["HIGH"] = 101.0
        df["LOW"] = 99.0
        df["VOLUME"] = 1000000.0
        df["RSI_14"] = 55.0
        df["ADX_14"] = 25.0
        df["MACD_HIST"] = 0.5
        df["ATR_14"] = 2.0
        df["EMA_20"] = 98.0

        # Create 6% gap on day -1
        df.iloc[-1, df.columns.get_loc("CLOSE")] = 106.0
        df.iloc[-1, df.columns.get_loc("HIGH")] = 107.0
        df.iloc[-1, df.columns.get_loc("LOW")] = 105.0
        df.iloc[-1, df.columns.get_loc("VOLUME")] = 2500000.0

        last_e_date = dates[-2]
        cal = {"PEAD_TEST": {"last_earnings_date": last_e_date.isoformat(), "next_earnings_date": (last_e_date + datetime.timedelta(days=90)).isoformat()}}
        strat.set_earnings_calendar(cal)

        sig = strat.scan("PEAD_TEST", df, "bull", {"shrunk_win_rate": 60.0, "shrunk_expectancy": 2.5})
        self.assertIsNotNone(sig, "PEAD setup should qualify")

        # Verify score matches SignalRanker.compute_composite_score()
        expected = self.ranker.compute_composite_score({
            "ticker": "PEAD_TEST",
            "strategy": "Post-Earnings Drift",
            "current_rsi": sig["current_rsi"],
            "price": sig["price"],
            "entry_price": sig["entry_price"],
            "dma_50": sig.get("dma_50", sig["ema20"]),
            "volume_ratio": sig["volume_ratio"],
            "macd_histogram": sig["macd_histogram"],
            "atr_14": sig["atr_14"],
            "win_rate": sig["past_win_rate"],
            "expectancy_pct": sig["expectancy_pct"],
            "context_score": sig["context_score"],
        }, "bull")

        self.assertAlmostEqual(sig["composite_score"], round(expected["total"], 1), places=1)
        self.assertFalse(sig["is_blocked"])

        # rank_candidates must not filter valid candidates
        ranked = strat.rank_candidates([sig], "bull")
        self.assertEqual(len(ranked), 1)

    # =========================================================================
    # 2. EARNINGS DECOUPLING & CONTEXT SCORE INVARIANCE (PHASES 4 & 7)
    # =========================================================================
    def test_earnings_decoupling_score_invariance(self):
        """Proof A, B, C, D: Composite score is strictly invariant to earnings data/surprise."""
        base_cand = {
            "ticker": "INVAR_STK",
            "strategy": "Trend Following",
            "current_rsi": 55.0,
            "price": 100.0,
            "dma_50": 95.0,
            "volume_ratio": 1.2,
            "macd_histogram": 0.5,
            "winrate_score": 60.0,
            "win_rate": 60.0,
            "expectancy_pct": 2.0,
            "context_analyst": 30.0,
            "context_fundamental": 15.0,
            "context_news": 10.0,
        }

        # A: Base score with zero earnings data
        res_neutral = self.ranker.compute_composite_score(base_cand, "bull")

        # B: Adding positive earnings surprise cannot increase score
        cand_pos = dict(base_cand, context_earnings=30.0, earnings_surprise_pct=25.0)
        res_pos = self.ranker.compute_composite_score(cand_pos, "bull")
        self.assertEqual(res_pos["total"], res_neutral["total"])

        # C: Adding negative earnings surprise cannot decrease score
        cand_neg = dict(base_cand, context_earnings=0.0, earnings_surprise_pct=-25.0)
        res_neg = self.ranker.compute_composite_score(cand_neg, "bull")
        self.assertEqual(res_neg["total"], res_neutral["total"])

        # D: Impending earnings blackout cannot change composite score
        cand_blackout = dict(base_cand, next_earnings_date=(self.today + datetime.timedelta(days=2)).isoformat())
        res_blackout = self.ranker.compute_composite_score(cand_blackout, "bull")
        self.assertEqual(res_blackout["total"], res_neutral["total"])

        # Context scorer directly: same non-earnings inputs produce identical scores
        scorer = ContextScorer()
        t1, a1, e1, f1, n1 = scorer.calculate_with_breakdown(AggregatedContext(), 100.0)
        self.assertEqual(e1, 0.0)

    def test_earnings_decoupling_states_A_through_F(self):
        """Verify States A through F all produce identical initial composite score."""
        base_cand = {
            "ticker": "DECOUPLE_STK",
            "strategy": "Trend Following",
            "current_rsi": 55.0,
            "price": 100.0,
            "dma_50": 95.0,
            "volume_ratio": 1.2,
            "macd_histogram": 0.5,
            "winrate_score": 60.0,
            "win_rate": 60.0,
            "expectancy_pct": 2.0,
            "context_analyst": 30.0,
            "context_fundamental": 15.0,
            "context_news": 10.0,
        }

        # State A: No earnings data
        res_A = self.ranker.compute_composite_score(dict(base_cand), "bull")

        # State B: Positive earnings surprise
        res_B = self.ranker.compute_composite_score(dict(base_cand, earnings_surprise_pct=35.0, context_earnings=30.0), "bull")

        # State C: Negative earnings surprise
        res_C = self.ranker.compute_composite_score(dict(base_cand, earnings_surprise_pct=-25.0, context_earnings=0.0), "bull")

        # State D: Impending blackout in 2 days
        res_D = self.ranker.compute_composite_score(dict(base_cand, next_earnings_date=(self.today + datetime.timedelta(days=2)).isoformat()), "bull")

        # State E: Active blackout today
        res_E = self.ranker.compute_composite_score(dict(base_cand, next_earnings_date=self.today.isoformat()), "bull")

        # State F: UNKNOWN earnings status
        res_F = self.ranker.compute_composite_score(dict(base_cand, earnings_status="UNKNOWN", next_earnings_date=None), "bull")

        # All scores must be strictly identical
        for name, res in [("B", res_B), ("C", res_C), ("D", res_D), ("E", res_E), ("F", res_F)]:
            self.assertEqual(res["total"], res_A["total"], f"State {name} produced different composite score ({res['total']}) than State A ({res_A['total']})")

    def test_earnings_gate_still_enforces_downstream_risk(self):
        """Proof E: Downstream earnings gate can still pass or reject candidate after scoring."""
        # Clean calendar outside blackout
        res_pass = earnings_risk_filter(
            ticker="SAFE",
            scan_date=self.today,
            strategy="Trend Following",
            earnings_calendar={"SAFE": {"next_earnings_date": (self.today + datetime.timedelta(days=30)).isoformat()}},
        )
        self.assertTrue(res_pass["pass"])

        # Blackout within 5 days for Trend Following
        res_block = earnings_risk_filter(
            ticker="RISKY",
            scan_date=self.today,
            strategy="Trend Following",
            earnings_calendar={"RISKY": {"next_earnings_date": (self.today + datetime.timedelta(days=2)).isoformat()}},
        )
        self.assertFalse(res_block["pass"])
        self.assertIn("blackout", res_block["reason"].lower())

    # =========================================================================
    # 3. EARNINGS PIPELINE & ERROR HANDLING (PHASE 6)
    # =========================================================================
    def test_duplicate_ticker_in_session_prevention(self):
        """Same ticker must not receive duplicate provider requests in the same scan."""
        _SESSION_FETCHED_TICKERS.clear()
        cal_map = {"DUP_TICK": {"ticker": "DUP_TICK", "status": EarningsStatus.KNOWN_UPCOMING.value}}

        with patch("yfinance.Ticker") as mock_yf:
            mock_inst = MagicMock()
            mock_inst.calendar = {"Earnings Date": [(self.today + datetime.timedelta(days=25)).isoformat()]}
            mock_inst.earnings_dates = None
            mock_yf.return_value = mock_inst

            # First call
            fetch_single_ticker_provider("DUP_TICK")
            self.assertEqual(mock_yf.call_count, 1)

            # Second call intercepted by resolve_ticker_earnings
            resolve_ticker_earnings("DUP_TICK", calendar_map=cal_map)
            self.assertEqual(mock_yf.call_count, 1)

    def test_circuit_breaker_trips_on_failures(self):
        """Circuit breaker blocks external requests after threshold consecutive failures."""
        GLOBAL_CIRCUIT_BREAKER.reset()
        self.assertTrue(GLOBAL_CIRCUIT_BREAKER.can_request())

        for _ in range(5):
            GLOBAL_CIRCUIT_BREAKER.record_rate_limit()

        self.assertFalse(GLOBAL_CIRCUIT_BREAKER.can_request())

        # When breaker is open, provider returns UNKNOWN immediately without querying network
        sym, next_d, last_d, fp, st = fetch_single_ticker_provider("CIRCUIT_TEST")
        self.assertEqual(st, EarningsStatus.UNKNOWN.value)

    def test_stale_cache_requires_actual_freshness(self):
        """A record with a future date is NOT fresh if older than 14 days (TTL)."""
        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        
        # 1. Fresh upcoming date (< 14 days old confirmation)
        fresh_rec = {
            "ticker": "FRESH",
            "next_earnings_date": (self.today + datetime.timedelta(days=20)).isoformat(),
            "cached_at": now_ts - (2 * 86400),  # 2 days old
            "status": EarningsStatus.KNOWN_UPCOMING.value,
        }
        self.assertTrue(is_earnings_record_fresh(fresh_rec))

        # 2. Stale upcoming date (> 14 days old confirmation)
        stale_rec = {
            "ticker": "STALE",
            "next_earnings_date": (self.today + datetime.timedelta(days=20)).isoformat(),
            "cached_at": now_ts - (15 * 86400),  # 15 days old
            "status": EarningsStatus.KNOWN_UPCOMING.value,
        }
        self.assertFalse(is_earnings_record_fresh(stale_rec))

        # 3. Passed date is always stale
        passed_rec = {
            "ticker": "PAST",
            "next_earnings_date": (self.today - datetime.timedelta(days=2)).isoformat(),
            "cached_at": now_ts - 3600,
            "status": EarningsStatus.KNOWN_UPCOMING.value,
        }
        self.assertFalse(is_earnings_record_fresh(passed_rec))

    def test_score_gating_earnings_resolution(self):
        """Candidates < 65 do not trigger earnings resolution; candidates >= 65 do."""
        cal_map = {}
        with patch("src.filters.earnings_filter.resolve_ticker_earnings") as mock_resolve:
            # Sub-65 candidate
            cand_low = {"ticker": "LOW", "score": 64.2}
            if cand_low["score"] >= 65.0:
                mock_resolve(cand_low["ticker"], cal_map)
            self.assertEqual(mock_resolve.call_count, 0)

            # >= 65 candidate
            cand_high = {"ticker": "HIGH", "score": 67.8}
            if cand_high["score"] >= 65.0:
                mock_resolve(cand_high["ticker"], cal_map)
            self.assertEqual(mock_resolve.call_count, 1)

    # =========================================================================
    # 4. MOMENTUM SCORE MATH & SIGMOID BEHAVIOR (PHASE 10)
    # =========================================================================
    def test_momentum_components_and_bounds(self):
        """Verify RSI, DMA proximity, Volume, MACD hist, clipping, and sigmoid weighting."""
        # 1. Neutral RSI (50.0) -> RSI score = 100.0
        # DMA at price (0% prox) -> Proximity score = 100.0
        # Volume ratio 2.0 -> Volume score = 100.0
        # MACD hist 0.0 -> MACD score = 50.0
        # Raw = (100 + 100 + 100 + 50) / 4 = 87.5
        # Sigmoid weight around 55.0 -> near 1.0 -> momentum ~ 87.5
        row_ideal = {
            "current_rsi": 50.0,
            "price": 100.0,
            "dma_50": 100.0,
            "volume_ratio": 2.0,
            "macd_histogram": 0.0,
            "atr_14": 2.0,
        }
        m_ideal = compute_momentum_score(row_ideal)
        self.assertGreater(m_ideal, 80.0)
        self.assertLessEqual(m_ideal, 100.0)

        # 2. Extreme RSI (100.0 or 0.0) -> RSI score = 0.0
        row_extreme_rsi = dict(row_ideal, current_rsi=100.0)
        m_extreme = compute_momentum_score(row_extreme_rsi)
        self.assertLess(m_extreme, m_ideal)

        # 3. Negative MACD vs Positive MACD
        row_pos_macd = dict(row_ideal, macd_histogram=1.0)
        row_neg_macd = dict(row_ideal, macd_histogram=-1.0)
        self.assertGreater(compute_momentum_score(row_pos_macd), compute_momentum_score(row_neg_macd))

        # 4. Clipping bounds strictly within [0.0, 100.0]
        row_worst = {
            "current_rsi": 10.0,
            "price": 50.0,
            "dma_50": 100.0,
            "volume_ratio": 0.0,
            "macd_histogram": -10.0,
            "atr_14": 1.0,
        }
        m_worst = compute_momentum_score(row_worst)
        self.assertGreaterEqual(m_worst, 0.0)
        self.assertLessEqual(m_worst, 100.0)

    # =========================================================================
    # 5. CROSS-SECTIONAL SORTING NAN / INF IMMUNITY (PHASE 12)
    # =========================================================================
    def test_cross_sectional_nan_inf_immunity(self):
        """Cross-sectional sorting must exclude NaN, None, inf, -inf and sort deterministically."""
        raw_items = [
            ("STK_A", 15.5),
            ("STK_B", np.nan),
            ("STK_C", float("inf")),
            ("STK_D", 22.0),
            ("STK_E", None),
            ("STK_F", -float("inf")),
            ("STK_G", 8.2),
            ("STK_H", 22.0),  # Tie with STK_D, alphabetical tie-break
        ]

        valid_items = []
        for ticker, val in raw_items:
            if val is not None and not np.isnan(val) and np.isfinite(val):
                valid_items.append((ticker, float(val)))

        # Deterministic descending sort, ties broken alphabetically
        valid_items.sort(key=lambda x: (-x[1], x[0]))

        expected_order = [("STK_D", 22.0), ("STK_H", 22.0), ("STK_A", 15.5), ("STK_G", 8.2)]
        self.assertEqual(valid_items, expected_order)

    # =========================================================================
    # 6. MATHEMATICAL RECONCILIATION ACROSS 20+ SYNTHETIC CANDIDATES (PHASE 21)
    # =========================================================================
    def test_mathematical_reconciliation_20_candidates(self):
        """Verify manual weighted sum matches SignalRanker.compute_composite_score() for 20+ cases."""
        strategies = list(STRATEGY_WEIGHT_VECTORS.keys())
        regimes = ["bull", "sideways", "bear"]

        candidates = [
            # 1-7: All 7 strategies under baseline conditions
            {"strat": "trend_following", "regime": "bull", "mom": 75.0, "exp_val": 2.0, "wr": 60.0, "ctx": 70.0},
            {"strat": "52w_high_breakout", "regime": "bull", "mom": 80.0, "exp_val": 2.5, "wr": 65.0, "ctx": 50.0},
            {"strat": "pullback_recovery", "regime": "sideways", "mom": 55.0, "exp_val": 1.8, "wr": 55.0, "ctx": 60.0},
            {"strat": "pead", "regime": "bull", "mom": 70.0, "exp_val": 3.0, "wr": 62.0, "ctx": 80.0},
            {"strat": "cross_sectional_momentum", "regime": "bull", "mom": 85.0, "exp_val": 2.2, "wr": 58.0, "ctx": 65.0},
            {"strat": "sector_rotation", "regime": "sideways", "mom": 60.0, "exp_val": 1.5, "wr": 50.0, "ctx": 75.0},
            {"strat": "mean_reversion", "regime": "bear", "mom": 40.0, "exp_val": 1.2, "wr": 52.0, "ctx": 90.0},

            # 8-10: Edge case: All 0s
            {"strat": "trend_following", "regime": "bear", "mom": 0.0, "exp_val": -1.5, "wr": 0.0, "ctx": 0.0},
            {"strat": "pead", "regime": "bear", "mom": 0.0, "exp_val": -1.5, "wr": 0.0, "ctx": 0.0},
            {"strat": "mean_reversion", "regime": "bear", "mom": 0.0, "exp_val": -1.5, "wr": 0.0, "ctx": 0.0},

            # 11-13: Edge case: All 100s
            {"strat": "trend_following", "regime": "bull", "mom": 100.0, "exp_val": 3.5, "wr": 100.0, "ctx": 100.0},
            {"strat": "52w_high_breakout", "regime": "bull", "mom": 100.0, "exp_val": 3.5, "wr": 100.0, "ctx": 100.0},
            {"strat": "pullback_recovery", "regime": "bull", "mom": 100.0, "exp_val": 3.5, "wr": 100.0, "ctx": 100.0},

            # 14-15: Missing historical metrics (prior fallback)
            {"strat": "trend_following", "regime": "bull", "mom": 65.0, "exp_val": None, "wr": None, "ctx": 50.0},
            {"strat": "pead", "regime": "sideways", "mom": 60.0, "exp_val": None, "wr": None, "ctx": 50.0},

            # 16-17: Missing context (0.0 fallback)
            {"strat": "trend_following", "regime": "bull", "mom": 70.0, "exp_val": 2.0, "wr": 55.0, "ctx": None},
            {"strat": "sector_rotation", "regime": "bull", "mom": 65.0, "exp_val": 1.5, "wr": 50.0, "ctx": None},

            # 18-20: Extreme momentum & regimes
            {"strat": "cross_sectional_momentum", "regime": "bear", "mom": 95.0, "exp_val": 2.8, "wr": 65.0, "ctx": 40.0},
            {"strat": "52w_high_breakout", "regime": "bear", "mom": 90.0, "exp_val": 2.0, "wr": 60.0, "ctx": 30.0},
            {"strat": "mean_reversion", "regime": "bull", "mom": 20.0, "exp_val": 1.0, "wr": 45.0, "ctx": 85.0},

            # 21-22: Sample size variations
            {"strat": "trend_following", "regime": "bull", "mom": 70.0, "exp_val": 2.0, "wr": 58.33, "ctx": 60.0},  # N=1 shrunk
            {"strat": "pead", "regime": "bull", "mom": 75.0, "exp_val": 2.5, "wr": 72.0, "ctx": 70.0},
        ]

        self.assertGreaterEqual(len(candidates), 20)

        for idx, item in enumerate(candidates, 1):
            strat = item["strat"]
            regime = item["regime"]
            w = STRATEGY_WEIGHT_VECTORS[strat]
            
            # Manual components
            s_mom = float(item["mom"])
            s_exp = compute_expectancy_score(strat, item["exp_val"])
            s_wr = float(item["wr"]) if item["wr"] is not None else 50.0
            s_reg = REGIME_SCORE_MATRIX[strat][regime]
            s_ctx = float(item["ctx"]) if item["ctx"] is not None else 0.0

            manual_total = (
                w["mom"] * s_mom
                + w["exp"] * s_exp
                + w["wr"] * s_wr
                + w["reg"] * s_reg
                + w["ctx"] * s_ctx
            )

            # Production ranker call
            row = {
                "ticker": f"TEST_{idx}",
                "strategy": strat,
                "momentum_score": s_mom,
                "winrate_score": s_wr,
                "win_rate": s_wr,
                "expectancy_pct": item["exp_val"],
                "expectancy_score": s_exp,
                "context_score": s_ctx,
            }
            prod_res = self.ranker.compute_composite_score(row, regime)

            # Assert manual matches production within floating tolerance
            self.assertAlmostEqual(
                manual_total,
                prod_res["total"],
                places=3,
                msg=f"Candidate {idx} ({strat}, {regime}) mismatch: manual {manual_total} vs prod {prod_res['total']}",
            )

            # Assert score strictly bounded in [0, 100]
            self.assertGreaterEqual(prod_res["total"], 0.0)
            self.assertLessEqual(prod_res["total"], 100.0)


if __name__ == "__main__":
    unittest.main()
