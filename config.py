"""Configuration settings for Rysk IV Tracker."""

TARGET_URL = "https://app.rysk.finance"
DB_PATH = "data/iv_history.db"
DEFAULT_INTERVAL = 3600  # 1 hour in seconds
DASHBOARD_PORT = 8080

# Request settings
REQUEST_TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
