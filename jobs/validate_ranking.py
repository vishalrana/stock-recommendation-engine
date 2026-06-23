"""
Validate Ranking — Strategy 1.2
================================
Post-deployment validation script that evaluates the quality of
signals produced by the new gated percentile-normalized ranking.

Queries signals_history, fetches actual prices 20 trading days later,
and determines whether each signal hit its target, stop, or is still open.

Usage:
    python -m jobs.validate_ranking
"""

import os
import sys
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# Add project root and src to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
sys.path.insert(0, PROJECT_ROOT)

from downloader import fetch_ohlcv_data
from jobs.supabase_client import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

EVALUATION_DAYS = 20  # Trading days to wait before evaluating outcome


def fetch_signals_history(supabase, days: int = 30) -> pd.DataFrame:
    """Fetch signals_history entries from the last N days."""
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    try:
        res = (
            supabase.table("signals_history")
            .select("*")
            .gte("scan_date", cutoff)
            .execute()
        )
        if not res.data:
            return pd.DataFrame()
        return pd.DataFrame(res.data)
    except Exception as e:
        logger.error("Failed to fetch signals_history: %s", e)
        return pd.DataFrame()


def evaluate_signal(ticker: str, scan_date_str: str, entry_price: float,
                    stop_loss: float, exit_price: float) -> dict:
    """
    Fetch actual price data after the signal date and determine outcome.

    Returns dict with: outcome, actual_price, return_pct, days_held
    """
    try:
        scan_date = datetime.strptime(scan_date_str, "%Y-%m-%d").date()
        fetch_start = scan_date
        fetch_end = scan_date + timedelta(days=EVALUATION_DAYS * 2)  # Buffer for weekends

        data = fetch_ohlcv_data(ticker, start_date=fetch_start, end_date=fetch_end)
        if data is None or data.empty:
            return {"outcome": "no_data", "actual_price": None, "return_pct": None, "days_held": None}

        data = data.sort_index()
        # Get rows AFTER the scan date (signal is for next day entry)
        post_signal = data[data.index > pd.Timestamp(scan_date)]

        if len(post_signal) < 2:
            return {"outcome": "insufficient_data", "actual_price": None, "return_pct": None, "days_held": None}

        # Simulate trade: check each day for target/stop hit
        for i, (dt, row) in enumerate(post_signal.iterrows(), 1):
            high = float(row.get("HIGH", row.get("High", 0)))
            low = float(row.get("LOW", row.get("Low", 0)))

            # Check stop hit first (conservative)
            if low <= stop_loss:
                return_pct = ((stop_loss - entry_price) / entry_price) * 100
                return {
                    "outcome": "stop_hit",
                    "actual_price": stop_loss,
                    "return_pct": round(return_pct, 2),
                    "days_held": i,
                }

            # Check target hit
            if high >= exit_price:
                return_pct = ((exit_price - entry_price) / entry_price) * 100
                return {
                    "outcome": "target_hit",
                    "actual_price": exit_price,
                    "return_pct": round(return_pct, 2),
                    "days_held": i,
                }

            # After EVALUATION_DAYS trading days, mark as open
            if i >= EVALUATION_DAYS:
                close = float(row.get("CLOSE", row.get("Close", entry_price)))
                return_pct = ((close - entry_price) / entry_price) * 100
                return {
                    "outcome": "open",
                    "actual_price": round(close, 2),
                    "return_pct": round(return_pct, 2),
                    "days_held": i,
                }

        # Ran out of data before EVALUATION_DAYS
        last_close = float(post_signal.iloc[-1].get("CLOSE", post_signal.iloc[-1].get("Close", entry_price)))
        return_pct = ((last_close - entry_price) / entry_price) * 100
        return {
            "outcome": "open",
            "actual_price": round(last_close, 2),
            "return_pct": round(return_pct, 2),
            "days_held": len(post_signal),
        }

    except Exception as e:
        logger.error("Error evaluating %s (scan_date=%s): %s", ticker, scan_date_str, e)
        return {"outcome": "error", "actual_price": None, "return_pct": None, "days_held": None}


def generate_report(results: list, signals_df: pd.DataFrame) -> str:
    """Generate markdown validation report."""
    lines = []
    lines.append("# Validation Report - Strategy 1.2")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    if not results:
        lines.append("> No signals available for validation yet.")
        lines.append("> signals_history needs to accumulate ~20+ trading days of data.")
        return "\n".join(lines)

    # Summary
    df = pd.DataFrame(results)
    total = len(df)
    target_hits = len(df[df["outcome"] == "target_hit"])
    stop_hits = len(df[df["outcome"] == "stop_hit"])
    still_open = len(df[df["outcome"] == "open"])
    errors = len(df[df["outcome"].isin(["error", "no_data", "insufficient_data"])])

    completed = df[df["outcome"].isin(["target_hit", "stop_hit"])]
    completed_returns = completed["return_pct"].dropna()

    lines.append("## Signals Analyzed")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Total signals evaluated | {total} |")

    if not signals_df.empty and "scan_date" in signals_df.columns:
        dates = signals_df["scan_date"].unique()
        lines.append(f"| Date range | {min(dates)} to {max(dates)} |")
        lines.append(f"| Unique scan dates | {len(dates)} |")

    lines.append(f"| Target hit | {target_hits} |")
    lines.append(f"| Stop hit | {stop_hits} |")
    lines.append(f"| Still open | {still_open} |")
    lines.append(f"| Errors/no data | {errors} |")
    lines.append("")

    # Performance Metrics
    lines.append("## Performance Metrics (Completed Trades)")
    lines.append("")

    if len(completed) > 0:
        win_rate = (target_hits / len(completed)) * 100 if len(completed) > 0 else 0
        avg_return = completed_returns.mean() if len(completed_returns) > 0 else 0
        max_dd = completed_returns.min() if len(completed_returns) > 0 else 0
        avg_days = completed["days_held"].dropna().mean() if len(completed) > 0 else 0

        lines.append(f"| Metric | Value |")
        lines.append(f"|---|---|")
        lines.append(f"| Win Rate | {win_rate:.1f}% |")
        lines.append(f"| Avg Return | {avg_return:+.2f}% |")
        lines.append(f"| Max Drawdown | {max_dd:+.2f}% |")
        lines.append(f"| Avg Days Held | {avg_days:.1f} |")
    else:
        lines.append("> No completed trades yet. Trades need 20+ trading days to resolve.")

    lines.append("")

    # Ticker breakdown
    lines.append("## Ticker-by-Ticker Breakdown")
    lines.append("")
    lines.append("| Ticker | Scan Date | Entry | Stop | Target | Outcome | Return | Days |")
    lines.append("|--------|-----------|-------|------|--------|---------|--------|------|")

    for r in results:
        outcome_emoji = {
            "target_hit": "TARGET",
            "stop_hit": "STOP",
            "open": "OPEN",
        }.get(r["outcome"], r["outcome"].upper())

        ret = f"{r['return_pct']:+.2f}%" if r["return_pct"] is not None else "-"
        days = str(r["days_held"]) if r["days_held"] is not None else "-"

        lines.append(
            f"| {r['ticker']} | {r['scan_date']} | "
            f"${r['entry_price']:.2f} | ${r['stop_loss']:.2f} | "
            f"${r['exit_price']:.2f} | {outcome_emoji} | {ret} | {days} |"
        )

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Old ranking formula comparison requires historical signals_history with old scores. Future enhancement.")
    lines.append("- Signals marked 'open' have not yet hit target or stop within the evaluation window.")
    lines.append(f"- Evaluation window: {EVALUATION_DAYS} trading days.")

    return "\n".join(lines)


def main():
    logger.info("Starting Strategy 1.2 ranking validation...")

    supabase = get_client()
    signals_df = fetch_signals_history(supabase, days=30)

    if signals_df.empty:
        logger.warning("No signals_history entries found. Writing minimal report.")
        report = generate_report([], signals_df)
    else:
        logger.info("Found %d signals_history entries to evaluate.", len(signals_df))

        results = []
        for idx, row in signals_df.iterrows():
            ticker = row["ticker"]
            scan_date = row["scan_date"]
            entry = float(row["entry_price"])
            stop = float(row["stop_loss"])
            target = float(row["exit_price"])

            logger.info("[%d/%d] Evaluating %s (scan_date=%s)...", idx + 1, len(signals_df), ticker, scan_date)

            outcome = evaluate_signal(ticker, str(scan_date), entry, stop, target)
            outcome["ticker"] = ticker
            outcome["scan_date"] = scan_date
            outcome["entry_price"] = entry
            outcome["stop_loss"] = stop
            outcome["exit_price"] = target
            results.append(outcome)

        report = generate_report(results, signals_df)

    # Write report
    report_path = os.path.join(PROJECT_ROOT, "data", "validation_report.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info("Validation report written to: %s", report_path)
    print("\n" + report)


if __name__ == "__main__":
    main()
