"""
Metrics Pipeline: Bayesian Shrinkage & Provenance Module
=========================================================
Implements Empirical Bayes Beta-Binomial shrinkage for historical win rates
and sample-stabilized expectancy for equities in the recommendation engine.

Guarantees:
1. Never treats missing metrics as 0% win rate (applies neutral 50.0% prior).
2. Prevents small-sample pathologies (e.g. 1/1 does not produce 100% win rate).
3. Preserves explicit provenance and confidence tagging.
4. Preserves raw metrics separately from shrunk metrics.
"""

import math
from typing import Optional, Dict, Any
from src.quant_config import (
    STRATEGY_HISTORICAL_EXPECTANCY,
    SURVIVORSHIP_BIAS_HAIRCUT,
    normalize_strategy_key,
)

# Canonical Bayesian Shrinkage Parameters
DEFAULT_SHRINKAGE_ALPHA: float = 5.0
DEFAULT_PRIOR_WIN_RATE: float = 50.0  # 50.0% neutral prior
DEFAULT_PRIOR_EXPECTANCY_PCT: float = 1.44  # Baseline historical swing expectancy


def calculate_shrunk_win_rate(
    wins: int,
    losses: int,
    alpha: float = DEFAULT_SHRINKAGE_ALPHA,
    prior_win_rate: float = DEFAULT_PRIOR_WIN_RATE,
) -> float:
    """
    Beta-Binomial Bayesian Shrinkage Formula:
    posterior_win_rate = (wins * 100.0 + alpha * prior_win_rate) / (wins + losses + alpha)
    """
    total = max(0, wins) + max(0, losses)
    if total == 0:
        return float(prior_win_rate)
    
    posterior = (wins * 100.0 + alpha * prior_win_rate) / (total + alpha)
    return round(float(posterior), 2)


def calculate_shrunk_expectancy(
    raw_expectancy: Optional[float],
    total_trades: int,
    strategy_name: Optional[str] = None,
    alpha: float = DEFAULT_SHRINKAGE_ALPHA,
) -> float:
    """
    Stabilize historical expectancy against small-sample variance by shrinking
    towards the strategy's canonical haircut-adjusted historical expectancy.
    """
    strat_key = normalize_strategy_key(strategy_name) if strategy_name else "trend_following"
    canonical_prior = STRATEGY_HISTORICAL_EXPECTANCY.get(
        strat_key, 0.0169 * SURVIVORSHIP_BIAS_HAIRCUT
    ) * 100.0  # e.g. 1.44%

    if raw_expectancy is None or total_trades <= 0:
        return round(float(canonical_prior), 2)

    try:
        raw_val = float(raw_expectancy)
        if not math.isfinite(raw_val):
            return round(float(canonical_prior), 2)
    except (ValueError, TypeError):
        return round(float(canonical_prior), 2)

    # Shrink raw expectancy toward canonical prior based on sample size
    shrunk = (total_trades * raw_val + alpha * canonical_prior) / (total_trades + alpha)
    return round(float(shrunk), 2)


def determine_metric_confidence(total_trades: int) -> str:
    """Classify statistical evidence confidence based on sample size."""
    if total_trades >= 30:
        return "high"
    elif total_trades >= 10:
        return "medium"
    elif total_trades >= 5:
        return "low"
    else:
        return "prior"


def build_hardened_metrics(
    ticker: str,
    raw_record: Optional[Dict[str, Any]] = None,
    strategy_name: Optional[str] = None,
    strategy_win_rate: Optional[float] = None,
    past_win_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Builds a complete, hardened metrics dictionary with Bayesian shrinkage,
    raw-vs-shrunk separation, and explicit provenance.
    """
    raw_m = raw_record or {}
    wins = int(raw_m.get("wins") or 0)
    losses = int(raw_m.get("losses") or 0)
    completed_trades = wins + losses
    total_signals = int(raw_m.get("total_signals") or raw_m.get("total_trades") or completed_trades)

    # Validate strategy_win_rate if provided
    valid_strat_wr = None
    if strategy_win_rate is not None:
        try:
            val = float(strategy_win_rate)
            if math.isfinite(val) and 0.0 <= val <= 100.0:
                valid_strat_wr = val
        except (ValueError, TypeError):
            pass

    # Validate past_win_rate if provided
    valid_past_wr = None
    if past_win_rate is not None:
        try:
            val = float(past_win_rate)
            if math.isfinite(val) and 0.0 <= val <= 100.0:
                valid_past_wr = val
        except (ValueError, TypeError):
            pass

    # Determine prior and provenance
    if valid_strat_wr is not None:
        eff_prior = valid_strat_wr
        if completed_trades > 0:
            provenance = "ticker_observed"
            metric_source = "ticker_observed_with_strategy_prior"
        else:
            provenance = "strategy_prior"
            metric_source = "strategy_backtest"
    elif valid_past_wr is not None:
        eff_prior = valid_past_wr
        if completed_trades > 0:
            provenance = "ticker_observed"
            metric_source = "ticker_observed_with_candidate_prior"
        else:
            provenance = "candidate_provided"
            metric_source = "candidate_payload"
    elif completed_trades > 0:
        eff_prior = DEFAULT_PRIOR_WIN_RATE
        provenance = "ticker_observed"
        metric_source = "ticker_historical_trades"
    elif "win_rate" in raw_m and raw_m["win_rate"] is not None:
        try:
            wr_val = float(raw_m["win_rate"])
            if math.isfinite(wr_val) and 0.0 <= wr_val <= 100.0:
                eff_prior = wr_val
                provenance = "generic_ticker_prior"
                metric_source = "ticker_metrics"
            else:
                eff_prior = DEFAULT_PRIOR_WIN_RATE
                provenance = "unavailable"
                metric_source = "unseeded_prior"
        except (ValueError, TypeError):
            eff_prior = DEFAULT_PRIOR_WIN_RATE
            provenance = "unavailable"
            metric_source = "unseeded_prior"
    else:
        eff_prior = DEFAULT_PRIOR_WIN_RATE
        provenance = "unavailable"
        metric_source = "unseeded_prior"

    # Compute raw win rate (empirical observations only)
    if completed_trades > 0:
        raw_win_rate = round((wins / completed_trades) * 100.0, 2)
    else:
        raw_win_rate = round(eff_prior, 2)

    # Compute Bayesian shrunk win rate (posterior)
    if completed_trades > 0:
        shrunk_wr = calculate_shrunk_win_rate(
            wins=wins,
            losses=losses,
            alpha=DEFAULT_SHRINKAGE_ALPHA,
            prior_win_rate=eff_prior,
        )
    else:
        # Zero trade observations: sample_size=0, no synthetic pseudo-counts;
        # posterior equals authoritative prior directly.
        shrunk_wr = round(float(eff_prior), 2)

    # Raw vs shrunk expectancy
    raw_exp = raw_m.get("expectancy_pct")
    if raw_exp is not None:
        raw_exp = float(raw_exp)
    shrunk_exp = calculate_shrunk_expectancy(
        raw_expectancy=raw_exp,
        total_trades=completed_trades,
        strategy_name=strategy_name,
        alpha=DEFAULT_SHRINKAGE_ALPHA,
    )

    confidence = determine_metric_confidence(completed_trades)

    return {
        "ticker": ticker.upper(),
        "raw_win_rate": raw_win_rate,
        "shrunk_win_rate": shrunk_wr,
        "win_rate": shrunk_wr,  # Consumed downstream by ranker and strategies
        "raw_expectancy": raw_exp,
        "shrunk_expectancy": shrunk_exp,
        "expectancy_pct": shrunk_exp,  # Consumed downstream
        "wins": wins,
        "losses": losses,
        "sample_size": completed_trades,
        "prior": round(float(eff_prior), 2),
        "posterior": round(float(shrunk_wr), 2),
        "confidence": confidence,
        "source": metric_source,
        "provenance": provenance,
        "completed_trades": completed_trades,
        "total_trades": total_signals,
        "metric_source": metric_source,
        "metric_confidence": confidence,
        "metric_sample_size": completed_trades,
        "win_rate_provenance": provenance,
        "insufficient_sample": completed_trades < 5,
        "median_win_return": float(raw_m.get("median_win_return") or 0.0),
        "median_holding_days": float(raw_m.get("median_holding_days") or 0.0),
    }
