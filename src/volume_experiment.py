"""Volume-filter sensitivity experiment for frozen Strategy Version 1.0."""

import logging
from datetime import timedelta
from statistics import mean, median
from typing import Dict, List, Optional

import pandas as pd

from backtester import (
    BACKTEST_YEARS,
    OUTPUT_DIR,
    fetch_validation_universe,
    simulate_trade,
)
from config import END_DATE, MIN_DATA_POINTS
from downloader import fetch_ohlcv_data
from indicators import calculate_indicators, get_indicator_values
from patterns import detect_pattern
from risk import evaluate_trade
from scanner import SignalQualifier


logger = logging.getLogger(__name__)

VOLUME_THRESHOLDS = [1.5, 1.2, 1.0]
EXPERIMENT_CSV = OUTPUT_DIR / "volume_filter_experiment.csv"
ANALYSIS_FILE = OUTPUT_DIR / "volume_filter_analysis.txt"
OUTPUT_COLUMNS = [
    "volume_threshold",
    "signals",
    "completed_trades",
    "wins",
    "losses",
    "win_rate",
    "expectancy_pct",
    "average_holding_days",
    "median_holding_days",
]


def create_experimental_signal(
    ticker: str,
    history: pd.DataFrame,
    volume_threshold: float,
) -> Optional[dict]:
    """Create a trade plan with only the volume threshold overridden."""
    values = get_indicator_values(history, ticker)
    if values is None:
        return None

    price, dma_50, dma_200, rsi, min_rsi, volume_ma, volume = values
    passes = (
        SignalQualifier.passes_price_filter(price, dma_50, dma_200)
        and SignalQualifier.passes_rsi_min_filter(min_rsi)
        and SignalQualifier.passes_rsi_current_filter(rsi)
        and SignalQualifier.passes_volume_filter(volume, volume_ma, volume_threshold)
    )
    if not passes:
        return None

    pattern, entry_price = detect_pattern(history)
    if pattern is None or entry_price is None:
        return None

    trade, _ = evaluate_trade(
        {"ticker": ticker, "pattern": pattern, "entry_price": entry_price},
        history,
    )
    return trade


def download_experiment_data(tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """Download and calculate indicators once for all experiment versions."""
    start_date = END_DATE - timedelta(days=365 * BACKTEST_YEARS)
    data_by_ticker = {}
    for position, ticker in enumerate(tickers, 1):
        logger.info("[%s/%s] Downloading %s", position, len(tickers), ticker)
        data = fetch_ohlcv_data(ticker, start_date=start_date, end_date=END_DATE)
        if data is None or data.empty:
            logger.warning("%s: no data; excluded from signal evaluation", ticker)
            data_by_ticker[ticker] = pd.DataFrame()
            continue
        data_by_ticker[ticker] = calculate_indicators(data).sort_index()
    return data_by_ticker


def backtest_threshold(
    tickers: List[str],
    data_by_ticker: Dict[str, pd.DataFrame],
    volume_threshold: float,
) -> dict:
    """Run one volume-threshold version against the shared historical data."""
    signals = 0
    outcomes = []

    for ticker in tickers:
        data = data_by_ticker[ticker]
        if data.empty:
            continue

        for signal_index in range(MIN_DATA_POINTS - 1, len(data) - 1):
            history = data.iloc[: signal_index + 1]
            trade = create_experimental_signal(ticker, history, volume_threshold)
            if trade is None:
                continue

            signals += 1
            outcome = simulate_trade(data, signal_index, trade)
            if outcome is not None:
                outcomes.append(outcome)

    wins = sum(outcome["outcome"] == "win" for outcome in outcomes)
    losses = sum(outcome["outcome"] == "loss" for outcome in outcomes)
    completed = wins + losses
    returns = [outcome["return_pct"] for outcome in outcomes]
    holding_days = [outcome["holding_days"] for outcome in outcomes]

    return {
        "volume_threshold": volume_threshold,
        "signals": signals,
        "completed_trades": completed,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / completed * 100.0, 2) if completed else 0.0,
        "expectancy_pct": round(float(mean(returns)), 2) if returns else 0.0,
        "average_holding_days": round(float(mean(holding_days)), 2) if holding_days else 0.0,
        "median_holding_days": round(float(median(holding_days)), 2) if holding_days else 0.0,
    }


def write_analysis(result: pd.DataFrame) -> None:
    """Write comparative changes and an evidence-based experiment recommendation."""
    baseline = result[result["volume_threshold"] == 1.5].iloc[0]
    lines = [
        "VOLUME FILTER SENSITIVITY ANALYSIS",
        "=" * 42,
        "",
        "1. Signal count increase",
    ]

    for _, row in result.iterrows():
        increase = int(row["signals"] - baseline["signals"])
        increase_pct = increase / baseline["signals"] * 100.0 if baseline["signals"] else 0.0
        lines.append(
            f"> {row['volume_threshold']:.1f}x: {int(row['signals'])} signals "
            f"({increase:+d}, {increase_pct:+.2f}% vs 1.5x)"
        )

    lines.extend(["", "2. Win rate change"])
    for _, row in result.iterrows():
        change = row["win_rate"] - baseline["win_rate"]
        lines.append(
            f"> {row['volume_threshold']:.1f}x: {row['win_rate']:.2f}% "
            f"({change:+.2f} percentage points vs 1.5x)"
        )

    lines.extend(["", "3. Expectancy change"])
    for _, row in result.iterrows():
        change = row["expectancy_pct"] - baseline["expectancy_pct"]
        lines.append(
            f"> {row['volume_threshold']:.1f}x: {row['expectancy_pct']:.2f}% "
            f"({change:+.2f} percentage points vs 1.5x)"
        )

    best = result.sort_values(
        ["expectancy_pct", "completed_trades"], ascending=[False, False]
    ).iloc[0]
    lines.extend(
        [
            "",
            "4. Recommendation",
            f"> Best observed threshold: {best['volume_threshold']:.1f}x.",
            f"> It produced {int(best['signals'])} signals, "
            f"{int(best['completed_trades'])} completed trades, a {best['win_rate']:.2f}% "
            f"win rate, and {best['expectancy_pct']:.2f}% expectancy.",
            "> This is an experimental comparison only; Strategy Version 1.0 remains unchanged.",
        ]
    )
    ANALYSIS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_experiment() -> pd.DataFrame:
    """Run all volume thresholds over one shared 100-stock dataset."""
    tickers, _ = fetch_validation_universe()
    data_by_ticker = download_experiment_data(tickers)
    rows = []

    for threshold in VOLUME_THRESHOLDS:
        logger.info("Running volume threshold %.1fx", threshold)
        rows.append(backtest_threshold(tickers, data_by_ticker, threshold))

    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    EXPERIMENT_CSV.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(EXPERIMENT_CSV, index=False)
    write_analysis(result)
    logger.info("Experiment results written to %s", EXPERIMENT_CSV)
    logger.info("Experiment analysis written to %s", ANALYSIS_FILE)
    return result


def main() -> bool:
    """Command-line entry point."""
    try:
        print(run_experiment().to_string(index=False))
        return True
    except Exception:
        logger.exception("Volume experiment failed")
        return False


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    raise SystemExit(0 if main() else 1)
