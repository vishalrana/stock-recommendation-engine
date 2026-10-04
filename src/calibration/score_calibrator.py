"""
Score Outcome Calibration Module
=================================
Builds an empirical score-to-outcome calibration layer from historical signals.

Implements:
1. Score bands: 50-54.99, 55-59.99, 60-64.99, 65-69.99, 70-74.99, 75-79.99, 80+.
2. Empirical calculation of P(T1), P(T2), P(T3), P(stop), P(positive outcome),
   mean return, and median return.
3. Fallback hierarchy:
   (1) Strategy + Score Band + Regime (if sample >= 20)
   (2) Strategy + Score Band (if sample >= 15)
   (3) Score Band (if sample >= 10)
   (4) Canonical Bayesian Prior (Beta-Binomial smoothed)
4. Strict bounds [0.0, 1.0], monotonicity P(T1) >= P(T2) >= P(T3), and no NaNs/None.
5. Explicit "INSUFFICIENT SAMPLE" reporting when evidence is sparse.
"""

import math
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import pandas as pd

from src.quant_config import (
    SCORE_CALIBRATION_BANDS,
    STRATEGY_TARGET_CONFIG,
    normalize_strategy_key,
)

# Canonical Bayesian prior parameters for target reach probabilities
CANONICAL_TARGET_PRIORS: Dict[str, Dict[str, float]] = {
    "trend_following":          {"t1": 0.58, "t2": 0.35, "t3": 0.20, "stop": 0.32, "pos": 0.62},
    "52w_high_breakout":        {"t1": 0.60, "t2": 0.38, "t3": 0.22, "stop": 0.30, "pos": 0.64},
    "pullback_recovery":        {"t1": 0.56, "t2": 0.32, "t3": 0.18, "stop": 0.35, "pos": 0.60},
    "pead":                     {"t1": 0.62, "t2": 0.40, "t3": 0.24, "stop": 0.28, "pos": 0.66},
    "cross_sectional_momentum": {"t1": 0.57, "t2": 0.34, "t3": 0.19, "stop": 0.33, "pos": 0.61},
    "sector_rotation":          {"t1": 0.55, "t2": 0.30, "t3": 0.16, "stop": 0.34, "pos": 0.59},
    "mean_reversion":           {"t1": 0.52, "t2": 0.28, "t3": 0.15, "stop": 0.38, "pos": 0.55},
}

GLOBAL_CANONICAL_PRIOR: Dict[str, float] = {
    "t1": 0.57,
    "t2": 0.34,
    "t3": 0.19,
    "stop": 0.33,
    "pos": 0.61,
}

MIN_SAMPLE_REGIME_STRATEGY: int = 20
MIN_SAMPLE_STRATEGY: int = 15
MIN_SAMPLE_SCORE_BAND: int = 10
SMOOTHING_ALPHA: float = 5.0


def find_score_band(score: float) -> str:
    """Map a composite score (0-100) to its canonical score band string."""
    sc = float(score)
    if sc < 50.0:
        return "<50"
    for b in SCORE_CALIBRATION_BANDS:
        if b["min"] <= sc <= b["max"] or (b["band"] == "80+" and sc >= 80.0):
            return b["band"]
    return "80+" if sc >= 80.0 else "<50"


class ScoreCalibrator:
    """
    Calibrates score-to-outcome probabilities using historical records
    with Bayesian shrinkage and hierarchical fallback.
    """

    def __init__(self, records: Optional[List[Dict[str, Any]]] = None):
        self.records = records or []
        self.band_stats: Dict[str, Dict[str, Any]] = {}
        self.strat_band_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.strat_band_regime_stats: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        if self.records:
            self._fit()

    def _fit(self):
        """Aggregate outcomes across score bands, strategies, and regimes."""
        # Completed / matured outcomes only
        matured = [
            r for r in self.records
            if r.get("outcome") in ("hit_t1", "hit_t2", "hit_t3", "stopped", "expired")
            and r.get("composite_score") is not None
        ]

        def _calc_stats(group_records: List[Dict[str, Any]]) -> Dict[str, Any]:
            n = len(group_records)
            if n == 0:
                return {"sample": 0}
            
            t1_hits = sum(1 for r in group_records if r.get("outcome") in ("hit_t1", "hit_t2", "hit_t3"))
            t2_hits = sum(1 for r in group_records if r.get("outcome") in ("hit_t2", "hit_t3"))
            t3_hits = sum(1 for r in group_records if r.get("outcome") == "hit_t3")
            stopped = sum(1 for r in group_records if r.get("outcome") == "stopped")
            returns = [float(r["outcome_return_pct"]) for r in group_records if r.get("outcome_return_pct") is not None]
            pos_hits = sum(1 for ret in returns if ret > 0)

            return {
                "sample": n,
                "t1_hits": t1_hits,
                "t2_hits": t2_hits,
                "t3_hits": t3_hits,
                "stopped": stopped,
                "pos_hits": pos_hits,
                "p_t1_raw": t1_hits / n,
                "p_t2_raw": t2_hits / n,
                "p_t3_raw": t3_hits / n,
                "p_stop_raw": stopped / n,
                "p_pos_raw": pos_hits / max(1, len(returns)),
                "mean_return": float(np.mean(returns)) if returns else 0.0,
                "median_return": float(np.median(returns)) if returns else 0.0,
            }

        # 1. Band stats
        by_band: Dict[str, List[Dict[str, Any]]] = {}
        for r in matured:
            b = find_score_band(float(r["composite_score"]))
            by_band.setdefault(b, []).append(r)
        
        for b_dict in SCORE_CALIBRATION_BANDS:
            b_name = b_dict["band"]
            self.band_stats[b_name] = _calc_stats(by_band.get(b_name, []))

        # 2. Strategy + Band stats
        by_strat_band: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for r in matured:
            strat = normalize_strategy_key(r.get("strategy", ""))
            b = find_score_band(float(r["composite_score"]))
            by_strat_band.setdefault((strat, b), []).append(r)
        
        for k, recs in by_strat_band.items():
            self.strat_band_stats[k] = _calc_stats(recs)

        # 3. Strategy + Band + Regime stats
        by_sbr: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for r in matured:
            strat = normalize_strategy_key(r.get("strategy", ""))
            b = find_score_band(float(r["composite_score"]))
            reg = str(r.get("regime", "")).lower()
            by_sbr.setdefault((strat, b, reg), []).append(r)
        
        for k, recs in by_sbr.items():
            self.strat_band_regime_stats[k] = _calc_stats(recs)

    def get_outcome_probabilities(
        self,
        score: float,
        strategy_name: Optional[str] = None,
        regime: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Hierarchical outcome probability lookup with Bayesian smoothing.
        Hierarchy:
        1. strategy + band + regime (if sample >= 20)
        2. strategy + band (if sample >= 15)
        3. score band (if sample >= 10)
        4. canonical prior (Beta-Binomial smoothed)
        """
        strat_key = normalize_strategy_key(strategy_name) if strategy_name else "trend_following"
        band = find_score_band(score)
        reg_key = str(regime).strip().lower() if regime else ""

        # Prior for strategy
        prior = CANONICAL_TARGET_PRIORS.get(strat_key, GLOBAL_CANONICAL_PRIOR)

        # Check hierarchy
        sbr_key = (strat_key, band, reg_key)
        sb_key = (strat_key, band)
        
        stats = None
        tier_used = "canonical_prior"
        
        if sbr_key in self.strat_band_regime_stats and self.strat_band_regime_stats[sbr_key]["sample"] >= MIN_SAMPLE_REGIME_STRATEGY:
            stats = self.strat_band_regime_stats[sbr_key]
            tier_used = "strategy_band_regime"
        elif sb_key in self.strat_band_stats and self.strat_band_stats[sb_key]["sample"] >= MIN_SAMPLE_STRATEGY:
            stats = self.strat_band_stats[sb_key]
            tier_used = "strategy_band"
        elif band in self.band_stats and self.band_stats[band]["sample"] >= MIN_SAMPLE_SCORE_BAND:
            stats = self.band_stats[band]
            tier_used = "score_band"

        if stats and stats["sample"] >= MIN_SAMPLE_SCORE_BAND:
            n = stats["sample"]
            # Beta-Binomial shrinkage towards canonical prior
            p_t1 = (stats["t1_hits"] + SMOOTHING_ALPHA * prior["t1"]) / (n + SMOOTHING_ALPHA)
            p_t2 = (stats["t2_hits"] + SMOOTHING_ALPHA * prior["t2"]) / (n + SMOOTHING_ALPHA)
            p_t3 = (stats["t3_hits"] + SMOOTHING_ALPHA * prior["t3"]) / (n + SMOOTHING_ALPHA)
            p_stop = (stats["stopped"] + SMOOTHING_ALPHA * prior["stop"]) / (n + SMOOTHING_ALPHA)
            p_pos = (stats["pos_hits"] + SMOOTHING_ALPHA * prior["pos"]) / (n + SMOOTHING_ALPHA)
            sample_size = n
            mean_ret = stats["mean_return"]
            median_ret = stats["median_return"]
        else:
            # Fallback directly to canonical Bayesian prior
            p_t1 = prior["t1"]
            p_t2 = prior["t2"]
            p_t3 = prior["t3"]
            p_stop = prior["stop"]
            p_pos = prior["pos"]
            sample_size = stats["sample"] if stats else 0
            mean_ret = 0.0
            median_ret = 0.0

        # Monotonicity enforcement: P(T1) >= P(T2) >= P(T3)
        p_t1 = max(0.0, min(1.0, float(p_t1)))
        p_t2 = max(0.0, min(p_t1, float(p_t2)))
        p_t3 = max(0.0, min(p_t2, float(p_t3)))
        p_stop = max(0.0, min(1.0, float(p_stop)))
        p_pos = max(0.0, min(1.0, float(p_pos)))

        return {
            "score": round(float(score), 2),
            "score_band": band,
            "strategy": strat_key,
            "p_t1": round(p_t1, 4),
            "p_t2": round(p_t2, 4),
            "p_t3": round(p_t3, 4),
            "p_stop": round(p_stop, 4),
            "p_positive": round(p_pos, 4),
            "mean_return": round(mean_ret, 2),
            "median_return": round(median_ret, 2),
            "sample_size": sample_size,
            "calibration_tier": tier_used,
            "is_sufficient_sample": sample_size >= MIN_SAMPLE_SCORE_BAND,
        }

    def generate_score_outcome_table(self) -> List[Dict[str, Any]]:
        """
        Generate empirical table rows for the required Section 22 report.
        If a band has insufficient sample size, values are marked 'INSUFFICIENT SAMPLE'.
        """
        rows = []
        for b_dict in SCORE_CALIBRATION_BANDS:
            band_str = b_dict["band"]
            st = self.band_stats.get(band_str, {"sample": 0})
            sample = st["sample"]
            
            if sample < MIN_SAMPLE_SCORE_BAND:
                rows.append({
                    "band": band_str,
                    "sample": sample,
                    "t1": "INSUFFICIENT SAMPLE",
                    "t2": "INSUFFICIENT SAMPLE",
                    "t3": "INSUFFICIENT SAMPLE",
                    "stop": "INSUFFICIENT SAMPLE",
                    "positive": "INSUFFICIENT SAMPLE",
                    "status": "INSUFFICIENT SAMPLE",
                })
            else:
                rows.append({
                    "band": band_str,
                    "sample": sample,
                    "t1": f"{st['p_t1_raw']:.1%}",
                    "t2": f"{st['p_t2_raw']:.1%}",
                    "t3": f"{st['p_t3_raw']:.1%}",
                    "stop": f"{st['p_stop_raw']:.1%}",
                    "positive": f"{st['p_pos_raw']:.1%}",
                    "status": "VALIDATED",
                })
        return rows
