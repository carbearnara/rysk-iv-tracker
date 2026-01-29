"""
Vercel Serverless API for Rysk IV Tracker

Handles all routes including dashboard, API endpoints, and cron jobs.
Uses PostgreSQL (Supabase) for data storage.
"""

import os
import json
import re
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template_string
import requests

# Database connection
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

# Configuration
DATABASE_URL = os.environ.get('DATABASE_URL', '')
CRON_SECRET = os.environ.get('CRON_SECRET', '')

# Dashboard HTML template (embedded for serverless)
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rysk IV Tracker</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; padding-bottom: 20px; border-bottom: 1px solid #30363d; flex-wrap: wrap; gap: 15px; }
        h1 { font-size: 24px; color: #58a6ff; }
        .controls { display: flex; gap: 15px; flex-wrap: wrap; }
        .control-group { display: flex; flex-direction: column; gap: 5px; }
        label { font-size: 12px; color: #8b949e; text-transform: uppercase; }
        select { padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px; background: #161b22; color: #c9d1d9; font-size: 14px; min-width: 120px; }
        button { padding: 8px 16px; border: none; border-radius: 6px; background: #238636; color: white; font-size: 14px; cursor: pointer; }
        button:hover { background: #2ea043; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px; }
        @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }
        .card h2 { font-size: 16px; margin-bottom: 15px; color: #8b949e; }
        .chart-container { position: relative; height: 300px; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #21262d; }
        th { color: #8b949e; font-weight: 500; text-transform: uppercase; font-size: 11px; }
        tr:hover { background: #21262d; }
        .iv-value { font-family: monospace; }
        .pricing-expensive { color: #f85149; font-weight: bold; }
        .pricing-cheap { color: #3fb950; font-weight: bold; }
        .pricing-fair { color: #8b949e; }
        .pricing-na { color: #484f58; }
        .aggregate-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
        .asset-card { background: #21262d; border-radius: 8px; padding: 15px; text-align: center; }
        .asset-card .asset-name { font-size: 18px; font-weight: bold; color: #c9d1d9; margin-bottom: 8px; }
        .asset-card .asset-pricing { font-size: 14px; font-weight: bold; padding: 4px 8px; border-radius: 4px; }
        .asset-card .asset-pricing.expensive { background: rgba(248,81,73,0.2); color: #f85149; }
        .asset-card .asset-pricing.cheap { background: rgba(63,185,80,0.2); color: #3fb950; }
        .asset-card .asset-pricing.fair { background: rgba(139,148,158,0.2); color: #8b949e; }
        .asset-card .asset-pct { font-size: 11px; color: #8b949e; margin-top: 6px; }
        .loading { display: flex; align-items: center; justify-content: center; height: 200px; color: #8b949e; }
        .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }
        .stat { background: #21262d; padding: 15px; border-radius: 6px; text-align: center; }
        .stat-value { font-size: 24px; font-weight: bold; color: #58a6ff; }
        .stat-label { font-size: 12px; color: #8b949e; margin-top: 5px; }
        @media (max-width: 600px) { .stats { grid-template-columns: repeat(2, 1fr); } }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Rysk IV Tracker</h1>
            <div class="controls">
                <div class="control-group">
                    <label>Asset</label>
                    <select id="asset-select"><option>Loading...</option></select>
                </div>
                <div class="control-group">
                    <label>Days</label>
                    <select id="days-select">
                        <option value="1">1 day</option>
                        <option value="7" selected>7 days</option>
                        <option value="30">30 days</option>
                    </select>
                </div>
                <div class="control-group">
                    <label>&nbsp;</label>
                    <button onclick="refresh()">Refresh</button>
                </div>
            </div>
        </header>
        <div class="stats">
            <div class="stat"><div class="stat-value" id="stat-assets">-</div><div class="stat-label">Assets</div></div>
            <div class="stat"><div class="stat-value" id="stat-records">-</div><div class="stat-label">Records</div></div>
            <div class="stat"><div class="stat-value" id="stat-avg-iv">-</div><div class="stat-label">Avg IV</div></div>
            <div class="stat"><div class="stat-value" id="stat-updated">-</div><div class="stat-label">Last Update</div></div>
        </div>
        <div class="card" style="margin-bottom: 20px;">
            <h2>Market Pricing Overview</h2>
            <div id="aggregate-pricing" class="aggregate-grid"><div class="loading">Loading...</div></div>
        </div>
        <div class="grid">
            <div class="card"><h2>IV Over Time</h2><div class="chart-container"><canvas id="iv-chart"></canvas></div></div>
            <div class="card"><h2>IV by Strike</h2><div class="chart-container"><canvas id="strike-chart"></canvas></div></div>
        </div>
        <div class="card"><h2>Latest IV Values</h2><div id="table-container"><div class="loading">Loading...</div></div></div>
    </div>
    <script>
        let ivChart = null, strikeChart = null;
        async function init() {
            const assets = await (await fetch('/api/assets')).json();
            const sel = document.getElementById('asset-select');
            sel.innerHTML = assets.map(a => `<option value="${a}">${a}</option>`).join('');
            sel.onchange = refresh;
            document.getElementById('days-select').onchange = refresh;
            await updateAggregatePricing();
            refresh();
        }
        async function updateAggregatePricing() {
            const allData = await (await fetch('/api/latest')).json();
            const byAsset = {};
            allData.forEach(d => {
                if (!byAsset[d.asset]) byAsset[d.asset] = [];
                if (d.iv_percentile !== null) byAsset[d.asset].push(d.iv_percentile);
            });
            const container = document.getElementById('aggregate-pricing');
            const cards = Object.entries(byAsset).map(([asset, pcts]) => {
                if (pcts.length === 0) return `<div class="asset-card"><div class="asset-name">${asset}</div><div class="asset-pricing fair">NO DATA</div></div>`;
                const avgPct = pcts.reduce((a,b) => a+b, 0) / pcts.length;
                let pricing, pricingClass;
                if (avgPct >= 75) { pricing = 'EXPENSIVE'; pricingClass = 'expensive'; }
                else if (avgPct <= 25) { pricing = 'CHEAP'; pricingClass = 'cheap'; }
                else { pricing = 'FAIR'; pricingClass = 'fair'; }
                return `<div class="asset-card"><div class="asset-name">${asset}</div><div class="asset-pricing ${pricingClass}">${pricing}</div><div class="asset-pct">Avg ${avgPct.toFixed(0)}th percentile</div></div>`;
            });
            container.innerHTML = cards.join('');
        }
        async function refresh() {
            const asset = document.getElementById('asset-select').value;
            const days = document.getElementById('days-select').value;
            const [ivData, latestData, assets] = await Promise.all([
                (await fetch(`/api/iv/${asset}?days=${days}`)).json(),
                (await fetch(`/api/latest?asset=${asset}`)).json(),
                (await fetch('/api/assets')).json(),
                updateAggregatePricing()
            ]);
            document.getElementById('stat-assets').textContent = assets.length;
            document.getElementById('stat-records').textContent = ivData.length;
            if (ivData.length > 0) {
                const avgIV = ivData.reduce((s,d) => s + (d.mid_iv||0), 0) / ivData.length;
                document.getElementById('stat-avg-iv').textContent = avgIV.toFixed(1) + '%';
                document.getElementById('stat-updated').textContent = new Date(ivData[ivData.length-1].timestamp).toLocaleTimeString();
            }
            updateCharts(ivData, latestData);
            updateTable(latestData);
        }
        function updateCharts(ivData, latestData) {
            const ctx1 = document.getElementById('iv-chart').getContext('2d');
            const groups = {};
            ivData.forEach(d => { const k = `${d.strike}-${d.expiry}`; if (!groups[k]) groups[k] = []; groups[k].push(d); });
            const datasets = Object.entries(groups).slice(0,5).map(([k, pts], i) => ({
                label: k, data: pts.map(p => ({x: new Date(p.timestamp), y: p.mid_iv})),
                borderColor: ['#58a6ff','#3fb950','#f85149','#a371f7','#f0883e'][i],
                backgroundColor: ['#58a6ff','#3fb950','#f85149','#a371f7','#f0883e'][i],
                tension: 0, pointRadius: 5, pointHoverRadius: 7, showLine: true, stepped: false
            }));
            if (ivChart) ivChart.destroy();
            ivChart = new Chart(ctx1, { type: 'line', data: { datasets }, options: { responsive: true, maintainAspectRatio: false, scales: { x: { type: 'time', grid: { color: '#21262d' }, ticks: { color: '#8b949e' } }, y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } } }, plugins: { legend: { labels: { color: '#8b949e' } } } } });

            const ctx2 = document.getElementById('strike-chart').getContext('2d');
            const strikeData = {};
            latestData.forEach(d => { if (!strikeData[d.strike]) strikeData[d.strike] = []; strikeData[d.strike].push(d.mid_iv); });
            const strikes = Object.keys(strikeData).sort((a,b) => a-b);
            const avgMid = strikes.map(s => strikeData[s].reduce((a,b)=>a+b,0)/strikeData[s].length);
            if (strikeChart) strikeChart.destroy();
            strikeChart = new Chart(ctx2, { type: 'bar', data: { labels: strikes, datasets: [{ label: 'IV %', data: avgMid, backgroundColor: '#58a6ff' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } }, y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } } }, plugins: { legend: { display: false } } } });
        }
        function updateTable(data) {
            if (!data.length) { document.getElementById('table-container').innerHTML = '<div class="loading">No data</div>'; return; }
            const pricingClass = p => p === 'EXPENSIVE' ? 'pricing-expensive' : p === 'CHEAP' ? 'pricing-cheap' : 'pricing-fair';
            const pricingLabel = d => d.pricing ? `<span class="${pricingClass(d.pricing)}">${d.pricing}</span>${d.iv_percentile !== null ? ` <small>(${d.iv_percentile.toFixed(0)}%ile)</small>` : ''}` : '<span class="pricing-na">-</span>';
            document.getElementById('table-container').innerHTML = `<table><thead><tr><th>Asset</th><th>Strike</th><th>Expiry</th><th>Type</th><th>IV</th><th>APY</th><th>Pricing</th></tr></thead><tbody>${data.map(d => `<tr><td>${d.asset}</td><td>${d.strike}</td><td>${d.expiry}</td><td>${d.option_type||'-'}</td><td class="iv-value">${d.mid_iv?d.mid_iv.toFixed(2)+'%':'-'}</td><td>${d.apy?d.apy.toFixed(2)+'%':'-'}</td><td>${pricingLabel(d)}</td></tr>`).join('')}</tbody></table>`;
        }
        init();
    </script>
</body>
</html>
'''


def get_db():
    """Get database connection."""
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not configured")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """Initialize database schema."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS iv_snapshots (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            asset TEXT NOT NULL,
            strike REAL NOT NULL,
            expiry TEXT NOT NULL,
            bid_iv REAL,
            ask_iv REAL,
            mid_iv REAL,
            option_type TEXT,
            apy REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_asset_time ON iv_snapshots(asset, timestamp)")
    conn.commit()
    conn.close()


# ============== Routes ==============

@app.route('/')
def index():
    """Serve dashboard."""
    return render_template_string(DASHBOARD_HTML)


@app.route('/api/assets')
def api_assets():
    """Get list of tracked assets."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT asset FROM iv_snapshots ORDER BY asset")
        assets = [row['asset'] for row in cursor.fetchall()]
        conn.close()
        return jsonify(assets)
    except Exception as e:
        return jsonify([])


@app.route('/api/latest')
def api_latest():
    """Get latest IV values with pricing indicator."""
    asset = request.args.get('asset')
    try:
        conn = get_db()
        cursor = conn.cursor()

        # Get latest values
        query = """
            SELECT DISTINCT ON (asset, strike, expiry) *
            FROM iv_snapshots
            WHERE 1=1
        """
        params = []
        if asset:
            query += " AND asset = %s"
            params.append(asset)
        query += " ORDER BY asset, strike, expiry, timestamp DESC"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        # Get historical IV for percentile calculation (last 7 days)
        since = datetime.utcnow() - timedelta(days=7)
        cursor.execute("""
            SELECT asset, strike, expiry, mid_iv
            FROM iv_snapshots
            WHERE mid_iv IS NOT NULL AND timestamp > %s
        """, (since,))
        history = cursor.fetchall()
        conn.close()

        # Build historical IV lookup: {(asset, strike, expiry): [iv1, iv2, ...]}
        iv_history = {}
        for h in history:
            key = (h['asset'], h['strike'], h['expiry'])
            if key not in iv_history:
                iv_history[key] = []
            iv_history[key].append(h['mid_iv'])

        # Add percentile and pricing indicator to each row
        results = []
        for r in rows:
            row = dict(r)
            key = (r['asset'], r['strike'], r['expiry'])
            hist = iv_history.get(key, [])

            if r['mid_iv'] and len(hist) >= 3:
                # Calculate percentile
                sorted_hist = sorted(hist)
                current_iv = r['mid_iv']
                below = sum(1 for iv in sorted_hist if iv < current_iv)
                percentile = (below / len(sorted_hist)) * 100

                row['iv_percentile'] = round(percentile, 1)
                row['iv_min'] = round(min(hist), 2)
                row['iv_max'] = round(max(hist), 2)

                # Pricing indicator
                if percentile >= 75:
                    row['pricing'] = 'EXPENSIVE'
                elif percentile <= 25:
                    row['pricing'] = 'CHEAP'
                else:
                    row['pricing'] = 'FAIR'
            else:
                row['iv_percentile'] = None
                row['pricing'] = None
                row['iv_min'] = None
                row['iv_max'] = None

            results.append(row)

        return jsonify(results)
    except Exception as e:
        return jsonify([])


@app.route('/api/iv/<asset>')
def api_iv(asset):
    """Get IV time series for an asset."""
    days = request.args.get('days', 7, type=int)
    try:
        conn = get_db()
        cursor = conn.cursor()
        since = datetime.utcnow() - timedelta(days=days)
        cursor.execute("""
            SELECT * FROM iv_snapshots
            WHERE asset = %s AND timestamp > %s
            ORDER BY timestamp ASC
        """, (asset, since))
        rows = cursor.fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify([])


@app.route('/api/cron/fetch')
def cron_fetch():
    """Cron endpoint to fetch IV data."""
    # Verify cron secret for security
    auth = request.headers.get('Authorization', '')
    if CRON_SECRET and auth != f'Bearer {CRON_SECRET}':
        # Also check Vercel cron header
        if request.headers.get('x-vercel-cron') != '1':
            return jsonify({'error': 'Unauthorized'}), 401

    try:
        init_db()
        records = fetch_iv_data()
        if records:
            save_records(records)
            return jsonify({'success': True, 'records': len(records)})
        return jsonify({'success': True, 'records': 0, 'message': 'No data found'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/fetch', methods=['POST'])
def manual_fetch():
    """Manual fetch endpoint (requires secret)."""
    auth = request.headers.get('Authorization', '')
    if CRON_SECRET and auth != f'Bearer {CRON_SECRET}':
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        init_db()
        records = fetch_iv_data()
        if records:
            save_records(records)
            return jsonify({'success': True, 'records': len(records)})
        return jsonify({'success': True, 'records': 0})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============== Scraping Logic ==============

def fetch_iv_data():
    """Fetch IV data from Rysk Finance."""
    url = 'https://app.rysk.finance'
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

    response = requests.get(url, headers=headers, timeout=30)
    html = response.text.replace('\\"', '"').replace('\\\\', '\\')

    records = []
    spot_prices = extract_spot_prices(html)

    assets = ['BTC', 'ETH', 'SOL', 'HYPE', 'PURR', 'PUMP', 'ZEC', 'XRP']
    asset_positions = []

    for asset in assets:
        pos = html.find(f'"{asset}":{{"combinations":')
        if pos >= 0:
            asset_positions.append((asset, pos))

    asset_positions.sort(key=lambda x: x[1])

    for i, (asset, start_pos) in enumerate(asset_positions):
        end_pos = asset_positions[i + 1][1] if i + 1 < len(asset_positions) else start_pos + 20000
        chunk = html[start_pos:end_pos]

        pattern = (
            r'"([\d.]+)-([\d]+)":\{'
            r'"expiry":"([^"]+)"[^}]*?'
            r'"strike":([\d.]+)[^}]*?'
            r'"isPut":(true|false)[^}]*?'
            r'"bidIv":([\d.]+)[^}]*?'
            r'"askIv":([\d.]+)[^}]*?'
            r'"apy":([\d.]+)'
        )

        entries = re.findall(pattern, chunk)

        for entry in entries:
            strike_key, timestamp, expiry, strike, is_put, bid_iv, ask_iv, apy = entry

            bid_iv_f = float(bid_iv)
            ask_iv_f = float(ask_iv)
            apy_f = float(apy)

            # Calculate IV from APY if bid/ask IV is 0
            if bid_iv_f == 0 and apy_f > 0:
                spot = spot_prices.get(asset)
                if spot:
                    calc_iv = calculate_iv_from_apy(
                        spot=spot,
                        strike=float(strike),
                        expiry=expiry,
                        apy=apy_f,
                        is_put=(is_put == 'true')
                    )
                    if calc_iv:
                        bid_iv_f = ask_iv_f = calc_iv

            mid_iv = (bid_iv_f + ask_iv_f) / 2 if bid_iv_f > 0 else None

            records.append({
                'asset': asset,
                'strike': float(strike),
                'expiry': expiry,
                'bid_iv': bid_iv_f if bid_iv_f > 0 else None,
                'ask_iv': ask_iv_f if ask_iv_f > 0 else None,
                'mid_iv': mid_iv,
                'option_type': 'put' if is_put == 'true' else 'call',
                'apy': apy_f if apy_f > 0 else None
            })

    # Filter valid records
    return [r for r in records if r.get('mid_iv') or r.get('apy')]


def extract_spot_prices(html):
    """Extract spot prices from HTML."""
    spot_prices = {}
    assets = ['BTC', 'ETH', 'SOL', 'HYPE', 'PURR', 'PUMP', 'ZEC', 'XRP']

    for asset in assets:
        pattern = rf'"{asset}":\{{"combinations":\{{[^}}]*?"index":([\d.]+)'
        match = re.search(pattern, html)
        if match:
            spot_prices[asset] = float(match.group(1))

    return spot_prices


def calculate_iv_from_apy(spot, strike, expiry, apy, is_put):
    """Calculate IV from APY using Black-Scholes."""
    import math
    from datetime import datetime

    # Parse expiry to DTE
    try:
        expiry_date = datetime.strptime(expiry, '%d%b%y')
        now = datetime.utcnow()
        dte = max(1, (expiry_date - now).days)
    except:
        return None

    if dte <= 0 or apy <= 0 or spot <= 0 or strike <= 0:
        return None

    T = dte / 365.0
    r = 0.05  # Risk-free rate

    # Collateral and premium from APY
    collateral = strike if is_put else spot
    premium = (apy / 100.0) * collateral * (dte / 365.0)

    # Newton-Raphson to find IV
    sigma = 0.5  # Initial guess
    converged = False

    # Use relative tolerance for small premiums
    tol = max(0.0000001, premium * 0.001)  # 0.1% of premium or 1e-7

    for _ in range(100):
        try:
            d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)

            # Standard normal CDF approximation
            def norm_cdf(x):
                return 0.5 * (1 + math.erf(x / math.sqrt(2)))

            if is_put:
                price = strike * math.exp(-r * T) * norm_cdf(-d2) - spot * norm_cdf(-d1)
            else:
                price = spot * norm_cdf(d1) - strike * math.exp(-r * T) * norm_cdf(d2)

            diff = price - premium

            if abs(diff) < tol:
                converged = True
                break

            # Vega (use relative threshold too)
            vega = spot * math.sqrt(T) * math.exp(-d1 ** 2 / 2) / math.sqrt(2 * math.pi)
            if vega < tol * 0.01:
                break

            sigma = sigma - diff / vega
            sigma = max(0.01, min(5.0, sigma))
        except (ValueError, ZeroDivisionError):
            break

    # Only return IV if converged and within reasonable bounds
    if converged and 0.05 < sigma < 4.0:
        return sigma * 100
    return None


def save_records(records):
    """Save records to database."""
    conn = get_db()
    cursor = conn.cursor()
    timestamp = datetime.utcnow()

    for r in records:
        cursor.execute("""
            INSERT INTO iv_snapshots (timestamp, asset, strike, expiry, bid_iv, ask_iv, mid_iv, option_type, apy)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (timestamp, r['asset'], r['strike'], r['expiry'], r['bid_iv'], r['ask_iv'], r['mid_iv'], r['option_type'], r['apy']))

    conn.commit()
    conn.close()


# For local development
if __name__ == '__main__':
    app.run(debug=True, port=3000)
