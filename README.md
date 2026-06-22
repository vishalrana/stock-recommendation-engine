# Stock Recommendation Engine - Phase 3

A quantitative stock scanner that identifies S&P 500 stocks matching technical criteria, detects price-action patterns, and constructs trades with risk management metrics.

## Project Overview

### Phase 2 Scope
- **Signal Scanner**: Identifies stocks matching Layer 1 filter criteria
- **Pattern Detection**: Bullish Engulfing, Hammer, Inside Bar
- **Data Source**: yfinance (free, no API keys required)
- **Universe**: S&P 500 stocks
- **Output**: CSV file with qualified signals and entry prices

### What It Does NOT Do (Yet)
- Pattern detection (Phase 2)
- Backtesting (Phase 3)
- Database storage (Phase 4)
- API/Web interface (Phase 5)

---

## Project Structure

```
stock-recommendation-engine/
├── src/
│   ├── __init__.py           (empty - makes src a package)
│   ├── config.py             (configuration & constants)
│   ├── downloader.py         (fetch S&P 500 tickers & OHLCV data)
│   ├── indicators.py         (calculate DMA, RSI, volume)
│   ├── patterns.py           (detect bullish patterns and entry price)
│   ├── risk.py               (construct stop loss, exit price, upside, and risk reward)
│   ├── scanner.py            (apply Layer 1 filters and pattern checks)
│   └── main.py               (pipeline orchestration)
├── data/                      (folder for caching, created automatically)
├── outputs/                   (folder for CSV results, created automatically)
├── requirements.txt           (Python dependencies)
└── README.md                  (this file)
```

---

## File Descriptions

### 1. **config.py** - Configuration & Constants
**Purpose**: Centralize all settings in one place for easy modifications.

**Key Settings**:
- `TEST_MODE = True` → Uses 10 hardcoded tickers (fast testing)
- `TEST_MODE = False` → Uses full S&P 500 from Wikipedia
- Indicator parameters: 50 DMA, 200 DMA, RSI(14), 20-day volume
- Filter thresholds: Price relationship, RSI ranges, volume multiplier
- Date range: Last 250 trading days of OHLCV data

**When to Modify**:
- Toggle `TEST_MODE` between True/False
- Change filter thresholds (e.g., RSI_CURRENT_MIN, VOLUME_MULTIPLIER)
- Adjust lookback period (LOOKBACK_DAYS)

---

### 2. **downloader.py** - Data Acquisition
**Purpose**: Fetch S&P 500 tickers and OHLCV data with error handling.

**Functions**:
- `fetch_sp500_tickers()` → Returns list of ticker symbols
  - If TEST_MODE: Returns hardcoded list
  - If False: Scrapes Wikipedia for real S&P 500 list
  
- `fetch_ohlcv_data(ticker)` → Downloads historical price/volume for one stock
  - Returns None on failure (no exception raised)
  - Allows batch processing to continue even if some tickers fail
  
- `fetch_batch_ohlcv(tickers)` → Downloads data for multiple tickers
  - Returns dict mapping ticker → DataFrame (or None if failed)
  - Logs progress for all tickers

**Error Handling**:
- Network failures are caught and logged, not raised
- Returns None for failed tickers, allowing others to continue
- Timeout after 30 seconds per request

---

### 3. **indicators.py** - Technical Indicators
**Purpose**: Calculate moving averages, RSI, and volume statistics.

**Calculations**:
- **DMA (50 & 200)**: Simple Moving Average
  - Formula: Average of last N closing prices
  - Used for trend identification
  
- **RSI(14)**: Relative Strength Index
  - Formula: RSI = 100 - (100 / (1 + RS))
  - RS = Average Gains / Average Losses
  - Range: 0-100 (0=oversold, 100=overbought)
  
- **Volume MA (20)**: 20-day average trading volume
  - Used to filter for abnormal volume activity

**Functions**:
- `calculate_indicators(df)` → Adds all indicators to OHLCV DataFrame
- `get_indicator_values(df, ticker)` → Extracts latest values and validation
  - Returns None if data has NaN or invalid values
  - Prevents garbage-in-garbage-out

---

### 4. **scanner.py** - Layer 1 Filter Logic
**Purpose**: Apply trading rules to identify qualified stocks.

**Layer 1 Filter Criteria** (ALL must be met):

| Condition | Requirement | Purpose |
|-----------|-------------|---------|
| **Price Relationship** | Price > 50 DMA > 200 DMA | Uptrend confirmation |
| **RSI Min (10 days)** | Min RSI last 10 days < 45 | Oversold condition occurred |
| **RSI Current** | 45 < Current RSI < 65 | Recovery from oversold, not overbought |
| **Volume** | Today's Vol > 1.5 × 20-day Avg | Institutional interest |

**Classes**:
- `SignalQualifier`: Static methods for each filter check
  - `passes_price_filter()` → Check trend
  - `passes_rsi_min_filter()` → Check if stock was oversold
  - `passes_rsi_current_filter()` → Check if recovering
  - `passes_volume_filter()` → Check volume spike
  - `check_all_filters()` → Run all checks, return results

**Output**:
```python
{
    "ticker": "AAPL",
    "date": "2024-01-15",
    "price": 180.25,
    "dma_50": 175.30,
    "dma_200": 170.15,
    "rsi_14": 55.2,
    "current_rsi": 55.2,
    "min_rsi_10d": 42.1,
    "volume_20d_avg": 52000000,
    "volume_current": 80000000,
    "volume_ratio": 1.54,
    "pattern": "Bullish Engulfing",
    "entry_price": 181.47
}
```

---

### 5. **patterns.py** - Pattern Detection
**Purpose**: Recognize price-action patterns and calculate entry price.

**Patterns Detected**:
- `Bullish Engulfing`
- `Hammer` with quality filters
- `Inside Bar`

**Entry Price Rules**:
- `Bullish Engulfing`: entry at the latest closing price
- `Hammer`: entry just above the hammer high
- `Inside Bar`: entry just above the inside bar high

**Functions**:
- `detect_pattern(df)` → Returns the detected pattern and entry price
- `is_bullish_engulfing(latest, prior)` → Checks two-candle engulfing structure
- `is_hammer(latest)` → Checks a bullish hammer with a long lower wick
- `is_inside_bar(latest, prior)` → Checks whether the latest candle is inside the prior range

---

### 6. **risk.py** - Trade Construction
**Purpose**: Build post-signal trade metrics and apply risk/reward filtering.

**Functions**:
- `find_swing_low(df)` → Finds the most recent swing low in the last 20 days.
- `find_resistance_zone(df)` → Finds the nearest overhead resistance zone in the last 126 days.
- `calculate_upside_pct(entry_price, exit_price)` → Computes upside in percent.
- `calculate_risk_reward(entry_price, stop_loss, exit_price)` → Computes the reward/risk ratio.
- `construct_trade(signal, df)` → Enriches a qualified signal with stop loss, exit price, upside, and risk reward. Returns `None` for invalid or sub-threshold trades.

---

### 7. **main.py** - Pipeline Orchestration
**Purpose**: Coordinate the complete scanning workflow.

**Pipeline Steps**:
1. Load configuration
2. Fetch S&P 500 tickers
3. Download OHLCV data
4. Calculate indicators
5. Scan for signals
6. Export to CSV

**Error Handling**:
- Graceful handling of download failures (doesn't stop entire scan)
- Detailed logging at each step
- Returns success/failure status

**Output**:
```
✓ QUALIFIED: AAPL | Price: $180.25 | RSI: 55.2 | Vol Ratio: 1.54x
✓ QUALIFIED: NVDA | Price: $650.80 | RSI: 52.1 | Vol Ratio: 1.89x
```

---

## Setup Instructions

### Step 1: Create Virtual Environment
```bash
cd C:\Users\acer\Documents\stock-recommendation-engine
python -m venv venv
venv\Scripts\activate
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

**What Gets Installed**:
- `yfinance` - Download stock data
- `pandas` - Data manipulation
- `numpy` - Numerical calculations
- `requests` & `beautifulsoup4` - Web scraping (S&P 500 list)

---

## Running the Scanner

### Option 1: Run in Test Mode (Recommended First)
```powershell
cd C:\Users\acer\Documents\stock-recommendation-engine
python src\main.py
```

### Option 2: PowerShell with activation
```powershell
cd C:\Users\acer\Documents\stock-recommendation-engine
.\venv\Scripts\Activate.ps1
python src\main.py
```

### Option 3: The simplest way to run the scanner
```powershell
run.bat
```

**What Happens**:
- Uses 10 hardcoded tickers (AAPL, MSFT, NVDA, etc.)
- Completes in ~20-30 seconds in TEST_MODE
- Builds a CSV with pattern and risk metrics

**Expected Output**:
```
============================================================
PHASE 3: TRADE CONSTRUCTION ENGINE
============================================================
Step 1: Loading configuration...
  test_mode: True
  tickers_count: 10
  ...

Step 2: Fetching ticker list...
✓ Using TEST_MODE with 10 tickers

Step 3: Downloading OHLCV data for 10 tickers...
[1/10] Fetching AAPL...
[2/10] Fetching MSFT...
...

Step 4: Calculating technical indicators...
✓ Indicator calculation complete

Step 5: Scanning for Layer 1 signals...
============================================================
LAYER 1 SIGNAL SCAN
============================================================
✓ QUALIFIED: AAPL | Price: $180.25 | RSI: 55.2 | Vol Ratio: 1.54x
Scan Complete: 10 processed, 3 qualified
============================================================

Step 6: Exporting results...
✓ Exported 3 signals to: C:\Users\acer\Documents\stock-recommendation-engine\outputs\signals.csv

============================================================
RESULTS SUMMARY
============================================================

Qualified Signals: 3

    ticker        date   price  dma_50  dma_200  rsi_14  ...
0     AAPL  2024-01-15  180.25  175.30  170.15   55.20  ...
1     NVDA  2024-01-15  650.80  640.10  620.15   52.10  ...
2     MSFT  2024-01-15  380.15  375.20  360.50   48.90  ...

============================================================
PIPELINE COMPLETE - SUCCESS
============================================================
```

### Option 2: Run with Full S&P 500
**Before running**, modify `src/config.py`:
```python
TEST_MODE = False  # Change from True to False
```

Then run:
```bash
python src\main.py
```

**What Happens**:
- Fetches all S&P 500 tickers from Wikipedia
- Downloads data for ~500 stocks (~5-10 minutes)
- Scans all for signals

---

## Output Files

### signals.csv
**Location**: `outputs/signals.csv`

**Columns**:
- `ticker` - Stock symbol (e.g., AAPL)
- `date` - Scan date
- `price` - Current closing price
- `dma_50` - 50-day moving average
- `dma_200` - 200-day moving average
- `rsi_14` - Current RSI(14)
- `current_rsi` - Same as rsi_14 (redundant but useful for clarity)
- `min_rsi_10d` - Lowest RSI from last 10 days
- `volume_20d_avg` - 20-day average volume
- `volume_current` - Today's volume
- `volume_ratio` - Today's vol / 20-day avg (should be > 1.5)
- `pattern` - Detected price-action pattern
- `entry_price` - Calculated entry level for the pattern

**Example Data**:
```
ticker,date,price,dma_50,dma_200,rsi_14,current_rsi,min_rsi_10d,volume_20d_avg,volume_current,volume_ratio,pattern,entry_price
AAPL,2026-06-18,298.01,288.63,267.79,48.39,48.39,28.37,52709745,85962200,1.63,Inside Bar,300.87
```

---

## Understanding the Filters

### Why These Filters?

**Price > 50 DMA > 200 DMA**
- Confirms uptrend
- Eliminates downtrending stocks
- Golden cross opportunity (50 DMA crossing 200 DMA from below)

**Min RSI last 10 days < 45**
- Stock must have been oversold recently
- Creates pullback/bounce opportunity

**Current RSI between 45 and 65**
- Not oversold anymore (< 45 would be too late to enter)
- Not overbought yet (> 65 would be extended rally)
- Sweet spot for entry

**Volume > 1.5 × 20-day Average**
- Confirms institutional participation
- Filters out low-liquidity penny stocks
- 1.5x spike shows renewed interest

### Real-World Example
```
Stock: AAPL
- Price: $180.25 ✓ (above both moving averages)
- 50 DMA: $175.30 ✓ (above 200 DMA)
- 200 DMA: $170.15 ✓ (supports uptrend)
- Min RSI 10d: $42.10 ✓ (was oversold)
- Current RSI: $55.20 ✓ (recovered, not overbought)
- Volume: 80M vs 52M avg ✓ (1.54x multiplier)

→ QUALIFIED for further analysis
```

---

## Troubleshooting

### "No data returned" for a ticker
- Likely delisted or invalid ticker
- yfinance returns None → stock is skipped
- Not an error, just logging and continuation

### ImportError: No module named 'yfinance'
```bash
# Fix: Reinstall requirements
pip install -r requirements.txt --upgrade
```

### No signals found
- Market conditions may not match filter criteria
- All stocks were above 200 DMA but didn't have RSI dip
- Try adjusting TEST_MODE or waiting for different market conditions

### Connection timeout
- Network issue with yfinance servers
- Re-run the script (temporary issue usually)
- Check internet connection

---

## Performance Notes

**Test Mode (10 tickers)**:
- Download: ~10 seconds
- Indicators: ~1 second
- Scanning: <1 second
- **Total: ~15-30 seconds**

**Full S&P 500 (~500 tickers)**:
- Download: ~5-10 minutes (yfinance rate limiting)
- Indicators: ~10 seconds
- Scanning: ~5 seconds
- **Total: ~6-11 minutes**

---

## Code Quality & Architecture

### Principles Applied

1. **Single Responsibility**
   - `config.py` → Configuration only
   - `downloader.py` → Download only
   - `indicators.py` → Calculation only
   - `scanner.py` → Filtering only
   - `main.py` → Orchestration only

2. **Production-Ready**
   - Comprehensive error handling
   - Detailed logging
   - Type hints
   - Docstrings for all functions
   - Input validation

3. **Maintainability**
   - No magic numbers (all in config.py)
   - Clear variable names
   - Modular imports (no circular dependencies)
   - Easy to modify filters or add new indicators

4. **Robustness**
   - Graceful degradation (one failed ticker doesn't stop scan)
   - NaN/inf checking in indicators
   - Timeout handling for network requests
   - Detailed logging for debugging

---

## What's Next? (Phase 2+)

After Phase 1 works:
1. **Phase 2**: Pattern detection (higher lows, support/resistance)
2. **Phase 3**: Backtesting framework
3. **Phase 4**: Database storage (Supabase)
4. **Phase 5**: FastAPI backend
5. **Phase 6**: Next.js frontend

---

## Questions & Support

- **Empty results?** Check if market matches filter criteria
- **Errors in output?** Check internet connection and yfinance availability
- **Want to modify filters?** Edit `config.py` (no code changes needed)
- **Want to add indicators?** Add to `indicators.py` and `scanner.py`

---

**Created**: 2024
**Phase**: 1 (Signal Scanner)
**Status**: Production-Ready
