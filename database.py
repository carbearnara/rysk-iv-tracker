"""Database operations for Rysk IV Tracker."""

import sqlite3
import os
from datetime import datetime, timedelta
from typing import Optional
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get database connection, creating directory if needed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the database schema."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS iv_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
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

    # Add apy column if it doesn't exist (for existing databases)
    try:
        cursor.execute("ALTER TABLE iv_snapshots ADD COLUMN apy REAL")
    except sqlite3.OperationalError:
        pass  # Column already exists

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_asset_time
        ON iv_snapshots(asset, timestamp)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp
        ON iv_snapshots(timestamp)
    """)

    conn.commit()
    conn.close()


def save_snapshot(records: list[dict]) -> int:
    """
    Save IV snapshot records to database.

    Args:
        records: List of dicts with keys: asset, strike, expiry,
                 bid_iv, ask_iv, mid_iv, option_type, apy

    Returns:
        Number of records saved
    """
    if not records:
        return 0

    # Filter out invalid records (must have valid IV values OR valid APY)
    valid_records = [
        r for r in records
        if r.get('asset') and r.get('strike')
        and (
            (r.get('bid_iv') and r.get('ask_iv') and r.get('bid_iv') > 0 and r.get('ask_iv') > 0)
            or (r.get('apy') and r.get('apy') > 0)
        )
    ]

    if not valid_records:
        return 0

    conn = get_connection()
    cursor = conn.cursor()
    timestamp = datetime.utcnow().isoformat()

    for record in valid_records:
        cursor.execute("""
            INSERT INTO iv_snapshots
            (timestamp, asset, strike, expiry, bid_iv, ask_iv, mid_iv, option_type, apy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            record.get('asset'),
            record.get('strike'),
            record.get('expiry'),
            record.get('bid_iv'),
            record.get('ask_iv'),
            record.get('mid_iv'),
            record.get('option_type'),
            record.get('apy')
        ))

    conn.commit()
    conn.close()
    return len(valid_records)


def get_assets() -> list[str]:
    """Get list of unique assets in database."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT asset FROM iv_snapshots ORDER BY asset")
    assets = [row['asset'] for row in cursor.fetchall()]

    conn.close()
    return assets


def get_history(
    asset: Optional[str] = None,
    days: int = 7,
    strike: Optional[float] = None,
    expiry: Optional[str] = None
) -> list[dict]:
    """
    Get historical IV data.

    Args:
        asset: Filter by asset (optional)
        days: Number of days of history
        strike: Filter by strike price (optional)
        expiry: Filter by expiry date (optional)

    Returns:
        List of IV records
    """
    conn = get_connection()
    cursor = conn.cursor()

    since = (datetime.utcnow() - timedelta(days=days)).isoformat()

    query = "SELECT * FROM iv_snapshots WHERE timestamp > ?"
    params = [since]

    if asset:
        query += " AND asset = ?"
        params.append(asset)

    if strike:
        query += " AND strike = ?"
        params.append(strike)

    if expiry:
        query += " AND expiry = ?"
        params.append(expiry)

    query += " ORDER BY timestamp DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    conn.close()
    return [dict(row) for row in rows]


def get_latest(asset: Optional[str] = None) -> list[dict]:
    """
    Get the most recent IV snapshot for each asset/strike/expiry combination.

    Args:
        asset: Filter by asset (optional)

    Returns:
        List of latest IV records
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT * FROM iv_snapshots s1
        WHERE timestamp = (
            SELECT MAX(timestamp) FROM iv_snapshots s2
            WHERE s1.asset = s2.asset
            AND s1.strike = s2.strike
            AND s1.expiry = s2.expiry
        )
    """
    params = []

    if asset:
        query += " AND asset = ?"
        params.append(asset)

    query += " ORDER BY asset, strike, expiry"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    conn.close()
    return [dict(row) for row in rows]


def get_iv_timeseries(
    asset: str,
    days: int = 30,
    strike: Optional[float] = None,
    expiry: Optional[str] = None
) -> list[dict]:
    """
    Get IV time series for charting.

    Args:
        asset: Asset symbol
        days: Number of days
        strike: Filter by strike (optional)
        expiry: Filter by expiry (optional)

    Returns:
        List of {timestamp, bid_iv, ask_iv, mid_iv, strike, expiry}
    """
    conn = get_connection()
    cursor = conn.cursor()

    since = (datetime.utcnow() - timedelta(days=days)).isoformat()

    query = """
        SELECT timestamp, bid_iv, ask_iv, mid_iv, strike, expiry, option_type
        FROM iv_snapshots
        WHERE asset = ? AND timestamp > ?
    """
    params = [asset, since]

    if strike:
        query += " AND strike = ?"
        params.append(strike)

    if expiry:
        query += " AND expiry = ?"
        params.append(expiry)

    query += " ORDER BY timestamp ASC"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    conn.close()
    return [dict(row) for row in rows]


def get_strikes_and_expiries(asset: str) -> dict:
    """Get available strikes and expiries for an asset."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT DISTINCT strike FROM iv_snapshots WHERE asset = ? ORDER BY strike",
        (asset,)
    )
    strikes = [row['strike'] for row in cursor.fetchall()]

    cursor.execute(
        "SELECT DISTINCT expiry FROM iv_snapshots WHERE asset = ? ORDER BY expiry",
        (asset,)
    )
    expiries = [row['expiry'] for row in cursor.fetchall()]

    conn.close()
    return {'strikes': strikes, 'expiries': expiries}


def export_to_csv(filepath: str, asset: Optional[str] = None, days: int = 30) -> int:
    """
    Export data to CSV file.

    Args:
        filepath: Output file path
        asset: Filter by asset (optional)
        days: Number of days to export

    Returns:
        Number of rows exported
    """
    import csv

    data = get_history(asset=asset, days=days)

    if not data:
        return 0

    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)

    return len(data)
