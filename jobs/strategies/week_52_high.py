import logging
from typing import Optional, List
import pandas as pd
from jobs.strategies.base import StrategyInterface

logger = logging.getLogger(__name__)


class Week52HighStrategy(StrategyInterface):
    @property
    def name(self) -> str:
        return "52-Week High"

    @property
    def description(self) -> str:
        return "Buy strength near all-time highs"

    def minimum_confidence(self) -> str:
        return "Buy"

    def scan(self, ticker: str, df: pd.DataFrame, regime: str, metrics: dict) -> Optional[dict]:
        if len(df) < 252:  # Need 1 year of data
            return None

        # Normalized to uppercase columns for consistency
        price = df['CLOSE'].iloc[-1]
        high_52w = df['HIGH'].rolling(252).max().iloc[-1]
        high_20 = df['HIGH'].rolling(20).max().iloc[-1]
        sma50 = df['CLOSE'].rolling(50).mean().iloc[-1]
        volume_avg = df['VOLUME'].rolling(20).mean().iloc[-1]
        volume_today = df['VOLUME'].iloc[-1]

        # Extract indicators already pre-calculated by calculate_indicators
        current_rsi = df['RSI_14'].iloc[-1]
        adx_value = df['ADX_14'].iloc[-1]
        macd_histogram = df['MACD_HIST'].iloc[-1]

        # === GATES ===
        # 1. Within 2% of 52-week high (George & Hwang anchoring anomaly)
        pct_vs_52w = (price / high_52w - 1) * 100
        if pct_vs_52w < -2:
            return None

        # 2. Price > 50 DMA (trend alignment)
        if price <= sma50:
            return None

        # 3. RSI 55-75 (strong but not overbought)
        if current_rsi < 55 or current_rsi > 75:
            return None

        # 4. ADX >= 20 (strong trend)
        if adx_value < 20:
            return None

        # 5. Volume >= 1.2x average (breakout confirmation)
        volume_ratio = volume_today / volume_avg if volume_avg > 0 else 0
        if volume_ratio < 1.2:
            return None

        # 6. New high gate: within 3% of 20-day high (recent momentum)
        pct_vs_20h = (price / high_20 - 1) * 100
        if pct_vs_20h < -3:
            return None

        # === SIGNAL CONSTRUCTION ===
        entry_price = price
        stop_loss = max(sma50 * 0.97, high_52w * 0.95)  # Below 50 DMA or 5% off 52w high
        risk = entry_price - stop_loss
        risk_pct = (risk / entry_price) * 100 if entry_price > 0 else 0

        if risk_pct < 2.5:
            return None

        # Targets: strength begets strength
        target_1 = entry_price * 1.12   # 12%
        target_2 = entry_price * 1.20   # 20%
        target_3 = entry_price * 1.30   # 30%
        target_1_pct = 12.0
        target_2_pct = 20.0
        target_3_pct = 30.0

        reward = (target_1 - entry_price) * 0.5 + (target_2 - entry_price) * 0.3 + (target_3 - entry_price) * 0.2
        weighted_rr = reward / risk if risk > 0 else 0
        position_sizing = "50/30/20"

        # Narrative
        parts = []
        if pct_vs_52w > -0.5:
            parts.append("At 52-week high")
        else:
            parts.append("Near 52-week high")

        if volume_ratio > 2:
            parts.append("massive breakout volume")
        elif volume_ratio > 1.5:
            parts.append("strong breakout volume")
        else:
            parts.append("volume confirming")

        if adx_value > 25:
            parts.append("powerful trend")
        else:
            parts.append("trend intact")

        narrative = ", ".join(parts) + "."

        # Scoring
        past_win_rate = metrics.get('win_rate', 55.0) if metrics else 55.0
        total_trades = metrics.get('total_trades', 0) if metrics else 0
        expectancy_pct = metrics.get('expectancy_pct', 2.0) if metrics else 2.0
        wins = metrics.get('wins', 0) if metrics else 0
        losses = metrics.get('losses', 0) if metrics else 0

        momentum_score = 0
        if pct_vs_52w > 0: momentum_score = 30
        elif pct_vs_52w > -1: momentum_score = 27
        else: momentum_score = 24

        exp_score = 0
        if expectancy_pct >= 10: exp_score = 40
        elif expectancy_pct >= 5: exp_score = 35
        elif expectancy_pct >= 2: exp_score = 25
        elif expectancy_pct >= 0: exp_score = 15
        else: exp_score = 5

        wr_score = 0
        if past_win_rate >= 70: wr_score = 20
        elif past_win_rate >= 60: wr_score = 17
        elif past_win_rate >= 50: wr_score = 14
        else: wr_score = 5

        regime_score = 10 if regime == 'bull' else 6 if regime == 'sideways' else 3

        composite_score = momentum_score + exp_score + wr_score + regime_score

        if composite_score >= 70: tier_label = 'Strong Buy'
        elif composite_score >= 50: tier_label = 'Buy'
        elif composite_score >= 35: tier_label = 'Watch'
        else: tier_label = 'Speculative'

        # Guardrails
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

        return {
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
            'current_rsi': round(current_rsi, 1),
            'adx_value': round(adx_value, 1),
            'volume_ratio': round(volume_ratio, 2),
            'macd_histogram': round(macd_histogram, 4),
            'ema20': round(sma50, 2),
            'is_blocked': is_blocked,
            'blocked_reason': blocked_reason,
            'strategy': '52-Week High',
        }

    def rank_candidates(self, candidates: List[dict], regime: str) -> List[dict]:
        # Filter out blocked candidates
        valid = [c for c in candidates if not c.get("is_blocked")]
        valid.sort(key=lambda x: x['composite_score'], reverse=True)
        return valid[:3]
