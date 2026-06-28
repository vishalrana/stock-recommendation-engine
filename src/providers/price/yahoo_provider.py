import yfinance as yf
import pandas as pd
from typing import List
from src.providers.base import PriceProvider

class YahooProvider(PriceProvider):
    def get_historical(self, tickers: List[str], start: str, end: str) -> pd.DataFrame:
        try:
            # Bulk download is fast and handles missing tickers gracefully
            data = yf.download(tickers, start=start, end=end, group_by='ticker', auto_adjust=True)
            
            # If single ticker, yfinance returns a single DataFrame, so we reshape to MultiIndex
            if len(tickers) == 1:
                data = data.stack(level=0).swaplevel(0, 1)
            
            return data
        except Exception as e:
            print(f"YahooProvider bulk download failed: {e}. Trying individually...")
            # Fallback to individual downloads for resilience
            all_data = []
            for t in tickers:
                try:
                    df = yf.download(t, start=start, end=end, auto_adjust=True)
                    df['Ticker'] = t
                    df.set_index('Ticker', append=True, inplace=True)
                    all_data.append(df)
                except:
                    continue
            if not all_data:
                return pd.DataFrame()
            return pd.concat(all_data).swaplevel(0, 1)
