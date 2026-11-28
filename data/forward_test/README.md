# Forward-test data

Inputs of the pre-registered reviews (`docs/forward-test-reviews.md`), read by `scripts/forward_test_review.py`.

- `backtest_40205392302_trades.csv`: the issued ideas, with their returns, of production-universe backtest run 40205392302 (GitHub Actions, artifact `backtest-production-universe`, file `production_backtest_trades_production_universe.csv`).
  - Copied on 2026-11-28, before the artifact expires on 2027-01-23. sha256 `6d72ba04bf14833dd8c7e0b9d3479b8425756aed63582ca229300a78ad54e355`.
  - 1,631 closed ideas: 1,640 issued, 9 censored at the data's end. Signal dates 2022-10-26 to 2026-09-22, in 46 signal months.
  - The STOP rule draws its bootstrap paths from `beta_adjusted_pct` and `signal_date`.
- `replay_ideas.jsonl`: the REPLAY population, one JSON object per line. It is empty because no session from 2026-10-26 to 2026-11-27 lacked a successful nightly: `scan_log` has a `success` row for each of the 24 sessions (checked 2026-11-28). So no backfill run was made, and COMBINED equals LIVE.
