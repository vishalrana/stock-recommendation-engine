"""
Entry Location Engine
=====================
Objective market-structure and price-location analysis for the stock recommendation engine.
Evaluates support, resistance, range position, extension, breakout confirmation,
and support stabilization to classify setups as:
- BUY: technically defensible entry location.
- WAIT: high-quality technical setup, but current location is unconfirmed, approaching resistance, or overextended.
- REJECT: broken market structure or falling knife.

All calculations strictly use historical data up to time T with zero lookahead bias.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
import numpy as np
import pandas as pd


@dataclass
class MarketStructure:
    support_level: float
    support_zone_low: float
    support_zone_high: float
    resistance_level: float
    resistance_zone_low: float
    resistance_zone_high: float
    range_low: float
    range_high: float
    range_span: float
    range_position_pct: float
    distance_to_support_pct: float
    distance_to_resistance_pct: float
    atr_dist_support: float
    atr_dist_resistance: float
    extension_ema20_atr: float
    extension_dma50_pct: float
    is_near_support: bool
    is_near_resistance: bool
    is_confirmed_breakout: bool
    is_failed_breakout: bool
    is_falling_knife: bool
    is_stabilized_support: bool
    is_extended: bool
    atr: float
    price: float


@dataclass
class EntryLocationResult:
    state: str  # "BUY", "WAIT", "REJECT"
    reason: str
    structure: MarketStructure

    @property
    def support_level(self) -> float:
        return self.structure.support_level

    @property
    def resistance_level(self) -> float:
        return self.structure.resistance_level

    @property
    def range_position_pct(self) -> float:
        return self.structure.range_position_pct


def find_recent_swing_levels(
    df: pd.DataFrame,
    lookback: int = 60,
    window: int = 2,
) -> Tuple[list[float], list[float]]:
    """
    Find local swing highs and swing lows strictly using historical data prior to current bar.
    Zero lookahead: bar t (last row) cannot be a confirmed swing pivot.
    """
    n = len(df)
    if n < window * 2 + 2:
        return [], []

    highs = df["HIGH"].to_numpy(dtype=float)
    lows = df["LOW"].to_numpy(dtype=float)

    start_idx = max(window, n - lookback)
    end_idx = n - window - 1  # Exclude current bar and bars lacking right-hand confirmation

    swing_highs = []
    swing_lows = []

    for i in range(start_idx, end_idx):
        h = highs[i]
        l = lows[i]

        # Local maximum
        is_pivot_high = True
        for offset in range(1, window + 1):
            if highs[i - offset] > h or highs[i + offset] > h:
                is_pivot_high = False
                break
        if is_pivot_high:
            swing_highs.append(float(h))

        # Local minimum
        is_pivot_low = True
        for offset in range(1, window + 1):
            if lows[i - offset] < l or lows[i + offset] < l:
                is_pivot_low = False
                break
        if is_pivot_low:
            swing_lows.append(float(l))

    return swing_highs, swing_lows


def analyze_market_structure(
    df: pd.DataFrame,
    lookback: int = 60,
    zone_atr_factor: float = 0.50,
) -> MarketStructure:
    """
    Compute objective market structure from historical OHLCV data up to the current bar.
    """
    if df is None or len(df) < 20:
        # Fallback for insufficient data
        p = float(df["CLOSE"].iloc[-1]) if df is not None and len(df) > 0 else 100.0
        atr = float(df["ATR_14"].iloc[-1]) if df is not None and "ATR_14" in df.columns else 2.0
        return MarketStructure(
            support_level=p * 0.95,
            support_zone_low=p * 0.94,
            support_zone_high=p * 0.96,
            resistance_level=p * 1.05,
            resistance_zone_low=p * 1.04,
            resistance_zone_high=p * 1.06,
            range_low=p * 0.95,
            range_high=p * 1.05,
            range_span=p * 0.10,
            range_position_pct=50.0,
            distance_to_support_pct=5.0,
            distance_to_resistance_pct=5.0,
            atr_dist_support=2.5,
            atr_dist_resistance=2.5,
            extension_ema20_atr=0.0,
            extension_dma50_pct=0.0,
            is_near_support=False,
            is_near_resistance=False,
            is_confirmed_breakout=False,
            is_failed_breakout=False,
            is_falling_knife=False,
            is_stabilized_support=False,
            is_extended=False,
            atr=atr,
            price=p,
        )

    price = float(df["CLOSE"].iloc[-1])
    high_today = float(df["HIGH"].iloc[-1])
    low_today = float(df["LOW"].iloc[-1])
    open_today = float(df["OPEN"].iloc[-1]) if "OPEN" in df.columns else price
    volume_today = float(df["VOLUME"].iloc[-1]) if "VOLUME" in df.columns else 1.0

    # ATR-14
    atr = float(df["ATR_14"].iloc[-1]) if "ATR_14" in df.columns and not np.isnan(df["ATR_14"].iloc[-1]) else 0.0
    if atr <= 0.0:
        recent_tr = (df["HIGH"] - df["LOW"]).tail(14).mean()
        atr = float(recent_tr) if recent_tr > 0 else max(1.0, price * 0.02)

    # 20-day High & Low (excluding today's bar to define prior resistance/support)
    prior_high_20 = float(df["HIGH"].iloc[-21:-1].max()) if len(df) >= 21 else float(df["HIGH"].iloc[:-1].max())
    prior_low_20 = float(df["LOW"].iloc[-21:-1].min()) if len(df) >= 21 else float(df["LOW"].iloc[:-1].min())

    # Moving averages
    dma50 = float(df["DMA_50"].iloc[-1]) if "DMA_50" in df.columns and not np.isnan(df["DMA_50"].iloc[-1]) else None
    if dma50 is None and len(df) >= 50:
        dma50 = float(df["CLOSE"].rolling(50).mean().iloc[-1])
    dma50_val = dma50 if dma50 is not None else price

    ema20 = float(df["EMA_20"].iloc[-1]) if "EMA_20" in df.columns and not np.isnan(df["EMA_20"].iloc[-1]) else None
    if ema20 is None and len(df) >= 20:
        ema20 = float(df["CLOSE"].ewm(span=20, adjust=False).mean().iloc[-1])
    ema20_val = ema20 if ema20 is not None else price

    # 52-week high (excluding today)
    lookback_52w = min(252, len(df) - 1)
    high_52w = float(df["HIGH"].iloc[-lookback_52w - 1:-1].max()) if lookback_52w > 5 else prior_high_20

    # Find swing highs and lows
    swing_highs, swing_lows = find_recent_swing_levels(df, lookback=lookback)

    # --- Identify Resistance Level ---
    # Prior resistance levels above or at price
    overhead_highs = [h for h in swing_highs if h >= price - 0.25 * atr]
    if overhead_highs:
        # Closest overhead resistance
        resistance_level = min(overhead_highs)
    else:
        # If no swing high overhead, use prior 20-day high or 52W high
        resistance_level = max(prior_high_20, high_52w if price >= prior_high_20 else prior_high_20)

    # --- Identify Support Level ---
    # Established support candidates: prior swing lows, 20-day low, and 50 DMA if tested
    support_candidates = list(swing_lows)
    support_candidates.append(prior_low_20)
    if dma50_val is not None:
        recent_closes = df["CLOSE"].iloc[-10:-1] if len(df) >= 11 else df["CLOSE"].iloc[:-1]
        if len(recent_closes) > 0 and recent_closes.mean() >= dma50_val * 0.98:
            support_candidates.append(dma50_val)

    # If price has fallen below prior_low_20, prior_low_20 was the support level that was broken
    if price < prior_low_20:
        support_level = prior_low_20
    else:
        valid_supports = [s for s in support_candidates if s <= price + 0.25 * atr]
        support_level = max(valid_supports) if valid_supports else prior_low_20

    # Safety: support must not exceed resistance
    if support_level >= resistance_level:
        support_level = min(prior_low_20, resistance_level - 1.0 * atr)

    # Zones
    support_zone_low = round(support_level - zone_atr_factor * atr, 2)
    support_zone_high = round(support_level + zone_atr_factor * atr, 2)
    resistance_zone_low = round(resistance_level - zone_atr_factor * atr, 2)
    resistance_zone_high = round(resistance_level + zone_atr_factor * atr, 2)

    range_low = support_level
    range_high = resistance_level
    range_span = max(0.01, range_high - range_low)

    # Range Position (0% = at support, 100% = at resistance)
    raw_range_pos = ((price - range_low) / range_span) * 100.0
    range_position_pct = round(float(np.clip(raw_range_pos, 0.0, 100.0)), 2)

    # Distances
    distance_to_support_pct = round(((price - support_level) / price) * 100.0, 2) if price > 0 else 0.0
    distance_to_resistance_pct = round(((resistance_level - price) / price) * 100.0, 2) if price > 0 else 0.0
    atr_dist_support = round((price - support_level) / atr, 2) if atr > 0 else 0.0
    atr_dist_resistance = round((resistance_level - price) / atr, 2) if atr > 0 else 0.0

    # Extension
    extension_ema20_atr = round((price - ema20_val) / atr, 2) if atr > 0 else 0.0
    extension_dma50_pct = round(((price - dma50_val) / dma50_val) * 100.0, 2) if dma50_val > 0 else 0.0

    # Proximities
    is_near_support = price <= support_zone_high or atr_dist_support <= 1.0 or distance_to_support_pct <= 2.5
    is_near_resistance = price >= resistance_zone_low or atr_dist_resistance <= 1.0 or distance_to_resistance_pct <= 2.5

    # Candle metrics
    candle_span = max(0.01, high_today - low_today)
    close_in_candle = (price - low_today) / candle_span  # 1.0 = closed at high, 0.0 = closed at low
    volume_avg = float(df["VOLUME"].rolling(20).mean().iloc[-1]) if "VOLUME" in df.columns and len(df) >= 20 else volume_today
    volume_ratio = volume_today / volume_avg if volume_avg > 0 else 1.0

    # --- Breakout Confirmation ---
    # True confirmed breakout:
    # 1. Closed strictly above resistance
    # 2. Bullish close (closed in upper 50% of day's candle range)
    # 3. Not an extreme extension above the breakout point
    is_breakout_candle = price > resistance_level
    is_confirmed_breakout = (
        is_breakout_candle
        and price >= resistance_level + 0.05 * atr
        and close_in_candle >= 0.45
        and (price - resistance_level) <= 2.5 * atr
    )

    # Failed breakout: high exceeded resistance but close collapsed back below resistance
    is_failed_breakout = (high_today >= resistance_level) and (price < resistance_level)

    # --- Support Stabilization vs Falling Knife ---
    # Falling knife: breaking through support or closing at the dead low on elevated down volume
    is_down_day = price < open_today
    is_closing_at_low = close_in_candle < 0.25
    is_breaking_support = price < support_zone_low
    is_falling_knife = is_breaking_support or (is_down_day and is_closing_at_low and volume_ratio >= 1.1)

    # Stabilized support: price held in or above support zone, closed in upper 40% of bar or green, not falling knife
    is_stabilized_support = (
        (price >= support_zone_low)
        and (close_in_candle >= 0.35 or price >= open_today)
        and not is_falling_knife
    )

    # Extension flag (e.g. > 3.5 ATR above 20 EMA or > 25% above 50 DMA)
    is_extended = extension_ema20_atr > 3.5 or extension_dma50_pct > 25.0

    return MarketStructure(
        support_level=round(support_level, 2),
        support_zone_low=support_zone_low,
        support_zone_high=support_zone_high,
        resistance_level=round(resistance_level, 2),
        resistance_zone_low=resistance_zone_low,
        resistance_zone_high=resistance_zone_high,
        range_low=round(range_low, 2),
        range_high=round(range_high, 2),
        range_span=round(range_span, 2),
        range_position_pct=range_position_pct,
        distance_to_support_pct=distance_to_support_pct,
        distance_to_resistance_pct=distance_to_resistance_pct,
        atr_dist_support=atr_dist_support,
        atr_dist_resistance=atr_dist_resistance,
        extension_ema20_atr=extension_ema20_atr,
        extension_dma50_pct=extension_dma50_pct,
        is_near_support=is_near_support,
        is_near_resistance=is_near_resistance,
        is_confirmed_breakout=is_confirmed_breakout,
        is_failed_breakout=is_failed_breakout,
        is_falling_knife=is_falling_knife,
        is_stabilized_support=is_stabilized_support,
        is_extended=is_extended,
        atr=round(atr, 2),
        price=round(price, 2),
    )


def evaluate_entry_location(
    candidate: dict,
    df: Optional[pd.DataFrame],
    strategy_name: str,
) -> EntryLocationResult:
    """
    Evaluate candidate entry location using market structure and strategy-specific entry behavior.
    Classifies setup into:
      - BUY: Defensible entry location right now.
      - WAIT: Valid underlying setup, but location is currently unconfirmed, approaching resistance, or extended.
      - REJECT: Invalid location (falling knife, broken support).
    """
    if df is None or len(df) < 20:
        # If price history dataframe is unavailable, allow candidate without location gate
        structure = analyze_market_structure(df)
        return EntryLocationResult(
            state="BUY",
            reason="Market structure data neutral/insufficient; strategy qualification stands",
            structure=structure,
        )

    structure = analyze_market_structure(df)
    strat = strategy_name.lower().replace("-", "_").replace(" ", "_")

    # 1. Universal Over-extension Gate
    # A stock that is wildly extended (> 3.5 ATR above EMA20 or > 25% above DMA50) suffers asymmetric downside
    if structure.extension_ema20_atr > 3.5 or structure.extension_dma50_pct > 25.0:
        return EntryLocationResult(
            state="WAIT",
            reason=f"Extended momentum: price is {structure.extension_ema20_atr:.1f} ATR above 20 EMA ({structure.extension_dma50_pct:.1f}% above 50 DMA). Await consolidation/pullback.",
            structure=structure,
        )

    # 2. Universal Failed Breakout Gate
    # If today tried to break resistance but got rejected back inside the range, do not buy the wick
    if structure.is_failed_breakout:
        return EntryLocationResult(
            state="WAIT",
            reason=f"Failed breakout attempt: touched resistance at ${structure.resistance_level:.2f} but closed back below. Await confirmation.",
            structure=structure,
        )

    # 3. Strategy-Specific Evaluation

    # --- A. 52-Week High Breakout ---
    if "52" in strat or "breakout" in strat:
        if structure.is_confirmed_breakout:
            return EntryLocationResult(
                state="BUY",
                reason=f"Confirmed breakout above 52-week resistance at ${structure.resistance_level:.2f} with strong closing structure.",
                structure=structure,
            )
        elif structure.price < structure.resistance_level:
            # Within 5% of 52W high, but has NOT broken out
            return EntryLocationResult(
                state="WAIT",
                reason=f"Approaching 52-week high resistance at ${structure.resistance_level:.2f} ({structure.distance_to_resistance_pct:.1f}% away). Await confirmed breakout.",
                structure=structure,
            )
        elif structure.price > structure.resistance_level + 2.5 * structure.atr:
            return EntryLocationResult(
                state="WAIT",
                reason=f"Extended breakout: price has already run {structure.atr_dist_resistance:.1f} ATR past the breakout level. Await base.",
                structure=structure,
            )
        else:
            return EntryLocationResult(
                state="BUY",
                reason=f"Breakout holding above resistance at ${structure.resistance_level:.2f}.",
                structure=structure,
            )

    # --- B. Trend Following ---
    elif "trend" in strat:
        # Near resistance without confirmed breakout -> WAIT
        if structure.is_near_resistance and not structure.is_confirmed_breakout:
            return EntryLocationResult(
                state="WAIT",
                reason=f"Approaching 20-day resistance at ${structure.resistance_level:.2f} ({structure.distance_to_resistance_pct:.1f}% away). Await breakout confirmation.",
                structure=structure,
            )
        # Confirmed breakout -> BUY
        if structure.is_confirmed_breakout:
            return EntryLocationResult(
                state="BUY",
                reason=f"Confirmed trend breakout above resistance at ${structure.resistance_level:.2f}.",
                structure=structure,
            )
        # Bouncing from EMA20 / DMA50 support -> BUY
        if structure.is_near_support and structure.is_stabilized_support:
            return EntryLocationResult(
                state="BUY",
                reason=f"Trend continuation: stabilizing near support at ${structure.support_level:.2f}.",
                structure=structure,
            )
        # Middle of range with no extension: allowed if trend is healthy
        if not structure.is_extended:
            return EntryLocationResult(
                state="BUY",
                reason="Steady trend continuation with healthy price location.",
                structure=structure,
            )
        return EntryLocationResult(
            state="WAIT",
            reason="Trend continuation unconfirmed or extended. Await cleaner location.",
            structure=structure,
        )

    # --- C. Pullback Recovery ---
    elif "pullback" in strat:
        if structure.is_falling_knife:
            return EntryLocationResult(
                state="REJECT",
                reason=f"Support breakdown / falling knife at ${structure.support_level:.2f}. Await stabilization.",
                structure=structure,
            )
        if structure.is_near_support:
            if structure.is_stabilized_support:
                return EntryLocationResult(
                    state="BUY",
                    reason=f"Pullback stabilized near support zone (${structure.support_zone_low:.2f}-${structure.support_zone_high:.2f}).",
                    structure=structure,
                )
            else:
                return EntryLocationResult(
                    state="WAIT",
                    reason=f"Pullback approaching support at ${structure.support_level:.2f}, but stabilization not yet confirmed. Await bounce.",
                    structure=structure,
                )
        if structure.range_position_pct > 65.0:
            return EntryLocationResult(
                state="WAIT",
                reason=f"Poor pullback location: price is near range high ({structure.range_position_pct:.0f}% of range). Await dip toward support.",
                structure=structure,
            )
        # Modest pullback in range
        return EntryLocationResult(
            state="BUY",
            reason=f"Pullback holding above support at ${structure.support_level:.2f}.",
            structure=structure,
        )

    # --- D. Mean Reversion ---
    elif "mean" in strat or "reversion" in strat:
        if structure.is_falling_knife:
            return EntryLocationResult(
                state="REJECT",
                reason=f"Falling knife: severe breakdown below support at ${structure.support_level:.2f}.",
                structure=structure,
            )
        if structure.is_near_support and structure.is_stabilized_support:
            return EntryLocationResult(
                state="BUY",
                reason=f"Oversold bounce stabilizing at 20-day support (${structure.support_level:.2f}).",
                structure=structure,
            )
        if structure.range_position_pct > 40.0:
            return EntryLocationResult(
                state="WAIT",
                reason=f"Mean reversion setup already advanced ({structure.range_position_pct:.0f}% of range). Await deeper oversold test.",
                structure=structure,
            )
        return EntryLocationResult(
            state="WAIT",
            reason=f"Oversold test at support (${structure.support_level:.2f}) not yet stabilized. Await confirmation.",
            structure=structure,
        )

    # --- E. Post-Earnings Drift (PEAD) ---
    elif "pead" in strat or "drift" in strat or "earnings" in strat:
        if structure.is_extended:
            return EntryLocationResult(
                state="WAIT",
                reason=f"Post-earnings move is overextended ({structure.extension_ema20_atr:.1f} ATR above 20 EMA). Await consolidation base.",
                structure=structure,
            )
        if structure.is_near_resistance and not structure.is_confirmed_breakout:
            return EntryLocationResult(
                state="WAIT",
                reason=f"Post-earnings reaction approaching overhead resistance at ${structure.resistance_level:.2f}. Await breakout.",
                structure=structure,
            )
        return EntryLocationResult(
            state="BUY",
            reason=f"Post-earnings continuation with healthy consolidation above support (${structure.support_level:.2f}).",
            structure=structure,
        )

    # --- F. Cross-Sectional Momentum ---
    elif "cross" in strat or "momentum" in strat:
        if structure.extension_dma50_pct > 20.0 or structure.extension_ema20_atr > 3.0:
            return EntryLocationResult(
                state="WAIT",
                reason=f"Momentum leader overextended ({structure.extension_dma50_pct:.1f}% above 50 DMA, {structure.extension_ema20_atr:.1f} ATR above 20 EMA). Await base.",
                structure=structure,
            )
        if structure.is_near_resistance and not structure.is_confirmed_breakout:
            return EntryLocationResult(
                state="WAIT",
                reason=f"Approaching resistance at ${structure.resistance_level:.2f}. Await breakout confirmation.",
                structure=structure,
            )
        return EntryLocationResult(
            state="BUY",
            reason=f"Strong relative strength with defensible price structure above support (${structure.support_level:.2f}).",
            structure=structure,
        )

    # --- G. Sector Rotation ---
    elif "sector" in strat or "rotation" in strat:
        if structure.is_near_resistance and not structure.is_confirmed_breakout:
            return EntryLocationResult(
                state="WAIT",
                reason=f"Sector ETF approaching resistance at ${structure.resistance_level:.2f}. Await breakout confirmation.",
                structure=structure,
            )
        if structure.is_confirmed_breakout:
            return EntryLocationResult(
                state="BUY",
                reason=f"Confirmed sector breakout above resistance at ${structure.resistance_level:.2f}.",
                structure=structure,
            )
        if structure.is_near_support and structure.is_stabilized_support:
            return EntryLocationResult(
                state="BUY",
                reason=f"Sector trend continuation bouncing from support at ${structure.support_level:.2f}.",
                structure=structure,
            )
        return EntryLocationResult(
            state="BUY",
            reason="Sector momentum with healthy price location.",
            structure=structure,
        )

    # Default fallback
    return EntryLocationResult(
        state="BUY",
        reason="Entry location verified within acceptable parameters.",
        structure=structure,
    )
