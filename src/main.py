"""
Main Module
===========
Purpose: Orchestrate the complete Phase 1 signal scanner pipeline.

Pipeline:
1. Fetch S&P 500 tickers (or test tickers)
2. Download OHLCV data from yfinance
3. Calculate technical indicators
4. Apply Layer 1 filters
5. Export results to CSV

Single Responsibility: Pipeline orchestration and top-level error handling.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# Import all modules
from config import (
    TEST_MODE,
    TEST_TICKERS,
    OUTPUT_CSV,
    DEBUG_CSV,
    CSV_COLUMNS,
    MIN_DATA_POINTS,
    get_config_summary,
)
from downloader import fetch_sp500_tickers, fetch_batch_ohlcv
from indicators import calculate_indicators
from risk import construct_trade, evaluate_trade
from scanner import scan_signals, signals_to_dataframe

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def main():
    """Main pipeline orchestrator."""
    
    logger.info("=" * 60)
    logger.info("PHASE 3: TRADE CONSTRUCTION ENGINE")
    logger.info("=" * 60)
    
    # ==================== STEP 1: CONFIGURATION ====================
    logger.info("Step 1: Loading configuration...")
    config = get_config_summary()
    for key, value in config.items():
        logger.info(f"  {key}: {value}")
    
    # ==================== STEP 2: FETCH TICKERS ====================
    logger.info("\nStep 2: Fetching ticker list...")
    try:
        if TEST_MODE:
            tickers = TEST_TICKERS
            logger.info(f"[OK] Using TEST_MODE with {len(tickers)} tickers")
        else:
            tickers = fetch_sp500_tickers()
            logger.info(f"[OK] Fetched {len(tickers)} tickers from Wikipedia")
    except Exception as e:
        logger.error(f"[FAILED] Failed to fetch tickers: {str(e)}")
        return False
    
    # ==================== STEP 3: DOWNLOAD DATA ====================
    logger.info(f"\nStep 3: Downloading OHLCV data for {len(tickers)} tickers...")
    try:
        data_dict = fetch_batch_ohlcv(tickers)
        
        # Count successful downloads
        successful_downloads = sum(1 for v in data_dict.values() if v is not None)
        failed_downloads = len(tickers) - successful_downloads
        
        logger.info(f"[OK] Downloaded {successful_downloads} tickers")
        if failed_downloads > 0:
            logger.warning(f"  {failed_downloads} tickers failed to download")
            
    except Exception as e:
        logger.error(f"[FAILED] Download failed: {str(e)}")
        return False
    
    # ==================== STEP 4: CALCULATE INDICATORS ====================
    logger.info(f"\nStep 4: Calculating technical indicators...")
    
    indicators_dict = {}
    
    for ticker, df in data_dict.items():
        if df is None or df.empty:
            indicators_dict[ticker] = None
            continue
        
        try:
            # Check if we have enough data points
            if len(df) < MIN_DATA_POINTS:
                logger.debug(f"{ticker}: Insufficient data ({len(df)} < {MIN_DATA_POINTS} required)")
                indicators_dict[ticker] = None
                continue
            
            # Calculate indicators
            df_with_indicators = calculate_indicators(df)
            indicators_dict[ticker] = df_with_indicators
            
        except Exception as e:
            logger.warning(f"{ticker}: Indicator calculation failed - {str(e)}")
            indicators_dict[ticker] = None
    
    logger.info("[OK] Indicator calculation complete")
    
    # ==================== STEP 5: SCAN FOR SIGNALS ====================
    logger.info(f"\nStep 5: Scanning for Layer 1 signals...")
    
    try:
        signals, processed_count, qualified_count = scan_signals(indicators_dict, tickers)
        
    except Exception as e:
        logger.error(f"[FAILED] Signal scan failed: {str(e)}")
        return False
    
    # ==================== STEP 5B: APPLY RISK FILTER ====================
    logger.info(f"\nStep 5B: Constructing trades and filtering by risk reward...")
    filtered_signals = []
    debug_rows = []
    skipped_risk = 0
    
    for signal in signals:
        df = indicators_dict.get(signal["ticker"])
        if df is None or df.empty:
            logger.warning(f"{signal['ticker']}: Missing OHLCV data for risk construction")
            skipped_risk += 1
            debug_rows.append({
                "ticker": signal["ticker"],
                "pattern": signal.get("pattern"),
                "entry_price": signal.get("entry_price"),
                "stop_loss": None,
                "exit_price": None,
                "upside_pct": None,
                "upside_valid": "FALSE",
                "risk_reward": None,
                "resistance_type": None,
                "candidate_resistance_levels": None,
                "removal_reason": "INVALID_ENTRY",
            })
            continue

        trade, debug_info = evaluate_trade(signal, df)
        debug_rows.append(debug_info)
 
        logger.info(
            f"\n{signal['ticker']}\n"
            f"Pattern: {signal.get('pattern')}\n"
            f"Entry: {signal.get('entry_price'):.2f}\n"
            f"Swing Low: {'Yes' if debug_info['stop_loss'] is not None else 'No'}\n"
            f"Swing Low Value: {debug_info['stop_loss'] if debug_info['stop_loss'] is not None else 'N/A'}\n"
            f"Exit: {debug_info['exit_price'] if debug_info['exit_price'] is not None else 'N/A'}\n"
            f"Target R Multiple: {debug_info.get('target_r_multiple')}\n"
            f"Upside: {debug_info['upside_pct'] if debug_info['upside_pct'] is not None else 0.0}%\n"
            f"Risk Reward: {debug_info['risk_reward'] if debug_info['risk_reward'] is not None else 0.0}\n"
            f"Removed: {debug_info['removal_reason']}"
        )

        if trade is None:
            skipped_risk += 1
            continue

        filtered_signals.append(trade)

    # Export debug file for all candidates before filtering
    try:
        debug_columns = [
            "ticker",
            "pattern",
            "entry_price",
            "stop_loss",
            "exit_price",
            "upside_pct",
            "upside_valid",
            "risk_reward",
            "resistance_type",
            "candidate_resistance_levels",
            "removal_reason",
        ]
        DEBUG_CSV.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(debug_rows, columns=debug_columns).to_csv(DEBUG_CSV, index=False)
        logger.info(f"[OK] Exported debug results to: {DEBUG_CSV}")
    except Exception as e:
        logger.warning(f"Failed to write debug CSV: {e}")

    logger.info(f"[OK] {len(filtered_signals)} trades remain after risk filter")
    logger.info(f"[INFO] {skipped_risk} candidates removed during risk construction")
    
    # Update final signals list for export
    signals = filtered_signals
    qualified_count = len(signals)
    
    # ==================== STEP 6: EXPORT RESULTS ====================
    logger.info(f"\nStep 6: Exporting results...")
    
    try:
        if signals:
            # Convert to DataFrame
            df_signals = signals_to_dataframe(signals)
            
            # Ensure columns are in correct order
            df_signals = df_signals[CSV_COLUMNS]
            
            # Export to CSV
            OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
            df_signals.to_csv(OUTPUT_CSV, index=False)
            
            logger.info(f"[OK] Exported {len(signals)} signals to: {OUTPUT_CSV}")
            
            # Display summary
            logger.info("\n" + "=" * 60)
            logger.info("RESULTS SUMMARY")
            logger.info("=" * 60)
            logger.info(f"\nQualified Signals: {qualified_count}")
            logger.info(f"\n{df_signals.to_string()}")
            
        else:
            logger.warning("[NO_SIGNALS] No signals found matching risk/reward criteria")
            OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(columns=CSV_COLUMNS).to_csv(OUTPUT_CSV, index=False)
            logger.info(f"0 signals exported to: {OUTPUT_CSV}")
            
    except Exception as e:
        logger.error(f"[FAILED] Export failed: {str(e)}")
        return False
    
    # ==================== COMPLETION ====================
    logger.info("\n" + "=" * 60)
    logger.info("PIPELINE COMPLETE - SUCCESS")
    logger.info("=" * 60)
    
    return True


if __name__ == "__main__":
    """Entry point for the signal scanner."""
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nPipeline interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        sys.exit(1)
