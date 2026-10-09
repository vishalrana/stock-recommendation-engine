"""
Shared Pipeline Steps
=====================
Candidate scoring and trade-plan construction used by BOTH the nightly scan
(jobs/generate_signals.py) and the backtest (scripts/validate_backtest_pipeline.py),
so the backtest measures exactly the logic that produces live recommendations.
"""

import logging
from typing import Any, Dict, Optional

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


def prepare_candidate_scores(
    sig: Dict[str, Any],
    regime: str,
    evidence: Optional[Dict[str, StrategyEvidence]] = None,
) -> StrategyEvidence:
    """Attach momentum, strategy-evidence (win rate / expectancy) and regime sub-scores."""
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
