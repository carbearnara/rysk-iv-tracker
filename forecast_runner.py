"""
TimesFM IV Forecast Runner

Standalone script that reads historical IV data from Supabase,
runs TimesFM inference to produce 7-day forecasts, and writes
the results back to Supabase.

Approach: For each asset, build a dense asset-level IV series (median
across all active options at each hour), forecast that single series
with TimesFM, then distribute back to individual options using their
characteristic IV ratio.

Intended to run locally or via GitHub Actions (not on Vercel).

Usage:
    python forecast_runner.py              # Full TimesFM forecast (requires timesfm + torch)
    python forecast_runner.py --seed-test  # Seed mock forecasts from historical data (no ML deps)
"""

import os
import sys
import random
import statistics
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
RATIO_LOOKBACK_HOURS = 48  # hours of recent data to compute per-option ratios


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


def get_all_iv_snapshots(asset):
    """Get ALL IV snapshots for an asset, used to build the asset-level series."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, strike, expiry, mid_iv FROM iv_snapshots
        WHERE asset = %s AND mid_iv IS NOT NULL
        ORDER BY timestamp ASC
    """, (asset,))
    rows = cur.fetchall()
    conn.close()
    return rows


def build_asset_level_series(all_rows):
    """
    Build a dense asset-level IV series by computing the median IV
    across all active options at each hourly interval.

    Returns (hourly_timestamps, hourly_median_ivs).
    """
    if not all_rows:
        return [], []

    # Bucket all data points into hourly bins
    buckets = {}
    for r in all_rows:
        ts = r['timestamp']
        hour_key = ts.replace(minute=0, second=0, microsecond=0)
        if hour_key not in buckets:
            buckets[hour_key] = []
        buckets[hour_key].append(r['mid_iv'])

    if not buckets:
        return [], []

    # Build continuous hourly grid with median IV per hour
    sorted_hours = sorted(buckets.keys())
    start = sorted_hours[0]
    end = sorted_hours[-1]

    hourly_ts = []
    hourly_vals = []
    current = start
    last_median = statistics.median(buckets[start])

    while current <= end:
        if current in buckets:
            last_median = statistics.median(buckets[current])
        hourly_ts.append(current)
        hourly_vals.append(last_median)
        current += timedelta(hours=1)

    return hourly_ts, hourly_vals


def compute_option_ratios(all_rows, asset_ts, asset_vals, combos, lookback_hours=RATIO_LOOKBACK_HOURS):
    """
    For each combo (strike-expiry), compute its characteristic ratio
    relative to the asset-level IV using recent data.

    ratio = median(option_iv / asset_iv) over the last `lookback_hours` hours.

    Returns dict: {(strike, expiry): ratio}
    """
    if not asset_ts or not asset_vals:
        return {}

    # Build a lookup from hour -> asset-level IV
    asset_by_hour = {}
    for ts, val in zip(asset_ts, asset_vals):
        asset_by_hour[ts] = val

    cutoff = asset_ts[-1] - timedelta(hours=lookback_hours)

    # Group recent option data by (strike, expiry)
    combo_set = {(c['strike'], c['expiry']) for c in combos}
    option_ratios_raw = {key: [] for key in combo_set}

    for r in all_rows:
        key = (r['strike'], r['expiry'])
        if key not in combo_set:
            continue
        if r['timestamp'] < cutoff:
            continue
        hour_key = r['timestamp'].replace(minute=0, second=0, microsecond=0)
        asset_iv = asset_by_hour.get(hour_key)
        if asset_iv and asset_iv > 0:
            option_ratios_raw[key].append(r['mid_iv'] / asset_iv)

    # Compute median ratio for each combo
    ratios = {}
    for key, vals in option_ratios_raw.items():
        if vals:
            ratios[key] = statistics.median(vals)
        else:
            ratios[key] = 1.0  # fallback: assume ratio of 1
    return ratios


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


def collect_asset_data():
    """
    For each asset, build:
      - asset-level IV series (median across all options per hour)
      - top 10 combos with their characteristic IV ratios
    Returns list of per-asset dicts.
    """
    assets = get_assets()
    if not assets:
        print("No assets found in iv_snapshots.")
        return []

    print(f"Assets to forecast: {assets}")
    now = datetime.utcnow()
    result = []

    for asset in assets:
        all_rows = get_all_iv_snapshots(asset)
        if not all_rows:
            print(f"  {asset}: no data")
            continue

        # Build asset-level series
        asset_ts, asset_vals = build_asset_level_series(all_rows)
        if len(asset_vals) < MIN_HISTORY_POINTS:
            print(f"  {asset}: only {len(asset_vals)} hourly points for asset-level series, skipping")
            continue

        print(f"  {asset}: {len(all_rows)} total snapshots -> {len(asset_vals)}h asset-level series")

        # Get top combos and filter by DTE
        combos = get_top_combos(asset)
        valid_combos = []
        for combo in combos:
            try:
                expiry_dt = parse_expiry(combo['expiry'])
                dte = (expiry_dt - now).total_seconds() / 86400
                if dte < MIN_DTE_DAYS:
                    print(f"    Skipping {combo['strike']}-{combo['expiry']} (DTE={dte:.0f}d)")
                    continue
            except Exception:
                continue
            valid_combos.append(combo)

        if not valid_combos:
            print(f"  {asset}: no valid combos after DTE filter")
            continue

        # Compute per-option ratios
        ratios = compute_option_ratios(all_rows, asset_ts, asset_vals, valid_combos)

        print(f"  {asset}: {len(valid_combos)} combos, ratios: " +
              ", ".join(f"{c['strike']}-{c['expiry']}={ratios.get((c['strike'], c['expiry']), 1.0):.3f}"
                        for c in valid_combos))

        result.append({
            'asset': asset,
            'asset_ts': asset_ts,
            'asset_vals': asset_vals,
            'combos': valid_combos,
            'ratios': ratios,
        })

    return result


def run_forecasts():
    """Main forecast pipeline using TimesFM with asset-level forecasting."""
    import numpy as np

    init_forecast_table()
    asset_data = collect_asset_data()

    if not asset_data:
        print("No assets to forecast.")
        return

    # Load model once
    tfm = load_model()

    for ad in asset_data:
        asset = ad['asset']
        asset_vals = ad['asset_vals']
        asset_ts = ad['asset_ts']
        combos = ad['combos']
        ratios = ad['ratios']

        print(f"\n--- {asset}: forecasting asset-level series ({len(asset_vals)}h) ---")

        # Forecast the single asset-level series
        forecast_input = [np.array(asset_vals)]
        freq_input = [0]  # 0 = hourly in TimesFM

        point_forecasts, quantile_forecasts = tfm.forecast(
            forecast_input,
            freq=freq_input,
            quantiles=[0.1, 0.9],
        )

        asset_pf = point_forecasts[0]  # shape: (HORIZON,)
        if quantile_forecasts is not None:
            asset_q10 = quantile_forecasts[0][:, 0]
            asset_q90 = quantile_forecasts[0][:, 1]
        else:
            asset_q10 = [None] * HORIZON
            asset_q90 = [None] * HORIZON

        # Distribute to individual options via ratios
        last_ts = asset_ts[-1]
        batch_meta = []
        option_pf = []
        option_q10 = []
        option_q90 = []

        for combo in combos:
            key = (combo['strike'], combo['expiry'])
            ratio = ratios.get(key, 1.0)

            pf = [max(0.0, float(asset_pf[h]) * ratio) for h in range(HORIZON)]
            q10 = [max(0.0, float(asset_q10[h]) * ratio) if asset_q10[h] is not None else None
                   for h in range(HORIZON)]
            q90 = [max(0.0, float(asset_q90[h]) * ratio) if asset_q90[h] is not None else None
                   for h in range(HORIZON)]

            batch_meta.append({
                'asset': asset,
                'strike': combo['strike'],
                'expiry': combo['expiry'],
                'option_type': combo['option_type'],
                'last_ts': last_ts,
            })
            option_pf.append(pf)
            option_q10.append(q10)
            option_q90.append(q90)

            print(f"    {combo['strike']}-{combo['expiry']}: ratio={ratio:.3f}, "
                  f"last_asset_iv={asset_vals[-1]:.1f}, last_option_iv={asset_vals[-1]*ratio:.1f}")

        store_forecasts(batch_meta, option_pf, option_q10, option_q90, MODEL_VERSION)

    cleanup_old_forecasts()
    print("\nDone.")


def seed_test_forecasts():
    """
    Generate mock forecasts using the asset-level approach for frontend testing.
    No TimesFM needed — uses simple random walk on asset-level series.
    """
    init_forecast_table()
    asset_data = collect_asset_data()

    if not asset_data:
        print("No assets to generate test forecasts from.")
        return

    for ad in asset_data:
        asset = ad['asset']
        asset_vals = ad['asset_vals']
        asset_ts = ad['asset_ts']
        combos = ad['combos']
        ratios = ad['ratios']

        print(f"\n--- {asset}: generating mock asset-level forecast ---")

        last_val = asset_vals[-1]
        mean_val = sum(asset_vals[-24:]) / min(24, len(asset_vals))
        revert_speed = 0.002
        volatility = max(0.5, last_val * 0.01)

        # Generate asset-level forecast with random walk
        asset_forecast = []
        asset_q10 = []
        asset_q90 = []
        current = last_val

        random.seed(42)
        for h in range(HORIZON):
            drift = revert_speed * (mean_val - current)
            current = max(0.1, current + drift + random.gauss(0, volatility * 0.3))
            asset_forecast.append(current)
            spread = volatility * (1 + h / HORIZON * 2)
            asset_q10.append(max(0.1, current - spread))
            asset_q90.append(current + spread)

        # Distribute to individual options via ratios
        last_ts = asset_ts[-1]
        batch_meta = []
        option_pf = []
        option_q10_list = []
        option_q90_list = []

        for combo in combos:
            key = (combo['strike'], combo['expiry'])
            ratio = ratios.get(key, 1.0)

            pf = [max(0.1, asset_forecast[h] * ratio) for h in range(HORIZON)]
            q10 = [max(0.1, asset_q10[h] * ratio) for h in range(HORIZON)]
            q90 = [asset_q90[h] * ratio for h in range(HORIZON)]

            batch_meta.append({
                'asset': asset,
                'strike': combo['strike'],
                'expiry': combo['expiry'],
                'option_type': combo['option_type'],
                'last_ts': last_ts,
            })
            option_pf.append(pf)
            option_q10_list.append(q10)
            option_q90_list.append(q90)

            print(f"    {combo['strike']}-{combo['expiry']}: ratio={ratio:.3f}")

        store_forecasts(batch_meta, option_pf, option_q10_list, option_q90_list, 'mock-test')

    print("\nTest forecasts seeded. Toggle 'Show 7d Forecast' on the dashboard.")


if __name__ == '__main__':
    if '--seed-test' in sys.argv:
        seed_test_forecasts()
    else:
        run_forecasts()
