"""
Random-stock placebo for the profitability program
==================================================
For every issued idea of a book, draw a random stock from the same date's eligible liquid
universe (the backtest's point-in-time _LIQUID flag: price >= $5, 20-day dollar volume >= $5M,
>= 252 sessions of history; not event-locked; a next bar exists for the fill). The placebo trade
uses the same entry rule (next open), the same holding limit, the idea's stop and targets
expressed in ATR multiples (so exits follow the same scale-out engine), and the same costs.

Draws are seeded per idea (seed, ticker, signal date, strategy), so a given idea gets the same
placebo draws in every book: variant books reuse production's simulations. Repeating the draw
N times (default 200) gives a distribution of placebo book means; a book's percentile in that
distribution, and the paired per-idea difference vs the placebo median, measure selection skill
beyond picking any liquid stock with the same trade plan.

fast_outcome() reimplements src.outcome.outcome_calculator.evaluate_signal_outcome on numpy
arrays (gap fills at the open, STOP_FIRST including the ratcheted stop on a target bar, 50/30/20,
60/40 and 70/30-runner scale-out plans, expiry at the close of the holding-limit bar, censoring
when bars run out). scripts/test_quant_correctness_fixes.py checks it against the original.
"""

import hashlib
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.research.stats import percentile_of

OPEN, T1, T2, T3 = 0, 1, 2, 3


def _weights(t1, t2, t3) -> Tuple[float, float, float]:
    if t1 and t2 and t3:
        return 0.50, 0.30, 0.20
    if t1 and t2:
        return 0.60, 0.40, 0.0
    return 0.70, 0.0, 0.0  # 30% runner to breakeven


def _ok(x: float) -> bool:
    return math.isfinite(x) and x > 0


def fast_outcome(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, start: int,
                 entry: float, stop: float, t1: Optional[float], t2: Optional[float] = None,
                 t3: Optional[float] = None, max_hold: int = 20,
                 stop_first: bool = True) -> Optional[Tuple[float, str, int, int]]:
    """(realized_return_pct, exit_reason, holding_days, exit_bar_index), or None if censored."""
    if not (entry > 0) or not t1:
        return None
    t1 = float(t1)
    t2 = float(t2) if t2 else None
    t3 = float(t3) if t3 else None
    w1, w2, w3 = _weights(t1, t2, t3)
    state, cur_stop, remaining, realized, hd = OPEN, float(stop), 1.0, 0.0, 0
    for i in range(start, len(c)):
        oi, hi, li, ci = float(o[i]), float(h[i]), float(l[i]), float(c[i])
        if not (_ok(oi) and _ok(hi) and _ok(li) and _ok(ci)):
            continue
        hd += 1
        at = t3 if (state == T2 and t3) else (t2 if (state == T1 and t2) else t1)
        stop_hit, exit_p = False, ci
        if cur_stop > 0 and oi <= cur_stop:
            stop_hit, exit_p = True, oi
        elif not (at > 0 and oi >= at):
            s_touch = cur_stop > 0 and li <= cur_stop
            t_touch = at > 0 and hi >= at
            if s_touch and (stop_first or not t_touch):
                stop_hit, exit_p = True, cur_stop
        if stop_hit:
            realized += remaining * (exit_p - entry) / entry * 100.0
            return round(realized, 4), "stop", hd, i
        closed_by = None
        if state == OPEN and hi >= t1:
            fill = max(t1, oi)
            realized += w1 * (fill - entry) / entry * 100.0
            remaining = round(remaining - w1, 6)
            state, cur_stop, closed_by = T1, max(cur_stop, entry), "target"
            if stop_first and remaining > 1e-6 and li <= cur_stop:
                realized += remaining * (cur_stop - entry) / entry * 100.0
                remaining, closed_by = 0.0, "stop"
        if remaining > 1e-6 and state == T1 and t2 and w2 > 0 and hi >= t2:
            fill = max(t2, oi)
            realized += w2 * (fill - entry) / entry * 100.0
            remaining = round(remaining - w2, 6)
            state, cur_stop, closed_by = T2, max(cur_stop, t1), "target"
            if stop_first and remaining > 1e-6 and li <= cur_stop:
                realized += remaining * (cur_stop - entry) / entry * 100.0
                remaining, closed_by = 0.0, "stop"
        if remaining > 1e-6 and state == T2 and t3 and w3 > 0 and hi >= t3:
            fill = max(t3, oi)
            realized += w3 * (fill - entry) / entry * 100.0
            remaining = round(remaining - w3, 6)
            state, closed_by = T3, "target"
        if remaining <= 1e-6:
            return round(realized, 4), closed_by, hd, i
        if hd >= max_hold:
            realized += remaining * (ci - entry) / entry * 100.0
            return round(realized, 4), "expiry", hd, i
    return None


def stable_seed(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


class PlaceboEngine:
    """Point-in-time random-stock placebo over the backtest's stock frames."""

    def __init__(self, stock_frames: Dict[str, pd.DataFrame], spy: pd.DataFrame, betas: Dict[str, pd.Series],
                 hold_days_for: Callable[[str], Optional[int]], cost_pct_per_side: float = 0.10,
                 event_locked: Optional[Callable[[str, pd.Timestamp], bool]] = None, seed: int = 20261011):
        self.tickers = sorted(stock_frames)
        self.ticker_pos = {t: i for i, t in enumerate(self.tickers)}
        self.seed = seed
        self.cost = cost_pct_per_side
        self.hold_days_for = hold_days_for
        self.event_locked = event_locked
        self.data = {}
        for t in self.tickers:
            f = stock_frames[t]
            self.data[t] = {
                "o": f["OPEN"].to_numpy(dtype=float), "h": f["HIGH"].to_numpy(dtype=float),
                "l": f["LOW"].to_numpy(dtype=float), "c": f["CLOSE"].to_numpy(dtype=float),
                "atr": f["ATR_14"].to_numpy(dtype=float), "index": f.index,
                "ns": np.asarray(f.index.values.astype("datetime64[ns]")).view("i8"),
                "beta": (betas[t].reindex(f.index).to_numpy(dtype=float) if betas.get(t) is not None
                         else np.full(len(f), np.nan)),
            }
        liquid = pd.DataFrame({t: stock_frames[t]["_LIQUID"].astype(bool) for t in self.tickers})
        liquid = liquid.fillna(False).astype(bool)
        mat = liquid.to_numpy()
        self.eligible = {d: np.flatnonzero(mat[i]) for i, d in enumerate(liquid.index)}
        self._eligible_sets: Dict[pd.Timestamp, set] = {}
        self.spy_open = spy["OPEN"].to_dict()
        self.spy_close = spy["CLOSE"].to_dict()
        self._cache: Dict[Tuple[str, str, str], np.ndarray] = {}

    @staticmethod
    def _pos(d: Dict[str, Any], date: pd.Timestamp) -> Optional[int]:
        """Bar position of `date` in a ticker's frame, or None when the ticker has no bar that day."""
        ns = d["ns"]
        i = int(np.searchsorted(ns, pd.Timestamp(date).value))
        return i if i < len(ns) and ns[i] == pd.Timestamp(date).value else None

    def is_eligible(self, ticker: str, date: pd.Timestamp) -> bool:
        """The eligibility rule the draws use (exposed for tests)."""
        d = self.data.get(ticker)
        p = None if d is None else self._pos(d, date)
        if p is None:
            return False
        idx = self.eligible.get(date)
        if idx is None:
            return False
        if date not in self._eligible_sets:
            self._eligible_sets[date] = set(idx.tolist())
        if self.ticker_pos[ticker] not in self._eligible_sets[date]:
            return False
        if self.event_locked is not None and self.event_locked(ticker, date):
            return False
        return p + 1 < len(d["c"]) and _ok(d["atr"][p]) and _ok(d["c"][p])

    def _simulate(self, ticker: str, date: pd.Timestamp, m_stop: float, m_targets: Sequence[Optional[float]],
                  hold: int) -> Optional[Tuple[float, str]]:
        d = self.data[ticker]
        p = self._pos(d, date)
        if p is None or p + 1 >= len(d["c"]):
            return None
        close, atr = d["c"][p], d["atr"][p]
        if not (_ok(close) and _ok(atr)):
            return None
        fill = d["o"][p + 1]
        if not _ok(fill):
            return None
        tg = [close + m * atr if m is not None else None for m in m_targets]
        res = fast_outcome(d["o"], d["h"], d["l"], d["c"], p + 1, fill, close - m_stop * atr, tg[0], tg[1], tg[2], hold)
        if res is None:
            return None
        ret, reason, _, exit_i = res
        fill_date, exit_date = d["index"][p + 1], d["index"][exit_i]
        so, sc = self.spy_open.get(fill_date), self.spy_close.get(exit_date)
        beta = float(d["beta"][p])
        if so is None or sc is None or not np.isfinite(beta):
            return None
        net = ret - 2.0 * self.cost
        return net - beta * (float(sc) / float(so) - 1.0) * 100.0, reason

    def draws_with_tickers(self, trade: Dict[str, Any], idea_atr: float, n: int) -> Optional[List[Tuple[str, float]]]:
        """n placebo (ticker, beta-adjusted return) draws for one issued idea, deterministic per idea."""
        key = (trade["ticker"], trade["signal_date"], trade["strategy"])
        date = pd.Timestamp(trade["signal_date"])
        elig = self.eligible.get(date)
        entry = float(trade["entry_signal_close"])
        if elig is None or not len(elig) or not _ok(idea_atr) or not _ok(entry):
            return None
        m_stop = (entry - float(trade["stop_loss"])) / idea_atr
        m_t = []
        for k in ("target_1", "target_2", "target_3"):
            v = trade.get(k)
            ok = v is not None and np.isfinite(float(v))
            m_t.append((float(v) - entry) / idea_atr if ok else None)
        hold = int(self.hold_days_for(trade["strategy"]) or 20)
        rng = np.random.default_rng([self.seed, stable_seed(*key)])
        out: List[Tuple[str, float]] = []
        tries = 0
        while len(out) < n and tries < n * 10:
            tries += 1
            t = self.tickers[int(elig[rng.integers(0, len(elig))])]
            if self.event_locked is not None and self.event_locked(t, date):
                continue
            sim = self._simulate(t, date, m_stop, m_t, hold)
            if sim is not None:
                out.append((t, sim[0]))
        return out if len(out) >= n else None

    def idea_draws(self, trade: Dict[str, Any], idea_atr: float, n: int) -> Optional[np.ndarray]:
        """n placebo beta-adjusted returns for one issued idea (cached by idea, shared across books)."""
        key = (trade["ticker"], trade["signal_date"], trade["strategy"])
        cached = self._cache.get(key)
        if cached is not None and len(cached) >= n:
            return cached[:n]
        draws = self.draws_with_tickers(trade, idea_atr, n)
        if draws is None:
            return None
        arr = np.asarray([r for _, r in draws])
        self._cache[key] = arr
        return arr

    def evaluate_book(self, trades: List[Dict[str, Any]], idea_atr: Callable[[Dict[str, Any]], float], n: int,
                      ci_fn=None) -> Dict[str, Any]:
        """Book percentile vs the placebo distribution and paired diff vs the per-idea placebo median."""
        rows, draws = [], []
        for t in trades:
            if t.get("beta_adjusted_pct") is None or not np.isfinite(t["beta_adjusted_pct"]):
                continue
            arr = self.idea_draws(t, idea_atr(t), n)
            if arr is None:
                continue
            rows.append(t)
            draws.append(arr)
        if len(rows) < 30:
            return {"n_ideas": len(rows), "placebo_draws": n, "note": "too few ideas"}
        mat = np.vstack(draws)                      # ideas x draws
        book = np.array([float(t["beta_adjusted_pct"]) for t in rows])
        book_mean = float(book.mean())
        placebo_means = mat.mean(axis=0)
        diffs = book - np.median(mat, axis=1)
        months = np.array([t["signal_date"][:7] for t in rows])
        out = {
            "n_ideas": len(rows),
            "placebo_draws": n,
            "book_mean_beta_adjusted_pct": round(book_mean, 4),
            "placebo_mean_of_means": round(float(placebo_means.mean()), 4),
            "placebo_p05_p95": [round(float(x), 4) for x in np.percentile(placebo_means, [5, 95])],
            "placebo_percentile": percentile_of(book_mean, placebo_means),
            "paired_diff_vs_placebo_median": round(float(diffs.mean()), 4),
        }
        if ci_fn is not None:
            lo, hi = ci_fn(diffs, months)
            out["paired_diff_ci95"] = None if lo is None else [round(lo, 4), round(hi, 4)]
        return out
