# Maintenance Notes

## Periodic Tasks

### Check Rysk Finance Asset Listings
- **Frequency:** Check periodically (weekly recommended)
- **URL:** https://app.rysk.finance
- **Action:** Look for new assets or delisted assets

#### Current Assets Being Tracked
| Asset | URL Path | Status |
|-------|----------|--------|
| BTC | `/earn/999/UBTC/UBTC/USDT0/put/` | Active |
| ETH | `/earn/999/UETH/UETH/USDT0/put/` | Active |
| SOL | `/earn/999/USOL/USOL/USDT0/put/` | Active |
| HYPE | `/earn/999/WHYPE/WHYPE/USDT0/call/` | Active |
| PUMP | `/earn/999/UPUMP/UPUMP/USDT0/call/` | Active |
| PURR | `/earn/999/PURR/PURR/USDT0/call/` | Active |
| XRP | `/earn/999/fXRP/fXRP/USDT0/call/` | Active |
| ZEC | `/earn/999/bZEC/bZEC/USDT0/call/` | Active |

#### To Add a New Asset
1. Find the asset's earn page URL on Rysk Finance
2. Add the URL path to `tracker.py` in the `asset_pages` list (~line 130)
3. If the asset symbol has a prefix (like `fXRP`, `bZEC`, `UBTC`), add a mapping in the `normalize_asset_name()` function (~line 591)

#### To Remove a Delisted Asset
1. Remove the URL from `asset_pages` in `tracker.py`
2. Optionally clean up old data from the database
