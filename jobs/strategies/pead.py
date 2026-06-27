import logging
from datetime import datetime
from typing import Optional, List
import pandas as pd
import yfinance as yf
from jobs.strategies.base import StrategyInterface

logger = logging.getLogger(__name__)


def get_last_earnings_date(ticker: str) -> Optional[datetime.date]:
    """Fetch last earnings date from yfinance. Returns datetime.date or None."""
    try:
        t = yf.Ticker(ticker)
        dates = t.earnings_dates
        if dates is None or len(dates) == 0:
            return None
        # Get most recent past earnings
        current_date = datetime.now().date()
        past_dates = [d for d in dates.index if d.date() <= current_date]
        if not past_dates:
            return None
        return max(past_dates).date()
    except Exception as e:
        logger.debug("Failed to get earnings date for %s: %s", ticker, e)
        return None


class PEADStrategy(StrategyInterface):
    @property
    def name(self) -> str:
        return "Post-Earnings Drift"

    @property
    def description(self) -> str:
        return "Buy earnings winners on first pullback after gap"

    def minimum_confidence(self) -> str:
        return "Buy"

    def scan(self, ticker: str, df: pd.DataFrame, regime: str, metrics: dict) -> Optional[dict]:
        if len(df) < 50:
            return None

        price = df['CLOSE'].iloc[-1]

        # === PRE-SCREEN FOR PERFORMANCE: NO CALL TO YFINANCE IF NO GAP ===
        # Stock must have a close price increase of >= 5% on some day in the last 5 days.
        has_recent_gap = False
        for days_ago in range(1, 6):
            if len(df) <= days_ago + 1:
                continue
            day_close = df['CLOSE'].iloc[-days_ago]
            prev_day_close = df['CLOSE'].iloc[-days_ago - 1]
            if prev_day_close > 0 and (day_close / prev_day_close - 1) >= 0.05:
                has_recent_gap = True
                break

        if not has_recent_gap:
            return None

        # === EARNINGS GATE ===
        earnings_date = get_last_earnings_date(ticker)
        if earnings_date is None:
            return None

        # Earnings must be within 1-5 days (recent enough to matter, not too old)
        days_since_earnings = (datetime.now().date() - earnings_date).days
        if days_since_earnings < 1 or days_since_earnings > 5:
            return None

        # === GAP GATE ===
        # Stock must have gapped up >= 5% on earnings (strong reaction)
        if len(df) <= days_since_earnings + 1:
            return None
        
        earnings_close = df['CLOSE'].iloc[-days_since_earnings]
        earnings_prev_close = df['CLOSE'].iloc[-days_since_earnings - 1]
        gap_pct = (earnings_close / earnings_prev_close - 1) * 100 if earnings_prev_close > 0 else 0
        if gap_pct < 5:
            return None

        # Stock must still hold >= 50% of gap (not a fake-out)
        gap_high = df['HIGH'].iloc[-days_since_earnings:].max()
        gap_low = df['LOW'].iloc[-days_since_earnings:].min()
        gap_range = gap_high - gap_low
        if gap_range > 0:
            hold_pct = (price - gap_low) / gap_range
            if hold_pct < 0.5:
                return None
        else:
            hold_pct = 1.0

        # === PULLBACK GATE ===
        # Price must be within 3% of the gap high (first pullback, not extended)
        pct_vs_gap_high = (price / gap_high - 1) * 100
        if pct_vs_gap_high < -3:  # Too far from gap high
            return None

        # === TREND GATE ===
        # Stock above 50 DMA (earnings winner in existing uptrend)
        sma50 = df['CLOSE'].rolling(50).mean().iloc[-1]
        if price <= sma50:
            return None

        # === VOLUME GATE ===
        # Volume on earnings day >= 2x average (institutional interest)
        volume_avg = df['VOLUME'].rolling(20).mean().iloc[-1]
        earnings_volume = df['VOLUME'].iloc[-days_since_earnings]
        if earnings_volume < volume_avg * 2:
            return None

        # === ADX GATE ===
        # ADX >= 15 (trend intact after earnings)
        adx_value = df['ADX_14'].iloc[-1]
        if adx_value < 15:
            return None

        # === SIGNAL CONSTRUCTION ===
        entry_price = price
        stop_loss = max(sma50 * 0.98, gap_low * 1.02)  # Below 50 DMA or gap low
        risk = entry_price - stop_loss
        risk_pct = (risk / entry_price) * 100 if entry_price > 0 else 0

        if risk_pct < 2.5:
            return None

        # Targets: earnings drift continues 2-4 weeks
        target_1 = entry_price * 1.08   # 8%
        target_2 = entry_price * 1.15   # 15%
        target_3 = entry_price * 1.25   # 25%
        target_1_pct = 8.0
        target_2_pct = 15.0
        target_3_pct = 25.0

        reward = (target_1 - entry_price) * 0.5 + (target_2 - entry_price) * 0.3 + (target_3 - entry_price) * 0.2
        weighted_rr = reward / risk if risk > 0 else 0
        position_sizing = "50/30/20"

        # === NARRATIVE ===
        def generate_pead_narrative(days_since_earnings, gap_pct, hold_pct, earnings_volume, volume_avg):
            parts = []
            parts.append(f"Earnings gap +{gap_pct:.1f}% {days_since_earnings}d ago")

            if hold_pct > 0.8:
                parts.append("holding strong")
            elif hold_pct > 0.6:
                parts.append("holding most gains")
            else:
                parts.append("pulling back to support")

            vol_ratio = earnings_volume / volume_avg if volume_avg > 0 else 0
            if vol_ratio > 3:
                parts.append("massive volume")
            elif vol_ratio > 2:
                parts.append("strong volume")

            return ", ".join(parts) + "."

        narrative = generate_pead_narrative(days_since_earnings, gap_pct, hold_pct, earnings_volume, volume_avg)

        # === COMPOSITE SCORING ===
        past_win_rate = metrics.get('win_rate', 55.0) if metrics else 55.0
        total_trades = metrics.get('total_trades', 0) if metrics else 0
        expectancy_pct = metrics.get('expectancy_pct', 2.0) if metrics else 2.0
        wins = metrics.get('wins', 0) if metrics else 0
        losses = metrics.get('losses', 0) if metrics else 0

        # PEAD-specific momentum: gap size + hold quality
        momentum_score = 0
        if gap_pct > 10: momentum_score = 30
        elif gap_pct > 7: momentum_score = 27
        elif gap_pct > 5: momentum_score = 24
        else: momentum_score = 20

        if hold_pct > 0.8: momentum_score += 0
        elif hold_pct > 0.6: momentum_score -= 2
        else: momentum_score -= 5

        # Expectancy
        exp_score = 0
        if expectancy_pct >= 10: exp_score = 40
        elif expectancy_pct >= 5: exp_score = 35
        elif expectancy_pct >= 2: exp_score = 25
        elif expectancy_pct >= 0: exp_score = 15
        else: exp_score = 5

        # Win rate
        wr_score = 0
        if past_win_rate >= 70: wr_score = 20
        elif past_win_rate >= 60: wr_score = 17
        elif past_win_rate >= 50: wr_score = 14
        elif past_win_rate >= 40: wr_score = 10
        else: wr_score = 5

        # Regime: PEAD works in all regimes but best in bull
        regime_score = 10 if regime == 'bull' else 8 if regime == 'sideways' else 6

        composite_score = momentum_score + exp_score + wr_score + regime_score

        if composite_score >= 70:
            tier_label = 'Strong Buy'
        elif composite_score >= 50:
            tier_label = 'Buy'
        elif composite_score >= 35:
            tier_label = 'Watch'
        else:
            tier_label = 'Speculative'

        # === GUARDRAILS ===
        MIN_WIN_RATE = 50.0
        MIN_EXPECTANCY = 1.0
        MIN_SAMPLE = 5

        is_blocked = False
        blocked_reason = None

        if past_win_rate < MIN_WIN_RATE:
            is_blocked = True
            blocked_reason = f'Win rate {past_win_rate:.1f}% below {MIN_WIN_RATE}%'
        if expectancy_pct < MIN_EXPECTANCY:
            is_blocked = True
            blocked_reason = f'Expectancy {expectancy_pct:.2f}% below {MIN_EXPECTANCY}%'
        if total_trades < MIN_SAMPLE:
            is_blocked = True
            blocked_reason = f'Sample size {total_trades} below {MIN_SAMPLE} trades'

        if is_blocked:
            tier_label = 'Blocked'

        # Get latest scan date from DataFrame index
        latest_date = df.index[-1]
        if hasattr(latest_date, "date"):
            signal_date = latest_date.date().isoformat()
        else:
            signal_date = str(latest_date)[:10]

        # Build signal dict
        signal = {
            'scan_date': signal_date,
            'ticker': ticker,
            'company_name': metrics.get('company_name', ticker) if metrics else ticker,
            'industry': metrics.get('industry', '') if metrics else '',
            'price': round(price, 2),
            'entry_price': round(entry_price, 2),
            'stop_loss': round(stop_loss, 2),
            'exit_price': round(target_3, 2),
            'target_1': round(target_1, 2),
            'target_2': round(target_2, 2),
            'target_3': round(target_3, 2),
            'target_1_pct': round(target_1_pct, 1),
            'target_2_pct': round(target_2_pct, 1),
            'target_3_pct': round(target_3_pct, 1),
            'upside_pct': round(target_3_pct, 1),
            'weighted_rr': round(weighted_rr, 2),
            'risk_reward': round(weighted_rr, 2),
            'position_sizing': position_sizing,
            'risk_dollar': round(risk, 2),
            'risk_pct': round(risk_pct, 2),
            'composite_score': round(composite_score, 1),
            'tier_label': tier_label,
            'quality_score': round(composite_score * 0.8, 1) if is_blocked else round(composite_score, 1),
            'narrative': narrative,
            'past_win_rate': past_win_rate,
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'expectancy_pct': expectancy_pct,
            'current_rsi': round(df['RSI_14'].iloc[-1], 1),
            'adx_value': round(adx_value, 1),
            'volume_ratio': round(earnings_volume / volume_avg, 2),
            'macd_histogram': round(df['MACD_HIST'].iloc[-1], 4),
            'ema20': round(sma50, 2),
            'is_blocked': is_blocked,
            'blocked_reason': blocked_reason,
            'strategy': 'Post-Earnings Drift',
            'days_since_earnings': days_since_earnings,
        }

        # Filter minimum confidence
        valid_tiers = []
        if self.minimum_confidence() == "Strong Buy":
            valid_tiers = ["Strong Buy"]
        elif self.minimum_confidence() == "Buy":
            valid_tiers = ["Strong Buy", "Buy"]
        elif self.minimum_confidence() == "Watch":
            valid_tiers = ["Strong Buy", "Buy", "Watch"]
        else:
            valid_tiers = ["Strong Buy", "Buy", "Watch", "Speculative"]

        if tier_label not in valid_tiers:
            return None

        return signal

    def rank_candidates(self, candidates: List[dict], regime: str) -> List[dict]:
        # Filter out blocked candidates
        valid = [c for c in candidates if not c.get("is_blocked")]
        # Rank by gap quality + recency
        valid.sort(key=lambda x: (x['composite_score'], -x.get('days_since_earnings', 5)), reverse=True)
        # Return top 3 PEAD candidates
        return valid[:3]
