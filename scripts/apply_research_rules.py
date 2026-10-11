"""
Apply the pre-registered keep/kill rules (CLAUDE.md, "Profitability program")
=============================================================================
Reads a backtest run's variant results (production_backtest_summary*.json with selection_variants)
and applies a rule mechanically:

  R1 (risk-control filters, Phase 2): ADOPT unless the FULL-period paired diff vs production has a
     95% CI entirely below zero (then KILL). If the point estimate is below -0.10 in both FULL and
     LATE but the CI includes zero: ADOPT, flagged "costs return; kept as a risk rule".
  R2 (selection / structure, Phases 4-6): ADOPT only if the paired diff vs production is > 0 in
     both FULL and LATE, the FULL CI excludes zero, and the Holm-adjusted one-sided p-value within
     the phase is < 0.05. If several pass: the largest FULL gain (ties: fewer parameters).

Metric: beta-adjusted %/idea (paired_block_diff, signal-month block bootstrap). Writes
outputs/research/<phase>_<rule>_decisions.json and prints the table.

Usage:
  python scripts/apply_research_rules.py --rule R1 --phase phase2 --summary <summary.json> \
      --variants earnings_full_hold,event_lock,rr_floor_stop_cap,hygiene_all
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.research.stats import holm  # noqa: E402

FLAG_THRESHOLD = -0.10


def r1(entry: Dict[str, Any]) -> Dict[str, Any]:
    full = entry["vs_production"]["beta_adjusted_pct"]
    late = entry["vs_production"]["late_beta_adjusted_pct"]
    if full.get("ci95") is None:
        return {"decision": "INSUFFICIENT_SAMPLE", "reason": "paired diff needs >= 30 ideas in both books"}
    lo, hi = full["ci95"]
    if hi < 0:
        return {"decision": "KILL", "reason": f"FULL paired diff {full['diff']} CI [{lo}, {hi}] entirely below zero"}
    flag = (full["diff"] is not None and full["diff"] < FLAG_THRESHOLD and late.get("diff") is not None
            and late["diff"] < FLAG_THRESHOLD)
    return {"decision": "ADOPT",
            "reason": f"FULL paired diff {full['diff']} CI [{lo}, {hi}] not entirely below zero",
            "flag": "costs return; kept as a risk rule" if flag else None}


def r2(entry: Dict[str, Any], holm_p: Optional[float]) -> Dict[str, Any]:
    full = entry["vs_production"]["beta_adjusted_pct"]
    late = entry["vs_production"]["late_beta_adjusted_pct"]
    if full.get("ci95") is None or late.get("diff") is None:
        return {"decision": "INSUFFICIENT_SAMPLE", "reason": "paired diff needs >= 30 ideas in both books"}
    checks = {
        "full_gt0": full["diff"] > 0,
        "late_gt0": late["diff"] > 0,
        "full_ci_excludes_zero": full["ci95"][0] > 0,
        "survives_holm": holm_p is not None and holm_p < 0.05,
    }
    ok = all(checks.values())
    return {"decision": "PASS" if ok else "KILL", "checks": checks, "holm_adjusted_p_gt0": holm_p}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", choices=("R1", "R2"), required=True)
    ap.add_argument("--phase", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--variants", required=True)
    ap.add_argument("--params", default="", help="R2 tie-break: name=count,... (number of parameters per variant)")
    args = ap.parse_args()

    with open(args.summary, encoding="utf-8") as f:
        summary = json.load(f)
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    book = summary.get("selection_variants") or {}
    missing = [v for v in variants if v not in book]
    if missing:
        print(f"Variants missing from the run: {missing}")
        return 1
    prod = book.get("production") or {}

    p_gt0 = [book[v]["vs_production"]["beta_adjusted_pct"].get("p_gt0") for v in variants]
    valid = [(v, p) for v, p in zip(variants, p_gt0) if p is not None]
    holm_adj = dict(zip([v for v, _ in valid], holm([p for _, p in valid]))) if valid else {}

    rows: List[Dict[str, Any]] = []
    for v in variants:
        e = book[v]
        decision = r1(e) if args.rule == "R1" else r2(e, holm_adj.get(v))
        rows.append({
            "variant": v,
            "ideas": e["all_trades"].get("trade_count"),
            "production_ideas": prod.get("all_trades", {}).get("trade_count"),
            "mean_beta_adjusted_pct": e["all_trades"].get("mean_beta_adjusted_pct"),
            "ci95": e["all_trades"].get("beta_adjusted_ci95_block_bootstrap"),
            "late_mean_beta_adjusted_pct": e["late_period"].get("mean_beta_adjusted_pct"),
            "paired_full": e["vs_production"]["beta_adjusted_pct"],
            "paired_late": e["vs_production"]["late_beta_adjusted_pct"],
            "holm_adjusted_p_gt0": None if v not in holm_adj else round(holm_adj[v], 4),
            "placebo_percentile": e.get("placebo_percentile"),
            "left_tail": e.get("left_tail"),
            "production_left_tail": prod.get("left_tail"),
            "funnel": e.get("funnel"),
            **decision,
        })

    adopted: List[str] = []
    if args.rule == "R1":
        adopted = [r["variant"] for r in rows if r["decision"] == "ADOPT"]
    else:
        passing = [r for r in rows if r["decision"] == "PASS"]
        if passing:
            n_params = {k: int(x) for k, x in (kv.split("=") for kv in args.params.split(",") if "=" in kv)}
            best = sorted(passing, key=lambda r: (-r["paired_full"]["diff"], n_params.get(r["variant"], 0)))[0]
            adopted = [best["variant"]]
            for r in rows:
                if r["decision"] == "PASS":
                    r["decision"] = "ADOPT" if r["variant"] == best["variant"] else "PASS_NOT_CHOSEN"

    out = {"rule": args.rule, "phase": args.phase, "summary": os.path.relpath(args.summary, PROJECT_ROOT),
           "evaluation_window": summary.get("metadata", {}).get("evaluation_window"),
           "production": {"ideas": prod.get("all_trades", {}).get("trade_count"),
                          "mean_beta_adjusted_pct": prod.get("all_trades", {}).get("mean_beta_adjusted_pct"),
                          "ci95": prod.get("all_trades", {}).get("beta_adjusted_ci95_block_bootstrap"),
                          "late_mean_beta_adjusted_pct": prod.get("late_period", {}).get("mean_beta_adjusted_pct")},
           "multiple_testing": summary.get("multiple_testing"),
           "rows": rows, "adopted": adopted}
    path = os.path.join(PROJECT_ROOT, "outputs", "research", f"{args.phase}_{args.rule}_decisions.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(f"{args.rule} ({args.phase}); production: {out['production']}")
    for r in rows:
        print(f"  {r['variant']:22s} ideas {r['ideas']:>6} | beta-adj {r['mean_beta_adjusted_pct']} CI {r['ci95']} | "
              f"late {r['late_mean_beta_adjusted_pct']} | paired full {r['paired_full'].get('diff')} "
              f"{r['paired_full'].get('ci95')} late {r['paired_late'].get('diff')} {r['paired_late'].get('ci95')} | "
              f"Holm p {r['holm_adjusted_p_gt0']} -> {r['decision']}" + (f" [{r.get('flag')}]" if r.get("flag") else ""))
    print("Adopted:", adopted or "none")
    print("Wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
