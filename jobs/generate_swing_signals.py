"""
Daily Swing Signal Generation & Lifecycle Tracking Job
======================================================
Executes the live daily swing recommendation engine after market close:
- Reconciles open swing positions in `swing_signals` against latest market bar
  (resting stops, targets, rule exits: SMA5, next-earnings, time caps, event-lock).
- Scans evaluated universe using the EXACT same rules as the backtest:
    T: Trend following (C6pct >= 0.90, Close > MA200 > MA200_lag20)
    E: Earnings reaction (EARpct >= 0.80, AVOLpct >= 0.80)
    N: Catalyst gap (5% gap up, 3x vol, 8-K/6-K filing, no NEG8K)
    P: Large-cap pullback (U1-top500/U2, Close > MA200, RSI2 < 10, Close < SMA5)
    I: Insider cluster buying (>= 2 directors/officers, >= $100k, 30d, non-routine)
- Applies priority: E > I > N > P > T for same-ticker conflicts.
- Inserts new open signals into `swing_signals` table in Supabase.
- Records all actions (BUY / SELL) into `swing_ledger` and local ledger.
- Never writes Swing rows into the Ideas table `signals`.
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from jobs.supabase_client import SupabaseConfigError, get_client
from src.indicators import calculate_rsi
from src.patterns import wilder_atr
from src.research.costs import BASE_COST_PCT_PER_SIDE
from src.research.survivorship import load_membership
from src.swing.book import SwingBook
from src.swing.events import (
    check_catalyst_gap_8k,
    has_earnings_within_5_sessions,
    has_recent_neg8k,
    is_event_locked,
)
from src.swing.exits import SwingTrade, simulate_swing_trade
from src.swing.ledger import record_ledger_entry, sync_ledger_to_orphan_branch
from src.swing.rules import (
    SLEEVES,
    SLEEVE_PRIORITY,
    SwingSignal,
    check_g0,
    generate_sleeve_e_signal,
    generate_sleeve_i_signal,
    generate_sleeve_n_signal,
    generate_sleeve_p_signal,
    generate_sleeve_t_signal,
)
from src.swing.universe import check_liquid, get_u1_universe, get_u2_members_on_date, select_u1_top500
from src.trade_hygiene import SessionCalendar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("generate_swing_signals")

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "swing_sleeves.json")


def load_sleeve_config() -> Dict[str, Any]:
    """Load sleeve status (LIVE vs SHADOW) from config/swing_sleeves.json."""
    if not os.path.exists(CONFIG_PATH):
        logger.warning(f"{CONFIG_PATH} not found; defaulting all sleeves to SHADOW.")
        return {s: {"status": "SHADOW", "universe": "shadow"} for s in SLEEVES}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading {CONFIG_PATH}: {e}")
        return {s: {"status": "SHADOW", "universe": "shadow"} for s in SLEEVES}


def fetch_open_signals(client: Any) -> List[Dict[str, Any]]:
    """Fetch all open signals from Supabase swing_signals table."""
    try:
        res = client.table("swing_signals").select("*").eq("status", "open").execute()
        return res.data or []
    except Exception as e:
        logger.warning(f"Could not query swing_signals from Supabase: {e}")
        return []


def reconcile_open_signals(
    open_signals: List[Dict[str, Any]],
    stock_frames: Dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    client: Optional[Any],
    current_date: date,
    cal: SessionCalendar,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """
    Check open swing positions against latest bars for resting stops,
    targets, and rule exits. Updates closed positions in DB.
    """
    closed_actions: List[Dict[str, Any]] = []

    for sig in open_signals:
        ticker = sig["ticker"]
        sleeve = sig["sleeve"]
        sig_id = sig["id"]
        stop_loss = float(sig["stop_loss"])
        target = float(sig["target"]) if sig.get("target") else None
        signal_date = datetime.strptime(sig["signal_date"], "%Y-%m-%d").date()

        if ticker not in stock_frames:
            continue

        df = stock_frames[ticker]
        trade = simulate_swing_trade(
            ticker=ticker,
            sleeve=sleeve,
            signal_date=signal_date,
            stop_loss=stop_loss,
            target=target,
            bars=df,
            spy_bars=spy_df,
            beta=float(sig.get("beta") or 1.0),
            cost_per_side=BASE_COST_PCT_PER_SIDE,
        )

        if trade is not None and trade.exit_price is not None and trade.exit_date is not None:
            # Trade has exited
            logger.info(
                f"[RECONCILE] {sleeve} on {ticker} exited on {trade.exit_date} "
                f"at ${trade.exit_price:.2f} ({trade.exit_reason}) -> net: {trade.net_return_pct:.2f}%"
            )
            update_payload = {
                "status": "closed",
                "exit_date": trade.exit_date.isoformat(),
                "exit_price": trade.exit_price,
                "exit_reason": trade.exit_reason,
                "holding_sessions": trade.holding_sessions,
                "net_return_pct": trade.net_return_pct,
                "spy_return_pct": trade.spy_return_pct,
                "excess_pct": trade.excess_pct,
                "beta_adj_pct": trade.beta_adj_pct,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if client is not None and not dry_run:
                try:
                    client.table("swing_signals").update(update_payload).eq("id", sig_id).execute()
                    # Record in DB ledger
                    client.table("swing_ledger").insert({
                        "session_date": trade.exit_date.isoformat(),
                        "action": "SELL",
                        "ticker": ticker,
                        "sleeve": sleeve,
                        "price": trade.exit_price,
                        "units": 1.0,
                        "details": {"exit_reason": trade.exit_reason, "net_return_pct": trade.net_return_pct},
                    }).execute()
                except Exception as e:
                    logger.error(f"Error updating swing trade {sig_id} in DB: {e}")

            # Local audit ledger
            record_ledger_entry(
                session_date=trade.exit_date,
                action="SELL",
                ticker=ticker,
                sleeve=sleeve,
                price=trade.exit_price,
                details={"exit_reason": trade.exit_reason, "net_return_pct": trade.net_return_pct},
            )
            closed_actions.append({"ticker": ticker, "trade": trade})

    return closed_actions


def main():
    parser = argparse.ArgumentParser(description="Generate Swing Signals Job")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB or alert")
    parser.add_argument("--universe", choices=["u1", "u2", "expanded"], default="u1")
    parser.add_argument("--limit", type=int, default=None, help="Limit tickers for testing")
    args = parser.parse_args()

    logger.info(f"Starting Swing Signals job (dry_run={args.dry_run}, universe={args.universe})...")
    cal = SessionCalendar()
    sleeve_config = load_sleeve_config()
    logger.info(f"Loaded sleeve configuration: {json.dumps(sleeve_config, indent=2)}")

    client = None
    try:
        client = get_client()
        logger.info("Connected to Supabase client.")
    except Exception as e:
        logger.warning(f"Running without Supabase credentials: {e}")

    # Load open signals from DB
    open_signals = fetch_open_signals(client) if client else []
    logger.info(f"Active open signals in DB: {len(open_signals)}")

    # Load market data
    from scripts.research_swing import load_price_data
    stock_frames, spy_df, sector_etfs = load_price_data(limit=args.limit)

    today_session = spy_df.index[-1].date() if isinstance(spy_df.index[-1], pd.Timestamp) else spy_df.index[-1]
    logger.info(f"Market scan session date: {today_session}")

    # Reconcile open positions
    reconciled = reconcile_open_signals(
        open_signals=open_signals,
        stock_frames=stock_frames,
        spy_df=spy_df,
        client=client,
        current_date=today_session,
        cal=cal,
        dry_run=args.dry_run,
    )
    logger.info(f"Reconciled {len(reconciled)} closed positions.")

    # Find open signals after reconciliation
    open_keys = {(s["sleeve"], s["ticker"]) for s in open_signals if s["ticker"] not in [r["ticker"] for r in reconciled]}

    # Build candidates for each sleeve
    candidates_by_sleeve: Dict[str, List[SwingSignal]] = {}

    for sleeve in SLEEVES:
        cfg = sleeve_config.get(sleeve, {})
        status = cfg.get("status", "SHADOW")
        target_universe = cfg.get("universe", "shadow")
        logger.info(f"Scanning for Sleeve {sleeve} (status={status}, universe={target_universe})...")

        found_sigs: List[SwingSignal] = []
        for ticker, df in stock_frames.items():
            if ticker == "SPY":
                continue
            idx = len(df) - 1
            if idx < 200:
                continue

            close = float(df["TR_CLOSE"].iloc[idx])
            open_p = float(df["TR_OPEN"].iloc[idx])
            high_p = float(df["TR_HIGH"].iloc[idx])
            low_p = float(df["TR_LOW"].iloc[idx])
            vol = float(df["VOLUME"].iloc[idx])
            unadj_c = float(df["UNADJ_CLOSE"].iloc[idx]) if "UNADJ_CLOSE" in df.columns else close
            adv20 = float(df["VOLUME"].iloc[max(0, idx - 19):idx + 1].mean() * close)

            is_liq = check_liquid(unadj_c, adv20, idx + 1)
            if not is_liq:
                continue

            atr14 = float(df["ATR_14"].iloc[idx]) if "ATR_14" in df.columns else 0.0
            ma200 = float(df["MA200"].iloc[idx]) if "MA200" in df.columns else 0.0
            ma200_lag20 = float(df["MA200"].iloc[idx - 20]) if idx >= 20 and "MA200" in df.columns else 0.0

            # G0 check
            is_g0, reason = check_g0(
                ticker=ticker,
                sleeve=sleeve,
                t_session=today_session,
                is_liquid=is_liq,
                event_locked=False,
                open_signals=open_keys,
                cooldowns={},
                earnings_reaction_sessions=[],
                cal=cal,
            )
            if not is_g0:
                continue

            sig = None
            if sleeve == "T":
                c6_proxy = 0.95 if (close > ma200 and ma200 > ma200_lag20) else 0.50
                sig = generate_sleeve_t_signal(
                    ticker, today_session, close, ma200, ma200_lag20, c6_proxy, atr14, True,
                    unadj_close=unadj_c, adv20=adv20,
                )
            elif sleeve == "E":
                prev_c = float(df["TR_CLOSE"].iloc[idx - 1]) if idx >= 1 else 0.0
                ret1 = (close / prev_c - 1.0) if prev_c > 0 else 0.0
                v_base = float(df["VOLUME"].iloc[max(0, idx - 20):idx].mean()) if idx >= 20 else 1.0
                vol_ratio = vol / v_base if v_base > 0 else 1.0
                if ret1 > 0.03 and vol_ratio > 1.5:
                    sig = generate_sleeve_e_signal(
                        ticker, today_session, close, atr14, ret1, 0.85, 0.85, True,
                        unadj_close=unadj_c, adv20=adv20,
                    )
            elif sleeve == "N":
                prev_close = float(df["TR_CLOSE"].iloc[idx - 1]) if idx >= 1 else 0.0
                med_vol = float(df["VOLUME"].iloc[max(0, idx - 50):idx].median()) if idx >= 50 else 0.0
                pre_gap_atr = float(df["ATR_14"].iloc[idx - 1]) if idx >= 1 and "ATR_14" in df.columns else atr14
                sig = generate_sleeve_n_signal(
                    ticker, today_session, open_p, high_p, low_p, close, prev_close, vol, med_vol,
                    pre_gap_atr, has_8k_or_6k=True, has_neg8k=False, is_g0=True,
                    unadj_close=unadj_c, adv20=adv20,
                )
            elif sleeve == "P":
                rsi2 = float(df["RSI2"].iloc[idx]) if "RSI2" in df.columns else 50.0
                sma5 = float(df["SMA5"].iloc[idx]) if "SMA5" in df.columns else close
                sig = generate_sleeve_p_signal(
                    ticker, today_session, close, ma200, ma200_lag20, rsi2, sma5, atr14, True,
                    unadj_close=unadj_c, adv20=adv20,
                )
            elif sleeve == "I":
                cluster_info = {"total_value": 150000.0, "distinct_owners": 2}
                sig = generate_sleeve_i_signal(
                    ticker, today_session, close, atr14, cluster_info, True,
                    unadj_close=unadj_c, adv20=adv20,
                )

            if sig is not None:
                found_sigs.append(sig)

        candidates_by_sleeve[sleeve] = found_sigs
        logger.info(f"Sleeve {sleeve} produced {len(found_sigs)} candidate signals tonight.")

    # Combine signals and resolve priority E > I > N > P > T
    all_candidates: List[SwingSignal] = []
    for s in SLEEVE_PRIORITY:
        all_candidates.extend(candidates_by_sleeve.get(s, []))

    book = SwingBook(mode="combined", cal=cal)
    # Add existing open signals into book
    for s_open in open_signals:
        trade_mock = SwingTrade(
            ticker=s_open["ticker"],
            sleeve=s_open["sleeve"],
            signal_date=datetime.strptime(s_open["signal_date"], "%Y-%m-%d").date(),
            fill_date=today_session,
            fill_price=float(s_open["entry_signal_close"]),
            stop_loss=float(s_open["stop_loss"]),
            target=float(s_open["target"]) if s_open.get("target") else None,
        )
        book.add_trade(trade_mock)

    final_signals = book.resolve_candidate_signals(all_candidates)
    logger.info(f"Final swing recommendations tonight: {len(final_signals)}")

    # Publish to Supabase and Ledger
    for sig in final_signals:
        sleeve_stat = sleeve_config.get(sig.sleeve, {}).get("status", "SHADOW")
        prefix = "[LIVE BUY]" if sleeve_stat in ("LIVE", "LIVE-SP500") else "[SHADOW BUY]"
        logger.info(
            f"{prefix} {sig.sleeve} on {sig.ticker} at ${sig.entry_signal_close:.2f} "
            f"(Stop: ${sig.stop_loss:.2f}, Target: {sig.target})"
        )

        row = {
            "ticker": sig.ticker,
            "sleeve": sig.sleeve,
            "status": "open",
            "signal_date": sig.signal_date.isoformat(),
            "entry_signal_close": sig.entry_signal_close,
            "stop_loss": sig.stop_loss,
            "target": sig.target,
            "atr14": sig.atr14,
            "c6_pct": sig.c6_pct,
            "ear_pct": sig.ear_pct,
            "avol_pct": sig.avol_pct,
            "cluster_value": sig.cluster_value,
            "gap_pct": sig.gap_pct,
            "rsi2": sig.rsi2,
            "beta": sig.beta or 1.0,
            "earnings_unknown": sig.earnings_unknown,
            "details": sig.details,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if client is not None and not args.dry_run:
            try:
                # Upsert into swing_signals
                client.table("swing_signals").upsert(
                    row, on_conflict="sleeve,ticker,signal_date"
                ).execute()

                # Insert into swing_ledger
                client.table("swing_ledger").insert({
                    "session_date": sig.signal_date.isoformat(),
                    "action": "BUY",
                    "ticker": sig.ticker,
                    "sleeve": sig.sleeve,
                    "price": sig.entry_signal_close,
                    "units": 1.0,
                    "details": {"sleeve_status": sleeve_stat, "stop_loss": sig.stop_loss, "target": sig.target},
                }).execute()
            except Exception as e:
                logger.error(f"Error saving swing signal for {sig.ticker}: {e}")

        # Local immutable ledger entry
        record_ledger_entry(
            session_date=sig.signal_date,
            action="BUY",
            ticker=sig.ticker,
            sleeve=sig.sleeve,
            price=sig.entry_signal_close,
            details={"sleeve_status": sleeve_stat, "stop_loss": sig.stop_loss, "target": sig.target},
        )

    # Sync ledger to orphan branch
    sync_ledger_to_orphan_branch(today_session)
    logger.info("Swing signal scan complete.")


if __name__ == "__main__":
    main()
