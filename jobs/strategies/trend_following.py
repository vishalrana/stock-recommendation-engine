import logging
from typing import Optional, List
import pandas as pd
from jobs.strategies.base import StrategyInterface
from src.utils.candidate_builder import build_candidate_from_row
from src.quant_config import STRATEGY_STOP_CONFIG

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
        from src.quant_config import normalize_regime_key
        regime_key = normalize_regime_key(regime)

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

        if 'EMA_20' in df.columns and not pd.isna(df['EMA_20'].iloc[-1]):
            ema_20 = float(df['EMA_20'].iloc[-1])
        elif len(df) >= 20 and 'CLOSE' in df.columns:
            ema_20 = float(df['CLOSE'].ewm(span=20, adjust=False).mean().iloc[-1])
        else:
            logger.warning(f"[GATE TREND] {ticker}: Missing EMA_20. Rejecting candidate.")
            return None

        current_rsi = df['RSI_14'].iloc[-1]
        adx_value = df['ADX_14'].iloc[-1]
        macd_histogram = df['MACD_HIST'].iloc[-1]

        # === GATES ===
        # 1. Trend gate: Price > 200 DMA (strong long-term trend)
        if price <= sma200 * 1.02:  # Must be at least 2% above 200 DMA
            return None
        # 1b. The long-term trend itself must be rising (200 DMA above its level 20 bars ago);
        #     price above a falling 200 DMA is a rebound, not a trend.
        sma200_prior = df['CLOSE'].rolling(200).mean().iloc[-21] if len(df) >= 220 else float("nan")
        if pd.isna(sma200_prior) or not (sma200 > sma200_prior):
            return None

        # 2. Breakout gate: Price within 5% of 20-day high (near breakout)
        pct_vs_high = (price / high_20 - 1) * 100
        if pct_vs_high < -5:  # Too far from recent highs
            return None

        # 3. Multi-Indicator Consensus Gate (Task 6.2)
        from jobs.strategies.base import consensus_pass
        volume_ratio = volume_today / volume_avg if volume_avg > 0 else 0.0
        row_data = {
            'RSI_14': current_rsi,
            'ADX_14': adx_value,
            'volume_ratio': volume_ratio,
            'CLOSE': price,
            'DMA_50': sma50
        }
        if not consensus_pass(row_data):
            return None

        # === SIGNAL CONSTRUCTION ===
        entry_price = price
        
        # ATR-Based Stop Loss (Task 6.3)
        if 'ATR_14' in df.columns and not pd.isna(df['ATR_14'].iloc[-1]) and float(df['ATR_14'].iloc[-1]) > 0:
            atr = float(df['ATR_14'].iloc[-1])
        elif len(df) >= 15 and all(c in df.columns for c in ['HIGH', 'LOW', 'CLOSE']):
            from src.indicators import calculate_atr
            computed_atr = calculate_atr(df, 14)
            atr = float(computed_atr.iloc[-1]) if not pd.isna(computed_atr.iloc[-1]) and float(computed_atr.iloc[-1]) > 0 else None
            if atr is None:
                return None
        else:
            logger.warning(f"[GATE TREND] {ticker}: Missing or non-positive ATR_14. Rejecting candidate.")
            return None
        atr_mult = STRATEGY_STOP_CONFIG.get("trend_following", {}).get("atr_multiplier", 2.5)
        stop_loss = min(low_10, entry_price - atr_mult * atr)
            
        risk = entry_price - stop_loss
        risk_pct = (risk / entry_price) * 100 if entry_price > 0 else 0

        # Min risk gate: >= 2.5%
        if risk_pct < 2.5:
            return None

        # Targets: trends run further than pullbacks
        target_1 = entry_price * 1.12
        target_2 = entry_price * 1.22
        target_3 = entry_price * 1.35
        target_1_pct = 12.0
        target_2_pct = 22.0
        target_3_pct = 35.0

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
        past_win_rate = metrics.get('shrunk_win_rate', metrics.get('win_rate', 50.0)) if metrics else 50.0
        total_trades = metrics.get('completed_trades', metrics.get('total_trades', 0)) if metrics else 0
        expectancy_pct = metrics.get('shrunk_expectancy', metrics.get('expectancy_pct', 1.44)) if metrics else 1.44
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
        regime_score = 10 if regime_key == 'bull' else 5 if regime_key == 'sideways' else 0

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

        # Qualification is decided centrally by the composite score; no per-strategy blocking.
        is_blocked = False
        blocked_reason = None

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
            'ema20': round(ema_20, 2),
            'dma_50': round(sma50, 2),
            'is_blocked': is_blocked,
            'blocked_reason': blocked_reason,
            'strategy': 'Trend Following',
            'context_score': 0.0,  # This strategy doesn't use context scoring yet
        }

        return signal

    def rank_candidates(self, candidates: List[dict], regime: str) -> List[dict]:
        # P0-1: Central SignalRanker is single source of truth.
        # Preserve non-blocked candidates without arbitrary strategy truncation.
        return [c for c in candidates if not c.get("is_blocked")]
