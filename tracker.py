#!/usr/bin/env python3
"""
Rysk Finance Implied Volatility Tracker

Scrapes IV data from app.rysk.finance and stores it for historical analysis.

Usage:
    python tracker.py fetch              # Single data fetch
    python tracker.py history --asset BTC  # View history
    python tracker.py export --format csv  # Export data
    python tracker.py daemon             # Run continuous collection
    python tracker.py dashboard          # Start web dashboard
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

import requests
from bs4 import BeautifulSoup
from tabulate import tabulate

import config
import database


def fetch_page() -> str:
    """Fetch the Rysk Finance app page using simple HTTP request."""
    headers = {
        'User-Agent': config.USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }

    response = requests.get(
        config.TARGET_URL,
        headers=headers,
        timeout=config.REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.text


def fetch_page_with_browser(wait_time: int = 10) -> str:
    """
    Fetch page using headless browser to capture dynamically loaded data.

    Args:
        wait_time: Seconds to wait for dynamic content to load

    Returns:
        Page HTML after JavaScript execution
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return fetch_page()  # Fallback to simple fetch

    print(f"Loading page with browser (waiting {wait_time}s for dynamic content)...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()

        # Collect WebSocket messages
        ws_data = []

        def handle_websocket(ws):
            def on_message(msg):
                ws_data.append(msg)
            ws.on("framereceived", lambda payload: on_message(payload))

        page.on("websocket", handle_websocket)

        # Navigate to the page
        page.goto(config.TARGET_URL, wait_until='networkidle')

        # Wait for dynamic content
        page.wait_for_timeout(wait_time * 1000)

        # Get the page HTML
        html = page.content()

        # Also try to extract data from page's JavaScript context
        try:
            # Try to get any global state that might contain IV data
            js_data = page.evaluate('''() => {
                // Look for Next.js data
                if (window.__NEXT_DATA__) return JSON.stringify(window.__NEXT_DATA__);
                // Look for any global state
                if (window.__APP_STATE__) return JSON.stringify(window.__APP_STATE__);
                return null;
            }''')
            if js_data:
                html += f'\\n<!-- JS_DATA: {js_data} -->'
        except Exception:
            pass

        # Append WebSocket data if any was captured
        if ws_data:
            html += f'\\n<!-- WS_DATA: {json.dumps(ws_data)} -->'

        browser.close()

    return html


def fetch_all_asset_pages(wait_time: int = 8) -> str:
    """
    Fetch multiple asset pages to capture IV data for all assets.

    Returns:
        Combined HTML from all pages
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return fetch_page()

    # Asset pages to fetch - covering different underlying assets
    asset_pages = [
        "/earn/999/UBTC/UBTC/USDT0/put/",      # BTC
        "/earn/999/UETH/UETH/USDT0/put/",      # ETH
        "/earn/999/WHYPE/WHYPE/USDT0/call/",   # HYPE
        "/earn/999/USOL/USOL/USDT0/put/",      # SOL
        "/earn/999/UPUMP/UPUMP/USDT0/call/",   # PUMP
        "/earn/999/PURR/PURR/USDT0/call/",     # PURR
    ]

    all_html = ""

    print(f"Fetching {len(asset_pages)} asset pages with browser...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()

        for i, asset_path in enumerate(asset_pages):
            url = f"https://app.rysk.finance{asset_path}"
            print(f"  [{i+1}/{len(asset_pages)}] Fetching {asset_path}...")

            try:
                page.goto(url, wait_until='networkidle', timeout=30000)
                page.wait_for_timeout(wait_time * 1000)
                html = page.content()
                all_html += html + "\\n"
            except Exception as e:
                print(f"    Warning: Failed to fetch {asset_path}: {e}")

        browser.close()

    return all_html


def parse_iv_data(html: str) -> List[Dict[str, Any]]:
    """
    Parse IV data from page HTML.

    Rysk Finance uses Next.js RSC with serverInventory containing IV data.
    The JSON is often escaped (backslash-quoted), so we handle both formats.
    For assets without IV data, calculates IV from APY using Black-Scholes.
    """
    records = []

    # Unescape the HTML if needed (handles \" -> ")
    unescaped_html = html.replace('\\"', '"').replace('\\\\', '\\')

    # Extract spot prices for IV calculation
    spot_prices = extract_spot_prices(unescaped_html)

    # Try parsing from serverInventory
    if 'serverInventory' in unescaped_html:
        records.extend(extract_iv_from_server_inventory(unescaped_html))

    # If no records found, try the raw (escaped) version
    if not records and 'serverInventory' in html:
        records.extend(extract_iv_from_raw_html(html))

    # Calculate IV from APY for records that don't have IV
    records = calculate_missing_iv(records, spot_prices)

    return deduplicate_records(records)


def extract_spot_prices(html: str) -> Dict[str, float]:
    """Extract spot prices (index) for each asset from serverInventory."""
    spot_prices = {}
    assets = ['BTC', 'ETH', 'SOL', 'HYPE', 'PURR', 'PUMP', 'ZEC', 'XRP']

    for asset in assets:
        # Find the index price in asset's combinations
        pattern = rf'"{asset}":\{{"combinations":\{{[^}}]*?"index":([\d.]+)'
        match = re.search(pattern, html)
        if match:
            spot_prices[asset] = float(match.group(1))

    return spot_prices


def calculate_missing_iv(records: List[Dict[str, Any]], spot_prices: Dict[str, float]) -> List[Dict[str, Any]]:
    """
    Calculate IV from APY for records that don't have IV data.

    Uses Black-Scholes model to reverse-calculate IV from option premium (APY).
    """
    try:
        from iv_calculator import implied_volatility_from_apy, parse_expiry_to_dte
    except ImportError:
        return records  # Skip if calculator not available

    for record in records:
        # Skip if already has IV
        if record.get('bid_iv') and record.get('bid_iv') > 0:
            continue

        # Need APY, spot price, and other data to calculate
        apy = record.get('apy')
        asset = record.get('asset')
        strike = record.get('strike')
        expiry = record.get('expiry')
        option_type = record.get('option_type')

        if not all([apy, asset, strike, expiry, option_type]):
            continue

        spot = spot_prices.get(asset)
        if not spot:
            continue

        # Parse expiry to DTE
        dte = parse_expiry_to_dte(expiry)
        if not dte or dte <= 0:
            continue

        is_put = option_type.lower() == 'put'

        # Calculate IV
        calculated_iv = implied_volatility_from_apy(
            spot=spot,
            strike=strike,
            dte=dte,
            apy=apy,
            is_put=is_put
        )

        if calculated_iv:
            # Store as calculated IV (mark it as calculated)
            record['bid_iv'] = calculated_iv
            record['ask_iv'] = calculated_iv
            record['mid_iv'] = calculated_iv
            record['iv_calculated'] = True  # Flag that this was calculated, not quoted

    return records


def extract_iv_from_server_inventory(html: str) -> List[Dict[str, Any]]:
    """
    Extract IV and APY data from serverInventory structure.

    Structure: serverInventory.{ASSET}.combinations.{strike-timestamp} = {
        expiry, strike, isPut, bidIv, askIv, apy, delta, ...
    }
    """
    records = []

    # Find all asset sections in serverInventory
    assets = ['BTC', 'ETH', 'SOL', 'HYPE', 'PURR', 'PUMP', 'ZEC', 'XRP']

    # First, find all asset section positions to determine boundaries
    asset_positions = []
    for asset in assets:
        pos = html.find(f'"{asset}":{{"combinations":')
        if pos >= 0:
            asset_positions.append((asset, pos))

    # Sort by position
    asset_positions.sort(key=lambda x: x[1])

    for i, (asset, start_pos) in enumerate(asset_positions):
        # Find the end of this asset's section (start of next asset or reasonable limit)
        if i + 1 < len(asset_positions):
            end_pos = asset_positions[i + 1][1]
        else:
            end_pos = start_pos + 20000  # Reasonable limit for last asset

        # Extract just this asset's combinations
        chunk = html[start_pos:end_pos]

        # Pattern to extract option entries with all fields including APY
        # This pattern captures: strike-timestamp key, expiry, strike, isPut, bidIv, askIv, apy
        entry_pattern = (
            r'"([\d.]+)-([\d]+)":\{'
            r'"expiry":"([^"]+)"[^}]*?'
            r'"strike":([\d.]+)[^}]*?'
            r'"isPut":(true|false)[^}]*?'
            r'"bidIv":([\d.]+)[^}]*?'
            r'"askIv":([\d.]+)[^}]*?'
            r'"apy":([\d.]+)'
        )

        entries = re.findall(entry_pattern, chunk)

        for entry in entries:
            strike_key, timestamp, expiry, strike, is_put, bid_iv, ask_iv, apy = entry

            bid_iv_f = float(bid_iv)
            ask_iv_f = float(ask_iv)
            apy_f = float(apy)

            # Calculate mid IV only if we have valid IV values
            mid_iv = (bid_iv_f + ask_iv_f) / 2 if bid_iv_f > 0 and ask_iv_f > 0 else None

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

    return records


def extract_iv_from_raw_html(html: str) -> List[Dict[str, Any]]:
    """Fallback: extract IV data from raw HTML patterns."""
    records = []

    # Pattern to match IV entries with context
    # Look for patterns like: "strike":75000,..."bidIv":45.21,"askIv":46.35
    pattern = r'"strike"\s*:\s*([\d.]+)[^}]*?"bidIv"\s*:\s*([\d.]+)\s*,\s*"askIv"\s*:\s*([\d.]+)'
    matches = re.finditer(pattern, html)

    for match in matches:
        strike = float(match.group(1))
        bid_iv = float(match.group(2))
        ask_iv = float(match.group(3))

        # Try to find asset and expiry from context
        context_start = max(0, match.start() - 300)
        context = html[context_start:match.end() + 100]

        expiry = extract_quoted_field(context, 'expiry')
        is_put_match = re.search(r'"isPut"\s*:\s*(true|false)', context)
        is_put = is_put_match and is_put_match.group(1) == 'true'

        # Determine asset from context
        asset = None
        for a in ['BTC', 'ETH', 'SOL', 'HYPE', 'PURR', 'PUMP']:
            if f'"{a}"' in context or f':{a}:' in context.upper():
                asset = a
                break

        if asset:
            records.append({
                'asset': asset,
                'strike': strike,
                'expiry': expiry or 'unknown',
                'bid_iv': bid_iv,
                'ask_iv': ask_iv,
                'mid_iv': (bid_iv + ask_iv) / 2,
                'option_type': 'put' if is_put else 'call'
            })

    return records


def extract_quoted_field(text: str, field: str) -> Optional[str]:
    """Extract a quoted string field value."""
    pattern = rf'"{field}"\s*:\s*"([^"]+)"'
    match = re.search(pattern, text)
    return match.group(1) if match else None


def extract_float_field(text: str, field: str) -> Optional[float]:
    """Extract a float field value."""
    pattern = rf'"{field}"\s*:\s*([\d.]+)'
    match = re.search(pattern, text)
    return float(match.group(1)) if match else None


def extract_iv_from_next_data(data: dict) -> List[Dict[str, Any]]:
    """Extract IV data from Next.js page props."""
    records = []

    def search_dict(obj, path=""):
        if isinstance(obj, dict):
            # Check if this dict contains IV data
            if 'bidIv' in obj or 'askIv' in obj:
                record = extract_record_from_dict(obj)
                if record:
                    records.append(record)

            # Recurse into nested dicts
            for key, value in obj.items():
                search_dict(value, f"{path}.{key}")

        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                search_dict(item, f"{path}[{i}]")

    search_dict(data)
    return records


def extract_iv_from_script(script_content: str) -> List[Dict[str, Any]]:
    """Extract IV data from inline script content."""
    records = []

    # Pattern to find IV data in various formats
    # Look for bidIv/askIv patterns
    iv_pattern = r'"bidIv"\s*:\s*([\d.]+)\s*,\s*"askIv"\s*:\s*([\d.]+)'
    matches = re.finditer(iv_pattern, script_content)

    for match in matches:
        bid_iv = float(match.group(1))
        ask_iv = float(match.group(2))

        # Try to find associated asset, strike, expiry nearby
        context_start = max(0, match.start() - 500)
        context_end = min(len(script_content), match.end() + 200)
        context = script_content[context_start:context_end]

        record = {
            'bid_iv': bid_iv,
            'ask_iv': ask_iv,
            'mid_iv': (bid_iv + ask_iv) / 2,
            'asset': extract_field(context, ['asset', 'underlying', 'symbol']),
            'strike': extract_numeric_field(context, ['strike', 'strikePrice']),
            'expiry': extract_field(context, ['expiry', 'expiration', 'maturity']),
            'option_type': extract_option_type(context)
        }

        if record['asset'] and record['strike']:
            records.append(record)

    # Also look for inventory/quotes arrays
    inventory_pattern = r'"inventory"\s*:\s*(\[[\s\S]*?\])'
    inv_matches = re.finditer(inventory_pattern, script_content)
    for match in inv_matches:
        try:
            inventory = json.loads(match.group(1))
            for item in inventory:
                record = extract_record_from_dict(item)
                if record:
                    records.append(record)
        except json.JSONDecodeError:
            pass

    return records


def extract_iv_from_html_attributes(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Extract IV data from HTML data attributes."""
    records = []

    # Look for elements with data-* attributes containing IV info
    elements = soup.find_all(attrs={'data-iv': True})
    for elem in elements:
        try:
            data = json.loads(elem.get('data-iv', '{}'))
            record = extract_record_from_dict(data)
            if record:
                records.append(record)
        except json.JSONDecodeError:
            pass

    return records


def extract_record_from_dict(data: dict) -> Optional[Dict[str, Any]]:
    """Extract a standardized IV record from a dict."""
    bid_iv = data.get('bidIv') or data.get('bid_iv')
    ask_iv = data.get('askIv') or data.get('ask_iv')

    if bid_iv is None and ask_iv is None:
        return None

    bid_iv = float(bid_iv) if bid_iv else None
    ask_iv = float(ask_iv) if ask_iv else None

    # Calculate mid IV
    if bid_iv and ask_iv:
        mid_iv = (bid_iv + ask_iv) / 2
    else:
        mid_iv = bid_iv or ask_iv

    # Extract asset - check various field names
    asset = (
        data.get('asset') or
        data.get('underlying') or
        data.get('symbol') or
        data.get('baseAsset') or
        extract_asset_from_name(data.get('name', ''))
    )

    # Normalize asset names
    asset = normalize_asset(asset)

    strike = data.get('strike') or data.get('strikePrice')
    expiry = data.get('expiry') or data.get('expiration') or data.get('maturity')

    # Determine option type
    option_type = data.get('optionType') or data.get('type')
    if not option_type:
        name = str(data.get('name', '')).upper()
        if 'CALL' in name or '-C-' in name:
            option_type = 'call'
        elif 'PUT' in name or '-P-' in name:
            option_type = 'put'

    if not asset or strike is None:
        return None

    return {
        'asset': asset,
        'strike': float(strike),
        'expiry': str(expiry) if expiry else 'unknown',
        'bid_iv': bid_iv,
        'ask_iv': ask_iv,
        'mid_iv': mid_iv,
        'option_type': option_type
    }


def extract_field(context: str, field_names: List[str]) -> Optional[str]:
    """Extract a string field from context."""
    for name in field_names:
        pattern = rf'"{name}"\s*:\s*"([^"]+)"'
        match = re.search(pattern, context, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def extract_numeric_field(context: str, field_names: List[str]) -> Optional[float]:
    """Extract a numeric field from context."""
    for name in field_names:
        pattern = rf'"{name}"\s*:\s*([\d.]+)'
        match = re.search(pattern, context, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def extract_option_type(context: str) -> Optional[str]:
    """Extract option type (call/put) from context."""
    if re.search(r'"(type|optionType)"\s*:\s*"(call|CALL)"', context, re.IGNORECASE):
        return 'call'
    if re.search(r'"(type|optionType)"\s*:\s*"(put|PUT)"', context, re.IGNORECASE):
        return 'put'
    return None


def extract_asset_from_name(name: str) -> Optional[str]:
    """Extract asset symbol from option name."""
    if not name:
        return None
    # Common patterns: BTC-27FEB26-75000-P, ETH_CALL_3000
    match = re.match(r'^([A-Z]+)', name.upper())
    if match:
        return match.group(1)
    return None


def normalize_asset(asset: Optional[str]) -> Optional[str]:
    """Normalize asset names."""
    if not asset:
        return None

    asset = asset.upper()

    # Map common variations
    mappings = {
        'UBTC': 'BTC',
        'WBTC': 'BTC',
        'UETH': 'ETH',
        'WETH': 'ETH',
        'USOL': 'SOL',
        'WSOL': 'SOL',
    }

    return mappings.get(asset, asset)


def deduplicate_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate records based on asset/strike/expiry/type."""
    seen = set()
    unique = []

    for record in records:
        key = (
            record.get('asset'),
            record.get('strike'),
            record.get('expiry'),
            record.get('option_type')
        )
        if key not in seen:
            seen.add(key)
            unique.append(record)

    return unique


def fetch_and_store(use_browser: bool = False, wait_time: int = 8) -> int:
    """
    Fetch IV data and store in database.

    Args:
        use_browser: If True, use headless browser to capture dynamic content
        wait_time: Seconds to wait for dynamic content (browser mode only)

    Returns:
        Number of records stored
    """
    database.init_db()

    print(f"Fetching data from {config.TARGET_URL}...")

    if use_browser:
        html = fetch_all_asset_pages(wait_time=wait_time)
    else:
        html = fetch_page()

    print("Parsing IV data...")
    records = parse_iv_data(html)

    if records:
        count = database.save_snapshot(records)
        print(f"Stored {count} IV records at {datetime.utcnow().isoformat()}")
        return count
    else:
        print("No IV data found in page")
        return 0


def show_history(asset: Optional[str], days: int, strike: Optional[float] = None):
    """Display historical IV data."""
    database.init_db()

    records = database.get_history(asset=asset, days=days, strike=strike)

    if not records:
        print("No data found")
        return

    # Format for display
    table_data = []
    for r in records[:50]:  # Limit display
        table_data.append([
            r['timestamp'][:19],
            r['asset'],
            f"{r['strike']:.0f}",
            r['expiry'],
            r['option_type'] or '-',
            f"{r['bid_iv']:.2f}%" if r['bid_iv'] else '-',
            f"{r['ask_iv']:.2f}%" if r['ask_iv'] else '-',
            f"{r['mid_iv']:.2f}%" if r['mid_iv'] else '-',
        ])

    headers = ['Timestamp', 'Asset', 'Strike', 'Expiry', 'Type', 'Bid IV', 'Ask IV', 'Mid IV']
    print(tabulate(table_data, headers=headers, tablefmt='simple'))
    print(f"\nShowing {len(table_data)} of {len(records)} records")


def show_latest(asset: Optional[str] = None):
    """Show latest IV and APY values."""
    database.init_db()

    records = database.get_latest(asset=asset)

    if not records:
        print("No data found")
        return

    table_data = []
    for r in records:
        apy = r.get('apy')
        table_data.append([
            r['asset'],
            f"{r['strike']:.2f}",
            r['expiry'],
            r['option_type'] or '-',
            f"{r['bid_iv']:.2f}%" if r['bid_iv'] else '-',
            f"{r['ask_iv']:.2f}%" if r['ask_iv'] else '-',
            f"{r['mid_iv']:.2f}%" if r['mid_iv'] else '-',
            f"{apy:.2f}%" if apy else '-',
        ])

    headers = ['Asset', 'Strike', 'Expiry', 'Type', 'Bid IV', 'Ask IV', 'Mid IV', 'APY']
    print(tabulate(table_data, headers=headers, tablefmt='simple'))


def show_assets():
    """Show tracked assets."""
    database.init_db()

    assets = database.get_assets()

    if not assets:
        print("No assets tracked yet. Run 'fetch' first.")
        return

    print("Tracked assets:")
    for asset in assets:
        print(f"  - {asset}")


def export_data(filepath: str, asset: Optional[str], days: int):
    """Export data to CSV."""
    database.init_db()

    count = database.export_to_csv(filepath, asset=asset, days=days)

    if count:
        print(f"Exported {count} records to {filepath}")
    else:
        print("No data to export")


def run_daemon(interval: int, use_browser: bool = False):
    """Run continuous data collection."""
    import schedule

    mode = "browser" if use_browser else "simple"
    print(f"Starting daemon mode (interval: {interval}s, mode: {mode})")
    print("Press Ctrl+C to stop\n")

    # Run once immediately
    fetch_and_store(use_browser=use_browser)

    # Schedule periodic runs
    schedule.every(interval).seconds.do(lambda: fetch_and_store(use_browser=use_browser))

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping daemon...")


def start_dashboard():
    """Start the web dashboard."""
    from dashboard import app
    print(f"Starting dashboard at http://localhost:{config.DASHBOARD_PORT}")
    app.run(host='0.0.0.0', port=config.DASHBOARD_PORT, debug=False)


def main():
    parser = argparse.ArgumentParser(
        description='Rysk Finance Implied Volatility Tracker',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # fetch command
    fetch_parser = subparsers.add_parser('fetch', help='Fetch IV data once')
    fetch_parser.add_argument(
        '--browser', '-b',
        action='store_true',
        help='Use headless browser to capture dynamic/WebSocket data (slower but more complete)'
    )
    fetch_parser.add_argument(
        '--wait', '-w',
        type=int,
        default=8,
        help='Seconds to wait for dynamic content (browser mode only, default: 8)'
    )

    # history command
    history_parser = subparsers.add_parser('history', help='View historical data')
    history_parser.add_argument('--asset', '-a', help='Filter by asset')
    history_parser.add_argument('--days', '-d', type=int, default=7, help='Days of history')
    history_parser.add_argument('--strike', '-s', type=float, help='Filter by strike')

    # latest command
    latest_parser = subparsers.add_parser('latest', help='View latest IV values')
    latest_parser.add_argument('--asset', '-a', help='Filter by asset')

    # assets command
    subparsers.add_parser('assets', help='List tracked assets')

    # export command
    export_parser = subparsers.add_parser('export', help='Export data to CSV')
    export_parser.add_argument('--output', '-o', default='iv_export.csv', help='Output file')
    export_parser.add_argument('--asset', '-a', help='Filter by asset')
    export_parser.add_argument('--days', '-d', type=int, default=30, help='Days to export')

    # daemon command
    daemon_parser = subparsers.add_parser('daemon', help='Run continuous collection')
    daemon_parser.add_argument(
        '--interval', '-i',
        type=int,
        default=config.DEFAULT_INTERVAL,
        help=f'Collection interval in seconds (default: {config.DEFAULT_INTERVAL})'
    )
    daemon_parser.add_argument(
        '--browser', '-b',
        action='store_true',
        help='Use headless browser for fetching'
    )

    # dashboard command
    subparsers.add_parser('dashboard', help='Start web dashboard')

    args = parser.parse_args()

    if args.command == 'fetch':
        fetch_and_store(use_browser=args.browser, wait_time=args.wait)
    elif args.command == 'history':
        show_history(args.asset, args.days, args.strike)
    elif args.command == 'latest':
        show_latest(args.asset)
    elif args.command == 'assets':
        show_assets()
    elif args.command == 'export':
        export_data(args.output, args.asset, args.days)
    elif args.command == 'daemon':
        run_daemon(args.interval, use_browser=args.browser)
    elif args.command == 'dashboard':
        start_dashboard()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
