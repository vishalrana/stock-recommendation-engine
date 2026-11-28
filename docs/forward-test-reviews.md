# Forward-test reviews (pre-registered 2026-11-28)

Written before any Ideas forward-test outcome was analysed. Swing shadow closed-trade returns were looked at once, on 2026-11-26, with open positions unmarked; the swing items below are report-only. Nothing in this file changes after results exist.

## Population
- LIVE: Ideas issued by a successful nightly from scan date 2026-10-20 on. Excluded: OBE (2026-10-16) and anything before 2026-10-20.
- REPLAY: Ideas the harness issues for the sessions without a nightly, from 2026-10-26 to the session before the first restored nightly. They are produced once by the backfill run, whose run id is recorded, and never regenerated. 2026-10-21 stays a gap with no replay.
- COMBINED: LIVE plus REPLAY, with one open idea per ticker. A later idea for a ticker that is open in the other set is dropped from COMBINED and counted.
- Every table shows LIVE, REPLAY and COMBINED separately.

## Measures
All are computed with the harness's own functions and default costs. LIVE outcomes are recomputed with the same exit engine from their issued levels, and any difference from the live lifecycle's recorded exits is reported.
- M1: mean beta-adjusted % per closed idea, with a 95% CI from 2,000 signal-month block-bootstrap resamples. Below 20 closed ideas, counts only.
- M2: cumulative equal-unit beta-adjusted P&L of all ideas, open ones marked at the latest close.
- Placebo: the harness's per-idea random-stock placebo; the paired difference of M1 against the per-idea placebo median, with its CI.
- Also: ideas a session, funnel counts, the share of ideas with a report inside the hold, and open and closed counts.

## Reviews
R1 2027-01-16, R2 2027-04-17, R3 2027-10-16, R4 2028-10-14. Each review uses data through the latest completed session on its date. The review workflow writes docs/forward-test-reviews/<date>.md with its run id.

## Decision rules (applied mechanically)
1. STOP, at any review: COMBINED M2 is below the 1st percentile of 2,000 signal-month block-bootstrap paths of run 40205392302's issued ideas with the same number of ideas. STOP ends the Ideas forward test, and the app shows "Forward test stopped by its pre-registered rule on <date>".
2. At R3 and R4: if the COMBINED M1 95% CI is entirely below 0, END. The live record confirms the backtest, and Ideas stay a screener. If the CI includes 0, continue to the next review; after R4, the user decides. If the CI is entirely above 0, report "live better than backtest"; Ideas stay SHADOW unless a new pre-registered study passes.
3. Forward results alone never take Ideas or a swing sleeve out of SHADOW.
4. Between reviews, no rule, threshold, scoring input or evidence file changes because of forward-test outcomes. Data handling, tooling and display fixes stay allowed and are logged.

## Swing sleeves (report only)
At each review, per sleeve: M1 and M2 on shadow signals (voided entries excluded), and the paired difference against a live placebo built like the holdout placebo (200 runs). The swing program's own rules are unchanged.
