from src.providers.context.metadata_provider import MetadataProvider
from src.providers.context.earnings_provider import EarningsProvider
from src.providers.context.news_provider import FinBERTNewsProvider
from src.providers.base import AggregatedContext
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class ContextAggregator:
    def __init__(self):
        self.metadata = MetadataProvider()
        self.earnings = EarningsProvider()
        self.news = FinBERTNewsProvider()
    
    def get_aggregated(self, ticker: str, price_df: pd.DataFrame) -> AggregatedContext:
        # Check database cache first if supabase settings exist
        from jobs.supabase_client import get_client
        import os
        from datetime import datetime, timezone
        
        ttl_hours = int(os.environ.get("CONTEXT_CACHE_TTL_HOURS", "24"))
        
        supabase = None
        try:
            supabase = get_client()
        except Exception:
            pass
            
        if supabase:
            try:
                res = supabase.table("context_cache").select("*").eq("ticker", ticker.upper()).execute()
                if res.data:
                    cache_row = res.data[0]
                    updated_at_str = cache_row.get("updated_at")
                    updated_at = pd.to_datetime(updated_at_str)
                    now = datetime.now(timezone.utc)
                    age_hours = (now - updated_at).total_seconds() / 3600.0
                    
                    if age_hours < ttl_hours:
                        from src.providers.base import AnalystContext, FundamentalContext, EarningsContext, NewsContext
                        
                        analyst_target = cache_row.get("analyst_target")
                        news_sentiment = cache_row.get("news_sentiment")
                        earnings_surprise = cache_row.get("earnings_surprise")
                        
                        analyst = AnalystContext(target_mean_price=analyst_target)
                        earnings = EarningsContext(surprise_percent=earnings_surprise)
                        news = NewsContext(headline_sentiment=news_sentiment or 0.0)
                        
                        logger.info(f"Context cache HIT for {ticker} (age: {age_hours:.1f}h). Reusing cached score: {cache_row['context_score']}")
                        return AggregatedContext(
                            analyst=analyst,
                            earnings=earnings,
                            news=news,
                            cached_score=cache_row["context_score"]
                        )
            except Exception as e:
                logger.warning(f"Failed to query context cache for {ticker}: {e}")

        # Cache miss: Run all providers
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


def save_context_to_cache(ticker: str, score: float, ctx: AggregatedContext) -> None:
    """Save/upsert computed context score and sub-components into context_cache table."""
    from jobs.supabase_client import get_client
    import datetime
    
    supabase = None
    try:
        supabase = get_client()
    except Exception:
        return
        
    if supabase:
        try:
            data = {
                "ticker": ticker.upper(),
                "date": datetime.date.today().isoformat(),
                "context_score": float(score),
                "analyst_target": float(ctx.analyst.target_mean_price) if ctx.analyst.target_mean_price is not None else None,
                "news_sentiment": float(ctx.news.headline_sentiment) if ctx.news.headline_sentiment is not None else None,
                "earnings_surprise": float(ctx.earnings.surprise_percent) if ctx.earnings.surprise_percent is not None else None,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            supabase.table("context_cache").upsert(data).execute()
            logger.debug(f"Saved context cache row for {ticker}: score={score:.2f}")
        except Exception as e:
            logger.warning(f"Failed to save context cache for {ticker}: {e}")
