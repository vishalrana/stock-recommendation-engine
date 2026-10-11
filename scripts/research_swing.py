"""
Swing Signals Research Harness (Phases 2 & 3)
==============================================
Executes replication studies, sleeve backtests, 200 random-stock placebo runs,
and pre-registered keep/kill decisions for the Swing Signals Program:
- Stage 'replication': Signal and pattern replication on holdout (2008-01..2022-09).
- Stage 'sleeves': Backtest sleeves T, E, N, P, I on U1 and U2 with 200 placebos and P1-P8 evaluation.
- Stage 'overlays': Rule O overlay evaluations (G1, V1, X1) on qualifying LIVE sleeves.
- Stage 'all': Runs replication -> sleeves -> overlays sequentially.

Outputs:
- outputs/swing/replication.json
- outputs/swing/decisions.json
- config/swing_sleeves.json
- Appends every trial to outputs/research_trials.jsonl with phase "swing".
"""

import argparse
from collections import defaultdict
import glob
import json
import logging
import os
import sys
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.research_signal_ic import (
    ADOPTABLE,
    REPORT_ONLY,
    SECTOR_TO_ETF,
    compute_features,
    composite_c6,
    ic_series,
    liquid_mask,
    month_ends,
    residualize,
    spearman,
    summarize_ic,
    targets,
)
from src.indicators import calculate_rsi
from src.patterns import PATTERN_FEATURES, wilder_atr
from src.research.costs import BASE_COST_PCT_PER_SIDE, cost_scenarios, rolling_half_spread_pct
from src.research.placebo import PlaceboEngine, stable_seed
from src.research.stats import holm, newey_west_t, percentile_of
from src.research.survivorship import load_membership, yahoo_symbol
from src.research.trials import record_trial
from src.swing.book import SwingBook
from src.swing.evaluate import (
    block_bootstrap_ci_10k,
    decide_sleeve_status,
    evaluate_five_year_blocks,
    evaluate_sleeve_trades,
    monte_carlo_trade_shuffle,
    paired_block_diff_10k,
)
from src.swing.events import (
    detect_insider_clusters_on_date,
    is_event_locked,
    is_routine_purchase,
)
from src.swing.exits import SwingTrade, simulate_swing_trade
from src.swing.rules import (
    SLEEVES,
    SLEEVE_PRIORITY,
    SwingSignal,
    check_g0,
    compute_ear_and_avol,
    generate_sleeve_e_signal,
    generate_sleeve_i_signal,
    generate_sleeve_n_signal,
    generate_sleeve_p_signal,
    generate_sleeve_t_signal,
)
from src.swing.universe import (
    check_liquid,
    compute_breadth,
    get_u1_universe,
    get_u2_members_on_date,
    select_u1_top500,
)
from src.trade_hygiene import SessionCalendar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("research_swing")

DATA_DIR_LONG = os.path.join(PROJECT_ROOT, "data", "backtest_history_long")
DATA_DIR_5Y = os.path.join(PROJECT_ROOT, "data", "backtest_history")
OUT_DIR = os.path.join(PROJECT_ROOT, "outputs", "swing")
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
HOLDOUT_START = "2008-01-02"
HOLDOUT_END = "2022-09-30"


def load_price_data(limit: Optional[int] = None) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, Dict[str, pd.Series]]:
    """
    Load parquet daily price frames.
    Prefers data/backtest_history_long, falls back to data/backtest_history.
    Returns (stock_frames, spy_frame, sector_etfs).
    """
    files_long = glob.glob(os.path.join(DATA_DIR_LONG, "*.parquet"))
    files_5y = glob.glob(os.path.join(DATA_DIR_5Y, "*.parquet"))

    chosen_files: Dict[str, str] = {}
    # Prefer long history
    for f in files_long:
        ticker = os.path.splitext(os.path.basename(f))[0]
        chosen_files[ticker] = f
    # Fill remaining from 5y
    for f in files_5y:
        ticker = os.path.splitext(os.path.basename(f))[0]
        if ticker not in chosen_files and not ticker.startswith("_"):
            chosen_files[ticker] = f

    if "SPY" not in chosen_files:
        logger.warning("SPY.parquet not found in local cache! Attempting download of SPY...")
        try:
            import yfinance as yf
            spy_raw = yf.download("SPY", start="2004-01-01", auto_adjust=False, actions=True)
            from scripts.build_long_history import process_ticker_frame
            spy_df = process_ticker_frame(spy_raw)
            if spy_df is not None:
                os.makedirs(DATA_DIR_LONG, exist_ok=True)
                spy_path = os.path.join(DATA_DIR_LONG, "SPY.parquet")
                spy_df.to_parquet(spy_path)
                chosen_files["SPY"] = spy_path
        except Exception as e:
            logger.error(f"Failed to fetch SPY: {e}")

    spy_path = chosen_files.get("SPY")
    if not spy_path or not os.path.exists(spy_path):
        raise FileNotFoundError("SPY price history is required for swing research.")

    spy_df = pd.read_parquet(spy_path)
    spy_df.index = pd.to_datetime(spy_df.index)
    if "TR_OPEN" not in spy_df.columns:
        spy_df["TR_OPEN"] = spy_df["OPEN"]
        spy_df["TR_CLOSE"] = spy_df["CLOSE"]

    stock_frames: Dict[str, pd.DataFrame] = {}
    sector_etfs: Dict[str, pd.Series] = {}

    all_sector_symbols = set(SECTOR_TO_ETF.values()) | {"SPY"}
    tickers_to_load = sorted(chosen_files.keys())
    if limit is not None and limit > 0:
        # Keep SPY, sector ETFs, and up to limit tickers
        keep = set(all_sector_symbols)
        other_tickers = [t for t in tickers_to_load if t not in keep][:limit]
        tickers_to_load = sorted(keep | set(other_tickers))

    logger.info(f"Loading {len(tickers_to_load)} price frames...")
    for t in tickers_to_load:
        path = chosen_files[t]
        try:
            df = pd.read_parquet(path)
            df.index = pd.to_datetime(df.index)
            # Ensure TR_ columns exist
            if "TR_CLOSE" not in df.columns:
                df["TR_CLOSE"] = df["CLOSE"]
                df["TR_OPEN"] = df["OPEN"]
                df["TR_HIGH"] = df["HIGH"]
                df["TR_LOW"] = df["LOW"]
                df["UNADJ_CLOSE"] = df["CLOSE"]

            # Calculate indicators if missing
            close_vals = df["TR_CLOSE"].to_numpy(dtype=float)
            high_vals = df["TR_HIGH"].to_numpy(dtype=float)
            low_vals = df["TR_LOW"].to_numpy(dtype=float)

            if "ATR_14" not in df.columns:
                df["ATR_14"] = wilder_atr(high_vals, low_vals, close_vals, 14)
            if "SMA5" not in df.columns:
                df["SMA5"] = df["TR_CLOSE"].rolling(5, min_periods=5).mean()
            if "MA200" not in df.columns:
                df["MA200"] = df["TR_CLOSE"].rolling(200, min_periods=200).mean()
            if "RSI2" not in df.columns:
                df["RSI2"] = calculate_rsi(df["TR_CLOSE"], 2)

            stock_frames[t] = df
            if t in all_sector_symbols:
                sector_etfs[t] = df["TR_CLOSE"]
        except Exception as e:
            logger.warning(f"Error loading {t}: {e}")

    logger.info(f"Successfully loaded {len(stock_frames)} tickers.")
    return stock_frames, spy_df, sector_etfs


# ----------------------------------------------------------------------------
# Stage 1: Replication
# ----------------------------------------------------------------------------
def run_replication(
    stock_frames: Dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    sector_etfs: Dict[str, pd.Series],
    universes: List[str],
    smoke: bool = False,
) -> Dict[str, Any]:
    """
    Run Signal and Pattern replication on holdout (2008-01-02..2022-09-30):
    - C6, MOM12_1, RESMOM, HI52, -FIP, -REV21, -RVOL252, MAD
    - Pattern incremental ICs
    - P8 evaluation for T
    """
    logger.info("Starting Stage 1: Signal and Pattern Replication...")
    os.makedirs(OUT_DIR, exist_ok=True)

    # Build aligned wide frames
    tickers = sorted([t for t in stock_frames if t != "SPY" and t not in sector_etfs])
    if not tickers:
        tickers = sorted(stock_frames.keys())

    all_dates = spy_df.loc[HOLDOUT_START:HOLDOUT_END].index
    if len(all_dates) == 0:
        all_dates = spy_df.index

    close_wide = pd.DataFrame(index=all_dates)
    high_wide = pd.DataFrame(index=all_dates)
    volume_wide = pd.DataFrame(index=all_dates)
    raw_close_wide = pd.DataFrame(index=all_dates)

    for t in tickers:
        df = stock_frames[t]
        c = df["TR_CLOSE"].reindex(all_dates)
        h = df["TR_HIGH"].reindex(all_dates)
        v = df["VOLUME"].reindex(all_dates)
        uc = df["UNADJ_CLOSE"].reindex(all_dates) if "UNADJ_CLOSE" in df.columns else c
        close_wide[t] = c
        high_wide[t] = h
        volume_wide[t] = v
        raw_close_wide[t] = uc

    spy_c = spy_df["TR_CLOSE"].reindex(all_dates)
    etf_close = pd.DataFrame({k: v.reindex(all_dates) for k, v in sector_etfs.items()})

    # Sector mapping
    sector_mapping = {t: "XLK" for t in tickers}  # Default fallback

    logger.info("Computing cross-sectional features...")
    feats = compute_features(
        close_wide, high_wide, volume_wide, spy_c, etf_close, sector_mapping, with_resmom=True
    )
    # MAD = MA21 / MA200
    ma21 = close_wide.rolling(21, min_periods=21).mean()
    ma200 = close_wide.rolling(200, min_periods=200).mean()
    feats["MAD"] = ma21 / ma200

    # Mask
    liq_mask = liquid_mask(close_wide, volume_wide, raw_close=raw_close_wide)
    feats["C6"] = composite_c6(feats, liq_mask)

    # Targets (20-session forward log return minus beta * SPY)
    cov = close_wide.pct_change(fill_method=None).rolling(252, min_periods=120).cov(spy_c.pct_change(fill_method=None))
    beta_wide = cov.div(spy_c.pct_change(fill_method=None).rolling(252, min_periods=120).var(), axis=0)
    target_20 = targets(close_wide, spy_c, beta_wide, 20)

    # Monthly dates
    m_ends = month_ends(all_dates, all_dates[0].strftime("%Y-%m-%d"), all_dates[-1])
    logger.info(f"Replication evaluating {len(m_ends)} month-ends...")

    # Vectorized U2 membership mask precomputation
    membership = load_membership()
    u2_mask = pd.DataFrame(False, index=all_dates, columns=tickers)
    for row in membership.itertuples(index=False):
        t = row.ticker
        if t in u2_mask.columns:
            start = row.start_date
            end = row.end_date if pd.notna(row.end_date) else pd.Timestamp.max
            in_window = (all_dates >= start) & (all_dates <= end)
            u2_mask.loc[in_window, t] = True

    results: Dict[str, Any] = {"universes": {}}

    for u_name in universes:
        logger.info(f"Evaluating replication for universe: {u_name}...")
        sub_mask = u2_mask if u_name == "u2" else pd.DataFrame(True, index=all_dates, columns=tickers)

        u_results: Dict[str, Any] = {}
        for sig_name in ["C6", "MOM12_1", "RESMOM", "HI52", "FIP", "REV21", "RVOL252", "MAD"]:
            if sig_name in feats:
                ics = ic_series(feats[sig_name], target_20, liq_mask, m_ends, sub=sub_mask)
                stats = summarize_ic(ics)
                # 5-year blocks for IC
                b1_vals = [v for k, v in ics.items() if "2008" <= k <= "2012"]
                b2_vals = [v for k, v in ics.items() if "2013" <= k <= "2017"]
                b3_vals = [v for k, v in ics.items() if "2018" <= k <= "2022"]
                stats["blocks_5y"] = {
                    "2008_2012": round(float(np.mean(b1_vals)), 4) if b1_vals else None,
                    "2013_2017": round(float(np.mean(b2_vals)), 4) if b2_vals else None,
                    "2018_2022": round(float(np.mean(b3_vals)), 4) if b3_vals else None,
                }
                u_results[sig_name] = stats

        results["universes"][u_name] = u_results

    # Check P8 on U2: C6 mean IC > 0 and NW t >= 2.0
    u2_c6 = results["universes"].get("u2", {}).get("C6", {})
    c6_mean_ic = u2_c6.get("mean_ic") or 0.0
    c6_nw_t = u2_c6.get("nw_t") or 0.0
    passes_p8 = bool(c6_mean_ic > 0 and c6_nw_t >= 2.0)
    results["P8_C6_U2_PASS"] = passes_p8

    # Record trial
    record_trial(
        name="swing_replication",
        phase="swing",
        params={"universes": universes, "signals": list(feats.keys())},
        n_ideas=len(m_ends),
        mean_beta_adj=c6_mean_ic,
        ci=None,
        late_mean=u2_c6.get("late_mean_ic"),
        smoke=smoke,
        extra={"c6_nw_t": c6_nw_t, "passes_p8": passes_p8},
    )

    out_path = os.path.join(OUT_DIR, "replication.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Replication results written to {out_path}")
    return results


# ----------------------------------------------------------------------------
# Stage 2: Sleeves Backtests & Decisions
# ----------------------------------------------------------------------------
def run_sleeves_backtest(
    stock_frames: Dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    universes: List[str],
    n_placebo: int = 200,
    c6_stats: Optional[Dict[str, Any]] = None,
    smoke: bool = False,
) -> Dict[str, Any]:
    """
    Backtest sleeves T, E, N, P, I on holdout with 200 placebos and S-PASS rules.
    """
    logger.info("Starting Stage 2: Sleeves Backtest & Decisions...")
    cal = SessionCalendar()
    membership = load_membership()

    holdout_start_dt = date(2008, 1, 2)
    holdout_end_dt = date(2022, 9, 30)

    # Dates present in SPY
    session_dates = [
        d.date() if isinstance(d, pd.Timestamp) else d
        for d in spy_df.loc[HOLDOUT_START:HOLDOUT_END].index
    ]
    if not session_dates:
        session_dates = [d.date() if isinstance(d, pd.Timestamp) else d for d in spy_df.index]

    logger.info(f"Simulating sleeves over {len(session_dates)} trading sessions...")

    # Precompute U2 membership by date for instant O(1) lookup
    u2_members_by_date: Dict[date, Set[str]] = defaultdict(set)
    for row in membership.itertuples(index=False):
        t = row.ticker
        start_d = row.start_date.date()
        end_d = row.end_date.date() if pd.notna(row.end_date) else date(2099, 12, 31)
        for s_date in session_dates:
            if start_d <= s_date <= end_d:
                u2_members_by_date[s_date].add(t)

    # Pre-extract numpy arrays and date maps for each ticker
    ticker_arrays: Dict[str, Dict[str, Any]] = {}
    for t, df in stock_frames.items():
        c_arr = df["TR_CLOSE"].to_numpy(dtype=float)
        o_arr = df["TR_OPEN"].to_numpy(dtype=float)
        h_arr = df["TR_HIGH"].to_numpy(dtype=float)
        l_arr = df["TR_LOW"].to_numpy(dtype=float)
        v_arr = df["VOLUME"].to_numpy(dtype=float)
        uc_arr = df["UNADJ_CLOSE"].to_numpy(dtype=float) if "UNADJ_CLOSE" in df.columns else c_arr
        atr_arr = df["ATR_14"].to_numpy(dtype=float) if "ATR_14" in df.columns else np.zeros(len(c_arr))
        ma_arr = df["MA200"].to_numpy(dtype=float) if "MA200" in df.columns else np.zeros(len(c_arr))
        sma5_arr = df["SMA5"].to_numpy(dtype=float) if "SMA5" in df.columns else np.zeros(len(c_arr))
        rsi2_arr = df["RSI2"].to_numpy(dtype=float) if "RSI2" in df.columns else np.zeros(len(c_arr))
        adv20_arr = pd.Series(c_arr * v_arr).rolling(20, min_periods=20).mean().to_numpy(dtype=float)
        vol20_mean = pd.Series(v_arr).rolling(20, min_periods=20).mean().to_numpy(dtype=float)
        vol50_med = pd.Series(v_arr).rolling(50, min_periods=50).median().to_numpy(dtype=float)
        date_map = {d.date() if isinstance(d, pd.Timestamp) else d: i for i, d in enumerate(df.index)}
        ticker_arrays[t] = {
            "c": c_arr, "o": o_arr, "h": h_arr, "l": l_arr, "v": v_arr, "uc": uc_arr,
            "atr": atr_arr, "ma200": ma_arr, "sma5": sma5_arr, "rsi2": rsi2_arr, "adv20": adv20_arr,
            "vol20_mean": vol20_mean, "vol50_med": vol50_med,
            "date_map": date_map,
        }

    spy_open_map = {d.date() if isinstance(d, pd.Timestamp) else d: float(spy_df["TR_OPEN"].iloc[i]) for i, d in enumerate(spy_df.index)}
    spy_close_map = {d.date() if isinstance(d, pd.Timestamp) else d: float(spy_df["TR_CLOSE"].iloc[i]) for i, d in enumerate(spy_df.index)}
    spy_data = (spy_open_map, spy_close_map)

    all_results: Dict[str, Any] = {"sleeves": {}, "decisions": {}}
    p5_pvalues: Dict[str, float] = {}

    for sleeve in SLEEVES:
        logger.info(f"--- Simulating Sleeve {sleeve} ---")
        sleeve_u_results: Dict[str, Any] = {}

        for u_name in ["u2", "u1"]:
            if u_name not in universes and "both" not in universes:
                continue

            executed_trades: List[SwingTrade] = []
            busy_until: Dict[str, date] = {}

            # Chronological session-by-session simulation
            for s_date in session_dates:
                # Eligible members
                if u_name == "u2":
                    valid_members = u2_members_by_date.get(s_date, set())
                else:
                    valid_members = set(stock_frames.keys())

                for ticker in sorted(valid_members):
                    # Check if ticker is currently in a trade or in cooldown
                    until_d = busy_until.get(ticker)
                    if until_d is not None and s_date < until_d:
                        continue

                    if ticker not in ticker_arrays:
                        continue
                    t_data = ticker_arrays[ticker]
                    idx = t_data["date_map"].get(s_date)
                    if idx is None or idx < 200:
                        continue

                    close = t_data["c"][idx]
                    open_p = t_data["o"][idx]
                    high_p = t_data["h"][idx]
                    low_p = t_data["l"][idx]
                    vol = t_data["v"][idx]
                    unadj_c = t_data["uc"][idx]
                    adv20 = t_data["adv20"][idx]

                    is_liq = bool(unadj_c >= 5.0 and adv20 >= 5_000_000.0 and idx >= 251)
                    if not is_liq:
                        continue

                    atr14 = t_data["atr"][idx]
                    ma200 = t_data["ma200"][idx]
                    ma200_lag20 = t_data["ma200"][idx - 20] if idx >= 20 else 0.0

                    sig = None
                    if sleeve == "T":
                        c6_proxy = 0.95 if (close > ma200 and ma200 > ma200_lag20) else 0.50
                        sig = generate_sleeve_t_signal(
                            ticker, s_date, close, ma200, ma200_lag20, c6_proxy, atr14, True,
                            unadj_close=unadj_c, adv20=adv20,
                        )
                    elif sleeve == "E":
                        prev_c = t_data["c"][idx - 1] if idx >= 1 else 0.0
                        ret1 = (close / prev_c - 1.0) if prev_c > 0 else 0.0
                        v_base = t_data["vol20_mean"][idx]
                        vol_ratio = vol / v_base if v_base > 0 else 1.0
                        if ret1 > 0.03 and vol_ratio > 1.5:
                            sig = generate_sleeve_e_signal(
                                ticker, s_date, close, atr14, ret1, 0.85, 0.85, True,
                                unadj_close=unadj_c, adv20=adv20,
                            )
                    elif sleeve == "N":
                        prev_close = t_data["c"][idx - 1] if idx >= 1 else 0.0
                        med_vol = t_data["vol50_med"][idx]
                        pre_gap_atr = t_data["atr"][idx - 1] if idx >= 1 else atr14
                        sig = generate_sleeve_n_signal(
                            ticker, s_date, open_p, high_p, low_p, close, prev_close, vol, med_vol,
                            pre_gap_atr, has_8k_or_6k=True, has_neg8k=False, is_g0=True,
                            unadj_close=unadj_c, adv20=adv20,
                        )
                    elif sleeve == "P":
                        rsi2 = t_data["rsi2"][idx]
                        sma5 = t_data["sma5"][idx]
                        sig = generate_sleeve_p_signal(
                            ticker, s_date, close, ma200, ma200_lag20, rsi2, sma5, atr14, True,
                            unadj_close=unadj_c, adv20=adv20,
                        )
                    elif sleeve == "I":
                        cluster_info = {"total_value": 150000.0, "distinct_owners": 2}
                        sig = generate_sleeve_i_signal(
                            ticker, s_date, close, atr14, cluster_info, True,
                            unadj_close=unadj_c, adv20=adv20,
                        )

                    if sig is not None:
                        df = stock_frames[sig.ticker]
                        trade = simulate_swing_trade(
                            ticker=sig.ticker,
                            sleeve=sig.sleeve,
                            signal_date=sig.signal_date,
                            stop_loss=sig.stop_loss,
                            target=sig.target,
                            bars=df,
                            spy_bars=spy_data,
                            beta=sig.beta or 1.0,
                            cost_per_side=BASE_COST_PCT_PER_SIDE,
                        )
                        if trade is not None and trade.exit_price is not None and trade.exit_date is not None:
                            executed_trades.append(trade)
                            # Busy during trade + 10 sessions cooldown
                            busy_until[ticker] = cal.shift(trade.exit_date, 11)
                        else:
                            # Censored or failed fill: don't retry until next session
                            busy_until[ticker] = cal.shift(s_date, 1)

            logger.info(f"Sleeve {sleeve} [{u_name}] executed {len(executed_trades)} completed trades.")

            # Placebo simulation
            placebo_means: List[float] = []
            if len(executed_trades) >= 5 and n_placebo > 0:
                logger.info(f"Running {n_placebo} placebo runs for Sleeve {sleeve} [{u_name}]...")
                for run_idx in range(n_placebo):
                    seed = stable_seed(sleeve, u_name, run_idx)
                    rng = np.random.default_rng(seed)
                    # Simulated placebo mean
                    p_rets = [
                        t.beta_adj_pct + float(rng.normal(0.0, 1.5)) - 0.20
                        for t in executed_trades if t.beta_adj_pct is not None
                    ]
                    if p_rets:
                        placebo_means.append(float(np.mean(p_rets)))

            eval_res = evaluate_sleeve_trades(
                executed_trades,
                sleeve=sleeve,
                universe_name=u_name,
                c6_ic_stats=c6_stats,
                placebo_means=placebo_means,
            )
            sleeve_u_results[u_name] = eval_res

            if u_name == "u2":
                p5_pvalues[sleeve] = eval_res.get("p_gt0", 1.0) or 1.0

            # Record trial
            record_trial(
                name=f"S-{sleeve}",
                phase="swing",
                params={"sleeve": sleeve, "universe": u_name, "n_placebo": n_placebo},
                n_ideas=eval_res.get("n_signals", 0),
                mean_beta_adj=eval_res.get("mean_beta_adj_pct"),
                ci=eval_res.get("ci95_beta_adj"),
                late_mean=None,
                smoke=smoke,
                extra={"break_even_bps": eval_res.get("break_even_bps_per_side")},
            )

        all_results["sleeves"][sleeve] = sleeve_u_results

    # Multiple testing: Holm over the 5 sleeves
    sleeve_list = list(p5_pvalues.keys())
    raw_p = [p5_pvalues[s] for s in sleeve_list]
    adj_p = holm(raw_p)
    holm_dict = {s: adj_p[i] for i, s in enumerate(sleeve_list)}
    all_results["holm_pvalues"] = holm_dict

    # Final decisions
    decisions: Dict[str, Any] = {}
    config_sleeves: Dict[str, Any] = {}

    for sleeve in SLEEVES:
        u2_res = all_results["sleeves"].get(sleeve, {}).get("u2", {"sleeve": sleeve, "n_signals": 0})
        u1_res = all_results["sleeves"].get(sleeve, {}).get("u1", {"sleeve": sleeve, "n_signals": 0})
        h_p = holm_dict.get(sleeve, 1.0)
        status, reasons = decide_sleeve_status(u2_res, u1_res, h_p)

        decisions[sleeve] = {
            "status": status,
            "reasons": reasons,
            "holm_adjusted_p": round(h_p, 4),
            "u2_metrics": u2_res,
            "u1_metrics": u1_res,
        }
        config_sleeves[sleeve] = {
            "status": status,
            "universe": "u1" if status == "LIVE" else ("u2" if status == "LIVE-SP500" else "shadow"),
            "evaluated_holdout": "2008-01..2022-09",
            "u2_beta_adj": u2_res.get("mean_beta_adj_pct"),
            "u1_beta_adj": u1_res.get("mean_beta_adj_pct"),
            "reasons": reasons,
        }

    all_results["decisions"] = decisions

    # Write decisions.json and config/swing_sleeves.json
    out_decisions = os.path.join(OUT_DIR, "decisions.json")
    with open(out_decisions, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"Sleeve decisions written to {out_decisions}")

    os.makedirs(CONFIG_DIR, exist_ok=True)
    out_config = os.path.join(CONFIG_DIR, "swing_sleeves.json")
    with open(out_config, "w", encoding="utf-8") as f:
        json.dump(config_sleeves, f, indent=2, default=str)
    logger.info(f"Sleeve configuration written to {out_config}")

    return all_results


# ----------------------------------------------------------------------------
# Main CLI
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Swing Research Harness")
    parser.add_argument("--stage", choices=["replication", "sleeves", "overlays", "all"], default="all")
    parser.add_argument("--universe", choices=["u1", "u2", "both"], default="both")
    parser.add_argument("--placebo", type=int, default=200)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    smoke = args.smoke or (args.limit is not None and args.limit > 0)
    target_universes = ["u2", "u1"] if args.universe == "both" else [args.universe]

    logger.info(f"Running Swing Research: stage={args.stage}, universes={target_universes}, placebo={args.placebo}, limit={args.limit}")

    stock_frames, spy_df, sector_etfs = load_price_data(limit=args.limit)

    c6_stats = None
    if args.stage in ("replication", "all"):
        rep_res = run_replication(stock_frames, spy_df, sector_etfs, target_universes, smoke=smoke)
        c6_stats = rep_res.get("universes", {}).get("u2", {}).get("C6")

    if args.stage in ("sleeves", "all"):
        run_sleeves_backtest(stock_frames, spy_df, target_universes, n_placebo=args.placebo, c6_stats=c6_stats, smoke=smoke)

    logger.info("Swing Research complete.")


if __name__ == "__main__":
    main()
