"""Parameterized, cache-only research framework for strategy experiments."""

import logging
from datetime import datetime, timezone
from statistics import mean, median
from typing import Dict, Optional
from uuid import uuid4

import pandas as pd

from backtester import simulate_trade
from cache import load_cached_data
from config import MIN_DATA_POINTS, OUTPUT_DIR
from indicators import calculate_indicators, get_indicator_values
from patterns import detect_pattern
from risk import evaluate_trade
from scanner import SignalQualifier


logger = logging.getLogger(__name__)

UNIVERSE_FILE = OUTPUT_DIR / "backtest_summary.csv"
RESEARCH_RESULTS_CSV = OUTPUT_DIR / "research_results.csv"
RESULT_COLUMNS = [
    "run_id",
    "run_timestamp_utc",
    "volume_threshold",
    "require_pattern",
    "require_current_rsi",
    "require_rsi_pullback",
    "universe_size",
    "signals",
    "completed_trades",
    "wins",
    "losses",
    "win_rate",
    "expectancy_pct",
    "average_holding_days",
    "median_holding_days",
]


def _load_cached_universe() -> Dict[str, pd.DataFrame]:
    """Load the fixed validation universe directly from fresh Parquet files."""
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(f"Validation universe is missing: {UNIVERSE_FILE}")

    tickers = pd.read_csv(UNIVERSE_FILE)["ticker"].tolist()
    if len(tickers) != 100:
        raise ValueError(f"Expected 100 validation tickers, found {len(tickers)}")

    data_by_ticker = {}
    unavailable = []
    for ticker in tickers:
        data = load_cached_data(ticker)
        if data is None or data.empty:
            unavailable.append(ticker)
            continue
        data_by_ticker[ticker] = calculate_indicators(data).sort_index()

    if unavailable:
        raise RuntimeError(
            "Research requires fresh cached data; missing or expired: "
            + ", ".join(unavailable)
        )
    return data_by_ticker


def _append_result(row: dict) -> None:
    """Atomically append one experiment result to the shared research table."""
    new_result = pd.DataFrame([row], columns=RESULT_COLUMNS)
    if RESEARCH_RESULTS_CSV.exists():
        existing = pd.read_csv(RESEARCH_RESULTS_CSV)
        if list(existing.columns) != RESULT_COLUMNS:
            raise ValueError(
                f"Existing research table has an incompatible schema: {RESEARCH_RESULTS_CSV}"
            )
        combined = pd.concat([existing, new_result], ignore_index=True)
    else:
        combined = new_result

    RESEARCH_RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = RESEARCH_RESULTS_CSV.with_suffix(".csv.tmp")
    combined.to_csv(temporary_path, index=False)
    temporary_path.replace(RESEARCH_RESULTS_CSV)


def run_experiment(
    volume_threshold: Optional[float] = 1.0,
    require_pattern: bool = False,
    require_current_rsi: bool = True,
    require_rsi_pullback: bool = True,
) -> dict:
    """Run one parameter set against cached data and append its aggregate result."""
    if volume_threshold is not None and volume_threshold < 0:
        raise ValueError("volume_threshold must be non-negative or None")

    data_by_ticker = _load_cached_universe()
    signals = 0
    outcomes = []

    for position, (ticker, data) in enumerate(data_by_ticker.items(), 1):
        logger.info("[%s/%s] Researching %s", position, len(data_by_ticker), ticker)
        for signal_index in range(MIN_DATA_POINTS - 1, len(data) - 1):
            history = data.iloc[: signal_index + 1]
            values = get_indicator_values(history, ticker)
            if values is None:
                continue

            price, dma_50, dma_200, rsi, min_rsi, volume_ma, volume = values
            if not SignalQualifier.passes_price_filter(price, dma_50, dma_200):
                continue
            if require_rsi_pullback and not SignalQualifier.passes_rsi_min_filter(min_rsi):
                continue
            if require_current_rsi and not SignalQualifier.passes_rsi_current_filter(rsi):
                continue
            if volume_threshold is not None and not SignalQualifier.passes_volume_filter(
                volume, volume_ma, volume_threshold
            ):
                continue

            pattern, pattern_entry = detect_pattern(history)
            if require_pattern and (pattern is None or pattern_entry is None):
                continue

            # Pattern-disabled research retains the frozen T+1 stop-entry convention.
            entry_price = (
                pattern_entry
                if pattern is not None and pattern_entry is not None
                else round(float(history["HIGH"].iloc[-1]) * 1.001, 2)
            )
            trade, _ = evaluate_trade(
                {
                    "ticker": ticker,
                    "pattern": pattern or "Pattern Not Required",
                    "entry_price": entry_price,
                },
                history,
            )
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

    result = {
        "run_id": uuid4().hex,
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "volume_threshold": volume_threshold,
        "require_pattern": require_pattern,
        "require_current_rsi": require_current_rsi,
        "require_rsi_pullback": require_rsi_pullback,
        "universe_size": len(data_by_ticker),
        "signals": signals,
        "completed_trades": completed,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / completed * 100.0, 2) if completed else 0.0,
        "expectancy_pct": round(float(mean(returns)), 2) if returns else 0.0,
        "average_holding_days": round(float(mean(holding_days)), 2)
        if holding_days
        else 0.0,
        "median_holding_days": round(float(median(holding_days)), 2)
        if holding_days
        else 0.0,
    }
    _append_result(result)
    logger.info("Research result appended to %s", RESEARCH_RESULTS_CSV)
    return result
