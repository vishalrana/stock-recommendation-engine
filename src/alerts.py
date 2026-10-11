"""
Swing Signals Alerts Dispatcher (Apprise)
=========================================
Formats and delivers multi-channel notifications via Apprise:
- Order: SELL first, then BUY, then WARN
- Includes ticker, sleeve, levels, and IST execution time
- Splits messages at 3,500 characters
- Shadow sleeves never alert except MODE changes
- Failures degrade gracefully and never crash the run
"""

import os
import logging
from typing import List, Dict, Any, Optional
import datetime as dt

logger = logging.getLogger(__name__)

IST_OFFSET = dt.timezone(dt.timedelta(hours=5, minutes=30))


def format_ist_time(d: Optional[dt.datetime] = None) -> str:
    """Format timestamp in Indian Standard Time (IST)."""
    now = d or dt.datetime.now(dt.timezone.utc)
    ist = now.astimezone(IST_OFFSET)
    return ist.strftime("%Y-%m-%d %H:%M IST")


def build_alert_message(
    sells: List[Dict[str, Any]],
    buys: List[Dict[str, Any]],
    warns: List[Dict[str, Any]],
    mode_changes: Optional[List[Dict[str, Any]]] = None,
    execution_time: Optional[dt.datetime] = None,
) -> List[str]:
    """
    Build structured alert message chunks (max 3,500 characters each).
    Order: SELL first, then BUY, then WARN.
    """
    time_str = format_ist_time(execution_time)
    lines: List[str] = [f"📢 SWING SIGNALS REPORT — {time_str}\n"]

    has_content = False

    # 1. SELL first
    if sells:
        has_content = True
        lines.append("🔴 SELL (Execute at Next Open):")
        for s in sells:
            ticker = s.get("ticker", "")
            sleeve = s.get("sleeve", "")
            reason = s.get("reason", "Exit rule")
            fill = s.get("fill", s.get("price", 0.0))
            ret = s.get("net_return_pct", 0.0)
            held = s.get("holding_sessions", 0)
            lines.append(f"• {ticker} ({sleeve}) | Reason: {reason} | Exit: ${fill:.2f} | Net: {ret:+.2f}% | Held: {held}d")
        lines.append("")

    # 2. BUY next (LIVE sleeves only)
    live_buys = [b for b in buys if b.get("is_live", False)]
    if live_buys:
        has_content = True
        lines.append("🟢 BUY (Execute at Next Open):")
        for b in live_buys:
            ticker = b.get("ticker", "")
            sleeve = b.get("sleeve", "")
            ref_close = b.get("entry_signal_close", 0.0)
            stop = b.get("stop_loss", 0.0)
            target = b.get("target")
            tgt_str = f"${target:.2f}" if target else "none"
            risk_pct = ((ref_close - stop) / ref_close * 100.0) if ref_close > 0 else 0.0
            why = b.get("why_summary", "")
            lines.append(f"• {ticker} ({sleeve}) | Close: ${ref_close:.2f} | Stop: ${stop:.2f} | Tgt: {tgt_str} | Risk: {risk_pct:.1f}%")
            if why:
                lines.append(f"  ↳ {why}")
        lines.append("")

    # 3. Mode changes (if any)
    if mode_changes:
        has_content = True
        lines.append("⚡ MODE CHANGES:")
        for m in mode_changes:
            lines.append(f"• Sleeve {m.get('sleeve')}: {m.get('old_mode')} ➔ {m.get('new_mode')} ({m.get('reason')})")
        lines.append("")

    # 4. WARN
    if warns:
        has_content = True
        lines.append("⚠️ WARNINGS:")
        for w in warns:
            ticker = w.get("ticker", "")
            reason = w.get("warning", "")
            lines.append(f"• {ticker}: {reason}")
        lines.append("")

    if not has_content:
        return []

    # Chunk lines into <= 3500 chars
    chunks: List[str] = []
    current_chunk: List[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > 3500 and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = line_len
        else:
            current_chunk.append(line)
            current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def send_alerts(
    sells: List[Dict[str, Any]],
    buys: List[Dict[str, Any]],
    warns: List[Dict[str, Any]],
    mode_changes: Optional[List[Dict[str, Any]]] = None,
    apprise_urls: Optional[str] = None,
    no_alerts: bool = False,
) -> bool:
    """Send multi-channel alerts via Apprise."""
    if no_alerts:
        logger.info("Alerts disabled (--no-alerts flag).")
        return True

    urls_str = apprise_urls or os.environ.get("APPRISE_URLS", "")
    if not urls_str:
        logger.info("APPRISE_URLS not set. Skipping alerts.")
        return True

    chunks = build_alert_message(sells, buys, warns, mode_changes)
    if not chunks:
        logger.info("No alertable events tonight. Zero messages sent.")
        return True

    try:
        import apprise

        ap = apprise.Apprise()
        for u in urls_str.split(","):
            u = u.strip()
            if u:
                ap.add(u)

        success = True
        for idx, chunk in enumerate(chunks, 1):
            title = f"Swing Signals Update ({idx}/{len(chunks)})" if len(chunks) > 1 else "Swing Signals Update"
            res = ap.notify(title=title, body=chunk)
            if not res:
                logger.warning(f"Apprise notification failed for chunk {idx}")
                success = False

        return success
    except Exception as e:
        logger.warning(f"Apprise alerting error: {e}. Failure does not break pipeline.")
        return False
