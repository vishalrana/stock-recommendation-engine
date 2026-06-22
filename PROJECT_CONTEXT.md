\# Stock Recommendation Engine



\## Objective



Build a personal stock recommendation website inspired by Danelfin but significantly simpler.



The platform scans stocks, generates ranked recommendations, and eventually displays:



\* Company

\* Past Win Rate

\* Performance Forecast

\* Industry

\* Entry Price

\* Exit Price

\* Stop Loss

\* Upside Potential

\* Risk/Reward

\* Holding Time



\---



\## Market



United States



Universe:



\* S\&P 500 stocks



Data Source:



\* Yahoo Finance (yfinance)



Cost Target:



\* Zero or near-zero operating cost



\---



\## Architecture



\### Backend



Python



Modules:



\* downloader.py

\* indicators.py

\* scanner.py

\* patterns.py

\* risk.py

\* backtester.py

\* cache.py



\### Future



\* FastAPI

\* Supabase

\* Next.js

\* Vercel



\---



\## Current Research Status



\### Phase 1



Completed



Trend Filter



Requirements:



\* Close > 50 DMA

\* 50 DMA > 200 DMA



\---



\### Phase 2



Completed



RSI Logic



Requirements:



\* RSI Pullback detected

\* Current RSI Recovery



RSI implementation uses Wilder RSI(14).



\---



\### Phase 3



Completed



Risk Engine



Trade Construction:



\* Entry

\* Stop

\* Target



Target Method:



3R



Formula:



Exit = Entry + (Risk × 3)



\---



\### Phase 4



Completed



Historical Backtesting



Rules:



\* Entry active T+1

\* Signal expires after 5 days

\* Stop-first collision rule

\* 3R target



Metrics:



\* Signals

\* Wins

\* Losses

\* Win Rate

\* Holding Time

\* Expectancy



\---



\## Data Cache



Location:



data/cache/



Format:



Parquet



Rules:



\* Reuse cache if age < 24h

\* Avoid redownloads

\* Use cache for all research



\---



\## Research Principle



Do not modify production strategy without experimental evidence.



All strategy changes must be validated using historical backtests.



\---



\## Current Candidate Strategy



Version 1.1



Filters:



\* Price > 50 DMA > 200 DMA

\* RSI Pullback

\* Current RSI Recovery

\* Volume > 1.0x

\* No Pattern Filter

\* 3R Target



Status:



Research Candidate



Not yet promoted to Production Strategy.



