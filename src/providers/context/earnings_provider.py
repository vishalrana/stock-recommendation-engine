import yfinance as yf
import pandas as pd
from src.providers.base import EarningsContext

class EarningsProvider:
    def get_surprise(self, ticker: str) -> EarningsContext:
        try:
            stock = yf.Ticker(ticker)
            # Fetch the latest earnings data
            earnings = stock.earnings
            if earnings is not None and not earnings.empty:
                # Get the most recent quarter
                latest = earnings.iloc[-1]
                surprise = latest.get('surprise', 0)  # yfinance has a 'surprise' column
                # If no surprise column, calculate it: (actual - estimate) / abs(estimate)
                if 'surprise' not in latest and 'estimated' in latest and 'actual' in latest:
                    est = latest['estimated']
                    actual = latest['actual']
                    if est and est != 0:
                        surprise = (actual - est) / abs(est) * 100
                return EarningsContext(
                    surprise_percent=surprise,
                    is_recent=True  # Assuming it's recent since we fetched latest
                )
        except Exception:
            pass
        return EarningsContext()
