import logging
import math
from typing import Optional, List
import numpy as np
import pandas as pd
from jobs.strategies.base import StrategyInterface
from src.utils.candidate_builder import build_candidate_from_row

logger = logging.getLogger(__name__)


class CrossSectionalMomentumStrategy(StrategyInterface):
    @property
    def name(self) -> str:
        return "Cross-Sectional Momentum"

    @property
    def description(self) -> str:
        return "Buy top performers relative to peers"

    def minimum_confidence(self) -> str:
        return "Buy"

    def scan(self, ticker: str, df: pd.DataFrame, regime: str, metrics: dict) -> Optional[dict]:
        from src.quant_config import normalize_regime_key
        regime_key = normalize_regime_key(regime)

        if len(df) < 64:  # 3 months (63 trading-day intervals)
            return None

        # Normalized to uppercase columns for consistency
        price = df['CLOSE'].iloc[-1]
        price_63d = df['CLOSE'].iloc[-64]
        returns_3m = (price / price_63d - 1) * 100 if price_63d > 0 else 0

        sma50 = df['CLOSE'].rolling(50).mean().iloc[-1]
        volume_avg = df['VOLUME'].rolling(20).mean().iloc[-1]
        volume_today = df['VOLUME'].iloc[-1]

        if 'EMA_20' in df.columns and not pd.isna(df['EMA_20'].iloc[-1]):
            ema_20 = float(df['EMA_20'].iloc[-1])
        elif len(df) >= 20 and 'CLOSE' in df.columns:
            ema_20 = float(df['CLOSE'].ewm(span=20, adjust=False).mean().iloc[-1])
        else:
            logger.warning(f"[GATE CROSS] {ticker}: Missing EMA_20. Rejecting candidate.")
            return None

        # Extract indicators already pre-calculated by calculate_indicators
        current_rsi = df['RSI_14'].iloc[-1]
        adx_value = df['ADX_14'].iloc[-1]
        if 'MACD_HIST' not in df.columns:
            logger.warning(f"[GATE CROSS] {ticker}: Missing MACD_HIST. Rejecting candidate.")
            return None
        macd_hist_val = float(df['MACD_HIST'].iloc[-1])
        if pd.isna(macd_hist_val) or np.isinf(macd_hist_val):
            return None

        # === GATES ===
        # 1. Top 15% 3-month returns (cross-sectional momentum)
        # This is checked at orchestrator level — see load_universe logic in generate_signals.py
        # Individual scan just validates the stock passes trend/volume

        # 2. Price > 50 DMA
        if price <= sma50:
            return None

        # 2b. Long-term uptrend: price above a rising 200 DMA. A sharp 3-month rebound inside a
        #     long-term downtrend ranks high on 63-day return but is not persistent momentum.
        sma200_series = df['CLOSE'].rolling(200).mean()
        if len(sma200_series) < 221 or pd.isna(sma200_series.iloc[-21]):
            return None
        sma200 = sma200_series.iloc[-1]
        if not (price > sma200 and sma200 > sma200_series.iloc[-21]):
            return None

        # 3. RSI 40-75 (relaxed from 50-70)
        if current_rsi < 40 or current_rsi > 75:
            return None

        # 4. ADX >= 10 (relaxed from 15)
        if adx_value < 10:
            return None

        # 5. Volume >= 0.8x average (relaxed from 1.0x)
        volume_ratio = volume_today / volume_avg if volume_avg > 0 else 0
        if volume_ratio < 0.8:
            return None

        # 6. Positive 3-month return
        if returns_3m <= 0:
            return None

        # === SIGNAL CONSTRUCTION ===
        entry_price = price
        stop_loss = sma50 * 0.97
        risk = entry_price - stop_loss
        risk_pct = (risk / entry_price) * 100 if entry_price > 0 else 0

        if risk_pct < 2.5:
            return None

        # Targets: momentum persists
        target_1 = entry_price * 1.10   # 10%
        target_2 = entry_price * 1.18   # 18%
        target_3 = entry_price * 1.25   # 25%
        target_1_pct = 10.0
        target_2_pct = 18.0
        target_3_pct = 25.0

        reward = (target_1 - entry_price) * 0.5 + (target_2 - entry_price) * 0.3 + (target_3 - entry_price) * 0.2
        weighted_rr = reward / risk if risk > 0 else 0
        position_sizing = "50/30/20"

        # Narrative
        parts = []
        if returns_3m > 30:
            parts.append(f"Top performer +{returns_3m:.0f}% in 3M")
        elif returns_3m > 20:
            parts.append(f"Strong performer +{returns_3m:.0f}% in 3M")
        else:
            parts.append(f"Outperforming +{returns_3m:.0f}% in 3M")

        if volume_ratio > 1.5:
            parts.append("volume confirming")
        else:
            parts.append("normal volume")

        if adx_value > 25:
            parts.append("powerful trend")
        else:
            parts.append("trend intact")

        narrative = ", ".join(parts) + "."

        # Scoring: rank by 3-month return primarily
        past_win_rate = metrics.get('shrunk_win_rate', metrics.get('win_rate', 50.0)) if metrics else 50.0
        total_trades = metrics.get('completed_trades', metrics.get('total_trades', 0)) if metrics else 0
        expectancy_pct = metrics.get('shrunk_expectancy', metrics.get('expectancy_pct', 1.53)) if metrics else 1.53
        wins = metrics.get('wins', 0) if metrics else 0
        losses = metrics.get('losses', 0) if metrics else 0


        momentum_score = min(30.0, returns_3m)  # Cap at 30
        if momentum_score < 10: momentum_score = 10.0

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

        regime_score = 10 if regime_key == 'bull' else 6 if regime_key == 'sideways' else 3

        composite_score = momentum_score + exp_score + wr_score + regime_score

        if composite_score >= 70: tier_label = 'Strong Buy'
        elif composite_score >= 50: tier_label = 'Buy'
        elif composite_score >= 35: tier_label = 'Watch'
        else: tier_label = 'Speculative'

        # Qualification is decided centrally by the composite score; no per-strategy blocking.
        is_blocked = False
        blocked_reason = None

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
            'macd_histogram': round(macd_hist_val, 4),
            'ema20': round(ema_20, 2),
            'dma_50': round(sma50, 2),
            'is_blocked': is_blocked,
            'blocked_reason': blocked_reason,
            'strategy': 'Cross-Sectional Momentum',
            'context_score': 0.0,  # This strategy doesn't use context scoring yet
            'returns_3m': round(returns_3m, 1),  # For ranking
        }

    def rank_candidates(self, candidates: List[dict], regime: str) -> List[dict]:
        # P0-1: Central SignalRanker is single source of truth.
        return [c for c in candidates if not c.get("is_blocked")]
