"""
TimesFM IV Forecast Runner

Standalone script that reads historical IV data from Supabase,
runs TimesFM inference to produce 7-day forecasts, and writes
the results back to Supabase.

Intended to run locally or via GitHub Actions (not on Vercel).

Usage:
    python forecast_runner.py              # Full TimesFM forecast (requires timesfm + torch)
    python forecast_runner.py --seed-test  # Seed mock forecasts from historical data (no ML deps)
"""

import os
import sys
import random
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.environ.get('DATABASE_URL', '')
HORIZON = 168  # 7 days * 24 hours
MODEL_VERSION = 'timesfm-2.5-200m'
MIN_HISTORY_POINTS = 48  # need at least 48 hourly points (~2 days)
MIN_DTE_DAYS = 7  # skip options expiring within 7 days


def get_db():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_forecast_table():
    """Create iv_forecasts table if it doesn't exist."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS iv_forecasts (
            id SERIAL PRIMARY KEY,
            generated_at TIMESTAMP NOT NULL,
            asset TEXT NOT NULL,
            strike REAL NOT NULL,
            expiry TEXT NOT NULL,
            option_type TEXT,
            forecast_timestamp TIMESTAMP NOT NULL,
            forecast_mid_iv REAL NOT NULL,
            quantile_10 REAL,
            quantile_90 REAL,
            model_version TEXT DEFAULT 'timesfm-2.5-200m'
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_forecasts_asset_gen
        ON iv_forecasts(asset, generated_at)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_forecasts_lookup
        ON iv_forecasts(asset, strike, expiry, generated_at DESC)
    """)
    conn.commit()
    conn.close()
    print("iv_forecasts table ready.")


def get_assets():
    """Get list of tracked assets."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT asset FROM iv_snapshots ORDER BY asset")
    assets = [r['asset'] for r in cur.fetchall()]
    conn.close()
    return assets


def parse_expiry(expiry_str):
    """Parse expiry like '13FEB26' to datetime."""
    months = {
        'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
        'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
    }
    day = int(expiry_str[:2])
    mon = months.get(expiry_str[2:5].upper(), 1)
    yr = 2000 + int(expiry_str[5:7])
    return datetime(yr, mon, day)


def get_top_combos(asset, days=7):
    """
    Get top 10 active strike-expiry combos for an asset,
    matching the dashboard chart logic (recent + highest IV).
    Groups by strike+expiry only (not option_type) to match chart keys.
    """
    conn = get_db()
    cur = conn.cursor()
    since = datetime.utcnow() - timedelta(days=days)
    cur.execute("""
        SELECT strike, expiry,
               MAX(option_type) as option_type,
               MAX(timestamp) as latest_time,
               MAX(mid_iv) as max_iv,
               COUNT(*) as cnt
        FROM iv_snapshots
        WHERE asset = %s AND timestamp > %s AND mid_iv IS NOT NULL
        GROUP BY strike, expiry
        ORDER BY
            CASE WHEN MAX(timestamp) > NOW() - INTERVAL '24 hours' THEN 0 ELSE 1 END,
            MAX(mid_iv) DESC
        LIMIT 10
    """, (asset, since))
    combos = cur.fetchall()
    conn.close()
    return combos


def get_time_series(asset, strike, expiry, option_type):
    """Get historical IV time series for a specific option."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, mid_iv FROM iv_snapshots
        WHERE asset = %s AND strike = %s AND expiry = %s
              AND mid_iv IS NOT NULL
        ORDER BY timestamp ASC
    """, (asset, strike, expiry))
    rows = cur.fetchall()
    conn.close()
    return rows


def resample_hourly(rows):
    """
    Resample irregular time series to hourly intervals.
    Uses forward-fill for gaps, required by TimesFM.
    """
    if not rows:
        return [], []

    timestamps = [r['timestamp'] for r in rows]
    values = [r['mid_iv'] for r in rows]

    # Create hourly grid from first to last timestamp
    start = timestamps[0].replace(minute=0, second=0, microsecond=0)
    end = timestamps[-1].replace(minute=0, second=0, microsecond=0)
    hourly_ts = []
    hourly_vals = []

    current = start
    src_idx = 0
    last_val = values[0]

    while current <= end:
        # Advance source index to closest point at or before current
        while src_idx < len(timestamps) - 1 and timestamps[src_idx + 1] <= current:
            src_idx += 1
        last_val = values[src_idx]
        hourly_ts.append(current)
        hourly_vals.append(last_val)
        current += timedelta(hours=1)

    return hourly_ts, hourly_vals


def load_model():
    """Load TimesFM model."""
    print("Loading TimesFM model...")
    import timesfm

    tfm = timesfm.TimesFm(
        hparams=timesfm.TimesFmHparams(
            backend="gpu" if _has_cuda() else "cpu",
            per_core_batch_size=32,
            horizon_len=HORIZON,
        ),
        checkpoint=timesfm.TimesFmCheckpoint(
            huggingface_repo_id="google/timesfm-2.0-200m-pytorch",
        ),
    )
    print("Model loaded.")
    return tfm


def _has_cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def store_forecasts(batch_meta, point_forecasts, quantile_lo, quantile_hi, model_ver):
    """Store forecast results in the database."""
    generated_at = datetime.utcnow()
    conn = get_db()
    cur = conn.cursor()

    total_rows = 0
    for i, meta in enumerate(batch_meta):
        pf = point_forecasts[i]
        q10 = quantile_lo[i] if quantile_lo is not None else [None] * HORIZON
        q90 = quantile_hi[i] if quantile_hi is not None else [None] * HORIZON

        last_ts = meta['last_ts']

        for h in range(HORIZON):
            forecast_ts = last_ts + timedelta(hours=h + 1)
            mid_iv = max(0.0, float(pf[h]))  # clamp non-negative
            low = max(0.0, float(q10[h])) if q10[h] is not None else None
            high = max(0.0, float(q90[h])) if q90[h] is not None else None

            cur.execute("""
                INSERT INTO iv_forecasts
                    (generated_at, asset, strike, expiry, option_type,
                     forecast_timestamp, forecast_mid_iv, quantile_10, quantile_90, model_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                generated_at, meta['asset'], meta['strike'], meta['expiry'],
                meta['option_type'], forecast_ts, mid_iv, low, high, model_ver,
            ))
            total_rows += 1

    conn.commit()
    conn.close()
    print(f"Stored {total_rows} forecast rows (generated_at={generated_at}).")
    return total_rows


def cleanup_old_forecasts():
    """Delete forecasts older than 7 days."""
    conn = get_db()
    cur = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(days=7)
    cur.execute("DELETE FROM iv_forecasts WHERE generated_at < %s", (cutoff,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"Cleaned up {deleted} old forecast rows.")


def collect_series():
    """Collect all series eligible for forecasting."""
    assets = get_assets()
    if not assets:
        print("No assets found in iv_snapshots.")
        return [], []

    print(f"Assets to forecast: {assets}")

    batch_series = []
    batch_meta = []
    now = datetime.utcnow()

    for asset in assets:
        combos = get_top_combos(asset)
        print(f"  {asset}: {len(combos)} combos")

        for combo in combos:
            strike = combo['strike']
            expiry = combo['expiry']
            option_type = combo['option_type']

            # Skip options expiring within MIN_DTE_DAYS
            try:
                expiry_dt = parse_expiry(expiry)
                dte = (expiry_dt - now).total_seconds() / 86400
                if dte < MIN_DTE_DAYS:
                    print(f"    Skipping {strike}-{expiry} (DTE={dte:.0f}d)")
                    continue
            except Exception:
                continue

            rows = get_time_series(asset, strike, expiry, option_type)
            hourly_ts, hourly_vals = resample_hourly(rows)

            if len(hourly_vals) < MIN_HISTORY_POINTS:
                print(f"    Skipping {strike}-{expiry} (only {len(hourly_vals)} hourly points)")
                continue

            batch_series.append(hourly_vals)
            batch_meta.append({
                'asset': asset,
                'strike': strike,
                'expiry': expiry,
                'option_type': option_type,
                'last_ts': hourly_ts[-1],
            })

    return batch_series, batch_meta


def run_forecasts():
    """Main forecast pipeline using TimesFM."""
    import numpy as np

    init_forecast_table()
    batch_series, batch_meta = collect_series()

    if not batch_series:
        print("No series to forecast.")
        return

    print(f"\nForecasting {len(batch_series)} series with horizon={HORIZON}...")

    # Load model and run inference
    tfm = load_model()

    forecast_input = [np.array(s) for s in batch_series]
    freq_input = [0] * len(forecast_input)  # 0 = hourly in TimesFM

    point_forecasts, quantile_forecasts = tfm.forecast(
        forecast_input,
        freq=freq_input,
        quantiles=[0.1, 0.9],
    )

    # Extract quantile arrays
    quantile_lo = []
    quantile_hi = []
    for i in range(len(batch_meta)):
        if quantile_forecasts is not None:
            quantile_lo.append(quantile_forecasts[i][:, 0])
            quantile_hi.append(quantile_forecasts[i][:, 1])
        else:
            quantile_lo.append([None] * HORIZON)
            quantile_hi.append([None] * HORIZON)

    store_forecasts(batch_meta, point_forecasts, quantile_lo, quantile_hi, MODEL_VERSION)
    cleanup_old_forecasts()
    print("Done.")


def seed_test_forecasts():
    """
    Generate mock forecasts from historical data for frontend testing.
    No TimesFM needed — uses simple random walk projection.
    """
    init_forecast_table()
    batch_series, batch_meta = collect_series()

    if not batch_series:
        print("No series to generate test forecasts from.")
        return

    print(f"\nGenerating mock forecasts for {len(batch_series)} series...")

    point_forecasts = []
    quantile_lo = []
    quantile_hi = []

    for series in batch_series:
        last_val = series[-1]
        # Simple mean-reverting random walk
        mean_val = sum(series[-24:]) / min(24, len(series))
        revert_speed = 0.002
        volatility = max(0.5, last_val * 0.01)  # 1% of current value

        forecast = []
        q10 = []
        q90 = []
        current = last_val

        for h in range(HORIZON):
            # Mean revert with noise
            drift = revert_speed * (mean_val - current)
            current = max(0.1, current + drift + random.gauss(0, volatility * 0.3))
            forecast.append(current)
            # Widen confidence band over time
            spread = volatility * (1 + h / HORIZON * 2)
            q10.append(max(0.1, current - spread))
            q90.append(current + spread)

        point_forecasts.append(forecast)
        quantile_lo.append(q10)
        quantile_hi.append(q90)

    store_forecasts(batch_meta, point_forecasts, quantile_lo, quantile_hi, 'mock-test')
    print("Test forecasts seeded. Toggle 'Show 7d Forecast' on the dashboard.")


if __name__ == '__main__':
    if '--seed-test' in sys.argv:
        seed_test_forecasts()
    else:
        run_forecasts()
