"""
Profitability Program — Research Tooling Regression Suite
=========================================================
Locks in the measurement tools of the pre-registered profitability program (CLAUDE.md):
1. Trials registry: every trial appends one JSON record that reads back intact.
2. Fast exit engine: identical to evaluate_signal_outcome on random paths (gaps, ratchets, censoring).
3. Random-stock placebo: deterministic per seed; never draws an ineligible, event-locked or
   not-yet-listed stock.
4. Corwin-Schultz spread: recovers a known synthetic spread; point-in-time.
5. Cost scenarios and break-even cost per side.
6. Multiple testing: Holm adjusted p-values; the paired bootstrap and StepM do not reject a null
   and do reject a strong series.
7. Survivorship gauge on a synthetic membership table.
"""

import json
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from src.outcome.outcome_calculator import evaluate_signal_outcome
from src.research.trials import record_trial, read_trials
from src.research.placebo import fast_outcome, PlaceboEngine
from src.research.costs import corwin_schultz_daily, rolling_half_spread_pct, cost_scenarios
from src.research.stats import holm, paired_month_bootstrap, newey_west_t, monthly_series, percentile_of
from src.research.survivorship import survivorship_gauge

try:
    import arch  # noqa: F401
    HAVE_ARCH = True
except Exception:
    HAVE_ARCH = False


class TestTrialsRegistry(unittest.TestCase):
    def test_record_and_read_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trials.jsonl")
            record_trial("production", "phase2", {"a": 1}, 120, -0.31234, [-0.9, 0.2], -0.2, path=path, sha="abc")
            record_trial("event_lock", "phase2", {"b": 2}, 100, None, None, None, smoke=True, path=path, sha="abc")
            rows = read_trials(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["name"], "production")
            self.assertEqual(rows[0]["mean_beta_adj"], -0.3123)
            self.assertEqual(rows[0]["git_sha"], "abc")
            self.assertFalse(rows[0]["smoke"])
            self.assertTrue(rows[1]["smoke"])
            for k in ("phase", "params", "n_ideas", "ci95", "late_mean", "series_path", "recorded_at"):
                self.assertIn(k, rows[0])


def _random_path(rng, n, start=100.0, vol=0.025, gap_prob=0.08, nan_prob=0.03):
    rows, px = [], start
    for _ in range(n):
        gap = rng.normal(0, 0.06) if rng.random() < gap_prob else rng.normal(0, 0.005)
        o = px * (1 + gap)
        c = o * (1 + rng.normal(0, vol))
        h = max(o, c) * (1 + abs(rng.normal(0, vol / 2)))
        l = min(o, c) * (1 - abs(rng.normal(0, vol / 2)))
        if rng.random() < nan_prob:
            o = h = l = c = np.nan
        else:
            px = c
        rows.append((o, h, l, c))
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame(rows, index=idx, columns=["OPEN", "HIGH", "LOW", "CLOSE"])


class TestFastOutcomeEquivalence(unittest.TestCase):
    def test_matches_evaluate_signal_outcome(self):
        rng = np.random.default_rng(42)
        checked = closed = 0
        reasons = set()
        for case in range(1500):
            n = int(rng.integers(3, 40))
            df = _random_path(rng, n)
            entry = float(df["OPEN"].dropna().iloc[0]) if df["OPEN"].notna().any() else 100.0
            stop = entry * (1 - rng.uniform(0.01, 0.12))
            t1 = entry * (1 + rng.uniform(0.01, 0.10))
            n_t = int(rng.integers(1, 4))
            t2 = t1 * (1 + rng.uniform(0.01, 0.08)) if n_t >= 2 else None
            t3 = t2 * (1 + rng.uniform(0.01, 0.08)) if n_t >= 3 else None
            hold = int(rng.integers(1, 30))
            ref = evaluate_signal_outcome(df, entry, stop, t1, t2, t3, max_holding_days=hold, ambiguity_policy="STOP_FIRST")
            got = fast_outcome(df["OPEN"].to_numpy(), df["HIGH"].to_numpy(), df["LOW"].to_numpy(), df["CLOSE"].to_numpy(),
                               0, entry, stop, t1, t2, t3, hold)
            checked += 1
            if ref is None:
                self.assertIsNone(got, f"case {case}: reference censored, fast engine closed")
                continue
            self.assertIsNotNone(got, f"case {case}: fast engine censored, reference closed")
            closed += 1
            ret, reason, hd, exit_i = got
            self.assertAlmostEqual(ret, ref["realized_return_pct"], places=4, msg=f"case {case}")
            self.assertEqual(reason, ref["exit_reason"], f"case {case}")
            self.assertEqual(hd, ref["holding_days"], f"case {case}")
            self.assertEqual(df.index[exit_i].date().isoformat(), ref["outcome_date"], f"case {case}")
            reasons.add(reason)
        self.assertGreater(closed, 1000)
        self.assertEqual(reasons, {"stop", "target", "expiry"})


def _synthetic_universe(n_tickers=12, n_bars=420, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n_bars)
    frames, betas = {}, {}
    for k in range(n_tickers):
        t = f"S{k:02d}"
        r = rng.normal(0.0004, 0.02, n_bars)
        close = 50 * np.exp(np.cumsum(r))
        o = close * (1 + rng.normal(0, 0.004, n_bars))
        h = np.maximum(o, close) * 1.01
        l = np.minimum(o, close) * 0.99
        f = pd.DataFrame({"OPEN": o, "HIGH": h, "LOW": l, "CLOSE": close, "ATR_14": close * 0.02}, index=idx)
        liquid = np.arange(n_bars) >= 252
        if k == 1:
            liquid[300:340] = False          # illiquid stretch
        f["_LIQUID"] = liquid
        if k == 2:
            f = f.iloc[200:]                 # listed later: no bars before its listing
            f["_LIQUID"] = np.arange(len(f)) >= 252
        frames[t] = f
        betas[t] = pd.Series(1.0, index=f.index)
    spy = pd.DataFrame({"OPEN": 400 * np.ones(n_bars), "CLOSE": 400 * np.ones(n_bars)}, index=idx)
    return frames, spy, betas, idx


class TestRandomStockPlacebo(unittest.TestCase):
    def setUp(self):
        self.frames, self.spy, self.betas, self.idx = _synthetic_universe()
        self.locked = lambda t, d: t == "S03" and d >= pd.Timestamp("2024-03-01")

    def _engine(self, seed=20261011):
        return PlaceboEngine(self.frames, self.spy, self.betas, lambda s: 10, event_locked=self.locked, seed=seed)

    def _trade(self, date):
        close = float(self.frames["S00"].loc[date, "CLOSE"])
        return {"ticker": "S00", "signal_date": date.date().isoformat(), "strategy": "Trend Following",
                "entry_signal_close": close, "stop_loss": close * 0.95, "target_1": close * 1.05,
                "target_2": close * 1.08, "target_3": None, "beta_adjusted_pct": 0.5}

    def test_deterministic_with_seed(self):
        trade = self._trade(self.idx[320])
        a = self._engine().draws_with_tickers(trade, float(self.frames["S00"].loc[self.idx[320], "ATR_14"]), 50)
        b = self._engine().draws_with_tickers(trade, float(self.frames["S00"].loc[self.idx[320], "ATR_14"]), 50)
        c = self._engine(seed=7).draws_with_tickers(trade, float(self.frames["S00"].loc[self.idx[320], "ATR_14"]), 50)
        self.assertEqual(a, b)
        self.assertNotEqual([t for t, _ in a], [t for t, _ in c])

    def test_never_draws_ineligible_or_future_listed(self):
        eng = self._engine()
        seen = set()
        for loc in (260, 310, 330, 360, 400):
            d = self.idx[loc]
            trade = self._trade(d)
            draws = eng.draws_with_tickers(trade, float(self.frames["S00"].loc[d, "ATR_14"]), 60)
            self.assertIsNotNone(draws)
            for t, _ in draws:
                seen.add(t)
                self.assertTrue(eng.is_eligible(t, d), f"{t} drawn on {d.date()} but ineligible")
                self.assertIn(d, self.frames[t].index, f"{t} drawn before it was listed")
                self.assertTrue(bool(self.frames[t].loc[d, "_LIQUID"]))
                self.assertFalse(self.locked(t, d), f"{t} is event-locked on {d.date()}")
            if 300 <= loc < 340:
                self.assertNotIn("S01", {t for t, _ in draws})
        # S02 is listed 200 bars late and needs 252 bars of history: never eligible in this window.
        self.assertNotIn("S02", seen)

    def test_book_percentile_and_paired_diff(self):
        eng = self._engine()
        trades = [dict(self._trade(self.idx[loc]), ticker=f"S{loc % 10:02d}") for loc in range(260, 400, 3)]
        trades = [t for t in trades if t["ticker"] not in ("S02",)]
        for t in trades:
            t["entry_signal_close"] = float(self.frames[t["ticker"]].loc[t["signal_date"], "CLOSE"])
            t["stop_loss"] = t["entry_signal_close"] * 0.95
            t["target_1"], t["target_2"] = t["entry_signal_close"] * 1.05, t["entry_signal_close"] * 1.08
        atr = lambda t: float(self.frames[t["ticker"]].loc[t["signal_date"], "ATR_14"])
        out = eng.evaluate_book(trades, atr, 20)
        self.assertEqual(out["placebo_draws"], 20)
        self.assertIn("placebo_percentile", out)
        self.assertTrue(0.0 <= out["placebo_percentile"] <= 100.0)
        self.assertIn("paired_diff_vs_placebo_median", out)


class TestCorwinSchultz(unittest.TestCase):
    def _bars(self, spread, days=400, steps=390, sigma=0.01, seed=1):
        rng = np.random.default_rng(seed)
        mid = 100 * np.exp(np.cumsum(rng.normal(0, sigma / np.sqrt(steps), days * steps))).reshape(days, steps)
        return pd.Series(mid.max(1) * (1 + spread / 2)), pd.Series(mid.min(1) * (1 - spread / 2))

    def test_recovers_known_spread(self):
        for spread, tol in ((0.01, 0.20), (0.02, 0.15)):
            h, l = self._bars(spread)
            est = float(corwin_schultz_daily(h, l).mean())
            self.assertLess(abs(est - spread) / spread, tol, f"spread {spread}: estimate {est}")
        h1, l1 = self._bars(0.01)
        h2, l2 = self._bars(0.02)
        self.assertLess(rolling_half_spread_pct(h1, l1).iloc[-1], rolling_half_spread_pct(h2, l2).iloc[-1])

    def test_overnight_adjustment(self):
        rng = np.random.default_rng(1)
        days, steps, spread = 300, 390, 0.01
        mid = (100 * np.exp(np.cumsum(rng.normal(0, 0.01 / np.sqrt(steps), days * steps)))).reshape(days, steps)
        h, l = pd.Series(mid.max(1) * (1 + spread / 2)), pd.Series(mid.min(1) * (1 - spread / 2))
        c = pd.Series(mid[:, -1])
        base = float(corwin_schultz_daily(h, l).mean())
        # Without gaps the adjustment changes nothing.
        pd.testing.assert_series_equal(corwin_schultz_daily(h, l, close=c), corwin_schultz_daily(h, l))
        # 4% overnight gaps on ~20% of days widen two-day ranges and bias the raw estimate down;
        # the adjustment moves it back toward the gap-free estimate.
        jumps = np.where(rng.random(days) < 0.2, rng.choice([0.96, 1.04], days), 1.0)
        jumps[0] = 1.0
        g = pd.Series(np.cumprod(jumps))
        raw = float(corwin_schultz_daily(h * g, l * g).mean())
        adj = float(corwin_schultz_daily(h * g, l * g, close=c * g).mean())
        self.assertLess(raw, base)
        self.assertLess(abs(adj - base), abs(raw - base))

    def test_point_in_time(self):
        h, l = self._bars(0.01, days=120)
        base = rolling_half_spread_pct(h, l)
        h2, l2 = h.copy(), l.copy()
        h2.iloc[80:] *= 1.5
        l2.iloc[80:] *= 0.6
        changed = rolling_half_spread_pct(h2, l2)
        pd.testing.assert_series_equal(base.iloc[:80], changed.iloc[:80])


class TestCostScenarios(unittest.TestCase):
    def test_scenarios_and_break_even(self):
        trades = [{"beta_adjusted_pct": v, "half_spread_pct": 0.05, "signal_date": f"2024-{m:02d}-15"}
                  for m, v in zip(range(1, 13), [0.3, -0.1, 0.5, 0.0, 0.2, 0.1, -0.2, 0.4, 0.1, 0.0, 0.3, 0.2])]
        out = cost_scenarios(trades)
        mean_a = np.mean([t["beta_adjusted_pct"] for t in trades])
        self.assertAlmostEqual(out["a_10bps"]["mean_beta_adjusted_pct"], round(mean_a, 4))
        self.assertAlmostEqual(out["b_10bps_plus_half_spread"]["mean_beta_adjusted_pct"], round(mean_a - 0.10, 4))
        self.assertAlmostEqual(out["c_twice_b"]["mean_beta_adjusted_pct"], round(mean_a + 0.20 - 0.60, 4))
        # Break-even: (mean + 0.20) / 2 percent per side, in bps
        self.assertAlmostEqual(out["break_even_bps_per_side"], round((mean_a + 0.20) / 2 * 100, 2))


class TestMultipleTesting(unittest.TestCase):
    def test_holm(self):
        adj = holm([0.01, 0.04, 0.03, 0.005])
        np.testing.assert_allclose(adj, [0.03, 0.06, 0.06, 0.02])
        self.assertEqual(holm([]), [])
        self.assertEqual(holm([0.9, 0.8]), [1.0, 1.0])
        self.assertEqual(holm([0.2, 0.3]), [0.4, 0.4])

    def _series(self, mean_shift, seed):
        rng = np.random.default_rng(seed)
        months = [f"{2022 + i // 12}-{i % 12 + 1:02d}" for i in range(48)]
        a_m, a_v, b_m, b_v = [], [], [], []
        for m in months:
            for _ in range(20):
                a_m.append(m)
                a_v.append(rng.normal(mean_shift, 3.0))
                b_m.append(m)
                b_v.append(rng.normal(0.0, 3.0))
        return a_m, a_v, b_m, b_v

    def test_paired_bootstrap_null_and_strong(self):
        null = paired_month_bootstrap(*self._series(0.0, 5))
        self.assertGreater(null["p_gt0"], 0.05)
        self.assertLessEqual(null["ci95"][0], 0.0)
        self.assertGreaterEqual(null["ci95"][1], 0.0)
        strong = paired_month_bootstrap(*self._series(1.0, 5))
        self.assertLess(strong["p_gt0"], 0.01)
        self.assertGreater(strong["ci95"][0], 0.0)
        self.assertLess(holm([strong["p_gt0"], null["p_gt0"]])[0], 0.05)

    def test_newey_west_t(self):
        rng = np.random.default_rng(9)
        self.assertLess(abs(newey_west_t(rng.normal(0, 1, 200))), 2.5)
        self.assertGreater(newey_west_t(rng.normal(0.5, 1, 200)), 4.0)

    def test_percentile_of(self):
        self.assertEqual(percentile_of(5.0, [1, 2, 3, 4]), 100.0)
        self.assertEqual(percentile_of(0.0, [1, 2, 3, 4]), 0.0)
        self.assertEqual(percentile_of(2.0, [1, 2, 3, 4]), 37.5)

    @unittest.skipUnless(HAVE_ARCH, "arch not installed (requirements-research.txt)")
    def test_stepm_null_and_strong(self):
        from src.research.stats import stepm_superior
        months = [f"{2022 + i // 12}-{i % 12 + 1:02d}" for i in range(48)]
        # Null: variants equal production in expectation. Familywise false positives stay near 5%.
        false_pos = 0
        for s in range(20):
            r = np.random.default_rng(100 + s)
            p = {m: float(r.normal(0, 1)) for m in months}
            nl = {f"v{k}": {m: p[m] + float(r.normal(0, 1)) for m in months} for k in range(3)}
            false_pos += bool(stepm_superior(p, nl, reps=500)["superior"])
        self.assertLessEqual(false_pos, 2)
        rng = np.random.default_rng(4)
        prod = {m: float(rng.normal(0, 1)) for m in months}
        null = {f"v{k}": {m: prod[m] + float(rng.normal(0, 1)) for m in months} for k in range(3)}
        strong = dict(null)
        strong["good"] = {m: prod[m] + 1.5 + float(rng.normal(0, 0.5)) for m in months}
        self.assertIn("good", stepm_superior(prod, strong)["superior"])


class TestMonthlySeries(unittest.TestCase):
    def test_monthly_means(self):
        s = monthly_series(["2024-01", "2024-01", "2024-02"], [1.0, 3.0, float("nan")])
        self.assertEqual(s, {"2024-01": 2.0})


class TestSurvivorshipGauge(unittest.TestCase):
    def test_missing_share(self):
        sessions = pd.bdate_range("2024-01-01", periods=10)
        membership = pd.DataFrame({
            "ticker": ["AAA", "BBB", "CCC"],
            "start_date": pd.to_datetime(["2020-01-01", "2020-01-01", "2024-01-08"]),
            "end_date": pd.to_datetime([None, "2024-01-05", None]),
        })
        bar_dates = {"AAA": list(sessions), "CCC": list(sessions[5:8])}
        g = survivorship_gauge(bar_dates, sessions, membership)
        # AAA 10 days (0 missing), BBB 5 days (all missing), CCC 5 days (2 missing)
        self.assertEqual(g["member_days"], 20)
        self.assertEqual(g["missing_member_days"], 7)
        self.assertEqual(g["missing_share_pct"], 35.0)
        self.assertEqual(g["members_never_seen"], 1)

    def test_reference_csv_loads(self):
        from src.research.survivorship import load_membership
        m = load_membership()
        self.assertGreater(len(m), 1000)
        self.assertIn("BRK-B", set(m["ticker"]))
        self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT, "data", "reference", "sp500_LICENSE.txt")))


if __name__ == "__main__":
    unittest.main()
