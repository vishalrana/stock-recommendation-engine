---
name: database-maintenance
description: Change or maintain the Supabase database for this repo, including writing and applying SQL migrations, resetting or clearing tables, taking or restoring backups, and reseeding swing sleeve status. Use before any schema change or destructive database operation.
---

# Database maintenance

## Migrations
- Files: `supabase/migration_<date>_<topic>.sql`. They must be idempotent (`ADD COLUMN IF NOT EXISTS` and similar).
- Apply: `python scripts/apply_migration.py supabase/<file>.sql` (statement by statement through `public.run_migration`, using the service key from `.env`); `--dry-run` lists the statements.
- `public.run_migration` was created once from `supabase/setup_run_migration.sql`.
- If logs show `[SCHEMA] Wrote without unmigrated column(s)`, a migration is missing: apply it.
- Record each applied migration (date, file) in `docs/data-conventions.md` or `docs/research-history.md`.

## Reset
- `python scripts/clear_and_reset_db.py` is a dry run: it prints row counts and the clear/keep plan, and lists any other tables it finds.
- `--yes` backs up every table to `backups/supabase_<UTC>/` (gitignored, local only, never committed), then clears: signals, signals_history, scan_log, context_cache, portfolio_state, swing_signals, swing_events, swing_sleeve_status, swing_ledger, news_items, news_labels, analyst_snapshots.
- It keeps earnings_calendar, ticker_metrics and delisted_tickers. The `recommendations` view rebuilds from the tables.
- `--reseed-sleeves` reseeds `swing_sleeve_status` from `config/swing_sleeves.json`.
- Destructive: get the user's explicit yes first, and run only on main with CI green.
- Afterwards, check that the backup counts match the before counts and the cleared tables are empty. Record the date, reason, path and per-table rows in `docs/research-history.md` (Status log) and the CLAUDE.md state line.

## Safety
- Never print or commit `.env` values or backups. The repo is public.
- Never corrupt lifecycle history: open ideas keep their frozen issuance fields.
