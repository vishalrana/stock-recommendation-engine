"""
Strategy Evidence
=================
Per-strategy win rate, expectancy and target-hit rates measured by the production-pipeline
backtest on the production universe (workflow backtest_production_universe.yml), adopted into
config/strategy_performance.json by scripts/adopt_strategy_evidence.py.

These replace hard-coded expectancy assumptions and per-ticker metrics from an unrelated
legacy backtest. Raw values are shrunk toward neutral priors by trade count:
    shrunk_win_rate   = (wins * 100 + a * 50) / (n + a)
    shrunk_expectancy = (n * mean_return + a * 0) / (n + a)
With no evidence the neutral priors apply (50% win rate, 0% expectancy).

The backtest itself uses the same functions walk-forward: at each signal date only trades
closed before that date count, so the evidence is never forward-looking.
"""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.quant_config import (
    BAYESIAN_SHRINKAGE_ALPHA,
    BAYESIAN_PRIOR_WIN_RATE,
    BAYESIAN_PRIOR_EXPECTANCY_PCT,
    normalize_strategy_key,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE_PATH = os.path.join(PROJECT_ROOT, "config", "strategy_performance.json")

T1_REACHED = ("hit_t1", "hit_t2", "hit_t3")
T2_REACHED = ("hit_t2", "hit_t3")
T3_REACHED = ("hit_t3",)


@dataclass
class StrategyEvidence:
    strategy: str
    trades: int = 0
    wins: int = 0
    raw_win_rate: Optional[float] = None       # percent
    raw_expectancy: Optional[float] = None     # percent per trade (net of costs)
    shrunk_win_rate: float = BAYESIAN_PRIOR_WIN_RATE
    shrunk_expectancy: float = BAYESIAN_PRIOR_EXPECTANCY_PCT
    # Display only (cards), never read by scoring: mean net return minus beta x SPY over each trade's
    # window, and minus SPY itself, over the trades that had a benchmark return.
    raw_beta_adjusted_expectancy: Optional[float] = None
    raw_excess_vs_spy_expectancy: Optional[float] = None
    # target -> (hits, trades where that target existed)
    target_hits: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    source: str = "no_evidence"
    as_of: Optional[str] = None


def aggregate_trades(trades: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Aggregate closed trades into per-strategy sufficient statistics.
    Each trade needs: strategy, net_return_pct, outcome, has_t2, has_t3.
    """
    agg: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        add_trade_to_aggregate(agg, t)
    return agg


def _finite(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def add_trade_to_aggregate(agg: Dict[str, Dict[str, Any]], t: Dict[str, Any]) -> None:
    """
    Add one closed trade to per-strategy sufficient statistics (in place; order-independent).
    The beta-adjusted and vs-SPY sums are display-only (cards); scoring never reads them.
    """
    key = normalize_strategy_key(t["strategy"])
    a = agg.setdefault(key, {
        "trades": 0, "wins": 0, "sum_return": 0.0,
        "t1_hits": 0, "t1_n": 0, "t2_hits": 0, "t2_n": 0, "t3_hits": 0, "t3_n": 0,
        "beta_adjusted_n": 0, "sum_beta_adjusted": 0.0, "excess_vs_spy_n": 0, "sum_excess_vs_spy": 0.0,
    })
    beta_adj = _finite(t.get("beta_adjusted_pct"))
    if beta_adj is not None:
        a["beta_adjusted_n"] = a.get("beta_adjusted_n", 0) + 1
        a["sum_beta_adjusted"] = a.get("sum_beta_adjusted", 0.0) + beta_adj
    excess = _finite(t.get("excess_vs_spy_pct"))
    if excess is not None:
        a["excess_vs_spy_n"] = a.get("excess_vs_spy_n", 0) + 1
        a["sum_excess_vs_spy"] = a.get("sum_excess_vs_spy", 0.0) + excess
    ret = float(t["net_return_pct"])
    outcome = str(t.get("outcome", ""))
    a["trades"] += 1
    a["wins"] += 1 if ret > 0 else 0
    a["sum_return"] += ret
    a["t1_n"] += 1
    a["t1_hits"] += 1 if outcome in T1_REACHED else 0
    if t.get("has_t2"):
        a["t2_n"] += 1
        a["t2_hits"] += 1 if outcome in T2_REACHED else 0
    if t.get("has_t3"):
        a["t3_n"] += 1
        a["t3_hits"] += 1 if outcome in T3_REACHED else 0


class WalkForwardEvidenceBook:
    """
    Walk-forward strategy evidence for a chronological replay: a closed trade counts from the first
    evaluation date after its exit date. Equivalent to re-aggregating every trade that exited before
    each date, but each trade is added once (a heap by exit date), so large universes stay linear.
    """

    def __init__(self):
        self._pending: list = []
        self._agg: Dict[str, Dict[str, Any]] = {}
        self._seq = 0

    def add(self, trade: Dict[str, Any]) -> None:
        import heapq
        heapq.heappush(self._pending, (trade["exit_date"], self._seq, trade))
        self._seq += 1

    def evidence_as_of(self, d_str: str) -> Dict[str, "StrategyEvidence"]:
        import heapq
        while self._pending and self._pending[0][0] < d_str:
            add_trade_to_aggregate(self._agg, heapq.heappop(self._pending)[2])
        return {k: evidence_from_aggregate(k, v, source="walk_forward", as_of=d_str) for k, v in self._agg.items()}


def evidence_from_aggregate(
    strategy: str,
    agg: Optional[Dict[str, Any]],
    source: str = "backtest",
    as_of: Optional[str] = None,
    alpha: float = BAYESIAN_SHRINKAGE_ALPHA,
) -> StrategyEvidence:
    key = normalize_strategy_key(strategy)
    if not agg or int(agg.get("trades", 0)) <= 0:
        return StrategyEvidence(strategy=key, as_of=as_of)
    n = int(agg["trades"])
    wins = int(agg["wins"])
    mean_ret = float(agg["sum_return"]) / n
    ba_n, ex_n = int(agg.get("beta_adjusted_n", 0) or 0), int(agg.get("excess_vs_spy_n", 0) or 0)
    return StrategyEvidence(
        strategy=key,
        trades=n,
        wins=wins,
        raw_win_rate=round(100.0 * wins / n, 2),
        raw_expectancy=round(mean_ret, 4),
        shrunk_win_rate=round((wins * 100.0 + alpha * BAYESIAN_PRIOR_WIN_RATE) / (n + alpha), 2),
        shrunk_expectancy=round((n * mean_ret + alpha * BAYESIAN_PRIOR_EXPECTANCY_PCT) / (n + alpha), 4),
        raw_beta_adjusted_expectancy=round(float(agg["sum_beta_adjusted"]) / ba_n, 4) if ba_n else None,
        raw_excess_vs_spy_expectancy=round(float(agg["sum_excess_vs_spy"]) / ex_n, 4) if ex_n else None,
        target_hits={
            "t1": (int(agg.get("t1_hits", 0)), int(agg.get("t1_n", 0))),
            "t2": (int(agg.get("t2_hits", 0)), int(agg.get("t2_n", 0))),
            "t3": (int(agg.get("t3_hits", 0)), int(agg.get("t3_n", 0))),
        },
        source=source,
        as_of=as_of,
    )


_EVIDENCE_CACHE: Optional[Dict[str, StrategyEvidence]] = None


def load_strategy_evidence(path: str = EVIDENCE_PATH, refresh: bool = False) -> Dict[str, StrategyEvidence]:
    """Load per-strategy evidence written by the backtest. Missing file -> neutral priors."""
    global _EVIDENCE_CACHE
    if _EVIDENCE_CACHE is not None and not refresh and path == EVIDENCE_PATH:
        return _EVIDENCE_CACHE
    evidence: Dict[str, StrategyEvidence] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        as_of = (data.get("metadata") or {}).get("evidence_as_of")
        for strat, agg in (data.get("strategy_evidence") or {}).items():
            ev = evidence_from_aggregate(strat, agg, source="backtest", as_of=as_of)
            evidence[ev.strategy] = ev
    except FileNotFoundError:
        logger.warning("Strategy evidence file %s not found; using neutral priors.", path)
    except Exception as e:
        logger.warning("Could not read strategy evidence %s (%s); using neutral priors.", path, e)
    if path == EVIDENCE_PATH:
        _EVIDENCE_CACHE = evidence
    return evidence


def get_strategy_evidence(strategy: str, evidence: Optional[Dict[str, StrategyEvidence]] = None) -> StrategyEvidence:
    key = normalize_strategy_key(strategy)
    pool = evidence if evidence is not None else load_strategy_evidence()
    return pool.get(key) or StrategyEvidence(strategy=key)


def apply_evidence_to_candidate(sig: Dict[str, Any], ev: StrategyEvidence) -> None:
    """Attach strategy evidence fields consumed by the composite ranker and persistence."""
    sig["winrate_score"] = float(ev.shrunk_win_rate)
    sig["win_rate"] = float(ev.shrunk_win_rate)
    sig["shrunk_win_rate"] = float(ev.shrunk_win_rate)
    sig["raw_win_rate"] = ev.raw_win_rate
    sig["expectancy_pct"] = float(ev.shrunk_expectancy)
    sig["shrunk_expectancy"] = float(ev.shrunk_expectancy)
    sig["raw_expectancy"] = ev.raw_expectancy
    sig["strategy_win_rate"] = ev.raw_win_rate
    sig["strategy_expectancy_pct"] = ev.raw_expectancy
    sig["strategy_trades"] = ev.trades
    sig["strategy_beta_adjusted_pct"] = ev.raw_beta_adjusted_expectancy   # display only
    sig["strategy_excess_vs_spy_pct"] = ev.raw_excess_vs_spy_expectancy   # display only
    sig["completed_trades"] = ev.trades
    sig["win_rate_provenance"] = ev.source
    sig["metric_source"] = f"strategy_{ev.source}"
    sig["metric_sample_size"] = ev.trades
    sig["metric_confidence"] = (
        "high" if ev.trades >= 100 else "medium" if ev.trades >= 30 else "low" if ev.trades >= 5 else "prior"
    )
