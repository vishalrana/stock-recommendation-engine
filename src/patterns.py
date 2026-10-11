"""
Point-in-time chart-pattern features (profitability program, Phase 5, rule R4)
==============================================================================
numpy / pandas only. The pivot design (each pivot carries the bar of its extreme and the later bar
at which it became known) is adapted from neurotrader888/TechnicalAnalysisAutomation (MIT licence,
Copyright (c) 2023 neurotrader888): its rolling-window extremes and directional-change (ATR
threshold) pivots. Nothing here uses measured-move targets.

Point-in-time rule: a pivot exists for a bar t only if its confirmation_index <= t, and a pattern
event is stamped at the bar where its last input became known (break bar or pivot confirmation,
whichever is later). Features at t therefore use bars up to t only;
scripts/test_no_lookahead.py checks bar-by-bar against the full-history computation.

Pivots
  rw_pivots(high, low, k): a top at i-k when high[i-k] is the maximum of [i-2k, i]; confirmed at i
  (confirmation_index = extreme_index + k). Bottoms likewise on lows.
  atr_zigzag(high, low, atr, mult): directional change; a top is confirmed when the low falls
  mult x ATR below the running high (and vice versa). The running extreme of the current leg is
  returned separately as provisional and is never treated as a pivot.

Templates (on confirmed zigzag pivots)
  Head and shoulders top: highs H1 < H2 > H3 with troughs L1, L2 between; shoulders within half the
  pattern height of each other; event when a close breaks below the neckline (L1-L2 line) within
  60 bars of H3. Inverse head and shoulders: the mirror image, event on a close above the neckline.
  Double bottom: lows L1, L2 within a quarter of the pattern height, peak H1 between; event on a
  close above H1 within 60 bars of L2.
  Flat base / contraction count: depth of the last 60 sessions' range; number of successive
  pullbacks (zigzag high-to-low declines in the last 120 sessions) each shallower than the one
  before (volatility contraction).
  Bull flag (computed for display, not a pre-registered feature): a >= 4 ATR rise over <= 10 bars,
  a 5-15 bar consolidation retracing <= 50% of it, then a close above the consolidation high.

Features (pre-registered names)
  hs_top_confirmed_20d, inv_hs_confirmed_20d, double_bottom_breakout_20d   1/0 event in last 20 bars
  base_depth_pct, contraction_count
  dist_to_neckline_atr      (close - neckline of the latest bullish structure) / ATR, last 60 bars
  breakout_volume_ratio     breakout-bar volume / prior 50-bar mean, latest bullish breakout <= 20 bars
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

PATTERN_FEATURES = ("hs_top_confirmed_20d", "inv_hs_confirmed_20d", "double_bottom_breakout_20d",
                    "base_depth_pct", "contraction_count", "dist_to_neckline_atr", "breakout_volume_ratio")
ZIGZAG_ATR_MULT = 2.0
EVENT_WINDOW = 20
BREAK_WITHIN = 60
BASE_WINDOW = 60
CONTRACTION_WINDOW = 120
FAILED_TICKERS: Dict[str, str] = {}   # pattern_feature_panel failures (reported by the IC study)


@dataclass(frozen=True)
class Pivot:
    kind: str                 # "H" (top) or "L" (bottom)
    extreme_index: int
    confirmation_index: int
    price: float


@dataclass(frozen=True)
class PatternEvent:
    kind: str                 # "hs_top", "inv_hs", "double_bottom", "bull_flag"
    index: int                # bar at which the pattern became known (point-in-time)
    neckline_a: Tuple[int, float]
    neckline_b: Tuple[int, float]
    breakout_index: Optional[int] = None

    def neckline_at(self, j: int) -> float:
        (i0, p0), (i1, p1) = self.neckline_a, self.neckline_b
        return p0 if i1 == i0 else p0 + (p1 - p0) * (j - i0) / (i1 - i0)


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.concatenate([[np.nan], close[:-1]])
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev), np.abs(low - prev)]), axis=0)
    out = np.full(len(tr), np.nan)
    if len(tr) <= n:
        return out
    out[n] = np.nanmean(tr[1:n + 1])
    for i in range(n + 1, len(tr)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / n if np.isfinite(tr[i]) else out[i - 1]
    return out


def rw_pivots(high: np.ndarray, low: np.ndarray, k: int) -> List[Pivot]:
    """Rolling-window extremes: confirmed k bars after the extreme."""
    out: List[Pivot] = []
    for i in range(2 * k, len(high)):
        c = i - k
        win_h, win_l = high[i - 2 * k: i + 1], low[i - 2 * k: i + 1]
        if np.isfinite(high[c]) and high[c] == np.nanmax(win_h):
            out.append(Pivot("H", c, i, float(high[c])))
        if np.isfinite(low[c]) and low[c] == np.nanmin(win_l):
            out.append(Pivot("L", c, i, float(low[c])))
    return out


def atr_zigzag(high: np.ndarray, low: np.ndarray, atr: np.ndarray,
               mult: float = ZIGZAG_ATR_MULT) -> Tuple[List[Pivot], Optional[Pivot]]:
    """Directional-change pivots on an ATR threshold, plus the provisional extreme of the last leg."""
    pivots: List[Pivot] = []
    trend = 0
    hi = lo = np.nan
    hi_i = lo_i = -1
    for i in range(len(high)):
        h, l, a = high[i], low[i], atr[i]
        if not (np.isfinite(h) and np.isfinite(l)):
            continue
        if not np.isfinite(a):
            if not np.isfinite(hi) or h > hi:
                hi, hi_i = h, i
            if not np.isfinite(lo) or l < lo:
                lo, lo_i = l, i
            continue
        thr = mult * a
        if trend == 0:
            if h > hi:
                hi, hi_i = h, i
            if l < lo:
                lo, lo_i = l, i
            if hi_i > lo_i and hi - lo >= thr:      # rally from the low: the low is a bottom
                pivots.append(Pivot("L", lo_i, i, float(lo)))
                trend = 1
            elif lo_i > hi_i and hi - lo >= thr:    # drop from the high: the high is a top
                pivots.append(Pivot("H", hi_i, i, float(hi)))
                trend = -1
        elif trend == 1:
            if h > hi:
                hi, hi_i = h, i
            elif l <= hi - thr:
                pivots.append(Pivot("H", hi_i, i, float(hi)))
                trend, lo, lo_i = -1, l, i
        else:
            if l < lo:
                lo, lo_i = l, i
            elif h >= lo + thr:
                pivots.append(Pivot("L", lo_i, i, float(lo)))
                trend, hi, hi_i = 1, h, i
    provisional = None
    if trend == 1 and hi_i >= 0:
        provisional = Pivot("H", hi_i, -1, float(hi))
    elif trend == -1 and lo_i >= 0:
        provisional = Pivot("L", lo_i, -1, float(lo))
    return pivots, provisional


def _first_close_beyond(close: np.ndarray, start: int, stop: int, line, below: bool) -> Optional[int]:
    for j in range(start, min(stop, len(close))):
        c = close[j]
        if np.isfinite(c) and ((c < line(j)) if below else (c > line(j))):
            return j
    return None


def pattern_events(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: Optional[np.ndarray] = None,
                   pivots: Optional[List[Pivot]] = None) -> List[PatternEvent]:
    """All H&S top, inverse H&S, double-bottom and bull-flag events, each stamped point-in-time."""
    if atr is None:
        atr = wilder_atr(high, low, close)
    if pivots is None:
        pivots, _ = atr_zigzag(high, low, atr)
    ev: List[PatternEvent] = []
    for a in range(len(pivots) - 4):
        p = pivots[a: a + 5]
        kinds = "".join(x.kind for x in p)
        if kinds == "HLHLH":                         # head and shoulders top
            h1, l1, h2, l2, h3 = p
            height = h2.price - min(l1.price, l2.price)
            if h2.price > max(h1.price, h3.price) and height > 0 and abs(h1.price - h3.price) <= 0.5 * height:
                e = PatternEvent("hs_top", -1, (l1.extreme_index, l1.price), (l2.extreme_index, l2.price))
                j = _first_close_beyond(close, h3.extreme_index + 1, h3.extreme_index + 1 + BREAK_WITHIN, e.neckline_at, True)
                if j is not None:
                    ev.append(PatternEvent("hs_top", max(j, h3.confirmation_index), e.neckline_a, e.neckline_b, j))
        elif kinds == "LHLHL":                       # inverse head and shoulders
            l1, h1, l2, h2, l3 = p
            height = max(h1.price, h2.price) - l2.price
            if l2.price < min(l1.price, l3.price) and height > 0 and abs(l1.price - l3.price) <= 0.5 * height:
                e = PatternEvent("inv_hs", -1, (h1.extreme_index, h1.price), (h2.extreme_index, h2.price))
                j = _first_close_beyond(close, l3.extreme_index + 1, l3.extreme_index + 1 + BREAK_WITHIN, e.neckline_at, False)
                if j is not None:
                    ev.append(PatternEvent("inv_hs", max(j, l3.confirmation_index), e.neckline_a, e.neckline_b, j))
    for a in range(len(pivots) - 2):
        l1, h1, l2 = pivots[a: a + 3]
        if (l1.kind, h1.kind, l2.kind) != ("L", "H", "L"):
            continue
        height = h1.price - min(l1.price, l2.price)
        if height > 0 and abs(l1.price - l2.price) <= 0.25 * height:
            line = lambda j, v=h1.price: v
            j = _first_close_beyond(close, l2.extreme_index + 1, l2.extreme_index + 1 + BREAK_WITHIN, line, False)
            if j is not None:
                ev.append(PatternEvent("double_bottom", max(j, l2.confirmation_index),
                                       (h1.extreme_index, h1.price), (h1.extreme_index, h1.price), j))
    ev += bull_flag_events(high, low, close, atr)
    return sorted(ev, key=lambda e: e.index)


def bull_flag_events(high, low, close, atr) -> List[PatternEvent]:
    out: List[PatternEvent] = []
    n = len(close)
    i = 10
    while i < n - 6:
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            i += 1
            continue
        seg = close[i - 10: i + 1]
        if not np.isfinite(seg).any() or not np.isfinite(close[i]):
            i += 1
            continue
        base = i - 10 + int(np.nanargmin(seg))
        pole = close[i] - close[base]
        if pole >= 4 * a and close[i] == np.nanmax(close[base: i + 1]):
            for length in range(5, 16):
                end = i + length
                if end >= n - 1:
                    break
                fl_hi = np.nanmax(high[i + 1: end + 1])
                fl_lo = np.nanmin(low[i + 1: end + 1])
                if close[i] - fl_lo > 0.5 * pole or fl_hi > high[i] * 1.01:
                    break
                j = end + 1
                if np.isfinite(close[j]) and close[j] > fl_hi:
                    out.append(PatternEvent("bull_flag", j, (i + 1, fl_hi), (end, fl_hi), j))
                    i = j
                    break
        i += 1
    return out


def _contractions(pivots: Sequence[Pivot]) -> int:
    """Successive high-to-low declines (latest first), each shallower than the one before it."""
    declines = []
    for a, b in zip(pivots[:-1], pivots[1:]):
        if a.kind == "H" and b.kind == "L" and a.price > 0:
            declines.append((a.price - b.price) / a.price)
    if not declines:
        return 0
    count = 1
    for k in range(len(declines) - 1, 0, -1):
        if declines[k] < declines[k - 1]:
            count += 1
        else:
            break
    return count


def features_at(t: int, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray,
                atr: np.ndarray, pivots: List[Pivot], events: List[PatternEvent]) -> Dict[str, float]:
    """The seven features at bar t from pivots and events known by t."""
    known_ev = [e for e in events if e.index <= t]
    recent = [e for e in known_ev if e.index > t - EVENT_WINDOW]
    out: Dict[str, float] = {
        "hs_top_confirmed_20d": float(any(e.kind == "hs_top" for e in recent)),
        "inv_hs_confirmed_20d": float(any(e.kind == "inv_hs" for e in recent)),
        "double_bottom_breakout_20d": float(any(e.kind == "double_bottom" for e in recent)),
    }
    lo_i = max(0, t - BASE_WINDOW + 1)
    hh, ll = np.nanmax(high[lo_i: t + 1]), np.nanmin(low[lo_i: t + 1])
    out["base_depth_pct"] = float((hh - ll) / hh * 100.0) if t - lo_i + 1 >= BASE_WINDOW and hh > 0 else np.nan
    known_piv = [p for p in pivots if p.confirmation_index <= t and p.extreme_index > t - CONTRACTION_WINDOW]
    out["contraction_count"] = float(_contractions(known_piv))
    bull = [e for e in known_ev if e.kind in ("inv_hs", "double_bottom") and e.index > t - BASE_WINDOW]
    a = atr[t]
    out["dist_to_neckline_atr"] = (float((close[t] - bull[-1].neckline_at(t)) / a)
                                   if bull and np.isfinite(a) and a > 0 else np.nan)
    br = [e for e in bull if e.index > t - EVENT_WINDOW and e.breakout_index is not None and e.breakout_index >= 50]
    if br:
        j = br[-1].breakout_index
        prior = np.nanmean(volume[j - 50: j])
        out["breakout_volume_ratio"] = float(volume[j] / prior) if prior > 0 else np.nan
    else:
        out["breakout_volume_ratio"] = np.nan
    return out


def ticker_pattern_features(high, low, close, volume, at: Iterable[int]) -> Dict[int, Dict[str, float]]:
    high, low, close, volume = (np.asarray(x, dtype=float) for x in (high, low, close, volume))
    atr = wilder_atr(high, low, close)
    pivots, _ = atr_zigzag(high, low, atr)
    events = pattern_events(high, low, close, atr, pivots)
    return {t: features_at(t, high, low, close, volume, atr, pivots, events) for t in at}


def pattern_feature_panel(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, volume: pd.DataFrame,
                          dates: Optional[Sequence[pd.Timestamp]] = None) -> Dict[str, pd.DataFrame]:
    """Feature frames (sessions x tickers), filled on `dates` (default: month ends) and NaN elsewhere."""
    idx = close.index
    if dates is None:
        s = pd.Series(idx, index=idx)
        dates = s.groupby([idx.year, idx.month]).max().tolist()
    out = {f: pd.DataFrame(np.nan, index=idx, columns=close.columns) for f in PATTERN_FEATURES}
    want = pd.DatetimeIndex(dates)
    for t in close.columns:
        # Each ticker's own valid bars (missing sessions dropped, as the backtest's prepare_frame does)
        ok = (close[t].notna() & high[t].notna() & low[t].notna()).to_numpy()
        if ok.sum() <= BASE_WINDOW:
            continue
        sub_idx = idx[ok]
        loc = {d: i for i, d in enumerate(sub_idx)}
        at = [loc[d] for d in want if d in loc and loc[d] >= BASE_WINDOW]
        if not at:
            continue
        try:
            vals = ticker_pattern_features(high[t].to_numpy()[ok], low[t].to_numpy()[ok], close[t].to_numpy()[ok],
                                           np.nan_to_num(volume[t].to_numpy()[ok]), at)
        except Exception as e:  # reported, never guessed: the ticker's features stay missing
            FAILED_TICKERS[t] = str(e)
            continue
        col = out[PATTERN_FEATURES[0]].columns.get_loc(t)
        for p in at:
            row = idx.get_loc(sub_idx[p])
            for f, v in vals[p].items():
                out[f].iat[row, col] = v
    return out
