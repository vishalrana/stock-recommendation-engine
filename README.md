# Stock Recommendation Engine

A personal-use, mobile-first quantitative stock research and recommendation engine designed to identify high-probability swing and trend setups across US equities.

## Core Philosophy & Design Principles

- **Simple Stock Ideas First**: Clear, clean stock cards with essential information (Ticker, Company Name, Current Price, Days Active, Indicative Targets & Stop Loss).
- **Deep Analysis on Demand**: Expanding an idea reveals the interactive TradingView chart, "Why this idea?" rationale, and analytical indicators.
- **Strictly a Recommendation Engine**:
  - **NOT** an automated trading system or portfolio manager.
  - **NO** capital allocation, position sizing, or Kelly betting in recommendation cards.
  - **NO** brokerage execution or automated order placement.
  - **ZERO** entry-price exposure on active recommendation cards.
- **Indicative Exits & Analytical R:R**:
  - Targets (T1, T2, T3) and Stop Losses are indicative suggestions calculated for swing planning.
  - Risk/Reward (R:R) and Reach Probabilities are analytical evaluation outputs; they do not disqualify an otherwise qualified stock.

---

## Strategy Catalog

The recommendation engine scans across six primary strategies and one conditional regime strategy:

1. **Pullback Recovery**: Identifies strong uptrending stocks experiencing short-term RSI pullbacks to dynamic support (50 DMA / 20 EMA).
2. **Post-Earnings Drift (PEAD)**: Captures sustained institutional momentum following significant positive earnings surprises and volume surges.
3. **Trend Following**: Classic moving average alignment (price > 50 DMA > 200 DMA) capturing sustained intermediate-term trends.
4. **Sector Rotation**: Identifies top-performing sector ETFs and leading equities exhibiting relative strength vs. SPY.
5. **52-Week High Breakout**: Identifies consolidation near annual highs with above-average volume expansion.
6. **Cross-Sectional Momentum**: Ranks universe by multi-month relative momentum to select market leaders.
7. **Mean Reversion (Conditional)**: Activated selectively during high-volatility or oversold regimes.

---

## Application Structure (UI Tabs)

The web application (`frontend/`) is built with Next.js 14, React, Tailwind CSS, and shadcn/ui:

1. **Ideas (Active Recommendations)**
   - Stock cards with live quotes, indicative stops, and targets (T1/T2/T3 shown only when genuinely available).
   - "Why this idea?" strategy explanation and TradingView interactive technical chart.
   - Server-confirmed manual removal with reason logging.
   - Controls: **Refresh Prices** and **Refresh Current Ideas**.

2. **Closed Ideas**
   - Authoritative historical record sourced exclusively from `signals_history`.
   - Displays outcome (`reached_t1`, `reached_t2`, `reached_t3`, `stopped`, `invalidated`, `manually_removed`), realized return percentage, holding days, and closure reason.

3. **Scan History**
   - Audit trail of historical engine runs from `scan_log`.
   - Summary statistics (tickers scanned, setups qualified, regime, duration) with expandable breakdown of gate rejections.

---

## Three Distinct Refresh Concepts

To balance low latency, minimal server resources, and analytical rigor, three separate refresh paths exist:

| Concept | Trigger | Execution Environment | Behavior |
| :--- | :--- | :--- | :--- |
| **Refresh Prices** | User button | Vercel Server Action (`frontend/src/app/actions.ts`) | Fetches real-time market prices from Tiingo (fallback: Yahoo Finance). Updates displayed quote only. No engine run, no calculations, no database mutations. |
| **Refresh Current Ideas** | User button | GitHub Actions (`.github/workflows/refresh_current_ideas.yml`) via Vercel dispatch | Dispatches targeted engine run `python jobs/generate_signals.py --tickers <current tickers>`. Runs full strategy evaluation on currently active ideas only. Reconciles lifecycle, updates targets/analytics in Supabase, and invalidates broken ideas. Protected against duplicate dispatches. |
| **Full Market Scan** | Cron / Manual | GitHub Actions / Cloud Runner | Executes full-market scan across the entire equity universe, archives prior ideas, updates `signals_history`, and logs to `scan_log`. |

---

## Technical Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Next.js Frontend                   │
│          (Vercel Production - Node.js Serverless)       │
│                                                         │
│  [Refresh Prices]             [Refresh Current Ideas]   │
│         │                                │              │
│         ▼                                ▼              │
│   Live Quote API               GitHub Actions API       │
│  (Tiingo / Yahoo)             (workflow_dispatch)       │
└──────────────────────────────────────────┬──────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────┐
│                   GitHub Actions Runner                 │
│                      (Python 3.11)                      │
│                                                         │
│   python jobs/generate_signals.py --tickers AAPL MSFT   │
│   - Targeted strategy evaluation                        │
│   - Pure score-based tiering (>=80 Strong Buy, >=65 Buy)│
│   - Analytical R:R and reach probabilities              │
│   - Lifecycle reconciliation                            │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                    Supabase Database                    │
│                                                         │
│   - signals (active & pending recommendations)          │
│   - signals_history (authoritative immutable history)   │
│   - scan_log (execution telemetry & gate audits)        │
└─────────────────────────────────────────────────────────┘
```

---

## Environment Variables

### Vercel Production Environment
Configure these in the Vercel Project Settings:

| Variable | Description |
| :--- | :--- |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project API URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase public anonymous key (read-only queries) |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service key (for secure server actions like manual removal) |
| `TIINGO_API_KEY` | Tiingo API token for high-accuracy live quotes |
| `GITHUB_TOKEN` | Personal Access Token with `repo` / `actions:write` scope for workflow dispatch |
| `GITHUB_OWNER` | Repository owner (`vishalrana`) |
| `GITHUB_REPO` | Repository name (`stock-recommendation-engine`) |

### GitHub Actions Environment
Configure these in GitHub Repository Secrets:

| Secret | Description |
| :--- | :--- |
| `SUPABASE_URL` | Supabase project API URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key for database writes |
| `TIINGO_API_KEY` | Tiingo API key for market data fetching |

---

## Testing & Quality Assurance

Run the comprehensive production audit test suite:

```bash
# Production audit & hardening regression tests
python scripts/test_production_audit.py

# Recommendation lifecycle & safeguard tests
python scripts/test_recommendation_lifecycle.py

# Tier logic and scoring tests
python scripts/test_position_sizer_and_tier.py

# Frontend TypeScript check
npx tsc -p frontend/tsconfig.json --noEmit

# Frontend production build
cd frontend && npm run build
```

---

## License

Personal and proprietary use.
