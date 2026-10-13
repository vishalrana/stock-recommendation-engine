"""
Swing Signals — point-in-time engine (shared by research and live)
==================================================================
Every sleeve input is computed here from data available at the close of the signal session t;
scripts/research_swing.py (holdout backtest) and jobs/generate_swing_signals.py (nightly) call
the same functions, so a rule cannot differ between the two.

  build_context()         price panel -> per-ticker arrays, universe and liquidity masks,
                          C6 percentile within the evaluated universe, beta252, event locks
  sleeve_candidates()     raw triggers per sleeve (vectorised), later checked against G0
  run_book()              chronological standalone book per sleeve: G0 (liquidity, event lock,
                          open signal / cooldown, earnings within 5 sessions), signal levels,
                          exits via src.swing.exits.simulate_swing_trade
  SwingPlacebo            random-stock placebo built on src.research.placebo.PlaceboEngine

Definitions follow CLAUDE.md, "Swing signals program", section 1 (pre-registered 2026-10-11).
"""

import bisect
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from src.patterns import wilder_atr
from src.providers.context.sec_filings import (
    Filing, earnings_events, effective_day, in_intervals, lock_intervals, price_pin,
)
from src.research.costs import rolling_half_spread_pct
from src.research.placebo import PlaceboEngine, stable_seed
from src.research.stats import percentile_of
from src.swing.events import NEG8K_ITEMS, detect_insider_clusters_on_date, is_routine_purchase
from src.swing.exits import SwingTrade, max_holding_sessions_for_sleeve, simulate_swing_trade
from src.swing.rules import (
    SwingSignal, check_g0, generate_sleeve_e_signal, generate_sleeve_i_signal, generate_sleeve_n_signal,
    generate_sleeve_p_signal, generate_sleeve_t_signal,
)
from src.trade_hygiene import SessionCalendar

logger = logging.getLogger(__name__)

PRICE_EXITS = ("stop", "target")
E_REFERENCE_SESSIONS = 63
E_MIN_REFERENCE = 50
COOLDOWN_SESSIONS = 10


# ----------------------------------------------------------------------------
# Indicators (Wilder conventions; total-return prices)
# ----------------------------------------------------------------------------
def wilder_rsi(close: np.ndarray, period: int = 2) -> np.ndarray:
    """Wilder RSI: average gain / loss with alpha = 1/period, seeded by the first `period` changes."""
    c = np.asarray(close, dtype=float)
    out = np.full(len(c), np.nan)
    d = np.diff(c)
    if len(d) < period:
        return out
    gain, loss = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = gain[:period].mean(), loss[:period].mean()
    out[period] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(period, len(d)):
        g, l = gain[i], loss[i]
        if not (np.isfinite(g) and np.isfinite(l)):
            continue
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
        out[i + 1] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


@dataclass
class TickerArrays:
    ticker: str
    index: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    raw_c: np.ndarray
    v: np.ndarray
    atr: np.ndarray
    rsi2: np.ndarray
    sma5: np.ndarray
    ma200: np.ndarray
    adv20: np.ndarray
    med_vol50: np.ndarray
    liquid: np.ndarray
    pin: np.ndarray
    half_spread: np.ndarray
    reactions: List[date] = field(default_factory=list)
    lock_iv: List[Tuple[date, date]] = field(default_factory=list)
    filings: Optional[List[Filing]] = None

    def pos(self, d) -> Optional[int]:
        i = self.index.searchsorted(pd.Timestamp(d))
        return int(i) if i < len(self.index) and self.index[i] == pd.Timestamp(d) else None

    _locked: Optional[np.ndarray] = None
    _bars: Optional[pd.DataFrame] = None

    def locked_array(self) -> np.ndarray:
        """Event-locked per bar: an SEC lock interval covers the session, or the price is pinned."""
        if self._locked is None:
            out = np.asarray(self.pin, dtype=bool).copy()
            ns = self.index.values.astype("datetime64[D]")
            for a, b in self.lock_iv:
                lo = np.searchsorted(ns, np.datetime64(a, "D"), side="left")
                hi = np.searchsorted(ns, np.datetime64(b, "D"), side="right")
                out[lo:hi] = True
            self._locked = out
        return self._locked

    def locked(self, i: int) -> bool:
        return bool(self.locked_array()[i])

    def bars(self) -> pd.DataFrame:
        if self._bars is None:
            self._bars = pd.DataFrame({"TR_OPEN": self.o, "TR_HIGH": self.h, "TR_LOW": self.l, "TR_CLOSE": self.c,
                                       "SMA5": self.sma5}, index=self.index)
        return self._bars


def ticker_arrays(t: str, df: pd.DataFrame, filings: Optional[List[Filing]] = None,
                  rights: Sequence[date] = ()) -> TickerArrays:
    """Per-ticker point-in-time arrays from a long-history frame (TR_* prices, UNADJ_CLOSE, VOLUME)."""
    df = df.sort_index()
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("TR_OPEN", "TR_HIGH", "TR_LOW", "TR_CLOSE"))
    raw = df["UNADJ_CLOSE"].to_numpy(dtype=float) if "UNADJ_CLOSE" in df else df["CLOSE"].to_numpy(dtype=float)
    nominal_close = df["CLOSE"].to_numpy(dtype=float) if "CLOSE" in df else c
    v = df["VOLUME"].to_numpy(dtype=float)
    dollar = pd.Series(nominal_close * v)
    adv20 = dollar.rolling(20, min_periods=20).mean().to_numpy()
    cs = pd.Series(c)
    ma200 = cs.rolling(200, min_periods=200).mean().to_numpy()
    liquid = (raw >= 5.0) & (adv20 >= 5_000_000.0) & (np.arange(len(c)) + 1 >= 252)
    f32 = lambda x: np.asarray(x, dtype=np.float32)       # memory: ~6,400 tickers x 20 years
    ta = TickerArrays(
        ticker=t, index=pd.DatetimeIndex(df.index), o=f32(o), h=f32(h), l=f32(l), c=f32(c), raw_c=f32(raw), v=f32(v),
        atr=f32(wilder_atr(h, l, c, 14)), rsi2=f32(wilder_rsi(c, 2)), sma5=f32(cs.rolling(5, min_periods=5).mean()),
        ma200=f32(ma200), adv20=f32(adv20),
        med_vol50=f32(pd.Series(v).rolling(50, min_periods=50).median().shift(1)),
        liquid=np.nan_to_num(liquid, nan=False).astype(bool),
        pin=price_pin(cs).to_numpy() if len(c) > 30 else np.zeros(len(c), dtype=bool),
        half_spread=f32(rolling_half_spread_pct(pd.Series(h), pd.Series(l), close=cs)),
        filings=filings,
    )
    if filings:
        ta.reactions = sorted({e.reaction for e in earnings_events(filings)})
        ta.lock_iv = lock_intervals(filings, rights)
    return ta


def build_ticker_arrays(frames: Dict[str, pd.DataFrame], filings: Dict[str, Optional[List[Filing]]],
                        rights: Dict[str, List[date]], names: Optional[Iterable[str]] = None) -> Dict[str, TickerArrays]:
    """Per-ticker arrays for many tickers (build once, share between universes)."""
    names = sorted(names) if names is not None else sorted(t for t in frames if t != "SPY")
    return {t: ticker_arrays(t, frames[t], filings.get(t), rights.get(t, ())) for t in names if t in frames}


# ----------------------------------------------------------------------------
# Context: universe, C6 percentile, beta
# ----------------------------------------------------------------------------
@dataclass
class SwingContext:
    universe: str                                   # "u1" or "u2"
    sessions: pd.DatetimeIndex                      # trading calendar (SPY bars)
    cal: SessionCalendar
    tickers: Dict[str, TickerArrays]
    spy_open: Dict[date, float]
    spy_close: Dict[date, float]
    members: pd.DataFrame                           # sessions x tickers: in the evaluated universe (before liquidity)
    liquid: pd.DataFrame                            # sessions x tickers
    beta: pd.DataFrame                              # sessions x tickers, BETA252 from compute_features
    c6pct: Optional[pd.DataFrame] = None            # sessions x tickers, percentile among universe & liquid
    adv_rank: Optional[pd.DataFrame] = None         # sessions x tickers, ADV20 rank within the universe's liquid names
    etf_close: Optional[pd.DataFrame] = None
    sectors: Dict[str, Optional[str]] = field(default_factory=dict)
    insider: Optional[pd.DataFrame] = None          # qualifying purchases (see qualifying_purchases)
    earnings_known: Optional[Callable[[str, date], Tuple[List[date], bool]]] = None  # live override

    def eligible(self, t: str, i_sess: int) -> bool:
        d = self.sessions[i_sess]
        return bool(self.members.iat[i_sess, self.members.columns.get_loc(t)]
                    and self.liquid.iat[i_sess, self.liquid.columns.get_loc(t)]) if t in self.members.columns else False


def u2_membership(sessions: pd.DatetimeIndex, tickers: Sequence[str], membership: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time S&P 500 membership per session (fja05680 intervals, Yahoo symbols)."""
    m = pd.DataFrame(False, index=sessions, columns=list(tickers))
    cols = set(m.columns)
    for row in membership.itertuples(index=False):
        if row.ticker not in cols:
            continue
        end = row.end_date if pd.notna(row.end_date) else sessions[-1]
        m.loc[(sessions >= row.start_date) & (sessions <= end), row.ticker] = True
    return m


def build_context(universe: str, frames: Dict[str, pd.DataFrame], spy: pd.DataFrame, etf_close: pd.DataFrame,
                  sectors: Dict[str, Optional[str]], filings: Dict[str, Optional[List[Filing]]],
                  rights: Dict[str, List[date]], u1_tickers: Optional[Iterable[str]] = None,
                  membership: Optional[pd.DataFrame] = None, window: Optional[Tuple[str, str]] = None,
                  with_c6: bool = True, insider: Optional[pd.DataFrame] = None,
                  tick: Optional[Dict[str, "TickerArrays"]] = None) -> SwingContext:
    """
    Build the evaluated universe. window = (first, last) session for the wide panels; per-ticker
    arrays keep their full history (exits and indicators need bars outside the window).
    """
    from scripts.research_signal_ic import compute_features, composite_c6, liquid_mask
    spy = spy.sort_index()
    sessions = spy.index if window is None else spy.loc[window[0]:window[1]].index
    names = sorted(t for t in frames if t != "SPY")
    if universe == "u1":
        allowed = set(u1_tickers) if u1_tickers is not None else set(names)
        names = [t for t in names if t in allowed]
    if tick is None:
        tick = build_ticker_arrays(frames, filings, rights, names)
    else:
        tick = {t: tick[t] for t in names if t in tick}
    names = sorted(tick)
    wide = lambda attr: pd.DataFrame({t: pd.Series(getattr(a, attr), index=a.index) for t, a in tick.items()}).reindex(sessions)
    close, high, vol, raw = wide("c"), wide("h"), wide("v"), wide("raw_c")
    liq = wide("liquid").fillna(False).astype(bool)
    if universe == "u2":
        from src.research.survivorship import load_membership
        members = u2_membership(sessions, names, membership if membership is not None else load_membership())
    else:
        members = close.notna()
    mask = members & liq & close.notna()
    spy_c = spy["TR_CLOSE"].reindex(sessions)
    etf = etf_close.reindex(sessions) if etf_close is not None else pd.DataFrame(index=sessions)
    sec = {t: (sectors.get(t) if sectors.get(t) in etf.columns else None) for t in names}
    if with_c6:
        feats = compute_features(close, high, vol, spy_c, etf, sec)
        c6 = composite_c6(feats, mask)
        c6pct = c6.where(mask).rank(axis=1, pct=True)
        beta = feats["BETA252"]
    else:
        r = close.pct_change(fill_method=None)
        sr = spy_c.pct_change(fill_method=None)
        beta = r.rolling(252, min_periods=120).cov(sr).div(sr.rolling(252, min_periods=120).var(), axis=0)
        c6pct = None
    adv = wide("adv20").where(mask)
    ctx = SwingContext(
        universe=universe, sessions=sessions, cal=SessionCalendar([d.date() for d in spy.index]), tickers=tick,
        spy_open={d.date(): float(x) for d, x in spy["TR_OPEN"].items()},
        spy_close={d.date(): float(x) for d, x in spy["TR_CLOSE"].items()},
        members=members, liquid=liq, beta=beta, c6pct=c6pct, adv_rank=adv.rank(axis=1, ascending=False),
        etf_close=etf, sectors=sec, insider=insider,
    )
    return ctx


# ----------------------------------------------------------------------------
# Sleeve inputs
# ----------------------------------------------------------------------------
def _col(frame: pd.DataFrame, t: str) -> np.ndarray:
    return frame[t].to_numpy() if t in frame.columns else np.full(len(frame), np.nan)


def earnings_reactions(ctx: SwingContext, t: str, d: date) -> Tuple[List[date], bool]:
    """Reaction sessions known for ticker t at d, and whether the date is unknown (live may override)."""
    if ctx.earnings_known is not None:
        return ctx.earnings_known(t, d)
    a = ctx.tickers[t]
    return a.reactions, a.filings is None


def t_candidates(ctx: SwingContext) -> List[Tuple[pd.Timestamp, str]]:
    c, ma = (pd.DataFrame({t: pd.Series(getattr(a, k), index=a.index) for t, a in ctx.tickers.items()}).reindex(ctx.sessions)
             for k in ("c", "ma200"))
    rising = ma > ma.shift(20)
    hit = (ctx.c6pct >= 0.90) & (c > ma) & rising & ctx.members & ctx.liquid
    return [(ctx.sessions[i], hit.columns[j]) for i, j in zip(*np.nonzero(hit.to_numpy()))]


def p_candidates(ctx: SwingContext) -> List[Tuple[pd.Timestamp, str]]:
    f = lambda k: pd.DataFrame({t: pd.Series(getattr(a, k), index=a.index) for t, a in ctx.tickers.items()}).reindex(ctx.sessions)
    c, ma, rsi, sma5 = f("c"), f("ma200"), f("rsi2"), f("sma5")
    universe = ctx.members & ctx.liquid
    if ctx.universe == "u1":
        universe &= ctx.adv_rank <= 500                      # U1-top500 by ADV20 on t
    hit = universe & (c > ma) & (ma > ma.shift(20)) & (rsi < 10.0) & (c < sma5)
    return [(ctx.sessions[i], hit.columns[j]) for i, j in zip(*np.nonzero(hit.to_numpy()))]


def n_candidates(ctx: SwingContext) -> List[Tuple[pd.Timestamp, str]]:
    out = []
    for t, a in ctx.tickers.items():
        prev = np.concatenate([[np.nan], a.c[:-1]])
        rg = a.h - a.l
        hit = ((a.o / prev - 1.0 >= 0.05) & (a.v >= 3.0 * a.med_vol50) & (a.c >= a.o)
               & ((rg <= 0) | (a.c - a.l >= 0.5 * rg)))
        for i in np.flatnonzero(np.nan_to_num(hit, nan=False)):
            d = a.index[i]
            if d in ctx.sessions:
                out.append((d, t))
    return sorted(out)


def catalyst_filings(a: TickerArrays, ctx: SwingContext, d: date) -> Tuple[bool, bool]:
    """(has an 8-K/6-K accepted in (16:00 ET on t-1, 20:00 ET on t], any of those 8-Ks carries a NEG8K item)."""
    from datetime import datetime
    prev = ctx.cal.shift(d, -1)
    lo = datetime(prev.year, prev.month, prev.day, 16, 0)
    hi = datetime(d.year, d.month, d.day, 20, 0)
    found, neg = False, False
    for f in a.filings or []:
        if f.form in ("8-K", "6-K") and lo < f.accepted <= hi:
            found = True
            if f.form == "8-K" and any(x in NEG8K_ITEMS for x in f.items):
                neg = True
    return found, neg


def e_events(ctx: SwingContext) -> List[Dict[str, Any]]:
    """
    Every 8-K item 2.02 reaction session d0 in the universe (member and Liquid on d0) with
    EAR = sum over d0-1..d0+1 of (r_stock - r_sector_etf or SPY) and
    AVOL = mean volume(d0-1..d0+1) / mean volume(d0-63..d0-8) - 1.
    """
    out = []
    spy_c = pd.Series(ctx.spy_close)
    spy_c.index = pd.DatetimeIndex(spy_c.index)
    for t, a in ctx.tickers.items():
        if not a.reactions:
            continue
        r = np.concatenate([[np.nan], a.c[1:] / a.c[:-1] - 1.0])
        etf = ctx.sectors.get(t)
        bench = (ctx.etf_close[etf] if etf and etf in ctx.etf_close else None)
        for d0 in a.reactions:
            i = a.pos(d0)
            if i is None or i < 64 or i + 1 >= len(a.c):
                continue
            ts0 = a.index[i]
            if ts0 not in ctx.sessions:
                continue
            si = ctx.sessions.get_loc(ts0)
            if not (ctx.members.iat[si, ctx.members.columns.get_loc(t)] and ctx.liquid.iat[si, ctx.liquid.columns.get_loc(t)]):
                continue
            days = a.index[i - 1: i + 2]
            b = bench.reindex(a.index[i - 2: i + 2]) if bench is not None else None
            if b is None or b.isna().any():                  # the ETF has no bar: SPY
                b = spy_c.reindex(a.index[i - 2: i + 2])
            if b.isna().any():
                continue
            rb = (b / b.shift(1) - 1.0).to_numpy()[1:]
            ear = float(np.sum(r[i - 1: i + 2] - rb))
            base = a.v[i - 63: i - 7]
            if not (np.isfinite(base).sum() >= 30 and np.nanmean(base) > 0):
                continue
            avol = float(np.mean(a.v[i - 1: i + 2]) / np.nanmean(base) - 1.0)
            if np.isfinite(ear) and np.isfinite(avol):
                out.append({"ticker": t, "d0": ts0, "i": i, "ear": ear, "avol": avol, "t": a.index[i + 1]})
    out.sort(key=lambda e: e["d0"])
    # Percentiles within the events whose reaction session falls in the 63 sessions ending at d0-1
    pos = {d: k for k, d in enumerate(ctx.sessions)}
    d0s = [pos[e["d0"]] for e in out]
    for k, e in enumerate(out):
        p = d0s[k]
        lo = bisect.bisect_left(d0s, p - E_REFERENCE_SESSIONS)
        hi = bisect.bisect_left(d0s, p)                       # strictly before d0
        ref = out[lo:hi]
        if len(ref) < E_MIN_REFERENCE:
            e["ear_pct"] = e["avol_pct"] = None
            continue
        e["ear_pct"] = percentile_of(e["ear"], [x["ear"] for x in ref]) / 100.0
        e["avol_pct"] = percentile_of(e["avol"], [x["avol"] for x in ref]) / 100.0
        e["ref_n"] = len(ref)
    return out


def qualifying_purchases(purchases: pd.DataFrame, ticker_of: Callable[[int, str, date], Optional[str]]) -> pd.DataFrame:
    """
    Qualifying sleeve-I purchases (CLAUDE.md, sleeve I): original Form 4 code-P acquisitions by a
    director or officer, shares x price >= $10,000, AFF10B5ONE not true, not routine (the owner made
    a code-P purchase in the same calendar month in each of the three prior calendar years).
    `ticker_of(issuer_cik, filed_symbol, filing_date)` maps the issuer to a price ticker.
    """
    p = purchases.copy()
    p["filing_date"] = pd.to_datetime(p["filing_date"]).dt.date
    hist = defaultdict(set)                       # owner -> {(year, month)} of any code-P purchase
    for o, fd in zip(p["owner_cik"], p["filing_date"]):
        hist[int(o)].add((fd.year, fd.month))
    routine = [all((fd.year - k, fd.month) in hist[int(o)] for k in (1, 2, 3)) for o, fd in zip(p["owner_cik"], p["filing_date"])]
    q = p[(p["is_director"] | p["is_officer"]) & (p["value"] >= 10_000.0) & (p["aff10b5one"] != True)  # noqa: E712
          & ~np.array(routine)].copy()
    q["ticker"] = [ticker_of(int(c), s, fd) for c, s, fd in zip(q["issuer_cik"], q["symbol"], q["filing_date"])]
    q = q.dropna(subset=["ticker"])
    q["owner_id"] = q["owner_cik"].astype(str)
    return q


def i_candidates(ctx: SwingContext) -> List[Tuple[pd.Timestamp, str, date]]:
    """(t, ticker, D) for each filing date D of a qualifying purchase; the cluster test runs in the book."""
    if ctx.insider is None or ctx.insider.empty:
        return []
    out = []
    for (tk, fd) in sorted(set(zip(ctx.insider["ticker"], ctx.insider["filing_date"]))):
        if tk not in ctx.tickers:
            continue
        t = ctx.cal.shift(fd + timedelta(days=1), 0)          # first session strictly after D
        ts = pd.Timestamp(t)
        if ts in ctx.sessions:
            out.append((ts, tk, fd))
    return sorted(out)


# ----------------------------------------------------------------------------
# Book: standalone sleeve replay with G0 and exits
# ----------------------------------------------------------------------------
def build_signal(ctx: SwingContext, sleeve: str, t: str, ts: pd.Timestamp, extra: Dict[str, Any]) -> Optional[SwingSignal]:
    a = ctx.tickers[t]
    i = a.pos(ts)
    if i is None or i < 1:
        return None
    d = ts.date()
    si = ctx.sessions.get_loc(ts)
    beta = ctx.beta[t].iat[si] if t in ctx.beta.columns else np.nan
    beta = float(beta) if np.isfinite(beta) else None
    common = dict(beta=beta, unadj_close=float(a.raw_c[i]), adv20=float(a.adv20[i]))
    ma_lag = a.ma200[i - 20] if i >= 20 else np.nan
    if sleeve == "T":
        return generate_sleeve_t_signal(t, d, a.c[i], a.ma200[i], ma_lag, float(ctx.c6pct[t].iat[si]), a.atr[i], True, **common)
    if sleeve == "P":
        return generate_sleeve_p_signal(t, d, a.c[i], a.ma200[i], ma_lag, a.rsi2[i], a.sma5[i], a.atr[i], True, **common)
    if sleeve == "N":
        found, neg = catalyst_filings(a, ctx, d)
        return generate_sleeve_n_signal(t, d, a.o[i], a.h[i], a.l[i], a.c[i], a.c[i - 1], a.v[i], a.med_vol50[i],
                                        a.atr[i - 1], found, neg, True, **common)
    if sleeve == "E":
        if extra.get("ear_pct") is None:
            return None
        return generate_sleeve_e_signal(t, d, a.c[i], a.atr[i], extra["ear"], extra["ear_pct"], extra["avol_pct"], True, **common)
    if sleeve == "I":
        return generate_sleeve_i_signal(t, d, a.c[i], a.atr[i], extra["cluster"], True, **common)
    return None


def run_book(ctx: SwingContext, sleeve: str, start: date, end: date, overlay_x1: bool = False,
             open_until: Optional[Dict[str, date]] = None, close_checked_stops: bool = False,
             veto: Optional[Callable[[str, date], bool]] = None) -> Dict[str, Any]:
    """
    Standalone replay of one sleeve over signal sessions in [start, end]. Returns trades (closed),
    censored signals and the G0 funnel. One open signal per (sleeve, ticker); cooldown of 10
    sessions after the exit's execution.
    """
    funnel = defaultdict(int)
    if sleeve == "T":
        raw = [(ts, t, {}) for ts, t in t_candidates(ctx)]
    elif sleeve == "P":
        raw = [(ts, t, {}) for ts, t in p_candidates(ctx)]
    elif sleeve == "N":
        raw = [(ts, t, {}) for ts, t in n_candidates(ctx)]
    elif sleeve == "E":
        raw = [(e["t"], e["ticker"], e) for e in e_events(ctx) if e["t"] in ctx.sessions
               and e["ear"] > 0 and (e.get("ear_pct") or 0) >= 0.80 and (e.get("avol_pct") or 0) >= 0.80]
    elif sleeve == "I":
        raw = [(ts, t, {"D": D}) for ts, t, D in i_candidates(ctx)]
    else:
        raise ValueError(sleeve)
    raw = sorted((r for r in raw if start <= r[0].date() <= end), key=lambda r: (r[0], r[1]))
    funnel["raw_triggers"] = len(raw)
    busy: Dict[str, date] = dict(open_until or {})            # ticker -> first session a new signal is allowed
    by_issuer = ({t: g.to_dict("records") for t, g in ctx.insider.groupby("ticker")}
                 if sleeve == "I" and ctx.insider is not None else {})
    last_fired: Dict[str, date] = {}
    trades: List[SwingTrade] = []
    censored: List[SwingSignal] = []
    signals: List[SwingSignal] = []
    for ts, t, extra in raw:
        d = ts.date()
        a = ctx.tickers[t]
        i = a.pos(ts)
        si = ctx.sessions.get_loc(ts)
        if i is None:
            continue
        if sleeve == "I":
            cl = detect_insider_clusters_on_date(extra["D"], t, by_issuer.get(t, []), last_fired.get(t))
            if cl is None:
                continue
            extra = dict(extra, cluster=cl)
            funnel["clusters"] += 1
        reactions, unknown = earnings_reactions(ctx, t, d)
        is_liquid = bool(ctx.members[t].iat[si] and ctx.liquid[t].iat[si])
        open_keys = {(sleeve, t)} if t in busy and d < busy[t] and busy[t] == date.max else set()
        cooldowns = {(sleeve, t): busy[t]} if t in busy else {}
        ok, why = check_g0(t, sleeve, d, is_liquid, a.locked(i), open_keys, cooldowns, reactions, ctx.cal)
        if not ok:
            funnel[f"g0_{why.split(' ')[0].lower()}"] += 1
            continue
        sig = build_signal(ctx, sleeve, t, ts, extra)
        if sig is None:
            funnel["rule_failed"] += 1
            continue
        if veto is not None and veto(t, d):
            funnel["overlay_vetoed"] += 1
            continue
        sig.earnings_unknown = unknown
        funnel["signals"] += 1
        signals.append(sig)
        if sleeve == "I":
            last_fired[t] = extra["D"]
        lk = a.locked_array()
        locked_sessions = [a.index[k].date() for k in range(i + 1, min(len(a.c), i + 2 + max_holding_sessions_for_sleeve(sleeve)))
                           if lk[k]]
        trade = simulate_swing_trade(t, sleeve, d, sig.stop_loss, sig.target, a.bars(), (ctx.spy_open, ctx.spy_close),
                                     next_reaction_sessions=reactions, event_locked_sessions=locked_sessions,
                                     beta=sig.beta, half_spread=float(a.half_spread[i]) if np.isfinite(a.half_spread[i]) else None,
                                     overlay_x1=overlay_x1, close_checked_stops=close_checked_stops)
        if trade is None or trade.exit_date is None:
            censored.append(sig)
            busy[t] = date.max                                 # still open when the data ends
            continue
        trade.details = {"signal_close": sig.entry_signal_close, "atr14": sig.atr14,
                         "c6_pct": sig.c6_pct, "ear_pct": sig.ear_pct, "avol_pct": sig.avol_pct,
                         "gap_pct": sig.gap_pct, "rsi2": sig.rsi2, "cluster_value": sig.cluster_value,
                         "earnings_unknown": unknown}
        trades.append(trade)
        busy[t] = ctx.cal.shift(trade.exit_date, COOLDOWN_SESSIONS + 1)
    return {"trades": trades, "censored": censored, "signals": signals, "funnel": dict(funnel)}


# ----------------------------------------------------------------------------
# Placebo (built on PlaceboEngine): random eligible stocks with the same plan in ATR multiples
# ----------------------------------------------------------------------------
class SwingPlacebo(PlaceboEngine):
    """
    For each signal: a random ticker of the same universe that is Liquid and not Event-locked on t
    and is not the signal's ticker; fills on the same session; stop and target at the signal's ATR
    multiples applied to the placebo's own ATR14 and close; the sleeve's max hold; when the real
    signal ended on a non-price exit, the placebo exits at the same execution session.
    200 runs seeded with stable_seed(sleeve, universe, run).
    """

    def __init__(self, ctx: SwingContext, cost_pct_per_side: float = 0.10):
        frames = {}
        for t, a in ctx.tickers.items():
            elig = (ctx.members[t] & ctx.liquid[t]).reindex(a.index).fillna(False).to_numpy(dtype=bool)
            ok = elig & ~a.locked_array()
            frames[t] = pd.DataFrame({"OPEN": a.o, "HIGH": a.h, "LOW": a.l, "CLOSE": a.c, "ATR_14": a.atr,
                                      "_LIQUID": ok}, index=a.index)
        betas = {t: ctx.beta[t] for t in ctx.beta.columns}
        spy = pd.DataFrame({"OPEN": pd.Series(ctx.spy_open), "CLOSE": pd.Series(ctx.spy_close)})
        spy.index = pd.DatetimeIndex(spy.index)
        super().__init__(frames, spy, betas, max_holding_sessions_for_sleeve, cost_pct_per_side=cost_pct_per_side)
        self.ctx = ctx

    def _swing_sim(self, t: str, ts: pd.Timestamp, m_stop: float, m_target: Optional[float], max_hold: int,
                   forced_exit: Optional[date]) -> Optional[float]:
        d = self.data[t]
        p = self._pos(d, ts)
        if p is None or p + 1 >= len(d["c"]):
            return None
        close, atr = d["c"][p], d["atr"][p]
        if not (np.isfinite(close) and np.isfinite(atr) and atr > 0):
            return None
        stop = close - m_stop * atr
        target = close + m_target * atr if m_target is not None else None
        o, h, l, c, idx = d["o"], d["h"], d["l"], d["c"], d["index"]
        fill = o[p + 1]
        if not (np.isfinite(fill) and fill > 0):
            return None
        exit_px, exit_day, at_open = None, None, False
        for k, i in enumerate(range(p + 1, len(c)), start=1):
            day = idx[i].date()
            if forced_exit is not None and day >= forced_exit:
                exit_px, exit_day, at_open = o[i], day, True
                break
            if o[i] <= stop:
                exit_px, exit_day = o[i], day
                break
            if l[i] <= stop:
                exit_px, exit_day = stop, day
                break
            if target is not None and (o[i] >= target or h[i] >= target):
                exit_px, exit_day = (o[i] if o[i] >= target else target), day
                break
            if forced_exit is None and k >= max_hold:
                if i + 1 >= len(c):
                    return None
                exit_px, exit_day, at_open = o[i + 1], idx[i + 1].date(), True
                break
        if exit_px is None:
            return None
        so = self.ctx.spy_open.get(idx[p + 1].date())
        se = (self.ctx.spy_open if at_open else self.ctx.spy_close).get(exit_day)
        beta = d["beta"][p]
        if not so or not se or not np.isfinite(beta):
            return None
        net = (exit_px - fill) / fill * 100.0 - 2.0 * self.cost
        return float(net - beta * (se / so - 1.0) * 100.0)

    def run(self, sleeve: str, trades: List[SwingTrade], n_runs: int = 200) -> Dict[str, Any]:
        """Per-run placebo means and per-trade placebo medians, aligned with `trades`."""
        draws = np.full((len(trades), n_runs), np.nan)
        plans = []
        for tr in trades:
            det = tr.details or {}
            atr, close = det.get("atr14"), det.get("signal_close")
            m_stop = (close - tr.stop_loss) / atr if atr else None
            m_t = (tr.target - close) / atr if (tr.target is not None and atr) else None
            forced = tr.exit_date if tr.exit_reason not in PRICE_EXITS else None
            plans.append((pd.Timestamp(tr.signal_date), tr.ticker, m_stop, m_t, forced))
        max_hold = max_holding_sessions_for_sleeve(sleeve)
        for r in range(n_runs):
            rng = np.random.default_rng(stable_seed(sleeve, self.ctx.universe, r))
            for k, (ts, own, m_stop, m_t, forced) in enumerate(plans):
                elig = self.eligible.get(ts)
                if m_stop is None or elig is None or len(elig) < 2:
                    continue
                for _ in range(20):
                    pick = self.tickers[int(elig[rng.integers(0, len(elig))])]
                    if pick == own:
                        continue
                    v = self._swing_sim(pick, ts, m_stop, m_t, max_hold, forced)
                    if v is not None:
                        draws[k, r] = v
                        break
        means = [float(np.nanmean(draws[:, r])) for r in range(n_runs) if np.isfinite(draws[:, r]).any()]
        medians = np.nanmedian(draws, axis=1) if n_runs else np.array([])
        return {"means": means, "per_trade_median": medians.tolist(), "coverage": float(np.isfinite(draws).mean())}
