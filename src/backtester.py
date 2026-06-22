"""
Historical backtest engine for frozen Strategy Version 1.0.

Signals are calculated using data available through day T. Entry orders become
active on T+1 and remain active for five trading sessions. Each signal is
evaluated independently; this module does not model portfolio constraints.
"""

import logging
from datetime import timedelta
from io import StringIO
from statistics import mean, median
from typing import Dict, List, Optional

import pandas as pd
import requests

from config import END_DATE, MIN_DATA_POINTS, OUTPUT_DIR
from downloader import fetch_ohlcv_data
from indicators import calculate_indicators, get_indicator_values
from patterns import detect_pattern
from risk import evaluate_trade
from scanner import SignalQualifier


logger = logging.getLogger(__name__)

BACKTEST_YEARS = 5
ENTRY_EXPIRY_DAYS = 5
UNIVERSE_SIZE = 100
OUTPUT_CSV = OUTPUT_DIR / "backtest_summary.csv"
DIAGNOSTICS_FILE = OUTPUT_DIR / "strategy_diagnostics.txt"
OUTPUT_COLUMNS = [
    "ticker",
    "industry",
    "signals",
    "wins",
    "losses",
    "win_rate",
    "median_holding_days",
    "average_holding_days",
    "expectancy_pct",
]


def fetch_validation_universe() -> tuple[List[str], Dict[str, str]]:
    """Return the first 100 current S&P 500 constituents and industries."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    response = requests.get(
        url,
        headers={"User-Agent": "stock-recommendation-engine/1.0"},
        timeout=15,
    )
    response.raise_for_status()
    table = pd.read_html(StringIO(response.text))[0].head(UNIVERSE_SIZE)

    tickers = []
    industries = {}
    for _, row in table.iterrows():
        ticker = str(row["Symbol"]).strip().upper().replace(".", "-")
        tickers.append(ticker)
        industries[ticker] = str(row["GICS Sub-Industry"]).strip()
    return tickers, industries


def create_signal(
    ticker: str,
    history: pd.DataFrame,
    diagnostics: Optional[Dict[str, int]] = None,
) -> Optional[dict]:
    """Recreate a Strategy 1.0 trade plan using only data through day T."""
    if diagnostics is not None:
        diagnostics["bars_evaluated"] += 1

    values = get_indicator_values(history, ticker)
    if values is None:
        if diagnostics is not None:
            diagnostics["indicator_failures"] += 1
        return None

    price, dma_50, dma_200, rsi, min_rsi, volume_ma, volume = values
    passes, filter_results = SignalQualifier.check_all_filters(
        price, dma_50, dma_200, rsi, min_rsi, volume_ma, volume
    )
    if diagnostics is not None:
        for filter_name, passed in filter_results.items():
            if not passed:
                diagnostics[f"{filter_name}_failures"] += 1
    if not passes:
        return None
    if diagnostics is not None:
        diagnostics["layer1_passes"] += 1

    pattern, entry_price = detect_pattern(history)
    if pattern is None or entry_price is None:
        if diagnostics is not None:
            diagnostics["pattern_failures"] += 1
        return None
    if diagnostics is not None:
        diagnostics["pattern_passes"] += 1

    signal = {
        "ticker": ticker,
        "pattern": pattern,
        "entry_price": entry_price,
    }
    trade, _ = evaluate_trade(signal, history)
    if diagnostics is not None:
        diagnostics["risk_passes" if trade is not None else "risk_failures"] += 1
    return trade


def simulate_trade(
    data: pd.DataFrame,
    signal_index: int,
    trade: dict,
) -> Optional[dict]:
    """Simulate one entry order and return a completed outcome, if any."""
    entry_price = float(trade["entry_price"])
    stop_loss = float(trade["stop_loss"])
    target = float(trade["exit_price"])

    first_eligible = signal_index + 1
    last_eligible = min(signal_index + ENTRY_EXPIRY_DAYS, len(data) - 1)
    entry_index = None

    for index in range(first_eligible, last_eligible + 1):
        if float(data["HIGH"].iloc[index]) >= entry_price:
            entry_index = index
            break

    if entry_index is None:
        return None

    for index in range(entry_index, len(data)):
        low = float(data["LOW"].iloc[index])
        high = float(data["HIGH"].iloc[index])
        stop_touched = low <= stop_loss
        target_touched = high >= target

        # Conservative ordering for daily candles: stop wins every ambiguity.
        if stop_touched:
            return {
                "outcome": "loss",
                "holding_days": index - entry_index,
                "return_pct": ((stop_loss - entry_price) / entry_price) * 100.0,
            }
        if target_touched:
            return {
                "outcome": "win",
                "holding_days": index - entry_index,
                "return_pct": ((target - entry_price) / entry_price) * 100.0,
            }

    return None


def backtest_ticker(
    ticker: str,
    data: pd.DataFrame,
    industry: str,
    diagnostics: Optional[Dict[str, int]] = None,
) -> dict:
    """Backtest all historical Strategy 1.0 signals for one ticker."""
    if data is None or data.empty:
        return {
            "ticker": ticker,
            "industry": industry,
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "median_holding_days": 0.0,
            "average_holding_days": 0.0,
            "expectancy_pct": 0.0,
        }

    data = calculate_indicators(data).sort_index()
    signals = 0
    outcomes = []

    # A signal needs at least one T+1 candle to be testable.
    for signal_index in range(MIN_DATA_POINTS - 1, len(data) - 1):
        history = data.iloc[: signal_index + 1]
        trade = create_signal(ticker, history, diagnostics)
        if trade is None:
            continue

        signals += 1
        outcome = simulate_trade(data, signal_index, trade)
        if outcome is not None:
            outcomes.append(outcome)

    wins = sum(outcome["outcome"] == "win" for outcome in outcomes)
    losses = sum(outcome["outcome"] == "loss" for outcome in outcomes)
    completed = wins + losses
    holding_days = [outcome["holding_days"] for outcome in outcomes]
    returns = [outcome["return_pct"] for outcome in outcomes]

    return {
        "ticker": ticker,
        "industry": industry,
        "signals": signals,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / completed) * 100.0, 2) if completed else 0.0,
        "median_holding_days": round(float(median(holding_days)), 2) if holding_days else 0.0,
        "average_holding_days": round(float(mean(holding_days)), 2) if holding_days else 0.0,
        "expectancy_pct": round(float(mean(returns)), 2) if returns else 0.0,
    }


def format_rankings(data: pd.DataFrame) -> str:
    """Format ranking rows for the diagnostics report."""
    if data.empty:
        return "None"
    columns = ["ticker", "signals", "wins", "losses", "win_rate", "expectancy_pct"]
    return data[columns].to_string(index=False)


def write_diagnostics(result: pd.DataFrame, filter_diagnostics: Dict[str, int]) -> None:
    """Write aggregate strategy and filter diagnostics."""
    total_stocks = len(result)
    total_signals = int(result["signals"].sum())
    total_wins = int(result["wins"].sum())
    total_losses = int(result["losses"].sum())
    completed = total_wins + total_losses
    overall_win_rate = (total_wins / completed * 100.0) if completed else 0.0

    if completed:
        z = 1.96
        proportion = total_wins / completed
        denominator = 1 + (z * z / completed)
        centre = (proportion + z * z / (2 * completed)) / denominator
        margin = (
            z
            * ((proportion * (1 - proportion) / completed + z * z / (4 * completed * completed)) ** 0.5)
            / denominator
        )
        confidence_interval = f"{(centre - margin) * 100:.2f}% to {(centre + margin) * 100:.2f}%"
    else:
        confidence_interval = "N/A"

    eligible = result[result["signals"] >= 5]
    top_win_rate = eligible.sort_values(
        ["win_rate", "expectancy_pct", "signals"], ascending=[False, False, False]
    ).head(10)
    top_expectancy = eligible.sort_values(
        ["expectancy_pct", "win_rate", "signals"], ascending=[False, False, False]
    ).head(10)
    bottom = result[(result["wins"] + result["losses"]) > 0].sort_values(
        ["expectancy_pct", "win_rate", "signals"], ascending=[True, True, False]
    ).head(10)

    bars = filter_diagnostics["bars_evaluated"]
    filter_lines = []
    for label, key in [
        ("Price Filter", "price_filter_failures"),
        ("RSI Minimum Filter", "rsi_min_filter_failures"),
        ("Current RSI Filter", "rsi_current_filter_failures"),
        ("Volume Filter", "volume_filter_failures"),
    ]:
        failures = filter_diagnostics[key]
        percentage = failures / bars * 100.0 if bars else 0.0
        filter_lines.append(f"{label}: {failures} failures ({percentage:.2f}% of evaluated bars)")

    lines = [
        "STRATEGY VERSION 1.0 - VALIDATION DIAGNOSTICS",
        "=" * 54,
        f"Total Stocks Tested: {total_stocks}",
        f"Total Signals Generated: {total_signals}",
        f"Total Wins: {total_wins}",
        f"Total Losses: {total_losses}",
        f"Completed Trades: {completed}",
        f"Untriggered or Unresolved Signals: {total_signals - completed}",
        f"Overall Win Rate: {overall_win_rate:.2f}%",
        f"Overall Win Rate 95% Wilson CI: {confidence_interval}",
        f"Average Signals Per Stock: {total_signals / total_stocks:.2f}" if total_stocks else "Average Signals Per Stock: 0.00",
        "",
        "Stocks With:",
        f"0 Signals: {(result['signals'] == 0).sum()}",
        f"1 Signal: {(result['signals'] == 1).sum()}",
        f"2-5 Signals: {result['signals'].between(2, 5).sum()}",
        f"5+ Signals: {(result['signals'] >= 5).sum()}",
        "Note: the requested 2-5 and 5+ ranges overlap for stocks with exactly 5 signals.",
        "",
        "Filter Diagnostics (independent failures; a bar may fail multiple filters):",
        *filter_lines,
        f"Layer 1 Passes: {filter_diagnostics['layer1_passes']}",
        f"Pattern Failures After Layer 1: {filter_diagnostics['pattern_failures']}",
        f"Pattern Passes: {filter_diagnostics['pattern_passes']}",
        f"Risk Failures After Pattern: {filter_diagnostics['risk_failures']}",
        f"Final Risk-Qualified Signals: {filter_diagnostics['risk_passes']}",
        "",
        "Top 10 Stocks By Win Rate (minimum 5 signals):",
        format_rankings(top_win_rate),
        "",
        "Top 10 Stocks By Expectancy (minimum 5 signals):",
        format_rankings(top_expectancy),
        "",
        "Bottom 10 Stocks (at least one completed trade, ranked by expectancy):",
        format_rankings(bottom),
    ]
    DIAGNOSTICS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_backtest() -> pd.DataFrame:
    """Run Strategy 1.0 against the first 100 current S&P 500 stocks."""
    tickers, industries = fetch_validation_universe()
    start_date = END_DATE - timedelta(days=365 * BACKTEST_YEARS)
    metrics = []
    filter_diagnostics = {
        "bars_evaluated": 0,
        "indicator_failures": 0,
        "price_filter_failures": 0,
        "rsi_min_filter_failures": 0,
        "rsi_current_filter_failures": 0,
        "volume_filter_failures": 0,
        "layer1_passes": 0,
        "pattern_failures": 0,
        "pattern_passes": 0,
        "risk_failures": 0,
        "risk_passes": 0,
    }

    logger.info(
        "Backtesting %s tickers from %s through %s",
        len(tickers),
        start_date,
        END_DATE,
    )

    for position, ticker in enumerate(tickers, 1):
        logger.info("[%s/%s] Backtesting %s", position, len(tickers), ticker)
        data = fetch_ohlcv_data(ticker, start_date=start_date, end_date=END_DATE)
        if data is None or data.empty:
            logger.warning("%s: no data; writing zero metrics", ticker)
            metrics.append(backtest_ticker(ticker, pd.DataFrame(), industries[ticker], filter_diagnostics))
            continue
        metrics.append(backtest_ticker(ticker, data, industries[ticker], filter_diagnostics))

    result = pd.DataFrame(metrics, columns=OUTPUT_COLUMNS)
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_CSV, index=False)
    write_diagnostics(result, filter_diagnostics)
    logger.info("Backtest summary written to %s", OUTPUT_CSV)
    logger.info("Strategy diagnostics written to %s", DIAGNOSTICS_FILE)
    return result


def main() -> bool:
    """Command-line entry point."""
    try:
        result = run_backtest()
        print(result.to_string(index=False))
        return True
    except Exception:
        logger.exception("Backtest failed")
        return False


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    raise SystemExit(0 if main() else 1)
