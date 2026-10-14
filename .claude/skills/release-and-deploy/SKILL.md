---
name: release-and-deploy
description: Ship a change in this repo, covering the branch, commit, PR, CI, merge, the Vercel production deploy, dispatching GitHub Actions workflows, and the verification checklist with run ids. Use when committing, opening or merging a PR, deploying, or dispatching nightly_scan, swing_signals or other workflows.
---

# Release and deploy

## Commit and PR
- Branch from main; logical commits. Messages end with the Co-Authored-By line; PR bodies end with the Claude Code line.
- Never commit `.env`, keys, `backups/`, `outputs/` or data caches.
- Local checks first: `python scripts/run_all_regressions.py`; in `frontend/`: `npm run lint`, `npm test`, `npm run build`.
- `gh pr create --base main`; wait for CI: `gh run list --branch <b> --workflow CI -L 1`, then `gh run watch <id> --exit-status`.
- Merge only when CI is green (`gh pr merge <n> --merge`), then confirm CI on main for the merge commit.

## Vercel
- Vercel deploys main automatically. Check it: `gh api "repos/vishalrana/stock-recommendation-engine/deployments?sha=<sha>"`, then `.../deployments/<id>/statuses` (state and URL).
- Deployment URLs are behind Vercel login. To check rendered output, build locally (`cd frontend && npm run build && npx next start -p 3100`) against the live Supabase data and inspect the HTML. The bare `stock-recommendation-engine.vercel.app` is not this project.

## Workflows
- `gh workflow run nightly_scan.yml --ref main` (Ideas; about 7–10 min).
- `gh workflow run swing_signals.yml --ref main -f dry_run=false` (swing; about 30 min with a warm cache, longer cold).
- `refresh_current_ideas.yml` re-evaluates open ideas only.
- Research workflows: see the backtest-and-research skill.
- Read results with `gh run view <id> --log` and the "Provider health" notice. Stale data (latest bar older than the latest completed session) means no new ideas or signals, by design.

## Verification checklist (report each with its run id)
1. CI green on the PR head and on the main merge commit.
2. Vercel production deployment state `success` for the merge sha.
3. Dispatched workflows completed `success`. Note `stale`, the latest bar and the counts written.
4. Database contents match expectations (row counts, statuses, run ids).
5. Anything skipped or failed is reported as such. Nothing counts as verified without a run id.
