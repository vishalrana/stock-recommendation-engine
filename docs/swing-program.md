# Swing signals program (pre-registered 2026-10-11)

Moved verbatim from CLAUDE.md on 2026-10-14; CLAUDE.md holds the binding rules and links here.

### 1. DEFINITIONS (exact; backtest and live code use the same functions)
Calendar and timing
- Session = NYSE trading session (SessionCalendar / trading_sessions_between). t = signal session.
- A decision at t uses daily bars through the close of t and SEC filings with acceptance time <= 20:00 ET on t. Filings accepted later belong to t+1.
- Fill = open of session t+1. The fill session is holding session 1.
- Resting exits (stop, target) are GTC orders placed at the fill, checked on each holding session in this order:
  - stop: if open <= stop, exit at the open; else if low <= stop, exit at the stop;
  - target: if open >= target, exit at the open; else if high >= target, exit at the target;
  - if stop and target are both touched on one bar: STOP_FIRST (as in fast_outcome).
- Rule exits (time limits, sleeve rule exits, next-earnings exit, event-lock exit) are DECIDED at a close s and EXECUTED at the open of s+1.
- Report-only sensitivity "close-checked stops": stops are evaluated on closes and executed at the next open.
Prices and indicators
- Download with yfinance auto_adjust=False, actions=True. Yahoo's Open/High/Low/Close are split-adjusted; Adj Close also adjusts for dividends.
- Total-return OHLC = OHLC x (Adj Close / Close). Use it for indicators, returns and backtest fills.
- Unadjusted close(t) = Close(t) x the product of split ratios effective after t. Use it ONLY for the $5 price filter.
- Dollar volume = Close x Volume (invariant to split adjustment).
- Test against known splits (NVDA 2024 10:1, AAPL 2020 4:1).
- Live levels are quoted in actual prices on the signal date: the latest bar's Close equals the traded price.
- MA_n(t) = mean total-return close over sessions t-n+1..t. "Rising 200-day MA" = MA200(t) > MA200(t-20).
- ATR14 = Wilder ATR(14) (src/patterns.wilder_atr). RSI2 = Wilder RSI with period 2. SMA5 = MA_5.
- ADV20(t) = mean dollar volume over t-19..t.
- Liquid(t): unadjusted close >= $5, ADV20 >= $5,000,000 nominal, and >= 252 sessions of history. Give liquid_mask an optional raw-close argument; keep its old default behaviour unchanged.
- C6pct(t) = percentile (0,1] of composite_c6 among the names of the evaluated universe that are Liquid on t. Rank within U1 for U1 tests and live, and within U2 for U2 tests. Inputs exactly as compute_features/composite_c6: the equal-weight ranks of MOM12_1, RESMOM, HI52, -FIP, -REV21 and -RVOL252, with >= 4 of the 6 present.
- beta(t) = BETA252 from compute_features at t.
- MAD(t) = MA21(t) / MA200(t) (report-only).
Universes
- U1 (broad): today's security master (production_universe), restricted to Liquid(t). It is survivorship-biased: delisted names are missing.
- U2 (point-in-time S&P 500): symbols that are S&P 500 members on t per data/reference/sp500_ticker_start_end.csv (mapped with yahoo_symbol), have a bar on t, and are Liquid(t).
  - Measure and report the member-days with no bar, by year.
  - Flag symbols whose first yfinance bar comes after their membership start (likely recycled or renamed) and exclude member-days before that first bar.
- U1-top500(t): the 500 U1 names with the highest ADV20 on t.
Events
- Earnings reaction sessions = earnings_events() from SEC 8-K item 2.02 (reaction_session of the acceptance time).
- "Earnings within 5 sessions" at t: a reaction session falls in t+1..t+5.
  - Backtest: SEC reaction sessions, assumed known 5 sessions ahead (a listed limitation).
  - Live: confirmed dates from the earnings_calendar table; else estimate_next_earnings() from SEC history; if the date is unknown, issue the signal and tag it earnings_unknown.
- Event-locked(t): event_lock(filings accepted by the cutoff, as_of=t) is not None, OR price_pin(total-return close) is true on t.
- NEG8K = 8-K items {1.03, 2.04, 2.06, 3.01, 3.02, 4.01, 4.02}. Verified meanings per SEC Form 8-K instructions:
  - Item 1.03: Bankruptcy or Receivership
  - Item 2.04: Triggering Events That Accelerate or Increase a Direct Financial Obligation or an Obligation under an Off-Balance Sheet Arrangement (Default / Acceleration)
  - Item 2.06: Material Impairments
  - Item 3.01: Notice of Delisting or Failure to Satisfy a Continued Listing Rule or Standard; Transfer of Listing
  - Item 3.02: Unregistered Sales of Equity Securities (Dilution)
  - Item 4.01: Changes in Registrant's Certifying Accountant (Auditor change / resignation)
  - Item 4.02: Non-Reliance on Previously Issued Financial Statements or a Related Audit Report or Completed Interim Review (Restatement)
Returns
- net_pct = (exit - fill) / fill x 100 - 2 x 0.10 (10 bps per side is the primary cost). Also report cost_scenarios() and the break-even cost per side.
- spy_pct = SPY return between the same execution points (SPY open on the fill session; SPY price on the exit's execution point).
- excess_pct = net_pct - spy_pct.
- beta_adj_pct = net_pct - beta(t) x spy_pct. This is the PRIMARY METRIC.
- Signal month = calendar month of t. CIs are signal-month block-bootstrap 95% intervals (10,000 resamples, fixed seed; existing machinery).
Book rules
- One open signal per (sleeve, ticker).
- Cooldown: no new signal for the same (sleeve, ticker) until 10 sessions after its exit execution.
- Combined live book: one open signal per ticker across all sleeves. If several sleeves fire for the same ticker on the same session, priority is E > I > N > P > T.
- Standalone sleeve evaluation (the PASS rules) ignores other sleeves.
- Equal unit per signal. The track record is the sum of per-signal returns in units: no compounding, no sizing.
Placebo (built on PlaceboEngine)
- For each signal, draw a random ticker from the same universe that is Liquid and not Event-locked on t and is not the signal's ticker. It fills on the same session.
- Its stop and target use the same ATR14 multiples, applied to the placebo's own ATR14 and close.
- Max hold = the sleeve's max hold. If the real signal ended on a non-price exit, the placebo exits on the same execution session.
- 200 runs with stable_seed(sleeve, universe, run).
- Report the book's percentile among the 200 placebo means, and the paired diff vs the placebo median with a block-bootstrap CI.
- Report-only "placebo-trend" for T: draws restricted to names with Close > MA200 and a rising MA200 on t.

### 2. SLEEVES (pre-registered; no parameter may change after results exist)
Common gates G0 for every BUY at t:
  - the ticker is in the evaluated universe and Liquid(t);
  - not Event-locked(t);
  - no open signal and no cooldown for (sleeve, ticker);
  - no earnings within 5 sessions.

T - Trend (the drafted Momentum Signals rule)
  BUY at the close of t if: G0; C6pct(t) >= 0.90; Close(t) > MA200(t); MA200(t) > MA200(t-20).
  Levels (C = Close(t), A = ATR14(t)): stop = C - 3A; target = C + 6A (2R).
  Exits:
    - stop or target (resting orders);
    - time: decided at the close of holding session 60;
    - event lock: decided at any close where Event-locked is true.
  Open T signals hold through earnings (WARN only).

E - Earnings reaction (EAR + abnormal volume)
  Event d0 = an 8-K item 2.02 reaction session. r = total-return close-to-close return. b = the stock's SECTOR_TO_ETF ETF, or SPY when the ETF has no bar.
  EAR = sum over sessions d0-1..d0+1 of (r_stock - r_b).
  AVOL = mean volume(d0-1..d0+1) / mean volume(d0-63..d0-8) - 1.
  Reference set: all events in the same universe whose reaction session falls in the 63 sessions ending at d0-1. At least 50 events are required; otherwise no signal.
  EARpct and AVOLpct = percentiles of EAR and AVOL within the reference set.
  BUY at the close of t = d0+1 if: G0; EAR > 0; EARpct >= 0.80; AVOLpct >= 0.80.
  Levels: stop = Close(t) - 3 x ATR14(t); no target.
  Exits:
    - stop (resting);
    - next earnings: decided at the close of (next reaction session d0' + 1);
    - time cap: decided at the close of holding session 75;
    - event lock.
  E holds through the next report by design; the card must say so.

N - Catalyst gap
  BUY at the close of the gap session t if all hold:
    - G0;
    - Open(t) / Close(t-1) - 1 >= 0.05;
    - Volume(t) >= 3 x median Volume(t-50..t-1);
    - Close(t) >= Open(t);
    - Close(t) - Low(t) >= 0.5 x (High(t) - Low(t));
    - at least one 8-K or 6-K accepted in (16:00 ET on t-1, 20:00 ET on t];
    - none of those 8-Ks carries a NEG8K item.
  Never enter at the gap-day open: the fill is Open(t+1).
  Levels: stop = Close(t) - 3 x ATR14(t-1) (the pre-gap ATR); no target.
  Exits:
    - stop (resting);
    - time: decided at the close of holding session 40;
    - event lock.

P - Large-cap pullback
  Universe: U1-top500 for U1 tests and live; all of U2 for U2 tests.
  BUY at the close of t if: G0; Close(t) > MA200(t); MA200(t) > MA200(t-20); RSI2(t) < 10; Close(t) < SMA5(t).
  Levels: stop = Close(t) - 3 x ATR14(t); no target.
  Exits:
    - stop (resting);
    - rule exit: decided at the first close s on or after the fill session with Close(s) > SMA5(s);
    - time: decided at the close of holding session 10;
    - event lock.

I - Insider cluster buying
  Data:
    - History: SEC Insider Transactions Data Sets (Forms 3/4/5, 2006Q1 onward; https://www.sec.gov/dera/data/form-345). Take table and field names from its readme.
    - Live: daily Form 4 XML from EDGAR for U1 issuers, parsed into the same schema.
  Qualifying purchase (all must hold):
    - Form 4 original (not /A), non-derivative table;
    - transaction code P, acquired (A);
    - the reporting owner is a director or officer;
    - shares x price >= $10,000;
    - AFF10B5ONE is not true, where the field is present;
    - not routine. Routine = the same reporting owner made a code-P purchase in the same calendar month in each of the three prior calendar years.
  Cluster completed on filing date D (all must hold):
    - >= 2 distinct reporting owners made qualifying purchases in the same issuer;
    - their filing dates fall within [D - 30 calendar days, D];
    - total value >= $100,000;
    - at least one qualifying filing came after the issuer's last fired I signal.
  Timing: t = the first NYSE session strictly after D. The data sets carry a date, not a time, so this is conservative; live uses the same rule.
  BUY at the close of t if: G0, and a cluster completed on D.
  Levels: stop = Close(t) - 3 x ATR14(t); no target.
  Exits:
    - stop (resting);
    - time: decided at the close of holding session 40;
    - event lock.

### 3. OVERLAYS (tested), REPORT-ONLY ANALYSES, SHADOW-ONLY LAYERS
Tested overlays (only on sleeves that pass; adoption by rule O in section 4):
  G1 regime/breadth gate (idea from RyanJHamby/stock-screener, MIT): new BUYs at t only if SPY Close(t) > SPY MA200(t) AND breadth(t) >= 0.40. breadth(t) = the share of the evaluated universe's Liquid names with Close > MA200.
  V1 deterministic 8-K veto: no BUY at t if the ticker has an 8-K with any NEG8K item accepted within the 10 sessions ending at t.
  X1 pre-earnings exit (T only): for the next confirmed reaction session d0 inside the hold, the exit is decided at the close of session d0-2 and executed at the open of d0-1. Evaluate it paired, on T's base entries.
Report-only (never adoptable in this program):
  - Signal replication on the holdout: monthly IC (20-session beta-adjusted target) of C6, MOM12_1, RESMOM, HI52, -FIP, -REV21, -RVOL252 and MAD. Run in U1 and U2, with NW t and the three five-year blocks.
  - Pattern replication: incremental IC of every PATTERN_FEATURES feature, residualized on MOM12_1 and HI52, at U2 and U1 month-ends. If breakout_volume_ratio or base_depth_pct is negative with t <= -3 in BOTH universes, record it in CLAUDE.md as a candidate veto for a FUTURE pre-registered study. Do not use it now.
  - placebo-trend for T.
  - Close-checked stops.
  - Cost scenarios.
  - Recent period: signals Oct 2022-Oct 2026. This is unseen data only for E, N, P and I.
  - Top-3-per-day sub-book. Order: sleeve priority; then C6pct descending (T), EARpct descending (E), cluster value descending (I), gap descending (N), RSI2 ascending (P).
  - Combined book: signals per month, and concurrent open signals (median, 95th percentile, max) = units of capital needed.
  - Monte Carlo trade-order shuffle (idea from Jesse, MIT): 2,000 shuffles per passing sleeve, giving a max-drawdown distribution in units.
Shadow-only (forward data only; rules in section 4):
  L1 LLM negative-event veto.
  D1 days-to-cover veto, plus a report-only 2021-2026 retrospective.
  A1 analyst archive.

### 4. PRE-REGISTRATION (commit BEFORE any new backtest or IC run)
Holdout: signals with t from 2008-01-02 to 2022-09-30. Exits may run past 2022-09-30 on real data. Oct 2022 onward is the window C6 was chosen on and is NOT part of the holdout.

S-PASS rules (per sleeve, holdout only; U2 is primary because its membership is point-in-time):
  P1 U2: mean beta_adj_pct > 0 and the 95% CI lower bound > 0.
  P2 U2: the book mean is above the 95th percentile of its 200 placebo means, AND the paired diff vs the placebo median is > 0 with CI lower bound > 0.
  P3 U1 (confirmation): mean beta_adj_pct > 0 AND placebo percentile >= 90.
  P4 U2 stability: mean > 0 in at least 2 of the blocks [2008-01..2012-12], [2013-01..2017-12], [2018-01..2022-09], and no block mean < -0.50.
  P5 Multiple testing: Holm over the five sleeves, applied to the one-sided month-block bootstrap p of U2 mean beta_adj_pct > 0. The adjusted p must be < 0.05.
  P6 Costs: U2 break-even cost >= 15 bps per side.
  P7 Sample: >= 100 U2 signals spread over >= 36 distinct signal months.
  P8 (T only): C6 replication in the U2 holdout has mean IC > 0 with NW t >= 2.0.
  Outcome:
    - P1-P8 all pass -> LIVE on U1.
    - P1, P2 and P4-P8 pass but P3 fails -> LIVE-SP500 (live restricted to current S&P 500 members).
    - Anything else -> SHADOW: computed, tracked and shown, never alerted as a BUY.
    - If a sleeve's data cannot be built -> SHADOW with reason "untested".
Rule O (overlays; U2 holdout; Holm within each overlay family G1 and V1 across the LIVE sleeves; K = kept signals, R = removed signals):
  G1 or V1 is ADOPTED for a sleeve only if all three hold:
    - |R| <= 0.5 x (|K| + |R|);
    - the U1 point estimate of mean(R) - mean(K) is <= 0;
    - (a) mean(R) < mean(K) with one-sided Holm-adjusted p < 0.05, OR (b) mean(R) <= mean(K) AND the worst rolling-252-session equal-unit cumulative beta_adj P&L improves by >= 25%.
  X1 is ADOPTED only if either holds:
    - the paired per-signal diff (X1 - base) is > 0 with CI lower bound > 0;
    - the paired mean is >= -0.05 AND the 5th-percentile signal return improves by >= 20%.
  Overlays never turn a SHADOW sleeve LIVE.
Live kill rules (per LIVE sleeve; closed live signals; beta_adj_pct at 10 bps per side; m_h and s_h = holdout mean and SD per signal in the universe the sleeve trades):
  K3: first checked once the sleeve has >= 63 live sessions AND >= 20 closed live signals, then monthly. Move to SHADOW if live mean < m_h - 2 x s_h / sqrt(n).
  K12: checked at >= 252 live sessions AND >= 50 closed signals. Move to SHADOW if live mean <= 0 OR the live paired diff vs the live placebo median is <= 0. The live placebo uses the same construction, 200 runs.
  KDD: checked at any time. Move to SHADOW if the cumulative equal-unit beta_adj P&L since go-live (closed signals plus open ones marked at the close) falls below the 1st percentile of 2,000 month-block bootstrap paths of the holdout with the same number of signals.
  On any kill:
    - send an alert and log a trial;
    - only a new pre-registered study can reverse it;
    - open signals of a killed sleeve keep getting their SELLs. Never orphan an open signal.
Shadow adoption rules:
  L1:
    - A BUY is L1-flagged if a validated LLM label with direction -1 and materiality >= 2 exists for the ticker, with first_seen within the 10 sessions ending at t.
    - Pool live and shadow sleeves. After >= 126 sessions of logging and >= 30 flagged BUYs, ADOPT as a BUY veto if mean beta_adj(flagged) < mean beta_adj(unflagged) with month-block bootstrap one-sided p < 0.10, AND flagged BUYs are <= 20% of all BUYs.
    - Otherwise it stays a WARN label. Re-evaluate every 126 sessions on cumulative data; each evaluation is a logged trial.
  D1:
    - A BUY is D1-flagged if the ticker's latest PUBLISHED days-to-cover is in the top decile of U1.
    - Same adoption rule as L1, but after >= 252 sessions.
    - The 2021-2026 retrospective is report-only (it is not unseen data).
  A1:
    - Archive only. No analyst-based rule may be pre-registered before 252 sessions of archive exist.
    - Never display target-implied upside as an expected return.
Wording rule:
  - For LIVE sleeves only, the UI may say "Validated on the 2008-2022 backtest" with the U2 and U1 numbers, always followed by "Backtests and short live records do not prove future profit."
  - Never write "edge" or "profitable".
Trial list (record every run in config/research_trials.jsonl with phase "swing"; mark smoke runs):
  - S-T, S-E, S-N, S-P, S-I (the Holm family of 5);
  - O-G1/<sleeve>, O-V1/<sleeve>, O-X1/T;
  - the report-only items of section 3;
  - live K3, K12 and KDD checks, and L1 and D1 evaluations, as they occur.

Registry location: `config/research_trials.jsonl` (committed; per-run file `outputs/research_trials.jsonl`)

## Status

- All five sleeves are SHADOW: computed, tracked and shown, never alerted as a BUY.
- The Signals tab shows them in a collapsed "Not validated - do not trade" drawer with no BUY action.

#### Known limitations (measured)
- U2 survivorship: S&P 500 membership is daily (fja05680 start/end dates). Yahoo, however, has no history for most members that later delisted or were acquired; it keeps a stub row for such symbols (AVB, EA and QRVO stopped trading in 2026).
  - Member-days with no bar: 39% in 2008, falling to 8% in 2022.
  - 52 symbols have a first bar after their membership start (recycled or renamed symbols, and stubs); their earlier member-days are excluded.
- U1 is today's security master, so delisted stocks are missing throughout.
- Earnings dates come from SEC 8-K item 2.02 and are assumed known 5 sessions ahead. Foreign 6-K filers have none.
- The insider data sets carry a filing date, not a time, so t is the first session after the filing date.

#### Credits and licences
- fja05680/sp500 (MIT): historical S&P 500 membership (`data/reference/sp500_ticker_start_end.csv`).
- neurotrader888/TechnicalAnalysisAutomation (MIT): pivot design adapted in `src/patterns.py`.
- Apprise (BSD-2-Clause): alert library.
- RyanJHamby/stock-screener (G1) and Jesse (Monte Carlo shuffle): ideas only, no code used.
- No GPL/AGPL code.
