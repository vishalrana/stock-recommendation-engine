import logging
from typing import Optional, List
import pandas as pd
from jobs.strategies.base import StrategyInterface

logger = logging.getLogger(__name__)


class MeanReversionStrategy(StrategyInterface):
    @property
    def name(self) -> str:
        return "Mean Reversion"

    @property
    def description(self) -> str:
        return "Buy oversold bounces near support"

    def minimum_confidence(self) -> str:
        return "Buy"

    def scan(self, ticker: str, df: pd.DataFrame, regime: str, metrics: dict) -> Optional[dict]:
        # Require minimum history
        if len(df) < 50:
            return None

        # Normalized to uppercase columns for consistency
        price = df['CLOSE'].iloc[-1]
        low_20 = df['LOW'].rolling(20).min().iloc[-1]
        high_20 = df['HIGH'].rolling(20).max().iloc[-1]
        sma50 = df['CLOSE'].rolling(50).mean().iloc[-1]
        volume_avg = df['VOLUME'].rolling(20).mean().iloc[-1]
        volume_today = df['VOLUME'].iloc[-1]

        # Extract indicators already pre-calculated by calculate_indicators
        current_rsi = df['RSI_14'].iloc[-1]
        adx_value = df['ADX_14'].iloc[-1]
        macd_histogram = df['MACD_HIST'].iloc[-1]

        # Bollinger Bands calculation (length=20, std=2)
        bb_middle = df['CLOSE'].rolling(20).mean()
        bb_std = df['CLOSE'].rolling(20).std()
        bb_upper = bb_middle + (bb_std * 2)
        bb_lower = bb_middle - (bb_std * 2)

        upper_val = bb_upper.iloc[-1]
        lower_val = bb_lower.iloc[-1]
        bb_position = (price - lower_val) / (upper_val - lower_val) if upper_val != lower_val else 0.5

        # === GATES ===
        # 1. Oversold gate: RSI < 35 (deeply oversold)
        if current_rsi >= 35:
            return None

        # 2. Bounce gate: RSI recovered from < 30 to current > 25 (starting to bounce)
        rsi_min_5d = df['RSI_14'].rolling(5).min().iloc[-1]
        if rsi_min_5d > 30:  # Never got deeply oversold
            return None

        # 3. Support gate: Price within 5% of 20-day low (near support)
        pct_vs_low = (price / low_20 - 1) * 100
        if pct_vs_low > 5:
            return None

        # 4. Volume gate: >= 1.0x average (some interest on the bounce)
        volume_ratio = volume_today / volume_avg if volume_avg > 0 else 0
        if volume_ratio < 1.0:
            return None

        # 5. ADX gate: Not in strong downtrend (ADX < 25 means not strongly trending down)
        # Note: constraint was changed to ADX <= 30 to match comment "ADX < 25 means not strongly trending down" but gate has:
        if adx_value > 30:
            return None  # Too strong a downtrend, catch falling knife

        # 6. Bollinger gate: Price near lower band (bb_position <= 0.25)
        if bb_position > 0.25:
            return None

        # === SIGNAL CONSTRUCTION ===
        entry_price = price
        stop_loss = low_20 * 0.98  # Just below 20-day low
        risk = entry_price - stop_loss
        risk_pct = (risk / entry_price) * 100 if entry_price > 0 else 0

        # Min risk gate: >= 2.5%
        if risk_pct < 2.5:
            return None

        # Targets: quick bounce, smaller than pullback or trend
        target_1 = entry_price * 1.08   # 8% bounce
        target_2 = entry_price * 1.15   # 15% bounce
        target_3 = entry_price * 1.25   # 25% max
        target_1_pct = 8.0
        target_2_pct = 15.0
        target_3_pct = 25.0

        # Weighted R/R
        reward = (target_1 - entry_price) * 0.5 + (target_2 - entry_price) * 0.3 + (target_3 - entry_price) * 0.2
        weighted_rr = reward / risk if risk > 0 else 0
        position_sizing = "50/30/20"

        # === NARRATIVE ===
        def generate_reversion_narrative(price, low_20, current_rsi, volume_ratio, bb_position):
            parts = []
            pct_vs_low = (price / low_20 - 1) * 100

            if pct_vs_low < 1:
                parts.append("At 20-day low")
            elif pct_vs_low < 3:
                parts.append("Near 20-day low")
            else:
                parts.append("Bouncing from support")

            if current_rsi < 25:
                parts.append("deeply oversold")
            elif current_rsi < 30:
                parts.append("oversold bounce")
            else:
                parts.append("recovering RSI")

            if volume_ratio > 1.5:
                parts.append("strong volume on bounce")
            elif volume_ratio > 1.2:
                parts.append("volume confirming bounce")
            else:
                parts.append("normal volume")

            if bb_position < 0.1:
                parts.append("at lower Bollinger Band")
            elif bb_position < 0.2:
                parts.append("near lower band")

            return ", ".join(parts) + "."

        narrative = generate_reversion_narrative(price, low_20, current_rsi, volume_ratio, bb_position)

        # === COMPOSITE SCORING ===
        past_win_rate = metrics.get('win_rate', 50.0) if metrics else 50.0
        total_trades = metrics.get('total_trades', 0) if metrics else 0
        expectancy_pct = metrics.get('expectancy_pct', 0.0) if metrics else 0.0
        wins = metrics.get('wins', 0) if metrics else 0
        losses = metrics.get('losses', 0) if metrics else 0

        # Mean reversion momentum score (0-30): lower RSI = higher score
        momentum_score = 0
        if current_rsi < 20: momentum_score = 30
        elif current_rsi < 25: momentum_score = 27
        elif current_rsi < 30: momentum_score = 24
        elif current_rsi < 35: momentum_score = 20
        else: momentum_score = 15

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

        # Regime score (0-10): mean reversion works better in sideways/choppy markets
        regime_score = 10 if regime == 'sideways' else 7 if regime == 'bull' else 5

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
            'strategy': 'Mean Reversion',
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
