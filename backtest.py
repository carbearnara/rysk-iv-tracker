"""
σ√T Backtester

Tests trading strategies based on σ√T signals using historical data.
Since we don't have actual option prices, we measure:
- Direction prediction accuracy
- Average σ√T/IV change after signals
- Comparison to baseline strategies
"""

import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict
import statistics
import random

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests")
    import requests

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
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(timestamp).replace(tzinfo=None)
        except:
            pass
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


def prepare_options_data(data):
    """Group data by option and calculate σ√T."""
    options = defaultdict(list)

    for row in data:
        key = f"{row['asset']}-{row['strike']}-{row['expiry']}"
        dte = calc_dte(row['expiry'], row['timestamp'])
        srt = calc_sigma_root_t(row['mid_iv'], dte)
        ts = parse_timestamp(row['timestamp'])

        if srt is not None and ts is not None and dte is not None and dte > 0:
            options[key].append({
                'timestamp': ts,
                'iv': row['mid_iv'],
                'dte': dte,
                'srt': srt,
                'apy': row.get('apy'),
                'asset': row['asset'],
                'strike': row['strike'],
                'expiry': row['expiry']
            })

    # Sort each option's data by timestamp
    for key in options:
        options[key] = sorted(options[key], key=lambda x: x['timestamp'])

    # Filter to options with enough data
    return {k: v for k, v in options.items() if len(v) >= 20}


def calculate_percentiles(options_data):
    """Calculate σ√T percentiles for each option at each point."""
    for key, points in options_data.items():
        srt_values = [p['srt'] for p in points]

        for i, point in enumerate(points):
            # Use all data up to this point for percentile calculation
            historical = srt_values[:i+1]
            if len(historical) >= 5:
                sorted_hist = sorted(historical)
                below = sum(1 for v in sorted_hist if v < point['srt'])
                point['srt_percentile'] = (below / len(sorted_hist)) * 100
            else:
                point['srt_percentile'] = 50  # Default to middle

            # Also calculate IV percentile
            iv_values = [p['iv'] for p in points[:i+1]]
            if len(iv_values) >= 5:
                sorted_iv = sorted(iv_values)
                below_iv = sum(1 for v in sorted_iv if v < point['iv'])
                point['iv_percentile'] = (below_iv / len(sorted_iv)) * 100
            else:
                point['iv_percentile'] = 50


def strategy_mean_reversion_srt(options_data, low_threshold=25, high_threshold=75, hold_periods=5):
    """
    Mean Reversion Strategy on σ√T:
    - BUY signal when σ√T percentile < low_threshold (expect rise)
    - SELL signal when σ√T percentile > high_threshold (expect fall)

    Measure success by whether σ√T moved in predicted direction after hold_periods.
    """
    trades = []

    for key, points in options_data.items():
        for i in range(len(points) - hold_periods):
            point = points[i]
            future = points[i + hold_periods]

            pct = point.get('srt_percentile', 50)

            if pct < low_threshold:
                # BUY signal - expect σ√T to rise
                srt_change = future['srt'] - point['srt']
                srt_change_pct = (srt_change / point['srt']) * 100 if point['srt'] > 0 else 0
                trades.append({
                    'signal': 'BUY',
                    'srt_change': srt_change,
                    'srt_change_pct': srt_change_pct,
                    'iv_change_pct': (future['iv'] - point['iv']) / point['iv'] * 100 if point['iv'] > 0 else 0,
                    'success': srt_change > 0,
                    'option': key,
                    'entry_srt': point['srt'],
                    'entry_pct': pct
                })

            elif pct > high_threshold:
                # SELL signal - expect σ√T to fall
                srt_change = point['srt'] - future['srt']  # Inverted for sell
                srt_change_pct = (srt_change / point['srt']) * 100 if point['srt'] > 0 else 0
                trades.append({
                    'signal': 'SELL',
                    'srt_change': srt_change,
                    'srt_change_pct': srt_change_pct,
                    'iv_change_pct': (point['iv'] - future['iv']) / point['iv'] * 100 if point['iv'] > 0 else 0,
                    'success': srt_change > 0,
                    'option': key,
                    'entry_srt': point['srt'],
                    'entry_pct': pct
                })

    return trades


def strategy_mean_reversion_iv(options_data, low_threshold=25, high_threshold=75, hold_periods=5):
    """
    Mean Reversion Strategy on IV (for comparison):
    Same logic but using IV percentile instead of σ√T.
    """
    trades = []

    for key, points in options_data.items():
        for i in range(len(points) - hold_periods):
            point = points[i]
            future = points[i + hold_periods]

            pct = point.get('iv_percentile', 50)

            if pct < low_threshold:
                iv_change = future['iv'] - point['iv']
                iv_change_pct = (iv_change / point['iv']) * 100 if point['iv'] > 0 else 0
                trades.append({
                    'signal': 'BUY',
                    'iv_change': iv_change,
                    'iv_change_pct': iv_change_pct,
                    'success': iv_change > 0,
                    'option': key,
                    'entry_iv': point['iv'],
                    'entry_pct': pct
                })

            elif pct > high_threshold:
                iv_change = point['iv'] - future['iv']
                iv_change_pct = (iv_change / point['iv']) * 100 if point['iv'] > 0 else 0
                trades.append({
                    'signal': 'SELL',
                    'iv_change': iv_change,
                    'iv_change_pct': iv_change_pct,
                    'success': iv_change > 0,
                    'option': key,
                    'entry_iv': point['iv'],
                    'entry_pct': pct
                })

    return trades


def strategy_momentum_srt(options_data, lookback=5, hold_periods=5):
    """
    Momentum Strategy on σ√T:
    - BUY when σ√T has been rising over lookback periods
    - SELL when σ√T has been falling over lookback periods
    """
    trades = []

    for key, points in options_data.items():
        for i in range(lookback, len(points) - hold_periods):
            point = points[i]
            past = points[i - lookback]
            future = points[i + hold_periods]

            past_change = (point['srt'] - past['srt']) / past['srt'] * 100 if past['srt'] > 0 else 0

            if past_change > 2:  # Rising σ√T
                # BUY signal - expect continued rise
                future_change = (future['srt'] - point['srt']) / point['srt'] * 100 if point['srt'] > 0 else 0
                trades.append({
                    'signal': 'BUY',
                    'srt_change_pct': future_change,
                    'success': future_change > 0,
                    'option': key,
                    'past_change': past_change
                })

            elif past_change < -2:  # Falling σ√T
                # SELL signal - expect continued fall
                future_change = (point['srt'] - future['srt']) / point['srt'] * 100 if point['srt'] > 0 else 0
                trades.append({
                    'signal': 'SELL',
                    'srt_change_pct': future_change,
                    'success': future_change > 0,
                    'option': key,
                    'past_change': past_change
                })

    return trades


def strategy_mean_reversion_to_expiry(options_data, low_threshold=10, high_threshold=90):
    """
    Mean Reversion Strategy holding to expiry:
    - BUY/SELL signal based on σ√T percentile
    - Hold until the option expires (last data point)
    - Measure total σ√T change from signal to expiry
    """
    trades = []

    for key, points in options_data.items():
        if len(points) < 15:
            continue

        # Find signal points in first half of data (so there's time to expiry)
        cutoff = len(points) // 2

        for i in range(5, cutoff):  # Start at 5 to have percentile history
            point = points[i]
            final = points[-1]  # Last point before expiry

            pct = point.get('srt_percentile', 50)

            # Calculate days held
            days_held = (final['timestamp'] - point['timestamp']).total_seconds() / 86400

            if pct <= low_threshold:
                # BUY signal - expect σ√T to rise
                srt_change = final['srt'] - point['srt']
                srt_change_pct = (srt_change / point['srt']) * 100 if point['srt'] > 0 else 0
                iv_change_pct = (final['iv'] - point['iv']) / point['iv'] * 100 if point['iv'] > 0 else 0
                trades.append({
                    'signal': 'BUY',
                    'srt_change': srt_change,
                    'srt_change_pct': srt_change_pct,
                    'iv_change_pct': iv_change_pct,
                    'success': srt_change > 0,
                    'option': key,
                    'entry_srt': point['srt'],
                    'entry_pct': pct,
                    'entry_dte': point['dte'],
                    'days_held': days_held
                })
                break  # One signal per option

            elif pct >= high_threshold:
                # SELL signal - expect σ√T to fall
                srt_change = point['srt'] - final['srt']
                srt_change_pct = (srt_change / point['srt']) * 100 if point['srt'] > 0 else 0
                iv_change_pct = (point['iv'] - final['iv']) / point['iv'] * 100 if point['iv'] > 0 else 0
                trades.append({
                    'signal': 'SELL',
                    'srt_change': srt_change,
                    'srt_change_pct': srt_change_pct,
                    'iv_change_pct': iv_change_pct,
                    'success': srt_change > 0,
                    'option': key,
                    'entry_srt': point['srt'],
                    'entry_pct': pct,
                    'entry_dte': point['dte'],
                    'days_held': days_held
                })
                break  # One signal per option

    return trades


def strategy_random(options_data, hold_periods=5, num_trades=1000):
    """Random baseline strategy for comparison."""
    trades = []
    all_points = []

    for key, points in options_data.items():
        for i in range(len(points) - hold_periods):
            all_points.append((key, i, points))

    if len(all_points) < num_trades:
        num_trades = len(all_points)

    samples = random.sample(all_points, num_trades)

    for key, i, points in samples:
        point = points[i]
        future = points[i + hold_periods]

        signal = random.choice(['BUY', 'SELL'])

        if signal == 'BUY':
            srt_change = future['srt'] - point['srt']
            success = srt_change > 0
        else:
            srt_change = point['srt'] - future['srt']
            success = srt_change > 0

        srt_change_pct = (srt_change / point['srt']) * 100 if point['srt'] > 0 else 0

        trades.append({
            'signal': signal,
            'srt_change_pct': srt_change_pct,
            'success': success,
            'option': key
        })

    return trades


def evaluate_strategy(trades, name):
    """Evaluate strategy performance."""
    if not trades:
        return None

    wins = sum(1 for t in trades if t['success'])
    total = len(trades)
    win_rate = wins / total * 100

    changes = [t.get('srt_change_pct', t.get('iv_change_pct', 0)) for t in trades]
    avg_change = statistics.mean(changes)

    buy_trades = [t for t in trades if t['signal'] == 'BUY']
    sell_trades = [t for t in trades if t['signal'] == 'SELL']

    buy_wins = sum(1 for t in buy_trades if t['success']) if buy_trades else 0
    sell_wins = sum(1 for t in sell_trades if t['success']) if sell_trades else 0

    return {
        'name': name,
        'total_trades': total,
        'wins': wins,
        'win_rate': win_rate,
        'avg_change': avg_change,
        'buy_trades': len(buy_trades),
        'buy_win_rate': (buy_wins / len(buy_trades) * 100) if buy_trades else 0,
        'sell_trades': len(sell_trades),
        'sell_win_rate': (sell_wins / len(sell_trades) * 100) if sell_trades else 0
    }


def run_backtest():
    """Run full backtest."""
    print("\n" + "="*70)
    print("σ√T STRATEGY BACKTESTER")
    print("="*70)

    # Fetch data
    print("\nFetching historical data...")
    data = fetch_data(days=30)

    if not data:
        print("No data available.")
        return

    print(f"\nPreparing data...")
    options_data = prepare_options_data(data)
    print(f"  Options with sufficient data: {len(options_data)}")

    total_points = sum(len(v) for v in options_data.values())
    print(f"  Total data points: {total_points}")

    print("\nCalculating percentiles...")
    calculate_percentiles(options_data)

    # Test different hold periods
    for hold_periods in [3, 5, 10]:
        print("\n" + "="*70)
        print(f"TESTING WITH HOLD PERIOD = {hold_periods} observations (~{hold_periods * 15} minutes)")
        print("="*70)

        # Run strategies
        print("\nRunning strategies...")

        strategies = [
            ("σ√T Mean Reversion (25/75)", strategy_mean_reversion_srt(options_data, 25, 75, hold_periods)),
            ("σ√T Mean Reversion (20/80)", strategy_mean_reversion_srt(options_data, 20, 80, hold_periods)),
            ("σ√T Mean Reversion (10/90)", strategy_mean_reversion_srt(options_data, 10, 90, hold_periods)),
            ("IV Mean Reversion (25/75)", strategy_mean_reversion_iv(options_data, 25, 75, hold_periods)),
            ("σ√T Momentum", strategy_momentum_srt(options_data, 5, hold_periods)),
            ("Random Baseline", strategy_random(options_data, hold_periods, 2000)),
        ]

        results = []
        for name, trades in strategies:
            result = evaluate_strategy(trades, name)
            if result:
                results.append(result)

        # Print results
        print("\n" + "-"*70)
        print("RESULTS")
        print("-"*70)
        print(f"\n{'Strategy':<35} {'Trades':<10} {'Win Rate':<12} {'Avg Change':<12}")
        print(f"{'-'*35} {'-'*10} {'-'*12} {'-'*12}")

        for r in sorted(results, key=lambda x: x['win_rate'], reverse=True):
            print(f"{r['name']:<35} {r['total_trades']:<10} {r['win_rate']:.1f}%{'':<6} {r['avg_change']:+.2f}%")

        # Detailed breakdown
        print("\n" + "-"*70)
        print("DETAILED BREAKDOWN")
        print("-"*70)

        for r in results:
            if 'Mean Reversion' in r['name'] or 'Momentum' in r['name']:
                print(f"\n{r['name']}:")
                print(f"  BUY signals:  {r['buy_trades']:>5} trades, {r['buy_win_rate']:.1f}% win rate")
                print(f"  SELL signals: {r['sell_trades']:>5} trades, {r['sell_win_rate']:.1f}% win rate")

    # Hold to Expiry Analysis
    print("\n" + "="*70)
    print("HOLD TO EXPIRY ANALYSIS")
    print("="*70)

    expiry_trades = strategy_mean_reversion_to_expiry(options_data, 10, 90)

    if expiry_trades:
        expiry_result = evaluate_strategy(expiry_trades, "σ√T MR Hold to Expiry")

        print(f"\nStrategy: Mean Reversion with Hold to Expiry")
        print(f"  Threshold: 10/90 percentile")
        print(f"  Total Trades: {expiry_result['total_trades']}")
        print(f"  Win Rate: {expiry_result['win_rate']:.1f}%")
        print(f"  Avg σ√T Change: {expiry_result['avg_change']:+.2f}%")

        buy_trades = [t for t in expiry_trades if t['signal'] == 'BUY']
        sell_trades = [t for t in expiry_trades if t['signal'] == 'SELL']

        print(f"\n  BUY Signals (σ√T < 10th pctl):")
        print(f"    Trades: {len(buy_trades)}")
        if buy_trades:
            buy_wins = sum(1 for t in buy_trades if t['success'])
            print(f"    Win Rate: {buy_wins/len(buy_trades)*100:.1f}%")
            print(f"    Avg Days Held: {sum(t['days_held'] for t in buy_trades)/len(buy_trades):.1f}")
            print(f"    Avg Entry DTE: {sum(t['entry_dte'] for t in buy_trades)/len(buy_trades):.1f}")

        print(f"\n  SELL Signals (σ√T > 90th pctl):")
        print(f"    Trades: {len(sell_trades)}")
        if sell_trades:
            sell_wins = sum(1 for t in sell_trades if t['success'])
            print(f"    Win Rate: {sell_wins/len(sell_trades)*100:.1f}%")
            print(f"    Avg Days Held: {sum(t['days_held'] for t in sell_trades)/len(sell_trades):.1f}")
            print(f"    Avg Entry DTE: {sum(t['entry_dte'] for t in sell_trades)/len(sell_trades):.1f}")

        # Breakdown by DTE at entry
        print("\n  Performance by Entry DTE:")
        dte_buckets = {'0-3 days': [], '3-7 days': [], '7-14 days': [], '14+ days': []}
        for t in expiry_trades:
            dte = t['entry_dte']
            if dte <= 3:
                dte_buckets['0-3 days'].append(t)
            elif dte <= 7:
                dte_buckets['3-7 days'].append(t)
            elif dte <= 14:
                dte_buckets['7-14 days'].append(t)
            else:
                dte_buckets['14+ days'].append(t)

        print(f"    {'DTE Bucket':<15} {'Trades':<10} {'Win Rate':<12} {'Avg Change':<12}")
        print(f"    {'-'*15} {'-'*10} {'-'*12} {'-'*12}")
        for bucket, trades in dte_buckets.items():
            if trades:
                wins = sum(1 for t in trades if t['success'])
                avg_chg = sum(t['srt_change_pct'] for t in trades) / len(trades)
                print(f"    {bucket:<15} {len(trades):<10} {wins/len(trades)*100:.1f}%{'':<6} {avg_chg:+.2f}%")

    # Summary
    print("\n" + "="*70)
    print("INTERPRETATION GUIDE")
    print("="*70)
    print("""
Win Rate Interpretation:
  > 55%  = Potentially useful signal
  50-55% = Marginal edge, may not be tradeable after costs
  < 50%  = Strategy doesn't work (or inverse might work)

Comparing σ√T vs IV Mean Reversion:
  - If σ√T has higher win rate → σ√T adds value beyond raw IV
  - If similar → σ√T is just a smoothed version of IV

Mean Reversion vs Momentum:
  - If Mean Reversion wins → Fade extreme values
  - If Momentum wins → Follow trends

vs Random Baseline:
  - Any strategy should beat ~50% random baseline
  - The margin above 50% indicates signal strength
""")


if __name__ == '__main__':
    run_backtest()
