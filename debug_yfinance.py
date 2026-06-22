import yfinance as yf
import pandas as pd

data = yf.download("AAPL", start="2025-06-21", end="2026-06-21", progress=False)
print("Column names:", data.columns.tolist())
print("Column names type:", type(data.columns))
print("Is MultiIndex:", isinstance(data.columns, pd.MultiIndex))
print("\nFirst few rows:")
print(data.head())
