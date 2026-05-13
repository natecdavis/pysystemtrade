"""
Full Carver-style trading rule library for crypto perpetual futures.

Implements rule families not available in pysystemtrade core:
- Normmom: Vol-normalised momentum
- Assettrend: ADV-weighted asset-class index momentum
- BtcLeadLag: BTC returns as leading signal for alts
- FundingCarry: Smoothed annualised funding rate signal
- Relcarry: Instrument funding relative to cross-sectional median
- FundingMR: Mean-reversion from extreme funding rates
- STreversal: Short-term price reversal
- ReturnSkew: Negative rolling skewness
- Mrinasset: Asset-class index mean-reversion
- Illiquidity: Amihud illiquidity defensive signal

All functions follow pysystemtrade convention:
  - Positional args = data series injected from YAML config ``data:`` field,
    called as stage.method(instrument_code) in the order listed
  - Keyword args = values from ``other_args:`` field
  - Return: pd.Series (unscaled, uncapped forecast)
"""

import pandas as pd
import numpy as np

from sysquant.estimators.vol import robust_vol_calc
from systems.provided.rules.ewmac import ewmac, ewmac_calc_vol


# ============================================================================
# DIVERGENT RULES
# ============================================================================


def normmom(price: pd.Series, vol: pd.Series, Lfast: int = 16) -> pd.Series:
    """
    Normalised momentum: EWMAC applied to cumulative vol-normalised return series.

    Differs from plain EWMAC in that the input series is vol-normalised before
    differencing, making the signal price-level independent and comparable
    across instruments with very different price scales.

    Args:
        price: Daily price series (data.daily_prices)
        vol: Daily price volatility — unit, not % (rawdata.daily_returns_volatility)
        Lfast: Fast EWMA lookback in days. Lslow is fixed to 4×Lfast.

    Returns:
        Unscaled, uncapped forecast series.
    """
    Lslow = Lfast * 4
    daily_ret = price.diff()
    vol_filled = vol.ffill().replace(0.0, np.nan).ffill()
    norm_ret = (daily_ret / vol_filled).fillna(0.0)
    cum_norm = norm_ret.cumsum()
    # Unit vol because cum_norm is already normalised
    unit_vol = pd.Series(1.0, index=cum_norm.index)
    return ewmac(cum_norm, unit_vol, Lfast=Lfast, Lslow=Lslow)


def assettrend(index_price: pd.Series, Lfast: int = 8) -> pd.Series:
    """
    Asset-class trend: EWMAC of ADV-weighted cross-asset price index.

    Generates a single market-regime signal identical for all instruments
    in the asset class. A rising index → long all, falling → short all.

    Args:
        index_price: ADV-weighted asset-class index price (data.get_asset_class_index_price)
        Lfast: Fast EWMA lookback in days. Lslow is fixed to 4×Lfast.

    Returns:
        Unscaled, uncapped forecast series.
    """
    Lslow = Lfast * 4
    return ewmac_calc_vol(index_price, Lfast=Lfast, Lslow=Lslow)


def btc_lead_lag(
    btc_price: pd.Series,
    instrument_price: pd.Series,
    lag_days: int = 1,
) -> pd.Series:
    """
    BTC lead-lag: lagged vol-normalised BTC return as a predictive signal for alts.

    Based on the empirical finding that BTC returns lead altcoin returns by
    1–2 days. Not applied to BTC itself (set forecast_weight = 0 for BTC).

    Args:
        btc_price: BTC daily price series (data.get_btc_price)
        instrument_price: Target instrument price series (data.daily_prices),
            used only to align the index of the returned signal.
        lag_days: Number of calendar days to lag the BTC signal.

    Returns:
        Unscaled, uncapped forecast series.
    """
    btc_vol = robust_vol_calc(btc_price.diff())
    btc_norm_ret = btc_price.diff() / btc_vol.ffill()
    signal = btc_norm_ret.shift(lag_days)
    return signal.reindex(instrument_price.index)


# ============================================================================
# CONVERGENT SUB-A: CARRY / FUNDING RULES
# ============================================================================


def funding_carry(funding_rates: pd.Series, smooth_days: int = 30) -> pd.Series:
    """
    Funding carry: smoothed annualised funding rate, negated.

    High positive funding → short bias (receive funding payments).
    High negative funding → long bias (receive carry via negative rate).

    Assumes 8-hourly funding payments: annualised as rate × 3 × 365.

    Args:
        funding_rates: Raw 8-hourly funding rate series (data.get_funding_rate).
            Typical scale: 0.0001 = 0.01% per 8h.
        smooth_days: EWM span for smoothing.

    Returns:
        Unscaled, uncapped forecast series.
    """
    ann_funding = funding_rates * 3 * 365
    smoothed = ann_funding.ewm(span=smooth_days, min_periods=1).mean()
    return -smoothed


def funding_momentum(
    funding_rates: pd.Series,
    Lfast: int = 16,
) -> pd.Series:
    """
    Funding rate trend: EWMAC on the annualised funding rate level.

    Captures whether funding is trending up or down over multi-week horizons.
    Rising funding trend → bullish (demand building, buyers paying more premium).
    Falling funding trend → bearish (sentiment souring).

    Orthogonal to:
    - funding_carry: trades the current level, not the trend direction
    - demeaned_carry: trades idiosyncratic level vs cross-section
    - funding_mr: fires only at extreme z-score spikes

    Args:
        funding_rates: Raw 8-hourly funding rate series (data.get_funding_rate).
        Lfast: Fast EWM span (slow = Lfast × 4).
    """
    if len(funding_rates.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=funding_rates.index)

    ann_funding = funding_rates * 3 * 365
    Lslow = Lfast * 4
    min_p = max(Lfast // 2, 2)
    raw = (
        ann_funding.ewm(span=Lfast, min_periods=min_p).mean()
        - ann_funding.ewm(span=Lslow, min_periods=min_p).mean()
    )
    roll_std = raw.rolling(Lslow * 2, min_periods=Lslow).std().clip(lower=1e-8)
    return (raw / roll_std).clip(-2.0, 2.0) * 10.0


def relcarry(
    funding_rates: pd.Series,
    median_funding: pd.Series,
    smooth_days: int = 30,
) -> pd.Series:
    """
    Relative carry: instrument funding rate relative to cross-sectional median.

    When this instrument's funding is above the median → more expensive to hold
    long → short bias. When below median → cheaper → long bias.

    Not applied to BTC (set forecast_weight = 0 for BTC).

    Args:
        funding_rates: Raw 8-hourly funding rate for this instrument
            (data.get_funding_rate).
        median_funding: Cross-sectional median of annualised funding rates
            across all instruments (data.get_cross_sectional_median_funding).
        smooth_days: EWM span for smoothing this instrument's funding.

    Returns:
        Unscaled, uncapped forecast series.
    """
    ann_funding = funding_rates * 3 * 365
    smoothed = ann_funding.ewm(span=smooth_days, min_periods=1).mean()
    median_aligned = median_funding.reindex(smoothed.index, method="ffill")
    return -(smoothed - median_aligned)


def funding_mr(
    funding_rates: pd.Series,
    window: int = 60,
    zscore_threshold: float = 2.0,
) -> pd.Series:
    """
    Funding mean-reversion: fires only when funding z-score exceeds threshold.

    Extreme funding tends to revert. When |z| > threshold, fade the extreme
    funding direction. Signal is zero when funding is within normal range.

    Args:
        funding_rates: Raw 8-hourly funding rate series (data.get_funding_rate).
        window: Rolling window for z-score calculation (days).
        zscore_threshold: Minimum absolute z-score to generate a non-zero signal.

    Returns:
        Unscaled forecast series (zero except at extreme funding episodes).
    """
    min_p = max(window // 2, 2)
    roll_mean = funding_rates.rolling(window, min_periods=min_p).mean()
    roll_std = funding_rates.rolling(window, min_periods=min_p).std()
    zscore = (funding_rates - roll_mean) / roll_std.clip(lower=1e-8)
    signal = pd.Series(0.0, index=funding_rates.index)
    fires = zscore.abs() > zscore_threshold
    # Scale so z=2 ≈ signal 20 (at forecast cap); opposite direction to funding
    signal[fires] = -zscore[fires] * 10
    return signal


def funding_crowd(
    funding_rates: pd.Series,
    window: int = 60,
    threshold: float = 1.0,
    persist_days: int = 5,
) -> pd.Series:
    """
    Persistent funding crowding: contrarian signal when funding stays elevated
    or depressed for persist_days+ consecutive days.

    Distinct from funding_mr which fires on single-day spikes (|z| > 2.0).
    This rule fires on moderate but sustained crowding (|z| > 1.0 for N+ days),
    capturing the regime where positions are building up before the washout.

    Args:
        funding_rates: Raw daily funding rate series (data.funding_rate).
        window:        Rolling window in days for z-score (default 60).
        threshold:     Z-score level to count as "crowded" (default 1.0).
        persist_days:  Min consecutive days above threshold (default 5).
    """
    min_p = max(window // 2, 2)
    roll_mean = funding_rates.rolling(window, min_periods=min_p).mean()
    roll_std  = funding_rates.rolling(window, min_periods=min_p).std()
    zscore    = (funding_rates - roll_mean) / roll_std.clip(lower=1e-8)

    # Consecutive-day streaks above/below threshold
    above = zscore >  threshold
    below = zscore < -threshold
    streak_long  = above.astype(int).groupby((above != above.shift()).cumsum()).cumsum()
    streak_short = below.astype(int).groupby((below != below.shift()).cumsum()).cumsum()

    # Signal: -zscore when active (same sign convention as funding_mr)
    # Long crowding (z > threshold N+ days): short bias (negative)
    # Short crowding (z < -threshold N+ days): long bias (positive)
    signal = pd.Series(0.0, index=funding_rates.index)
    signal[streak_long  >= persist_days] = -zscore[streak_long  >= persist_days]
    signal[streak_short >= persist_days] = -zscore[streak_short >= persist_days]

    return (signal * 10).fillna(0.0)


def lsr_divergence(
    toptrader_lsr: pd.Series,
    lsr: pd.Series,
    window: int = 60,
) -> pd.Series:
    """
    Smart money vs retail divergence. Directional: long when smart money
    more bullish than retail relative to history.

    Uses log(toptrader_lsr / lsr) z-score. Positive divergence = top traders
    more net-long than retail → follow smart money.

    Args:
        toptrader_lsr: Top-trader long/short ratio (data.get_toptrader_long_short_ratio).
        lsr:           Retail long/short ratio (data.get_long_short_ratio).
        window:        Rolling window for z-score (days).
    """
    combined = toptrader_lsr.to_frame('top').join(lsr.to_frame('retail'), how='inner')
    combined = combined[(combined > 0).all(axis=1)]
    if combined.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))

    log_ratio = np.log(combined['top'] / combined['retail'])
    roll_mean = log_ratio.rolling(window, min_periods=window // 2).mean()
    roll_std  = log_ratio.rolling(window, min_periods=window // 2).std()
    zscore    = (log_ratio - roll_mean) / roll_std.clip(lower=1e-8)
    return (zscore * 10).fillna(0.0)


def lsr_retail_crowding(
    lsr: pd.Series,
    window: int = 60,
) -> pd.Series:
    """
    Contrarian retail crowding. Short bias when retail accounts are very long,
    long bias when retail accounts are very short.

    Uses z-score of log(lsr), negated. Orthogonal to funding_mr (different data
    source: account positioning, not funding rate).

    Args:
        lsr:    Retail long/short ratio (data.get_long_short_ratio).
        window: Rolling window for z-score (days).
    """
    log_lsr   = np.log(lsr.clip(lower=1e-4))
    roll_mean = log_lsr.rolling(window, min_periods=window // 2).mean()
    roll_std  = log_lsr.rolling(window, min_periods=window // 2).std()
    zscore    = (log_lsr - roll_mean) / roll_std.clip(lower=1e-8)
    return (-zscore * 10).fillna(0.0)


def vol_normalized_carry(
    funding_rates: pd.Series,
    price: pd.Series,
    vol: pd.Series,
    smooth_days: int = 30,
    vol_floor: float = 0.01,
) -> pd.Series:
    """
    Vol-normalized carry: smoothed funding rate normalized by percentage volatility.

    This rule returns the raw carry score per instrument. Cross-sectional
    percentile ranking will be applied in the ForecastCombineGated stage,
    where all instruments' scores can be compared simultaneously.

    The score is negated so that high positive funding (expensive to hold long)
    → negative score → short bias.

    Args:
        funding_rates: Raw 8-hourly funding rate series (data.get_funding_rate).
            Typical scale: 0.0001 = 0.01% per 8h.
        price: Daily price series (data.daily_prices). Used to convert vol to
            percentage units so the carry score is price-level independent.
        vol: Daily price volatility in price units (rawdata.daily_returns_volatility).
        smooth_days: EWM span for smoothing funding (default 30).
        vol_floor: Minimum percentage volatility floor (default 0.01 = 1%/day).

    Returns:
        Unscaled carry score (raw, will be percentile-ranked in ForecastCombine).
    """
    # Smooth and annualize funding (3 payments/day × 365 days)
    ann_funding = funding_rates * 3 * 365
    f_smooth = ann_funding.ewm(span=smooth_days, min_periods=1).mean()

    # Convert vol to percentage units (dimensionless) to match funding rate units
    vol_aligned = vol.reindex(price.index).ffill().replace(0.0, np.nan).ffill()
    price_filled = price.ffill()
    pct_vol = (vol_aligned / price_filled).clip(lower=vol_floor)

    # Vol-normalize (negated so positive funding → short bias)
    carry_score = -f_smooth.reindex(pct_vol.index) / pct_vol

    return carry_score


# ============================================================================
# CONVERGENT SUB-B: SHORT-TERM / STRUCTURAL RULES
# ============================================================================


def streversal(price: pd.Series, vol: pd.Series, horizon: int = 1) -> pd.Series:
    """
    Short-term price reversal: negative N-day return normalised by daily vol.

    Negative recent return → positive forecast (expect mean reversion upward).

    Args:
        price: Daily price series (data.daily_prices).
        vol: Daily price volatility — unit, not % (rawdata.daily_returns_volatility).
        horizon: Return lookback in days.

    Returns:
        Unscaled, uncapped forecast series.
    """
    ret = price.diff(horizon)
    vol_filled = vol.ffill().replace(0.0, np.nan).ffill()
    return -(ret / vol_filled)


def return_skew(price: pd.Series, window: int = 20) -> pd.Series:
    """
    Return skewness: negative rolling skewness of daily returns, z-scored over time.

    Negative skewness (fat left tail) → positive forecast (expect skew reversion).
    Positive skewness (fat right tail) → negative forecast.

    Args:
        price: Daily price series (data.daily_prices).
        window: Rolling window for skewness calculation (days).

    Returns:
        Unscaled, uncapped forecast series.
    """
    daily_ret = price.pct_change()
    skew_series = daily_ret.rolling(window, min_periods=max(window // 2, 3)).skew()
    roll_mean = skew_series.rolling(252, min_periods=60).mean()
    roll_std = skew_series.rolling(252, min_periods=60).std()
    zscore = (skew_series - roll_mean) / roll_std.clip(lower=1e-8)
    return -zscore


def _get_round_numbers(price_series: pd.Series) -> list:
    """
    Return (round_number, significance_weight) pairs relevant to price_series range.
    Uses 1-2-5 series: coefficients {1, 2, 5} × 10^n.
    Weights: 1× multiples → 3 (most significant), 5× → 2, 2× → 1.
    """
    valid = price_series.dropna()
    if valid.empty:
        return []
    lo = max(valid.min(), 1e-8)
    hi = valid.max()
    exp_lo = int(np.floor(np.log10(lo))) - 1
    exp_hi = int(np.floor(np.log10(hi))) + 1
    coeff_weight = {1: 3, 5: 2, 2: 1}
    result = []
    for exp in range(exp_lo, exp_hi + 1):
        for coeff, w in coeff_weight.items():
            rn = coeff * (10.0 ** exp)
            if lo * 0.5 <= rn <= hi * 2.0:
                result.append((rn, w))
    result.sort(key=lambda x: x[0])
    return result


def round_number_break(price: pd.Series, lookback: int = 20) -> pd.Series:
    """
    Round-number breakout momentum.

    When price crosses a psychologically significant level (1-2-5 series × 10^n),
    clustered stop-losses and FOMO orders create a directional momentum burst.

    Signal = sum over all round-number crossings in the past `lookback` days of
    (significance_weight × linear_time_decay). Positive = net upward crossings.
    Sparse by design (~60-80% zeros); walk-forward scalar normalises scale.

    Args:
        price:    Daily price series (data.daily_prices).
        lookback: Days to accumulate crossing contributions (linear decay to 0).

    Returns:
        Unscaled, uncapped forecast series.
    """
    round_numbers = _get_round_numbers(price)
    if not round_numbers:
        return pd.Series(0.0, index=price.index)

    n = len(price)
    signal = pd.Series(0.0, index=price.index)
    price_filled = price.ffill().fillna(0.0)

    # decay_weights[0]=1/L (oldest lag), decay_weights[L-1]=1.0 (most recent)
    decay_weights = np.arange(1, lookback + 1, dtype=float) / lookback
    # Reversed kernel for causal FIR: kernel[0]=1.0 (lag 0), kernel[L-1]=1/L (lag L-1)
    kernel = decay_weights[::-1]

    for rn, rn_weight in round_numbers:
        side = np.sign(price_filled - rn)
        side_prev = side.shift(1).fillna(0.0)
        crossing = pd.Series(0.0, index=price.index)
        crossing[(side_prev < 0) & (side > 0)] =  float(rn_weight)
        crossing[(side_prev > 0) & (side < 0)] = -float(rn_weight)
        # Causal FIR: output[t] = sum_{lag=0}^{L-1} crossing[t-lag] * kernel[lag]
        conv = np.convolve(crossing.values, kernel, mode='full')[:n]
        signal += pd.Series(conv, index=price.index)

    return signal.fillna(0.0)


def round_number_prox(price: pd.Series, proximity_pct: float = 0.03) -> pd.Series:
    """
    Round-number proximity: continuous amplifier for near-threshold positions.

    Price approaching a round number from below → positive signal (stop-squeeze
    building, limit sellers being absorbed). Price just above a round number →
    negative signal (disappointed longs selling).

    When combined additively with existing trend forecasts this naturally amplifies
    positions when the trend direction aligns with an imminent round-number break:
    trend-long + price below $100 = stronger combined long; opposing signals cancel.

    Linear scale: 0 at outer edge of zone (±proximity_pct of rn), max at rn itself.

    Args:
        price:         Daily price series (data.daily_prices).
        proximity_pct: Half-width of proximity zone as fraction of round number.

    Returns:
        Unscaled, uncapped forecast series.
    """
    round_numbers = _get_round_numbers(price)
    if not round_numbers:
        return pd.Series(0.0, index=price.index)

    price_filled = price.ffill().fillna(0.0)
    signal = pd.Series(0.0, index=price.index)

    for rn, rn_weight in round_numbers:
        lo = rn * (1.0 - proximity_pct)
        hi = rn * (1.0 + proximity_pct)
        below = (price_filled >= lo) & (price_filled < rn)
        above = (price_filled >  rn) & (price_filled <= hi)
        signal[below] += float(rn_weight) * (price_filled[below] - lo) / (rn - lo)
        signal[above] -= float(rn_weight) * (price_filled[above] - rn) / (hi - rn)

    return signal.fillna(0.0)


def mrinasset(index_price: pd.Series, ma_window: int = 1000) -> pd.Series:
    """
    Asset-class index mean-reversion: deviation from long-term MA, inverted.

    When the asset class is well above its long-term average → short bias
    (expect reversion toward the mean). Only assigned non-zero weight for
    BTC and ETH (the most liquid, long-history instruments).

    Args:
        index_price: ADV-weighted asset-class index price
            (data.get_asset_class_index_price).
        ma_window: Rolling window for long-term moving average (days).

    Returns:
        Unscaled, uncapped forecast series.
    """
    ma = index_price.rolling(
        ma_window, min_periods=max(ma_window // 2, 60)
    ).mean()
    vol = robust_vol_calc(index_price.diff())
    deviation = (index_price - ma) / vol.ffill()
    return -deviation


def illiquidity(price: pd.Series, adv: pd.Series, window: int = 20) -> pd.Series:
    """
    Illiquidity defensive signal: rising Amihud illiquidity ratio → negative signal.

    The Amihud ratio (|return| / dollar_volume) measures market impact.
    When it rises, markets are thinning and prices may gap — a defensive
    signal to reduce exposure.

    Args:
        price: Daily price series (data.daily_prices).
        adv: Average daily volume in notional USD (data.get_adv_notional).
        window: Rolling window for smoothing the Amihud ratio (days).

    Returns:
        Unscaled forecast series (negative when illiquidity rising — defensive).
    """
    abs_ret = price.pct_change().abs()
    adv_filled = adv.reindex(price.index, method="ffill").clip(lower=1.0)
    amihud = abs_ret / adv_filled
    smoothed = amihud.rolling(window, min_periods=max(window // 2, 2)).mean()
    change = smoothed.diff()
    change_vol = change.rolling(252, min_periods=60).std()
    return -(change / change_vol.clip(lower=1e-12))


# ============================================================================
# MACRO-RESIDUALISED MOMENTUM
# ============================================================================


def _rolling_ols_residuals(
    y: pd.Series, X: pd.DataFrame, window: int
) -> pd.Series:
    """
    Rolling OLS: regress y on X (with intercept) and return point-in-time residuals.

    Only the final-date residual of each rolling window is stored (the actual
    out-of-sample error for that day given the trailing window's coefficients).

    Args:
        y: Dependent variable (instrument daily returns).
        X: Factor DataFrame (spx_ret, dxy_ret, yield_chg) — same index as y.
        window: Rolling window length in days.

    Returns:
        pd.Series of residuals with the same index as y; NaN for the first
        (window-1) observations.
    """
    n = len(y)
    residuals = pd.Series(np.nan, index=y.index, dtype=float)
    X_vals = X.values
    y_vals = y.values
    ones = np.ones((window, 1))

    for i in range(window - 1, n):
        X_win = np.hstack([ones, X_vals[i - window + 1 : i + 1]])
        y_win = y_vals[i - window + 1 : i + 1]
        try:
            coeffs, _, _, _ = np.linalg.lstsq(X_win, y_win, rcond=None)
            predicted = X_win[-1] @ coeffs
            residuals.iloc[i] = y_win[-1] - predicted
        except np.linalg.LinAlgError:
            pass

    return residuals


def _rolling_ols_fitted(
    y: pd.Series, X: pd.DataFrame, window: int
) -> pd.Series:
    """
    Rolling OLS: regress y on X (with intercept) and return point-in-time fitted values.

    Parallel to _rolling_ols_residuals — returns the predicted value for the last
    observation in each rolling window rather than the residual.
    """
    n = len(y)
    fitted = pd.Series(np.nan, index=y.index, dtype=float)
    X_vals = X.values
    y_vals = y.values
    ones = np.ones((window, 1))

    for i in range(window - 1, n):
        X_win = np.hstack([ones, X_vals[i - window + 1 : i + 1]])
        y_win = y_vals[i - window + 1 : i + 1]
        try:
            coeffs, _, _, _ = np.linalg.lstsq(X_win, y_win, rcond=None)
            fitted.iloc[i] = X_win[-1] @ coeffs
        except np.linalg.LinAlgError:
            pass

    return fitted


def residual_momentum(
    price: pd.Series,
    spx_price: pd.Series,
    dxy_price: pd.Series,
    us10y_yield: pd.Series,
    Lfast: int = 16,
    reg_window: int = 60,
) -> pd.Series:
    """
    Macro-residualised momentum.

    Runs a rolling OLS regression of instrument daily returns on three macro
    factors (SPX returns, DXY returns, 10Y yield changes). Cumulates the
    residuals — the part of the instrument's return that macro cannot explain —
    into a 'crypto-specific price' series and applies EWMAC.

    This signal is orthogonal to standard EWMAC by construction: it captures
    halving-cycle rallies and crypto-native catalysts while going quiet when BTC
    is simply tracking SPX risk-on/risk-off.

    Missing macro days (weekends, US holidays) are treated as zero-return days,
    which is conservative and avoids double-counting weekend moves.

    Args:
        price: Daily instrument price series (data.daily_prices).
        spx_price: S&P 500 daily close (data.get_spx_price).
        dxy_price: US Dollar Index daily close (data.get_dxy_price).
        us10y_yield: US 10Y yield in % (data.get_us10y_yield).
        Lfast: Fast EWMA lookback (days). Lslow is fixed at 4×Lfast.
        reg_window: Rolling OLS window (days). Default 60 ≈ one quarter.

    Returns:
        Unscaled, uncapped forecast series.
    """
    # Guard: return NaN if any macro series is unavailable (empty)
    if len(spx_price) == 0 or len(dxy_price) == 0 or len(us10y_yield) == 0:
        return pd.Series(np.nan, index=price.index)

    Lslow = Lfast * 4

    # Factor returns / changes (same for all instruments on a given date)
    spx_ret = spx_price.pct_change()
    dxy_ret = dxy_price.pct_change()
    yield_chg = us10y_yield.diff()

    # Instrument daily returns on instrument's own calendar
    instr_ret = price.pct_change(fill_method=None)
    idx = instr_ret.dropna().index

    # Align macro series to instrument's date index.
    # Gaps (weekends / US holidays where crypto trades but macro markets are closed)
    # are filled with 0.0 — neutral, avoids double-counting weekend moves.
    factors = pd.DataFrame(
        {
            'spx': spx_ret.reindex(idx),
            'dxy': dxy_ret.reindex(idx),
            'yield': yield_chg.reindex(idx),
        }
    ).fillna(0.0)
    y = instr_ret.reindex(idx).fillna(0.0)

    # Rolling OLS residuals (point-in-time; NaN for first reg_window days)
    residuals = _rolling_ols_residuals(y, factors, window=reg_window)

    # Cumulate residuals → crypto-specific price series
    cum_resid = residuals.cumsum()

    # Apply EWMAC with unit vol — residuals are already dimensionless daily fractions
    unit_vol = pd.Series(1.0, index=cum_resid.index)
    return ewmac(cum_resid, unit_vol, Lfast=Lfast, Lslow=Lslow)


def macro_momentum(
    price: pd.Series,
    spx_price: pd.Series,
    dxy_price: pd.Series,
    us10y_yield: pd.Series,
    Lfast: int = 16,
    reg_window: int = 60,
) -> pd.Series:
    """
    Macro-driven momentum — exact complement to residual_momentum.

    Runs the same rolling OLS as residual_momentum but extracts the fitted values
    (the macro-explained component) rather than the residuals. Cumulating and
    EWMAC-ing the fitted values gives a trend signal driven purely by each
    instrument's rolling beta exposure to SPX, DXY, and 10Y yield direction.

    Instruments with high positive SPX beta accumulate positive fitted values
    during risk-on rallies; high DXY-negative-beta instruments benefit when DXY falls.
    """
    if len(spx_price) == 0 or len(dxy_price) == 0 or len(us10y_yield) == 0:
        return pd.Series(np.nan, index=price.index)

    Lslow = Lfast * 4

    spx_ret = spx_price.pct_change()
    dxy_ret = dxy_price.pct_change()
    yield_chg = us10y_yield.diff()

    instr_ret = price.pct_change(fill_method=None)
    idx = instr_ret.dropna().index

    factors = pd.DataFrame(
        {
            'spx': spx_ret.reindex(idx),
            'dxy': dxy_ret.reindex(idx),
            'yield': yield_chg.reindex(idx),
        }
    ).fillna(0.0)
    y = instr_ret.reindex(idx).fillna(0.0)

    fitted = _rolling_ols_fitted(y, factors, window=reg_window)
    cum_fitted = fitted.cumsum()

    unit_vol = pd.Series(1.0, index=cum_fitted.index)
    return ewmac(cum_fitted, unit_vol, Lfast=Lfast, Lslow=Lslow)


def dxy_momentum(
    price: pd.Series,
    dxy_price: pd.Series,
    Lfast: int = 16,
) -> pd.Series:
    """
    Portfolio-level macro signal: DXY trending down → long crypto (risk-on).

    EWMAC on DXY cumulative log-returns, inverted. When the dollar is weakening,
    crypto historically outperforms broadly. Same forecast returned for all instruments.
    Distinct from residual_momentum and macro_momentum: no per-instrument OLS regression.
    """
    if len(dxy_price.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)

    Lslow = Lfast * 4
    dxy_ret = np.log(dxy_price / dxy_price.shift(1))
    cum_dxy = dxy_ret.cumsum()
    unit_vol = pd.Series(1.0, index=cum_dxy.index)
    raw = ewmac(cum_dxy, unit_vol, Lfast=Lfast, Lslow=Lslow)
    return (-raw * 10.0).reindex(price.index)


def spx_momentum(
    price: pd.Series,
    spx_price: pd.Series,
    Lfast: int = 16,
) -> pd.Series:
    """
    Portfolio-level macro signal: SPX trending down → short crypto (risk-off).

    EWMAC on SPX cumulative log-returns, inverted. Rising equities = risk-on = long crypto.
    Orthogonal to dxy_momentum: DXY captures dollar strength; SPX captures equity risk appetite.
    Same forecast for all instruments.
    """
    if len(spx_price.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)

    Lslow = Lfast * 4
    spx_ret = np.log(spx_price / spx_price.shift(1))
    cum_spx = spx_ret.cumsum()
    unit_vol = pd.Series(1.0, index=cum_spx.index)
    raw = ewmac(cum_spx, unit_vol, Lfast=Lfast, Lslow=Lslow)
    return (raw * 10.0).reindex(price.index)


def us10y_momentum(
    price: pd.Series,
    us10y_yield: pd.Series,
    Lfast: int = 16,
) -> pd.Series:
    """
    Portfolio-level macro signal: rising 10Y yields → short crypto (liquidity tightening).

    EWMAC on 10Y yield level (not log-returns — yields can go negative), inverted.
    Rising yields = tighter financial conditions = crypto headwind.
    Orthogonal to dxy_momentum and spx_momentum.
    Same forecast for all instruments.
    """
    if len(us10y_yield.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)

    Lslow = Lfast * 4
    unit_vol = pd.Series(1.0, index=us10y_yield.index)
    raw = ewmac(us10y_yield, unit_vol, Lfast=Lfast, Lslow=Lslow)
    return (-raw * 10.0).reindex(price.index)


def gold_momentum(
    price: pd.Series,
    gold_price: pd.Series,
    Lfast: int = 16,
) -> pd.Series:
    """
    Portfolio-level macro signal: rising gold → short crypto (risk-off).

    EWMAC on gold log-returns, inverted. Gold rising signals risk-off sentiment
    which is a headwind for crypto as a risk asset.
    Orthogonal to dxy_momentum (dollar) and us10y_momentum (rates).
    Same forecast for all instruments.
    """
    if len(gold_price.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)
    Lslow = Lfast * 4
    gold_ret = np.log(gold_price / gold_price.shift(1))
    cum_gold = gold_ret.cumsum()
    unit_vol = pd.Series(1.0, index=cum_gold.index)
    raw = ewmac(cum_gold, unit_vol, Lfast=Lfast, Lslow=Lslow)
    return (-raw * 10.0).reindex(price.index)  # inverted: gold up → short crypto


def vix_momentum(
    price: pd.Series,
    vix_level: pd.Series,
    Lfast: int = 16,
) -> pd.Series:
    """
    Portfolio-level macro signal: rising VIX → short crypto (fear / risk-off).

    EWMAC on VIX level normalized by rolling std (VIX has extreme spikes that
    dwarf normal variation — raw EWMAC needs explicit normalization).
    Rising fear index = equity volatility regime = crypto headwind.
    Same forecast for all instruments.
    """
    if len(vix_level.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)
    Lslow = Lfast * 4
    min_p = max(Lfast // 2, 2)
    raw = (
        vix_level.ewm(span=Lfast, min_periods=min_p).mean()
        - vix_level.ewm(span=Lslow, min_periods=min_p).mean()
    )
    roll_std = raw.rolling(Lslow * 2, min_periods=Lslow).std().clip(lower=1e-8)
    scaled = (raw / roll_std).clip(-2.0, 2.0) * 10.0
    return (-scaled).reindex(price.index)  # inverted: VIX up → short crypto


def oil_momentum(
    price: pd.Series,
    oil_price: pd.Series,
    Lfast: int = 16,
) -> pd.Series:
    """
    Portfolio-level macro signal: EWMAC on WTI crude oil log-returns.

    Not inverted — tested as directional (oil up → long crypto via risk-on / inflation regime).
    Hypothesis: oil rising signals economic expansion → risk appetite → crypto tailwind.
    Alternatively, oil up → inflation → Fed tightening → headwind (polarity uncertain).
    Ablation determines whether this directional hypothesis is correct.
    Same forecast for all instruments.
    """
    if len(oil_price.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)
    Lslow = Lfast * 4
    oil_ret = np.log(oil_price.clip(lower=0.01) / oil_price.clip(lower=0.01).shift(1))
    cum_oil = oil_ret.cumsum()
    unit_vol = pd.Series(1.0, index=cum_oil.index)
    raw = ewmac(cum_oil, unit_vol, Lfast=Lfast, Lslow=Lslow)
    return (raw * 10.0).reindex(price.index)  # not inverted: test natural polarity


def basis_mr(
    price: pd.Series,
    premium_index: pd.Series,
    lookback: int = 5,
    threshold_bp: float = 50.0,
) -> pd.Series:
    """
    Per-instrument basis mean-reversion: short when the 5-day rolling mean of the
    premium-index basis exceeds +threshold_bp; long when it's below -threshold_bp.

    Premium index = (mark_price - index_price) / index_price, EOD daily snapshot
    from Binance Vision. Persistent basis indicates one-sided positioning that
    funding payments will eventually unwind — fade the extreme.

    Forecast magnitude scales linearly between threshold and 3×threshold, capped
    at ±20. Inside the deadband (|basis_5d| < threshold_bp), forecast is zero.

    Sign convention: positive basis (mark > spot, longs paying) → SHORT crypto.
    Negative basis (mark < spot, shorts paying) → LONG.
    """
    if premium_index is None or len(premium_index.dropna()) < lookback * 2:
        return pd.Series(dtype=float, index=price.index)

    # Convert threshold_bp from basis points to fractional units
    threshold = threshold_bp / 1e4  # 50 bp = 0.005

    basis_smoothed = premium_index.rolling(lookback, min_periods=lookback).mean()

    # Deadband + linear scaling. Forecast saturates (+/-20) at 3×threshold.
    raw = -basis_smoothed  # invert: positive basis → short
    abs_excess = (raw.abs() - threshold).clip(lower=0)
    scaled = (abs_excess / (2 * threshold)).clip(upper=1) * 20.0
    forecast = scaled * np.sign(raw)
    return forecast.reindex(price.index)


def btc_etf_flow_trend(
    price: pd.Series,
    btc_etf_signed_volume: pd.Series,
    Lfast: int = 20,
) -> pd.Series:
    """
    Portfolio-level institutional capital signal: EWMAC on BTC spot-ETF (IBIT) signed
    daily dollar volume. Sign = sign(close − open) × |dollar_volume|.

    Hypothesis: net institutional dollars flowing into the spot BTC ETF over the past
    20-trading-day window predicts spot BTC and broader crypto price 1-3 weeks ahead.
    Inflows = institutional accumulation = bullish; outflows = distribution = bearish.

    Same forecast broadcast to every instrument (BTC ETF flows are a market-wide
    leading signal, not BTC-specific). Pre-launch (before 2024-01-11) returns NaN —
    the WF stitched OOS series ignores those windows automatically.
    """
    if len(btc_etf_signed_volume.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)
    Lslow = Lfast * 4
    # Use cumulative signed flow as the "price" series for EWMAC: when cumulative
    # inflows are accelerating (fast EMA > slow EMA), forecast is positive.
    cum = btc_etf_signed_volume.fillna(0).cumsum()
    unit_vol = pd.Series(1.0, index=cum.index)
    raw = ewmac(cum, unit_vol, Lfast=Lfast, Lslow=Lslow)
    # Robust normalization: divide by rolling std of raw to put on a stable scale.
    roll_std = raw.rolling(Lslow * 2, min_periods=Lslow).std().clip(lower=1e-8)
    scaled = (raw / roll_std).clip(-2.0, 2.0) * 10.0
    return scaled.reindex(price.index)


def stablecoin_dominance_trend(
    price: pd.Series,
    stablecoin_dominance: pd.Series,
    Lfast: int = 32,
) -> pd.Series:
    """
    Portfolio-level capital-flow signal: EWMAC on log(stablecoin dominance).

    Dominance = total stablecoin supply / total crypto market cap proxy.
    Hypothesis (opposite of the absolute-supply rule): rising stablecoin share of
    total crypto value = capital is parked on the sidelines rather than deployed
    into spot — bearish for next-period prices. Falling dominance = stables being
    spent into crypto = bullish.

    Sign committed a priori: rising dominance → SHORT (negative forecast). Same
    forecast broadcast to every instrument. Spearman 0.05 vs the absolute-supply
    rule (orthogonal — captures the relative-share dimension the supply rule
    misses by construction).

    Source: data.get_stablecoin_dominance — derived in parquet_perps_sim_data.py
    from stablecoin_supply.parquet (DefiLlama) and market_cap.parquet (CoinMetrics).
    """
    if len(stablecoin_dominance.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)
    Lslow = Lfast * 4
    log_dom = np.log(stablecoin_dominance.clip(lower=1e-8))
    unit_vol = pd.Series(1.0, index=log_dom.index)
    raw = ewmac(log_dom, unit_vol, Lfast=Lfast, Lslow=Lslow)
    roll_std = raw.rolling(Lslow * 2, min_periods=Lslow).std().clip(lower=1e-8)
    # Invert: rising dominance → SHORT, so positive raw EWMAC → negative forecast.
    scaled = -(raw / roll_std).clip(-2.0, 2.0) * 10.0
    return scaled.reindex(price.index)


def stablecoin_supply_trend(
    price: pd.Series,
    stablecoin_supply: pd.Series,
    Lfast: int = 32,
) -> pd.Series:
    """
    Portfolio-level capital-flow signal: EWMAC on log(total USD-pegged stablecoin supply).

    Hypothesis: aggregate stablecoin issuance is a leading indicator of capital entering
    crypto. Issuance grows when investors are converting fiat into on-chain dollars to
    deploy into spot/perps over the following days/weeks; supply contracts when capital
    is exiting. Same forecast broadcast to every instrument — this is a regime signal,
    not an instrument-specific one.

    Sign committed a priori: rising supply → LONG crypto (positive forecast). The plan
    text suggested "rising share = risk-off → short" but that interpretation applies to
    stablecoin DOMINANCE (stables / total crypto), not absolute supply. We test the
    absolute-supply hypothesis here; the WF harness adjudicates whether it survives.

    Source: data/stablecoin_supply.parquet (DefiLlama aggregate, 2017-present).
    """
    if len(stablecoin_supply.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)
    Lslow = Lfast * 4
    # Log scale: stablecoin supply has grown ~3000x since 2018 ($100M → $300B), so
    # absolute returns dominate the EWMAC at higher levels. Log returns normalize.
    log_supply = np.log(stablecoin_supply.clip(lower=1.0))
    cum = log_supply  # already a "cumulative" series
    unit_vol = pd.Series(1.0, index=cum.index)
    raw = ewmac(cum, unit_vol, Lfast=Lfast, Lslow=Lslow)
    return (raw * 10.0).reindex(price.index)  # not inverted: rising supply → long


# ============================================================================
# VOLATILITY TIME-SERIES SIGNALS (TS, price-based, full 319-instrument coverage)
# ============================================================================


def vol_trend_16(
    price: pd.Series,
    vol: pd.Series,
    Lfast: int = 16,
) -> pd.Series:
    """
    Volatility trend: EWMAC on per-instrument realized volatility.

    Hypothesis: rising volatility predicts further volatility clustering (GARCH effect).
    Used as a directional forecast — short when vol trending up, long when vol declining.

    Inverted: rising vol → short (instruments with expanding vol tend to sell off).
    Orthogonal to xs_low_vol (cross-sectional ranking) — this is the per-instrument TS trend.
    """
    if len(vol.dropna()) < 4 * Lfast:
        return pd.Series(dtype=float, index=price.index)
    Lslow = Lfast * 4
    min_p = max(Lfast // 2, 2)
    raw = (
        vol.ewm(span=Lfast, min_periods=min_p).mean()
        - vol.ewm(span=Lslow, min_periods=min_p).mean()
    )
    roll_std = raw.rolling(Lslow * 2, min_periods=Lslow).std().clip(lower=1e-8)
    scaled = (raw / roll_std).clip(-2.0, 2.0) * 10.0
    return (-scaled).reindex(price.index)  # inverted: rising vol → short


def vol_zscore_ts(
    price: pd.Series,
    vol: pd.Series,
    lookback: int = 252,
) -> pd.Series:
    """
    Volatility mean-reversion: per-instrument RV z-score vs own rolling history.

    When current vol is extreme relative to its own history, it tends to revert.
    High vol z-score → short (expect vol contraction and price reversal).
    Low vol z-score → long (calm regime, trend-following friendly).

    Distinct from vol_trend_16 (trend direction) and xs_low_vol (cross-sectional rank).
    """
    if len(vol.dropna()) < lookback // 2:
        return pd.Series(dtype=float, index=price.index)
    min_p = max(lookback // 4, 20)
    roll_mean = vol.rolling(lookback, min_periods=min_p).mean()
    roll_std = vol.rolling(lookback, min_periods=min_p).std().clip(lower=1e-8)
    z = ((vol - roll_mean) / roll_std).clip(-3.0, 3.0)
    return (-z * (20.0 / 3.0)).reindex(price.index)  # inverted: high vol z → short


# ============================================================================
# ATTENTION / NEWS PROXY SIGNALS (OI-based, Phase 1)
# ============================================================================


def attn_exhaustion_fade(
    price: pd.Series,
    vol: pd.Series,
    open_interest: pd.Series,
    oi_window: int = 60,
    oi_threshold: float = 2.0,
    ret_threshold: float = 1.5,
) -> pd.Series:
    """
    Per-instrument OI-spike + return-spike exhaustion fade (attention proxy S1).

    Hypothesis: when an instrument sees extreme OI surge AND a large price move
    simultaneously, the position is crowded and due for reversal. Fades the
    direction of the move only when both OI and return clear their thresholds.

    Inputs: price, vol (daily_returns_volatility), open_interest (get_open_interest).
    Returns zero (not NaN) before OI data begins so scalar calibration is clean.
    """
    if open_interest is None or len(open_interest.dropna()) < oi_window:
        return pd.Series(dtype=float, index=price.index)

    df = pd.concat(
        [price, vol, open_interest], axis=1, keys=["price", "vol", "oi"]
    ).dropna(subset=["oi"])
    if df.empty:
        return pd.Series(dtype=float, index=price.index)

    log_oi = np.log(df["oi"].clip(lower=1.0))
    log_oi_chg = log_oi.diff()
    min_p = max(oi_window // 2, 2)
    oi_ts_z = (log_oi_chg - log_oi_chg.rolling(oi_window, min_periods=min_p).mean()) / (
        log_oi_chg.rolling(oi_window, min_periods=min_p).std().clip(lower=1e-8)
    )

    ret_1d = df["price"].pct_change()
    ret_3d = (1 + ret_1d).rolling(3).apply(np.prod, raw=True) - 1
    ret_z_3d = ret_3d / df["vol"].clip(lower=1e-8)

    oi_excess = (oi_ts_z - oi_threshold).clip(lower=0.0)
    ret_excess = (ret_z_3d.abs() - ret_threshold).clip(lower=0.0)
    raw = -oi_excess * ret_excess * np.sign(ret_z_3d)

    return (raw * 10.0).reindex(price.index)


def attn_panic_rebound(
    price: pd.Series,
    vol: pd.Series,
    open_interest: pd.Series,
    oi_window: int = 60,
    panic_oi_threshold: float = 1.5,
    panic_ret_threshold: float = 2.0,
) -> pd.Series:
    """
    Per-instrument OI-spike + panic selloff + stabilization → long rebound (S3 proxy).

    Hypothesis: OI-driven panic conditions (high OI + extreme down move) create
    overshoot. After next-day stabilization (positive daily return), signal is long.
    Distinct from funding_mr, which fades extreme funding rather than OI-driven panics.

    Inputs: price, vol (daily_returns_volatility), open_interest (get_open_interest).
    """
    if open_interest is None or len(open_interest.dropna()) < oi_window:
        return pd.Series(dtype=float, index=price.index)

    df = pd.concat(
        [price, vol, open_interest], axis=1, keys=["price", "vol", "oi"]
    ).dropna(subset=["oi"])
    if df.empty:
        return pd.Series(dtype=float, index=price.index)

    log_oi = np.log(df["oi"].clip(lower=1.0))
    log_oi_chg = log_oi.diff()
    min_p = max(oi_window // 2, 2)
    oi_ts_z = (log_oi_chg - log_oi_chg.rolling(oi_window, min_periods=min_p).mean()) / (
        log_oi_chg.rolling(oi_window, min_periods=min_p).std().clip(lower=1e-8)
    )

    ret_1d = df["price"].pct_change()
    ret_3d = (1 + ret_1d).rolling(3).apply(np.prod, raw=True) - 1
    ret_z_3d = ret_3d / df["vol"].clip(lower=1e-8)

    panic_strength = (
        (oi_ts_z - panic_oi_threshold).clip(lower=0.0)
        * (-ret_z_3d - panic_ret_threshold).clip(lower=0.0)
    )
    stabilized = (ret_1d >= 0).astype(float)
    raw = panic_strength * stabilized

    return (raw * 10.0).reindex(price.index)


# ============================================================================
# CALENDAR SEASONALITY (contrarian annual mean-reversion)
# ============================================================================


def seasonality(price: pd.Series, n_lags_min: int = 2, n_lags_max: int = 5) -> pd.Series:
    """
    Contrarian same-calendar-month seasonality (Wang 2024 / crypto adaptation).

    At each month-end, averages the monthly return from the NEXT calendar month
    in prior years (annual lags t-n_lags_min through t-n_lags_max, skipping t-1).
    Negated: high historical same-month performance predicts underperformance
    (annual mean-reversion; TS IC = -0.07, t = -4.3 in 2020-2026 crypto perps).

    Signal is constant within each calendar month (forward-filled from month-end).
    """
    monthly = price.resample("ME").last()
    monthly_rets = monthly.pct_change()

    scores = {}
    for dt in monthly_rets.index:
        target = dt + pd.DateOffset(months=1)
        month_num = target.month
        year_num = target.year
        lags = []
        for k in range(n_lags_min, n_lags_max + 1):
            lag_dates = monthly_rets.index[
                (monthly_rets.index.month == month_num)
                & (monthly_rets.index.year == year_num - k)
            ]
            if len(lag_dates) == 1:
                val = monthly_rets.loc[lag_dates[0]]
                if not np.isnan(val):
                    lags.append(val)
        scores[dt] = -float(np.mean(lags)) if lags else np.nan  # negated: contrarian

    score_series = pd.Series(scores)
    return score_series.reindex(price.index, method="ffill")


# ============================================================================
# PASSTHROUGH (for pre-computed cross-sectional signals)
# ============================================================================


def passthrough_forecast(signal: pd.Series) -> pd.Series:
    """
    Return a pre-computed forecast unchanged.

    Used for cross-sectional signals (XS Carry, XS Activity, XS VAL, Inter-Sector)
    that are computed in the data layer and exposed via data.get_*_forecast().
    The signal is already in [-20, +20] scale (percentile-ranked × 40), so the
    walk-forward forecast scalar will estimate ≈ 1.0.
    """
    return signal


# ============================================================================
# VOLUME-BASED RULES
# ============================================================================


def volume_surge_momentum(
    price: pd.Series,
    vol: pd.Series,
    daily_volume: pd.Series,
    vol_window: int = 63,
    vol_threshold: float = 1.5,
) -> pd.Series:
    """
    Divergent TS rule: volume surge in the direction of a price move predicts continuation.

    Hypothesis: when a price move is accompanied by abnormally high volume,
    it reflects conviction behind the trend rather than noise.

    Inputs: price, vol (daily_returns_volatility), daily_volume (get_daily_volume).
    Returns empty Series if fewer than vol_window days of volume data are available.
    """
    if daily_volume is None or len(daily_volume.dropna()) < vol_window:
        return pd.Series(dtype=float, index=price.index)

    df = pd.concat(
        [price, vol, daily_volume], axis=1, keys=["price", "vol", "volume"]
    ).dropna(subset=["volume"])
    if df.empty:
        return pd.Series(dtype=float, index=price.index)

    log_vol = np.log(df["volume"].clip(lower=1.0))
    log_vol_chg = log_vol.diff()
    min_p = max(vol_window // 2, 10)
    vol_z = (log_vol_chg - log_vol_chg.rolling(vol_window, min_periods=min_p).mean()) / (
        log_vol_chg.rolling(vol_window, min_periods=min_p).std().clip(lower=1e-8)
    )

    ret_1d = df["price"].pct_change()
    ret_3d = (1 + ret_1d).rolling(3).apply(np.prod, raw=True) - 1
    ret_z_3d = ret_3d / df["vol"].clip(lower=1e-8)

    vol_excess = (vol_z - vol_threshold).clip(lower=0.0)
    raw = np.sign(ret_z_3d) * vol_excess
    smoothed = raw.ewm(span=5, min_periods=2).mean()

    return (smoothed * 10.0).reindex(price.index)


def volume_price_divergence(
    price: pd.Series,
    vol: pd.Series,
    daily_volume: pd.Series,
    fast: int = 32,
    slow: int = 128,
) -> pd.Series:
    """
    Convergent TS rule: price-trend and volume-trend divergence signals weakening momentum.

    Hypothesis: when price trends up but volume trends down (or vice versa),
    the move lacks conviction and is likely to fade.

    Inputs: price, vol (daily_returns_volatility), daily_volume (get_daily_volume).
    Returns empty Series if fewer than slow days of volume data are available.
    """
    if daily_volume is None or len(daily_volume.dropna()) < slow:
        return pd.Series(dtype=float, index=price.index)

    df = pd.concat(
        [price, vol, daily_volume], axis=1, keys=["price", "vol", "volume"]
    ).dropna(subset=["volume"])
    if df.empty:
        return pd.Series(dtype=float, index=price.index)

    min_p = max(fast, 2)
    price_ewmac = (
        df["price"].ewm(span=fast, min_periods=min_p).mean()
        - df["price"].ewm(span=slow, min_periods=min_p).mean()
    )

    log_vol = np.log(df["volume"].clip(lower=1.0))
    vol_ewmac = (
        log_vol.ewm(span=fast, min_periods=min_p).mean()
        - log_vol.ewm(span=slow, min_periods=min_p).mean()
    )

    price_vol_norm = df["vol"].rolling(slow, min_periods=min_p).mean().clip(lower=1e-8)
    price_norm = price_ewmac / (df["price"].rolling(slow, min_periods=min_p).mean().clip(lower=1e-8) * price_vol_norm)

    vol_std = vol_ewmac.rolling(slow, min_periods=min_p).std().clip(lower=1e-8)
    vol_norm = vol_ewmac / vol_std

    divergence = price_norm * vol_norm
    raw = -np.sign(price_norm) * (price_norm.abs() * (-vol_norm).clip(lower=0.0))

    return (raw * 10.0).reindex(price.index)


def vol_regime_trend(
    price: pd.Series,
    vol: pd.Series,
    trend_fast: int = 32,
    trend_slow: int = 128,
    rv_short: int = 20,
    rv_long: int = 60,
) -> pd.Series:
    """
    TS rule: per-instrument vol contraction amplifies price trend; expansion attenuates.

    Vol-targeting funds add exposure when per-instrument vol falls → trend persists.
    Distinct from crowd_deleverage_trend: per-instrument (not portfolio-level), continuous
    modulation (not a rare stress-episode trigger), no OI component.
    """
    if len(price.dropna()) < trend_slow:
        return pd.Series(dtype=float, index=price.index)

    df = pd.concat([price, vol], axis=1, keys=["price", "vol"]).ffill()
    vol_floor = df["vol"].clip(lower=1e-8)

    # Price trend: EWMAC vol-normalized
    ewma_f = df["price"].ewm(span=trend_fast, min_periods=trend_fast // 2).mean()
    ewma_s = df["price"].ewm(span=trend_slow, min_periods=trend_slow // 2).mean()
    price_ewmac = (ewma_f - ewma_s) / vol_floor

    # Per-instrument realized vol (computed from log-returns inside rule)
    log_ret = np.log(df["price"] / df["price"].shift(1))
    rv_s = log_ret.rolling(rv_short, min_periods=rv_short // 2).std()
    rv_l = log_ret.rolling(rv_long, min_periods=rv_long // 2).std().clip(lower=1e-8)

    # Vol direction: negative = contracting, positive = expanding
    vol_direction = (rv_s / rv_l) - 1.0
    vol_dir_std = vol_direction.rolling(rv_long, min_periods=rv_long // 2).std().clip(lower=1e-8)
    vol_dir_z = (vol_direction / vol_dir_std).clip(-1.0, 1.0)

    # Modifier: 1.5 when vol contracting strongly, 0.5 when expanding strongly
    vol_modifier = 1.0 - vol_dir_z * 0.5

    raw = price_ewmac * vol_modifier
    return (raw * 10.0).reindex(price.index)


# ============================================================================
# TREND-GATED CARRY (Carver-compliant replacement for additive sleeve)
# ============================================================================


def gated_carry(
    funding_rates: pd.Series,
    price: pd.Series,
    vol: pd.Series,
    carry_span: int = 10,
    trend_fast: int = 32,
    trend_slow: int = 128,
    vol_floor: float = 0.01,
) -> pd.Series:
    """
    Trend-gated vol-normalized carry.

    Computes vol-normalized carry from funding rates, then gates by a
    per-instrument trend proxy derived from price EWMAC. Returns 0 on days
    when carry and trend disagree in direction.

    This is the Carver-compliant replacement for the additive gated carry sleeve
    in ForecastCombineGated. The gate is baked into the rule function, so it
    goes through the standard forecast scalar → weighted average → FDM pipeline.

    Trend proxy: (EWM_fast − EWM_slow) / vol — per-instrument EWMAC, computed
    inside the rule to avoid circular dependency with the ForecastCombine stage.

    ~50% of outputs are 0 (when gated). Walk-forward forecast scalar compensates
    by estimating a larger scalar from the non-zero outputs. This is correct
    Carver behavior — the scalar adapts to the signal's active fraction.

    Args:
        funding_rates: Raw 8-hourly funding rate (data.get_funding_rate).
            Typical scale: 0.0001 = 0.01% per 8h.
        price: Daily price series (data.daily_prices).
        vol: Daily price vol — unit, not % (rawdata.daily_returns_volatility).
        carry_span: EWM span for smoothing funding in days.
        trend_fast: Fast EWM for trend proxy in days.
        trend_slow: Slow EWM for trend proxy in days (default 4× fast).
        vol_floor: Minimum vol to avoid division-by-zero.

    Returns:
        Unscaled carry score; 0 when carry and trend directions disagree.
    """
    # Align funding_rates to price index (funding may start/end on different dates)
    funding_aligned = funding_rates.reindex(price.index).ffill()
    ann_funding = funding_aligned * 3 * 365
    f_smooth = ann_funding.ewm(span=carry_span, min_periods=1).mean()

    # NOTE: vol_filled is in price-dollar units (not percentage vol). Dividing by price-dollar
    # vol gives carry in units of (annualized_funding / price_vol) which is inversely proportional
    # to the instrument's price level. This intentionally attenuates carry for large-cap
    # instruments (BTC, ETH — where funding carry is well-arbitraged and less predictive) and
    # amplifies it for small-caps (where carry has genuine alpha). Empirically tested: switching
    # to pct vol (vol/price) equalizes carry across instruments but reduces Sharpe by ~6%.
    vol_filled = vol.reindex(price.index).ffill().replace(0.0, np.nan).ffill().clip(lower=vol_floor)
    carry = -f_smooth / vol_filled  # negative: positive funding → short bias

    trend = (
        price.ewm(span=trend_fast, min_periods=1).mean()
        - price.ewm(span=trend_slow, min_periods=1).mean()
    ) / vol_filled

    return carry.where(np.sign(carry) == np.sign(trend), other=0.0)


def crowd_deleverage_trend(
    price: pd.Series,
    vol: pd.Series,
    xs_vol_zscore: pd.Series,
    xs_oi_change_zscore: pd.Series,
    fast_span: int = 16,
    slow_span: int = 64,
    window: int = 60,
) -> pd.Series:
    """
    Crowd-deleveraging trend amplifier.

    Hypothesis (Levine): when realized vol spikes AND open interest falls, this
    signals forced liquidation by vol-targeting leveraged players. These sellers
    are directional (they must close in the direction of the move), so trend
    signals are more predictive during these episodes.

    Computes an EWMAC-style momentum signal and scales it up when both:
      - Universe realized vol is elevated (xs_vol_zscore > 0)
      - Universe OI is declining (xs_oi_change_zscore < 0)

    In normal markets, stress_normalized ≈ 0 and the signal ≈ EWMAC(16, 64).
    During crowd-deleverage episodes, the signal is amplified by 1× to ~3×.

    Args:
        price:               Daily price series (data.daily_prices).
        vol:                 Daily price vol in price units (rawdata.daily_returns_volatility).
        xs_vol_zscore:       Universe-median realized-vol z-score (data.get_xs_vol_zscore).
        xs_oi_change_zscore: Universe-median OI log-change z-score (data.get_xs_oi_change_zscore).
        fast_span:           Fast EWMA span in days (default 16).
        slow_span:           Slow EWMA span in days (default 64).
        window:              Rolling window for stress normalization (default 60).

    Returns:
        Unscaled, uncapped forecast series.
    """
    # Align all inputs on a shared index, dropping missing rows
    df = pd.concat(
        [price, vol, xs_vol_zscore, xs_oi_change_zscore],
        axis=1,
        keys=["price", "vol", "vol_z", "oi_z"],
    ).dropna()

    if df.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))

    price_a = df["price"]
    vol_a = df["vol"].replace(0.0, np.nan).ffill().clip(lower=1e-12)

    # EWMAC momentum component (vol-normalized)
    ewma_fast = price_a.ewm(span=fast_span, min_periods=1).mean()
    ewma_slow = price_a.ewm(span=slow_span, min_periods=1).mean()
    ewmac_signal = (ewma_fast - ewma_slow) / vol_a

    # Stress indicator: geometric mean of vol-up and OI-down components
    # Only fires when BOTH conditions are active simultaneously
    vol_up = df["vol_z"].clip(lower=0.0)          # positive when vol elevated
    oi_down = (-df["oi_z"]).clip(lower=0.0)       # positive when OI falling
    stress_raw = np.sqrt(vol_up * oi_down)

    min_p = max(window // 2, 2)
    stress_std = stress_raw.rolling(window, min_periods=min_p).std().clip(lower=1e-8)
    stress_normalized = (stress_raw / stress_std).clip(0.0, 3.0)

    # Amplify trend: normal markets → ×1.0; deleveraging episodes → ×(1 + stress)
    raw = ewmac_signal * (1.0 + stress_normalized)

    return (raw * 10.0).reindex(price.index)


def demeaned_carry(
    funding_rates: pd.Series,
    market_funding: pd.Series,
    price: pd.Series,
    vol: pd.Series,
    carry_span: int = 30,
    trend_fast: int = 32,
    trend_slow: int = 128,
    vol_floor: float = 0.0001,
    gate: bool = False,
) -> pd.Series:
    """
    De-meaned (idiosyncratic) vol-normalized carry, optionally trend-gated.

    Captures the per-instrument funding rate after subtracting the universe-wide
    mean (the "crypto carry beta"). When the whole market is funding high,
    gated_carry goes long everything; demeaned_carry is flat, expressing only
    relative carry strength vs peers.

    Distinct from:
    - gated_carry: uses raw funding vs own history, not vs contemporaneous peers
    - xs_carry: uses cross-sectional rank (ordinal), not absolute deviation from mean

    De-meaned carry score = (smoothed_instrument - smoothed_market) / price_vol
    Negated so positive idiosyncratic funding → short bias (same as gated_carry).

    ~50% of gated outputs are 0 (when gate=True). Walk-forward forecast scalar
    compensates by estimating a larger scalar from non-zero outputs.

    Args:
        funding_rates: Raw 8-hourly funding rate (data.get_funding_rate).
            Typical scale: 0.0001 = 0.01% per 8h. Will be annualised ×3×365.
        market_funding: Cross-sectional median of annualised funding rates across
            all instruments (data.get_cross_sectional_median_funding). Already
            annualised — do NOT apply ×3×365 again.
        price: Daily price series (data.daily_prices).
        vol: Daily price vol — unit, not % (rawdata.daily_returns_volatility).
        carry_span: EWM span for smoothing funding (days).
        trend_fast: Fast EWM for trend gate (days). Only used when gate=True.
        trend_slow: Slow EWM for trend gate (days). Only used when gate=True.
        vol_floor: Minimum vol floor to avoid division-by-zero.
        gate: If True, zero out when idiosyncratic carry and trend directions
            disagree (same gate as gated_carry).

    Returns:
        Unscaled carry score; optionally 0 when gated.
    """
    # Align and annualise instrument funding
    funding_aligned = funding_rates.reindex(price.index).ffill()
    ann_funding = funding_aligned * 3 * 365
    f_smooth = ann_funding.ewm(span=carry_span, min_periods=1).mean()

    # Smooth market funding (already annualised — no ×3×365)
    market_aligned = market_funding.reindex(price.index, method="ffill")
    market_smooth = market_aligned.ewm(span=carry_span, min_periods=1).mean()

    # Idiosyncratic carry = instrument - universe mean
    idiosyncratic = f_smooth - market_smooth

    # Vol-normalize using price-dollar vol (same as gated_carry — intentional:
    # attenuates carry for large-caps where it is well-arbitraged)
    vol_filled = (
        vol.reindex(price.index)
        .ffill()
        .replace(0.0, np.nan)
        .ffill()
        .clip(lower=vol_floor)
    )
    carry = -idiosyncratic / vol_filled  # negative: positive idio funding → short

    if not gate:
        return carry

    # Trend gate: zero out when carry and trend disagree in direction
    trend = (
        price.ewm(span=trend_fast, min_periods=1).mean()
        - price.ewm(span=trend_slow, min_periods=1).mean()
    ) / vol_filled

    return carry.where(np.sign(carry) == np.sign(trend), other=0.0)
