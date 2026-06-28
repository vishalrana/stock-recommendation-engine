import feedparser
import requests
from transformers import pipeline
from src.providers.base import NewsContext

class FinBERTNewsProvider:
    def __init__(self):
        self.sentiment_pipeline = None  # Lazy load to avoid slow imports
    
    def _load_model(self):
        if self.sentiment_pipeline is None:
            # Load open-source FinBERT (ProsusAI/finbert)
            self.sentiment_pipeline = pipeline(
                "sentiment-analysis",
                model="ProsusAI/finbert",
                device=-1  # CPU, set to 0 if GPU available
            )
    
    def fetch_and_score(self, ticker: str) -> NewsContext:
        try:
            # Scrape Google News RSS
            rss_url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(rss_url, headers=headers, timeout=10)
            feed = feedparser.parse(response.text)
            
            sentiments = []
            # Limit to first 10 articles to save compute
            for entry in feed.entries[:10]:
                if entry.title:
                    # Only run FinBERT if we have articles
                    self._load_model()
                    result = self.sentiment_pipeline(entry.title)[0]
                    # Map label to score (handle positive, negative, neutral)
                    label = result['label'].lower()
                    if label == 'positive':
                        score = result['score']
                    elif label == 'negative':
                        score = -result['score']
                    else:
                        score = 0.0
                    sentiments.append(score)
            
            avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0
            
            return NewsContext(
                headline_sentiment=avg_sentiment,
                article_count=len(sentiments),
                source_reliability=0.8  # Google News sources are credible
            )
        except Exception as e:
            print(f"News fetch failed for {ticker}: {e}")
            # Return neutral on failure
            return NewsContext(headline_sentiment=0.0, article_count=0)
