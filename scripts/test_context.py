import os
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd
import yfinance as yf
from src.providers.context.aggregator import ContextAggregator
from src.scorers.context_scorer import ContextScorer

if __name__ == "__main__":
    ticker = "AAPL"
    print(f"Downloading historical data for {ticker}...")
    price_df = yf.download(ticker, period="1mo", auto_adjust=True)
    if isinstance(price_df.columns, pd.MultiIndex):
        price_df.columns = price_df.columns.get_level_values(0)
    
    print("Initializing ContextAggregator...")
    agg = ContextAggregator()
    
    print(f"Fetching aggregated context for {ticker}...")
    ctx = agg.get_aggregated(ticker, price_df)
    
    print("Initializing ContextScorer...")
    scorer = ContextScorer()
    
    print("Calculating context score...")
    current_price = float(price_df['Close'].iloc[-1])
    score = scorer.calculate(ctx, current_price)
    
    print("\n--- RESULTS ---")
    print(f"Context Score for {ticker}: {score:.2f}")
    print(f"Analyst Target Price: {ctx.analyst.target_mean_price}")
    print(f"Analyst Recommendation: {ctx.analyst.recommendation}")
    print(f"News Sentiment Score: {ctx.news.headline_sentiment:.4f} (based on {ctx.news.article_count} headlines)")
    print(f"Recent Earnings Surprise %: {ctx.earnings.surprise_percent}")
    print(f"Price/Volume Event Signal: {ctx.price_volume_signal}")
