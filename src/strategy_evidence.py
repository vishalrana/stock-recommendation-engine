"""
Strategy Evidence
=================
Per-strategy win rate, expectancy and target-hit rates measured by the production-pipeline
backtest (scripts/validate_backtest_pipeline.py), which writes config/strategy_performance.json.

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
        key = normalize_strategy_key(t["strategy"])
        a = agg.setdefault(key, {
            "trades": 0, "wins": 0, "sum_return": 0.0,
            "t1_hits": 0, "t1_n": 0, "t2_hits": 0, "t2_n": 0, "t3_hits": 0, "t3_n": 0,
        })
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
    return agg


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
    return StrategyEvidence(
        strategy=key,
        trades=n,
        wins=wins,
        raw_win_rate=round(100.0 * wins / n, 2),
        raw_expectancy=round(mean_ret, 4),
        shrunk_win_rate=round((wins * 100.0 + alpha * BAYESIAN_PRIOR_WIN_RATE) / (n + alpha), 2),
        shrunk_expectancy=round((n * mean_ret + alpha * BAYESIAN_PRIOR_EXPECTANCY_PCT) / (n + alpha), 4),
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
    sig["completed_trades"] = ev.trades
    sig["win_rate_provenance"] = ev.source
    sig["metric_source"] = f"strategy_{ev.source}"
    sig["metric_sample_size"] = ev.trades
    sig["metric_confidence"] = (
        "high" if ev.trades >= 100 else "medium" if ev.trades >= 30 else "low" if ev.trades >= 5 else "prior"
    )
