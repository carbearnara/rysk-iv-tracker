"""Flask web dashboard for Rysk IV Tracker."""

from flask import Flask, jsonify, render_template, request

import config
import database

app = Flask(__name__)


@app.before_request
def init():
    """Initialize database on first request."""
    database.init_db()


@app.route('/')
def index():
    """Main dashboard page."""
    return render_template('dashboard.html')


@app.route('/api/assets')
def api_assets():
    """Get list of tracked assets."""
    assets = database.get_assets()
    return jsonify(assets)


@app.route('/api/iv/<asset>')
def api_iv(asset: str):
    """
    Get IV time series for an asset.

    Query params:
        days: Number of days (default 30)
        strike: Filter by strike price
        expiry: Filter by expiry date
    """
    days = request.args.get('days', 30, type=int)
    strike = request.args.get('strike', type=float)
    expiry = request.args.get('expiry')

    data = database.get_iv_timeseries(
        asset=asset,
        days=days,
        strike=strike,
        expiry=expiry
    )

    return jsonify(data)


@app.route('/api/latest')
def api_latest():
    """Get latest IV values."""
    asset = request.args.get('asset')
    data = database.get_latest(asset=asset)
    return jsonify(data)


@app.route('/api/strikes/<asset>')
def api_strikes(asset: str):
    """Get available strikes and expiries for an asset."""
    data = database.get_strikes_and_expiries(asset)
    return jsonify(data)


@app.route('/api/history')
def api_history():
    """
    Get historical data.

    Query params:
        asset: Filter by asset
        days: Number of days (default 7)
        strike: Filter by strike
        expiry: Filter by expiry
    """
    asset = request.args.get('asset')
    days = request.args.get('days', 7, type=int)
    strike = request.args.get('strike', type=float)
    expiry = request.args.get('expiry')

    data = database.get_history(
        asset=asset,
        days=days,
        strike=strike,
        expiry=expiry
    )

    return jsonify(data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=config.DASHBOARD_PORT, debug=True)
