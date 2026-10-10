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
"""

import argparse
import json
import os
import sys

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.strategy_evidence import EVIDENCE_PATH, aggregate_trades  # noqa: E402

ARTIFACT_EVIDENCE = "strategy_performance_production_universe.json"
ARTIFACT_SETUPS = "production_backtest_setup_trades_production_universe.csv"
TRADE_FIELDS = ("strategy", "net_return_pct", "outcome", "has_t2", "has_t3", "beta_adjusted_pct", "excess_vs_spy_pct")


def rebuild(artifact_dir: str):
    """Return (artifact evidence doc, rebuilt aggregates, list of mismatches)."""
    with open(os.path.join(artifact_dir, ARTIFACT_EVIDENCE), encoding="utf-8") as f:
        doc = json.load(f)
    setups = pd.read_csv(os.path.join(artifact_dir, ARTIFACT_SETUPS))
    missing = [c for c in TRADE_FIELDS if c not in setups.columns]
    if missing:
        raise ValueError(f"Setup-trades CSV lacks columns {missing}")
    # Same row order as the run's shadow-trade list, so float sums add up in the same order
    records = setups[list(TRADE_FIELDS)].to_dict("records")
    for r in records:
        for k in ("has_t2", "has_t3"):
            r[k] = bool(r[k]) if not pd.isna(r[k]) else False
        for k in ("beta_adjusted_pct", "excess_vs_spy_pct"):
            if pd.isna(r[k]):
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Adopt production-universe backtest evidence")
    parser.add_argument("artifact_dir")
    parser.add_argument("--run-id", required=True, help="GitHub Actions run id of the backtest (recorded in the file)")
    parser.add_argument("--check-only", action="store_true", help="Verify the match without writing")
    parser.add_argument("--out", default=EVIDENCE_PATH)
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

    out = dict(doc)
    out["metadata"] = dict(doc["metadata"])
    out["metadata"]["adopted_from"] = {
        "workflow": "backtest_production_universe.yml",
        "run_id": str(args.run_id),
        "universe": "production (today's broad US security master; delisted stocks missing)",
        "rebuilt_from": ARTIFACT_SETUPS,
    }
    out["strategy_evidence"] = rebuilt
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
