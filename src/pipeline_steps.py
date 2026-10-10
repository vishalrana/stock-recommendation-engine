"""
Shared Pipeline Steps
=====================
Candidate scoring and trade-plan construction used by BOTH the nightly scan
(jobs/generate_signals.py) and the backtest (scripts/validate_backtest_pipeline.py),
so the backtest measures exactly the logic that produces live recommendations.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from src import quant_config
from src.quant_config import normalize_strategy_key
from src.ranker import (
    compute_momentum_score,
    compute_expectancy_score,
    compute_regime_alignment,
    validate_candidate_features,
)
from src.strategy_evidence import StrategyEvidence, get_strategy_evidence, apply_evidence_to_candidate
from src.strategies.target_calculator import calculate_targets, TargetCalculationResult

logger = logging.getLogger(__name__)

BUY_THRESHOLD: float = 65.0

MOMENTUM_MODELS = ("technical", "relative_strength_12m")
ENTRY_LOCATION_MODES = ("gate", "info")
CONTEXT_COMPONENTS = ("analyst", "fundamental", "news")


@dataclass(frozen=True)
class SelectionSettings:
    """Selection switches (src.quant_config); the backtest can evaluate other combinations."""
    momentum_model: str
    entry_location_mode: str
    context_components: Tuple[str, ...]
    name: str = "production"

    def __post_init__(self):
        if self.momentum_model not in MOMENTUM_MODELS:
            raise ValueError(f"Unknown momentum model {self.momentum_model!r}; expected one of {MOMENTUM_MODELS}")
        if self.entry_location_mode not in ENTRY_LOCATION_MODES:
            raise ValueError(f"Unknown entry-location mode {self.entry_location_mode!r}; expected one of {ENTRY_LOCATION_MODES}")
        unknown = set(self.context_components) - set(CONTEXT_COMPONENTS)
        if unknown:
            raise ValueError(f"Unknown context components {sorted(unknown)}; expected a subset of {CONTEXT_COMPONENTS}")

    def blocks_entry(self, entry_state: Optional[str]) -> bool:
        """True when the entry-location verdict stops the idea from being issued."""
        return self.entry_location_mode == "gate" and entry_state in ("WAIT", "REJECT")


def selection_settings() -> SelectionSettings:
    return SelectionSettings(
        momentum_model=quant_config.MOMENTUM_MODEL,
        entry_location_mode=quant_config.ENTRY_LOCATION_MODE,
        context_components=tuple(quant_config.CONTEXT_SCORE_COMPONENTS),
    )


def relative_strength_percentiles(
    closes: pd.DataFrame,
    eligible: Optional[pd.DataFrame] = None,
    lookback: Optional[int] = None,
    skip: Optional[int] = None,
) -> pd.DataFrame:
    """
    Cross-sectional percentile (0-100] of the 12-1 month return on each date.

    closes:   close prices indexed by trading date, one column per ticker, on a shared calendar.
    eligible: optional boolean frame; only True cells count in that date's cross-section.
    The return runs from `lookback` bars ago to `skip` bars ago (t-252 -> t-21): the latest month
    is skipped because one-month returns tend to reverse. Uses only bars up to each date.
    """
    lookback = quant_config.RS_LOOKBACK_BARS if lookback is None else lookback
    skip = quant_config.RS_SKIP_BARS if skip is None else skip
    ret = closes.shift(skip) / closes.shift(lookback) - 1.0
    ret = ret.replace([np.inf, -np.inf], np.nan)
    if eligible is not None:
        ret = ret.where(eligible.reindex(index=ret.index, columns=ret.columns).eq(True))
    return ret.rank(axis=1, pct=True, method="average") * 100.0


def prepare_candidate_scores(
    sig: Dict[str, Any],
    regime: str,
    evidence: Optional[Dict[str, StrategyEvidence]] = None,
    settings: Optional[SelectionSettings] = None,
) -> StrategyEvidence:
    """
    Attach momentum, strategy-evidence (win rate / expectancy) and regime sub-scores.
    With the relative-strength model the momentum sub-score is the candidate's precomputed
    `rs_percentile_12m`; without it the candidate fails closed in composite_score().
    """
    settings = settings or selection_settings()
    sig["momentum_model"] = settings.momentum_model
    if settings.momentum_model == "relative_strength_12m":
        rs = sig.get("rs_percentile_12m")
        sig["momentum_score"] = round(float(rs), 4) if rs is not None and np.isfinite(rs) else None
    else:
        try:
            sig["momentum_score"] = compute_momentum_score(sig)
        except Exception as m_err:
            logger.warning(f"Could not compute momentum score for {sig.get('ticker')}: {m_err}")
            sig["momentum_score"] = None

    strategy = sig.get("strategy")
    ev = get_strategy_evidence(strategy, evidence)
    apply_evidence_to_candidate(sig, ev)
    sig["expectancy_score"] = compute_expectancy_score(strategy, ev.shrunk_expectancy)
    sig["regime_score"] = compute_regime_alignment(strategy, regime)
    return ev


def composite_score(sig: Dict[str, Any], regime: str, ranker) -> Optional[str]:
    """
    Validate features and compute the composite score in place.
    Returns None on success, or a rejection reason.
    Context fields (or context_available=False) must already be set.
    """
    if sig.get("momentum_model") == "relative_strength_12m" and sig.get("momentum_score") is None:
        return "Validation failed: no 12-1 month relative strength (needs 253 bars of history)"
    is_valid_feat, feat_msg = validate_candidate_features(sig)
    if not is_valid_feat:
        return f"Validation failed: {feat_msg}"
    try:
        res = ranker.compute_composite_score(sig, regime)
    except Exception as score_err:
        return f"Scoring error: {score_err}"
    sig["composite_score"] = round(res["total"], 4)
    sig["quality_score"] = round(res["total"], 4)
    sig["score_breakdown"] = res["breakdown"]
    return None


def build_trade_plan(
    sig: Dict[str, Any],
    price_df,
    evidence: Optional[Dict[str, StrategyEvidence]] = None,
    as_of_date: Optional[str] = None,
) -> TargetCalculationResult:
    """
    Targets, reach probabilities and scale-out plan from the strategy's own stop (used as
    issued, no clamping). Writes the trade-plan fields onto the candidate when valid.
    """
    ev = get_strategy_evidence(sig["strategy"], evidence)
    calc_res = calculate_targets(
        ticker=sig["ticker"],
        entry_price=float(sig["entry_price"]),
        atr_14=float(sig.get("atr_14", 0.0) or 0.0),
        stop_loss=float(sig["stop_loss"]),
        strategy_name=sig["strategy"],
        price_df=price_df,
        sector=sig.get("sector") or sig.get("industry"),
        as_of_date=as_of_date,
        strategy_target_hits=ev.target_hits or None,
    )
    if not calc_res.is_valid:
        return calc_res

    for k in (
        "target_1", "target_2", "target_3",
        "target_1_atr", "target_2_atr", "target_3_atr",
        "target_1_pct", "target_2_pct", "target_3_pct",
        "reach_prob_t1", "reach_prob_t2", "reach_prob_t3",
        "reach_prob_raw", "reach_prob_adjusted",
        "reach_prob_t1_ci_low", "reach_prob_t1_ci_high", "reach_prob_effective_samples",
        "reach_prob_source", "scale_out_weights", "weighted_scaleout_rr",
    ):
        sig[k] = getattr(calc_res, k)
    sig["weighted_rr"] = calc_res.weighted_scaleout_rr
    sig["weighted_rr_honest"] = calc_res.weighted_scaleout_rr

    # The strategy's own fixed-percentage exit/upside/R:R are superseded by the canonical
    # targets; keep the summary fields consistent with them.
    final_target, final_target_pct = next(
        (t, p) for t, p in (
            (calc_res.target_3, calc_res.target_3_pct),
            (calc_res.target_2, calc_res.target_2_pct),
            (calc_res.target_1, calc_res.target_1_pct),
        ) if t is not None
    )
    sig["exit_price"] = final_target
    sig["upside_pct"] = final_target_pct
    sig["risk_reward"] = calc_res.weighted_scaleout_rr
    return calc_res


def holding_days_for(strategy: Optional[str]) -> Optional[int]:
    """Strategy holding period (trading days) after which an open idea expires."""
    from src.quant_config import STRATEGY_TARGET_CONFIG
    try:
        return int(STRATEGY_TARGET_CONFIG[normalize_strategy_key(strategy)]["hold_days"])
    except Exception:
        return None
