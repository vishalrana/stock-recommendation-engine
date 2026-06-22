# GitHub Actions Setup Guide

This guide explains how to configure the **Nightly Recommendation Scan** workflow.

---

## What It Does

Every weekday after US market close, GitHub Actions will:

1. Download fresh stock data via `yfinance`
2. Run Strategy 1.1 Beta signal evaluation
3. Clear old signals from Supabase
4. Insert new qualified signals
5. Log the scan result to `scan_log`

The Vercel-hosted frontend automatically shows the latest data — no redeployment needed.

---

## Step 1 — Add GitHub Secrets

Go to your GitHub repository:

```
https://github.com/YOUR_USERNAME/stock-recommendation-engine
```

Navigate to:

```
Settings → Secrets and variables → Actions → New repository secret
```

Add these two secrets:

| Secret Name            | Where to Find It                                                    |
|------------------------|---------------------------------------------------------------------|
| `SUPABASE_URL`         | Supabase Dashboard → Settings → API → Project URL                  |
| `SUPABASE_SERVICE_KEY` | Supabase Dashboard → Settings → API → service_role (under `secret`) |

> ⚠️ **Use `service_role` key, NOT `anon` key.** The service_role key bypasses Row Level Security and is required for server-side writes.

### How to Find Values in Supabase

1. Go to [https://supabase.com/dashboard](https://supabase.com/dashboard)
2. Select your project
3. Click **Settings** (gear icon) in the left sidebar
4. Click **API**
5. Copy:
   - **Project URL** → paste as `SUPABASE_URL`
   - **service_role key** (click "Reveal") → paste as `SUPABASE_SERVICE_KEY`

---

## Step 2 — Verify Workflow File Exists

The workflow file is located at:

```
.github/workflows/nightly_scan.yml
```

It must be committed and pushed to the `main` branch for GitHub Actions to detect it.

---

## Step 3 — Run Manually (First Test)

1. Go to your GitHub repository
2. Click the **Actions** tab
3. In the left sidebar, click **Nightly Recommendation Scan**
4. Click the **Run workflow** button (top right)
5. Select branch: `main`
6. Click **Run workflow**

The workflow will start within a few seconds.

---

## Step 4 — Verify Success

### In GitHub Actions

1. Click into the running workflow
2. Click the **Generate Signals** job
3. Expand each step to see logs
4. Look for:
   - `✅ Dependencies installed.`
   - `[QUALIFIED]` lines for each signal found
   - `Scan complete. Scanned: XX, Qualified signals: XX`
   - `✅ Signal generation completed in XXs`

### In Supabase

1. Go to Supabase Dashboard → **Table Editor**
2. Check `signals` table — should have fresh rows
3. Check `scan_log` table — should have a new entry with:
   - `status: success`
   - Today's `scan_date`
   - `signals_generated` count
   - `scan_duration_secs`

### On the Website

- Visit your Vercel URL
- The recommendations table should show updated data
- The "Latest Scan Date" should match today

---

## Automatic Schedule

The workflow runs automatically:

- **When:** Every weekday (Mon–Fri) at **1:00 AM UTC**
- **US Eastern:** ~9:00 PM EST / 8:00 PM EDT
- **Why weekdays only:** US stock markets are closed on weekends

You can also trigger it manually at any time via the Actions tab.

---

## Troubleshooting

### Workflow Not Appearing in Actions Tab

- Ensure `.github/workflows/nightly_scan.yml` is committed to the `main` branch
- GitHub only detects workflows on the default branch

### "Missing SUPABASE_URL" or "Missing SUPABASE_SERVICE_KEY"

- Go to Settings → Secrets → verify both secrets exist
- Secret names are case-sensitive — must be exactly `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`

### "No cached files found"

- This means data download failed
- Check the `Install Dependencies` step for pip errors
- Check network connectivity in the `Run Signal Generation` step

### Workflow Passes But No Signals

- This is normal if no stocks currently meet Strategy 1.1 Beta criteria
- Check `scan_log` in Supabase — `signals_generated` will be `0`
- The strategy requires specific RSI pullback + recovery + volume conditions

### Workflow Fails

- Click into the failed job
- Expand the **Run Signal Generation** step
- The Python error traceback will be visible
- The **Log Summary** step shows exit code and duration even on failure

### Workflow Takes Too Long

- Expected runtime: **3–8 minutes**
- Downloads 100 tickers from yfinance + evaluates signals
- Timeout is set to 30 minutes as a safety net

---

## File Reference

| File | Purpose |
|------|---------|
| `.github/workflows/nightly_scan.yml` | GitHub Actions workflow definition |
| `jobs/generate_signals.py` | Signal generation script (executed by workflow) |
| `jobs/supabase_client.py` | Supabase connection helper |
| `jobs/requirements.txt` | Python dependencies for jobs |
| `requirements.txt` | Python dependencies for core engine |
