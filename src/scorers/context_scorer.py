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
            'max_scores': {'analyst': 30, 'earnings': 30, 'fundamental': 20, 'news': 20, 'pv_signal': 15},
            'analyst': {'upside_threshold_bonus': 0.05, 'buy_bonus': 10},
            'earnings': {'surprise_beat_big': 5.0, 'surprise_beat_small': 0.0, 'surprise_miss_big': -5.0},
            'fundamental': {'debt_to_equity_max': 1.0, 'current_ratio_min': 1.5},
            'news': {'sentiment_positive_threshold': 0.2, 'sentiment_negative_threshold': -0.2},
            'global_multiplier': 0.15
        }
    
    def calculate(self, ctx: AggregatedContext, current_price: float, tech_data=None) -> float:
        score = 0.0
        
        # 1. Analyst Alignment (Max 30)
        if ctx.analyst.target_mean_price and current_price > 0:
            upside = (ctx.analyst.target_mean_price - current_price) / current_price
            if upside > self.config['analyst']['upside_threshold_bonus']:
                score += 30
            elif upside > 0:
                score += 15
            if ctx.analyst.recommendation in ["buy", "strong_buy"]:
                score += self.config['analyst']['buy_bonus']
        
        # 2. Earnings Momentum (Max 30)
        if ctx.earnings.surprise_percent is not None:
            surprise = ctx.earnings.surprise_percent
            if surprise > self.config['earnings']['surprise_beat_big']:
                score += 30
            elif surprise > self.config['earnings']['surprise_beat_small']:
                score += 15
            elif surprise < self.config['earnings']['surprise_miss_big']:
                score -= 15
        
        # 3. Fundamental Safety (Max 20)
        if ctx.fundamental.debt_to_equity is not None and ctx.fundamental.debt_to_equity < self.config['fundamental']['debt_to_equity_max']:
            score += 10
        if ctx.fundamental.current_ratio is not None and ctx.fundamental.current_ratio > self.config['fundamental']['current_ratio_min']:
            score += 10
        
        # 4. News Sentiment (Max 20)
        if ctx.news.headline_sentiment > self.config['news']['sentiment_positive_threshold']:
            score += min(20, ctx.news.headline_sentiment * 50)  # Scale up
        elif ctx.news.headline_sentiment < self.config['news']['sentiment_negative_threshold']:
            score -= 10
        
        # 5. Price/Volume Event (Max 15)
        if ctx.price_volume_signal > 0:
            score += min(15, ctx.price_volume_signal * 10)
        
        # Fallback: If raw score is still 0 and tech_data provided, use technical heuristic
        if score == 0 and tech_data:
            rsi = tech_data.get('rsi', 50)
            adx = tech_data.get('adx', 20)
            vol_ratio = tech_data.get('volume_ratio', 1.0)
            
            # Simple heuristic to give a small meaningful score (0-15 points)
            fallback = (
                max(0, (rsi - 30) / 70) * 5 +   # 0-5 points for RSI momentum
                max(0, (adx - 10) / 40) * 5 +   # 0-5 points for trend strength
                max(0, (vol_ratio - 0.5) * 10)  # 0-5 points for volume confirmation
            )
            score = max(score, min(15, fallback))
        
        # Clamp to [0, 100] and apply global weight (15%)
        raw_score = max(0, min(100, score))
        return raw_score * self.config.get('global_multiplier', 0.15)
