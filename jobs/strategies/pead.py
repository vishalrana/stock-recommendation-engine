import logging
import math
from datetime import datetime
from typing import Optional, List
import numpy as np
import pandas as pd
from jobs.strategies.base import StrategyInterface
from src.utils.candidate_builder import build_candidate_from_row

logger = logging.getLogger(__name__)


def get_last_earnings_date(
    ticker: str,
    as_of_date: Optional[datetime.date] = None,
    earnings_calendar: Optional[dict] = None,
    supabase=None,
    allow_network: bool = False,
) -> Optional[datetime.date]:
    """Fetch last earnings date from shared calendar map, local cache, or Supabase. Returns datetime.date or None."""
    ticker_upper = ticker.strip().upper()
    if earnings_calendar and ticker_upper in earnings_calendar:
        rec = earnings_calendar[ticker_upper]
        last_e_str = rec.get("last_earnings_date") or rec.get("last_earnings")
        if last_e_str:
            try:
                parsed = datetime.strptime(str(last_e_str)[:10], "%Y-%m-%d").date()
                if as_of_date is None or parsed <= as_of_date:
                    return parsed
            except Exception:
                pass

    from src.utils.earnings_cache import get_ticker_earnings
    last_e_str, _ = get_ticker_earnings(ticker_upper, as_of_date=as_of_date, supabase=supabase, allow_network=allow_network)
    if last_e_str:
        try:
            return datetime.strptime(str(last_e_str)[:10], "%Y-%m-%d").date()
        except Exception:
            pass
    return None


class PEADStrategy(StrategyInterface):
    def __init__(self):
        super().__init__()
        self.earnings_calendar = None
        self.supabase = None
        self.allow_network = False

    def set_earnings_calendar(self, earnings_calendar: Optional[dict], supabase=None, allow_network: bool = False):
        self.earnings_calendar = earnings_calendar
        self.supabase = supabase
        self.allow_network = allow_network

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
            if prev_day_close > 0 and (day_close / prev_day_close - 1) >= 0.02:
                has_recent_gap = True
                break

        if not has_recent_gap:
            return None

        # Determine reference evaluation date for point-in-time backtesting vs live scan
        if hasattr(df.index[-1], 'date'):
            bar_date = df.index[-1].date()
        elif isinstance(df.index[-1], (datetime, datetime.date)):
            bar_date = df.index[-1]
        else:
            bar_date = datetime.now().date()

        now_date = datetime.now().date()
        ref_date = now_date if abs((now_date - bar_date).days) <= 4 else bar_date

        # === EARNINGS GATE ===
        earnings_date = get_last_earnings_date(
            ticker,
            as_of_date=ref_date,
            earnings_calendar=self.earnings_calendar,
            supabase=self.supabase,
            allow_network=self.allow_network,
        )
        if earnings_date is None:
            return None

        # Earnings must be within 1-5 days (recent enough to matter, not too old)
        days_since_earnings = (ref_date - earnings_date).days
        if days_since_earnings < 1 or days_since_earnings > 5:
            return None

        # === GAP GATE ===
        # Stock must have gapped up >= 5% on earnings (strong reaction)
        if len(df) <= days_since_earnings + 1:
            return None
        
        earnings_close = df['CLOSE'].iloc[-days_since_earnings]
        earnings_prev_close = df['CLOSE'].iloc[-days_since_earnings - 1]
        gap_pct = (earnings_close / earnings_prev_close - 1) * 100 if earnings_prev_close > 0 else 0
        if gap_pct < 2:  # Relaxed from 5
            return None

        # Stock must still hold >= 30% of gap (relaxed from 50%)
        gap_high = df['HIGH'].iloc[-days_since_earnings:].max()
        gap_low = df['LOW'].iloc[-days_since_earnings:].min()
        gap_range = gap_high - gap_low
        if gap_range > 0:
            hold_pct = (price - gap_low) / gap_range
            if hold_pct < 0.3:
                return None
        else:
            hold_pct = 1.0

        # === PULLBACK GATE ===
        # Price must be within 5% of the gap high (relaxed from 3%)
        pct_vs_gap_high = (price / gap_high - 1) * 100
        if pct_vs_gap_high < -5:
            return None

        # === TREND GATE ===
        # Stock above 50 DMA (earnings winner in existing uptrend)
        sma50 = df['CLOSE'].rolling(50).mean().iloc[-1]
        if price <= sma50:
            return None

        # === VOLUME GATE ===
        # Volume on earnings day >= 1.5x average (relaxed from 2x)
        volume_avg = df['VOLUME'].rolling(20).mean().iloc[-1]
        earnings_volume = df['VOLUME'].iloc[-days_since_earnings]
        if earnings_volume < volume_avg * 1.5:
            return None

        # === ADX GATE ===
        # ADX >= 10 (relaxed from 15)
        adx_value = df['ADX_14'].iloc[-1]
        if adx_value < 10:
            return None

        # === SIGNAL CONSTRUCTION ===
        entry_price = price
        stop_loss = min(sma50 * 0.98, gap_low * 1.02)  # Below 50 DMA or gap low
        risk = entry_price - stop_loss
        risk_pct = (risk / entry_price) * 100 if entry_price > 0 else 0

        if risk_pct < 2.5:
            return None

        # Targets: earnings drift continues 2-4 weeks
        target_1 = entry_price * 1.08
        target_2 = entry_price * 1.15
        target_3 = entry_price * 1.22
        target_1_pct = 8.0
        target_2_pct = 15.0
        target_3_pct = 22.0

        reward = (target_1 - entry_price) * 0.5 + (target_2 - entry_price) * 0.3 + (target_3 - entry_price) * 0.2
        weighted_rr = reward / risk if risk > 0 else 0
        position_sizing = "50/30/20"

        # === NARRATIVE ===
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

        narrative = ", ".join(parts) + "."

        # === CANONICAL COMPOSITE SCORING (SignalRanker) ===
        # Delegate scoring strictly to canonical SignalRanker
        # Canonical weights: Momentum 30%, Expectancy 25%, Win Rate 15%, Regime 10%, Context 20%
        from src.utils.metrics_pipeline import build_hardened_metrics
        from src.ranker import SignalRanker, assign_tier, compute_expectancy_score

        if 'ATR_14' in df.columns and not pd.isna(df['ATR_14'].iloc[-1]) and float(df['ATR_14'].iloc[-1]) > 0:
            atr_14 = float(df['ATR_14'].iloc[-1])
        elif len(df) >= 15 and all(c in df.columns for c in ['HIGH', 'LOW', 'CLOSE']):
            from src.indicators import calculate_atr
            computed_atr = calculate_atr(df, 14)
            atr_14 = float(computed_atr.iloc[-1]) if not pd.isna(computed_atr.iloc[-1]) and float(computed_atr.iloc[-1]) > 0 else None
            if atr_14 is None:
                return None
        else:
            logger.warning(f"[GATE PEAD] {ticker}: Missing or non-positive ATR_14. Rejecting candidate.")
            return None

        if 'EMA_20' in df.columns and not pd.isna(df['EMA_20'].iloc[-1]):
            ema_20 = float(df['EMA_20'].iloc[-1])
        elif len(df) >= 20 and 'CLOSE' in df.columns:
            ema_20 = float(df['CLOSE'].ewm(span=20, adjust=False).mean().iloc[-1])
        else:
            logger.warning(f"[GATE PEAD] {ticker}: Missing EMA_20. Rejecting candidate.")
            return None

        if 'RSI_14' not in df.columns or 'MACD_HIST' not in df.columns:
            logger.warning(f"[GATE PEAD] {ticker}: Missing RSI_14 or MACD_HIST. Rejecting candidate.")
            return None

        current_rsi = float(df['RSI_14'].iloc[-1])
        if pd.isna(current_rsi) or np.isinf(current_rsi):
            return None

        volume_ratio = float(earnings_volume / volume_avg) if volume_avg > 0 else 0.0
        if pd.isna(volume_ratio) or volume_ratio <= 0:
            return None

        macd_histogram = float(df['MACD_HIST'].iloc[-1])
        if pd.isna(macd_histogram) or np.isinf(macd_histogram):
            return None

        # 1. Canonical Bayesian Historical Metrics Pipeline
        p_wr = metrics.get("past_win_rate") or metrics.get("win_rate") or metrics.get("shrunk_win_rate") if metrics else None
        p_exp = metrics.get("expectancy_pct") or metrics.get("raw_expectancy") or metrics.get("shrunk_expectancy") if metrics else None
        hardened_input = dict(metrics) if metrics else {}
        if p_exp is not None and "expectancy_pct" not in hardened_input:
            hardened_input["expectancy_pct"] = p_exp
        hardened = build_hardened_metrics(
            ticker=ticker,
            raw_record=hardened_input,
            strategy_name=self.name,
            strategy_win_rate=metrics.get("strategy_win_rate") if metrics else None,
            past_win_rate=p_wr,
        )
        past_win_rate = float(hardened["shrunk_win_rate"])
        raw_win_rate = float(hardened["raw_win_rate"])
        expectancy_pct = float(hardened["shrunk_expectancy"])
        raw_expectancy = float(hardened["raw_expectancy"]) if hardened.get("raw_expectancy") is not None else None
        total_trades = int(hardened["completed_trades"])
        wins = int(hardened["wins"])
        losses = int(hardened["losses"])
        provenance = hardened["metric_source"]

        # 2. Canonical Non-Earnings Context Scoring (Analyst, Fundamental, News)
        c_score = 0.0
        c_analyst = 0.0
        c_fundamental = 0.0
        c_news = 0.0
        de_val = None
        cr_val = None
        finbert = None
        target_c = None

        if metrics and ("context_score" in metrics or "context_analyst" in metrics):
            c_score = float(metrics.get("context_score", 0.0) or 0.0)
            c_analyst = float(metrics.get("context_analyst", 0.0) or 0.0)
            c_fundamental = float(metrics.get("context_fundamental", 0.0) or 0.0)
            c_news = float(metrics.get("context_news", 0.0) or 0.0)
            de_val = metrics.get("de_ratio")
            cr_val = metrics.get("current_ratio")
            finbert = metrics.get("finbert_sentiment")
            target_c = metrics.get("target_consensus")
        elif hasattr(self, "context_aggregator") and self.context_aggregator is not None:
            try:
                from src.scorers.context_scorer import ContextScorer
                scorer = getattr(self, "context_scorer", None) or ContextScorer()
                ctx = self.context_aggregator.get_aggregated(ticker, df)
                tech_data = {
                    'rsi': current_rsi,
                    'adx': float(adx_value),
                    'volume_ratio': volume_ratio,
                }
                c_score, c_analyst, _, c_fundamental, c_news = scorer.calculate_with_breakdown(
                    ctx, float(entry_price), tech_data
                )
                de_val = ctx.fundamental.debt_to_equity if ctx.fundamental else None
                cr_val = ctx.fundamental.current_ratio if ctx.fundamental else None
                finbert = ctx.news.headline_sentiment if ctx.news else None
                target_c = ctx.analyst.target_mean_price if ctx.analyst else None
            except Exception as ctx_err:
                logger.debug(f"PEAD Context scoring fallback: {ctx_err}")

        candidate_for_ranker = {
            'ticker': ticker,
            'strategy': 'Post-Earnings Drift',
            'current_rsi': current_rsi,
            'price': price,
            'entry_price': entry_price,
            'dma_50': sma50,
            'volume_ratio': volume_ratio,
            'macd_histogram': macd_histogram,
            'atr_14': atr_14,
            'win_rate': past_win_rate,
            'winrate_score': past_win_rate,
            'expectancy_pct': expectancy_pct,
            'expectancy_score': compute_expectancy_score('pead', expectancy_pct),
            'context_score': c_score,
            'context_analyst': c_analyst,
            'context_earnings': 0.0,
            'context_fundamental': c_fundamental,
            'context_news': c_news,
            'de_ratio': de_val,
            'current_ratio': cr_val,
            'finbert_sentiment': finbert,
            'target_consensus': target_c,
        }

        ranker = SignalRanker()
        score_res = ranker.compute_composite_score(candidate_for_ranker, regime)
        composite_score = score_res["total"]
        tier_label = assign_tier(composite_score, has_strategy_setup=True)
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
            'raw_win_rate': raw_win_rate,
            'win_rate_provenance': provenance,
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'expectancy_pct': expectancy_pct,
            'raw_expectancy': raw_expectancy,
            'current_rsi': round(df['RSI_14'].iloc[-1], 1),
            'adx_value': round(adx_value, 1),
            'volume_ratio': round(earnings_volume / volume_avg, 2),
            'macd_histogram': round(df['MACD_HIST'].iloc[-1], 4),
            'atr_14': round(atr_14, 4),
            'dma_50': round(sma50, 2),
            'ema20': round(ema_20, 2),
            'is_blocked': is_blocked,
            'blocked_reason': blocked_reason,
            'strategy': 'Post-Earnings Drift',
            'context_score': round(c_score, 2),
            'context_analyst': round(c_analyst, 2),
            'context_earnings': 0.0,
            'context_fundamental': round(c_fundamental, 2),
            'context_news': round(c_news, 2),
            'de_ratio': de_val,
            'current_ratio': cr_val,
            'finbert_sentiment': finbert,
            'target_consensus': target_c,
            'score_breakdown': score_res.get("breakdown", {}),
            'days_since_earnings': days_since_earnings,
        }

        return signal

    def rank_candidates(self, candidates: List[dict], regime: str) -> List[dict]:
        # P0-1: Canonical SignalRanker is single source of truth.
        return list(candidates)
