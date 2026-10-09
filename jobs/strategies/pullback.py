import logging
from typing import Optional, List

import numpy as np
import pandas as pd

from indicators import check_rsi_pullback_recovery
from ranker import SignalRanker
from jobs.strategies.base import StrategyInterface
from src.utils.candidate_builder import build_candidate_from_row

logger = logging.getLogger(__name__)

# Pullback-and-recover RSI pattern: RSI dipped below the pullback threshold within the
# lookback window, is now back inside the recovery band, AND has risen a minimum number of
# points off that low (so a stock still sitting at its RSI low does not count as recovered).
RSI_PULLBACK_THRESHOLD = 50.0
RSI_RECOVERY_MIN = 45.0
RSI_RECOVERY_MAX = 67.0
MIN_RSI_RECOVERY_POINTS = 5.0
ADX_MIN = 12.0                 # Relaxed from 18
VOLUME_MULTIPLIER = 0.8        # Relaxed from 1.0
LOOKBACK_RSI_DAYS = 10
SWING_LOW_LOOKBACK = 20


def find_swing_low(df_slice: pd.DataFrame) -> float:
    """Find the most recent valid swing low in the last 20 trading days."""
    if len(df_slice) < SWING_LOW_LOOKBACK:
        return None
    lookback = df_slice.tail(SWING_LOW_LOOKBACK)
    if "LOW" not in lookback.columns or "CLOSE" not in lookback.columns:
        return None
    lows = lookback["LOW"].to_numpy(dtype=float)
    current_price = float(lookback["CLOSE"].iloc[-1])
    for i in range(len(lows) - 3, 1, -1):
        c = lows[i]
        if c >= current_price:
            continue
        if c < lows[i - 2] and c < lows[i - 1] and c < lows[i + 1] and c < lows[i + 2]:
            return float(c)
    return None


def get_earnings_date(
    ticker: str,
    earnings_calendar: Optional[dict] = None,
    supabase=None,
    allow_network: bool = False,
) -> str | None:
    """Fetch next earnings date from calendar map, local cache, or Supabase, returning ISO string or None."""
    ticker_upper = ticker.strip().upper()
    if earnings_calendar and ticker_upper in earnings_calendar:
        rec = earnings_calendar[ticker_upper]
        next_e_str = rec.get("next_earnings_date") or rec.get("next_earnings")
        if next_e_str:
            return str(next_e_str)[:10]

    from src.utils.earnings_cache import get_ticker_earnings
    _, next_e_str = get_ticker_earnings(ticker_upper, supabase=supabase, allow_network=allow_network)
    return next_e_str


def compute_targets(df: pd.DataFrame, entry: float) -> dict:
    """Find resistance levels (significant highs) from past 6 months."""
    highs = df["HIGH"].rolling(5).max().iloc[-120:]
    significant_highs = []

    for i in range(2, len(highs) - 2):
        if (
            highs.iloc[i] > highs.iloc[i - 1]
            and highs.iloc[i] > highs.iloc[i - 2]
            and highs.iloc[i] > highs.iloc[i + 1]
            and highs.iloc[i] > highs.iloc[i + 2]
        ):
            significant_highs.append(highs.iloc[i])

    sorted_highs = sorted(set([round(h, 2) for h in significant_highs]))
    deduped_highs = []
    for h in sorted_highs:
        if not deduped_highs or (h - deduped_highs[-1]) / deduped_highs[-1] > 0.02:
            deduped_highs.append(h)
    significant_highs = deduped_highs

    resistance_levels = [h for h in significant_highs if h > entry * 1.01]

    target_1 = entry * 1.07
    target_2 = entry * 1.12
    target_3 = entry * 1.18

    return {
        "target_1": round(target_1, 2),
        "target_2": round(target_2, 2),
        "target_3": round(target_3, 2),
        "target_1_pct": 7.0,
        "target_2_pct": 12.0,
        "target_3_pct": 18.0,
    }



def compute_weighted_rr(entry: float, stop: float, targets: dict) -> float:
    """Compute weighted R/R using 50/30/20 scale-out model."""
    risk = entry - stop
    if risk <= 0:
        return 0.0

    t1_rr = (targets["target_1"] - entry) / risk
    t2_rr = (targets["target_2"] - entry) / risk
    t3_rr = (targets["target_3"] - entry) / risk

    weighted = 0.5 * t1_rr + 0.3 * t2_rr + 0.2 * t3_rr
    return round(weighted, 2)


def generate_narrative(price, ema20, volume_ratio, current_rsi):
    parts = []

    if ema20 and ema20 > 0:
        pct_vs_ema = (price / ema20 - 1) * 100
        if pct_vs_ema > 5:
            parts.append("Strong uptrend")
        elif pct_vs_ema > 2:
            parts.append("Rising trend")
        elif pct_vs_ema > -1:
            parts.append("At support")
        else:
            parts.append("Pullback to support")
    else:
        parts.append("Trend unclear")

    if volume_ratio > 1.3:
        parts.append("strong volume")
    elif volume_ratio > 1.05:
        parts.append("volume confirming")
    elif volume_ratio > 0.9:
        parts.append("normal volume")
    else:
        parts.append("light volume")

    if current_rsi < 30:
        parts.append("deeply oversold")
    elif current_rsi < 40:
        parts.append("oversold bounce")
    elif current_rsi < 48:
        parts.append("recovering")
    elif current_rsi < 55:
        parts.append("neutral RSI")
    elif current_rsi < 62:
        parts.append("momentum building")
    elif current_rsi < 70:
        parts.append("strong momentum")
    else:
        parts.append("overbought")

    return ", ".join(parts) + "."


class PullbackRecoveryStrategy(StrategyInterface):
    def __init__(self):
        self.gate_rejections = {
            "failed_rsi_gate": 0,
            "failed_adx_gate": 0,
            "failed_trend_gate": 0,
            "failed_volume_gate": 0,
            "failed_maxrisk_gate": 0,
            "failed_minrisk_gate": 0,
            "failed_maxgap_gate": 0,
            "failed_earnings_gate": 0,
            "failed_trades_gate": 0,
            "momentum_exceptions": 0,
        }
        self.rsi_passed_count = 0
        self.signals_strong_buy = 0
        self.signals_buy = 0
        self.signals_watch = 0
        self.signals_speculative = 0
        self.signals_blocked = 0
        self._last_failed_gate: str | None = None
        self.earnings_calendar = None
        self.supabase = None

    def set_earnings_calendar(self, earnings_calendar: Optional[dict], supabase=None):
        self.earnings_calendar = earnings_calendar
        self.supabase = supabase

    @property
    def name(self) -> str:
        return "Pullback Recovery"

    @property
    def description(self) -> str:
        return "Buy pullbacks to support in established uptrends"

    def minimum_confidence(self) -> str:
        return "Buy"

    def reset_scan_stats(self):
        for key in self.gate_rejections:
            self.gate_rejections[key] = 0
        self.rsi_passed_count = 0

    @property
    def last_failed_gate(self) -> str | None:
        return self._last_failed_gate

    def _record_failure(self, gate: str):
        self._last_failed_gate = gate
        if gate in self.gate_rejections:
            self.gate_rejections[gate] += 1

    def scan(self, ticker: str, df: pd.DataFrame, regime: str, metrics: dict) -> Optional[dict]:
        from src.quant_config import normalize_regime_key
        regime_key = normalize_regime_key(regime)

        company_name = metrics.get("company_name", ticker)
        industry = metrics.get("industry", "Unknown")
        total_trades = metrics.get("total_trades", 0)
        win_rate = metrics.get("win_rate", 0.0)
        expectancy_pct = metrics.get("expectancy_pct", 0.0)

        sig, failed_gate = self._check_latest_signal(
            ticker,
            df,
            company_name,
            industry,
            total_trades,
            regime_str=regime_key,
        )

        if sig is None:
            self._record_failure(failed_gate or "failed_trend_gate")
            if failed_gate not in ("failed_trend_gate", "failed_rsi_gate"):
                self.rsi_passed_count += 1
            return None

        self.rsi_passed_count += 1
        if sig.get("is_momentum_exception"):
            self.gate_rejections["momentum_exceptions"] += 1

        risk = sig["entry_price"] - sig["stop_loss"]
        risk_pct = (risk / sig["entry_price"]) * 100 if sig["entry_price"] > 0 else 0.0

        sig.update(
            {
                "past_win_rate": win_rate,
                "total_trades": total_trades,
                "wins": metrics.get("wins", 0),
                "losses": metrics.get("losses", 0),
                "expectancy_pct": expectancy_pct,
                "risk_dollar": round(risk, 2),
                "risk_pct": round(risk_pct, 2),
                "composite_score": 0.0,
                "tier_label": "",
                "quality_score": 0.0,
                "is_blocked": False,
                "blocked_reason": None,
                "strategy": self.name,
            }
        )
        return sig

    def _check_latest_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        company_name: str,
        industry: str,
        total_trades: int,
        regime_str: str = "neutral",
    ) -> tuple[dict | None, str | None]:
        effective_rsi_threshold = RSI_PULLBACK_THRESHOLD
        effective_adx_min = 15.0 if regime_str.lower() == "bull" else 18.0

        n_bars = len(df)
        if n_bars < 201:
            return None, "failed_trend_gate"

        t = n_bars - 1

        closes = df["CLOSE"].to_numpy(dtype=float)
        dma50s = df["DMA_50"].to_numpy(dtype=float)
        dma200s = df["DMA_200"].to_numpy(dtype=float)
        rsis = df["RSI_14"].to_numpy(dtype=float)
        volumes = df["VOLUME"].to_numpy(dtype=float)
        vol_mas = df["VOLUME_MA_20"].to_numpy(dtype=float)
        highs = df["HIGH"].to_numpy(dtype=float)
        adxs = df["ADX_14"].to_numpy(dtype=float)
        macd_lines = df["MACD_LINE"].to_numpy(dtype=float)
        macd_sigs = df["MACD_SIGNAL"].to_numpy(dtype=float)
        macd_hists = df["MACD_HIST"].to_numpy(dtype=float)
        ema20s = df["EMA_20"].to_numpy(dtype=float)
        dates = df.index

        c = closes[t]
        d50 = dma50s[t]
        d200 = dma200s[t]
        rsi_now = rsis[t]
        vol = volumes[t]
        vma = vol_mas[t]
        adx_now = adxs[t]
        macd_line = macd_lines[t]
        macd_sig = macd_sigs[t]
        macd_hist = macd_hists[t]
        ema20 = ema20s[t]

        if any(
            np.isnan(x)
            for x in (c, d50, d200, rsi_now, vol, vma, adx_now, macd_line, macd_sig, macd_hist, ema20)
        ):
            return None, "failed_trend_gate"

        if regime_str == "bull":
            if not (c > d50):
                return None, "failed_trend_gate"
        else:
            if not (c > d50 > d200):
                return None, "failed_trend_gate"

        price_vs_50dma_pct = (c / d50 - 1) * 100 if d50 > 0 else 0.0
        volume_ratio = round(vol / vma, 2) if vma > 0 else 0.0

        momentum_exception = {
            "min_price_vs_50dma_pct": 20.0,
            "min_volume_ratio": 1.5,
            "min_adx": 20.0,
        }

        is_momentum_exception = (
            price_vs_50dma_pct >= momentum_exception["min_price_vs_50dma_pct"]
            and volume_ratio >= momentum_exception["min_volume_ratio"]
            and adx_now >= momentum_exception["min_adx"]
        )

        rsi_res = check_rsi_pullback_recovery(
            df["RSI_14"],
            lookback=LOOKBACK_RSI_DAYS,
            dip_threshold=effective_rsi_threshold,
            recovery_min=RSI_RECOVERY_MIN,
            recovery_max=RSI_RECOVERY_MAX,
        )
        rsi_min_10d = rsi_res.get("rsi_min_10d") if rsi_res.get("rsi_min_10d") is not None else rsis[t]

        if not is_momentum_exception:
            if not rsi_res.get("passed"):
                return None, "failed_rsi_gate"
            if rsi_now < rsi_min_10d + MIN_RSI_RECOVERY_POINTS:
                return None, "failed_rsi_gate"
        else:
            if rsi_now > 75:
                return None, "failed_rsi_gate"

        if np.isnan(adx_now) or not (adx_now >= effective_adx_min):
            return None, "failed_adx_gate"

        volume_ratio = round(vol / vma, 2) if vma > 0 else 0.0
        if not (volume_ratio >= VOLUME_MULTIPLIER):
            return None, "failed_volume_gate"

        stop_loss = find_swing_low(df)
        if stop_loss is None:
            return None, "failed_maxrisk_gate"

        entry_price = round(c, 2)
        if stop_loss >= entry_price:
            return None, "failed_maxrisk_gate"

        risk = entry_price - stop_loss
        if risk <= 0:
            return None, "failed_maxrisk_gate"

        if (entry_price - stop_loss) / entry_price > 0.15:
            return None, "failed_maxrisk_gate"

        risk_pct = (entry_price - stop_loss) / entry_price * 100
        min_risk_pct = 2.5
        if risk_pct < min_risk_pct:
            return None, "failed_minrisk_gate"

        max_gap_pct = 5.0
        daily_returns = df["CLOSE"].pct_change().iloc[-5:]
        max_drop = daily_returns.min() * 100
        if max_drop < -max_gap_pct:
            return None, "failed_maxgap_gate"

        targets = compute_targets(df, entry_price)
        weighted_rr = compute_weighted_rr(entry_price, stop_loss, targets)

        exit_price = targets["target_3"]
        upside_pct = targets["target_3_pct"]
        risk_reward = weighted_rr

        earnings_buffer_days = 7
        earnings_date = get_earnings_date(
            ticker,
            earnings_calendar=self.earnings_calendar,
            supabase=self.supabase,
            allow_network=False,
        )
        if earnings_date:
            ts_earnings = pd.Timestamp(earnings_date).normalize()
            # Measured from the signal bar's date (point-in-time), not the wall clock
            ts_now = pd.Timestamp(dates[t]).tz_localize(None).normalize() if getattr(pd.Timestamp(dates[t]), "tzinfo", None) else pd.Timestamp(dates[t]).normalize()
            days_to_earnings = (ts_earnings - ts_now).days
            if 0 < days_to_earnings <= earnings_buffer_days:
                return None, "failed_earnings_gate"

        high_20d = float(df["HIGH"].rolling(20).max().iloc[-1])
        distance_from_high_pct = (high_20d - c) / high_20d * 100

        latest_date = dates[t]
        if hasattr(latest_date, "date"):
            signal_date = latest_date.date().isoformat()
        else:
            signal_date = str(latest_date)[:10]

        narrative = generate_narrative(c, ema20, volume_ratio, rsi_now)

        return {
            "scan_date": signal_date,
            "ticker": ticker,
            "company_name": company_name,
            "industry": industry,
            "price": round(c, 2),
            "dma_50": round(d50, 2),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "exit_price": exit_price,
            "upside_pct": upside_pct,
            "risk_reward": risk_reward,
            "current_rsi": round(rsi_now, 2),
            "rsi_min_10d": round(float(rsi_min_10d), 2),
            "volume_ratio": volume_ratio,
            "adx_value": round(float(adx_now), 2),
            "macd_histogram": round(float(macd_hist), 4),
            "ema20": round(float(ema20), 2),
            "earnings_date": earnings_date,
            "is_momentum_exception": is_momentum_exception,
            "distance_from_high_pct": round(float(distance_from_high_pct), 2),
            "target_1": targets["target_1"],
            "target_2": targets["target_2"],
            "target_3": targets["target_3"],
            "target_1_pct": targets["target_1_pct"],
            "target_2_pct": targets["target_2_pct"],
            "target_3_pct": targets["target_3_pct"],
            "weighted_rr": weighted_rr,
            "position_sizing": "50/30/20",
            "narrative": narrative,
            "is_fallback": False,
        }, None

    def rank_candidates(self, candidates: List[dict], regime: str) -> List[dict]:
        # P0-1: Central SignalRanker is single source of truth.
        # Preserve non-blocked candidates without arbitrary strategy truncation.
        return [c for c in candidates if not c.get("is_blocked")]

