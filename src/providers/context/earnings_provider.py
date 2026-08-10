import yfinance as yf
import pandas as pd
from src.providers.base import EarningsContext

class EarningsProvider:
    def get_surprise(self, ticker: str) -> EarningsContext:
        try:
            stock = yf.Ticker(ticker)
            # Use modern yfinance API: .earnings_dates gives actual/estimate EPS
            earnings_dates = stock.earnings_dates
            if earnings_dates is not None and not earnings_dates.empty:
                # Filter to rows that have both actual and estimated EPS (past quarters)
                past = earnings_dates.dropna(subset=["Reported EPS", "EPS Estimate"])
                if not past.empty:
                    latest = past.iloc[0]  # Most recent completed quarter
                    actual_eps = float(latest["Reported EPS"])
                    estimated_eps = float(latest["EPS Estimate"])
                    if estimated_eps != 0:
                        surprise = (actual_eps - estimated_eps) / abs(estimated_eps) * 100
                    else:
                        surprise = 0.0
                    # Also get the Surprise(%) column if available
                    if "Surprise(%)" in past.columns:
                        surprise_col = past.iloc[0].get("Surprise(%)")
                        if pd.notna(surprise_col):
                            surprise = float(surprise_col)
                    return EarningsContext(
                        surprise_percent=round(surprise, 2),
                        is_recent=True
                    )
        except Exception:
            pass
        return EarningsContext()
