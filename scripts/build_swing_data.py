"""
Build the swing research data (GitHub Actions data job)
=======================================================
1. Insider Transactions Data Sets 2006Q1 onward (also gives historical symbol -> CIK)
2. Long daily history from 2004 for U1 (today's security master), historical S&P 500 members
   overlapping 2008-2022, SPY and the sector ETFs (scripts/build_long_history.py)
3. SEC submissions since 2006 for every ticker with a price file (src/swing/data.load_filings)
Writes outputs/swing/data_summary.json with coverage counts.
"""

import argparse
import json
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_swing_data")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    from scripts.build_insider_history import build as build_insider
    from scripts.build_long_history import build_long_history, verify_adjustment_identities
    from src.swing.data import CikResolver, load_filings, load_price_frames
    summary = {"insider": build_insider()}
    ok, failed = build_long_history(limit=args.limit)
    summary["prices"] = {"ok": ok, "failed": failed, "identities_pass": verify_adjustment_identities()}
    frames = load_price_frames()
    from scripts.validate_backtest_pipeline import production_universe
    u1, _ = production_universe()
    have = set(frames)
    u1_cov = sum(1 for t in u1 if t in have) / max(1, len(u1))
    summary["prices"]["u1_tickers"] = len(u1)
    summary["prices"]["u1_with_history"] = sum(1 for t in u1 if t in have)
    summary["prices"]["u1_coverage_pct"] = round(100.0 * u1_cov, 2)
    summary["prices"]["u1_missing_sample"] = sorted(t for t in u1 if t not in have)[:50]
    _, _, sec = load_filings([t for t in frames if t != "SPY"], CikResolver())
    summary["sec"] = sec
    os.makedirs(os.path.join(PROJECT_ROOT, "outputs", "swing"), exist_ok=True)
    with open(os.path.join(PROJECT_ROOT, "outputs", "swing", "data_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info("Swing data: %s", summary)
    return 0 if summary["prices"]["identities_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
