import yfinance as yf
from src.providers.base import AnalystContext, FundamentalContext

class MetadataProvider:
    def get_analyst_rating(self, ticker: str) -> AnalystContext:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            return AnalystContext(
                target_mean_price=info.get('targetMeanPrice'),
                recommendation=info.get('recommendationKey'),  # e.g., "buy"
                num_analysts=info.get('numberOfAnalystOpinions')
            )
        except Exception:
            return AnalystContext()

    def get_fundamentals(self, ticker: str) -> FundamentalContext:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            return FundamentalContext(
                debt_to_equity=info.get('debtToEquity'),
                current_ratio=info.get('currentRatio'),
                trailing_pe=info.get('trailingPE')
            )
        except Exception:
            return FundamentalContext()
