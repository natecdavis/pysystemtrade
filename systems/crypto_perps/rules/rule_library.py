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
