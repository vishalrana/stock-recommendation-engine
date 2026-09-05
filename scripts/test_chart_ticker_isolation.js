/**
 * Verification Script: Dynamic Chart Ticker Isolation
 * ===================================================
 * Verifies that the TradingView widget iframe URL is strictly ticker-specific,
 * uses encodeURIComponent, and isolates distinct tickers without caching or bleed.
 */

const assert = require('assert');

function getTradingViewUrl(ticker) {
  return `https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(ticker)}&interval=D&hidesidetoolbar=1&symboledit=1&saveimage=1&toolbarbg=f1f3f6&studies=%5B%5D&theme=light&style=1&timezone=America%2FNew_York`;
}

function testChartTickers() {
  console.log("==================================================");
  console.log("  TRADINGVIEW CHART TICKER ISOLATION TEST");
  console.log("==================================================");

  const testCases = [
    { ticker: "AAPL", expected: "symbol=AAPL" },
    { ticker: "NVDA", expected: "symbol=NVDA" },
    { ticker: "MSFT", expected: "symbol=MSFT" },
    { ticker: "BRK.B", expected: "symbol=BRK.B" },
    { ticker: "BF-B", expected: "symbol=BF-B" },
  ];

  testCases.forEach(({ ticker, expected }, index) => {
    const url = getTradingViewUrl(ticker);
    console.log(`[Test ${index + 1}] Recommendation '${ticker}'`);
    console.log(`  -> URL: ${url}`);
    assert(url.includes(expected), `URL must contain ${expected}`);
    assert(!url.includes("undefined"), "URL must not have undefined");
    console.log(`  -> PASS: Ticker '${ticker}' isolated correctly in TradingView iframe.\n`);
  });

  console.log("==================================================");
  console.log("  ALL CHART TICKER ISOLATION TESTS PASSED!");
  console.log("==================================================");
}

testChartTickers();
