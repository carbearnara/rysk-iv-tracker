"""
σ√T Analysis Tool

Analyzes the usefulness of σ√T (sigma root T) as a trading indicator
by examining historical IV data from the Rysk IV Tracker database.
"""

import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict
import statistics
import json

try:
    import requests
except ImportError:
    print("Installing requests...")
    os.system(f"{sys.executable} -m pip install requests")
    import requests

# API base URL
API_BASE = "https://rysk-biscuit.vercel.app"


def parse_expiry(expiry_str):
    """Parse expiry string like '13FEB26' to datetime."""
    months = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
              'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
    try:
        day = int(expiry_str[:2])
        mon = months[expiry_str[2:5].upper()]
        yr = 2000 + int(expiry_str[5:7])
        return datetime(yr, mon, day)
    except:
        return None


def parse_timestamp(timestamp):
    """Parse various timestamp formats."""
    if isinstance(timestamp, datetime):
        return timestamp.replace(tzinfo=None)
    if isinstance(timestamp, str):
        # Try RFC 2822 format: 'Tue, 27 Jan 2026 23:22:00 GMT'
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(timestamp).replace(tzinfo=None)
        except:
            pass
        # Try ISO format
        try:
            return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).replace(tzinfo=None)
        except:
            pass
    return None


def calc_dte(expiry_str, timestamp):
    """Calculate days to expiry."""
    expiry_date = parse_expiry(expiry_str)
    if not expiry_date:
        return None
    ts = parse_timestamp(timestamp)
    if not ts:
        return None
    dte = (expiry_date - ts).days
    return max(0, dte)


def calc_sigma_root_t(iv, dte):
    """Calculate σ√T = IV × √(DTE/365)."""
    if dte is None or dte <= 0 or iv is None:
        return None
    return iv * (dte / 365) ** 0.5


def fetch_data(days=30):
    """Fetch historical IV data from API."""
    # Get list of assets
    assets_resp = requests.get(f"{API_BASE}/api/assets")
    assets = assets_resp.json()

    all_data = []
    print(f"Fetching data for {len(assets)} assets...")

    for asset in assets:
        print(f"  Fetching {asset}...", end=" ")
        resp = requests.get(f"{API_BASE}/api/iv/{asset}?days={days}")
        data = resp.json()
        print(f"{len(data)} records")
        all_data.extend(data)

    return all_data


def analyze_data(data):
    """Run analysis on the data."""
    print("\n" + "="*60)
    print("σ√T ANALYSIS REPORT")
    print("="*60)

    # Group by option (asset-strike-expiry)
    options = defaultdict(list)
    for row in data:
        key = f"{row['asset']}-{row['strike']}-{row['expiry']}"
        dte = calc_dte(row['expiry'], row['timestamp'])
        srt = calc_sigma_root_t(row['mid_iv'], dte)
        if srt is not None:
            options[key].append({
                'timestamp': row['timestamp'],
                'iv': row['mid_iv'],
                'dte': dte,
                'srt': srt,
                'apy': row['apy'],
                'asset': row['asset']
            })

    print(f"\nData Summary:")
    print(f"  Total records: {len(data)}")
    print(f"  Unique options: {len(options)}")
    print(f"  Assets: {set(row['asset'] for row in data)}")

    # Filter options with enough data points
    options_with_data = {k: v for k, v in options.items() if len(v) >= 5}
    print(f"  Options with 5+ data points: {len(options_with_data)}")

    if not options_with_data:
        print("\nNot enough data for analysis. Need more historical records.")
        return

    # Analysis 1: σ√T Trend Analysis
    print("\n" + "-"*60)
    print("ANALYSIS 1: σ√T TREND PATTERNS")
    print("-"*60)

    rising_srt = 0
    falling_srt = 0
    flat_srt = 0

    srt_changes = []
    iv_changes = []

    for key, points in options_with_data.items():
        points = sorted(points, key=lambda x: x['timestamp'])
        if len(points) < 2:
            continue

        first_srt = points[0]['srt']
        last_srt = points[-1]['srt']
        first_iv = points[0]['iv']
        last_iv = points[-1]['iv']

        srt_change_pct = (last_srt - first_srt) / first_srt * 100 if first_srt > 0 else 0
        iv_change_pct = (last_iv - first_iv) / first_iv * 100 if first_iv > 0 else 0

        srt_changes.append(srt_change_pct)
        iv_changes.append(iv_change_pct)

        if srt_change_pct > 5:
            rising_srt += 1
        elif srt_change_pct < -5:
            falling_srt += 1
        else:
            flat_srt += 1

    total = rising_srt + falling_srt + flat_srt
    if total > 0:
        print(f"\n  σ√T Trends (5% threshold):")
        print(f"    Rising:  {rising_srt} ({rising_srt/total*100:.1f}%)")
        print(f"    Falling: {falling_srt} ({falling_srt/total*100:.1f}%)")
        print(f"    Flat:    {flat_srt} ({flat_srt/total*100:.1f}%)")

    # Analysis 2: σ√T vs IV - Which is more stable?
    print("\n" + "-"*60)
    print("ANALYSIS 2: σ√T vs IV STABILITY")
    print("-"*60)

    srt_volatilities = []
    iv_volatilities = []

    for key, points in options_with_data.items():
        if len(points) < 3:
            continue
        srt_values = [p['srt'] for p in points]
        iv_values = [p['iv'] for p in points]

        # Calculate coefficient of variation (std/mean)
        srt_mean = statistics.mean(srt_values)
        iv_mean = statistics.mean(iv_values)

        if srt_mean > 0 and iv_mean > 0:
            srt_cv = statistics.stdev(srt_values) / srt_mean
            iv_cv = statistics.stdev(iv_values) / iv_mean
            srt_volatilities.append(srt_cv)
            iv_volatilities.append(iv_cv)

    if srt_volatilities and iv_volatilities:
        avg_srt_cv = statistics.mean(srt_volatilities)
        avg_iv_cv = statistics.mean(iv_volatilities)
        print(f"\n  Average Coefficient of Variation:")
        print(f"    σ√T: {avg_srt_cv:.4f}")
        print(f"    IV:  {avg_iv_cv:.4f}")
        print(f"\n  Interpretation:")
        if avg_srt_cv < avg_iv_cv:
            print(f"    σ√T is MORE STABLE than IV ({(1-avg_srt_cv/avg_iv_cv)*100:.1f}% less variable)")
            print(f"    This suggests σ√T smooths out some IV noise")
        else:
            print(f"    IV is MORE STABLE than σ√T ({(1-avg_iv_cv/avg_srt_cv)*100:.1f}% less variable)")

    # Analysis 3: Mean Reversion Test
    print("\n" + "-"*60)
    print("ANALYSIS 3: MEAN REVERSION TEST")
    print("-"*60)

    # For each option, check if high σ√T tends to fall and low tends to rise
    reversion_count = 0
    continuation_count = 0

    for key, points in options_with_data.items():
        points = sorted(points, key=lambda x: x['timestamp'])
        if len(points) < 10:
            continue

        srt_values = [p['srt'] for p in points]
        mean_srt = statistics.mean(srt_values)

        # Look at first half vs second half
        mid = len(points) // 2
        first_half_avg = statistics.mean([p['srt'] for p in points[:mid]])
        second_half_avg = statistics.mean([p['srt'] for p in points[mid:]])

        # If first half was above mean and second half moved toward mean, that's reversion
        if first_half_avg > mean_srt and second_half_avg < first_half_avg:
            reversion_count += 1
        elif first_half_avg < mean_srt and second_half_avg > first_half_avg:
            reversion_count += 1
        else:
            continuation_count += 1

    total_mr = reversion_count + continuation_count
    if total_mr > 0:
        print(f"\n  Mean Reversion vs Continuation:")
        print(f"    Reverted to mean: {reversion_count} ({reversion_count/total_mr*100:.1f}%)")
        print(f"    Continued trend:  {continuation_count} ({continuation_count/total_mr*100:.1f}%)")
        if reversion_count > continuation_count:
            print(f"\n  Interpretation: σ√T shows MEAN REVERTING behavior")
            print(f"    Potential strategy: Fade extreme σ√T values")
        else:
            print(f"\n  Interpretation: σ√T shows TRENDING behavior")
            print(f"    Potential strategy: Follow σ√T momentum")

    # Analysis 4: Correlation between σ√T change and IV change
    print("\n" + "-"*60)
    print("ANALYSIS 4: σ√T vs IV CORRELATION")
    print("-"*60)

    if len(srt_changes) > 2 and len(iv_changes) > 2:
        # Calculate Pearson correlation manually
        n = len(srt_changes)
        mean_srt_chg = sum(srt_changes) / n
        mean_iv_chg = sum(iv_changes) / n

        numerator = sum((s - mean_srt_chg) * (i - mean_iv_chg) for s, i in zip(srt_changes, iv_changes))
        denom_srt = sum((s - mean_srt_chg) ** 2 for s in srt_changes) ** 0.5
        denom_iv = sum((i - mean_iv_chg) ** 2 for i in iv_changes) ** 0.5

        if denom_srt > 0 and denom_iv > 0:
            correlation = numerator / (denom_srt * denom_iv)
            print(f"\n  Correlation between σ√T change and IV change: {correlation:.3f}")
            print(f"\n  Interpretation:")
            if correlation > 0.7:
                print(f"    Strong positive correlation - σ√T and IV move together")
                print(f"    σ√T may not add much info beyond IV")
            elif correlation > 0.3:
                print(f"    Moderate correlation - σ√T captures some unique signal")
            else:
                print(f"    Weak/no correlation - σ√T provides different information than IV")

    # Analysis 5: Predictive Value - Does today's σ√T predict tomorrow's?
    print("\n" + "-"*60)
    print("ANALYSIS 5: PREDICTIVE VALUE (Autocorrelation)")
    print("-"*60)

    all_srt_pairs = []  # (today's srt, tomorrow's srt)

    for key, points in options_with_data.items():
        points = sorted(points, key=lambda x: x['timestamp'])
        for i in range(len(points) - 1):
            all_srt_pairs.append((points[i]['srt'], points[i+1]['srt']))

    if len(all_srt_pairs) > 10:
        today_srt = [p[0] for p in all_srt_pairs]
        tomorrow_srt = [p[1] for p in all_srt_pairs]

        n = len(today_srt)
        mean_today = sum(today_srt) / n
        mean_tomorrow = sum(tomorrow_srt) / n

        numerator = sum((t - mean_today) * (m - mean_tomorrow) for t, m in zip(today_srt, tomorrow_srt))
        denom_today = sum((t - mean_today) ** 2 for t in today_srt) ** 0.5
        denom_tomorrow = sum((m - mean_tomorrow) ** 2 for m in tomorrow_srt) ** 0.5

        if denom_today > 0 and denom_tomorrow > 0:
            autocorr = numerator / (denom_today * denom_tomorrow)
            print(f"\n  Autocorrelation (lag-1): {autocorr:.3f}")
            print(f"\n  Interpretation:")
            if autocorr > 0.8:
                print(f"    High autocorrelation - σ√T is persistent/predictable")
                print(f"    Today's σ√T strongly predicts tomorrow's")
            elif autocorr > 0.5:
                print(f"    Moderate autocorrelation - some predictability")
            else:
                print(f"    Low autocorrelation - σ√T is more random/noisy")

    # Analysis 6: Asset-level comparison
    print("\n" + "-"*60)
    print("ANALYSIS 6: ASSET COMPARISON")
    print("-"*60)

    asset_stats = defaultdict(lambda: {'srt_values': [], 'iv_values': []})

    for key, points in options_with_data.items():
        asset = points[0]['asset']
        for p in points:
            asset_stats[asset]['srt_values'].append(p['srt'])
            asset_stats[asset]['iv_values'].append(p['iv'])

    print(f"\n  {'Asset':<8} {'Avg σ√T':<12} {'Avg IV':<12} {'σ√T StdDev':<12} {'IV StdDev':<12}")
    print(f"  {'-'*8} {'-'*12} {'-'*12} {'-'*12} {'-'*12}")

    for asset, stats in sorted(asset_stats.items()):
        if len(stats['srt_values']) >= 5:
            avg_srt = statistics.mean(stats['srt_values'])
            avg_iv = statistics.mean(stats['iv_values'])
            std_srt = statistics.stdev(stats['srt_values']) if len(stats['srt_values']) > 1 else 0
            std_iv = statistics.stdev(stats['iv_values']) if len(stats['iv_values']) > 1 else 0
            print(f"  {asset:<8} {avg_srt:<12.2f} {avg_iv:<12.2f} {std_srt:<12.2f} {std_iv:<12.2f}")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print("""
Based on this analysis, consider:

1. If σ√T is MORE STABLE than IV:
   - It may be useful for filtering out IV noise
   - Better for identifying true premium direction

2. If σ√T shows MEAN REVERSION:
   - Extreme values may be good contrarian signals
   - High σ√T → expect premium to fall
   - Low σ√T → expect premium to rise

3. If σ√T has LOW correlation with IV changes:
   - It provides unique information
   - Worth tracking alongside IV

4. If σ√T has HIGH autocorrelation:
   - It's persistent and tradeable
   - Trends tend to continue

Run this analysis with more data (30+ days) for more reliable results.
""")


def main():
    print("Fetching data from database...")
    try:
        data = fetch_data(days=30)
        if not data:
            print("No data found in database.")
            return
        analyze_data(data)
    except Exception as e:
        print(f"Error: {e}")
        raise


if __name__ == '__main__':
    main()
