"""
Implied Volatility Calculator

Calculates IV from APY (option premium yield) using Black-Scholes model.

APY = (Premium / Collateral) * (365 / DTE)

For covered calls: Collateral = Spot Price, Premium = Call Price
For cash-secured puts: Collateral = Strike Price, Premium = Put Price

We reverse-solve Black-Scholes to find the IV that produces the observed premium.
"""

import math
from scipy.stats import norm
from scipy.optimize import brentq
from typing import Optional, Tuple


def black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Calculate Black-Scholes call option price.

    Args:
        S: Spot price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility

    Returns:
        Call option price
    """
    if T <= 0 or sigma <= 0:
        return max(0, S - K)

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    call_price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return call_price


def black_scholes_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Calculate Black-Scholes put option price.

    Args:
        S: Spot price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility

    Returns:
        Put option price
    """
    if T <= 0 or sigma <= 0:
        return max(0, K - S)

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    put_price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return put_price


def premium_from_apy(apy: float, collateral: float, dte: float) -> float:
    """
    Calculate option premium from APY.

    Args:
        apy: Annual percentage yield (as decimal, e.g., 0.50 for 50%)
        collateral: Collateral amount (spot for calls, strike for puts)
        dte: Days to expiry

    Returns:
        Option premium
    """
    return apy * collateral * (dte / 365.0)


def implied_volatility_from_apy(
    spot: float,
    strike: float,
    dte: float,
    apy: float,
    is_put: bool,
    risk_free_rate: float = 0.05
) -> Optional[float]:
    """
    Calculate implied volatility from APY using Black-Scholes.

    Args:
        spot: Current spot price
        strike: Option strike price
        dte: Days to expiry
        apy: Annual percentage yield (as percentage, e.g., 50 for 50%)
        is_put: True for put, False for call
        risk_free_rate: Risk-free rate (default 5%)

    Returns:
        Implied volatility as percentage (e.g., 45.5 for 45.5%), or None if calculation fails
    """
    if dte <= 0 or apy <= 0 or spot <= 0 or strike <= 0:
        return None

    # Convert APY from percentage to decimal
    apy_decimal = apy / 100.0

    # Time to expiry in years
    T = dte / 365.0

    # Calculate collateral and target premium
    if is_put:
        # Cash-secured put: collateral is strike price
        collateral = strike
        target_premium = premium_from_apy(apy_decimal, collateral, dte)
        price_func = lambda sigma: black_scholes_put(spot, strike, T, risk_free_rate, sigma)
    else:
        # Covered call: collateral is spot price
        collateral = spot
        target_premium = premium_from_apy(apy_decimal, collateral, dte)
        price_func = lambda sigma: black_scholes_call(spot, strike, T, risk_free_rate, sigma)

    # Use Brent's method to find IV
    # Search between 1% and 500% volatility
    try:
        iv = brentq(
            lambda sigma: price_func(sigma) - target_premium,
            0.01,  # 1% IV minimum
            5.0,   # 500% IV maximum
            xtol=1e-6
        )
        return iv * 100  # Return as percentage
    except (ValueError, RuntimeError):
        # If Brent's method fails, try a wider range or return None
        try:
            iv = brentq(
                lambda sigma: price_func(sigma) - target_premium,
                0.001,  # 0.1% IV minimum
                10.0,   # 1000% IV maximum
                xtol=1e-6
            )
            return iv * 100
        except (ValueError, RuntimeError):
            return None


def calculate_iv_for_record(
    record: dict,
    spot_prices: dict,
    risk_free_rate: float = 0.05
) -> Optional[float]:
    """
    Calculate IV for a single database record.

    Args:
        record: Database record with asset, strike, expiry, option_type, apy
        spot_prices: Dict mapping asset symbols to spot prices
        risk_free_rate: Risk-free rate

    Returns:
        Calculated IV as percentage, or None if calculation fails
    """
    asset = record.get('asset')
    strike = record.get('strike')
    apy = record.get('apy')
    option_type = record.get('option_type')
    expiry = record.get('expiry')

    if not all([asset, strike, apy, option_type, expiry]):
        return None

    spot = spot_prices.get(asset)
    if not spot:
        return None

    # Parse expiry to get DTE
    dte = parse_expiry_to_dte(expiry)
    if dte is None or dte <= 0:
        return None

    is_put = option_type.lower() == 'put'

    return implied_volatility_from_apy(
        spot=spot,
        strike=strike,
        dte=dte,
        apy=apy,
        is_put=is_put,
        risk_free_rate=risk_free_rate
    )


def parse_expiry_to_dte(expiry: str) -> Optional[float]:
    """
    Parse expiry string (e.g., '27FEB26', '6FEB26') to days to expiry.

    Args:
        expiry: Expiry string in format 'DDMMMYY'

    Returns:
        Days to expiry, or None if parsing fails
    """
    from datetime import datetime

    try:
        # Parse format like '27FEB26' or '6FEB26'
        expiry_date = datetime.strptime(expiry, '%d%b%y')
        now = datetime.utcnow()
        delta = expiry_date - now
        return max(0, delta.days + delta.seconds / 86400)
    except ValueError:
        return None


def fetch_spot_prices() -> dict:
    """
    Fetch current spot prices from Rysk Finance page.

    Returns:
        Dict mapping asset symbols to spot prices
    """
    import requests
    import re

    url = 'https://app.rysk.finance'
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

    try:
        response = requests.get(url, headers=headers, timeout=30)
        html = response.text.replace('\\"', '"')

        spot_prices = {}

        # Extract index prices from serverInventory
        # Pattern: "ASSET":{"combinations":{..."index":PRICE...
        assets = ['BTC', 'ETH', 'SOL', 'HYPE', 'PURR', 'PUMP', 'ZEC', 'XRP']

        for asset in assets:
            # Find the asset section
            pattern = rf'"{asset}":\{{"combinations":\{{[^}}]*?"index":([\d.]+)'
            match = re.search(pattern, html)
            if match:
                spot_prices[asset] = float(match.group(1))

        return spot_prices
    except Exception as e:
        print(f"Error fetching spot prices: {e}")
        return {}


if __name__ == '__main__':
    # Test the calculator
    print("Testing IV Calculator\n")

    # Fetch spot prices
    print("Fetching spot prices...")
    spot_prices = fetch_spot_prices()
    for asset, price in spot_prices.items():
        print(f"  {asset}: ${price:,.2f}")

    print("\nTest calculations:")

    # Test with HYPE data
    # HYPE spot ~$29.77, Strike $31.5, APY 62.43%, Call, ~30 DTE
    test_cases = [
        {'spot': 29.77, 'strike': 31.5, 'dte': 30, 'apy': 62.43, 'is_put': False, 'name': 'HYPE $31.5 Call'},
        {'spot': 29.77, 'strike': 29.0, 'dte': 9, 'apy': 112.61, 'is_put': True, 'name': 'HYPE $29 Put'},
        {'spot': 89000, 'strike': 91000, 'dte': 9, 'apy': 50.18, 'is_put': False, 'name': 'BTC $91k Call'},
        {'spot': 3050, 'strike': 2950, 'dte': 9, 'apy': 75.99, 'is_put': True, 'name': 'ETH $2950 Put'},
    ]

    for tc in test_cases:
        iv = implied_volatility_from_apy(
            spot=tc['spot'],
            strike=tc['strike'],
            dte=tc['dte'],
            apy=tc['apy'],
            is_put=tc['is_put']
        )
        print(f"  {tc['name']}: IV = {iv:.2f}%" if iv else f"  {tc['name']}: IV calculation failed")
