# Rysk IV Tracker - Project Context

## Overview
Options IV (Implied Volatility) tracker for Rysk Finance on HyperEVM. Single-file Flask serverless app deployed on Vercel with Supabase PostgreSQL backend. Includes an on-chain activity indexer and a TimesFM-based 7-day IV forecasting system.

## Architecture

```
api/index.py          — Main Vercel serverless function (~2200 lines)
                        Flask app with embedded HTML/CSS/JS dashboards
                        All API endpoints, cron jobs, scraping logic

forecast_runner.py    — Standalone TimesFM forecast script (GitHub Actions)
                        Asset-level IV forecasting with ratio distribution

.github/workflows/    — forecast.yml: daily at 06:00 UTC
```

## Infrastructure
- **Hosting**: Vercel (auto-deploys from `main` branch)
- **Database**: Supabase PostgreSQL
  - Project ref: `oalhhiovrdpczpfmjbrn`
  - Direct connection uses pooler on port 6543
- **GitHub repo**: `carbearnara/rysk-iv-tracker`
- **Vercel project**: `carnations-projects/rysk-iv-tracker`
- **Secrets**:
  - `DATABASE_URL` — set as both Vercel env var and GitHub Actions secret
  - `CRON_SECRET` — Vercel env var for cron auth
  - Supabase anon key (JWT) used for REST API access when needed

## Database Tables
- `iv_snapshots` — Historical IV data (scraped from Rysk Finance)
- `iv_forecasts` — Precomputed 7-day IV forecasts from TimesFM
- `onchain_positions` — Indexed on-chain trading activity
- `indexer_state` — Block tracking for the on-chain indexer

## Key Files

### `api/index.py`
- **Routes**: `/` (dashboard), `/activity` (on-chain dashboard), `/api/assets`, `/api/latest`, `/api/iv/<asset>`, `/api/forecasts/<asset>`, `/api/cron/fetch`, `/api/cron/index-activity`, `/api/activity/*`
- **Cache-Control headers** on all routes to reduce Vercel Fast Origin Transfer:
  - HTML pages: 5min browser / 10min edge
  - Data APIs: 1-2min browser / 2-4min edge
  - Forecasts: 1hr browser / 2hr edge (updates daily)
- **Dashboard features**:
  - Three display modes: IV, APR, σ√T (sigma root T)
  - "Show Spot Price" toggle with CoinGecko overlay
  - "Show 7d Forecast" toggle with dashed lines + confidence bands
  - Top 10 strike-expiry combos per asset shown on chart
  - Forecast toggle uses `updateForecastOverlay()` for smooth Chart.js animation (not destroy/recreate)
- **JS helper functions**:
  - `calcDTE(expiry, timestamp)` — parses "13FEB26" format expiry
  - `calcSigmaRootT(iv, dte)` — IV × √(DTE/365)
  - `calcAprFromIV(iv, strike, spot, dte, isPut)` — Black-Scholes forward pricing to convert IV → APR
- **Forecast rendering**: works in all three display modes
  - IV: direct forecast values
  - σ√T: forecast IV converted via calcSigmaRootT
  - APR: forecast IV converted to approximate APR via Black-Scholes with current spot price
  - All modes: forecast points past option expiry are filtered out (`dte > 0` check)

### `forecast_runner.py`
- **Asset-level approach**: Instead of forecasting each option independently, computes a single asset-level median IV series from ALL options, forecasts that once with TimesFM, then distributes to individual options using characteristic IV ratios
- **Key functions**:
  - `build_asset_level_series(all_rows)` — hourly median IV across all options
  - `compute_option_ratios(all_rows, asset_ts, asset_vals, combos)` — median(option_iv / asset_iv) over last 48h
  - `run_forecasts()` — loads TimesFM 2.0 200M, forecasts ONE series per asset, multiplies by ratios
  - `seed_test_forecasts()` — mock version using random walk (no ML deps needed)
- **CLI flags**: `--seed-test` for mock data generation
- **Model**: `google/timesfm-2.0-200m-pytorch`, horizon=168 (7 days × 24 hours)
- **Filters**: skips options with < 7 days to expiry

### `.github/workflows/forecast.yml`
- Runs daily at 06:00 UTC + manual dispatch
- Caches pip packages and HuggingFace model (~400MB)
- Python 3.11, ubuntu-latest, 30min timeout
- Uses `DATABASE_URL` from GitHub repository secrets

## Recent Changes (This Session)
1. `20365b9` — Refactored forecast_runner.py to asset-level approach with ratio distribution
2. `3742bf7` — Added Cache-Control + Vercel-CDN-Cache-Control headers to all routes
3. `5f76917` — Added forecast support for APR mode (BS forward pricing) and σ√T mode
4. `7a6f22d` — Fixed forecast lines extending past option expiry (clipped at dte > 0)

## Known Design Decisions
- **Forecast mid_iv only** — σ√T and APR are derived on the frontend from mid_iv
- **APR forecast is approximate** — uses current spot price (spot will change in reality)
- **Top 10 combos per asset** — matches the chart's existing selection logic
- **Hourly resampling** — TimesFM expects uniform spacing; historical data is irregular
- **Non-negative clamping** — forecast values floored at 0 (IV can't be negative)
- **Embedded HTML/JS** — required by Vercel serverless (no static file serving from Flask)
- **Separate requirements files** — `api/requirements.txt` for Vercel, `requirements-forecast.txt` for forecast script (TimesFM+PyTorch too large for Vercel's 250MB limit)

## Potential Future Work
- Run actual TimesFM forecasts via GitHub Actions (currently only seeded test data in DB)
- Extract CSS/JS from embedded HTML to separate cached files (further bandwidth reduction)
- Pre-compute percentile calculations for `/api/latest` to reduce payload size
- Consider lighter charting alternatives to Chart.js CDN loads
