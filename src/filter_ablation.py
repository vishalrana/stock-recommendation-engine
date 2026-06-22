"""Research-only filter ablation study using cached Strategy 1.0 data."""

import logging
from statistics import mean, median
from typing import Dict

import pandas as pd

from backtester import simulate_trade
from cache import load_cached_data
from config import MIN_DATA_POINTS, OUTPUT_DIR
from indicators import calculate_indicators, get_indicator_values
from patterns import detect_pattern
from risk import evaluate_trade
from scanner import SignalQualifier


logger = logging.getLogger(__name__)

VOLUME_THRESHOLD = 1.0
UNIVERSE_FILE = OUTPUT_DIR / "backtest_summary.csv"
OUTPUT_CSV = OUTPUT_DIR / "filter_ablation.csv"
ANALYSIS_FILE = OUTPUT_DIR / "filter_ablation_analysis.txt"

EXPERIMENTS = {
    "Baseline": {
        "description": "All filters enabled",
        "rsi_pullback": True,
        "current_rsi": True,
        "volume": True,
        "pattern": True,
    },
    "Experiment A": {
        "description": "Remove Volume Filter",
        "rsi_pullback": True,
        "current_rsi": True,
        "volume": False,
        "pattern": True,
    },
    "Experiment B": {
        "description": "Remove Current RSI Filter",
        "rsi_pullback": True,
        "current_rsi": False,
        "volume": True,
        "pattern": True,
    },
    "Experiment C": {
        "description": "Remove RSI Pullback Filter",
        "rsi_pullback": False,
        "current_rsi": True,
        "volume": True,
        "pattern": True,
    },
    "Experiment D": {
        "description": "Remove Pattern Filter",
        "rsi_pullback": True,
        "current_rsi": True,
        "volume": True,
        "pattern": False,
    },
    "Experiment E": {
        "description": "Price Trend Only",
        "rsi_pullback": False,
        "current_rsi": False,
        "volume": False,
        "pattern": False,
    },
}

FILTER_BY_EXPERIMENT = {
    "Experiment A": "Volume Filter",
    "Experiment B": "Current RSI Filter",
    "Experiment C": "RSI Pullback Filter",
    "Experiment D": "Pattern Filter",
}

OUTPUT_COLUMNS = [
    "experiment",
    "description",
    "signals",
    "completed_trades",
    "wins",
    "losses",
    "win_rate",
    "expectancy_pct",
    "median_holding_days",
]


def load_cached_universe() -> Dict[str, pd.DataFrame]:
    """Load the fixed 100-stock validation universe without a network fallback."""
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(f"Validation universe is missing: {UNIVERSE_FILE}")

    tickers = pd.read_csv(UNIVERSE_FILE)["ticker"].tolist()
    if len(tickers) != 100:
        raise ValueError(f"Expected 100 validation tickers, found {len(tickers)}")

    data_by_ticker = {}
    missing = []
    for ticker in tickers:
        data = load_cached_data(ticker)
        if data is None or data.empty:
            missing.append(ticker)
            continue
        data_by_ticker[ticker] = calculate_indicators(data).sort_index()

    if missing:
        raise RuntimeError(
            "Ablation requires fresh cached data; missing or expired: " + ", ".join(missing)
        )
    return data_by_ticker


def experiment_passes(config: dict, flags: dict) -> bool:
    """Apply one experiment's enabled filters to precomputed rule flags."""
    return (
        (not config["rsi_pullback"] or flags["rsi_pullback"])
        and (not config["current_rsi"] or flags["current_rsi"])
        and (not config["volume"] or flags["volume"])
        and (not config["pattern"] or flags["pattern"])
    )


def run_ablation() -> pd.DataFrame:
    """Evaluate all ablations in one pass over identical cached histories."""
    data_by_ticker = load_cached_universe()
    outcomes = {
        name: {"signals": 0, "trades": []}
        for name in EXPERIMENTS
    }

    for position, (ticker, data) in enumerate(data_by_ticker.items(), 1):
        logger.info("[%s/%s] Evaluating %s", position, len(data_by_ticker), ticker)
        for signal_index in range(MIN_DATA_POINTS - 1, len(data) - 1):
            history = data.iloc[: signal_index + 1]
            values = get_indicator_values(history, ticker)
            if values is None:
                continue

            price, dma_50, dma_200, rsi, min_rsi, volume_ma, volume = values
            if not SignalQualifier.passes_price_filter(price, dma_50, dma_200):
                continue

            pattern, pattern_entry = detect_pattern(history)
            flags = {
                "rsi_pullback": SignalQualifier.passes_rsi_min_filter(min_rsi),
                "current_rsi": SignalQualifier.passes_rsi_current_filter(rsi),
                "volume": SignalQualifier.passes_volume_filter(
                    volume, volume_ma, VOLUME_THRESHOLD
                ),
                "pattern": pattern is not None and pattern_entry is not None,
            }

            # All current patterns use the same T+1 stop entry above day-T high.
            entry_price = round(float(history["HIGH"].iloc[-1]) * 1.001, 2)
            trade, _ = evaluate_trade(
                {
                    "ticker": ticker,
                    "pattern": pattern or "Pattern Filter Disabled",
                    "entry_price": entry_price,
                },
                history,
            )
            if trade is None:
                continue

            for name, config in EXPERIMENTS.items():
                if not experiment_passes(config, flags):
                    continue
                outcomes[name]["signals"] += 1
                result = simulate_trade(data, signal_index, trade)
                if result is not None:
                    outcomes[name]["trades"].append(result)

    rows = []
    for name, config in EXPERIMENTS.items():
        trades = outcomes[name]["trades"]
        wins = sum(trade["outcome"] == "win" for trade in trades)
        losses = sum(trade["outcome"] == "loss" for trade in trades)
        completed = wins + losses
        returns = [trade["return_pct"] for trade in trades]
        holding_days = [trade["holding_days"] for trade in trades]
        rows.append(
            {
                "experiment": name,
                "description": config["description"],
                "signals": outcomes[name]["signals"],
                "completed_trades": completed,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / completed * 100.0, 2) if completed else 0.0,
                "expectancy_pct": round(float(mean(returns)), 2) if returns else 0.0,
                "median_holding_days": round(float(median(holding_days)), 2)
                if holding_days
                else 0.0,
            }
        )

    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_CSV, index=False)
    write_analysis(result)
    return result


def write_analysis(result: pd.DataFrame) -> None:
    """Write filter contributions and the research recommendation."""
    baseline = result[result["experiment"] == "Baseline"].iloc[0]
    comparisons = []
    for experiment, filter_name in FILTER_BY_EXPERIMENT.items():
        row = result[result["experiment"] == experiment].iloc[0]
        comparisons.append(
            {
                "experiment": experiment,
                "filter": filter_name,
                "signal_increase": int(row["signals"] - baseline["signals"]),
                "win_rate_contribution": baseline["win_rate"] - row["win_rate"],
                "expectancy_contribution": baseline["expectancy_pct"]
                - row["expectancy_pct"],
                "removed_expectancy": row["expectancy_pct"],
                "removed_win_rate": row["win_rate"],
                "removed_completed_trades": int(row["completed_trades"]),
            }
        )

    contribution = pd.DataFrame(comparisons)
    expectancy_filter = contribution.sort_values(
        "expectancy_contribution", ascending=False
    ).iloc[0]
    win_rate_filter = contribution.sort_values(
        "win_rate_contribution", ascending=False
    ).iloc[0]
    opportunity_filter = contribution.sort_values(
        "signal_increase", ascending=False
    ).iloc[0]

    unnecessary = contribution[
        (contribution["expectancy_contribution"] <= 0)
        & (contribution["win_rate_contribution"] <= 0)
    ].sort_values(
        ["signal_increase", "removed_completed_trades", "removed_expectancy"],
        ascending=False,
    )

    positive_expectancy = expectancy_filter["expectancy_contribution"] > 0
    positive_win_rate = win_rate_filter["win_rate_contribution"] > 0

    if unnecessary.empty:
        unnecessary_answer = "None of the tested filters improved both metrics when removed."
        recommendation = "Keep the Baseline filter set for Strategy Version 1.1."
    else:
        candidate = unnecessary.iloc[0]
        unnecessary_answer = (
            f"{candidate['filter']}: removing it did not reduce expectancy or win rate."
        )
        recommendation = (
            f"Candidate Strategy Version 1.1: remove the {candidate['filter']} and retain "
            "all other Baseline rules, subject to out-of-sample validation."
        )

    table = result.to_string(index=False)
    lines = [
        "FILTER ABLATION STUDY - STRATEGY 1.0",
        "=" * 44,
        "",
        table,
        "",
        "1. Which filter contributes most to expectancy?",
        (
            f"> {expectancy_filter['filter']} ({expectancy_filter['expectancy_contribution']:+.2f} percentage points versus removal)."
            if positive_expectancy
            else "> None. Every tested single-filter removal increased observed expectancy."
        ),
        "",
        "2. Which filter contributes most to win rate?",
        (
            f"> {win_rate_filter['filter']} ({win_rate_filter['win_rate_contribution']:+.2f} percentage points versus removal)."
            if positive_win_rate
            else "> None. Every tested single-filter removal increased observed win rate."
        ),
        "",
        "3. Which filter removes the most opportunities?",
        f"> {opportunity_filter['filter']} ({int(opportunity_filter['signal_increase']):+d} signals when removed).",
        "",
        "4. Which filter appears unnecessary?",
        f"> {unnecessary_answer}",
        "",
        "5. Recommended Strategy Version 1.1",
        f"> {recommendation}",
        "> Research result only; production Strategy Version 1.0 was not modified.",
    ]
    ANALYSIS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> bool:
    """Command-line entry point."""
    try:
        result = run_ablation()
        print(result.to_string(index=False))
        logger.info("Ablation results written to %s", OUTPUT_CSV)
        logger.info("Ablation analysis written to %s", ANALYSIS_FILE)
        return True
    except Exception:
        logger.exception("Filter ablation failed")
        return False


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    raise SystemExit(0 if main() else 1)
