"""
Adopt production-universe backtest evidence
===========================================
Rebuilds config/strategy_performance.json from a production-universe backtest artifact
(GitHub workflow backtest_production_universe.yml, downloaded with `gh run download`).

The per-strategy aggregates are recomputed from the artifact's setup-trades CSV with
src.strategy_evidence.aggregate_trades. Every field the run itself wrote (its
strategy_performance_production_universe.json) must match exactly, or nothing is written. The
rebuilt aggregates add the display-only beta-adjusted and vs-SPY sums and counts.

Usage:
  python scripts/adopt_strategy_evidence.py outputs/production_universe_run_<run id> --run-id <run id> [--check-only]

It also writes per-strategy stats of the run's issued ideas (n, distinct signal months, win rate,
net and beta-adjusted expectancy with signal-month block-bootstrap 95% CIs) under
issued_ideas_by_strategy, and copies them to frontend/src/lib/issued-evidence.json for the cards.
Display only: scoring never reads them. --issued-only writes just the cards' file (the scoring
evidence stays as adopted).
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.strategy_evidence import EVIDENCE_PATH, aggregate_trades  # noqa: E402

ARTIFACT_EVIDENCE = "strategy_performance_production_universe.json"
ARTIFACT_SETUPS = "production_backtest_setup_trades_production_universe.csv"
ARTIFACT_TRADES = "production_backtest_trades_production_universe.csv"
FRONTEND_ISSUED = os.path.join(PROJECT_ROOT, "frontend", "src", "lib", "issued-evidence.json")
TRADE_FIELDS = ("strategy", "net_return_pct", "outcome", "has_t2", "has_t3", "beta_adjusted_pct", "excess_vs_spy_pct")
# Display-only aggregates (expected R, exit mix) use these when the artifact has them (runs since 2026-10-11)
OPTIONAL_FIELDS = ("stop_pct", "exit_reason")


def rebuild(artifact_dir: str):
    """Return (artifact evidence doc, rebuilt aggregates, list of mismatches)."""
    with open(os.path.join(artifact_dir, ARTIFACT_EVIDENCE), encoding="utf-8") as f:
        doc = json.load(f)
    setups = pd.read_csv(os.path.join(artifact_dir, ARTIFACT_SETUPS))
    missing = [c for c in TRADE_FIELDS if c not in setups.columns]
    if missing:
        raise ValueError(f"Setup-trades CSV lacks columns {missing}")
    # Same row order as the run's shadow-trade list, so float sums add up in the same order
    fields = list(TRADE_FIELDS) + [c for c in OPTIONAL_FIELDS if c in setups.columns]
    records = setups[fields].to_dict("records")
    for r in records:
        for k in ("has_t2", "has_t3"):
            r[k] = bool(r[k]) if not pd.isna(r[k]) else False
        for k in ("beta_adjusted_pct", "excess_vs_spy_pct", "stop_pct", "exit_reason"):
            if k in r and pd.isna(r[k]):
                r[k] = None
    rebuilt = aggregate_trades(records)

    original = doc.get("strategy_evidence") or {}
    mismatches = []
    if set(original) != set(rebuilt):
        mismatches.append(f"strategies differ: artifact {sorted(original)} vs rebuilt {sorted(rebuilt)}")
    for strat, fields in original.items():
        for key, value in fields.items():
            got = rebuilt.get(strat, {}).get(key)
            if got != value:
                mismatches.append(f"{strat}.{key}: artifact {value!r} vs rebuilt {got!r}")
    return doc, rebuilt, mismatches


def issued_by_strategy(artifact_dir: str) -> dict:
    """Per-strategy stats of the issued ideas (the live book), with signal-month block-bootstrap CIs."""
    from scripts.validate_backtest_pipeline import block_bootstrap_ci

    def r(v):
        return None if v is None else round(float(v), 3)

    t = pd.read_csv(os.path.join(artifact_dir, ARTIFACT_TRADES))
    out = {}
    for strat, g in t.groupby("strategy"):
        months = g["signal_date"].str[:7].to_numpy()
        net = g["net_return_pct"].to_numpy(float)
        win = (net > 0).astype(float)
        ba = g["beta_adjusted_pct"].dropna()
        ba_months = g.loc[ba.index, "signal_date"].str[:7].to_numpy()
        out[strat] = {
            "n": int(len(g)),
            "months": int(len(set(months))),   # a month-block bootstrap needs many months to mean anything
            "win_rate_pct": r(100 * win.mean()),
            "win_rate_ci95": [None if x is None else r(100 * x) for x in block_bootstrap_ci(win, months, np.mean)],
            "net_expectancy_pct": r(net.mean()),
            "net_expectancy_ci95": [r(x) for x in block_bootstrap_ci(net, months, np.mean)],
            "beta_adjusted_pct": r(ba.mean()) if len(ba) else None,
            "beta_adjusted_ci95": [r(x) for x in block_bootstrap_ci(ba.to_numpy(float), ba_months, np.mean)] if len(ba) else [None, None],
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Adopt production-universe backtest evidence")
    parser.add_argument("artifact_dir")
    parser.add_argument("--run-id", required=True, help="GitHub Actions run id of the backtest (recorded in the file)")
    parser.add_argument("--check-only", action="store_true", help="Verify the match without writing")
    parser.add_argument("--out", default=EVIDENCE_PATH)
    parser.add_argument("--issued-only", action="store_true",
                        help="Write only frontend/src/lib/issued-evidence.json (leave the scoring evidence)")
    args = parser.parse_args()

    doc, rebuilt, mismatches = rebuild(args.artifact_dir)
    if mismatches:
        print("MISMATCH: the rebuilt evidence does not reproduce the run's own file. Nothing written.")
        for m in mismatches:
            print("  " + m)
        return 1
    n_fields = sum(len(v) for v in (doc.get("strategy_evidence") or {}).values())
    print(f"Match: all {n_fields} existing fields in {len(rebuilt)} strategies reproduce exactly.")
    if args.check_only:
        return 0
    if args.issued_only:
        with open(FRONTEND_ISSUED, "w", encoding="utf-8") as f:
            json.dump({"run_id": str(args.run_id), "strategies": issued_by_strategy(args.artifact_dir)}, f, indent=2)
        print(f"Wrote {FRONTEND_ISSUED} (scoring evidence unchanged)")
        return 0

    out = dict(doc)
    out["metadata"] = dict(doc["metadata"])
    out["metadata"]["adopted_from"] = {
        "workflow": "backtest_production_universe.yml",
        "run_id": str(args.run_id),
        "universe": "production (today's broad US security master; delisted stocks missing)",
        "rebuilt_from": ARTIFACT_SETUPS,
    }
    out["strategy_evidence"] = rebuilt
    if os.path.exists(os.path.join(args.artifact_dir, ARTIFACT_TRADES)):
        issued = {"run_id": str(args.run_id), "strategies": issued_by_strategy(args.artifact_dir)}
        out["issued_ideas_by_strategy"] = issued
        if os.path.abspath(args.out) == os.path.abspath(EVIDENCE_PATH):   # the cards follow the adopted config only
            with open(FRONTEND_ISSUED, "w", encoding="utf-8") as f:
                json.dump(issued, f, indent=2)
            print(f"Wrote {FRONTEND_ISSUED}")
    else:
        print(f"No {ARTIFACT_TRADES} in the artifact: issued-idea stats not written.")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
