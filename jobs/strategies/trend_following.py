import logging
from typing import Optional, List
import pandas as pd
from jobs.strategies.base import StrategyInterface

logger = logging.getLogger(__name__)


class TrendFollowingStrategy(StrategyInterface):
    @property
    def name(self) -> str:
        return "Trend Following"

    @property
    def description(self) -> str:
        return "Ride strong uptrends with trailing stops"

    def minimum_confidence(self) -> str:
        return "Buy"

    def scan(self, ticker: str, df: pd.DataFrame, regime: str, metrics: dict) -> Optional[dict]:
        # Require minimum history
        if len(df) < 200:
            return None

        # Normalized to uppercase columns for consistency
        price = df['CLOSE'].iloc[-1]
        sma200 = df['CLOSE'].rolling(200).mean().iloc[-1]
        sma50 = df['CLOSE'].rolling(50).mean().iloc[-1]
        high_20 = df['HIGH'].rolling(20).max().iloc[-1]
        low_10 = df['LOW'].rolling(10).min().iloc[-1]
        volume_avg = df['VOLUME'].rolling(20).mean().iloc[-1]
        volume_today = df['VOLUME'].iloc[-1]

        current_rsi = df['RSI_14'].iloc[-1]
        adx_value = df['ADX_14'].iloc[-1]
        macd_histogram = df['MACD_HIST'].iloc[-1]

        # === GATES ===
        # 1. Trend gate: Price > 200 DMA (strong long-term trend)
        if price <= sma200 * 1.02:  # Must be at least 2% above 200 DMA
            return None

        # 2. Momentum gate: Price > 50 DMA (intermediate trend up)
        if price <= sma50:
            return None

        # 3. ADX gate: Trend strength >= 20
        if adx_value < 20:
            return None

        # 4. Volume gate: >= 1.2x average (institutional participation)
        volume_ratio = volume_today / volume_avg if volume_avg > 0 else 0
        if volume_ratio < 1.2:
            return None

        # 5. RSI gate: Strong momentum (60-80), not overbought > 80
        if current_rsi < 60 or current_rsi > 80:
            return None

        # 6. Breakout gate: Price within 5% of 20-day high (near breakout)
        pct_vs_high = (price / high_20 - 1) * 100
        if pct_vs_high < -5:  # Too far from recent highs
            return None

        # === SIGNAL CONSTRUCTION ===
        entry_price = price
        stop_loss = max(low_10, sma200 * 0.98)  # 10-day low or 200 DMA cushion
        risk = entry_price - stop_loss
        risk_pct = (risk / entry_price) * 100 if entry_price > 0 else 0

        # Min risk gate: >= 2.5%
        if risk_pct < 2.5:
            return None

        # Targets: trends run further than pullbacks
        target_1 = entry_price * 1.20
        target_2 = entry_price * 1.35
        target_3 = entry_price * 1.50
        target_1_pct = 20.0
        target_2_pct = 35.0
        target_3_pct = 50.0

        # Weighted R/R
        reward = (target_1 - entry_price) * 0.5 + (target_2 - entry_price) * 0.3 + (target_3 - entry_price) * 0.2
        weighted_rr = reward / risk if risk > 0 else 0
        position_sizing = "50/30/20"

        # === NARRATIVE ===
        def generate_trend_narrative(price, sma200, sma50, volume_ratio, current_rsi, adx_value):
            parts = []
            pct_vs_200 = (price / sma200 - 1) * 100
            if pct_vs_200 > 10:
                parts.append("Strong uptrend")
            elif pct_vs_200 > 5:
                parts.append("Rising trend")
            else:
                parts.append("Above 200 DMA")

            if volume_ratio > 1.5:
                parts.append("strong volume")
            elif volume_ratio > 1.2:
                parts.append("volume confirming")

            if current_rsi > 70:
                parts.append("strong momentum")
            elif current_rsi > 60:
                parts.append("momentum building")

            if adx_value > 25:
                parts.append("powerful trend")
            elif adx_value > 20:
                parts.append("trend intact")

            return ", ".join(parts) + "."

        narrative = generate_trend_narrative(price, sma200, sma50, volume_ratio, current_rsi, adx_value)

        # === COMPOSITE SCORING ===
        past_win_rate = metrics.get('win_rate', 50.0) if metrics else 50.0
        total_trades = metrics.get('total_trades', 0) if metrics else 0
        expectancy_pct = metrics.get('expectancy_pct', 0.0) if metrics else 0.0
        wins = metrics.get('wins', 0) if metrics else 0
        losses = metrics.get('losses', 0) if metrics else 0

        # Trend-specific momentum score (0-30)
        pct_vs_200 = (price / sma200 - 1) * 100
        momentum_score = 0
        if pct_vs_200 > 15: momentum_score = 30
        elif pct_vs_200 > 10: momentum_score = 25
        elif pct_vs_200 > 5: momentum_score = 20
        elif pct_vs_200 > 2: momentum_score = 15
        else: momentum_score = 10

        # Expectancy score (0-40)
        exp_score = 0
        if expectancy_pct >= 10: exp_score = 40
        elif expectancy_pct >= 5: exp_score = 35
        elif expectancy_pct >= 2: exp_score = 25
        elif expectancy_pct >= 0: exp_score = 15
        elif expectancy_pct >= -5: exp_score = 5
        else: exp_score = 0

        # Win rate score (0-20)
        wr_score = 0
        if past_win_rate >= 70: wr_score = 20
        elif past_win_rate >= 60: wr_score = 17
        elif past_win_rate >= 50: wr_score = 14
        elif past_win_rate >= 40: wr_score = 10
        elif past_win_rate >= 25: wr_score = 5
        else: wr_score = 0

        # Regime score (0-10)
        regime_score = 10 if regime == 'bull' else 5 if regime == 'sideways' else 0

        composite_score = momentum_score + exp_score + wr_score + regime_score

        # Tier mapping
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
        MIN_SAMPLE = 10

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
            'quality_score': round(composite_score * 0.3, 1) if is_blocked else round(composite_score, 1),
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
            'ema20': round(sma50, 2),  # Using SMA50 as proxy for EMA20 display
            'is_blocked': is_blocked,
            'blocked_reason': blocked_reason,
            'strategy': 'Trend Following',
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
        # Sort by composite_score descending
        valid.sort(key=lambda x: x['composite_score'], reverse=True)
        # Return top 5 from this strategy
        return valid[:5]
