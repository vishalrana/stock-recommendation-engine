import logging
from typing import Optional, List

import numpy as np
import pandas as pd

from indicators import check_rsi_pullback_recovery
from ranker import SignalRanker
from jobs.strategies.base import StrategyInterface

logger = logging.getLogger(__name__)

RSI_PULLBACK_THRESHOLD = 55.0  # Relaxed from 50
RSI_RECOVERY_MIN = 35.0        # Relaxed from 45
RSI_RECOVERY_MAX = 70.0        # Relaxed from 67
ADX_MIN = 12.0                 # Relaxed from 18
VOLUME_MULTIPLIER = 0.8        # Relaxed from 1.0
LOOKBACK_RSI_DAYS = 10
SWING_LOW_LOOKBACK = 20

MIN_WIN_RATE = 35.0            # Relaxed from 50.0
MIN_EXPECTANCY = 0.0           # Relaxed from 1.0
MIN_SAMPLE_SIZE = 5            # Relaxed from 10


def apply_guardrails(signal: dict) -> dict:
    """Override tier label if historical performance is below minimums."""
    win_rate = signal.get("past_win_rate", 0)
    expectancy = signal.get("expectancy_pct", 0)
    sample = signal.get("total_trades", 0)

    reasons = []

    if win_rate < MIN_WIN_RATE:
        reasons.append(f"Win rate {win_rate:.1f}% below {MIN_WIN_RATE}%")
    if expectancy < MIN_EXPECTANCY:
        reasons.append(f"Expectancy {expectancy:.2f}% below {MIN_EXPECTANCY}%")
    if sample < MIN_SAMPLE_SIZE:
        reasons.append(f"Sample size {sample} below {MIN_SAMPLE_SIZE} trades")

    if reasons:
        signal["tier_label"] = "Blocked"
        signal["blocked_reason"] = "; ".join(reasons)
        signal["is_blocked"] = True
        signal["quality_score"] = signal.get("composite_score", 0) * 0.3
    else:
        signal["is_blocked"] = False
        signal["blocked_reason"] = None

    return signal


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


def get_earnings_date(ticker: str) -> str | None:
    """Fetch next earnings date from yfinance for a ticker, returning ISO string or None."""
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        calendar = stock.calendar
        if calendar is None:
            return None

        if isinstance(calendar, dict):
            dates = calendar.get("Earnings Date")
            if dates and isinstance(dates, list) and len(dates) > 0:
                return pd.Timestamp(dates[0]).strftime("%Y-%m-%d")
            return None

        if hasattr(calendar, "empty") and not calendar.empty:
            if hasattr(calendar, "index") and len(calendar.index) > 0:
                return pd.Timestamp(calendar.index[0]).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


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

    target_1 = resistance_levels[0] if resistance_levels else entry * 1.05
    target_2 = resistance_levels[1] if len(resistance_levels) > 1 else target_1 * 1.05
    target_3 = resistance_levels[2] if len(resistance_levels) > 2 else target_2 * 1.05

    max_target = entry * 1.20
    target_1 = min(target_1, max_target)
    target_2 = min(target_2, max_target)
    target_3 = min(target_3, max_target)

    target_2 = max(target_2, target_1 * 1.02)
    target_3 = max(target_3, target_2 * 1.02)

    return {
        "target_1": round(target_1, 2),
        "target_2": round(target_2, 2),
        "target_3": round(target_3, 2),
        "target_1_pct": round((target_1 / entry - 1) * 100, 2),
        "target_2_pct": round((target_2 / entry - 1) * 100, 2),
        "target_3_pct": round((target_3 / entry - 1) * 100, 2),
    }


def compute_weighted_rr(entry: float, stop: float, targets: dict) -> float:
    """Compute weighted R/R using 50/30/20 position sizing."""
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
            regime_str=regime,
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

        entry_price = round(highs[t] * 1.001, 2)
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

        if total_trades < 10:
            return None, "failed_trades_gate"

        earnings_buffer_days = 7
        earnings_date = get_earnings_date(ticker)
        if earnings_date:
            ts_earnings = pd.Timestamp(earnings_date).normalize()
            ts_now = pd.Timestamp.now().normalize()
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
        if not candidates:
            return []

        signals_df = pd.DataFrame(candidates)
        if "win_rate" not in signals_df.columns:
            signals_df["win_rate"] = signals_df.apply(
                lambda row: row.get("past_win_rate", 0.0), axis=1
            )
        if "expectancy_pct" not in signals_df.columns:
            signals_df["expectancy_pct"] = signals_df.apply(
                lambda row: row.get("expectancy_pct", 0.0), axis=1
            )
        if "total_trades" not in signals_df.columns:
            signals_df["total_trades"] = signals_df.apply(
                lambda row: row.get("total_trades", 0), axis=1
            )
        if "wins" not in signals_df.columns:
            signals_df["wins"] = signals_df.apply(
                lambda row: row.get("wins", 0), axis=1
            )
        if "losses" not in signals_df.columns:
            signals_df["losses"] = signals_df.apply(
                lambda row: row.get("losses", 0), axis=1
            )

        logger.info("Applying composite ranking (Strategy 1.3 Rev B)...")
        ranker = SignalRanker()
        scored_df = ranker.composite_rank(signals_df, regime, top_n=len(signals_df))

        self.signals_strong_buy = ranker.signals_strong_buy
        self.signals_buy = ranker.signals_buy
        self.signals_watch = ranker.signals_watch
        self.signals_speculative = ranker.signals_speculative

        logger.info(
            "Candidate pool tier counts: Strong Buy=%d, Buy=%d, "
            "Watch=%d (debug only), Speculative=%d (debug only)",
            self.signals_strong_buy,
            self.signals_buy,
            self.signals_watch,
            self.signals_speculative,
        )

        if scored_df.empty:
            return []

        ranked: List[dict] = []
        for _, row in scored_df.iterrows():
            breakdown = row["score_breakdown"]
            a = 0.30 * breakdown["momentum"]
            b = 0.40 * breakdown["expectancy"]
            c = 0.20 * breakdown["winrate"]
            d = 0.10 * breakdown["regime"]

            logger.info(
                "%s | Composite: %.1f | Tier: %s | "
                "Momentum: %.1f/30, Expectancy: %.1f/40, WinRate: %.1f/20, Regime: %.1f/10 | "
                "Raw: exp=%.2f%%, win=%.1f%%, trades=%s",
                row["ticker"],
                row["composite_score"],
                row["tier_label"],
                a,
                b,
                c,
                d,
                row["expectancy_pct"],
                row["win_rate"],
                row["total_trades"],
            )

            composite_score = round(float(row["composite_score"]), 4)
            candidate = {
                    "scan_date": row["scan_date"],
                    "ticker": row["ticker"],
                    "company_name": row["company_name"],
                    "industry": row["industry"],
                    "price": row["price"],
                    "entry_price": row["entry_price"],
                    "stop_loss": row["stop_loss"],
                    "exit_price": row["exit_price"],
                    "upside_pct": row["upside_pct"],
                    "risk_reward": row["risk_reward"],
                    "current_rsi": row["current_rsi"],
                    "rsi_min_10d": row["rsi_min_10d"],
                    "volume_ratio": row["volume_ratio"],
                    "adx_value": row["adx_value"],
                    "macd_histogram": row["macd_histogram"],
                    "ema20": row["ema20"],
                    "score": composite_score,
                    "composite_score": composite_score,
                    "quality_score": composite_score,
                    "tier_label": row["tier_label"],
                    "past_win_rate": row.get("win_rate", row.get("past_win_rate", 0.0)),
                    "expectancy_pct": row["expectancy_pct"],
                    "total_trades": row["total_trades"],
                    "wins": row.get("wins", 0),
                    "losses": row.get("losses", 0),
                    "is_blocked": False,
                    "blocked_reason": None,
                    "strategy": self.name,
                    "is_fallback": bool(row.get("is_fallback", False)),
                    "target_1": row.get("target_1"),
                    "target_2": row.get("target_2"),
                    "target_3": row.get("target_3"),
                    "target_1_pct": row.get("target_1_pct"),
                    "target_2_pct": row.get("target_2_pct"),
                    "target_3_pct": row.get("target_3_pct"),
                    "weighted_rr": row.get("weighted_rr"),
                    "position_sizing": row.get("position_sizing", "50/30/20"),
                    "narrative": row.get("narrative"),
                    "context_score": float(row["context_score"]) if "context_score" in row and pd.notna(row["context_score"]) else 0.0,
                    "risk_dollar": round(float(row["entry_price"] - row["stop_loss"]), 2),
                    "risk_pct": round(
                        float((row["entry_price"] - row["stop_loss"]) / row["entry_price"] * 100),
                        2,
                    ),
                }
            candidate = apply_guardrails(candidate)
            if candidate.get("is_blocked"):
                self.signals_blocked += 1
                logger.info(
                    "[BLOCKED] %s | %s",
                    candidate["ticker"],
                    candidate.get("blocked_reason", "Guardrail failure"),
                )
                continue
            ranked.append(candidate)

        ranked.sort(key=lambda x: x["composite_score"], reverse=True)
        return ranked
