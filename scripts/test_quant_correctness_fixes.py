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
        from src.providers.context.sec_fundamentals import SecFundamentalsProvider
        mp = MetadataProvider(sec_provider=SecFundamentalsProvider(user_agent=""))  # SEC off: Yahoo path only
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



def _fact(val, end, filed, start=None, form="10-Q", fp="Q2"):
    f = {"val": val, "end": end, "filed": filed, "form": form, "fp": fp}
    if start:
        f["start"] = start
    return f


def _company(gaap):
    return {"facts": {"us-gaap": {tag: {"units": units} for tag, units in gaap.items()}}}


class TestSecFundamentals(unittest.TestCase):
    """SEC EDGAR extraction on synthetic filings (no network)."""

    def _base(self):
        return {
            "EarningsPerShareDiluted": {"USD/shares": [
                _fact(10.0, "2025-12-31", "2026-02-20", start="2025-01-01", form="10-K", fp="FY"),
                _fact(2.0, "2025-06-30", "2025-07-30", start="2025-01-01"),   # prior-year H1
                _fact(3.0, "2026-06-30", "2026-07-30", start="2026-01-01"),   # current H1
            ]},
            "AssetsCurrent": {"USD": [_fact(300.0, "2026-06-30", "2026-07-30")]},
            "LiabilitiesCurrent": {"USD": [_fact(200.0, "2026-06-30", "2026-07-30")]},
            "StockholdersEquity": {"USD": [_fact(1000.0, "2026-06-30", "2026-07-30")]},
            "LongTermDebt": {"USD": [_fact(400.0, "2026-06-30", "2026-07-30")]},
            "ShortTermBorrowings": {"USD": [_fact(100.0, "2026-06-30", "2026-07-30")]},
            "OperatingLeaseLiability": {"USD": [_fact(50.0, "2026-06-30", "2026-07-30")]},
        }

    def test_ttm_eps_pe_de_cr(self):
        from src.providers.context.sec_fundamentals import extract_fundamentals
        f = extract_fundamentals(_company(self._base()), reference_date=date(2026, 10, 1))
        self.assertAlmostEqual(f.eps_ttm, 11.0)            # 10 + 3 - 2
        self.assertEqual(f.eps_period_end, "2026-06-30")
        self.assertAlmostEqual(f.pe_ratio(220.0), 20.0)
        self.assertAlmostEqual(f.current_ratio, 1.5)
        self.assertAlmostEqual(f.debt_to_equity, 0.55)     # (400 + 100 + 50 leases) / 1000
        self.assertIsNone(f.pe_ratio(None))

    def test_loss_maker_has_no_pe(self):
        from src.providers.context.sec_fundamentals import extract_fundamentals
        g = self._base()
        g["EarningsPerShareDiluted"]["USD/shares"][2]["val"] = -11.0
        f = extract_fundamentals(_company(g), reference_date=date(2026, 10, 1))
        self.assertLess(f.eps_ttm, 0)
        self.assertIsNone(f.pe_ratio(100.0))

    def test_point_in_time_excludes_later_filings(self):
        from src.providers.context.sec_fundamentals import extract_fundamentals
        g = self._base()
        # As of 2026-07-15 the H1-2026 10-Q (filed 2026-07-30) does not exist yet
        f = extract_fundamentals(_company(g), as_of=date(2026, 7, 15), reference_date=date(2026, 7, 15))
        self.assertEqual(f.eps_period_end, "2025-12-31")
        self.assertAlmostEqual(f.eps_ttm, 10.0)
        self.assertIsNone(f.current_ratio)  # no balance sheet filed yet as of that date

    def test_split_between_filings_uses_net_income(self):
        from src.providers.context.sec_fundamentals import extract_fundamentals
        g = self._base()
        # 10:1 split after the 10-K: annual EPS pre-split (10.0), YTD EPS post-split (0.3 / 0.2)
        g["EarningsPerShareDiluted"]["USD/shares"][1]["val"] = 0.2
        g["EarningsPerShareDiluted"]["USD/shares"][2]["val"] = 0.3
        g["NetIncomeLoss"] = {"USD": [
            _fact(1000.0, "2025-12-31", "2026-02-20", start="2025-01-01", form="10-K", fp="FY"),
            _fact(200.0, "2025-06-30", "2025-07-30", start="2025-01-01"),
            _fact(300.0, "2026-06-30", "2026-07-30", start="2026-01-01"),
        ]}
        g["WeightedAverageNumberOfDilutedSharesOutstanding"] = {"shares": [
            _fact(1000.0, "2026-06-30", "2026-07-30", start="2026-04-01"),  # post-split share count
        ]}
        f = extract_fundamentals(_company(g), reference_date=date(2026, 10, 1))
        self.assertAlmostEqual(f.eps_ttm, 1.1)  # (1000 + 300 - 200) / 1000, not 10 + 0.3 - 0.2
        self.assertIn("split-check", f.eps_method)

    def test_unknown_borrowings_are_never_zero(self):
        from src.providers.context.sec_fundamentals import extract_fundamentals
        g = self._base()
        # Debt was tagged in an older period but not on the latest balance-sheet date
        g["LongTermDebt"]["USD"][0]["end"] = "2025-12-31"
        g["ShortTermBorrowings"]["USD"][0]["end"] = "2025-12-31"
        f = extract_fundamentals(_company(g), reference_date=date(2026, 10, 1))
        self.assertIsNone(f.debt_to_equity)
        # A company that never tagged borrowings: only leases count
        del g["LongTermDebt"]
        del g["ShortTermBorrowings"]
        f2 = extract_fundamentals(_company(g), reference_date=date(2026, 10, 1))
        self.assertAlmostEqual(f2.debt_to_equity, 0.05)

    def test_reit_style_debt_tags(self):
        from src.providers.context.sec_fundamentals import extract_fundamentals
        g = self._base()
        del g["LongTermDebt"]
        del g["ShortTermBorrowings"]
        g["SecuredDebt"] = {"USD": [_fact(700.0, "2026-06-30", "2026-07-30")]}
        g["UnsecuredDebt"] = {"USD": [_fact(300.0, "2026-06-30", "2026-07-30")]}
        f = extract_fundamentals(_company(g), reference_date=date(2026, 10, 1))
        self.assertAlmostEqual(f.debt_to_equity, 1.05)  # (700 + 300 + 50) / 1000

    def test_stale_filer_returns_nothing(self):
        from src.providers.context.sec_fundamentals import extract_fundamentals
        f = extract_fundamentals(_company(self._base()), reference_date=date(2027, 6, 1))
        self.assertIsNone(f.eps_ttm)
        self.assertIsNone(f.current_ratio)

    def test_metadata_prefers_sec_and_fills_gaps_from_yahoo(self):
        from unittest.mock import MagicMock, patch
        from src.providers.context.metadata_provider import MetadataProvider
        from src.providers.context.sec_fundamentals import SecFundamentals
        sec = MagicMock()
        sec.available = True
        sec.fundamentals.return_value = SecFundamentals(eps_ttm=5.0, debt_to_equity=0.4, current_ratio=None, balance_sheet_date="2026-06-30")
        mp = MetadataProvider(sec_provider=sec)
        fake = MagicMock()
        fake.info = {"trailingPE": 99.0, "debtToEquity": 300.0, "currentRatio": 2.0}
        with patch("yfinance.Ticker", return_value=fake):
            f = mp.get_fundamentals("GAPX", price=100.0)
        self.assertEqual(f.trailing_pe, 20.0)      # SEC: 100 / 5
        self.assertEqual(f.debt_to_equity, 0.4)    # SEC wins
        self.assertEqual(f.current_ratio, 2.0)     # gap filled by Yahoo
        self.assertEqual(f.source, "sec+yahoo")

    def test_yahoo_non_finite_values_are_missing(self):
        from unittest.mock import MagicMock, patch
        from src.providers.context.metadata_provider import MetadataProvider
        from src.providers.context.sec_fundamentals import SecFundamentalsProvider
        mp = MetadataProvider(sec_provider=SecFundamentalsProvider(user_agent=""))  # SEC off: Yahoo path only
        fake = MagicMock()
        fake.info = {"trailingPE": "Infinity", "debtToEquity": float("nan"), "currentRatio": 1.4, "targetMeanPrice": "n/a"}
        with patch("yfinance.Ticker", return_value=fake):
            f = mp.get_fundamentals("JUNK", price=10.0)
            a = mp.get_analyst_rating("JUNK")
        self.assertIsNone(f.trailing_pe)
        self.assertIsNone(f.debt_to_equity)
        self.assertEqual(f.current_ratio, 1.4)
        self.assertIsNone(a.target_mean_price)


class TestContextNormalization(unittest.TestCase):
    def test_missing_analyst_is_excluded_not_zero(self):
        from src.scorers.context_scorer import ContextScorer
        from src.providers.base import AggregatedContext, FundamentalContext, DataQuality
        ctx = AggregatedContext(fundamental=FundamentalContext(debt_to_equity=0.5, current_ratio=2.0, quality=DataQuality.VALID))
        total, a, f, n, avail = ContextScorer().calculate_with_components(ctx, 100.0, ("analyst", "fundamental", "news"))
        self.assertEqual((a, f, avail), (0.0, 20.0, 20.0))
        self.assertEqual(total, 100.0)   # perfect fundamentals, no analyst data: 100, not 25

    def test_fundamentals_are_display_only_by_default(self):
        from src.scorers.context_scorer import ContextScorer
        from src.providers.base import AggregatedContext, AnalystContext, FundamentalContext, DataQuality
        from src.quant_config import CONTEXT_SCORE_COMPONENTS
        self.assertNotIn("fundamental", CONTEXT_SCORE_COMPONENTS)
        fund_only = AggregatedContext(fundamental=FundamentalContext(debt_to_equity=0.5, current_ratio=2.0, quality=DataQuality.VALID))
        self.assertIsNone(ContextScorer().calculate_with_components(fund_only, 100.0)[0])  # unavailable, not 100
        both = AggregatedContext(
            analyst=AnalystContext(target_mean_price=120.0, recommendation="buy", quality=DataQuality.VALID),
            fundamental=FundamentalContext(debt_to_equity=5.0, current_ratio=0.5, quality=DataQuality.VALID),
        )
        total, a, f, n, avail = ContextScorer().calculate_with_components(both, 100.0)
        self.assertEqual((total, a, f, avail), (100.0, 40.0, 0.0, 40.0))  # weak balance sheet does not count

    def test_no_component_means_unavailable(self):
        from src.scorers.context_scorer import ContextScorer
        from src.providers.base import AggregatedContext
        total = ContextScorer().calculate_with_components(AggregatedContext(), 100.0)[0]
        self.assertIsNone(total)

    def test_ranker_uses_available_points(self):
        from src.ranker import compute_context_score
        self.assertEqual(compute_context_score(fundamental_pts=10.0, max_points=20.0), 50.0)
        # legacy rows without max_points keep the old scale
        self.assertEqual(compute_context_score(fundamental_pts=10.0), 12.5)



class TestSelectionSettings(unittest.TestCase):
    """Selection switches shared by the nightly scan and the backtest."""

    def test_defaults_and_validation(self):
        from src.pipeline_steps import SelectionSettings, selection_settings
        st = selection_settings()
        self.assertEqual((st.momentum_model, st.entry_location_mode), ("technical", "gate"))
        self.assertTrue(st.blocks_entry("WAIT") and st.blocks_entry("REJECT"))
        self.assertFalse(st.blocks_entry("BUY"))
        info = SelectionSettings("technical", "info", ())
        self.assertFalse(info.blocks_entry("WAIT") or info.blocks_entry("REJECT"))
        with self.assertRaises(ValueError):
            SelectionSettings("rsi_only", "gate", ())
        with self.assertRaises(ValueError):
            SelectionSettings("technical", "gate", ("earnings",))

    def test_relative_strength_percentiles(self):
        from src.pipeline_steps import relative_strength_percentiles
        idx = pd.bdate_range("2024-01-01", periods=10)
        # A doubles, B flat, C halves between t-4 and t-1; the latest bar (t) is skipped
        closes = pd.DataFrame({
            "A": [10, 10, 10, 10, 10, 10, 20, 20, 20, 99],
            "B": [10] * 9 + [1],
            "C": [10, 10, 10, 10, 10, 10, 5, 5, 5, 99],
        }, index=idx, dtype=float)
        rs = relative_strength_percentiles(closes, lookback=4, skip=1)
        last = rs.iloc[-1]
        np.testing.assert_allclose([last["A"], last["B"], last["C"]], [100.0, 200 / 3, 100 / 3])
        self.assertTrue(rs.iloc[:4].isna().all().all())  # not enough history yet
        # Ineligible tickers leave the cross-section
        elig = pd.DataFrame(True, index=idx, columns=closes.columns)
        elig["C"] = False
        last2 = relative_strength_percentiles(closes, eligible=elig, lookback=4, skip=1).iloc[-1]
        self.assertEqual((last2["A"], last2["B"]), (100.0, 50.0))
        self.assertTrue(np.isnan(last2["C"]))

    def test_rs_momentum_uses_percentile_and_fails_closed(self):
        from src.pipeline_steps import SelectionSettings, prepare_candidate_scores, composite_score
        rs_settings = SelectionSettings("relative_strength_12m", "gate", ())
        base = {"ticker": "RSX", "strategy": "Trend Following", "current_rsi": 60.0, "price": 100.0,
                "entry_price": 100.0, "dma_50": 95.0, "volume_ratio": 1.2, "macd_histogram": 0.5, "atr_14": 2.0,
                "context_available": False}
        sig = dict(base, rs_percentile_12m=87.5)
        prepare_candidate_scores(sig, "bull", settings=rs_settings)
        self.assertEqual(sig["momentum_score"], 87.5)
        self.assertIsNone(composite_score(sig, "bull", SignalRanker()))
        self.assertEqual(sig["score_breakdown"]["momentum"], 87.5)
        missing = dict(base, rs_percentile_12m=None)
        prepare_candidate_scores(missing, "bull", settings=rs_settings)
        self.assertIn("relative strength", composite_score(missing, "bull", SignalRanker()))

    def test_live_relative_strength_ranks_whole_universe(self):
        from jobs.generate_signals import attach_relative_strength
        idx = pd.bdate_range("2024-01-01", periods=300)

        def hist(growth):
            close = 50.0 * np.power(growth, np.arange(300))
            return pd.DataFrame({"OPEN": close, "HIGH": close, "LOW": close, "CLOSE": close,
                                 "VOLUME": 1e6}, index=idx)

        class FakeCache:
            data = {"AAA": hist(1.002), "BBB": hist(1.001), "CCC": hist(1.0), "XLK": hist(1.001), "XLF": hist(1.0)}

            def get_ticker_history(self, t, start, end):
                return self.data.get(t)

        cands = [{"ticker": "BBB"}, {"ticker": "XLK"}]
        n = attach_relative_strength(cands, FakeCache(), ["AAA", "BBB", "CCC"], ["XLK", "XLF"],
                                     str(idx[0].date()), str(idx[-1].date()))
        self.assertEqual(n, 5)
        self.assertAlmostEqual(cands[0]["rs_percentile_12m"], 200 / 3)  # middle of 3 stocks
        self.assertEqual(cands[1]["rs_percentile_12m"], 100.0)           # best of 2 sector ETFs


class TestBacktestSecPointInTime(unittest.TestCase):
    def test_lookup_uses_only_earlier_fresh_filings(self):
        from scripts.validate_backtest_pipeline import sec_fundamentals_on
        table = {"AAA": (["2025-02-20", "2025-05-01"], [(0.8, 1.6, "2024-12-31"), (1.2, 1.1, "2025-03-31")])}
        self.assertEqual(sec_fundamentals_on(table, "AAA", "2025-02-20"), (None, None))  # filed that day: not yet known
        self.assertEqual(sec_fundamentals_on(table, "AAA", "2025-03-03"), (0.8, 1.6))
        self.assertEqual(sec_fundamentals_on(table, "AAA", "2025-05-02"), (1.2, 1.1))
        self.assertEqual(sec_fundamentals_on(table, "AAA", "2026-01-15"), (None, None))  # balance sheet > 200 days old
        self.assertEqual(sec_fundamentals_on(table, "ZZZ", "2025-05-02"), (None, None))
        no_bs = {"AAA": (["2025-02-20"], [(float("nan"), float("nan"), float("nan"))])}  # as read back from parquet
        self.assertEqual(sec_fundamentals_on(no_bs, "AAA", "2025-03-03"), (None, None))

    def test_context_only_when_fundamentals_are_scored(self):
        from scripts.validate_backtest_pipeline import apply_backtest_context
        from jobs.generate_signals import _mark_context_unavailable
        from src.pipeline_steps import SelectionSettings
        from src.scorers.context_scorer import ContextScorer
        table = {"AAA": (["2025-02-20"], [(3.0, 0.8, "2024-12-31")])}
        sig = {"ticker": "AAA", "entry_price": 50.0}
        apply_backtest_context(sig, SelectionSettings("technical", "gate", ("analyst", "news")), table,
                               ContextScorer(), "2025-03-03", _mark_context_unavailable)
        self.assertFalse(sig["context_available"])
        apply_backtest_context(sig, SelectionSettings("technical", "gate", ("analyst", "fundamental", "news")), table,
                               ContextScorer(), "2025-03-03", _mark_context_unavailable)
        self.assertTrue(sig["context_available"])
        self.assertEqual((sig["context_score"], sig["context_max_points"], sig["de_ratio"]), (0.0, 20.0, 3.0))



class TestWeek52HighDecision(unittest.TestCase):
    def test_52_week_high_is_switched_off(self):
        # Backtest decision 2026-10-10 (CLAUDE.md "52-Week High decision"): re-enabling needs a new backtest.
        from jobs.generate_signals import REGIME_STRATEGY_MAP
        for regime, active in REGIME_STRATEGY_MAP.items():
            self.assertNotIn("52-Week High", active, regime)
        self.assertIn("Trend Following", REGIME_STRATEGY_MAP["bull"])

    def test_screening_features_match_strategy_windows(self):
        from scripts.validate_backtest_pipeline import _w52_features, w52_breakout, w52_original_rules
        n = 300
        close = np.linspace(50.0, 80.0, n)
        frame = pd.DataFrame({
            "CLOSE": close, "HIGH": close * 1.01, "VOLUME": np.full(n, 1e6),
            "RSI_14": np.full(n, 60.0), "ADX_14": np.full(n, 25.0),
        }, index=pd.bdate_range("2024-01-01", periods=n))
        frame.iloc[-1, frame.columns.get_loc("VOLUME")] = 2.6e6  # breakout-day volume
        f = _w52_features(frame, n - 1)
        self.assertTrue(f["new_52w_close"])                        # highest close of the last 252 sessions
        self.assertAlmostEqual(f["volume_ratio"], 2.6e6 / ((19 * 1e6 + 2.6e6) / 20))
        self.assertAlmostEqual(f["pct_vs_52w"], (1 / 1.01 - 1) * 100)  # close vs intraday 52-week high
        sig = {"_loc": n - 1}
        self.assertTrue(w52_breakout(sig, frame) and w52_original_rules(sig, frame))
        frame.iloc[-1, frame.columns.get_loc("CLOSE")] = close[-2] * 0.99  # pulls back: no new closing high
        self.assertFalse(_w52_features(frame, n - 1)["new_52w_close"])
        self.assertFalse(w52_breakout(sig, frame))



class TestNegativeEquityDisplay(unittest.TestCase):
    def test_flag_from_sec_equity(self):
        from src.providers.context.sec_fundamentals import SecFundamentals
        self.assertTrue(SecFundamentals(total_equity=-5.9e9).negative_equity)
        self.assertFalse(SecFundamentals(total_equity=7e6).negative_equity)
        self.assertIsNone(SecFundamentals().negative_equity)

    def test_negative_equity_never_takes_yahoo_de(self):
        from unittest.mock import MagicMock, patch
        from src.providers.context.metadata_provider import MetadataProvider
        from src.providers.context.sec_fundamentals import SecFundamentals
        sec = MagicMock()
        sec.available = True
        sec.fundamentals.return_value = SecFundamentals(eps_ttm=4.0, current_ratio=0.8, debt_to_equity=None,
                                                        total_debt=70e9, total_equity=-5.9e9, balance_sheet_date="2026-06-30")
        fake = MagicMock()
        fake.info = {"debtToEquity": 250.0, "trailingPE": 30.0}
        with patch("yfinance.Ticker", return_value=fake):
            f = MetadataProvider(sec_provider=sec).get_fundamentals("NEGQ", price=120.0)
        self.assertTrue(f.negative_equity)
        self.assertIsNone(f.debt_to_equity)  # Yahoo's 2.5 must not stand in for an undefined ratio
        self.assertEqual(f.trailing_pe, 30.0)  # 120 / 4

    def test_yahoo_negative_de_means_negative_equity(self):
        from unittest.mock import MagicMock, patch
        from src.providers.context.metadata_provider import MetadataProvider
        from src.providers.context.sec_fundamentals import SecFundamentalsProvider
        fake = MagicMock()
        fake.info = {"debtToEquity": -310.0, "currentRatio": 1.1}
        with patch("yfinance.Ticker", return_value=fake):
            f = MetadataProvider(sec_provider=SecFundamentalsProvider(user_agent="")).get_fundamentals("NEGY", price=50.0)
        self.assertTrue(f.negative_equity)
        self.assertIsNone(f.debt_to_equity)

    def test_display_fields_carry_flag(self):
        from jobs.generate_signals import _fundamentals_fields
        from src.providers.base import FundamentalContext, DataQuality
        out = _fundamentals_fields(FundamentalContext(current_ratio=0.65, quality=DataQuality.VALID, source="sec",
                                                      negative_equity=True))
        self.assertIs(out["negative_equity"], True)
        self.assertIsNone(out["de_ratio"])


class TestTradingDayBlackout(unittest.TestCase):
    def test_trading_sessions_between(self):
        from src.utils.market_date import trading_sessions_between as sessions
        self.assertEqual(sessions(date(2026, 10, 9), date(2026, 10, 14)), 3)   # Fri -> Wed: Mon, Tue, Wed
        self.assertEqual(sessions(date(2026, 10, 9), date(2026, 10, 16)), 5)   # 7 calendar days
        self.assertEqual(sessions(date(2026, 11, 24), date(2026, 11, 30)), 3)  # Thanksgiving closed
        self.assertEqual(sessions(date(2027, 3, 24), date(2027, 3, 29)), 2)    # Good Friday closed
        self.assertEqual(sessions(date(2026, 7, 2), date(2026, 7, 6)), 1)      # July 4 observed Friday 3rd
        self.assertEqual(sessions(date(2026, 10, 9), date(2026, 10, 9)), 0)

    def test_blackout_counts_trading_days(self):
        from src.filters.earnings_filter import earnings_risk_filter
        cal = {"ZZZ": {"next_earnings_date": "2026-10-16", "last_earnings_date": "2026-07-30"}}
        res = earnings_risk_filter("ZZZ", date(2026, 10, 9), "trend_following", cal)
        self.assertFalse(res["pass"])                 # 5 sessions <= 5-day blackout
        self.assertEqual(res["days_to_earnings"], 7)  # calendar days, as displayed
        self.assertIn("5 trading days", res["reason"])


class TestScanRuntimeFixes(unittest.TestCase):
    def test_pead_uses_preloaded_calendar_only(self):
        from unittest.mock import patch
        from jobs.strategies.pead import get_last_earnings_date
        cal = {"AAA": {"next_earnings_date": "2026-11-01", "last_earnings_date": None},
               "BBB": {"last_earnings_date": "2026-10-06"}}
        with patch("src.utils.earnings_cache.get_ticker_earnings", side_effect=AssertionError("per-ticker lookup")) as fallback:
            self.assertIsNone(get_last_earnings_date("AAA", as_of_date=date(2026, 10, 9), earnings_calendar=cal))
            self.assertEqual(get_last_earnings_date("BBB", as_of_date=date(2026, 10, 9), earnings_calendar=cal), date(2026, 10, 6))
            fallback.assert_not_called()

    def test_refresh_cache_chunking_and_pause(self):
        from unittest.mock import patch
        from src.data import cache_manager as cm
        mgr = cm.CacheManager.__new__(cm.CacheManager)
        calls = []
        with patch.object(cm.CacheManager, "download_batch_with_retry", lambda self, t, s, e: calls.append(len(t)) or pd.DataFrame()), \
             patch.object(cm.time, "sleep") as sleep:
            mgr.refresh_cache([f"T{i}" for i in range(450)], "2026-10-01", "2026-10-09")
        self.assertEqual(calls, [200, 200, 50])
        self.assertTrue(all(c.args[0] == cm.REFRESH_CHUNK_PAUSE_S for c in sleep.call_args_list))



class TestWalkForwardEvidenceBook(unittest.TestCase):
    def test_matches_reaggregating_every_day(self):
        import random
        from src.strategy_evidence import WalkForwardEvidenceBook, aggregate_trades, evidence_from_aggregate
        rng = random.Random(7)
        days = [f"2025-{m:02d}-{d:02d}" for m in range(1, 7) for d in range(1, 29)]
        book, closed = WalkForwardEvidenceBook(), []
        strategies = ("Trend Following", "Pullback Recovery", "Cross-Sectional Momentum")
        for i, day in enumerate(days):
            batch = {k: evidence_from_aggregate(k, v, source="walk_forward", as_of=day)
                     for k, v in aggregate_trades([t for t in closed if t["exit_date"] < day]).items()}
            incremental = book.evidence_as_of(day)
            self.assertEqual(set(batch), set(incremental), day)
            for k in batch:
                self.assertEqual(batch[k], incremental[k], (day, k))
            for _ in range(rng.randint(0, 4)):  # trades opened today exit 1-30 days later
                exit_day = days[min(len(days) - 1, i + rng.randint(1, 30))]
                trade = {"strategy": rng.choice(strategies), "net_return_pct": rng.uniform(-8, 8),
                         "outcome": rng.choice(["stop", "expired", "t1", "t2", "t3"]),
                         "has_t2": rng.random() < 0.8, "has_t3": rng.random() < 0.5, "exit_date": exit_day}
                closed.append(trade)
                book.add(trade)


if __name__ == "__main__":
    unittest.main()
