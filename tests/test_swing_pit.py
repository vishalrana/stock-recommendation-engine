"""
Swing engine — point-in-time inputs (no placeholders, no look-ahead)
====================================================================
1. No literal sleeve inputs: the research and live drivers contain none of the old placeholders,
   and on a fixture date the inputs (C6 percentile, beta, EAR/AVOL percentiles, catalyst flags,
   insider cluster values) vary across tickers.
2. Truncation look-ahead harness: for every sleeve, the signals for session t computed from data
   cut at t (bars and filings known by t) equal those computed from the full history.
3. G0 really gates: earnings inside t+1..t+5, an event lock and an open signal each block a signal.
4. The placebo draws other eligible stocks (never the signal's own ticker) and is deterministic.
"""

import os
import re
import sys
import unittest
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from src.providers.context.sec_filings import Filing  # noqa: E402
from src.swing.engine import (  # noqa: E402
    SwingPlacebo, build_context, build_signal, catalyst_filings, e_events, qualifying_purchases, run_book,
)

N_TICKERS, N_BARS = 24, 900


def fixture(seed=7):
    """Synthetic panel: total-return OHLCV for 24 tickers + SPY + XLK, 8-K filings and insider purchases."""
    rng = np.random.default_rng(seed)
    from src.utils.market_date import is_nyse_session
    idx = pd.DatetimeIndex([d for d in pd.bdate_range("2019-01-02", periods=int(N_BARS * 1.05))
                            if is_nyse_session(d.date())][:N_BARS])          # real NYSE sessions
    mkt = rng.normal(0.0004, 0.01, N_BARS)
    spy_c = 300 * np.exp(np.cumsum(mkt))
    spy = pd.DataFrame({"TR_OPEN": spy_c * (1 + rng.normal(0, 0.002, N_BARS)), "TR_CLOSE": spy_c}, index=idx)
    etf = pd.DataFrame({"XLK": 100 * np.exp(np.cumsum(mkt + rng.normal(0, 0.005, N_BARS)))}, index=idx)
    frames, filings, sectors = {}, {}, {}
    rows = []
    for k in range(N_TICKERS):
        t = f"S{k:02d}"
        beta = 0.5 + k / N_TICKERS * 1.5
        r = beta * mkt + rng.normal(0.0002 * (k % 5), 0.018, N_BARS)
        gaps = rng.random(N_BARS) < 0.01
        r[gaps] += 0.08                                       # occasional catalyst gaps
        c = 40 * np.exp(np.cumsum(r))
        o = c / np.exp(r) * np.where(gaps, 1.07, 1 + rng.normal(0, 0.003, N_BARS))
        h = np.maximum(o, c) * (1 + rng.uniform(0, 0.01, N_BARS))
        l = np.minimum(o, c) * (1 - rng.uniform(0, 0.01, N_BARS))
        v = rng.integers(400_000, 1_200_000, N_BARS).astype(float) * np.where(gaps, 5.0, 1.0)
        frames[t] = pd.DataFrame({"TR_OPEN": o, "TR_HIGH": h, "TR_LOW": l, "TR_CLOSE": c, "CLOSE": c, "UNADJ_CLOSE": c,
                                  "VOLUME": v}, index=idx)
        sectors[t] = "XLK" if k % 2 else None
        fl = []
        for q in range(0, N_BARS - 5, 21):                    # frequent 8-K 2.02s: E needs >= 50 reference events
            d = idx[q + (k % 7)]
            fl.append(Filing("8-K", datetime(d.year, d.month, d.day, 7, 30), ("2.02", "9.01")))
        for i in np.flatnonzero(gaps):                        # a current report around each gap
            d = idx[i]
            fl.append(Filing("8-K", datetime(d.year, d.month, d.day, 8, 0), ("8.01",) if k % 3 else ("3.02",)))
        if k == 5:
            d = idx[600]
            fl.append(Filing("DEFM14A", datetime(d.year, d.month, d.day, 17, 0), ()))
        filings[t] = sorted(fl, key=lambda f: f.accepted)
        for j in range(3):                                    # insider purchases: clusters for some tickers
            fd = idx[300 + 40 * j + k].date()
            for owner in range(2 if k % 2 == 0 else 1):
                rows.append({"accession": f"{t}-{j}-{owner}", "filing_date": fd, "trans_date": fd, "issuer_cik": 1000 + k,
                             "symbol": t, "owner_cik": 9000 + 10 * k + owner, "relationship": "Director",
                             "is_director": True, "is_officer": False, "shares": 2000.0 + 500 * k, "price": 30.0 + k,
                             "aff10b5one": None, "value": (2000.0 + 500 * k) * (30.0 + k)})
    insider = qualifying_purchases(pd.DataFrame(rows), lambda cik, sym, fd: sym)
    return frames, spy, etf, sectors, filings, insider, idx


def ctx_for(frames, spy, etf, sectors, filings, insider, upto=None):
    full_reactions = None
    if upto is not None:
        from src.providers.context.sec_filings import earnings_events
        full_reactions = {t: sorted({e.reaction for e in earnings_events(f)}) for t, f in filings.items()}
        cut = pd.Timestamp(upto)
        frames = {t: f.loc[:cut] for t, f in frames.items()}
        spy = spy.loc[:cut]
        etf = etf.loc[:cut]
        from src.providers.context.sec_filings import known_by
        filings = {t: known_by(f, upto) for t, f in filings.items()}
        insider = insider[pd.to_datetime(insider["filing_date"]) <= cut]
    ctx = build_context("u1", frames, spy, etf, sectors, filings, {}, u1_tickers=list(frames), with_c6=True, insider=insider)
    if full_reactions is not None:
        # The one pre-registered exception to "data cut at t": backtest earnings reaction sessions are
        # assumed known 5 sessions ahead (CLAUDE.md, Events). Nothing later than t+5 is visible.
        horizon = ctx.cal.shift(upto, 5)
        ctx.earnings_known = lambda t, d: ([x for x in full_reactions[t] if x <= horizon], False)
    return ctx


class TestNoPlaceholders(unittest.TestCase):
    PLACEHOLDERS = [
        r"c6_proxy", r"c6_pct\s*=\s*0\.95", r"ear_pct\s*=\s*0\.85", r"avol_pct\s*=\s*0\.85",
        r"has_8k_or_6k\s*=\s*True", r"has_neg8k\s*=\s*False", r"is_g0\s*=\s*True", r"150000\.0",
        r"beta\s*or\s*1\.0", r"beta\s*=\s*1\.0", r"rng\.normal\(0\.0,\s*1\.5\)",
    ]

    def test_drivers_have_no_literal_inputs(self):
        for rel in ("scripts/research_swing.py", "jobs/generate_swing_signals.py", "src/swing/engine.py"):
            src = open(os.path.join(PROJECT_ROOT, rel), encoding="utf-8").read()
            for pat in self.PLACEHOLDERS:
                self.assertIsNone(re.search(pat, src), f"{rel} contains placeholder pattern {pat}")

    def test_inputs_vary_across_tickers(self):
        frames, spy, etf, sectors, filings, insider, idx = fixture()
        ctx = ctx_for(frames, spy, etf, sectors, filings, insider)
        d = idx[700]
        c6 = ctx.c6pct.loc[d].dropna()
        beta = ctx.beta.loc[d].dropna()
        self.assertGreater(c6.nunique(), 10, "C6 percentile is constant across tickers")
        self.assertGreater(beta.nunique(), 10, "beta is constant across tickers")
        self.assertGreater(beta.max() - beta.min(), 0.5)
        ev = [e for e in e_events(ctx) if e.get("ear_pct") is not None]
        self.assertGreater(len({round(e["ear_pct"], 4) for e in ev}), 10, "EAR percentiles are constant")
        self.assertGreater(len({round(e["avol_pct"], 4) for e in ev}), 10, "AVOL percentiles are constant")
        flags = {catalyst_filings(ctx.tickers[t], ctx, d.date()) for t in ctx.tickers for d in idx[300:320]}
        self.assertGreater(len(flags), 1, "catalyst flags are constant")
        self.assertGreater(insider.groupby("ticker")["value"].sum().nunique(), 5, "insider values are constant")


class TestTruncationLookahead(unittest.TestCase):
    """Signals at t from data cut at t equal signals at t from the full history."""

    def test_every_sleeve(self):
        frames, spy, etf, sectors, filings, insider, idx = fixture()
        full = ctx_for(frames, spy, etf, sectors, filings, insider)
        checked = 0
        for k in (420, 520, 640, 760):
            d = idx[k].date()
            cut = ctx_for(frames, spy, etf, sectors, filings, insider, upto=d)
            for sleeve in ("T", "E", "N", "P", "I"):
                a = run_book(full, sleeve, d, d)["signals"]
                b = run_book(cut, sleeve, d, d)["signals"]
                key = lambda s: (s.ticker, s.signal_date, round(s.stop_loss, 6), s.target and round(s.target, 6),
                                 s.c6_pct and round(s.c6_pct, 9), s.ear_pct, s.avol_pct, s.beta and round(s.beta, 9))
                self.assertEqual(sorted(map(key, a)), sorted(map(key, b)), f"{sleeve} differs at {d}")
                checked += len(a)
            # raw inputs too, ticker by ticker
            for t in full.tickers:
                self.assertTrue(np.allclose(full.c6pct.loc[pd.Timestamp(d), t], cut.c6pct.loc[pd.Timestamp(d), t], equal_nan=True))
                self.assertTrue(np.allclose(full.beta.loc[pd.Timestamp(d), t], cut.beta.loc[pd.Timestamp(d), t], equal_nan=True))
        self.assertGreater(checked, 0, "fixture produced no signals to compare")


class TestG0Gates(unittest.TestCase):
    def test_earnings_lock_and_open_signal_block(self):
        frames, spy, etf, sectors, filings, insider, idx = fixture()
        ctx = ctx_for(frames, spy, etf, sectors, filings, insider)
        r = run_book(ctx, "P", idx[300].date(), idx[880].date())
        f = r["funnel"]
        self.assertGreater(f.get("g0_earnings", 0), 0, "earnings within 5 sessions never blocked")
        self.assertGreater(f.get("g0_in", 0) + f.get("g0_open", 0), 0, "open signal / cooldown never blocked")
        locked = ctx.tickers["S05"].locked_array()
        self.assertTrue(locked[600:700].any(), "DEFM14A lock missing")
        for s in r["signals"]:                                # no signal while its ticker is locked or before a report
            a = ctx.tickers[s.ticker]
            i = a.pos(s.signal_date)
            self.assertFalse(a.locked(i))
            nxt = [x for x in a.reactions if x > s.signal_date][:1]
            if nxt:
                self.assertGreater(ctx.cal.shift(s.signal_date, 5), s.signal_date)
                self.assertFalse(s.signal_date < nxt[0] <= ctx.cal.shift(s.signal_date, 5))


class TestPlacebo(unittest.TestCase):
    def test_random_other_stocks_deterministic(self):
        frames, spy, etf, sectors, filings, insider, idx = fixture()
        ctx = ctx_for(frames, spy, etf, sectors, filings, insider)
        trades = run_book(ctx, "P", idx[300].date(), idx[850].date())["trades"][:40]
        self.assertGreater(len(trades), 5)
        a = SwingPlacebo(ctx).run("P", trades, n_runs=8)
        b = SwingPlacebo(ctx).run("P", trades, n_runs=8)
        self.assertEqual(a["means"], b["means"])
        own = [tr.beta_adj_pct for tr in trades]
        self.assertNotEqual([round(x, 6) for x in a["per_trade_median"]], [round(x, 6) for x in own],
                            "placebo reproduces the book's own returns")
        self.assertGreater(a["coverage"], 0.9)


if __name__ == "__main__":
    unittest.main()
