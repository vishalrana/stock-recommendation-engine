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
            raw_de = info.get('debtToEquity')
            debt_to_equity = (raw_de / 100.0) if raw_de is not None else None
            return FundamentalContext(
                debt_to_equity=debt_to_equity,
                current_ratio=info.get('currentRatio'),
                trailing_pe=info.get('trailingPE')
            )
        except Exception:
            return FundamentalContext()
