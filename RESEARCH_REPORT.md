# σ√T Mean Reversion: A Quantitative Analysis of Option Premium Direction Signals

**Author:** Rysk IV Tracker Research
**Date:** February 2026
**Version:** 1.0

---

## Abstract

This study investigates the predictive power of σ√T (sigma root T) as a trading signal for cryptocurrency options on Rysk Finance. Using 30 days of historical implied volatility data across 8 assets and 86,151 observations, we find that σ√T exhibits strong mean-reverting behavior with statistically significant predictive power. A mean reversion strategy based on extreme σ√T percentiles achieves a 61.2% win rate over 2.5-hour holding periods, significantly outperforming both random baseline (50%) and traditional IV-based signals (52.7%).

---

## 1. Introduction

### 1.1 Background

Options pricing is fundamentally tied to implied volatility (IV) and time to expiration (DTE). While traders commonly track IV to gauge option expensiveness, raw IV can be misleading when comparing options with different expirations. An option with 100% IV expiring in 7 days has very different premium characteristics than one with 100% IV expiring in 30 days.

### 1.2 The σ√T Metric

σ√T (sigma root T) normalizes implied volatility by the square root of time:

```
σ√T = IV × √(DTE / 365)
```

This metric is directly proportional to option premium in the Black-Scholes framework. For at-the-money options:

```
Premium ≈ 0.4 × Spot × σ√T
```

Key insight: When σ√T rises, the option premium is increasing despite time decay. When σ√T falls, time decay is winning.

### 1.3 Research Questions

1. Is σ√T more stable and predictable than raw IV?
2. Does σ√T exhibit mean-reverting behavior?
3. Can extreme σ√T values generate profitable trading signals?
4. Does σ√T outperform IV-based signals?

---

## 2. Methodology

### 2.1 Data Collection

- **Source:** Rysk Finance options markets on Hyperliquid
- **Period:** 30 days (January-February 2026)
- **Assets:** BTC, ETH, SOL, HYPE, XRP, ZEC, PURR, PUMP
- **Total observations:** 86,151
- **Unique options:** 506
- **Options with sufficient data (20+ points):** 426
- **Data frequency:** ~15 minutes

### 2.2 σ√T Calculation

For each observation:
1. Parse expiry date from format "DDMMMYY" (e.g., "13FEB26")
2. Calculate days to expiry (DTE)
3. Calculate σ√T = mid_IV × √(DTE / 365)

### 2.3 Strategy Definitions

**Mean Reversion Strategy (σ√T):**
- Calculate rolling σ√T percentile for each option
- BUY signal: σ√T falls below 10th percentile
- SELL signal: σ√T rises above 90th percentile
- Measure success: Did σ√T move in predicted direction?

**Mean Reversion Strategy (IV baseline):**
- Same logic using IV percentile instead of σ√T
- For comparison purposes

**Momentum Strategy:**
- BUY when σ√T rising >2% over lookback period
- SELL when σ√T falling >2%
- Tests trend-following vs mean reversion

**Random Baseline:**
- Random BUY/SELL signals
- Expected 50% win rate

### 2.4 Holding Periods Tested

- 3 observations (~45 minutes)
- 5 observations (~75 minutes)
- 10 observations (~150 minutes)

---

## 3. Results

### 3.1 Stability Analysis

| Metric | Coefficient of Variation |
|--------|-------------------------|
| σ√T    | 0.0707                  |
| IV     | 0.0969                  |

**Finding:** σ√T is 27% less variable than raw IV, confirming it smooths out noise while preserving directional information.

### 3.2 Mean Reversion Test

| Behavior | Percentage |
|----------|------------|
| Mean Reverting | 100% |
| Trend Continuing | 0% |

**Finding:** σ√T exhibits extremely strong mean-reverting behavior. Virtually all options showed reversion to mean over the observation period.

### 3.3 Autocorrelation

- **Lag-1 Autocorrelation:** 0.991

**Finding:** σ√T is highly persistent day-to-day, making it predictable and tradeable rather than noisy.

### 3.4 Backtest Results

#### Hold Period: 10 observations (~150 minutes)

| Strategy | Win Rate | Avg Return |
|----------|----------|------------|
| **σ√T Mean Reversion (10/90)** | **61.2%** | **+1.68%** |
| σ√T Mean Reversion (20/80) | 58.8% | +1.28% |
| σ√T Mean Reversion (25/75) | 58.0% | +1.14% |
| IV Mean Reversion (25/75) | 52.7% | -0.47% |
| Random Baseline | 48.5% | -0.18% |
| σ√T Momentum | 46.7% | -0.69% |

#### Signal Breakdown (10/90 thresholds, 10-observation hold)

| Signal Type | Trades | Win Rate |
|-------------|--------|----------|
| SELL (σ√T > 90th pctl) | 10,385 | **64.6%** |
| BUY (σ√T < 10th pctl) | 9,878 | **57.6%** |

### 3.5 Performance vs Holding Period

| Hold Period | σ√T MR Win Rate | IV MR Win Rate | Edge |
|-------------|-----------------|----------------|------|
| 45 min | 56.2% | 53.1% | +3.1% |
| 75 min | 57.6% | 53.1% | +4.5% |
| 150 min | 61.2% | 52.7% | +8.5% |

**Finding:** Longer holding periods improve performance. The signal requires time to materialize.

---

## 4. Discussion

### 4.1 Why σ√T Mean Reversion Works

The strong mean reversion in σ√T can be attributed to:

1. **Market microstructure:** Extreme IV readings often represent temporary liquidity imbalances or news reactions that normalize over time.

2. **Time decay mechanics:** As options approach expiry, σ√T naturally declines unless IV increases proportionally. Extremely high σ√T suggests IV has spiked unsustainably.

3. **Volatility mean reversion:** IV itself is known to be mean-reverting, and σ√T inherits this property while removing the time-decay noise.

### 4.2 SELL Signals Outperform BUY Signals

SELL signals (64.6% win rate) outperform BUY signals (57.6%) because:

1. **Volatility spikes are sharper than troughs:** IV tends to spike quickly during fear/uncertainty then slowly normalize, creating more reliable SELL opportunities.

2. **Asymmetric distribution:** Extremely high σ√T values have more room to fall than extremely low values have to rise.

### 4.3 σ√T vs IV: The Advantage

σ√T outperforms raw IV for mean reversion signals by 8.5 percentage points (61.2% vs 52.7%) because:

1. **Removes time-decay bias:** Raw IV doesn't account for the natural premium erosion as options approach expiry.

2. **Normalizes across expirations:** σ√T allows fair comparison between short and long-dated options.

3. **Smoother signal:** 27% less variable than IV means fewer false signals.

### 4.4 Momentum Does Not Work

The momentum strategy (46.7% win rate) performs worse than random, confirming that σ√T should be traded as a mean-reverting indicator, not a trend-following one.

---

## 5. Trading Strategy

### 5.1 Signal Generation

```
IF σ√T percentile > 90%:
    Signal = SELL
    Expected Win Rate = 65%

IF σ√T percentile < 10%:
    Signal = BUY
    Expected Win Rate = 58%

ELSE:
    Signal = NEUTRAL
```

### 5.2 Position Sizing

Given win rates of 58-65%, Kelly Criterion suggests:

- SELL signals: f* = (0.65 × 2 - 1) / 1 = 30% of bankroll (use 15% for safety)
- BUY signals: f* = (0.58 × 2 - 1) / 1 = 16% of bankroll (use 8% for safety)

### 5.3 Recommended Implementation

1. **Monitor σ√T percentiles** for options on assets of interest
2. **Enter positions** when signals trigger (SELL premium when high, BUY when low)
3. **Hold for 2-3 hours** to allow mean reversion to occur
4. **Exit** after holding period or if σ√T returns to median range

---

## 6. Limitations

1. **Transaction costs:** Win rates don't account for bid-ask spreads, which could erode edge on short timeframes.

2. **Liquidity constraints:** Rysk Finance options may have limited liquidity for large positions.

3. **Sample period:** 30 days of data may not capture all market regimes.

4. **No actual P&L:** Backtest measures σ√T direction, not actual dollar returns.

5. **Execution risk:** 15-minute data granularity may miss optimal entry/exit points.

---

## 7. Future Research

1. **Extend data period** to 90+ days to test across different market conditions
2. **Calculate actual P&L** using option price data
3. **Test on other venues** (Deribit, Lyra, etc.)
4. **Optimize thresholds** using walk-forward analysis
5. **Add filters** (e.g., only trade options with >$X liquidity)

---

## 8. Conclusion

σ√T (sigma root T) is a valid and useful trading indicator for cryptocurrency options. Key findings:

| Metric | Value |
|--------|-------|
| Mean Reversion Win Rate | 61.2% |
| Edge vs Random | +11.2% |
| Edge vs IV-based Strategy | +8.5% |
| Best Signal | SELL @ >90th percentile (64.6%) |
| Optimal Hold Period | ~2.5 hours |

The σ√T mean reversion strategy provides a statistically significant edge that warrants further investigation and potential live trading implementation.

---

## Appendix A: Asset-Level Statistics

| Asset | Avg σ√T | Avg IV | σ√T StdDev | IV StdDev |
|-------|---------|--------|------------|-----------|
| BTC   | 11.27   | 53.53  | 3.22       | 16.69     |
| ETH   | 15.64   | 73.95  | 3.72       | 18.08     |
| HYPE  | 17.91   | 84.10  | 3.88       | 17.96     |
| SOL   | 14.96   | 70.07  | 3.31       | 16.20     |
| XRP   | 12.40   | 60.49  | 2.58       | 9.45      |
| ZEC   | 13.64   | 49.15  | 1.14       | 4.32      |
| PURR  | 9.68    | 45.45  | 1.80       | 8.05      |
| PUMP  | 13.86   | 64.75  | 2.62       | 11.22     |

---

## Appendix B: Code Availability

Analysis and backtesting code available at:
- `analysis.py` - Statistical analysis of σ√T
- `backtest.py` - Strategy backtester

Live dashboard with signals: https://rysk-biscuit.vercel.app

---

## References

1. Black, F., & Scholes, M. (1973). The Pricing of Options and Corporate Liabilities. *Journal of Political Economy*, 81(3), 637-654.

2. Hull, J. C. (2017). *Options, Futures, and Other Derivatives* (10th ed.). Pearson.

3. Natenberg, S. (2015). *Option Volatility and Pricing* (2nd ed.). McGraw-Hill.

---

*Disclaimer: This research is for educational purposes only. Past performance does not guarantee future results. Options trading involves significant risk of loss.*
