import yaml
from typing import Dict, Iterable, Optional, Tuple
from src.providers.base import AggregatedContext, DataQuality
import os


# Maximum points per context component (unchanged weights of the original design)
ANALYST_MAX_POINTS = 40.0
FUNDAMENTAL_SUBCOMPONENT_POINTS = 10.0   # D/E and current ratio, 10 each
NEWS_MAX_POINTS = 20.0


class ContextScorer:
    """
    Context score (0-100) from analyst, fundamental and news data.

    The score is the share of AVAILABLE points earned: earned / max_available * 100, over the
    components that actually have data. Missing data is excluded, never scored as zero (the old
    formula (analyst + fundamental + news) * 1.25 assumed all three were present, so a stock with
    perfect fundamentals but no analyst coverage could score at most 25). A component counts as
    available only when its data arrived:
      * analyst:      a mean price target is present
      * fundamental:  each of D/E and current ratio separately, when known
      * news:         scored headlines (DataQuality.VALID or article_count > 0)
    Only the components listed in quant_config.CONTEXT_SCORE_COMPONENTS count (fundamentals are
    display-only by default).
    No technical inputs (RSI / ADX / volume / price-volume events) are used: those are already in
    the momentum sub-score.
    """

    def __init__(self, config_path="config/context_weights.yaml"):
        self.config = self._load_config(config_path)

    def _load_config(self, path):
        if os.path.exists(path):
            with open(path, 'r') as f:
                return yaml.safe_load(f)
        # Default config if file missing
        return {
            'max_scores': {'analyst': 40, 'fundamental': 20, 'news': 20, 'pv_signal': 15},
            'analyst': {'upside_threshold_bonus': 0.05, 'buy_bonus': 10},
            'fundamental': {'debt_to_equity_max': 1.0, 'current_ratio_min': 1.5},
            'news': {'sentiment_positive_threshold': 0.2, 'sentiment_negative_threshold': -0.2},
            'global_multiplier': 0.15
        }

    def component_points(
        self, ctx: AggregatedContext, current_price: float, components: Optional[Iterable[str]] = None
    ) -> Dict[str, Tuple[float, float]]:
        """
        {component: (points_earned, points_available)} for components with data that count toward
        the score (`components`, default quant_config.CONTEXT_SCORE_COMPONENTS).
        """
        if components is None:
            from src import quant_config
            components = quant_config.CONTEXT_SCORE_COMPONENTS
        include = set(components)
        out: Dict[str, Tuple[float, float]] = {}

        # 1. Analyst alignment (max 40)
        analyst = ctx.analyst
        if analyst is not None and analyst.target_mean_price and current_price and current_price > 0:
            pts = 0.0
            upside = (analyst.target_mean_price - current_price) / current_price
            if upside > self.config.get('analyst', {}).get('upside_threshold_bonus', 0.05):
                pts += 30
            elif upside > 0:
                pts += 15
            if analyst.recommendation in ["buy", "strong_buy"]:
                pts += self.config.get('analyst', {}).get('buy_bonus', 10)
            out["analyst"] = (min(pts, ANALYST_MAX_POINTS), ANALYST_MAX_POINTS)

        # 2. Fundamental safety (10 for D/E below max, 10 for current ratio above min)
        fund = ctx.fundamental
        if fund is not None:
            pts, avail = 0.0, 0.0
            if fund.debt_to_equity is not None:
                avail += FUNDAMENTAL_SUBCOMPONENT_POINTS
                if fund.debt_to_equity < self.config.get('fundamental', {}).get('debt_to_equity_max', 1.0):
                    pts += FUNDAMENTAL_SUBCOMPONENT_POINTS
            if fund.current_ratio is not None:
                avail += FUNDAMENTAL_SUBCOMPONENT_POINTS
                if fund.current_ratio > self.config.get('fundamental', {}).get('current_ratio_min', 1.5):
                    pts += FUNDAMENTAL_SUBCOMPONENT_POINTS
            if avail > 0:
                out["fundamental"] = (pts, avail)

        # 3. News sentiment (max 20; strongly negative news costs 10)
        news = ctx.news
        if news is not None and (news.quality == DataQuality.VALID or (news.article_count or 0) > 0):
            s = news.headline_sentiment or 0.0
            pts = 0.0
            if s > self.config.get('news', {}).get('sentiment_positive_threshold', 0.2):
                pts = min(NEWS_MAX_POINTS, s * 50)
            elif s < self.config.get('news', {}).get('sentiment_negative_threshold', -0.2):
                pts = -10.0
            out["news"] = (pts, NEWS_MAX_POINTS)
        return {k: v for k, v in out.items() if k in include}

    @staticmethod
    def normalize(components: Dict[str, Tuple[float, float]]) -> Optional[float]:
        avail = sum(m for _, m in components.values())
        if avail <= 0:
            return None
        earned = sum(p for p, _ in components.values())
        return round(min(100.0, max(0.0, earned / avail * 100.0)), 4)

    def calculate(self, ctx: AggregatedContext, current_price: float, tech_data=None) -> float:
        """Normalized context score (0 when no component has data; tech_data is ignored)."""
        score = self.normalize(self.component_points(ctx, current_price))
        return score if score is not None else 0.0

    def calculate_with_components(
        self, ctx: AggregatedContext, current_price: float, components: Optional[Iterable[str]] = None
    ):
        """Return (total_or_None, analyst_pts, fundamental_pts, news_pts, available_points)."""
        comps = self.component_points(ctx, current_price, components)
        total = self.normalize(comps)
        return (
            total,
            comps.get("analyst", (0.0, 0.0))[0],
            comps.get("fundamental", (0.0, 0.0))[0],
            comps.get("news", (0.0, 0.0))[0],
            sum(m for _, m in comps.values()),
        )

    def calculate_with_breakdown(self, ctx: AggregatedContext, current_price: float, tech_data=None):
        """Return (total_score, analyst_score, earnings_score, fundamental_score, news_score)."""
        total, a, f, n, _ = self.calculate_with_components(ctx, current_price)
        return (total if total is not None else 0.0), a, 0.0, f, n
