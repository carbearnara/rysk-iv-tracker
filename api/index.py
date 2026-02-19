"""
Vercel Serverless API for Rysk IV Tracker

Handles all routes including dashboard, API endpoints, and cron jobs.
Uses PostgreSQL (Supabase) for data storage.
"""

import os
import json
import re
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template_string, make_response
import requests

# Database connection
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)


def cached_json(data, max_age=60, stale_revalidate=300, cdn_max_age=None):
    """Return a JSON response with Cache-Control headers to reduce Fast Origin Transfer."""
    resp = make_response(jsonify(data))
    resp.headers['Cache-Control'] = f'public, max-age={max_age}, stale-while-revalidate={stale_revalidate}'
    cdn = cdn_max_age or max_age * 2
    resp.headers['Vercel-CDN-Cache-Control'] = f'public, max-age={cdn}, stale-while-revalidate={stale_revalidate}'
    return resp


def cached_html(html_str, max_age=300, stale_revalidate=3600):
    """Return an HTML response with Cache-Control headers."""
    resp = make_response(render_template_string(html_str))
    resp.headers['Cache-Control'] = f'public, max-age={max_age}, stale-while-revalidate={stale_revalidate}'
    resp.headers['Vercel-CDN-Cache-Control'] = f'public, max-age={max_age * 2}, stale-while-revalidate={stale_revalidate}'
    return resp


# Configuration
DATABASE_URL = os.environ.get('DATABASE_URL', '')
CRON_SECRET = os.environ.get('CRON_SECRET', '')

# On-Chain Activity Configuration
HYPERVM_RPC = 'https://rpc.hyperliquid.xyz/evm'
CONTROLLER_CONTRACT = '0x577b846A95711015769452F7f29d8054Cf087964'
OTOKEN_FACTORY = '0xD8eB81D7D31b420b435Cb3C61a8B4E7805e12Eff'
RYSK_MARGIN_POOL = '0x691a5fc3a81a144e36c6C4fBCa1fC82843c80d0d'
FEE_RECIPIENT = '0xFb69f38Eae27705720Eb4AABB04be9edbec5B555'

# Event topic hashes (keccak256 of event signatures)
TOPIC_OTOKEN_CREATED = '0xddd3483766ccee42359dad67a40f9b28d76f9d433dc39bca1474b98e065038e5'
TOPIC_SHORT_OTOKEN_MINTED = '0x4d7f96086c92b2f9a254ad21548b1c1f2d99502c7949508866349b96bb1a8d8a'
TOPIC_COLLATERAL_DEPOSITED = '0xbfab88b861f171b7db714f00e5966131253918d55ddba816c3eb94657d102390'
TOPIC_TRANSFER_TO_USER = '0x60a0dc9b39e897fcb3abc1fbd47021fe4df80b4bbc379d3ebd3a5dd756895a14'

# Known token addresses on HyperEVM (lowercase for comparison)
TOKEN_INFO = {
    '0xb88339cb7199b77e23db6e890353e22632ba630f': {'symbol': 'USDC', 'decimals': 6},
    '0xb8ce59fc3717ada4c02eadf9682a9e934f625ebb': {'symbol': 'WHYPE', 'decimals': 6},
    '0x9fdbda0a5e284c32744d2f17ee5c74b284993463': {'symbol': 'UBTC', 'decimals': 8},
    '0xfd739d4e423301ce9385c1fb8850539d657c296d': {'symbol': 'kHYPE', 'decimals': 18},
}

# Underlying token address -> tracked asset name
UNDERLYING_TO_ASSET = {
    '0xb8ce59fc3717ada4c02eadf9682a9e934f625ebb': 'HYPE',
    '0x5555555555555555555555555555555555555555': 'HYPE',  # synthetic native HYPE
    '0x9fdbda0a5e284c32744d2f17ee5c74b284993463': 'BTC',
}

LOGS_BLOCK_RANGE = 1000
MAX_BLOCKS_PER_CRON = 50000
ACTIVITY_START_BLOCK = int(os.environ.get('ACTIVITY_START_BLOCK', '27000000'))
INDEXER_TIME_BUDGET = 8  # seconds - stop before Vercel 10s timeout

# Dashboard HTML template (embedded for serverless)
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rysk IV Tracker</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🍪</text></svg>">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; padding-bottom: 20px; border-bottom: 1px solid #30363d; flex-wrap: wrap; gap: 15px; }
        h1 { font-size: 24px; color: #58a6ff; margin-bottom: 4px; }
        .subtitle { font-size: 14px; color: #8b949e; font-style: italic; }
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
        .chart-container-large { position: relative; height: 450px; }
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
        .asset-card { background: #21262d; border-radius: 8px; padding: 15px; text-align: center; cursor: pointer; transition: background 0.2s, transform 0.1s, border 0.2s; border: 2px solid transparent; }
        .asset-card:hover { background: #30363d; transform: translateY(-2px); }
        .asset-card.selected { border-color: #58a6ff; background: #1a2332; }
        .asset-card .asset-name { font-size: 18px; font-weight: bold; color: #c9d1d9; margin-bottom: 8px; }
        .asset-card .asset-pricing { font-size: 14px; font-weight: bold; padding: 4px 8px; border-radius: 4px; }
        .asset-card .asset-pricing.expensive { background: rgba(248,81,73,0.2); color: #f85149; }
        .asset-card .asset-pricing.cheap { background: rgba(63,185,80,0.2); color: #3fb950; }
        .asset-card .asset-pricing.fair { background: rgba(139,148,158,0.2); color: #8b949e; }
        .asset-card .asset-pricing.inactive { background: rgba(72,79,88,0.2); color: #484f58; }
        .asset-card.inactive { opacity: 0.6; }
        .asset-card .asset-pct { font-size: 11px; color: #8b949e; margin-top: 6px; }
        .loading { display: flex; align-items: center; justify-content: center; height: 200px; color: #8b949e; }
        .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 20px; }
        .stat { background: #21262d; padding: 15px; border-radius: 6px; text-align: center; }
        .stat-value { font-size: 24px; font-weight: bold; color: #58a6ff; }
        .stat-label { font-size: 12px; color: #8b949e; margin-top: 5px; }
        @media (max-width: 600px) {
            .stats { grid-template-columns: repeat(3, 1fr); gap: 10px; }
            .stat { padding: 10px; }
            .stat-value { font-size: 18px; }
            header { flex-direction: column; align-items: flex-start; }
            .title-section { width: 100%; }
            .controls { width: 100%; justify-content: space-between; }
            .card { padding: 15px; }
            .card h2 { font-size: 14px; }
            .aggregate-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; }
            .asset-card { padding: 10px; }
            .asset-card .asset-name { font-size: 14px; }
            .asset-card .asset-pricing { font-size: 12px; }
            body { padding: 10px; }
            table { font-size: 11px; }
            th, td { padding: 6px 4px; }
        }
        .btn-secondary { background: #21262d; border: 1px solid #30363d; }
        .btn-secondary:hover { background: #30363d; }
        .toggle-container { display: flex; border: 1px solid #30363d; border-radius: 6px; overflow: hidden; }
        .toggle-btn { padding: 8px 16px; border: none; background: #161b22; color: #8b949e; font-size: 14px; cursor: pointer; transition: all 0.2s; }
        .toggle-btn:hover { background: #21262d; color: #c9d1d9; }
        .toggle-btn.active { background: #238636; color: white; }
        .toggle-btn:first-child { border-right: 1px solid #30363d; }
        .collapsible-header { display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
        .collapsible-header h2 { margin-bottom: 0; }
        .collapse-toggle { background: none; border: none; color: #8b949e; font-size: 20px; cursor: pointer; transition: transform 0.2s; }
        .collapse-toggle:hover { color: #c9d1d9; background: none; }
        .collapse-toggle.collapsed { transform: rotate(-90deg); }
        .collapsible-content { overflow: hidden; transition: max-height 0.3s ease-out; }
        .collapsible-content.collapsed { max-height: 0 !important; }
        footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #30363d; text-align: center; color: #8b949e; font-size: 13px; }
        footer a { color: #58a6ff; text-decoration: none; }
        footer a:hover { text-decoration: underline; }
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 1000; justify-content: center; align-items: center; }
        .modal-overlay.active { display: flex; }
        .modal { background: #161b22; border: 1px solid #30363d; border-radius: 12px; max-width: 600px; width: 90%; max-height: 80vh; overflow-y: auto; padding: 24px; }
        .modal h2 { color: #58a6ff; margin-bottom: 16px; font-size: 20px; }
        .modal h3 { color: #c9d1d9; margin: 16px 0 8px 0; font-size: 16px; }
        .modal p { color: #8b949e; line-height: 1.6; margin-bottom: 12px; font-size: 14px; }
        .modal ul { color: #8b949e; margin-left: 20px; margin-bottom: 12px; font-size: 14px; line-height: 1.6; }
        .modal code { background: #21262d; padding: 2px 6px; border-radius: 4px; font-size: 13px; color: #f0883e; }
        .modal-close { float: right; background: none; border: none; color: #8b949e; font-size: 24px; cursor: pointer; padding: 0; line-height: 1; }
        .modal-close:hover { color: #c9d1d9; background: none; }
        .signals-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 20px; }
        .signals-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
        .signals-header h2 { margin: 0; font-size: 16px; color: #8b949e; }
        .signal-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
        .signal-item { background: #21262d; border-radius: 8px; padding: 12px 15px; display: flex; justify-content: space-between; align-items: center; }
        .signal-info { display: flex; flex-direction: column; gap: 4px; }
        .signal-option { font-weight: 600; color: #c9d1d9; font-size: 14px; }
        .signal-details { font-size: 12px; color: #8b949e; }
        .signal-badge { padding: 6px 12px; border-radius: 6px; font-weight: 600; font-size: 12px; text-transform: uppercase; }
        .signal-badge.buy { background: rgba(63,185,80,0.2); color: #3fb950; }
        .signal-badge.sell { background: rgba(248,81,73,0.2); color: #f85149; }
        .signal-badge.neutral { background: rgba(139,148,158,0.2); color: #8b949e; }
        .signal-pct { font-size: 11px; color: #8b949e; margin-top: 2px; }
        .signal-winrate { font-size: 10px; opacity: 0.8; }
        .info-icon { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 50%; background: #30363d; color: #8b949e; font-size: 11px; font-weight: bold; cursor: help; margin-left: 8px; position: relative; vertical-align: middle; }
        .info-icon:hover { background: #58a6ff; color: #0d1117; }
        .info-tooltip { display: none; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); width: 280px; padding: 12px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; font-size: 12px; font-weight: normal; color: #c9d1d9; line-height: 1.5; z-index: 100; margin-bottom: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); }
        .info-icon:hover .info-tooltip { display: block; }
        .info-tooltip::after { content: ''; position: absolute; top: 100%; left: 50%; transform: translateX(-50%); border: 6px solid transparent; border-top-color: #30363d; }
        .info-tooltip strong { color: #58a6ff; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-section" style="display: flex; align-items: center; gap: 15px;">
                <div>
                    <h1>Rysk IV Tracker</h1>
                    <p class="subtitle">You gotta Rysk it IV the Biscuit</p>
                </div>
                <button class="btn-secondary" onclick="openModal()">Methodology</button>
                <a href="/activity" style="padding: 8px 16px; border: 1px solid #30363d; border-radius: 6px; background: #21262d; color: #c9d1d9; text-decoration: none; font-size: 14px;">On-Chain Activity</a>
            </div>
            <div class="controls">
                <div class="control-group">
                    <label>Display Mode</label>
                    <div class="toggle-container">
                        <button id="mode-iv" class="toggle-btn active" onclick="setDisplayMode('iv')">IV</button>
                        <button id="mode-apr" class="toggle-btn" onclick="setDisplayMode('apr')">APR</button>
                        <button id="mode-svt" class="toggle-btn" onclick="setDisplayMode('svt')">σ√T</button>
                    </div>
                </div>
                <div class="control-group">
                    <label>Show data from previous</label>
                    <select id="days-select">
                        <option value="1">1 day</option>
                        <option value="7" selected>7 days</option>
                        <option value="30">30 days</option>
                    </select>
                </div>
            </div>
            <select id="asset-select" style="display:none;"><option>Loading...</option></select>
        </header>
        <div class="card" style="margin-bottom: 20px;">
            <h2>Market Pricing Overview</h2>
            <div id="aggregate-pricing" class="aggregate-grid"><div class="loading">Loading...</div></div>
        </div>
        <div class="stats">
            <div class="stat"><div class="stat-value" id="stat-records">-</div><div class="stat-label">Records</div></div>
            <div class="stat"><div class="stat-value" id="stat-avg-iv">-</div><div class="stat-label" id="stat-avg-label">Avg IV</div></div>
            <div class="stat"><div class="stat-value" id="stat-updated">-</div><div class="stat-label">Last Update</div></div>
        </div>
        <div class="card" style="margin-bottom: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <h2 style="margin: 0;"><span id="iv-chart-title">IV Over Time</span><span id="chart-info-icon" class="info-icon" style="display:none;">i<span class="info-tooltip" id="chart-info-tooltip"></span></span></h2>
                <div style="display: flex; gap: 16px; align-items: center;">
                    <label style="display: flex; align-items: center; gap: 6px; font-size: 13px; color: #8b949e; cursor: pointer;">
                        <input type="checkbox" id="show-spot-toggle" onchange="toggleSpotOverlay()" style="cursor: pointer;">
                        Show Spot Price
                    </label>
                    <label style="display: flex; align-items: center; gap: 6px; font-size: 13px; color: #8b949e; cursor: pointer;">
                        <input type="checkbox" id="show-forecast-toggle" onchange="toggleForecast()" style="cursor: pointer;">
                        Show 7d Forecast
                    </label>
                </div>
            </div>
            <div class="chart-container-large"><canvas id="iv-chart"></canvas></div>
        </div>
        <div class="signals-card">
            <div class="signals-header">
                <h2>σ√T Trading Signals</h2>
                <span class="info-icon">i<span class="info-tooltip">Mean reversion signals based on σ√T percentiles.<br><br>• BUY when σ√T &lt; 10th percentile<br>• SELL when σ√T &gt; 90th percentile<br>• Only shows options with 14+ DTE<br><br>Backtested: 63% win rate holding to expiry.</span></span>
            </div>
            <div id="signals-container" class="signal-grid"><div class="loading">Analyzing...</div></div>
        </div>
        <div class="card" style="margin-bottom: 20px;">
            <h2 id="strike-chart-title">IV by Strike</h2>
            <div class="chart-container"><canvas id="strike-chart"></canvas></div>
        </div>
        <div class="card">
            <div class="collapsible-header" onclick="toggleTable()">
                <h2>Full IV Values</h2>
                <button class="collapse-toggle collapsed" id="table-toggle">&#9660;</button>
            </div>
            <div class="collapsible-content collapsed" id="table-collapsible">
                <div id="table-container"><div class="loading">Loading...</div></div>
            </div>
        </div>
        <footer>Made with love by <a href="https://x.com/0xcarnation" target="_blank">0xcarnation</a>. Powered by Claude.</footer>
    </div>
    <script>
        let ivChart = null, strikeChart = null;
        let displayMode = 'iv'; // 'iv', 'apr', or 'svt'
        let showSpotOverlay = false;
        let showForecast = false;
        let historicalSpotData = {};
        let forecastCache = {};
        const coinGeckoIds = { BTC: 'bitcoin', ETH: 'ethereum', SOL: 'solana', XRP: 'ripple', ZEC: 'zcash', HYPE: 'hyperliquid', PURR: 'purr-2', PUMP: 'pump' };
        const coveredCallOnly = ['PUMP', 'PURR', 'XRP']; // Assets with only covered calls (no puts)
        let spotPrices = {};

        function toggleSpotOverlay() {
            showSpotOverlay = document.getElementById('show-spot-toggle').checked;
            refresh();
        }

        function toggleForecast() {
            showForecast = document.getElementById('show-forecast-toggle').checked;
            updateForecastOverlay();
        }

        async function fetchForecastData(asset) {
            if (forecastCache[asset]) return forecastCache[asset];
            try {
                const resp = await fetch(`/api/forecasts/${asset}`);
                const data = await resp.json();
                if (!data.length) console.log(`No forecast data for ${asset}. Run: python forecast_runner.py --seed-test`);
                forecastCache[asset] = data;
                return data;
            } catch (e) { console.log('Failed to fetch forecasts:', e); return []; }
        }

        async function updateForecastOverlay() {
            if (!ivChart) return;
            const asset = document.getElementById('asset-select').value;
            const useSvt = displayMode === 'svt';
            const useApr = displayMode === 'apr';
            const spot = spotPrices[asset];

            // Remove existing forecast datasets
            ivChart.data.datasets = ivChart.data.datasets.filter(ds => !ds._isForecast && !ds._isForecastBand);

            if (showForecast) {
                const fcData = await fetchForecastData(asset);
                if (fcData.length > 0) {
                    const fcGroups = {};
                    fcData.forEach(f => {
                        const k = `${f.strike}-${f.expiry}`;
                        if (!fcGroups[k]) fcGroups[k] = { points: [], option_type: f.option_type };
                        fcGroups[k].points.push(f);
                    });
                    // Match against existing historical datasets on the y axis
                    const histDatasets = ivChart.data.datasets.filter(ds =>
                        ds.yAxisID === 'y' && !ds.label.startsWith('Other markets') && !ds.label.includes('Spot Price')
                    );
                    histDatasets.forEach(ds => {
                        const fc = fcGroups[ds.label];
                        if (!fc || !ds.data.length) return;
                        const sorted = [...ds.data].sort((a, b) => a.x - b.x);
                        const bridge = sorted[sorted.length - 1];
                        const color = ds.borderColor;
                        const isPut = (fc.option_type || '').toLowerCase() === 'put';
                        const strike = fc.points[0] ? parseFloat(fc.points[0].strike) : 0;
                        const fcPoints = fc.points.map(f => {
                            let val = f.forecast_mid_iv;
                            const dte = f.expiry ? calcDTE(f.expiry, f.forecast_timestamp) : 0;
                            if (useSvt && dte > 0) {
                                val = calcSigmaRootT(f.forecast_mid_iv, dte);
                            } else if (useApr && spot && dte > 0) {
                                val = calcAprFromIV(f.forecast_mid_iv, strike, spot, dte, isPut);
                            }
                            let q10 = f.quantile_10, q90 = f.quantile_90;
                            if (useSvt && dte > 0) {
                                q10 = q10 != null ? calcSigmaRootT(q10, dte) : null;
                                q90 = q90 != null ? calcSigmaRootT(q90, dte) : null;
                            } else if (useApr && spot && dte > 0) {
                                q10 = q10 != null ? calcAprFromIV(q10, strike, spot, dte, isPut) : null;
                                q90 = q90 != null ? calcAprFromIV(q90, strike, spot, dte, isPut) : null;
                            }
                            return { x: new Date(f.forecast_timestamp), y: val, q10, q90 };
                        }).filter(p => p.y != null && !isNaN(p.y) && isFinite(p.y));
                        if (!fcPoints.length) return;
                        ivChart.data.datasets.push({
                            label: `${ds.label} forecast`,
                            data: [{ x: bridge.x, y: bridge.y }, ...fcPoints],
                            borderColor: color, backgroundColor: 'transparent',
                            borderWidth: 2, borderDash: [6, 4], pointRadius: 0, pointHoverRadius: 3,
                            tension: 0.1, yAxisID: 'y', _isForecast: true,
                        });
                        const bandUpper = [{ x: bridge.x, y: bridge.y }];
                        const bandLower = [{ x: bridge.x, y: bridge.y }];
                        fcPoints.forEach(p => {
                            if (p.q10 != null && p.q90 != null) {
                                bandUpper.push({ x: p.x, y: p.q90 }); bandLower.push({ x: p.x, y: p.q10 });
                            }
                        });
                        if (bandUpper.length > 1) {
                            ivChart.data.datasets.push({
                                label: `${ds.label} q90`, data: bandUpper,
                                borderColor: 'transparent', backgroundColor: color + '20',
                                borderWidth: 0, pointRadius: 0, fill: false, tension: 0.1, yAxisID: 'y', _isForecastBand: true,
                            });
                            ivChart.data.datasets.push({
                                label: `${ds.label} q10`, data: bandLower,
                                borderColor: 'transparent', backgroundColor: color + '20',
                                borderWidth: 0, pointRadius: 0, fill: '-1', tension: 0.1, yAxisID: 'y', _isForecastBand: true,
                            });
                        }
                    });
                }
            }
            ivChart.update();
        }

        async function fetchHistoricalSpot(asset, days) {
            const geckoId = coinGeckoIds[asset];
            if (!geckoId) return [];
            const cacheKey = `${asset}-${days}`;
            if (historicalSpotData[cacheKey]) return historicalSpotData[cacheKey];
            try {
                const resp = await fetch(`https://api.coingecko.com/api/v3/coins/${geckoId}/market_chart?vs_currency=usd&days=${days}`);
                const data = await resp.json();
                if (data.prices) {
                    historicalSpotData[cacheKey] = data.prices.map(([ts, price]) => ({ x: new Date(ts), y: price }));
                    return historicalSpotData[cacheKey];
                }
            } catch (e) { console.log('Failed to fetch historical spot:', e); }
            return [];
        }
        function setDisplayMode(mode) {
            displayMode = mode;
            document.getElementById('mode-iv').classList.toggle('active', mode === 'iv');
            document.getElementById('mode-apr').classList.toggle('active', mode === 'apr');
            document.getElementById('mode-svt').classList.toggle('active', mode === 'svt');
            refresh();
        }
        function calcDTE(expiry, timestamp) {
            // Parse expiry like "13FEB26" to date
            const months = {JAN:0,FEB:1,MAR:2,APR:3,MAY:4,JUN:5,JUL:6,AUG:7,SEP:8,OCT:9,NOV:10,DEC:11};
            const day = parseInt(expiry.slice(0,2));
            const mon = months[expiry.slice(2,5).toUpperCase()];
            const yr = 2000 + parseInt(expiry.slice(5,7));
            const expiryDate = new Date(yr, mon, day);
            const dataDate = new Date(timestamp);
            const dte = Math.max(0, (expiryDate - dataDate) / (1000 * 60 * 60 * 24));
            return dte;
        }
        function calcSigmaRootT(iv, dte) {
            if (!dte || dte <= 0 || isNaN(dte) || !iv || isNaN(iv)) return 0;
            return iv * Math.sqrt(dte / 365);
        }
        function calcAprFromIV(iv, strike, spot, dte, isPut) {
            // Black-Scholes forward pricing: IV -> option premium -> APR
            if (!iv || !strike || !spot || !dte || dte <= 0 || spot <= 0 || strike <= 0) return null;
            const sigma = iv / 100;
            const T = dte / 365;
            const r = 0.05;
            const d1 = (Math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
            const d2 = d1 - sigma * Math.sqrt(T);
            // Standard normal CDF via error function approximation
            function normCDF(x) { return 0.5 * (1 + erf(x / Math.SQRT2)); }
            function erf(x) {
                const a1=0.254829592, a2=-0.284496736, a3=1.421413741, a4=-1.453152027, a5=1.061405429, p=0.3275911;
                const sign = x < 0 ? -1 : 1; x = Math.abs(x);
                const t = 1 / (1 + p * x);
                return sign * (1 - (((((a5*t+a4)*t)+a3)*t+a2)*t+a1)*t*Math.exp(-x*x));
            }
            let premium;
            if (isPut) {
                premium = strike * Math.exp(-r * T) * normCDF(-d2) - spot * normCDF(-d1);
            } else {
                premium = spot * normCDF(d1) - strike * Math.exp(-r * T) * normCDF(d2);
            }
            if (premium <= 0) return null;
            const collateral = isPut ? strike : spot;
            return (premium / collateral) * (365 / dte) * 100;
        }
        async function updateSignals(asset) {
            const container = document.getElementById('signals-container');
            try {
                // Fetch 7 days of data for percentile calculation
                const ivData = await (await fetch(`/api/iv/${asset}?days=7`)).json();
                if (!ivData.length) {
                    container.innerHTML = '<div class="signal-item"><span class="signal-details">No data available</span></div>';
                    return;
                }
                // Group by option (strike-expiry)
                const options = {};
                ivData.forEach(d => {
                    if (!d.mid_iv || !d.expiry) return;
                    const key = `${d.strike}-${d.expiry}`;
                    const dte = calcDTE(d.expiry, d.timestamp);
                    const srt = calcSigmaRootT(d.mid_iv, dte);
                    if (srt > 0 && dte > 0) {
                        if (!options[key]) options[key] = { points: [], strike: d.strike, expiry: d.expiry, type: d.option_type };
                        options[key].points.push({ srt, iv: d.mid_iv, dte, timestamp: d.timestamp });
                    }
                });
                // Calculate percentile for latest point of each option
                const signals = [];
                for (const [key, opt] of Object.entries(options)) {
                    if (opt.points.length < 10) continue; // Need enough history
                    const sorted = [...opt.points].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
                    const latest = sorted[sorted.length - 1];
                    const historical = sorted.slice(0, -1).map(p => p.srt);
                    if (historical.length < 5) continue;
                    // Calculate percentile
                    const below = historical.filter(v => v < latest.srt).length;
                    const pct = (below / historical.length) * 100;
                    // Generate signal if extreme AND has enough DTE (14+ days works best)
                    if (latest.dte < 7) continue; // Skip short-dated options
                    const isLongDated = latest.dte >= 14;
                    if (pct <= 10) {
                        signals.push({ key, ...opt, latestSrt: latest.srt, latestIv: latest.iv, dte: latest.dte, pct, signal: 'BUY', winRate: isLongDated ? 63 : 51, isLongDated });
                    } else if (pct >= 90) {
                        signals.push({ key, ...opt, latestSrt: latest.srt, latestIv: latest.iv, dte: latest.dte, pct, signal: 'SELL', winRate: isLongDated ? 63 : 51, isLongDated });
                    }
                }
                // Sort by: long-dated first, then SELL before BUY, then by extremity
                signals.sort((a, b) => {
                    if (a.isLongDated !== b.isLongDated) return a.isLongDated ? -1 : 1;
                    if (a.signal === 'SELL' && b.signal === 'BUY') return -1;
                    if (a.signal === 'BUY' && b.signal === 'SELL') return 1;
                    return a.signal === 'SELL' ? b.pct - a.pct : a.pct - b.pct;
                });
                if (signals.length === 0) {
                    container.innerHTML = '<div class="signal-item"><span class="signal-details">No extreme signals for ' + asset + ' - σ√T values are within normal range</span></div>';
                    return;
                }
                const isCoveredCallOnly = coveredCallOnly.includes(asset);
                container.innerHTML = signals.slice(0, 6).map(s => {
                    const typeLabel = s.type === 'call' ? 'Call' : s.type === 'put' ? 'Put' : 'Option';
                    const signalLabel = s.signal === 'SELL' ? (s.type === 'call' ? 'Sell Call' : 'Sell Put') : (s.type === 'call' ? 'Buy Call' : 'Buy Put');
                    return `
                    <div class="signal-item" style="${s.isLongDated ? 'border-left: 3px solid #3fb950;' : ''}">
                        <div class="signal-info">
                            <span class="signal-option">${asset} ${s.strike} ${s.expiry}${s.isLongDated ? ' ★' : ''}</span>
                            <span class="signal-details">${typeLabel} · ${s.dte.toFixed(0)}d DTE · IV: ${s.latestIv.toFixed(1)}%</span>
                            <span class="signal-pct">σ√T: ${s.latestSrt.toFixed(2)} (${s.pct.toFixed(0)}th pctl)</span>
                        </div>
                        <div>
                            <div class="signal-badge ${s.signal.toLowerCase()}">${signalLabel}</div>
                            <div class="signal-winrate">${s.winRate}% win rate</div>
                        </div>
                    </div>
                `}).join('');
            } catch (e) {
                console.error('Signal error:', e);
                container.innerHTML = '<div class="signal-item"><span class="signal-details">Error calculating signals</span></div>';
            }
        }
        async function fetchSpotPrices() {
            try {
                const ids = Object.values(coinGeckoIds).join(',');
                const resp = await fetch(`https://api.coingecko.com/api/v3/simple/price?ids=${ids}&vs_currencies=usd`);
                const data = await resp.json();
                for (const [symbol, geckoId] of Object.entries(coinGeckoIds)) {
                    if (data[geckoId]) spotPrices[symbol] = data[geckoId].usd;
                }
            } catch (e) { console.log('Failed to fetch spot prices:', e); }
        }
        function toggleTable() {
            const content = document.getElementById('table-collapsible');
            const toggle = document.getElementById('table-toggle');
            content.classList.toggle('collapsed');
            toggle.classList.toggle('collapsed');
        }
        function selectAsset(asset) {
            document.getElementById('asset-select').value = asset;
            refresh();
        }
        async function init() {
            await fetchSpotPrices();
            const assets = await (await fetch('/api/assets')).json();
            const sel = document.getElementById('asset-select');
            sel.innerHTML = assets.map(a => `<option value="${a}">${a}</option>`).join('');
            sel.onchange = refresh;
            document.getElementById('days-select').onchange = refresh;
            await updateAggregatePricing();
            refresh();
            // Auto-refresh every 5 minutes
            setInterval(() => {
                refresh();
                fetchSpotPrices();
            }, 5 * 60 * 1000);
        }
        async function updateAggregatePricing() {
            const allData = await (await fetch('/api/latest')).json();
            const byAsset = {};
            const now = new Date();
            const oneDayAgo = new Date(now - 24 * 60 * 60 * 1000);
            allData.forEach(d => {
                if (!byAsset[d.asset]) byAsset[d.asset] = { pcts: [], latestTime: null };
                if (d.iv_percentile !== null) byAsset[d.asset].pcts.push(d.iv_percentile);
                const ts = new Date(d.timestamp);
                if (!byAsset[d.asset].latestTime || ts > byAsset[d.asset].latestTime) byAsset[d.asset].latestTime = ts;
            });
            const container = document.getElementById('aggregate-pricing');
            const cards = Object.entries(byAsset).map(([asset, { pcts, latestTime }]) => {
                const isActive = latestTime && latestTime > oneDayAgo;
                const isCCOnly = coveredCallOnly.includes(asset);
                const ccBadge = isCCOnly ? '<span style="font-size:10px;background:#30363d;padding:2px 5px;border-radius:3px;margin-left:5px;">CC</span>' : '';
                if (!isActive) return `<div class="asset-card inactive" onclick="selectAsset('${asset}')"><div class="asset-name">${asset}${ccBadge}</div><div class="asset-pricing inactive">NOT ACTIVE</div></div>`;
                if (pcts.length === 0) return `<div class="asset-card" onclick="selectAsset('${asset}')"><div class="asset-name">${asset}${ccBadge}</div><div class="asset-pricing fair">NO DATA</div></div>`;
                const avgPct = pcts.reduce((a,b) => a+b, 0) / pcts.length;
                let pricing, pricingClass;
                if (avgPct >= 75) { pricing = 'EXPENSIVE'; pricingClass = 'expensive'; }
                else if (avgPct <= 25) { pricing = 'CHEAP'; pricingClass = 'cheap'; }
                else { pricing = 'FAIR'; pricingClass = 'fair'; }
                return `<div class="asset-card" onclick="selectAsset('${asset}')"><div class="asset-name">${asset}${ccBadge}</div><div class="asset-pricing ${pricingClass}">${pricing}</div><div class="asset-pct">Avg ${avgPct.toFixed(0)}th percentile</div></div>`;
            });
            container.innerHTML = cards.join('');
        }
        async function refresh() {
            try {
                const asset = document.getElementById('asset-select').value;
                if (!asset) { console.log('No asset selected'); return; }
                const days = document.getElementById('days-select').value;
                const modeLabel = displayMode === 'apr' ? 'APR' : displayMode === 'svt' ? 'σ√T' : 'IV';
                document.getElementById('iv-chart-title').textContent = `${asset} ${modeLabel} Over Time`;
                document.getElementById('strike-chart-title').textContent = `${asset} ${modeLabel} by Strike`;
                const infoIcon = document.getElementById('chart-info-icon');
                const infoTooltip = document.getElementById('chart-info-tooltip');
                if (infoIcon && infoTooltip) {
                    infoIcon.style.display = 'inline-flex';
                    if (displayMode === 'svt') {
                        infoTooltip.innerHTML = '<strong>σ√T = IV × √(DTE/365)</strong><br><br>Shows premium direction over time:<br>• <strong>Rising ↑</strong> = Premium increasing (IV beating time decay)<br>• <strong>Falling ↓</strong> = Premium decreasing (theta winning)<br>• <strong>Flat →</strong> = IV and time decay balanced';
                    } else if (displayMode === 'apr') {
                        infoTooltip.innerHTML = '<strong>APR (Annual Percentage Rate)</strong><br><br>The annualized return if you sell this option:<br>• Higher APR = more premium income<br>• APR decreases as option nears expiry<br>• Compare to σ√T mode to see premium direction';
                    } else {
                        infoTooltip.innerHTML = '<strong>IV (Implied Volatility)</strong><br><br>The market expected price movement:<br>• Higher IV = larger expected moves<br>• IV typically rises before events<br>• Use σ√T mode to see premium impact';
                    }
                }
                const [ivRes, latestRes] = await Promise.all([
                    fetch(`/api/iv/${asset}?days=${days}`),
                    fetch(`/api/latest?asset=${asset}`)
                ]);
                const [ivData, latestData] = await Promise.all([ivRes.json(), latestRes.json()]);
                await updateAggregatePricing();
                updateSignals(asset);
            document.querySelectorAll('.asset-card').forEach(card => {
                card.classList.toggle('selected', card.querySelector('.asset-name').textContent === asset);
            });
            document.getElementById('stat-records').textContent = ivData.length;
            document.getElementById('stat-avg-label').textContent = displayMode === 'apr' ? 'Avg APR' : displayMode === 'svt' ? 'Avg σ√T' : 'Avg IV';
            if (ivData.length > 0) {
                let avgValue;
                if (displayMode === 'svt') {
                    const srtValues = ivData.filter(d => d.mid_iv != null && d.expiry).map(d => calcSigmaRootT(d.mid_iv, calcDTE(d.expiry, d.timestamp))).filter(v => !isNaN(v) && isFinite(v));
                    avgValue = srtValues.length > 0 ? srtValues.reduce((s, v) => s + v, 0) / srtValues.length : 0;
                } else {
                    const valueKey = displayMode === 'apr' ? 'apy' : 'mid_iv';
                    const validData = ivData.filter(d => d[valueKey] != null);
                    avgValue = validData.length > 0 ? validData.reduce((s,d) => s + d[valueKey], 0) / validData.length : 0;
                }
                document.getElementById('stat-avg-iv').textContent = avgValue.toFixed(1) + (displayMode === 'svt' ? '' : '%');
                document.getElementById('stat-updated').textContent = new Date(ivData[ivData.length-1].timestamp).toLocaleTimeString();
            }
            await updateCharts(ivData, latestData, asset);
            updateTable(latestData);
            } catch (e) { console.error('Refresh error:', e); }
        }
        async function updateCharts(ivData, latestData, asset) {
            const useApr = displayMode === 'apr';
            const useSvt = displayMode === 'svt';
            const yAxisLabel = useApr ? 'APR %' : useSvt ? 'σ√T' : 'IV %';

            const ctx1 = document.getElementById('iv-chart').getContext('2d');
            const groups = {};
            ivData.forEach(d => {
                let val;
                if (useSvt) {
                    if (d.mid_iv != null && d.expiry) {
                        const dte = calcDTE(d.expiry, d.timestamp);
                        val = calcSigmaRootT(d.mid_iv, dte);
                    }
                } else {
                    val = useApr ? d.apy : d.mid_iv;
                }
                if (val != null && !isNaN(val) && isFinite(val) && val > 0) {
                    const k = `${d.strike}-${d.expiry}`;
                    if (!groups[k]) groups[k] = [];
                    groups[k].push({...d, displayValue: val});
                }
            });
            // Sort by recency first (markets with recent quotes), then by max value
            const now = new Date();
            const sorted = Object.entries(groups).map(([k, pts]) => {
                const latestTime = Math.max(...pts.map(p => new Date(p.timestamp).getTime()));
                const hoursAgo = (now - latestTime) / (1000 * 60 * 60);
                const isRecent = hoursAgo < 24;
                return { k, pts, maxVal: Math.max(...pts.map(p => p.displayValue)), strike: parseFloat(k.split('-')[0]), latestTime, isRecent };
            }).sort((a, b) => {
                // Prioritize recent markets, then sort by max value
                if (a.isRecent !== b.isRecent) return a.isRecent ? -1 : 1;
                return b.maxVal - a.maxVal;
            });
            const top10 = sorted.slice(0, 10).sort((a, b) => a.strike - b.strike);
            const othersCount = sorted.length - 10;
            const colors = ['#58a6ff','#3fb950','#f85149','#a371f7','#f0883e','#79c0ff','#56d364','#ff7b72','#d2a8ff','#ffa657'];
            const datasets = top10.map(({k, pts}, i) => ({
                label: k, data: pts.map(p => ({x: new Date(p.timestamp), y: p.displayValue})),
                borderColor: colors[i], backgroundColor: colors[i],
                borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.1
            }));
            // Add "other markets" as a single combined indicator if there are more
            if (othersCount > 0) {
                const otherData = sorted.slice(10).flatMap(({pts}) => pts.map(p => ({x: new Date(p.timestamp), y: p.displayValue})));
                datasets.push({ label: `Other markets (${othersCount})`, data: otherData, borderColor: '#6e7681', backgroundColor: '#6e7681', borderWidth: 1, pointRadius: 0, pointHoverRadius: 2, tension: 0.1, hidden: true, yAxisID: 'y' });
            }
            // Add spot price overlay if enabled
            let scales = {
                x: { type: 'time', time: { unit: 'day', displayFormats: { day: 'MMM d' } }, title: { display: true, text: 'Date', color: '#8b949e' }, grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                y: { position: 'left', title: { display: true, text: yAxisLabel, color: '#8b949e' }, grid: { color: '#21262d' }, ticks: { color: '#8b949e' } }
            };
            if (showSpotOverlay) {
                const days = document.getElementById('days-select').value;
                const spotData = await fetchHistoricalSpot(asset, days);
                if (spotData.length > 0) {
                    datasets.unshift({ label: `${asset} Spot Price`, data: spotData, borderColor: '#f0883e', backgroundColor: 'rgba(240,136,62,0.1)', borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.1, fill: true, yAxisID: 'y1' });
                    scales.y1 = { position: 'right', title: { display: true, text: 'Spot Price ($)', color: '#f0883e' }, grid: { drawOnChartArea: false }, ticks: { color: '#f0883e' } };
                }
            }
            // Add forecast overlay if enabled
            if (showForecast) {
                const fcData = await fetchForecastData(asset);
                if (fcData.length > 0) {
                    // Group forecasts by strike-expiry
                    const fcGroups = {};
                    fcData.forEach(f => {
                        const k = `${f.strike}-${f.expiry}`;
                        if (!fcGroups[k]) fcGroups[k] = { points: [], option_type: f.option_type };
                        fcGroups[k].points.push(f);
                    });
                    // For each top-10 historical line, add matching forecast
                    const spot = spotPrices[asset];
                    top10.forEach(({k, pts}, i) => {
                        const fc = fcGroups[k];
                        if (!fc) return;
                        // Get last historical point as bridge
                        const lastHistPt = pts.sort((a,b) => new Date(a.timestamp) - new Date(b.timestamp));
                        const bridge = lastHistPt[lastHistPt.length - 1];
                        const bridgeVal = bridge.displayValue;
                        const bridgeTime = new Date(bridge.timestamp);
                        const isPut = (fc.option_type || '').toLowerCase() === 'put';
                        const strike = fc.points[0] ? parseFloat(fc.points[0].strike) : 0;
                        // Transform forecast values based on display mode
                        const fcPoints = fc.points.map(f => {
                            let val = f.forecast_mid_iv;
                            const dte = f.expiry ? calcDTE(f.expiry, f.forecast_timestamp) : 0;
                            if (useSvt && dte > 0) {
                                val = calcSigmaRootT(f.forecast_mid_iv, dte);
                            } else if (useApr && spot && dte > 0) {
                                val = calcAprFromIV(f.forecast_mid_iv, strike, spot, dte, isPut);
                            }
                            let q10 = f.quantile_10, q90 = f.quantile_90;
                            if (useSvt && dte > 0) {
                                q10 = q10 != null ? calcSigmaRootT(q10, dte) : null;
                                q90 = q90 != null ? calcSigmaRootT(q90, dte) : null;
                            } else if (useApr && spot && dte > 0) {
                                q10 = q10 != null ? calcAprFromIV(q10, strike, spot, dte, isPut) : null;
                                q90 = q90 != null ? calcAprFromIV(q90, strike, spot, dte, isPut) : null;
                            }
                            return { x: new Date(f.forecast_timestamp), y: val, q10, q90 };
                        }).filter(p => p.y != null && !isNaN(p.y) && isFinite(p.y));
                        if (!fcPoints.length) return;
                        // Bridge: prepend last historical point
                        const bridgedData = [{ x: bridgeTime, y: bridgeVal }, ...fcPoints];
                        // Dashed forecast line
                        datasets.push({
                            label: `${k} forecast`,
                            data: bridgedData,
                            borderColor: colors[i],
                            backgroundColor: 'transparent',
                            borderWidth: 2,
                            borderDash: [6, 4],
                            pointRadius: 0,
                            pointHoverRadius: 3,
                            tension: 0.1,
                            yAxisID: 'y',
                            _isForecast: true,
                        });
                        // Confidence band (10th-90th quantile fill)
                        const bandUpper = [{ x: bridgeTime, y: bridgeVal }];
                        const bandLower = [{ x: bridgeTime, y: bridgeVal }];
                        fcPoints.forEach(p => {
                            if (p.q10 != null && p.q90 != null) {
                                bandUpper.push({ x: p.x, y: p.q90 });
                                bandLower.push({ x: p.x, y: p.q10 });
                            }
                        });
                        if (bandUpper.length > 1) {
                            // Upper bound (invisible line, serves as fill target)
                            datasets.push({
                                label: `${k} q90`,
                                data: bandUpper,
                                borderColor: 'transparent',
                                backgroundColor: colors[i] + '20',
                                borderWidth: 0,
                                pointRadius: 0,
                                fill: false,
                                tension: 0.1,
                                yAxisID: 'y',
                                _isForecastBand: true,
                            });
                            // Lower bound with fill to previous dataset (upper)
                            datasets.push({
                                label: `${k} q10`,
                                data: bandLower,
                                borderColor: 'transparent',
                                backgroundColor: colors[i] + '20',
                                borderWidth: 0,
                                pointRadius: 0,
                                fill: '-1',
                                tension: 0.1,
                                yAxisID: 'y',
                                _isForecastBand: true,
                            });
                        }
                    });
                }
            }
            // Set yAxisID for all other datasets
            datasets.forEach(ds => { if (!ds.yAxisID) ds.yAxisID = 'y'; });
            if (ivChart) ivChart.destroy();
            ivChart = new Chart(ctx1, { type: 'line', data: { datasets }, options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'nearest', intersect: false }, scales, plugins: { legend: { position: 'bottom', labels: { color: '#8b949e', usePointStyle: true, pointStyle: 'line', font: { size: 11 }, padding: 10, boxWidth: 25, filter: function(item, chart) { const ds = chart.datasets[item.datasetIndex]; return !ds._isForecastBand; } } } } } });

            const ctx2 = document.getElementById('strike-chart').getContext('2d');
            const strikeData = {};
            latestData.forEach(d => {
                let val;
                if (useSvt) {
                    if (d.mid_iv != null && d.expiry) {
                        const dte = calcDTE(d.expiry, d.timestamp);
                        val = calcSigmaRootT(d.mid_iv, dte);
                    }
                } else {
                    val = useApr ? d.apy : d.mid_iv;
                }
                if (val != null && !isNaN(val) && isFinite(val) && val > 0) {
                    if (!strikeData[d.strike]) strikeData[d.strike] = [];
                    strikeData[d.strike].push(val);
                }
            });
            const strikes = Object.keys(strikeData).sort((a,b) => a-b);
            const avgValues = strikes.map(s => strikeData[s].reduce((a,b)=>a+b,0)/strikeData[s].length);
            const spotPrice = spotPrices[asset];
            let closestIdx = -1;
            if (spotPrice) {
                let minDiff = Infinity;
                strikes.forEach((s, i) => { const diff = Math.abs(parseFloat(s) - spotPrice); if (diff < minDiff) { minDiff = diff; closestIdx = i; } });
            }
            const barColors = strikes.map((_, i) => i === closestIdx ? '#f0883e' : '#58a6ff');
            const labels = strikes.map((s, i) => i === closestIdx ? `${s} (spot)` : s);
            if (strikeChart) strikeChart.destroy();
            strikeChart = new Chart(ctx2, { type: 'bar', data: { labels: labels, datasets: [{ label: yAxisLabel, data: avgValues, backgroundColor: barColors }] }, options: { responsive: true, maintainAspectRatio: false, scales: { x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } }, y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } } }, plugins: { legend: { display: false } } } });
        }
        function updateTable(data) {
            if (!data.length) { document.getElementById('table-container').innerHTML = '<div class="loading">No data</div>'; return; }
            const pricingClass = p => p === 'EXPENSIVE' ? 'pricing-expensive' : p === 'CHEAP' ? 'pricing-cheap' : 'pricing-fair';
            const pricingLabel = d => d.pricing ? `<span class="${pricingClass(d.pricing)}">${d.pricing}</span>${d.iv_percentile !== null ? ` <small>(${d.iv_percentile.toFixed(0)}%ile)</small>` : ''}` : '<span class="pricing-na">-</span>';
            document.getElementById('table-container').innerHTML = `<table><thead><tr><th>Asset</th><th>Strike</th><th>Expiry</th><th>Type</th><th>IV</th><th>APY</th><th>Pricing</th></tr></thead><tbody>${data.map(d => `<tr><td>${d.asset}</td><td>${d.strike}</td><td>${d.expiry}</td><td>${d.option_type||'-'}</td><td class="iv-value">${d.mid_iv?d.mid_iv.toFixed(2)+'%':'-'}</td><td>${d.apy?d.apy.toFixed(2)+'%':'-'}</td><td>${pricingLabel(d)}</td></tr>`).join('')}</tbody></table>`;
        }
        function openModal() { document.getElementById('methodology-modal').classList.add('active'); }
        function closeModal() { document.getElementById('methodology-modal').classList.remove('active'); }
        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
        init();
    </script>
    <div id="methodology-modal" class="modal-overlay" onclick="if(event.target===this)closeModal()">
        <div class="modal">
            <button class="modal-close" onclick="closeModal()">&times;</button>
            <h2>Methodology</h2>
            <p>The Rysk IV Tracker monitors implied volatility (IV) data from <a href="https://app.rysk.finance" target="_blank" style="color:#58a6ff">Rysk Finance</a> options markets on Hyperliquid.</p>

            <h3>Data Collection</h3>
            <p>The tracker scrapes option data from Rysk Finance pages, extracting:</p>
            <ul>
                <li><strong>Bid/Ask IV</strong> - Implied volatility from market quotes</li>
                <li><strong>APY</strong> - Annual percentage yield for options</li>
                <li><strong>Strike & Expiry</strong> - Contract specifications</li>
            </ul>
            <p>When explicit IV values aren't available, IV is calculated from APY using the Black-Scholes model with the current spot price.</p>

            <h3>IV Calculation</h3>
            <p>Mid IV is calculated as: <code>(Bid IV + Ask IV) / 2</code></p>
            <p>When only APY is available, we reverse-engineer IV using Black-Scholes, solving for the volatility that produces the given premium.</p>

            <h3>Pricing Indicator</h3>
            <p>Each option is labeled based on where current IV sits relative to its 7-day history:</p>
            <ul>
                <li><strong style="color:#f85149">EXPENSIVE</strong> - IV is in the top 33% (above 67th percentile)</li>
                <li><strong style="color:#3fb950">CHEAP</strong> - IV is in the bottom 33% (below 33rd percentile)</li>
                <li><strong style="color:#8b949e">FAIR</strong> - IV is in the middle range</li>
            </ul>
            <p>Requires at least 3 data points in the lookback period.</p>

            <h3>σ√T Mode</h3>
            <p>This mode shows <strong>sigma root T</strong>, calculated as:</p>
            <p><code>σ√T = IV × √(DTE / 365)</code></p>
            <p>This metric is proportional to option premium. When σ√T is <strong>rising</strong>, the option premium is increasing despite time decay—meaning IV is rising faster than theta is eroding value.</p>
            <ul>
                <li><strong>σ√T rising</strong> → Premium increasing (IV outpacing time decay)</li>
                <li><strong>σ√T falling</strong> → Premium decreasing (time decay winning)</li>
                <li><strong>σ√T flat</strong> → IV and time decay are balanced</li>
            </ul>

            <h3>Quote Asset</h3>
            <p>All options are currently tracked against <code>USDT</code> as the quote asset.</p>

            <h3>Update Frequency</h3>
            <p>Data is fetched every 15 minutes. Manual refreshes pull the latest stored data from the database.</p>
        </div>
    </div>
<script defer src="/_vercel/insights/script.js"></script>
</body>
</html>
'''

ACTIVITY_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rysk On-Chain Activity</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🍪</text></svg>">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; padding-bottom: 20px; border-bottom: 1px solid #30363d; flex-wrap: wrap; gap: 15px; }
        h1 { font-size: 24px; color: #58a6ff; margin-bottom: 4px; }
        .subtitle { font-size: 14px; color: #8b949e; font-style: italic; }
        .controls { display: flex; gap: 15px; flex-wrap: wrap; align-items: center; }
        select { padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px; background: #161b22; color: #c9d1d9; font-size: 14px; min-width: 120px; }
        .btn-secondary { padding: 8px 16px; border: 1px solid #30363d; border-radius: 6px; background: #21262d; color: #c9d1d9; text-decoration: none; font-size: 14px; cursor: pointer; }
        .btn-secondary:hover { background: #30363d; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }
        .card h2 { font-size: 16px; margin-bottom: 15px; color: #8b949e; }
        .chart-container { position: relative; height: 300px; }
        .chart-container-large { position: relative; height: 450px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }
        @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
        .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 20px; }
        .stat { background: #21262d; padding: 15px; border-radius: 6px; text-align: center; }
        .stat-value { font-size: 24px; font-weight: bold; color: #58a6ff; }
        .stat-label { font-size: 12px; color: #8b949e; margin-top: 5px; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #21262d; }
        th { color: #8b949e; font-weight: 500; text-transform: uppercase; font-size: 11px; }
        tr:hover { background: #21262d; }
        .loading { display: flex; align-items: center; justify-content: center; height: 200px; color: #8b949e; }
        footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #30363d; text-align: center; color: #8b949e; font-size: 13px; }
        footer a { color: #58a6ff; text-decoration: none; }
        footer a:hover { text-decoration: underline; }
        .heatmap { display: grid; grid-template-columns: 50px repeat(24, 1fr); gap: 2px; }
        .heatmap-cell { aspect-ratio: 1; border-radius: 3px; display: flex; align-items: center; justify-content: center; font-size: 10px; cursor: default; min-height: 24px; }
        .heatmap-label { font-size: 11px; color: #8b949e; display: flex; align-items: center; justify-content: center; }
        .heatmap-header { font-size: 10px; color: #8b949e; text-align: center; }
        .tag-put { color: #f85149; }
        .tag-call { color: #3fb950; }
        a.tx-link { color: #58a6ff; text-decoration: none; }
        a.tx-link:hover { text-decoration: underline; }
        @media (max-width: 600px) {
            .stats { grid-template-columns: repeat(3, 1fr); gap: 10px; }
            .stat { padding: 10px; }
            .stat-value { font-size: 18px; }
            header { flex-direction: column; align-items: flex-start; }
            .controls { width: 100%; }
            body { padding: 10px; }
            table { font-size: 11px; }
            th, td { padding: 6px 4px; }
            .heatmap { grid-template-columns: 40px repeat(24, 1fr); }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>On-Chain Activity</h1>
                <p class="subtitle">RyskHype Position Tracker</p>
            </div>
            <div class="controls">
                <a href="/" class="btn-secondary">IV Dashboard</a>
                <select id="asset-filter">
                    <option value="all">All Assets</option>
                    <option value="BTC">BTC</option>
                    <option value="HYPE">HYPE</option>
                    <option value="ETH">ETH</option>
                    <option value="SOL">SOL</option>
                </select>
                <select id="days-select">
                    <option value="7">7 days</option>
                    <option value="30" selected>30 days</option>
                    <option value="90">90 days</option>
                    <option value="365">All time</option>
                </select>
            </div>
        </header>
        <div class="stats">
            <div class="stat"><div class="stat-value" id="stat-positions">-</div><div class="stat-label">Total Positions</div></div>
            <div class="stat"><div class="stat-value" id="stat-users">-</div><div class="stat-label">Unique Users</div></div>
            <div class="stat"><div class="stat-value" id="stat-premium">-</div><div class="stat-label">Total Premium</div></div>
        </div>
        <div class="card" style="margin-bottom: 20px;">
            <h2>Volume Over Time</h2>
            <div class="chart-container-large"><canvas id="volume-chart"></canvas></div>
        </div>
        <div class="grid">
            <div class="card">
                <h2>Premium Distribution</h2>
                <div class="chart-container"><canvas id="premium-chart"></canvas></div>
            </div>
            <div class="card">
                <h2>Popular Strikes</h2>
                <div class="chart-container"><canvas id="strikes-chart"></canvas></div>
            </div>
        </div>
        <div class="card" style="margin-bottom: 20px;">
            <h2>IV at Trade Time vs Current IV</h2>
            <div class="chart-container-large"><canvas id="correlation-chart"></canvas></div>
        </div>
        <div class="card" style="margin-bottom: 20px;">
            <h2>Activity Heatmap (UTC)</h2>
            <div id="heatmap-container"></div>
        </div>
        <div class="card">
            <h2>Recent Positions</h2>
            <div id="positions-table"><div class="loading">Loading...</div></div>
        </div>
        <footer>Made with love by <a href="https://x.com/0xcarnation" target="_blank">0xcarnation</a>. Powered by Claude.</footer>
    </div>
    <script>
        let volumeChart = null, premiumChart = null, strikesChart = null, correlationChart = null;
        const assetColors = {'BTC': '#f7931a', 'HYPE': '#58a6ff', 'ETH': '#627eea', 'SOL': '#00ffa3', 'XRP': '#23292f', 'HYPE': '#a371f7'};

        async function init() {
            document.getElementById('asset-filter').onchange = refresh;
            document.getElementById('days-select').onchange = refresh;
            await refresh();
            setInterval(refresh, 5 * 60 * 1000);
        }

        async function refresh() {
            const asset = document.getElementById('asset-filter').value;
            const days = document.getElementById('days-select').value;
            const params = `asset=${asset}&days=${days}`;
            try {
                const [stats, volume, positions, heatmap, strikes, correlation] = await Promise.all([
                    fetch(`/api/activity/stats?${params}`).then(r => r.json()),
                    fetch(`/api/activity/volume?${params}`).then(r => r.json()),
                    fetch(`/api/activity/positions?${params}&limit=50`).then(r => r.json()),
                    fetch(`/api/activity/heatmap?${params}`).then(r => r.json()),
                    fetch(`/api/activity/strikes?${params}`).then(r => r.json()),
                    fetch(`/api/activity/correlation?${params}`).then(r => r.json()),
                ]);
                updateStats(stats);
                updateVolumeChart(volume);
                updatePremiumChart(positions);
                updateStrikesChart(strikes);
                updateCorrelationChart(correlation);
                updateHeatmap(heatmap);
                updatePositionsTable(positions);
            } catch (e) { console.error('Refresh error:', e); }
        }

        function updateStats(data) {
            document.getElementById('stat-positions').textContent = (data.total_positions || 0).toLocaleString();
            document.getElementById('stat-users').textContent = (data.unique_users || 0).toLocaleString();
            const prem = data.total_premium || 0;
            document.getElementById('stat-premium').textContent = prem >= 1000 ? '$' + (prem/1000).toFixed(1) + 'k' : '$' + prem.toFixed(2);
        }

        function updateVolumeChart(data) {
            const ctx = document.getElementById('volume-chart').getContext('2d');
            const dates = [...new Set(data.map(d => d.date))].sort();
            const assets = [...new Set(data.map(d => d.asset))];
            const datasets = assets.map(asset => ({
                label: asset,
                data: dates.map(date => {
                    const entry = data.find(d => d.date === date && d.asset === asset);
                    return entry ? entry.total_premium : 0;
                }),
                backgroundColor: assetColors[asset] || '#8b949e',
                stack: 'premium',
            }));
            const countByDate = {};
            data.forEach(d => { countByDate[d.date] = (countByDate[d.date] || 0) + d.trade_count; });
            datasets.push({
                label: 'Trade Count',
                data: dates.map(d => countByDate[d] || 0),
                type: 'line',
                borderColor: '#f0883e',
                backgroundColor: 'transparent',
                yAxisID: 'y1',
                tension: 0.2,
                pointRadius: 3,
            });
            if (volumeChart) volumeChart.destroy();
            volumeChart = new Chart(ctx, {
                type: 'bar',
                data: { labels: dates, datasets },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {
                        x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                        y: { position: 'left', title: { display: true, text: 'Premium', color: '#8b949e' }, grid: { color: '#21262d' }, ticks: { color: '#8b949e' }, stacked: true },
                        y1: { position: 'right', title: { display: true, text: 'Trades', color: '#f0883e' }, grid: { drawOnChartArea: false }, ticks: { color: '#f0883e' } },
                    },
                    plugins: { legend: { position: 'bottom', labels: { color: '#8b949e', usePointStyle: true, font: { size: 11 } } } },
                },
            });
        }

        function updatePremiumChart(positions) {
            const ctx = document.getElementById('premium-chart').getContext('2d');
            if (!positions.length) { if (premiumChart) premiumChart.destroy(); return; }
            const premiums = positions.filter(p => p.premium_amount > 0).map(p => p.premium_amount);
            if (!premiums.length) { if (premiumChart) premiumChart.destroy(); return; }
            const maxP = Math.max(...premiums);
            const bucketCount = 10;
            const bucketSize = maxP / bucketCount;
            const buckets = Array(bucketCount).fill(0);
            premiums.forEach(p => {
                const idx = Math.min(Math.floor(p / bucketSize), bucketCount - 1);
                buckets[idx]++;
            });
            const labels = buckets.map((_, i) => {
                const lo = (i * bucketSize).toFixed(1);
                const hi = ((i + 1) * bucketSize).toFixed(1);
                return `${lo}-${hi}`;
            });
            if (premiumChart) premiumChart.destroy();
            premiumChart = new Chart(ctx, {
                type: 'bar',
                data: { labels, datasets: [{ label: 'Positions', data: buckets, backgroundColor: '#58a6ff' }] },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {
                        x: { title: { display: true, text: 'Premium Amount', color: '#8b949e' }, grid: { color: '#21262d' }, ticks: { color: '#8b949e', maxRotation: 45 } },
                        y: { title: { display: true, text: 'Count', color: '#8b949e' }, grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                    },
                    plugins: { legend: { display: false } },
                },
            });
        }

        function updateStrikesChart(data) {
            const ctx = document.getElementById('strikes-chart').getContext('2d');
            if (!data.length) { if (strikesChart) strikesChart.destroy(); return; }
            const top = data.slice(0, 15);
            const labels = top.map(d => `${d.strike} ${d.expiry} ${d.is_put ? 'P' : 'C'}`);
            const counts = top.map(d => d.count);
            const premiums = top.map(d => d.total_premium || 0);
            if (strikesChart) strikesChart.destroy();
            strikesChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        { label: 'Trades', data: counts, backgroundColor: '#58a6ff', yAxisID: 'y' },
                        { label: 'Premium', data: premiums, backgroundColor: '#3fb950', yAxisID: 'y1' },
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false, indexAxis: 'y',
                    scales: {
                        x: { position: 'bottom', grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                        x1: { position: 'top', grid: { drawOnChartArea: false }, ticks: { color: '#3fb950' } },
                        y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', font: { size: 10 } } },
                    },
                    plugins: { legend: { position: 'bottom', labels: { color: '#8b949e', usePointStyle: true, font: { size: 11 } } } },
                },
            });
        }

        function updateCorrelationChart(data) {
            const ctx = document.getElementById('correlation-chart').getContext('2d');
            const valid = data.filter(d => d.trade_iv != null && d.current_iv != null);
            if (!valid.length) {
                if (correlationChart) correlationChart.destroy();
                correlationChart = null;
                ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
                ctx.fillStyle = '#8b949e';
                ctx.textAlign = 'center';
                ctx.fillText('No correlation data available yet', ctx.canvas.width / 2, ctx.canvas.height / 2);
                return;
            }
            const byAsset = {};
            valid.forEach(d => {
                if (!byAsset[d.asset]) byAsset[d.asset] = [];
                byAsset[d.asset].push({ x: d.trade_iv, y: d.current_iv });
            });
            const datasets = Object.entries(byAsset).map(([asset, points]) => ({
                label: asset,
                data: points,
                backgroundColor: assetColors[asset] || '#8b949e',
                pointRadius: 5,
                pointHoverRadius: 7,
            }));
            const allIVs = valid.flatMap(d => [d.trade_iv, d.current_iv]);
            const minIV = Math.min(...allIVs) * 0.9;
            const maxIV = Math.max(...allIVs) * 1.1;
            datasets.push({
                label: 'No Change Line',
                data: [{ x: minIV, y: minIV }, { x: maxIV, y: maxIV }],
                type: 'line',
                borderColor: '#30363d',
                borderDash: [5, 5],
                borderWidth: 1,
                pointRadius: 0,
                showLine: true,
            });
            if (correlationChart) correlationChart.destroy();
            correlationChart = new Chart(ctx, {
                type: 'scatter',
                data: { datasets },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {
                        x: { title: { display: true, text: 'IV at Trade Time (%)', color: '#8b949e' }, grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                        y: { title: { display: true, text: 'Current IV (%)', color: '#8b949e' }, grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                    },
                    plugins: {
                        legend: { position: 'bottom', labels: { color: '#8b949e', usePointStyle: true, font: { size: 11 } } },
                        tooltip: {
                            callbacks: {
                                label: function(ctx) {
                                    return `${ctx.dataset.label}: Trade IV ${ctx.parsed.x.toFixed(1)}% → Current IV ${ctx.parsed.y.toFixed(1)}%`;
                                }
                            }
                        }
                    },
                },
            });
        }

        function updateHeatmap(data) {
            const container = document.getElementById('heatmap-container');
            const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            const counts = {};
            let maxCount = 0;
            data.forEach(d => {
                const key = `${d.dow}-${d.hour}`;
                counts[key] = d.count;
                if (d.count > maxCount) maxCount = d.count;
            });
            if (maxCount === 0) {
                container.innerHTML = '<div class="loading">No activity data yet</div>';
                return;
            }
            let html = '<div class="heatmap">';
            html += '<div class="heatmap-label"></div>';
            for (let h = 0; h < 24; h++) html += `<div class="heatmap-header">${h}</div>`;
            for (let d = 0; d < 7; d++) {
                html += `<div class="heatmap-label">${days[d]}</div>`;
                for (let h = 0; h < 24; h++) {
                    const count = counts[`${d}-${h}`] || 0;
                    const intensity = count / maxCount;
                    const bg = count > 0 ? `rgba(88,166,255,${0.15 + intensity * 0.85})` : '#161b22';
                    html += `<div class="heatmap-cell" style="background:${bg}" title="${days[d]} ${h}:00 UTC - ${count} trades">${count || ''}</div>`;
                }
            }
            html += '</div>';
            container.innerHTML = html;
        }

        function updatePositionsTable(positions) {
            const container = document.getElementById('positions-table');
            if (!positions.length) { container.innerHTML = '<div class="loading">No positions found</div>'; return; }
            const rows = positions.map(p => {
                const time = p.block_timestamp ? new Date(p.block_timestamp).toLocaleString() : '-';
                const user = p.user_address ? (p.user_address.slice(0, 6) + '...' + p.user_address.slice(-4)) : '-';
                const type = p.is_put ? '<span class="tag-put">PUT</span>' : '<span class="tag-call">CALL</span>';
                const premium = p.premium_amount != null ? p.premium_amount.toFixed(4) : '-';
                const collToken = p.collateral_token || '';
                const ivLabel = p.trade_iv != null ? p.trade_iv.toFixed(1) + '%' : '-';
                const txShort = p.tx_hash ? p.tx_hash.slice(0, 10) + '...' : '-';
                const txLink = p.tx_hash ? `<a class="tx-link" href="https://purrsec.com/tx/${p.tx_hash}" target="_blank">${txShort}</a>` : '-';
                return `<tr><td>${time}</td><td title="${p.user_address || ''}">${user}</td><td>${p.asset}</td><td>${p.strike}</td><td>${p.expiry}</td><td>${type}</td><td>${premium} ${collToken}</td><td>${txLink}</td></tr>`;
            }).join('');
            container.innerHTML = `<table><thead><tr><th>Time</th><th>User</th><th>Asset</th><th>Strike</th><th>Expiry</th><th>Type</th><th>Premium</th><th>Tx</th></tr></thead><tbody>${rows}</tbody></table>`;
        }

        init();
    </script>
<script defer src="/_vercel/insights/script.js"></script>
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
    # Also ensure iv_forecasts table exists (populated by forecast_runner.py)
    cursor.execute("""
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_forecasts_asset_gen ON iv_forecasts(asset, generated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_forecasts_lookup ON iv_forecasts(asset, strike, expiry, generated_at DESC)")
    conn.commit()
    conn.close()


def init_activity_db():
    """Initialize on-chain activity database schema."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS indexer_state (
            contract_address TEXT PRIMARY KEY,
            last_block BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS onchain_positions (
            id SERIAL PRIMARY KEY,
            tx_hash TEXT UNIQUE NOT NULL,
            block_number BIGINT NOT NULL,
            block_timestamp TIMESTAMP,
            user_address TEXT NOT NULL,
            asset TEXT NOT NULL,
            strike REAL NOT NULL,
            expiry TEXT NOT NULL,
            is_put BOOLEAN NOT NULL,
            collateral_amount REAL,
            collateral_token TEXT,
            premium_amount REAL,
            fee_amount REAL,
            otoken_amount REAL,
            otoken_address TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_asset_time ON onchain_positions(asset, block_timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_user ON onchain_positions(user_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_timestamp ON onchain_positions(block_timestamp)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS otoken_registry (
            otoken_address TEXT PRIMARY KEY,
            underlying TEXT,
            strike REAL,
            expiry TEXT,
            expiry_timestamp BIGINT,
            is_put BOOLEAN,
            collateral TEXT,
            asset TEXT
        )
    """)
    conn.commit()
    conn.close()


# ============== RPC Helpers ==============

_last_rpc_call = 0


_indexer_deadline = None


def rpc_call(method, params=None):
    """Make a JSON-RPC call to HyperEVM. No retries - fail fast for Vercel."""
    global _last_rpc_call
    if _indexer_deadline and time.time() > _indexer_deadline:
        raise Exception("time budget exceeded")
    now = time.time()
    elapsed = now - _last_rpc_call
    min_gap = 0.6
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)
    _last_rpc_call = time.time()

    resp = requests.post(HYPERVM_RPC, json={
        'jsonrpc': '2.0',
        'method': method,
        'params': params or [],
        'id': 1,
    }, timeout=5)
    result = resp.json()
    if 'error' in result:
        raise Exception(f"RPC error: {result['error']}")
    return result.get('result')


def get_block_number():
    """Get the latest block number."""
    result = rpc_call('eth_blockNumber')
    return int(result, 16)


def get_logs(from_block, to_block, address, topics):
    """Get logs from HyperEVM."""
    return rpc_call('eth_getLogs', [{
        'address': address,
        'topics': topics,
        'fromBlock': hex(from_block),
        'toBlock': hex(to_block),
    }]) or []


def get_receipt(tx_hash):
    """Get transaction receipt."""
    return rpc_call('eth_getTransactionReceipt', [tx_hash])


def get_block_timestamp(block_number):
    """Get block timestamp."""
    block = rpc_call('eth_getBlockByNumber', [hex(block_number), False])
    if block and 'timestamp' in block:
        return int(block['timestamp'], 16)
    return None


# ============== ABI Decode Helpers ==============

def decode_address(topic_hex):
    """Decode an address from a 32-byte hex topic."""
    h = topic_hex.replace('0x', '')
    return '0x' + h[-40:]


def decode_uint256(data_hex, offset=0):
    """Decode a uint256 from data at given 32-byte word offset."""
    h = data_hex.replace('0x', '')
    start = offset * 64
    return int(h[start:start + 64], 16)


def decode_bool(data_hex, offset=0):
    """Decode a bool from data at given 32-byte word offset."""
    return decode_uint256(data_hex, offset) != 0


def format_expiry_timestamp(unix_ts):
    """Convert unix timestamp to DDMMMYY format matching iv_snapshots."""
    dt = datetime.utcfromtimestamp(unix_ts)
    months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
    return f"{dt.day:02d}{months[dt.month - 1]}{dt.year % 100:02d}"


def token_to_asset(address):
    """Map a token address to its tracked asset name."""
    return UNDERLYING_TO_ASSET.get(address.lower(), address[:10])


# ============== Otoken Registry ==============

_otoken_cache = {}


def get_otoken_info(otoken_address, conn=None):
    """Get otoken info from cache or DB."""
    addr = otoken_address.lower()
    if addr in _otoken_cache:
        return _otoken_cache[addr]
    try:
        close_conn = False
        if conn is None:
            conn = get_db()
            close_conn = True
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM otoken_registry WHERE otoken_address = %s", (addr,))
            row = cursor.fetchone()
            if row:
                info = dict(row)
                _otoken_cache[addr] = info
                return info
        finally:
            if close_conn:
                conn.close()
    except Exception:
        pass
    return None


def save_otoken_info(otoken_address, info, conn=None):
    """Save otoken info to DB and cache."""
    addr = otoken_address.lower()
    _otoken_cache[addr] = info
    try:
        close_conn = False
        if conn is None:
            conn = get_db()
            close_conn = True
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO otoken_registry (otoken_address, underlying, strike, expiry, expiry_timestamp, is_put, collateral, asset)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (otoken_address) DO NOTHING
            """, (addr, info.get('underlying'), info['strike'], info['expiry'],
                  info.get('expiry_timestamp'), info['is_put'], info.get('collateral'), info['asset']))
            conn.commit()
        finally:
            if close_conn:
                conn.close()
    except Exception:
        pass  # Cache-only mode when DB unavailable


# Symbol prefix -> asset name mapping
SYMBOL_TO_ASSET = {
    'UETH': 'ETH', 'UBTC': 'BTC', 'USOL': 'SOL', 'WHYPE': 'HYPE',
    'HYPE': 'HYPE', 'ETH': 'ETH', 'BTC': 'BTC', 'SOL': 'SOL',
}


def query_otoken_onchain(otoken_address):
    """Query otoken contract on-chain for its properties via name() and strikePrice()."""
    addr = otoken_address.lower()
    # name() = 0x06fdde03 - returns ABI-encoded string like "UETHUSDC 20-February-2026 1750Put USDC Collateral"
    name_result = rpc_call('eth_call', [{'to': addr, 'data': '0x06fdde03'}, 'latest'])
    if not name_result or len(name_result) < 130:
        return None
    # Decode ABI string
    data_hex = name_result[2:]
    str_len = int(data_hex[64:128], 16)
    name_str = bytes.fromhex(data_hex[128:128 + str_len * 2]).decode('utf-8', errors='replace')

    # strikePrice() = 0xc52987cf
    strike_result = rpc_call('eth_call', [{'to': addr, 'data': '0xc52987cf'}, 'latest'])
    if not strike_result:
        return None
    strike = int(strike_result, 16) / 1e8

    # Parse name: "UETHUSDC 20-February-2026 1750Put USDC Collateral"
    parts = name_str.split()
    if len(parts) < 3:
        return None

    # Extract asset from first word (e.g. "UETHUSDC" -> "UETH" by removing known suffixes)
    pair = parts[0]
    asset_name = None
    for prefix in sorted(SYMBOL_TO_ASSET.keys(), key=len, reverse=True):
        if pair.upper().startswith(prefix):
            asset_name = SYMBOL_TO_ASSET[prefix]
            break
    if not asset_name:
        asset_name = pair[:4]

    # Parse expiry from date part (e.g. "20-February-2026")
    expiry = None
    from datetime import datetime as _dt
    for p in parts[1:]:
        try:
            dt = _dt.strptime(p, '%d-%B-%Y')
            expiry = dt.strftime('%d%b%y').upper()
            expiry_ts = int(dt.timestamp())
            break
        except ValueError:
            continue

    # Parse isPut from name (e.g. "1750Put" or "1750Call")
    is_put = 'put' in name_str.lower()

    if expiry is None:
        return None

    return {
        'strike': strike,
        'expiry': expiry,
        'is_put': is_put,
        'asset': asset_name,
        'underlying': None,
        'expiry_timestamp': expiry_ts,
        'collateral': None,
    }


# ============== Event Decoder ==============

def decode_position_from_receipt(receipt, conn=None):
    """Decode position data from a transaction receipt."""
    logs = receipt.get('logs', [])

    position = {
        'tx_hash': receipt['transactionHash'],
        'block_number': int(receipt['blockNumber'], 16),
        'user_address': None,
        'asset': None,
        'strike': None,
        'expiry': None,
        'is_put': None,
        'collateral_amount': None,
        'collateral_token': None,
        'premium_amount': None,
        'fee_amount': None,
        'otoken_amount': None,
        'otoken_address': None,
    }

    otoken_created_info = None

    for log in logs:
        topics = log.get('topics', [])
        if not topics:
            continue
        topic0 = topics[0].lower()
        addr = log.get('address', '').lower()
        data = log.get('data', '0x')

        # OtokenCreated from OtokenFactory
        if topic0 == TOPIC_OTOKEN_CREATED.lower() and addr == OTOKEN_FACTORY.lower():
            if len(topics) < 4:
                continue
            underlying = decode_address(topics[1])
            collateral = decode_address(topics[3])
            # data: tokenAddress(0), creator(1), strikePrice(2), expiry(3), isPut(4), extra(5)
            otoken_addr = decode_address(data[:66])  # 0x + 64 hex chars
            strike_raw = decode_uint256(data, 2)
            expiry_raw = decode_uint256(data, 3)
            is_put = decode_bool(data, 4)

            asset_name = token_to_asset(underlying)

            position['strike'] = strike_raw / 1e8
            position['expiry'] = format_expiry_timestamp(expiry_raw)
            position['is_put'] = is_put
            position['asset'] = asset_name
            position['otoken_address'] = otoken_addr.lower()

            otoken_created_info = {
                'underlying': underlying.lower(),
                'strike': strike_raw / 1e8,
                'expiry': format_expiry_timestamp(expiry_raw),
                'expiry_timestamp': expiry_raw,
                'is_put': is_put,
                'collateral': collateral.lower(),
                'asset': asset_name,
            }

        # ShortOtokenMinted from Controller
        elif topic0 == TOPIC_SHORT_OTOKEN_MINTED.lower() and addr == CONTROLLER_CONTRACT.lower():
            if len(topics) < 4:
                continue
            otoken_addr = decode_address(topics[1])
            amount = decode_uint256(data, 1)  # data[0]=vaultId, data[1]=amount

            position['otoken_address'] = otoken_addr.lower()
            position['otoken_amount'] = amount / 1e8

        # CollateralAssetDeposited from Controller
        elif topic0 == TOPIC_COLLATERAL_DEPOSITED.lower() and addr == CONTROLLER_CONTRACT.lower():
            if len(topics) < 4:
                continue
            asset_addr = decode_address(topics[1])
            amount = decode_uint256(data, 1)  # data[0]=vaultId, data[1]=amount

            token = TOKEN_INFO.get(asset_addr.lower())
            if token:
                position['collateral_amount'] = amount / (10 ** token['decimals'])
                position['collateral_token'] = token['symbol']

        # TransferToUser from Rysk MarginPool
        elif topic0 == TOPIC_TRANSFER_TO_USER.lower() and addr == RYSK_MARGIN_POOL.lower():
            if len(topics) < 4:
                continue
            asset_addr = decode_address(topics[1])
            to_addr = decode_address(topics[3])
            amount = decode_uint256(data, 0)

            token = TOKEN_INFO.get(asset_addr.lower())
            if token:
                scaled = amount / (10 ** token['decimals'])
                if to_addr.lower() == FEE_RECIPIENT.lower():
                    position['fee_amount'] = (position.get('fee_amount') or 0) + scaled
                else:
                    position['premium_amount'] = (position.get('premium_amount') or 0) + scaled
                    position['user_address'] = to_addr

    # Save otoken info if we saw OtokenCreated
    if otoken_created_info and position['otoken_address']:
        save_otoken_info(position['otoken_address'], otoken_created_info, conn)

    # Look up otoken from registry if not seen in this receipt
    if position['strike'] is None and position['otoken_address']:
        info = get_otoken_info(position['otoken_address'], conn)
        if info:
            position['strike'] = info['strike']
            position['expiry'] = info['expiry']
            position['is_put'] = info['is_put']
            position['asset'] = info['asset']

    # Last resort: query otoken contract on-chain for its properties
    if position['strike'] is None and position['otoken_address']:
        try:
            info = query_otoken_onchain(position['otoken_address'])
            if info:
                position['strike'] = info['strike']
                position['expiry'] = info['expiry']
                position['is_put'] = info['is_put']
                position['asset'] = info['asset']
                save_otoken_info(position['otoken_address'], info, conn)
        except Exception:
            pass

    # Validate minimum required fields
    if position['strike'] is not None and position['asset'] is not None:
        if not position['user_address']:
            position['user_address'] = 'unknown'
        return position
    return None


# ============== Indexer ==============

def index_activity_batch(max_blocks=None):
    """Index a batch of blocks for on-chain activity."""
    global _indexer_deadline
    _indexer_deadline = time.time() + INDEXER_TIME_BUDGET
    if max_blocks is None:
        max_blocks = MAX_BLOCKS_PER_CRON

    conn = get_db()
    cursor = conn.cursor()

    # Get last indexed block
    cursor.execute("SELECT last_block FROM indexer_state WHERE contract_address = %s",
                   (CONTROLLER_CONTRACT.lower(),))
    row = cursor.fetchone()

    if row:
        last_block = max(row['last_block'], ACTIVITY_START_BLOCK)
    else:
        last_block = ACTIVITY_START_BLOCK
        cursor.execute(
            "INSERT INTO indexer_state (contract_address, last_block) VALUES (%s, %s)",
            (CONTROLLER_CONTRACT.lower(), last_block))
        conn.commit()

    try:
        current_block = get_block_number()
    except Exception:
        # If rate limited on first call, estimate current block far ahead
        current_block = last_block + max_blocks
    from_block = last_block + 1
    to_block = min(from_block + max_blocks - 1, current_block)

    if from_block > current_block:
        conn.close()
        return {'from_block': from_block, 'to_block': to_block,
                'positions_found': 0, 'blocks_remaining': 0, 'status': 'caught_up'}

    positions_found = 0
    processed_tx_hashes = set()
    block_timestamps = {}
    start_time = time.time()

    scan_from = from_block
    while scan_from <= to_block:
        # Stop before Vercel function timeout
        if time.time() - start_time > INDEXER_TIME_BUDGET:
            to_block = scan_from - 1
            break
        scan_to = min(scan_from + LOGS_BLOCK_RANGE - 1, to_block)

        try:
            logs = get_logs(scan_from, scan_to, CONTROLLER_CONTRACT,
                          [TOPIC_SHORT_OTOKEN_MINTED])
        except Exception as e:
            err_msg = str(e).lower()
            if 'time budget' in err_msg:
                to_block = scan_from - 1
                break
            if 'too many' in err_msg or 'range' in err_msg or 'limit' in err_msg or 'rate' in err_msg:
                smaller_range = max(50, LOGS_BLOCK_RANGE // 10)
                scan_to = min(scan_from + smaller_range - 1, to_block)
                try:
                    logs = get_logs(scan_from, scan_to, CONTROLLER_CONTRACT,
                                  [TOPIC_SHORT_OTOKEN_MINTED])
                except Exception:
                    scan_from = scan_to + 1
                    continue
            else:
                to_block = scan_from - 1
                break

        if logs:
            tx_hashes = list(set(log['transactionHash'] for log in logs))

            for tx_hash in tx_hashes:
                if _indexer_deadline and time.time() > _indexer_deadline:
                    break
                if tx_hash in processed_tx_hashes:
                    continue
                processed_tx_hashes.add(tx_hash)

                # Check if already indexed
                cursor.execute("SELECT 1 FROM onchain_positions WHERE tx_hash = %s", (tx_hash,))
                if cursor.fetchone():
                    continue

                try:
                    receipt = get_receipt(tx_hash)
                except Exception:
                    continue
                if not receipt:
                    continue

                position = decode_position_from_receipt(receipt, conn)
                if not position:
                    continue

                # Get block timestamp (cached per block)
                block_num = position['block_number']
                if block_num not in block_timestamps:
                    try:
                        ts = get_block_timestamp(block_num)
                    except Exception:
                        ts = None
                    block_timestamps[block_num] = datetime.utcfromtimestamp(ts) if ts else None
                position['block_timestamp'] = block_timestamps[block_num]

                cursor.execute("""
                    INSERT INTO onchain_positions
                    (tx_hash, block_number, block_timestamp, user_address, asset, strike, expiry, is_put,
                     collateral_amount, collateral_token, premium_amount, fee_amount, otoken_amount, otoken_address)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tx_hash) DO NOTHING
                """, (
                    position['tx_hash'], position['block_number'], position['block_timestamp'],
                    position['user_address'], position['asset'], position['strike'], position['expiry'],
                    position['is_put'], position['collateral_amount'], position['collateral_token'],
                    position['premium_amount'], position['fee_amount'], position['otoken_amount'],
                    position['otoken_address']
                ))
                conn.commit()
                positions_found += 1

        scan_from = scan_to + 1

    # Update last indexed block
    cursor.execute(
        "UPDATE indexer_state SET last_block = %s, updated_at = CURRENT_TIMESTAMP WHERE contract_address = %s",
        (to_block, CONTROLLER_CONTRACT.lower()))
    conn.commit()
    conn.close()

    blocks_remaining = current_block - to_block
    return {
        'from_block': from_block,
        'to_block': to_block,
        'positions_found': positions_found,
        'blocks_remaining': blocks_remaining,
        'status': 'indexed',
    }


# ============== Routes ==============

@app.route('/')
def index():
    """Serve dashboard."""
    return cached_html(DASHBOARD_HTML, max_age=300, stale_revalidate=3600)


@app.route('/api/assets')
def api_assets():
    """Get list of tracked assets."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT asset FROM iv_snapshots ORDER BY asset")
        assets = [row['asset'] for row in cursor.fetchall()]
        conn.close()
        return cached_json(assets, max_age=300, stale_revalidate=3600)
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

        return cached_json(results, max_age=60, stale_revalidate=300)
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
        return cached_json([dict(r) for r in rows], max_age=120, stale_revalidate=600)
    except Exception as e:
        return jsonify([])


@app.route('/api/forecasts/<asset>')
def api_forecasts(asset):
    """Get latest precomputed IV forecasts for an asset."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        # Get the most recent generated_at for this asset
        cursor.execute("""
            SELECT generated_at FROM iv_forecasts
            WHERE asset = %s ORDER BY generated_at DESC LIMIT 1
        """, (asset,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify([])
        latest_gen = row['generated_at']
        cursor.execute("""
            SELECT strike, expiry, option_type, forecast_timestamp,
                   forecast_mid_iv, quantile_10, quantile_90
            FROM iv_forecasts
            WHERE asset = %s AND generated_at = %s
            ORDER BY strike, expiry, forecast_timestamp
        """, (asset, latest_gen))
        rows = cursor.fetchall()
        conn.close()
        return cached_json([dict(r) for r in rows], max_age=3600, stale_revalidate=7200)
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


@app.route('/activity')
def activity_page():
    """Serve on-chain activity dashboard."""
    return cached_html(ACTIVITY_HTML, max_age=300, stale_revalidate=3600)


@app.route('/api/cron/index-activity')
def cron_index_activity():
    """Cron endpoint to index on-chain activity."""
    auth = request.headers.get('Authorization', '')
    if CRON_SECRET and auth != f'Bearer {CRON_SECRET}':
        if request.headers.get('x-vercel-cron') != '1':
            return jsonify({'error': 'Unauthorized'}), 401
    try:
        init_activity_db()
        # One-time data fixes from early mapping bugs
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("UPDATE onchain_positions SET asset = 'HYPE' WHERE asset LIKE '0x5555%'")
            cursor.execute("UPDATE onchain_positions SET asset = 'ETH' WHERE asset LIKE '0xbe67%'")
            # Fix kHYPE collateral (200e18 = 200 kHYPE)
            cursor.execute(
                "UPDATE onchain_positions SET collateral_amount = 200, collateral_token = 'kHYPE' "
                "WHERE collateral_amount IS NULL AND tx_hash = '0x4324c4f0a6aac76db8fd40ea2521a747b50ee0e7ae17d070b2fc5a3b1870cbe3'")
            conn.commit()
            conn.close()
        except Exception:
            pass
        result = index_activity_batch()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/activity/positions')
def api_activity_positions():
    """Get recent on-chain positions."""
    asset = request.args.get('asset')
    days = request.args.get('days', 30, type=int)
    limit = request.args.get('limit', 100, type=int)
    try:
        conn = get_db()
        cursor = conn.cursor()
        since = datetime.utcnow() - timedelta(days=days)
        query = "SELECT * FROM onchain_positions WHERE block_timestamp > %s"
        params = [since]
        if asset and asset != 'all':
            query += " AND asset = %s"
            params.append(asset)
        query += " ORDER BY block_timestamp DESC LIMIT %s"
        params.append(min(limit, 500))
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return cached_json([dict(r) for r in rows], max_age=120, stale_revalidate=600)
    except Exception as e:
        return jsonify([])


@app.route('/api/activity/volume')
def api_activity_volume():
    """Get volume over time aggregated by day and asset."""
    asset = request.args.get('asset')
    days = request.args.get('days', 30, type=int)
    try:
        conn = get_db()
        cursor = conn.cursor()
        since = datetime.utcnow() - timedelta(days=days)
        query = """
            SELECT DATE(block_timestamp) as date, asset,
                   COUNT(*) as trade_count,
                   SUM(COALESCE(premium_amount, 0)) as total_premium,
                   SUM(COALESCE(fee_amount, 0)) as total_fees
            FROM onchain_positions
            WHERE block_timestamp > %s
        """
        params = [since]
        if asset and asset != 'all':
            query += " AND asset = %s"
            params.append(asset)
        query += " GROUP BY DATE(block_timestamp), asset ORDER BY date"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return cached_json([dict(r) for r in rows], max_age=120, stale_revalidate=600)
    except Exception as e:
        return jsonify([])


@app.route('/api/activity/stats')
def api_activity_stats():
    """Get summary stats for on-chain activity."""
    asset = request.args.get('asset')
    days = request.args.get('days', 30, type=int)
    try:
        conn = get_db()
        cursor = conn.cursor()
        since = datetime.utcnow() - timedelta(days=days)
        query = """
            SELECT COUNT(*) as total_positions,
                   COUNT(DISTINCT user_address) as unique_users,
                   SUM(COALESCE(premium_amount, 0)) as total_premium,
                   SUM(COALESCE(fee_amount, 0)) as total_fees
            FROM onchain_positions
            WHERE block_timestamp > %s
        """
        params = [since]
        if asset and asset != 'all':
            query += " AND asset = %s"
            params.append(asset)
        cursor.execute(query, params)
        row = cursor.fetchone()
        conn.close()
        return cached_json(dict(row) if row else {}, max_age=120, stale_revalidate=600)
    except Exception as e:
        return jsonify({})


@app.route('/api/activity/heatmap')
def api_activity_heatmap():
    """Get hour-of-day vs day-of-week trade counts."""
    asset = request.args.get('asset')
    days = request.args.get('days', 30, type=int)
    try:
        conn = get_db()
        cursor = conn.cursor()
        since = datetime.utcnow() - timedelta(days=days)
        query = """
            SELECT EXTRACT(HOUR FROM block_timestamp)::int as hour,
                   EXTRACT(DOW FROM block_timestamp)::int as dow,
                   COUNT(*) as count
            FROM onchain_positions
            WHERE block_timestamp > %s
        """
        params = [since]
        if asset and asset != 'all':
            query += " AND asset = %s"
            params.append(asset)
        query += " GROUP BY hour, dow ORDER BY dow, hour"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return cached_json([dict(r) for r in rows], max_age=120, stale_revalidate=600)
    except Exception as e:
        return jsonify([])


@app.route('/api/activity/strikes')
def api_activity_strikes():
    """Get strike/expiry distribution."""
    asset = request.args.get('asset')
    days = request.args.get('days', 30, type=int)
    try:
        conn = get_db()
        cursor = conn.cursor()
        since = datetime.utcnow() - timedelta(days=days)
        query = """
            SELECT strike, expiry, is_put,
                   COUNT(*) as count,
                   SUM(COALESCE(premium_amount, 0)) as total_premium,
                   SUM(COALESCE(otoken_amount, 0)) as total_contracts
            FROM onchain_positions
            WHERE block_timestamp > %s
        """
        params = [since]
        if asset and asset != 'all':
            query += " AND asset = %s"
            params.append(asset)
        query += " GROUP BY strike, expiry, is_put ORDER BY count DESC LIMIT 50"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return cached_json([dict(r) for r in rows], max_age=120, stale_revalidate=600)
    except Exception as e:
        return jsonify([])


@app.route('/api/activity/correlation')
def api_activity_correlation():
    """Get positions correlated with IV at time of trade."""
    asset = request.args.get('asset')
    days = request.args.get('days', 30, type=int)
    try:
        conn = get_db()
        cursor = conn.cursor()
        since = datetime.utcnow() - timedelta(days=days)
        query = """
            SELECT p.tx_hash, p.asset, p.strike, p.expiry, p.is_put,
                   p.premium_amount, p.block_timestamp,
                   iv.mid_iv as trade_iv,
                   latest.mid_iv as current_iv
            FROM onchain_positions p
            LEFT JOIN LATERAL (
                SELECT mid_iv FROM iv_snapshots
                WHERE asset = p.asset AND strike = p.strike AND expiry = p.expiry
                AND timestamp <= p.block_timestamp
                ORDER BY timestamp DESC LIMIT 1
            ) iv ON true
            LEFT JOIN LATERAL (
                SELECT mid_iv FROM iv_snapshots
                WHERE asset = p.asset AND strike = p.strike AND expiry = p.expiry
                ORDER BY timestamp DESC LIMIT 1
            ) latest ON true
            WHERE p.block_timestamp > %s
        """
        params = [since]
        if asset and asset != 'all':
            query += " AND p.asset = %s"
            params.append(asset)
        query += " ORDER BY p.block_timestamp DESC LIMIT 200"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return cached_json([dict(r) for r in rows], max_age=120, stale_revalidate=600)
    except Exception as e:
        return jsonify([])


# ============== Scraping Logic ==============

def fetch_iv_data():
    """Fetch IV data from Rysk Finance."""
    url = 'https://app.rysk.finance'
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

    response = requests.get(url, headers=headers, timeout=30)
    html = response.text.replace('\\"', '"').replace('\\\\', '\\')

    records = []
    spot_prices = extract_spot_prices(html)

    # Dynamically detect all assets from HTML
    asset_matches = re.findall(r'"([A-Z]{2,6})":\{"combinations"', html)
    asset_positions = []

    for asset in asset_matches:
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
    # Dynamically detect all assets from HTML
    assets = re.findall(r'"([A-Z]{2,6})":\{"combinations"', html)

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
