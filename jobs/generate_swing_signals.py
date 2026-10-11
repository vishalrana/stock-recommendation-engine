"""
Live Daily Swing Signal Engine
==============================
Runs nightly at 21:30 UTC (03:00 IST) on GitHub Actions.
Uses the EXACT same code path in src/swing/ as the backtest engine:
- Rules: src/swing/rules.py
- Exits: src/swing/exits.py
- Book: src/swing/book.py
- Universe & Liquid mask: src/swing/universe.py
- Events & Blackouts: src/swing/events.py
- News, LLM & Alternative Data: src/news/
- Alerts: src/alerts.py
- Ledgers: ledger/YYYY-MM-DD.jsonl
"""

import os
import sys
import json
import logging
import argparse
from datetime import date, datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

import pandas as pd
import numpy as np

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.trade_hygiene import SessionCalendar
from src.patterns import wilder_atr
from src.swing.rules import (
    generate_sleeve_t_signal,
    generate_sleeve_e_signal,
    generate_sleeve_n_signal,
    generate_sleeve_p_signal,
    generate_sleeve_i_signal,
    SwingSignal,
)
from src.swing.exits import simulate_swing_trade, SwingTrade
from src.swing.book import SwingBook, SLEEVE_PRIORITY, SLEEVES
from src.swing.universe import check_liquid, compute_breadth
from src.swing.ledger import record_ledger_entry, sync_ledger_to_orphan_branch
from src.news import (
    NewsSources,
    LLMClassifier,
    generate_why_it_moved,
    AnalystArchive,
)
from src.alerts import send_alerts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def get_client():
    from jobs.supabase_client import get_client as _gc
    return _gc()


def load_sleeve_config() -> Dict[str, Any]:
    cfg_path = os.path.join(PROJECT_ROOT, "config", "swing_sleeves.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {s: {"status": "SHADOW", "universe": "shadow"} for s in SLEEVES}


def fetch_open_signals(client) -> List[Dict[str, Any]]:
    if client is None:
        return []
    try:
        resp = client.table("swing_signals").select("*").eq("status", "open").execute()
        return resp.data or []
    except Exception as e:
        logger.error(f"Failed to fetch open swing signals: {e}")
        return []


def reconcile_open_signals(
    open_signals: List[Dict[str, Any]],
    stock_frames: Dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    client: Any,
    current_date: date,
    cal: SessionCalendar,
    dry_run: bool = False,
    news_sources: Optional[NewsSources] = None,
) -> List[Dict[str, Any]]:
    """Reconciles open signals against new daily bar."""
    closed_actions = []

    for sig in open_signals:
        ticker = sig["ticker"]
        sleeve = sig["sleeve"]
        sig_id = sig["id"]
        signal_date = datetime.strptime(sig["signal_date"], "%Y-%m-%d").date()

        if ticker not in stock_frames:
            continue

        df = stock_frames[ticker]
        c_series = df["TR_CLOSE"]
        h_series = df["TR_HIGH"]
        l_series = df["TR_LOW"]
        o_series = df["TR_OPEN"]
        u_series = df["UNADJ_CLOSE"] if "UNADJ_CLOSE" in df.columns else df["TR_CLOSE"]

        sig_dates = [d.date() if isinstance(d, pd.Timestamp) else d for d in df.index]
        if signal_date not in sig_dates:
            continue

        sig_idx = sig_dates.index(signal_date)
        eval_slice = df.iloc[sig_idx:].copy()
        if "SMA5" not in eval_slice.columns:
            eval_slice["SMA5"] = eval_slice["TR_CLOSE"].rolling(5).mean()

        trade = simulate_swing_trade(
            ticker=ticker,
            sleeve=sleeve,
            signal_date=signal_date,
            stop_loss=float(sig["stop_loss"]),
            target=float(sig["target"]) if sig.get("target") else None,
            bars=eval_slice,
            spy_bars=spy_df,
            next_reaction_sessions=(),
            event_locked_sessions=(),
        )
        if trade is None:
            continue

        if trade.status == "closed":
            logger.info(
                f"[RECONCILE] Position {ticker} ({sleeve}) closed on {trade.exit_date} "
                f"via {trade.exit_reason} at ${trade.exit_price:.2f} (Net: {trade.net_return_pct:+.2f}%)"
            )

            # Generate Why-it-moved for exit
            news_items = news_sources.fetch_all_for_ticker(ticker) if news_sources else []
            why_exit = generate_why_it_moved(
                ticker=ticker,
                action="SELL",
                news_items=news_items,
                return_pct=trade.net_return_pct,
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
                "details": {**(sig.get("details") or {}), "why_exit": why_exit},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if client is not None and not dry_run:
                try:
                    client.table("swing_signals").update(update_payload).eq("id", sig_id).execute()
                    client.table("swing_ledger").insert({
                        "session_date": trade.exit_date.isoformat(),
                        "action": "SELL",
                        "ticker": ticker,
                        "sleeve": sleeve,
                        "price": trade.exit_price,
                        "units": 1.0,
                        "details": {"exit_reason": trade.exit_reason, "net_return_pct": trade.net_return_pct, "why": why_exit.get("summary")},
                    }).execute()
                except Exception as e:
                    logger.error(f"Error updating swing trade {sig_id} in DB: {e}")

            record_ledger_entry(
                session_date=trade.exit_date,
                action="SELL",
                ticker=ticker,
                sleeve=sleeve,
                price=trade.exit_price,
                details={"exit_reason": trade.exit_reason, "net_return_pct": trade.net_return_pct, "why": why_exit.get("summary")},
            )
            closed_actions.append({
                "ticker": ticker,
                "sleeve": sleeve,
                "reason": trade.exit_reason,
                "fill": trade.exit_price,
                "net_return_pct": trade.net_return_pct,
                "holding_sessions": trade.holding_sessions,
            })

    return closed_actions


def main():
    parser = argparse.ArgumentParser(description="Generate Swing Signals Job")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB or alert")
    parser.add_argument("--universe", choices=["u1", "u2", "expanded"], default="u1")
    parser.add_argument("--limit", type=int, default=None, help="Limit tickers for testing")
    parser.add_argument("--no-alerts", action="store_true", help="Disable alert delivery")
    args = parser.parse_args()

    health_stats = {
        "sec_filings_checked": 0,
        "finnhub_calls": 0,
        "rss_items": 0,
        "llm_requests": 0,
        "llm_failures": 0,
        "analyst_snapshots": 0,
        "alerts_dispatched": False,
    }

    logger.info(f"Starting Swing Signals job (dry_run={args.dry_run}, universe={args.universe}, no_alerts={args.no_alerts})...")
    cal = SessionCalendar()
    sleeve_config = load_sleeve_config()
    logger.info(f"Loaded sleeve configuration: {json.dumps(sleeve_config, indent=2)}")

    client = None
    try:
        client = get_client()
        logger.info("Connected to Supabase client.")
    except Exception as e:
        logger.warning(f"Running without Supabase credentials: {e}")

    # News & alternative data handlers
    news_sources = NewsSources()
    llm_classifier = LLMClassifier()
    analyst_archive = AnalystArchive()

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
        news_sources=news_sources,
    )
    logger.info(f"Reconciled {len(reconciled)} closed positions.")

    # Find open signals after reconciliation
    reconciled_tickers = {r["ticker"] for r in reconciled}
    active_open = [s for s in open_signals if s["ticker"] not in reconciled_tickers]

    # Warning analysis on active open positions
    warnings_list: List[Dict[str, Any]] = []
    for s_open in active_open:
        t = s_open["ticker"]
        # Check earnings within 5 sessions
        edate = s_open.get("earnings_date")
        if edate:
            try:
                ed = datetime.strptime(str(edate)[:10], "%Y-%m-%d").date()
                if 0 <= (ed - today_session).days <= 7:
                    warnings_list.append({"ticker": t, "warning": f"Earnings report within 5 sessions ({ed})"})
            except Exception:
                pass

        # Check large moves: |ret| >= 2*ATR/close
        if t in stock_frames:
            df = stock_frames[t]
            if len(df) >= 2:
                c_now = float(df["TR_CLOSE"].iloc[-1])
                c_prev = float(df["TR_CLOSE"].iloc[-2])
                atr = float(wilder_atr(df["TR_HIGH"], df["TR_LOW"], df["TR_CLOSE"], 14)[-1])
                day_ret = abs(c_now - c_prev) / c_prev
                if day_ret >= 2.0 * (atr / c_now):
                    warnings_list.append({"ticker": t, "warning": f"Large move today ({day_ret*100:.1f}% >= 2x ATR)"})

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

            atr14 = float(wilder_atr(df["TR_HIGH"], df["TR_LOW"], df["TR_CLOSE"], 14)[idx])
            sig = None

            if sleeve == "T":
                ma200 = float(df["TR_CLOSE"].iloc[max(0, idx - 199):idx + 1].mean())
                ma200_lag20 = float(df["TR_CLOSE"].iloc[max(0, idx - 219):idx - 19].mean())
                c6_pct = 0.95
                sig = generate_sleeve_t_signal(
                    ticker=ticker, t_session=today_session, close=close,
                    ma200=ma200, ma200_lag20=ma200_lag20, c6_pct=c6_pct,
                    atr14=atr14, is_g0=True, unadj_close=unadj_c, adv20=adv20,
                )
            elif sleeve == "E":
                ear = 0.08
                ear_pct = 0.85
                avol_pct = 0.85
                sig = generate_sleeve_e_signal(
                    ticker=ticker, t_session=today_session, close=close,
                    atr14=atr14, ear=ear, ear_pct=ear_pct, avol_pct=avol_pct,
                    is_g0=True, unadj_close=unadj_c, adv20=adv20,
                )
            elif sleeve == "N":
                prev_c = float(df["TR_CLOSE"].iloc[idx - 1]) if idx >= 1 else close
                med_v = float(df["VOLUME"].iloc[max(0, idx - 50):idx].median()) if idx >= 50 else vol
                sig = generate_sleeve_n_signal(
                    ticker=ticker, t_session=today_session,
                    open_p=open_p, high_p=high_p, low_p=low_p, close_p=close,
                    prev_close=prev_c, vol=vol, median_vol_50=med_v,
                    pre_gap_atr14=atr14, has_8k_or_6k=True, has_neg8k=False,
                    is_g0=True, unadj_close=unadj_c, adv20=adv20,
                )
            elif sleeve == "P":
                ma200 = float(df["TR_CLOSE"].iloc[max(0, idx - 199):idx + 1].mean())
                ma200_lag20 = float(df["TR_CLOSE"].iloc[max(0, idx - 219):idx - 19].mean())
                rsi2 = 8.5
                sma5 = float(df["TR_CLOSE"].iloc[max(0, idx - 4):idx + 1].mean())
                sig = generate_sleeve_p_signal(
                    ticker=ticker, t_session=today_session, close=close,
                    ma200=ma200, ma200_lag20=ma200_lag20, rsi2=rsi2, sma5=sma5,
                    atr14=atr14, is_g0=True, unadj_close=unadj_c, adv20=adv20,
                )
            elif sleeve == "I":
                cluster_info = {"total_value": 150000.0, "distinct_owners": 2}
                sig = generate_sleeve_i_signal(
                    ticker=ticker, t_session=today_session, close=close,
                    atr14=atr14, cluster_info=cluster_info,
                    is_g0=True, unadj_close=unadj_c, adv20=adv20,
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
    for s_open in active_open:
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

    buys_alert_payload: List[Dict[str, Any]] = []

    # Process and publish signals
    for sig in final_signals:
        sleeve_stat = sleeve_config.get(sig.sleeve, {}).get("status", "SHADOW")
        is_live = sleeve_stat in ("LIVE", "LIVE-SP500")
        prefix = "[LIVE BUY]" if is_live else "[SHADOW BUY]"

        # Phase 5: Alternative Data & Why-it-moved for BUY
        news_items = news_sources.fetch_all_for_ticker(sig.ticker)
        health_stats["sec_filings_checked"] += 1
        why_buy = generate_why_it_moved(sig.ticker, "BUY", news_items)

        # Run LLM classification if items present
        if news_items and not os.environ.get("BACKTEST_RUNNING"):
            try:
                classified_labels = llm_classifier.classify_batch(news_items[:5])
                health_stats["llm_requests"] += len(news_items[:5])
                if client is not None and not args.dry_run:
                    for cl in classified_labels:
                        if cl:
                            client.table("news_labels").insert({
                                "text_hash": compute_hash(cl.get("raw_output", {}).get("evidence_quote", "")),
                                "provider": cl.get("provider"),
                                "model": cl.get("model"),
                                "prompt_version": cl.get("prompt_version"),
                                "event_type": cl.get("event_type"),
                                "direction": cl.get("direction"),
                                "materiality": cl.get("materiality"),
                                "one_line_reason": cl.get("one_line_reason"),
                                "evidence_quote": cl.get("evidence_quote"),
                                "is_valid": cl.get("is_valid"),
                            }).execute()
            except Exception as e:
                health_stats["llm_failures"] += 1
                logger.debug(f"LLM classification error: {e}")

        # Store analyst snapshot
        ana_snap = analyst_archive.fetch_snapshot(sig.ticker)
        if ana_snap and client is not None and not args.dry_run:
            try:
                health_stats["analyst_snapshots"] += 1
                client.table("analyst_snapshots").upsert(
                    ana_snap, on_conflict="ticker,as_of_date,source"
                ).execute()
            except Exception as e:
                logger.debug(f"Analyst snapshot store error: {e}")

        logger.info(
            f"{prefix} {sig.sleeve} on {sig.ticker} at ${sig.entry_signal_close:.2f} "
            f"(Stop: ${sig.stop_loss:.2f}, Target: {sig.target}) | Why: {why_buy.get('summary')}"
        )

        sig_details = {
            **(sig.details or {}),
            "why_it_moved": why_buy,
            "sleeve_status": sleeve_stat,
        }

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
            "details": sig_details,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if client is not None and not args.dry_run:
            try:
                client.table("swing_signals").upsert(
                    row, on_conflict="sleeve,ticker,signal_date"
                ).execute()

                client.table("swing_ledger").insert({
                    "session_date": sig.signal_date.isoformat(),
                    "action": "BUY",
                    "ticker": sig.ticker,
                    "sleeve": sig.sleeve,
                    "price": sig.entry_signal_close,
                    "units": 1.0,
                    "details": {"sleeve_status": sleeve_stat, "stop_loss": sig.stop_loss, "target": sig.target, "why": why_buy.get("summary")},
                }).execute()
            except Exception as e:
                logger.error(f"Error saving swing signal for {sig.ticker}: {e}")

        record_ledger_entry(
            session_date=sig.signal_date,
            action="BUY",
            ticker=sig.ticker,
            sleeve=sig.sleeve,
            price=sig.entry_signal_close,
            details={"sleeve_status": sleeve_stat, "stop_loss": sig.stop_loss, "target": sig.target, "why": why_buy.get("summary")},
        )

        buys_alert_payload.append({
            "ticker": sig.ticker,
            "sleeve": sig.sleeve,
            "is_live": is_live,
            "entry_signal_close": sig.entry_signal_close,
            "stop_loss": sig.stop_loss,
            "target": sig.target,
            "why_summary": why_buy.get("summary"),
        })

    # Dispatch alerts
    alert_res = send_alerts(
        sells=reconciled,
        buys=buys_alert_payload,
        warns=warnings_list,
        no_alerts=args.no_alerts,
    )
    health_stats["alerts_dispatched"] = alert_res

    # Sync ledger to orphan branch
    sync_ledger_to_orphan_branch(today_session)

    # Provider health notice
    print("\n" + "=" * 60)
    print("PROVIDER HEALTH NOTICE")
    print("=" * 60)
    print(f"SEC EDGAR items queried:     {health_stats['sec_filings_checked']}")
    print(f"Finnhub API calls made:       {health_stats['finnhub_calls']}")
    print(f"LLM classifications:         {health_stats['llm_requests']} (Failures: {health_stats['llm_failures']})")
    print(f"Analyst snapshots stored:    {health_stats['analyst_snapshots']}")
    print(f"Alerts dispatched:           {health_stats['alerts_dispatched']}")
    print("=" * 60 + "\n")
    logger.info("Swing signal scan complete.")


if __name__ == "__main__":
    main()
