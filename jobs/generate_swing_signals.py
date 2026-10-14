"""
Nightly swing signals job
=========================
Runs after the US close on GitHub Actions (.github/workflows/swing_signals.yml). Every sleeve input
comes from src/swing/engine.py, the code the holdout backtest (scripts/research_swing.py) runs:
run_book() for the latest session, simulate_swing_trade() for open signals.

  1. Fresh daily bars for U1 (+ SPY, sector ETFs, open-signal tickers), incremental, own cache.
  2. Stale data: if SPY's latest bar is not the latest completed NYSE session, no BUYs are
     published; open signals are still reconciled against the bars that exist.
  3. SEC filings since 2006 (refreshed only for companies with new filings in the daily indexes),
     insider purchases (quarterly data sets + daily Form 4 XML), earnings dates for G0 from the
     earnings_calendar table, else estimated from SEC history, else issued with earnings_unknown.
  4. Reconcile open signals (stop / target / rule exits; pending SELLs decided at tonight's close).
  5. New signals per sleeve in its mode from config/swing_sleeves.json (LIVE on U1, LIVE-SP500 on
     current S&P 500 members, SHADOW tracked on U1 and never alerted). The combined live book holds
     one open signal per ticker, priority E > I > N > P > T.
  6. Supabase (swing_signals, swing_ledger, swing_events, swing_sleeve_status), ledger/*.jsonl,
     Apprise alerts (APPRISE_URLS) for LIVE sleeves only.
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("SKIP_NLP", "true")

from src.swing.engine import build_context, run_book  # noqa: E402
from src.swing.exits import max_holding_sessions_for_sleeve, simulate_swing_trade  # noqa: E402
from src.swing.ledger import native, record_ledger_entry  # noqa: E402
from src.swing.rules import SLEEVE_PRIORITY  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("generate_swing_signals")

CONFIG = os.path.join(PROJECT_ROOT, "config", "swing_sleeves.json")
STATE = os.path.join(PROJECT_ROOT, "data", "swing_live", "state.json")
LIVE_SEC_DIR = os.path.join(PROJECT_ROOT, "data", "swing_live", "sec_filings")
SLEEVES = ("T", "E", "N", "P", "I")
SLEEVE_NAMES = {"T": "Trend", "E": "Earnings reaction", "N": "Catalyst gap", "P": "Large-cap pullback",
                "I": "Insider cluster buying"}
LIVE_MODES = ("LIVE", "LIVE-SP500")
PANEL_SESSIONS = 800


def load_config() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Sleeve modes from the research run. Anything missing or unproven is SHADOW."""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}
    sleeves = doc.get("sleeves") if isinstance(doc.get("sleeves"), dict) else {}
    prov = doc.get("provenance") or {}
    if not prov.get("github_run_id") or prov.get("smoke"):
        sleeves = {}                                         # no real research run: nothing is LIVE
    out = {}
    for s in SLEEVES:
        c = dict(sleeves.get(s) or {})
        c.setdefault("status", "SHADOW")
        c.setdefault("reasons", ["not yet tested"] if not sleeves else [])
        out[s] = c
    return out, prov


def client_or_none():
    try:
        from jobs.supabase_client import get_client
        return get_client()
    except Exception as e:
        logger.warning("Supabase unavailable (%s): nothing will be written to the database.", e)
        return None


def fetch_rows(client, table: str, **eq) -> List[Dict[str, Any]]:
    if client is None:
        return []
    out, start = [], 0
    while True:
        q = client.table(table).select("*")
        for k, v in eq.items():
            q = q.eq(k, v)
        r = q.range(start, start + 999).execute().data or []
        out += r
        if len(r) < 1000:
            return out
        start += 1000


def earnings_lookup(client, filings_ctx_tickers, cal):
    """G0 earnings dates for live: earnings_calendar table, else SEC estimate, else unknown."""
    from src.trade_hygiene import estimate_next_earnings
    table = {}
    for r in fetch_rows(client, "earnings_calendar"):
        nd = r.get("next_earnings_date")
        if nd:
            try:
                table[str(r["ticker"]).upper()] = date.fromisoformat(str(nd)[:10])
            except ValueError:
                pass

    def known(ctx_tickers):
        def f(t: str, d: date):
            a = ctx_tickers[t]
            past = [x for x in a.reactions if x <= d]
            nxt = table.get(t)
            if nxt is not None and nxt >= d:
                return sorted(set(past + [nxt, cal.shift(nxt, 1)])), False     # timing unknown: that day or the next
            est = estimate_next_earnings(a.reactions, d) if a.reactions else None
            if est is not None:
                return sorted(set(past + [est])), False
            return past, True
        return f
    return known, len(table)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="No database writes, no alerts")
    ap.add_argument("--limit", type=int, default=None, help="Smoke test on the first N U1 tickers")
    ap.add_argument("--no-alerts", action="store_true")
    ap.add_argument("--form4-budget", type=int, default=8000)
    args = ap.parse_args()

    from scripts.research_signal_ic import SECTOR_TO_ETF
    from scripts.validate_backtest_pipeline import production_universe
    from src.providers.context.sec_fundamentals import get_sec_provider
    from src.research.survivorship import load_membership
    from src.swing import live
    from src.swing.data import CikResolver, load_filings, load_price_frames, sector_map
    from src.swing.engine import qualifying_purchases
    from src.trade_hygiene import SessionCalendar

    health: Dict[str, Any] = {}
    cfg, prov = load_config()
    session = live.latest_completed_session()
    logger.info("Latest completed NYSE session: %s; sleeve modes %s (research run %s)", session,
                {s: c["status"] for s, c in cfg.items()}, prov.get("github_run_id"))
    client = None if args.dry_run else client_or_none()
    open_rows = fetch_rows(client, "swing_signals", status="open")
    recent_closed = [r for r in fetch_rows(client, "swing_signals", status="closed")
                     if r.get("exit_date") and r["exit_date"] >= (session - timedelta(days=30)).isoformat()]

    # 1. Prices
    u1, _ = production_universe()
    u1 = sorted(u1)[: args.limit] if args.limit else sorted(u1)
    etfs = sorted(set(SECTOR_TO_ETF.values()))
    want = sorted(set(u1) | set(etfs) | {"SPY"} | {r["ticker"] for r in open_rows})
    health["prices"] = live.update_live_prices(want)
    frames = load_price_frames(live.PRICE_DIR, want)
    if "SPY" not in frames:
        logger.error("No SPY bars: nothing can be evaluated.")
        return 1
    spy = frames.pop("SPY")
    etf_close = pd.DataFrame({e: frames[e]["TR_CLOSE"] for e in etfs if e in frames})
    for e in etfs:
        frames.pop(e, None)
    last_bar = spy.index[-1].date()
    stale = last_bar != session
    health["latest_bar"] = last_bar.isoformat()
    health["stale"] = stale
    if stale:
        logger.warning("[STALE DATA] SPY's latest bar %s is not the latest completed session %s: "
                       "no BUYs tonight; open signals are reconciled on the bars that exist.", last_bar, session)

    # 2. SEC filings (refresh companies with new filings since the last run) and insider data
    sec = get_sec_provider()
    resolver = CikResolver(sec)
    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE))
        except Exception:
            state = {}
    since = date.fromisoformat(state["last_session"]) if state.get("last_session") else session - timedelta(days=10)
    cal = SessionCalendar([d.date() for d in spy.index])
    days = [since + timedelta(days=k) for k in range((session - since).days + 1)]
    refresh = live.recent_filer_ciks(days, sec)
    filings, rights, health["sec"] = load_filings(frames, resolver, cache_dir=LIVE_SEC_DIR, refresh_ciks=refresh)
    health["sec"]["refreshed_ciks"] = len(refresh)

    insider = None
    try:
        from scripts.build_insider_history import build as build_insider, PURCHASES
        q_start = date(session.year - 4, 1, 1)
        health["insider_datasets"] = build_insider(start=q_start)
        hist = pd.read_parquet(PURCHASES)
        last_q_end = pd.to_datetime(hist["filing_date"]).max().date()
        # Clusters need the last 30 days of Form 4s; the routine test needs prior calendar years, which the
        # quarterly data sets cover. Daily XML therefore only for the last 40 days not yet in a data set.
        first_f4 = max(last_q_end + timedelta(days=1), session - timedelta(days=40))
        f4_days = [first_f4 + timedelta(days=k) for k in range((session - first_f4).days + 1)]
        f4, health["form4"] = live.form4_purchases(f4_days, set(resolver.ticker_map(frames)), sec, budget=args.form4_budget)
        raw = pd.concat([hist, f4], ignore_index=True) if len(f4) else hist
        cik_to_t = resolver.ticker_map(frames)
        insider = qualifying_purchases(raw, lambda c, s, fd: cik_to_t.get(int(c)) if c == c else None)
        health["insider_complete"] = health["form4"]["complete_days"] >= len(f4_days)
    except Exception as e:
        logger.warning("Insider data unavailable (%s): sleeve I issues no signals tonight.", e)
        health["insider_complete"] = False

    # 3. Context on U1 (and current S&P 500 members for LIVE-SP500 sleeves)
    first = spy.index[max(0, len(spy) - PANEL_SESSIONS)].date().isoformat()
    sectors = sector_map(frames)
    ctx = build_context("u1", frames, spy, etf_close, sectors, filings, rights, u1_tickers=set(u1),
                        window=(first, last_bar.isoformat()), with_c6=True,
                        insider=insider if health.get("insider_complete") else None)
    known, health["earnings_calendar_rows"] = earnings_lookup(client, None, ctx.cal)
    ctx.earnings_known = known(ctx.tickers)
    ctx_sp = None
    if any(c["status"] == "LIVE-SP500" for c in cfg.values()):
        m = load_membership()
        current = m[m["end_date"].isna()]
        ctx_sp = build_context("u2", frames, spy, etf_close, sectors, filings, rights, membership=current,
                               window=(first, last_bar.isoformat()), with_c6=True, insider=ctx.insider, tick=ctx.tickers)
        ctx_sp.earnings_known = ctx.earnings_known

    # 4. Reconcile open signals
    sells, warns = [], []
    now = datetime.now(timezone.utc).isoformat()
    for r in open_rows:
        t, sl = r["ticker"], r["sleeve"]
        a = ctx.tickers.get(t)
        if a is None:
            warns.append({"ticker": t, "warning": "no price data tonight: exits not checked"})
            continue
        sd = date.fromisoformat(r["signal_date"])
        i = a.pos(sd)
        if i is None:
            continue
        lk = a.locked_array()
        locked = [a.index[k].date() for k in range(i + 1, len(a.c)) if lk[k]]
        tr = simulate_swing_trade(t, sl, sd, float(r["stop_loss"]), float(r["target"]) if r.get("target") else None,
                                  a.bars(), (ctx.spy_open, ctx.spy_close), next_reaction_sessions=a.reactions,
                                  event_locked_sessions=locked, beta=r.get("beta"))
        upd: Dict[str, Any] = {"updated_at": now}
        if tr is not None:
            upd.update({"fill_date": tr.fill_date.isoformat(), "entry_fill_price": tr.fill_price,
                        "holding_sessions": tr.holding_sessions})
        if tr is not None and tr.exit_date is not None:
            upd.update({"status": "closed", "exit_date": tr.exit_date.isoformat(), "exit_price": tr.exit_price,
                        "exit_reason": tr.exit_reason, "net_return_pct": tr.net_return_pct,
                        "spy_return_pct": tr.spy_return_pct, "excess_pct": tr.excess_pct, "beta_adj_pct": tr.beta_adj_pct})
            sells.append({"ticker": t, "sleeve": sl, "reason": tr.exit_reason, "fill": tr.exit_price,
                          "net_return_pct": tr.net_return_pct, "holding_sessions": tr.holding_sessions,
                          "is_live": r.get("mode") in LIVE_MODES})
            record_ledger_entry(tr.exit_date, "SELL", t, sl, tr.exit_price,
                                details={"exit_reason": tr.exit_reason, "mode": r.get("mode")})
        elif tr is not None and (tr.details or {}).get("pending_exit"):
            upd["details"] = dict(r.get("details") or {}, pending_exit=tr.exit_reason, decided_on=tr.details["decided_on"])
            sells.append({"ticker": t, "sleeve": sl, "reason": f"{tr.exit_reason} (sell at the next open)", "fill": None,
                          "net_return_pct": None, "holding_sessions": tr.holding_sessions,
                          "is_live": r.get("mode") in LIVE_MODES})
        if client is not None:
            client.table("swing_signals").update(native(upd)).eq("id", r["id"]).execute()
    health["reconciled_open"] = len(open_rows)
    health["closed_tonight"] = sum(1 for s in sells if s["fill"] is not None)

    # 5. New signals for tonight's session (same run_book as the backtest), unless the data is stale
    new_rows: List[Dict[str, Any]] = []
    if not stale:
        still_open = {(r["sleeve"], r["ticker"]) for r in open_rows} - {(s["sleeve"], s["ticker"]) for s in sells if s["fill"] is not None}
        busy: Dict[str, Dict[str, date]] = defaultdict(dict)
        for sl, t in still_open:
            busy[sl][t] = date.max
        for r in recent_closed:
            busy[r["sleeve"]][r["ticker"]] = ctx.cal.shift(date.fromisoformat(r["exit_date"]), 11)
        by_sleeve = {}
        for sl in SLEEVES:
            mode = cfg[sl]["status"]
            if sl == "I" and not health.get("insider_complete"):
                logger.warning("Sleeve I: insider data incomplete, no signals tonight.")
                continue
            c = ctx_sp if mode == "LIVE-SP500" else ctx
            res = run_book(c, sl, last_bar, last_bar, open_until=busy[sl])
            by_sleeve[sl] = (mode, res["signals"], res["funnel"])
            health[f"sleeve_{sl}"] = {"mode": mode, "signals": len(res["signals"]), "funnel": res["funnel"]}
        live_tickers = {r["ticker"] for r in open_rows if r.get("mode") in LIVE_MODES}
        for sl in SLEEVE_PRIORITY:                            # combined live book: E > I > N > P > T
            if sl not in by_sleeve:
                continue
            mode, sigs, _ = by_sleeve[sl]
            for s in sigs:
                if mode in LIVE_MODES:
                    if s.ticker in live_tickers:
                        continue
                    live_tickers.add(s.ticker)
                a = ctx.tickers[s.ticker]
                fut = [x for x in a.reactions if x > s.signal_date]
                new_rows.append({
                    "ticker": s.ticker, "sleeve": sl, "status": "open", "mode": mode,
                    "signal_date": s.signal_date.isoformat(), "entry_signal_close": round(float(s.entry_signal_close), 4),
                    "stop_loss": float(s.stop_loss), "target": float(s.target) if s.target is not None else None,
                    "atr14": round(float(s.atr14), 4), "c6_pct": s.c6_pct, "ear_pct": s.ear_pct, "avol_pct": s.avol_pct,
                    "cluster_value": s.cluster_value, "gap_pct": s.gap_pct, "rsi2": s.rsi2, "beta": s.beta,
                    "earnings_unknown": bool(s.earnings_unknown), "earnings_date": fut[0].isoformat() if fut else None,
                    "details": native({k: v for k, v in (s.details or {}).items()
                                     if isinstance(v, (int, float, str, bool, date, np.generic)) or v is None}),
                    "created_at": now, "updated_at": now,
                })
                record_ledger_entry(s.signal_date, "BUY", s.ticker, sl, s.entry_signal_close,
                                    details={"mode": mode, "stop_loss": s.stop_loss, "target": s.target})
        for row in new_rows:
            if client is not None:
                client.table("swing_signals").upsert(native(row), on_conflict="sleeve,ticker,signal_date").execute()
                client.table("swing_ledger").insert(native({"session_date": row["signal_date"], "action": "BUY", "ticker": row["ticker"],
                                                     "sleeve": row["sleeve"], "price": row["entry_signal_close"],
                                                     "details": {"mode": row["mode"]}})).execute()
    for s in sells:
        if client is not None and s["fill"] is not None:
            client.table("swing_ledger").insert(native({"session_date": session.isoformat(), "action": "SELL", "ticker": s["ticker"],
                                                 "sleeve": s["sleeve"], "price": s["fill"],
                                                 "details": {"exit_reason": s["reason"]}})).execute()

    # 6. Sleeve status (real research numbers or "not yet tested")
    if client is not None:
        for sl in SLEEVES:
            c = cfg[sl]
            client.table("swing_sleeve_status").upsert(native({
                "sleeve": sl, "name": SLEEVE_NAMES[sl], "status": c["status"], "universe": c.get("universe", "shadow"),
                "u2_beta_adj": (c.get("u2") or {}).get("mean_beta_adj_pct"), "u1_beta_adj": (c.get("u1") or {}).get("mean_beta_adj_pct"),
                "reasons": c.get("reasons") or [],
                "details": {"u2": c.get("u2"), "u1": c.get("u1"), "last_session": last_bar.isoformat(), "stale": stale},
                "research_run_id": prov.get("github_run_id"), "last_evaluated_at": now,
            }), on_conflict="sleeve").execute()
        if not stale:
            client.table("swing_ledger").select("id").limit(1).execute()

    # 7. Alerts: LIVE sleeves only; shadow signals are computed and shown, never alerted
    from src.alerts import send_alerts
    live_buys = [{"ticker": r["ticker"], "sleeve": r["sleeve"], "is_live": True, "entry_signal_close": r["entry_signal_close"],
                  "stop_loss": r["stop_loss"], "target": r["target"], "why_summary": None}
                 for r in new_rows if r["mode"] in LIVE_MODES]
    live_sells = [s for s in sells if s["is_live"]]
    if not args.dry_run:
        health["alerts"] = send_alerts(sells=live_sells, buys=live_buys, warns=warns, no_alerts=args.no_alerts)

    if not args.dry_run and not stale:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump({"last_session": session.isoformat()}, open(STATE, "w"))
    health["new_signals"] = {"live": len(live_buys), "shadow": len(new_rows) - len(live_buys)}
    print("::notice title=Swing provider health::" + json.dumps(health, default=str)[:3500])
    logger.info("Swing signals done: %s", json.dumps(health, default=str)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
