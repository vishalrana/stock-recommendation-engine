"""
Validate Ranking — Strategy 1.3 Rev A
======================================
Post-deployment validation script that evaluates the quality of
signals produced by the ranking system.

Can be run manually to generate a markdown validation report, or
automatically with the `--auto` flag to track outcomes 28 days later.

Usage:
    python -m jobs.validate_ranking [--auto]
"""

import os
import sys
import logging
import time
import argparse
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
    lines.append("# Validation Report - Strategy 1.3 Rev A")
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
    lines.append("- Signals marked 'open' have not yet hit target or stop within the evaluation window.")
    lines.append(f"- Evaluation window: {EVALUATION_DAYS} trading days.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Validate Stock Ranking Recommendations")
    parser.add_argument("--auto", action="store_true", help="Automated outcome tracking mode (28 calendar days lag)")
    args = parser.parse_args()

    supabase = get_client()

    if args.auto:
        start_time = time.time()
        # Validation date is exactly 28 calendar days ago (approx 20 trading days)
        target_date = datetime.now().date() - timedelta(days=28)
        target_date_str = target_date.isoformat()
        logger.info(f"Starting automated outcome tracking for scan_date: {target_date_str}...")

        # Query signals_history
        res = (
            supabase.table("signals_history")
            .select("*")
            .eq("scan_date", target_date_str)
            .execute()
        )
        signals = res.data or []

        if not signals:
            logger.info(f"No historical signals found for date: {target_date_str}. Nothing to validate.")
            # Log a summary row anyway to record that we checked
            duration = round(time.time() - start_time, 2)
            scan_log_row = {
                "scan_date": target_date_str,
                "tickers_scanned": 0,
                "signals_generated": 0,
                "signals_qualified": 0,
                "signals_recommended": 0,
                "scan_duration_secs": duration,
                "status": "validation",
                "error_message": f"No signals found for validation on {target_date_str}",
            }
            supabase.table("scan_log").upsert(scan_log_row, on_conflict="scan_date").execute()
            return

        logger.info(f"Found {len(signals)} signals to validate for {target_date_str}.")

        wins_count = 0
        losses_count = 0

        for sig in signals:
            ticker = sig.get("ticker", "").upper()
            entry = float(sig.get("entry_price"))
            stop = float(sig.get("stop_loss"))
            target = float(sig.get("exit_price"))

            logger.info(f"Validating outcome for {ticker}...")
            outcome = evaluate_signal(ticker, target_date_str, entry, stop, target)
            r = outcome.get("return_pct")

            if r is None:
                logger.warning(f"Could not retrieve price history for {ticker}. Skipping.")
                continue

            # Determine win or loss
            if outcome["outcome"] == "target_hit":
                is_win = True
            elif outcome["outcome"] == "stop_hit":
                is_win = False
            elif outcome["outcome"] == "open":
                is_win = (r > 0.0)
            else:
                logger.warning(f"Outcome is {outcome['outcome']} for {ticker}. Skipping.")
                continue

            if is_win:
                wins_count += 1
            else:
                losses_count += 1

            # Fetch existing metrics
            met_res = supabase.table("ticker_metrics").select("*").eq("ticker", ticker).execute()
            if met_res.data:
                row = met_res.data[0]
                wins = int(row.get("wins") or 0)
                losses = int(row.get("losses") or 0)
                total_signals = int(row.get("total_signals") or 0)
                expectancy_pct_old = float(row.get("expectancy_pct") or 0.0)

                if is_win:
                    wins += 1
                else:
                    losses += 1
                new_total = total_signals + 1
                new_expectancy = (expectancy_pct_old * total_signals + r) / new_total
                new_win_rate = (wins / new_total) * 100.0
            else:
                wins = 1 if is_win else 0
                losses = 0 if is_win else 1
                new_total = 1
                new_expectancy = r
                new_win_rate = 100.0 if is_win else 0.0

            # Upsert ticker_metrics
            upsert_data = {
                "ticker": ticker,
                "wins": wins,
                "losses": losses,
                "total_signals": new_total,
                "win_rate": round(new_win_rate, 2),
                "expectancy_pct": round(new_expectancy, 4),
                "updated_at": datetime.now().isoformat(),
            }
            supabase.table("ticker_metrics").upsert(upsert_data, on_conflict="ticker").execute()
            logger.info(f"Updated ticker_metrics for {ticker}: win_rate={upsert_data['win_rate']}%, expectancy={upsert_data['expectancy_pct']}%")

        duration = round(time.time() - start_time, 2)
        # Log a summary row to scan_log with status = 'validation'
        scan_log_row = {
            "scan_date": target_date_str,
            "tickers_scanned": len(signals),
            "signals_generated": wins_count + losses_count,
            "signals_qualified": wins_count + losses_count,
            "signals_recommended": len(signals),
            "scan_duration_secs": duration,
            "status": "validation",
            "error_message": f"Auto validation complete. Wins: {wins_count}, Losses: {losses_count}",
        }
        supabase.table("scan_log").upsert(scan_log_row, on_conflict="scan_date").execute()
        logger.info(f"Recorded validation log in scan_log for {target_date_str}. Duration: {duration}s")

    else:
        logger.info("Starting manual validation report generation...")
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
