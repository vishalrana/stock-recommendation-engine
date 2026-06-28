from src.providers.context.metadata_provider import MetadataProvider
from src.providers.context.earnings_provider import EarningsProvider
from src.providers.context.news_provider import FinBERTNewsProvider
from src.providers.base import AggregatedContext
import pandas as pd

class ContextAggregator:
    def __init__(self):
        self.metadata = MetadataProvider()
        self.earnings = EarningsProvider()
        self.news = FinBERTNewsProvider()
    
    def get_aggregated(self, ticker: str, price_df: pd.DataFrame) -> AggregatedContext:
        # Run all providers
        analyst = self.metadata.get_analyst_rating(ticker)
        fundamental = self.metadata.get_fundamentals(ticker)
        earnings = self.earnings.get_surprise(ticker)
        news = self.news.fetch_and_score(ticker)
        
        # Calculate Price/Volume Event Signal (Proxy for news/action)
        pv_signal = 0.0
        if price_df is not None and not price_df.empty:
            price_df = price_df.copy()
            if isinstance(price_df.columns, pd.MultiIndex):
                price_df.columns = price_df.columns.get_level_values(0)
            # Normalize column names lookup (case-insensitive)
            df_cols = {c.title(): c for c in price_df.columns}
            close_col = df_cols.get("Close")
            vol_col = df_cols.get("Volume")
            
            if close_col and vol_col and len(price_df) >= 20:
                vol_sma = price_df[vol_col].rolling(20).mean().iloc[-1]
                current_vol = price_df[vol_col].iloc[-1]
                vol_ratio = current_vol / vol_sma if vol_sma > 0 else 1.0
                
                close_delta = (price_df[close_col].iloc[-1] / price_df[close_col].iloc[-2]) - 1 if len(price_df) >= 2 else 0
                
                if vol_ratio > 2.0 and close_delta > 0.05:
                    pv_signal = 1.5  # Extra strong
                elif vol_ratio > 1.5 and close_delta > 0.02:
                    pv_signal = 1.0
        
        return AggregatedContext(
            analyst=analyst,
            fundamental=fundamental,
            earnings=earnings,
            news=news,
            price_volume_signal=pv_signal
        )
