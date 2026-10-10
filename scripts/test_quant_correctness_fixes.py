"""
Quantitative Correctness Fixes — Regression Suite
=================================================
Locks in the fixes from the October 2026 math review:
1. Outcome engine: bars exhausted after T1 are censored (unresolved), never force-closed as expired.
2. Outcome engine: STOP_FIRST applies to the ratcheted (breakeven / T1) stop on the same bar as a target.
3. Outcome engine: a target fills at the open when the bar gaps above it.
4. Composite score: unavailable context is excluded and weights renormalized (not scored as 0).
5. PEAD: the earnings reaction bar is located by date, not by calendar-day offset.
6. Survivorship: an empty registry sector never matches every sector.
7. Reach probability: effective sample size and Wilson interval reflect overlapping windows.
"""

import os
import sys
import unittest
from datetime import date

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.outcome.outcome_calculator import evaluate_signal_outcome
from src.ranker import SignalRanker
from src.quant_config import STRATEGY_WEIGHT_VECTORS
from src.strategies.target_calculator import (
    wilson_interval,
    get_reach_prob_target_before_stop_structured,
    reset_reach_prob_cache,
)


def _ohlc(start, rows):
    idx = pd.bdate_range(start=start, periods=len(rows))
    return pd.DataFrame([{"Open": o, "High": h, "Low": l, "Close": c} for (o, h, l, c) in rows], index=idx)


class TestOutcomeEngine(unittest.TestCase):
    def test_bars_exhausted_after_t1_is_censored_not_expired(self):
        df = _ohlc("2026-01-05", [(100.0, 111.0, 101.0, 109.0)])  # T1 hit, then data ends
        self.assertIsNone(evaluate_signal_outcome(df, 100.0, 93.0, 110.0, 120.0, 130.0, max_holding_days=20))
        res = evaluate_signal_outcome(df, 100.0, 93.0, 110.0, 120.0, 130.0, max_holding_days=20, return_open=True)
        self.assertFalse(res["is_closed"])
        self.assertEqual(res["outcome"], "hit_t1")
        self.assertAlmostEqual(res["remaining_weight"], 0.5)
        self.assertAlmostEqual(res["realized_return_pct"], 5.0)  # only the exited 50% at +10%
        self.assertAlmostEqual(res["current_stop"], 100.0)

    def test_same_bar_ratchet_stop_first(self):
        # T1 (110) and breakeven (100) both inside the same bar's range -> remainder stopped at breakeven
        df = _ohlc("2026-01-05", [(101.0, 112.0, 99.0, 108.0)])
        res = evaluate_signal_outcome(df, 100.0, 93.0, 110.0, 120.0, 130.0)
        self.assertEqual(res["outcome"], "hit_t1")
        self.assertTrue(res["is_closed"])
        self.assertAlmostEqual(res["outcome_return_pct"], 5.0)
        # The non-conservative policy keeps the remainder open on that bar
        res_tf = evaluate_signal_outcome(df, 100.0, 93.0, 110.0, 120.0, 130.0, ambiguity_policy="TARGET_FIRST")
        self.assertIsNone(res_tf)

    def test_gap_above_target_fills_at_open(self):
        df = _ohlc("2026-01-05", [(100.0, 104.0, 99.5, 103.0), (113.0, 115.0, 112.0, 114.0), (112.0, 112.5, 109.0, 110.0)])
        res = evaluate_signal_outcome(df, 100.0, 93.0, 110.0, 120.0, 130.0)
        # 50% exits at the 113 open (not 110), remainder stopped at breakeven... never reached; still open
        self.assertIsNone(res)
        res_open = evaluate_signal_outcome(df, 100.0, 93.0, 110.0, 120.0, 130.0, return_open=True)
        self.assertAlmostEqual(res_open["realized_return_pct"], 0.5 * 13.0)


class TestContextRenormalization(unittest.TestCase):
    def _row(self, **extra):
        row = {
            "ticker": "TEST", "strategy": "Mean Reversion",
            "momentum_score": 80.0, "expectancy_score": 70.0, "winrate_score": 60.0,
            "regime_score": 100.0,
        }
        row.update(extra)
        return row

    def test_unavailable_context_is_excluded_not_zeroed(self):
        ranker = SignalRanker()
        w = STRATEGY_WEIGHT_VECTORS["mean_reversion"]
        res = ranker.compute_composite_score(self._row(context_available=False, context_score=None), "bear")
        expected = (w["mom"] * 80 + w["exp"] * 70 + w["wr"] * 60 + w["reg"] * 100) / (1 - w["ctx"])
        self.assertAlmostEqual(res["total"], round(expected, 4), places=3)
        self.assertIsNone(res["breakdown"]["context"])
        self.assertFalse(res["context_available"])
        # Mean Reversion (40% context weight) can now qualify on strong evidence without context
        self.assertGreaterEqual(res["total"], 65.0)

    def test_genuine_zero_context_still_counts(self):
        ranker = SignalRanker()
        w = STRATEGY_WEIGHT_VECTORS["mean_reversion"]
        res = ranker.compute_composite_score(self._row(context_available=True, context_score=0.0), "bear")
        expected = w["mom"] * 80 + w["exp"] * 70 + w["wr"] * 60 + w["reg"] * 100
        self.assertAlmostEqual(res["total"], round(expected, 4), places=3)


class TestPEADReactionBar(unittest.TestCase):
    def test_weekend_does_not_shift_reaction_bar(self):
        from jobs.strategies.pead import PEADStrategy

        # Thursday 2026-03-05 after-close report; Friday 03-06 is the +8% reaction; scan on Monday 03-09.
        idx = pd.bdate_range(end="2026-03-09", periods=80)
        close = np.linspace(80.0, 100.0, len(idx))
        df = pd.DataFrame({"CLOSE": close}, index=idx)
        fri = idx.get_loc(pd.Timestamp("2026-03-06"))
        df.iloc[fri:, 0] = df["CLOSE"].iloc[fri - 1] * 1.08
        df.iloc[-1, 0] = df["CLOSE"].iloc[fri] * 0.995
        df["HIGH"] = df["CLOSE"] * 1.01
        df["LOW"] = df["CLOSE"] * 0.99
        df["OPEN"] = df["CLOSE"]
        df["VOLUME"] = 1_000_000.0
        df.iloc[fri, df.columns.get_loc("VOLUME")] = 4_000_000.0
        df["ADX_14"] = 25.0
        df["ATR_14"] = 2.0
        df["EMA_20"] = df["CLOSE"]
        df["RSI_14"] = 60.0
        df["MACD_HIST"] = 0.3

        strat = PEADStrategy()
        strat.set_earnings_calendar({"PEADX": {"last_earnings_date": "2026-03-05"}})
        sig = strat.scan("PEADX", df, "bull", {"wins": 10, "losses": 5, "win_rate": 66.7})
        self.assertIsNotNone(sig, "Friday reaction bar must be found despite the weekend")
        self.assertIn("+8.0%", sig["narrative"])


class TestSurvivorshipSectorMatch(unittest.TestCase):
    def test_empty_registry_sector_never_matches(self):
        import src.filters.survivorship_bias as sb
        original = sb._DELISTED_TICKERS_CACHE
        try:
            sb._DELISTED_TICKERS_CACHE = [
                {"ticker": "NOSECTOR", "sector": ""},
                {"ticker": "TECHCO", "sector": "Information Technology"},
            ]
            self.assertEqual(sb.get_delisted_tickers_by_sector("Health Care"), [])
            self.assertEqual(sb.get_delisted_tickers_by_sector("Information Technology"), ["TECHCO"])
        finally:
            sb._DELISTED_TICKERS_CACHE = original


class TestReachProbabilitySampleQuality(unittest.TestCase):
    def test_wilson_interval_bounds(self):
        lo, hi = wilson_interval(0.5, 25)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)
        self.assertAlmostEqual(lo, 0.3175, places=3)
        self.assertEqual(wilson_interval(0.5, 0), (None, None))

    def test_effective_samples_account_for_overlap(self):
        reset_reach_prob_cache()
        idx = pd.bdate_range(end="2026-09-30", periods=300)
        rng = np.random.default_rng(7)
        close = 100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.015, len(idx)))
        df = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close}, index=idx)
        res = get_reach_prob_target_before_stop_structured("SIMX", 0.05, 0.05, 10, price_df=df, lookback_days=504)
        self.assertEqual(res.sample_count, 290)
        self.assertAlmostEqual(res.effective_samples, 29.0)
        self.assertLessEqual(res.ci_low, res.raw_prob)
        self.assertGreaterEqual(res.ci_high, res.raw_prob)
        # Cached lookups keep the sample-quality metadata
        cached = get_reach_prob_target_before_stop_structured("SIMX", 0.05, 0.05, 10, price_df=df, lookback_days=504)
        self.assertEqual(cached.effective_samples, res.effective_samples)


class TestStrategyAwareMomentum(unittest.TestCase):
    def _row(self, strategy, rsi, price):
        return {"strategy": strategy, "current_rsi": rsi, "price": price, "dma_50": 100.0,
                "volume_ratio": 1.0, "macd_histogram": 0.2, "atr_14": 2.0}

    def test_trend_strategies_reward_strength(self):
        from src.ranker import compute_momentum_score
        strong = compute_momentum_score(self._row("Trend Following", 65.0, 105.0))
        neutral = compute_momentum_score(self._row("Trend Following", 50.0, 100.0))
        below = compute_momentum_score(self._row("Trend Following", 50.0, 97.0))
        self.assertGreater(strong, neutral)
        self.assertGreater(neutral, below)

    def test_pullback_keeps_near_average_scoring(self):
        from src.ranker import compute_momentum_score
        near = compute_momentum_score(self._row("Pullback Recovery", 50.0, 100.0))
        strong = compute_momentum_score(self._row("Pullback Recovery", 65.0, 105.0))
        self.assertGreater(near, strong)


class TestSetupConditionalReachProbability(unittest.TestCase):
    def test_strategy_hit_rates_replace_ticker_base_rate(self):
        from src.strategies.target_calculator import calculate_targets
        res = calculate_targets(
            ticker="CONDX", entry_price=100.0, atr_14=2.0, stop_loss=94.0, strategy_name="Trend Following",
            strategy_target_hits={"t1": (40, 100), "t2": (15, 100), "t3": (5, 100)},
        )
        self.assertEqual(res.reach_prob_source, "strategy_conditional")
        self.assertAlmostEqual(res.reach_prob_t1, 0.40)
        self.assertAlmostEqual(res.reach_prob_t2, 0.15)
        self.assertEqual(res.reach_prob_effective_samples, 100.0)
        self.assertLess(res.reach_prob_t1_ci_low, 0.40)
        self.assertGreater(res.reach_prob_t1_ci_high, 0.40)
        # trend t1_min 0.30 passes, t2_min 0.12 passes, t3_min 0.15 fails -> T3 pruned
        self.assertIsNone(res.target_3)

    def test_too_few_strategy_trades_falls_back(self):
        from src.strategies.target_calculator import calculate_targets
        res = calculate_targets(
            ticker="CONDY", entry_price=100.0, atr_14=2.0, stop_loss=94.0, strategy_name="Trend Following",
            strategy_target_hits={"t1": (5, 10)},
        )
        self.assertEqual(res.reach_prob_source, "ticker_base_rate")


class TestStrategyRuleTightening(unittest.TestCase):
    def _frame(self, rsi_path, closes=None):
        n = 260
        idx = pd.bdate_range(end="2026-06-30", periods=n)
        close = np.linspace(80.0, 120.0, n) if closes is None else closes
        df = pd.DataFrame({"CLOSE": close, "HIGH": close * 1.01, "LOW": close * 0.99, "OPEN": close,
                           "VOLUME": 1_000_000.0}, index=idx)
        rsi = np.full(n, 55.0)
        rsi[-len(rsi_path):] = rsi_path
        df["RSI_14"] = rsi
        df["DMA_50"] = df["CLOSE"].rolling(50).mean()
        df["DMA_200"] = df["CLOSE"].rolling(200).mean()
        df["VOLUME_MA_20"] = 1_000_000.0
        df["ADX_14"] = 25.0
        df["MACD_LINE"] = 0.5
        df["MACD_SIGNAL"] = 0.3
        df["MACD_HIST"] = 0.2
        df["EMA_20"] = df["CLOSE"]
        df["ATR_14"] = 2.0
        return df

    def test_pullback_requires_rsi_to_rise_off_its_low(self):
        from jobs.strategies.pullback import PullbackRecoveryStrategy
        strat = PullbackRecoveryStrategy()
        # RSI dipped to 46 and is still 46-47: inside the band but no recovery
        sig, gate = strat._check_latest_signal("PBX", self._frame([48, 47, 46, 46.5, 47]), "PBX", "Tech", 0, "bull")
        self.assertIsNone(sig)
        self.assertEqual(gate, "failed_rsi_gate")

    def test_mean_reversion_requires_a_bounce(self):
        from jobs.strategies.mean_reversion import MeanReversionStrategy
        n = 260
        closes = np.concatenate([np.full(n - 25, 100.0), np.linspace(100.0, 85.0, 25)])  # still falling
        df = self._frame([34, 33, 32, 31, 30], closes=closes)
        self.assertIsNone(MeanReversionStrategy().scan("MRX", df, "bear", {}))


class TestDataAndPersistenceFixes(unittest.TestCase):
    def test_refresh_cache_end_date_is_inclusive(self):
        """yfinance's end is exclusive: refresh_cache must request end+1 so the market date is cached."""
        from unittest.mock import patch
        from src.data.cache_manager import get_cache_manager
        cm = get_cache_manager()
        seen = {}
        def fake_download(tickers, start, end):
            seen["start"], seen["end"] = start, end
            return pd.DataFrame()
        with patch.object(cm, "download_batch_with_retry", side_effect=fake_download), patch("time.sleep"):
            cm.refresh_cache(["AAA"], "2026-10-08", "2026-10-09")
        self.assertEqual(seen["end"], "2026-10-10")

    def test_missing_column_retry_drops_only_the_named_column(self):
        from jobs.generate_signals import write_with_missing_column_retry
        written = []
        def write(rows):
            if any("reference_entry_price" in r for r in rows):
                raise Exception("{'code': 'PGRST204', 'message': \"Could not find the 'reference_entry_price' column of 'signals' in the schema cache\"}")
            written.extend(rows)
        rows = [{"ticker": "A", "reference_entry_price": 1.0, "pe_ratio": 20.0, "strategy_trades": 50}]
        dropped = write_with_missing_column_retry(write, rows, {"reference_entry_price", "pe_ratio", "strategy_trades"})
        self.assertEqual(dropped, ["reference_entry_price"])
        self.assertEqual(written[0]["pe_ratio"], 20.0)       # kept
        self.assertEqual(written[0]["strategy_trades"], 50)  # kept
        # A column outside the allowed set is never silently dropped
        def write_bad(rows):
            raise Exception("Could not find the 'ticker' column of 'signals' in the schema cache")
        with self.assertRaises(Exception):
            write_with_missing_column_retry(write_bad, rows, {"pe_ratio"})

    def test_get_client_raises_instead_of_exiting(self):
        import os
        from unittest.mock import patch
        from jobs.supabase_client import get_client, SupabaseConfigError
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with self.assertRaises(SupabaseConfigError):
                get_client()


class TestContextDataQuality(unittest.TestCase):
    def test_no_technical_fallback_in_context_score(self):
        from src.scorers.context_scorer import ContextScorer
        from src.providers.base import AggregatedContext
        score = ContextScorer().calculate(AggregatedContext(), 100.0, {"rsi": 70, "adx": 40, "volume_ratio": 2.0})
        self.assertEqual(score, 0.0)

    def test_provider_failure_marks_context_unavailable(self):
        from unittest.mock import patch
        from src.providers.context.aggregator import ContextAggregator
        from src.providers.base import DataQuality, NewsContext
        agg = ContextAggregator()
        with patch("jobs.supabase_client.get_client", side_effect=Exception("no db")),              patch.object(agg.metadata, "_get_info", return_value=None),              patch.object(agg.news, "fetch_and_score", return_value=NewsContext()):
            ctx = agg.get_aggregated("FAILX", None)
        self.assertEqual(ctx.quality, DataQuality.UNAVAILABLE)
        self.assertEqual(ctx.fundamental.quality, DataQuality.UNAVAILABLE)

    def test_metadata_single_request_per_ticker(self):
        from unittest.mock import patch, MagicMock
        from src.providers.context.metadata_provider import MetadataProvider
        mp = MetadataProvider()
        fake = MagicMock(); fake.info = {"trailingPE": 25.0, "debtToEquity": 80.0, "currentRatio": 1.2, "targetMeanPrice": 110.0}
        with patch("yfinance.Ticker", return_value=fake) as tk:
            f = mp.get_fundamentals("ONEX"); a = mp.get_analyst_rating("ONEX")
        self.assertEqual(tk.call_count, 1)
        self.assertAlmostEqual(f.debt_to_equity, 0.8)
        self.assertEqual(f.trailing_pe, 25.0)
        self.assertEqual(a.target_mean_price, 110.0)


class TestTrendQualityGates(unittest.TestCase):
    def _df(self, closes):
        idx = pd.bdate_range(end="2026-06-30", periods=len(closes))
        df = pd.DataFrame({"CLOSE": closes, "HIGH": closes * 1.01, "LOW": closes * 0.99, "OPEN": closes,
                           "VOLUME": 1_000_000.0}, index=idx)
        df["DMA_50"] = df["CLOSE"].rolling(50).mean()
        df["DMA_200"] = df["CLOSE"].rolling(200).mean()
        df["VOLUME_MA_20"] = 1_000_000.0
        df["RSI_14"] = 55.0; df["ADX_14"] = 25.0; df["MACD_LINE"] = 0.5; df["MACD_SIGNAL"] = 0.3
        df["MACD_HIST"] = 0.2; df["EMA_20"] = df["CLOSE"]; df["ATR_14"] = 2.0
        return df

    def test_pullback_rejects_bounce_inside_downtrend_even_in_bull_regime(self):
        from jobs.strategies.pullback import PullbackRecoveryStrategy
        # Long decline, then a short bounce above the (falling) 50 DMA: 50 DMA < 200 DMA
        closes = np.concatenate([np.linspace(150, 100, 240), np.linspace(100, 112, 20)])
        sig, gate = PullbackRecoveryStrategy()._check_latest_signal("DTX", self._df(closes), "DTX", "X", 0, "bull")
        self.assertIsNone(sig)
        self.assertEqual(gate, "failed_trend_gate")

    def test_cross_sectional_rejects_falling_200dma(self):
        from jobs.strategies.cross_sectional import CrossSectionalMomentumStrategy
        closes = np.concatenate([np.linspace(160, 90, 200), np.linspace(90, 120, 60)])  # strong 3m rebound
        self.assertIsNone(CrossSectionalMomentumStrategy().scan("CSX", self._df(closes), "bull", {}))

    def test_trend_following_rejects_falling_200dma(self):
        from jobs.strategies.trend_following import TrendFollowingStrategy
        closes = np.concatenate([np.linspace(160, 80, 200), np.linspace(80, 130, 60)])
        self.assertIsNone(TrendFollowingStrategy().scan("TFX", self._df(closes), "bull", {}))


if __name__ == "__main__":
    unittest.main()
