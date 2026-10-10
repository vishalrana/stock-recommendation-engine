import yaml
from src.providers.base import AggregatedContext
import os

class ContextScorer:
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
    
    def calculate(self, ctx: AggregatedContext, current_price: float, tech_data=None) -> float:
        score = 0.0
        
        # 1. Analyst Alignment (Max 40)
        if ctx.analyst.target_mean_price and current_price > 0:
            upside = (ctx.analyst.target_mean_price - current_price) / current_price
            if upside > self.config.get('analyst', {}).get('upside_threshold_bonus', 0.05):
                score += 30
            elif upside > 0:
                score += 15
            if ctx.analyst.recommendation in ["buy", "strong_buy"]:
                score += self.config.get('analyst', {}).get('buy_bonus', 10)
        
        # 2. Fundamental Safety (Max 20)
        if ctx.fundamental.debt_to_equity is not None and ctx.fundamental.debt_to_equity < self.config.get('fundamental', {}).get('debt_to_equity_max', 1.0):
            score += 10
        if ctx.fundamental.current_ratio is not None and ctx.fundamental.current_ratio > self.config.get('fundamental', {}).get('current_ratio_min', 1.5):
            score += 10
        
        # 3. News Sentiment (Max 20)
        if ctx.news.headline_sentiment > self.config.get('news', {}).get('sentiment_positive_threshold', 0.2):
            score += min(20, ctx.news.headline_sentiment * 50)  # Scale up
        elif ctx.news.headline_sentiment < self.config.get('news', {}).get('sentiment_negative_threshold', -0.2):
            score -= 10
        
        # 4. Price/Volume Event (Max 15)
        if ctx.price_volume_signal > 0:
            score += min(15, ctx.price_volume_signal * 10)
        
        # No technical fallback: RSI / ADX / volume are already in the momentum sub-score, and a
        # context score must reflect context data. When no context data is available the caller
        # marks context unavailable and the composite renormalizes the other weights instead.
        # (tech_data is accepted for signature compatibility only.)

        # Normalize non-earnings score to 0-100 scale:
        # Maximum unscaled non-earnings components = 80 (excluding pv) / 95 (including pv).
        # Normalization factor: 1.25 (scaled from 80 base ceiling)
        normalized_score = min(100.0, max(0.0, score * 1.25))
        return round(normalized_score, 4)

    def calculate_with_breakdown(self, ctx: AggregatedContext, current_price: float, tech_data=None):
        """Return (total_score, analyst_score, earnings_score, fundamental_score, news_score)."""
        analyst_score = 0.0
        earnings_score = 0.0  # Decoupled: earnings is not part of initial opportunity score
        fundamental_score = 0.0
        news_score = 0.0

        # 1. Analyst (max 40)
        if ctx.analyst.target_mean_price and current_price > 0:
            upside = (ctx.analyst.target_mean_price - current_price) / current_price
            if upside > self.config.get('analyst', {}).get('upside_threshold_bonus', 0.05):
                analyst_score += 30
            elif upside > 0:
                analyst_score += 15
            if ctx.analyst.recommendation in ["buy", "strong_buy"]:
                analyst_score += self.config.get('analyst', {}).get('buy_bonus', 10)
        analyst_score = min(analyst_score, 40.0)

        # 2. Fundamental (max 20)
        if ctx.fundamental.debt_to_equity is not None and ctx.fundamental.debt_to_equity < self.config.get('fundamental', {}).get('debt_to_equity_max', 1.0):
            fundamental_score += 10.0
        if ctx.fundamental.current_ratio is not None and ctx.fundamental.current_ratio > self.config.get('fundamental', {}).get('current_ratio_min', 1.5):
            fundamental_score += 10.0

        # 3. News (max 20)
        if ctx.news.headline_sentiment > self.config.get('news', {}).get('sentiment_positive_threshold', 0.2):
            news_score += min(20.0, ctx.news.headline_sentiment * 50)
        elif ctx.news.headline_sentiment < self.config.get('news', {}).get('sentiment_negative_threshold', -0.2):
            news_score -= 10.0

        total = self.calculate(ctx, current_price, tech_data)
        return total, analyst_score, earnings_score, fundamental_score, news_score
