"""
Portfolio Constraint Application with Carver-Style IDM Multiplier

This module implements two-stage constraint application:

1. IDM Multiplier (Carver-Style Diversification Benefit)
   - Calculate IDM from normalized weights: idm_raw ≥ 1.0 (scale-invariant)
   - Apply as multiplier: exposure = base_weight × min(idm_raw, idm_cap)
   - Increases leverage based on diversification benefit (capped for safety)

2. Gross Leverage Cap (Absolute Risk Limit)
   - Calculate gross_lev = sum(|exposure_weights|)
   - If gross_lev > cap: scale down uniformly to meet cap
   - Takes absolute priority over IDM (can prevent full IDM benefit)

Key Concepts:
- base_weights: From forecasts + vol targeting (before diversification benefit)
- idm_raw: Diversification measure from normalized weights (≥1.0, scale-invariant)
- idm_applied: min(idm_raw, idm_cap) - the actual multiplier used
- exposure_weights: base_weights × idm_applied (after diversification benefit)
- constrained_weights: exposure_weights × scalar (if gross_lev > cap)

Invariants:
- idm_raw ≥ 1.0 (Carver-style normalization)
- idm_applied ≤ idm_cap (by definition of min)
- gross_lev_final ≤ gross_leverage_cap (by design)
- idm_final ≈ idm_raw (scale-invariant, unless instruments dropped)

Phase 1: Minimal implementation using simple EWMA correlations
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, List


# ============================================================================
# Shared EWMA Configuration (Batch + Incremental)
# ============================================================================
# IMPORTANT: These constants control BOTH batch and incremental behavior
# They are set based on inspection of the current batch implementation

# EWMA parameters (inspected from batch function)
BATCH_EWM_ADJUST = True      # Bias-corrected EWMA (pandas default)
BATCH_DEMEAN = False         # No centering of returns for covariance
BATCH_IDM_PRE_CAP = False    # IDM series uses final weights (post both caps)
BATCH_RETURNS = "pct"        # Return calculation: percentage returns

# Missing data policy (enforced everywhere)
MISSING_DATA_FILL = 0.0      # Fill NaN returns with this value

# Oracle decision: Use recursive EWMA for exact equivalence
# True: Batch uses same recursion as incremental (exact match)
# False: Batch uses pandas ewm().cov() (may diverge ~1e-8 to 1e-12)
USE_RECURSIVE_EWMA = True    # Set to True for guaranteed exact equivalence


def compute_returns(
    prices_df: pd.DataFrame,
    method: str = "pct",
    fill_value: float = None
) -> pd.DataFrame:
    """
    Compute returns using specified method with consistent NaN handling

    Single source of truth for return calculation - used by batch, incremental, and tests.

    Args:
        prices_df: Price DataFrame (dates × instruments)
        method: "pct" for percentage returns, "log" for log returns
        fill_value: Value to fill NaN returns (default: use MISSING_DATA_FILL constant)

    Returns:
        Returns DataFrame with same shape as prices_df

    Notes:
        - First row will be NaN (no prior price), filled with fill_value
        - Missing prices → NaN returns → filled with fill_value
        - Enforces consistent missing-data policy across batch + incremental + tests
    """
    if fill_value is None:
        fill_value = MISSING_DATA_FILL

    if method == "pct":
        returns = prices_df.pct_change()
    elif method == "log":
        returns = np.log(prices_df).diff()
    else:
        raise ValueError(f"Unknown return method: {method}. Use 'pct' or 'log'.")

    # Fill NaN returns with constant (enforced everywhere)
    return returns.fillna(fill_value)


def get_constraints_config() -> Dict:
    """
    Return shared constraints configuration

    Both apply_portfolio_constraints() and IncrementalConstraintsEngine
    reference these module-level constants to ensure identical behavior.

    Returns:
        Dict with keys: adjust, demean, idm_pre_cap, returns, use_recursive

    Example:
        config = get_constraints_config()
        engine = IncrementalConstraintsEngine(..., **config)

    Notes:
        - Returns hardcoded constants set during initial implementation
        - Both batch and incremental read same constants (single source of truth)
    """
    return {
        'adjust': BATCH_EWM_ADJUST,
        'demean': BATCH_DEMEAN,
        'idm_pre_cap': BATCH_IDM_PRE_CAP,
        'returns': BATCH_RETURNS,
        'use_recursive': USE_RECURSIVE_EWMA
    }


def calculate_ewma_correlation_recursive(
    returns_df: pd.DataFrame,
    span: int = 60,
    min_periods: int = 20,
    adjust: bool = True,
    demean: bool = False
) -> pd.DataFrame:
    """
    Calculate EWMA correlation using recursive formula (exact match to incremental)

    Args:
        returns_df: DataFrame of returns with instruments as columns
        span: EWMA span in days
        min_periods: Minimum periods required
        adjust: Use bias-corrected EWMA
        demean: Use demeaned returns for covariance

    Returns:
        Correlation matrix (DataFrame) for the last date

    Notes:
        - Uses same recursion as IncrementalConstraintsEngine
        - Guarantees exact numerical match to incremental engine
    """
    instruments = list(returns_df.columns)
    n = len(instruments)
    alpha = 2.0 / (span + 1)

    # State variables
    ewma_cov = np.zeros((n, n))
    ewma_mean = np.zeros(n) if demean else None
    weight = 0.0 if adjust else None
    weight_mean = 0.0 if (adjust and demean) else None
    count = 0

    # Process all dates to build final covariance
    for date in returns_df.index:
        count += 1
        r = returns_df.loc[date].to_numpy()

        # Update EWMA mean (if demeaning)
        if demean:
            if count == 1:
                ewma_mean = r
                if adjust:
                    weight_mean = 1.0
            else:
                if adjust:
                    old_weight = weight_mean
                    weight_mean = 1.0 + (1.0 - alpha) * old_weight
                    ewma_mean = (alpha * r + (1.0 - alpha) * ewma_mean * old_weight) / weight_mean
                else:
                    ewma_mean = alpha * r + (1.0 - alpha) * ewma_mean

            r_centered = r - ewma_mean
            outer_product = np.outer(r_centered, r_centered)
        else:
            outer_product = np.outer(r, r)

        # Update EWMA covariance
        if count == 1:
            ewma_cov = outer_product
            if adjust:
                weight = 1.0
        else:
            if adjust:
                old_weight = weight
                weight = 1.0 + (1.0 - alpha) * old_weight
                ewma_cov = (alpha * outer_product + (1.0 - alpha) * ewma_cov * old_weight) / weight
            else:
                ewma_cov = alpha * outer_product + (1.0 - alpha) * ewma_cov

    # Check if enough data
    if count < min_periods:
        # Return identity matrix
        return pd.DataFrame(
            np.eye(n),
            index=instruments,
            columns=instruments
        )

    # Convert covariance to correlation
    std_vec = np.sqrt(np.diag(ewma_cov))
    std_mat = np.outer(std_vec, std_vec)
    corr_matrix = np.where(std_mat > 0, ewma_cov / std_mat, 0.0)
    np.fill_diagonal(corr_matrix, 1.0)

    return pd.DataFrame(corr_matrix, index=instruments, columns=instruments)


def calculate_ewma_correlation(
    returns_df: pd.DataFrame,
    span: int = 60,
    min_periods: int = 20
) -> pd.DataFrame:
    """
    Calculate exponentially weighted moving average correlation matrix

    Args:
        returns_df: DataFrame of returns with instruments as columns
        span: EWMA span in days (default 60, shorter for crypto volatility)
        min_periods: Minimum periods required (default 20)

    Returns:
        Correlation matrix (DataFrame)

    Notes:
        - Phase 1: Simple EWMA correlation
        - Carver-style shorter half-life for crypto (60 days vs 125 for traditional)
        - Returns latest correlation matrix estimate
        - Uses recursive formula if USE_RECURSIVE_EWMA=True for exact incremental match
    """
    if USE_RECURSIVE_EWMA:
        # Use recursive EWMA (exact match to incremental engine)
        return calculate_ewma_correlation_recursive(
            returns_df,
            span=span,
            min_periods=min_periods,
            adjust=BATCH_EWM_ADJUST,
            demean=BATCH_DEMEAN
        )
    else:
        # Use pandas ewm().cov() (may diverge from incremental ~1e-8)
        # Calculate EWMA covariance
        if BATCH_DEMEAN:
            # Demean returns first
            returns_mean = returns_df.ewm(
                span=span,
                min_periods=min_periods,
                adjust=BATCH_EWM_ADJUST
            ).mean()
            returns_centered = returns_df - returns_mean
            ewma_cov = returns_centered.ewm(
                span=span,
                min_periods=min_periods,
                adjust=BATCH_EWM_ADJUST
            ).cov()
        else:
            ewma_cov = returns_df.ewm(
                span=span,
                min_periods=min_periods,
                adjust=BATCH_EWM_ADJUST
            ).cov()

        # Extract the most recent covariance matrix
        latest_date = returns_df.index[-1]
        cov_matrix = ewma_cov.loc[latest_date]

        # Convert covariance to correlation
        # corr[i,j] = cov[i,j] / (std[i] * std[j])
        std_vec = np.sqrt(np.diag(cov_matrix))
        std_mat = np.outer(std_vec, std_vec)

        # Avoid division by zero
        corr_matrix = np.where(std_mat > 0, cov_matrix / std_mat, 0.0)

        # Ensure diagonal is 1.0
        np.fill_diagonal(corr_matrix, 1.0)

        # Convert to DataFrame
        corr_df = pd.DataFrame(
            corr_matrix,
            index=cov_matrix.index,
            columns=cov_matrix.columns
        )

        return corr_df


def calculate_portfolio_stdev(
    weights: Dict[str, float],
    corr_matrix: pd.DataFrame
) -> float:
    """
    Calculate portfolio standard deviation from weights and correlation matrix

    IMPORTANT: For Carver-style IDM calculation, weights should be NORMALIZED
    (sum to 1.0 in absolute terms) before calling this function. This is handled
    automatically by calculate_idm(normalize=True).

    For leveraged weights (sum != 1.0), the result scales proportionally with
    the weight sum, which may not be the desired behavior for IDM calculation.

    Args:
        weights: Dict mapping instrument -> weight (can be any scale)
        corr_matrix: Correlation matrix (must include all instruments in weights)

    Returns:
        Portfolio standard deviation (same units as weights)

    Formula:
        portfolio_stdev = sqrt(W' × Corr × W)
        where W is the weight vector

    Notes:
        - Assumes equal volatility across instruments (simplified for Phase 1)
        - More accurate would use actual volatilities, but correlation structure
          dominates for diversification calculation
    """
    # Align weights to correlation matrix columns
    instruments = list(corr_matrix.columns)
    weight_vec = np.array([weights.get(inst, 0.0) for inst in instruments])

    # Portfolio variance: W' * Corr * W
    portfolio_var = weight_vec @ corr_matrix.values @ weight_vec

    # Standard deviation
    portfolio_stdev = np.sqrt(max(portfolio_var, 0.0))

    return portfolio_stdev


def calculate_idm(
    weights: Dict[str, float],
    corr_matrix: pd.DataFrame,
    normalize: bool = True
) -> float:
    """
    Calculate Carver-style Instrument Diversification Multiplier (IDM).

    IDM measures the diversification benefit from holding multiple instruments.
    It is used as a LEVERAGE MULTIPLIER: exposure = base_weight × min(idm_raw, cap).

    Carver-style IDM properties:
    - Always ≥ 1.0 (1.0 = no diversification, higher = more diversification)
    - Scale-invariant: IDM(k×weights) = IDM(weights) for any k > 0
    - Calculated on NORMALIZED weights (sum(abs(w)) = 1.0 to handle long/short)

    Carver-style IDM definition:
    1. Normalize weights to sum to 1.0 in absolute terms
    2. Calculate portfolio_stdev on normalized weights: σ_p = sqrt(W' * Corr * W)
    3. IDM = 1 / σ_p

    This ensures IDM >= 1.0 always, with:
    - IDM = 1.0 for perfectly correlated assets (no diversification benefit)
    - IDM > 1.0 for less correlated assets (diversification benefit)
    - IDM = sqrt(N) for N perfectly uncorrelated, equal-weighted assets

    Args:
        weights: Dict mapping instrument -> weight (any scale, can be long/short)
        corr_matrix: Correlation matrix (must include all instruments)
        normalize: If True (default), normalize weights before calculation using
                   sum(abs(w)) to handle long/short positions correctly.
                   This ensures IDM ≥ 1.0 and scale-invariance (Carver-style).
                   If False, IDM can be < 1.0 (legacy behavior, not recommended).

    Returns:
        IDM (float): Diversification multiplier, ≥ 1.0 if normalize=True

    Examples:
        Perfect correlation (ρ=1.0): IDM = 1.0 (no diversification benefit)
        Zero correlation, equal weights: IDM = sqrt(N)
        Typical crypto (ρ~0.6-0.7, N=15): IDM ≈ 1.5-2.0

    Usage in system:
        idm_raw = calculate_idm(base_weights, corr_matrix, normalize=True)
        idm_applied = min(idm_raw, idm_cap)  # Cap for safety
        exposure_weights = {k: v * idm_applied for k, v in base_weights.items()}

    Notes:
        - Standard Carver-style: normalize=True (default)
        - IDM measures diversification benefit independent of leverage
        - Leverage is handled separately via gross_leverage_cap
    """
    if normalize:
        # Carver-style: normalize weights first
        total_abs_weight = sum(abs(w) for w in weights.values())
        if total_abs_weight < 1e-10:
            return 1.0
        normalized_weights = {k: v / total_abs_weight for k, v in weights.items()}
        portfolio_stdev = calculate_portfolio_stdev(normalized_weights, corr_matrix)
    else:
        # Legacy behavior (can give IDM < 1.0 with leveraged weights)
        portfolio_stdev = calculate_portfolio_stdev(weights, corr_matrix)

    if portfolio_stdev < 1e-10:
        return 1.0

    idm = 1.0 / portfolio_stdev

    return idm


def apply_gross_leverage_cap(
    weights: Dict[str, float],
    cap: float
) -> Dict[str, float]:
    """
    Apply gross leverage cap to weights

    Gross leverage = sum(|weights|)

    If gross leverage > cap, scale all weights proportionally to meet cap.

    Args:
        weights: Dict mapping instrument -> weight
        cap: Maximum gross leverage (e.g., 2.0)

    Returns:
        Dict mapping instrument -> adjusted weight

    Example:
        - weights: {BTC: 1.2, ETH: 0.8, BNB: -0.6}
        - gross leverage = |1.2| + |0.8| + |-0.6| = 2.6
        - cap = 2.0
        - scaling factor = 2.0 / 2.6 = 0.769
        - adjusted weights: {BTC: 0.923, ETH: 0.615, BNB: -0.462}
    """
    # Calculate gross leverage
    gross_leverage = sum(abs(w) for w in weights.values())

    if gross_leverage <= cap:
        # No adjustment needed
        return weights.copy()

    # Scale weights proportionally
    scaling_factor = cap / gross_leverage
    adjusted_weights = {
        inst: w * scaling_factor
        for inst, w in weights.items()
    }

    return adjusted_weights


def apply_idm_cap(
    weights: Dict[str, float],
    corr_matrix: pd.DataFrame,
    cap: float
) -> Dict[str, float]:
    """
    [DEPRECATED] This function is incompatible with Carver-style normalization.

    With normalize=True in calculate_idm(), IDM is scale-invariant. Scaling
    weights does NOT change IDM. This function incorrectly tries to reduce IDM
    by scaling weights, which is a no-op or increases leverage pointlessly.

    CORRECT APPROACH (implemented in IncrementalConstraintsEngine.step()):
        1. Calculate idm_raw from normalized weights (scale-invariant)
        2. Use as multiplier: idm_applied = min(idm_raw, idm_cap)
        3. Apply to weights: exposure = base_weight × idm_applied
        4. Then apply gross leverage cap

    This function is kept for backward compatibility only. Do not use.

    Args:
        weights: Dict mapping instrument -> weight
        corr_matrix: Correlation matrix
        cap: Maximum IDM (e.g., 2.5)

    Returns:
        Unchanged weights (deprecated behavior)
    """
    import warnings
    warnings.warn(
        "apply_idm_cap() is deprecated and incompatible with Carver-style normalization. "
        "IDM should be used as a multiplier (exposure = base_weight × min(idm_raw, cap)), "
        "not by scaling weights to change IDM.",
        DeprecationWarning,
        stacklevel=2
    )

    # Return weights unchanged (safest behavior)
    return weights.copy()


class IncrementalConstraintsEngine:
    """
    Incremental EWMA correlation and constraint calculator

    Maintains running EWMA covariance state to avoid O(T²) recalculation.

    Key Features:
    - Matches the batch correlation math exactly (adjust flag + return definition)
    - Updates correlation incrementally with O(N²) per step
    - Handles edge cases (insufficient data, zero variance)
    - Preserves constraint application order (IDM → gross leverage)

    Usage:
        engine = IncrementalConstraintsEngine(
            instruments=['BTC', 'ETH'],
            span=60,
            min_periods=20,
            idm_cap=2.5,
            gross_leverage_cap=2.0
        )

        for date in dates:
            returns_today = {...}  # Dict[instrument, return]
            weights_today = {...}  # Dict[instrument, weight]

            constrained_weights, gross_lev, idm = engine.step(
                date=date,
                returns=returns_today,
                weights=weights_today
            )
    """

    def __init__(
        self,
        instruments: List[str],
        span: int,
        min_periods: int,
        idm_cap: float,
        gross_leverage_cap: float = float('inf'),
        adjust: bool = True,
        demean: bool = False,
        idm_pre_cap: bool = False,
        returns: str = "pct",
        use_recursive: bool = True
    ):
        """
        Initialize incremental constraints engine

        Args:
            instruments: List of instrument names
            span: EWMA span (default 60)
            min_periods: Minimum observations before calculating correlation (default 20)
            idm_cap: Maximum IDM value
            gross_leverage_cap: Maximum gross leverage
            adjust: Whether to use bias-corrected EWMA (match batch function)
            demean: Whether to use demeaned returns for covariance (match batch)
            idm_pre_cap: Whether IDM series contains pre-cap values (match batch)
            returns: Return calculation method - "pct" or "log" (match batch)
            use_recursive: Use recursive EWMA (for documentation, not used here)
        """
        self.instruments = instruments
        self.n = len(instruments)
        self.span = span
        self.min_periods = min_periods
        self.idm_cap = idm_cap
        self.gross_leverage_cap = gross_leverage_cap
        self.adjust = adjust
        self.demean = demean
        self.idm_pre_cap = idm_pre_cap
        self.returns = returns  # For documentation; actual calculation in system.py

        # EWMA parameters
        self.alpha = 2.0 / (span + 1)

        # Running state (N×N matrices for covariance)
        self.ewma_cov = np.zeros((self.n, self.n))  # EWMA covariance matrix
        self.weight = 0.0  # Bias correction weight w[t] (if adjust=True)
        self.count = 0  # Number of observations seen

        # Running mean state (if demean=True)
        if self.demean:
            self.ewma_mean = np.zeros(self.n)  # EWMA mean vector
            self.weight_mean = 0.0  # Bias correction weight for mean

        # Instrument name to index mapping
        self.inst_to_idx = {inst: i for i, inst in enumerate(instruments)}

    def step(
        self,
        date: pd.Timestamp,
        returns: Dict[str, float],
        weights: Dict[str, float],
        return_diagnostics: bool = False
    ) -> Tuple[Dict[str, float], float, float, dict]:
        """
        Update EWMA state and apply Carver-style IDM multiplier + constraints for one date

        Args:
            date: Current date (for logging)
            returns: Dict mapping instrument -> return for this date
            weights: Dict mapping instrument -> base weights (before diversification benefit)
            return_diagnostics: If True, return detailed diagnostics dict

        Returns:
            Tuple of (constrained_weights, gross_leverage, idm, diagnostics)
            - constrained_weights: Final weights after IDM multiplier + gross lev cap
            - gross_leverage: Gross leverage of final weights
            - idm: IDM of final weights (Carver-style, normalized, ≥1.0)
            - diagnostics: Dict with detailed intermediate values (empty if return_diagnostics=False)

        Diagnostics dict contains (Carver-style IDM multiplier):
            IDM metrics:
            - idm_raw: Diversification measure (≥1.0, scale-invariant)
            - idm_applied: min(idm_raw, idm_cap) - capped multiplier
            - idm_cap: The cap value
            - idm_final: After all constraints (≈ idm_raw if only uniform scaling)
            - idm_multiplier_used: Boolean, True if idm_applied > 1.0
            - idm_cap_binding: Boolean, True if idm_raw > idm_cap

            Gross leverage metrics:
            - gross_lev_base: Before IDM multiplier
            - gross_lev_pre: After IDM multiplier, before gross lev cap
            - gross_lev_scalar: Scaling applied by gross lev cap
            - gross_lev_final: Final gross leverage
            - gross_lev_cap: The cap value
            - gross_lev_cap_binding: Boolean, True if gross_lev_pre > cap

            Overall scalars:
            - idm_scalar: IDM multiplier applied (= idm_applied)
            - overall_scalar_from_base: gross_lev_final / gross_lev_base

        Notes:
            - Updates internal EWMA covariance state
            - Calculates correlation from covariance
            - Applies IDM as multiplier (Carver-style): exposure = base_weight × idm_applied
            - Then applies gross leverage cap (absolute priority)
            - Returns results for this date only
        """
        # Update EWMA covariance with new returns
        self._update_ewma_cov(returns)

        # Get current correlation matrix
        corr_matrix = self._get_correlation_matrix()

        # Initialize diagnostics
        diag = {} if return_diagnostics else None

        # CRITICAL: IDM is the ONLY portfolio-level scaling (Carver-style)
        # - weights are already sized per-instrument (forecast + instrument vol)
        # - weights assume IDM = 1.0 (no diversification benefit)
        # - IDM is applied exactly ONCE here as a multiplier
        # - DO NOT apply any additional portfolio vol targeting or scaling

        # Calculate IDM from normalized weights (risk allocation perspective)
        idm_raw = calculate_idm(
            weights,
            corr_matrix,
            normalize=True  # Carver-style: always normalize for IDM calculation
        )

        # Apply IDM as leverage multiplier (Carver-style diversification benefit)
        idm_applied = min(idm_raw, self.idm_cap)

        # Scale exposure by diversification benefit (ONLY place IDM is applied)
        exposure_weights = {k: v * idm_applied for k, v in weights.items()}

        # Track intermediate values for diagnostics
        gross_lev_base = sum(abs(w) for w in weights.values())
        gross_lev_pre = sum(abs(w) for w in exposure_weights.values())

        # Apply gross leverage cap to exposure_weights
        if gross_lev_pre > self.gross_leverage_cap:
            gross_lev_scalar = self.gross_leverage_cap / gross_lev_pre
            constrained_weights = {k: v * gross_lev_scalar for k, v in exposure_weights.items()}
            gross_lev_final = self.gross_leverage_cap
        else:
            gross_lev_scalar = 1.0
            constrained_weights = exposure_weights
            gross_lev_final = gross_lev_pre

        # Calculate final IDM for verification (should be unchanged by uniform scaling)
        idm_final = calculate_idm(
            constrained_weights,
            corr_matrix,
            normalize=True
        )

        # Build comprehensive diagnostics dictionary
        if return_diagnostics:
            diag = {
                # IDM metrics
                'idm_raw': idm_raw,                      # Diversification measure (≥1.0)
                'idm_applied': idm_applied,              # Capped multiplier (≤cap)
                'idm_cap': self.idm_cap,                 # The cap value
                'idm_final': idm_final,                  # After all constraints
                'idm_multiplier_used': idm_applied > 1.0 + 1e-6,  # Boolean: did IDM increase leverage?

                # Gross leverage metrics (before IDM multiplier)
                'gross_lev_base': gross_lev_base,

                # Gross leverage metrics (after IDM multiplier, before gross lev cap)
                'gross_lev_pre': gross_lev_pre,

                # Gross leverage metrics (after gross lev cap)
                'gross_lev_scalar': gross_lev_scalar,    # Scaling applied by gross lev cap
                'gross_lev_final': gross_lev_final,      # Final gross leverage
                'gross_lev_cap': self.gross_leverage_cap,

                # Overall scalars
                'idm_scalar': idm_applied,                          # Scalar from IDM multiplier
                'overall_scalar_from_base': gross_lev_final / gross_lev_base if gross_lev_base > 1e-10 else 1.0,

                # Constraint binding indicators
                'idm_cap_binding': idm_raw > self.idm_cap + 1e-6,
                'gross_lev_cap_binding': gross_lev_pre > self.gross_leverage_cap + 0.01,
            }
        else:
            diag = {}

        # Invariant checks
        eps = 0.01

        # Invariant 1: Carver-style IDM always ≥ 1.0
        if not (idm_raw >= 1.0 - eps):
            raise ValueError(
                f"idm_raw {idm_raw:.3f} should be >= 1.0 (Carver-style normalization). "
                f"This indicates a bug in IDM calculation."
            )

        # Invariant 2: Applied IDM never exceeds cap (by definition of min)
        if not (idm_applied <= self.idm_cap + eps):
            raise ValueError(
                f"idm_applied {idm_applied:.3f} exceeds cap {self.idm_cap}. "
                f"This indicates a bug in IDM capping logic."
            )

        # Invariant 3: Applied IDM always ≥ 1.0 (since idm_raw ≥ 1.0)
        if not (idm_applied >= 1.0 - eps):
            raise ValueError(
                f"idm_applied {idm_applied:.3f} should be >= 1.0. "
                f"This indicates a bug in IDM calculation."
            )

        # Invariant 4: Gross leverage never exceeds cap (after constraint)
        if not (gross_lev_final <= self.gross_leverage_cap + eps):
            raise ValueError(
                f"gross_lev {gross_lev_final:.3f} exceeds cap {self.gross_leverage_cap}. "
                f"This indicates a bug in position constraints."
            )

        # Invariant 5: IDM final ≈ IDM raw (conditional on uniform scaling)
        # Only holds if normalized relative proportions unchanged
        # (i.e., only uniform scaling occurred, no instruments dropped or reweighted)
        def normalize_abs_weights(w_dict):
            total = sum(abs(v) for v in w_dict.values())
            if total < 1e-10:
                return {}
            return {k: abs(v) / total for k, v in w_dict.items()}

        norm_pre = normalize_abs_weights(weights)
        norm_post = normalize_abs_weights(constrained_weights)

        # Check if relative proportions changed
        proportions_changed = False
        if set(norm_pre.keys()) != set(norm_post.keys()):
            proportions_changed = True  # Instruments added/removed
        else:
            for k in norm_pre.keys():
                if abs(norm_pre[k] - norm_post.get(k, 0)) > 1e-6:
                    proportions_changed = True
                    break

        if not proportions_changed:
            # Only uniform scaling → IDM should be unchanged (scale-invariant)
            if not (abs(idm_final - idm_raw) < 0.1):
                raise ValueError(
                    f"IDM changed with uniform scaling: {idm_raw:.3f} -> {idm_final:.3f}. "
                    f"This indicates a bug in IDM scale-invariance."
                )
        else:
            # Proportions changed → IDM can legitimately differ
            if return_diagnostics:
                diag['proportions_changed'] = True

        return constrained_weights, gross_lev_final, idm_final, diag

    def _update_ewma_cov(self, returns: Dict[str, float]):
        """
        Update EWMA covariance matrix with new returns

        Matches the batch function's EWMA mode (adjust + demean settings).
        If demean=True: cov = EWMA(outer(r - mean, r - mean))
        If demean=False: cov = EWMA(outer(r, r))

        Args:
            returns: Dict mapping instrument -> return for this date
        """
        self.count += 1

        # Convert returns dict to vector (ordered by self.instruments)
        r = np.array([returns.get(inst, 0.0) for inst in self.instruments])

        # Update EWMA mean if demeaning (must happen before covariance update)
        if self.demean:
            self._update_ewma_mean(r)
            # Use demeaned returns for outer product
            r_centered = r - self.ewma_mean
            outer_product = np.outer(r_centered, r_centered)
        else:
            # Use raw returns for outer product
            outer_product = np.outer(r, r)

        if self.count == 1:
            # First observation: initialize
            self.ewma_cov = outer_product
            if self.adjust:
                self.weight = 1.0
        else:
            if self.adjust:
                # Bias-corrected EWMA (adjust=True)
                # ewma[t] = (alpha * x[t] + (1-alpha) * ewma[t-1] * w[t-1]) / w[t]
                # w[t] = 1 + (1-alpha) * w[t-1]
                old_weight = self.weight
                self.weight = 1.0 + (1.0 - self.alpha) * old_weight

                numerator = (
                    self.alpha * outer_product +
                    (1.0 - self.alpha) * self.ewma_cov * old_weight
                )
                self.ewma_cov = numerator / self.weight
            else:
                # Simple EWMA (adjust=False)
                # ewma[t] = alpha * x[t] + (1-alpha) * ewma[t-1]
                self.ewma_cov = (
                    self.alpha * outer_product +
                    (1.0 - self.alpha) * self.ewma_cov
                )

    def _update_ewma_mean(self, r: np.ndarray):
        """
        Update EWMA mean vector (only called if demean=True)

        Args:
            r: Returns vector for this date (N-dimensional)
        """
        if self.count == 1:
            self.ewma_mean = r
            if self.adjust:
                self.weight_mean = 1.0
        else:
            if self.adjust:
                old_weight = self.weight_mean
                self.weight_mean = 1.0 + (1.0 - self.alpha) * old_weight
                numerator = self.alpha * r + (1.0 - self.alpha) * self.ewma_mean * old_weight
                self.ewma_mean = numerator / self.weight_mean
            else:
                self.ewma_mean = self.alpha * r + (1.0 - self.alpha) * self.ewma_mean

    def _get_correlation_matrix(self) -> pd.DataFrame:
        """
        Convert EWMA covariance to correlation matrix

        Returns:
            N×N correlation matrix (DataFrame)

        Notes:
            - If count < min_periods, returns identity matrix (edge case)
            - Handles zero variance by setting corr=0 (diagonal=1)
        """
        if self.count < self.min_periods:
            # Insufficient data: return identity matrix
            return pd.DataFrame(
                np.eye(self.n),
                index=self.instruments,
                columns=self.instruments
            )

        # Extract standard deviations from diagonal
        std_vec = np.sqrt(np.diag(self.ewma_cov))

        # Convert covariance to correlation
        std_mat = np.outer(std_vec, std_vec)
        corr_matrix = np.where(std_mat > 0, self.ewma_cov / std_mat, 0.0)

        # Ensure diagonal is exactly 1.0
        np.fill_diagonal(corr_matrix, 1.0)

        # Convert to DataFrame for compatibility with existing functions
        corr_df = pd.DataFrame(
            corr_matrix,
            index=self.instruments,
            columns=self.instruments
        )

        # CRITICAL VALIDATIONS: Validate correlation matrix properties
        # These checks catch bugs in correlation calculation under jagged panels
        if not (corr_df.shape == (self.n, self.n)):
            raise ValueError(
                f"Correlation matrix shape {corr_df.shape} != expected ({self.n}, {self.n}). "
                f"This indicates a bug in correlation matrix construction."
            )

        # Check diagonal is 1.0
        diag = np.diag(corr_df.values)
        if not np.allclose(diag, 1.0, atol=1e-10):
            raise ValueError(
                f"Correlation matrix diagonal not all 1.0: {diag}. "
                f"This indicates a bug in correlation calculation."
            )

        # Check symmetry
        if not np.allclose(corr_df.values, corr_df.values.T, atol=1e-10):
            raise ValueError(
                "Correlation matrix not symmetric. "
                "This indicates a bug in correlation calculation."
            )

        # Check values in [-1, 1]
        if not ((corr_df.values >= -1.0 - 1e-10).all() and (corr_df.values <= 1.0 + 1e-10).all()):
            raise ValueError(
                f"Correlation values outside [-1, 1]: min={corr_df.values.min()}, max={corr_df.values.max()}. "
                f"This indicates a bug in correlation calculation."
            )

        return corr_df


def apply_portfolio_constraints(
    weights_df: pd.DataFrame,
    prices_df: pd.DataFrame,
    gross_leverage_cap: float,
    idm_cap: float,
    corr_span: int = 60,
    corr_min_periods: int = 20
) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
    """
    Apply portfolio constraints (gross leverage and IDM caps) to weights

    Args:
        weights_df: DataFrame with date index and instrument columns (weights)
        prices_df: DataFrame with date index and instrument columns (prices)
        gross_leverage_cap: Maximum gross leverage (e.g., 2.0)
        idm_cap: Maximum IDM (e.g., 2.5)
        corr_span: EWMA span for correlation (default 60 days)
        corr_min_periods: Minimum periods for correlation (default 20)

    Returns:
        Tuple of (constrained_weights_df, gross_leverage_series, idm_series):
        - constrained_weights_df: Adjusted weights after constraints
        - gross_leverage_series: Gross leverage over time (for diagnostics)
        - idm_series: IDM over time (for diagnostics)

    Notes:
        - Applies constraints in order: IDM first, then gross leverage
        - Calculates returns and correlations from prices
        - Returns diagnostic series for validation
    """
    # Calculate returns for correlation using shared helper
    returns_df = compute_returns(prices_df, method=BATCH_RETURNS)

    # Initialize outputs
    constrained_weights_data = {inst: [] for inst in weights_df.columns}
    gross_leverage_list = []
    idm_list = []

    for date in weights_df.index:
        # Get weights for this date
        weights_dict = weights_df.loc[date].to_dict()

        # Get returns up to this date for correlation calculation
        returns_history = returns_df.loc[:date]

        # Calculate EWMA correlation (if enough data)
        if len(returns_history) >= corr_min_periods:
            corr_matrix = calculate_ewma_correlation(
                returns_history,
                span=corr_span,
                min_periods=corr_min_periods
            )
        else:
            # Not enough data - use identity matrix (no correlation assumed)
            instruments = list(weights_df.columns)
            corr_matrix = pd.DataFrame(
                np.eye(len(instruments)),
                index=instruments,
                columns=instruments
            )

        # Apply Carver-style IDM multiplier + gross leverage cap
        # This matches the new IncrementalConstraintsEngine logic

        # Step 1: Calculate IDM from normalized weights (Carver-style)
        idm_raw = calculate_idm(weights_dict, corr_matrix, normalize=True)
        idm_applied = min(idm_raw, idm_cap)

        # Step 2: Apply IDM as multiplier (Carver-style diversification benefit)
        exposure_weights = {k: v * idm_applied for k, v in weights_dict.items()}

        # Step 3: Apply gross leverage cap (absolute priority)
        gross_lev_pre = sum(abs(w) for w in exposure_weights.values())
        if gross_lev_pre > gross_leverage_cap:
            scalar = gross_leverage_cap / gross_lev_pre
            final_weights = {k: v * scalar for k, v in exposure_weights.items()}
        else:
            final_weights = exposure_weights

        # Store constrained weights
        for inst in weights_df.columns:
            constrained_weights_data[inst].append(final_weights.get(inst, 0.0))

        # Calculate diagnostics (use Carver-style normalized IDM)
        gross_lev = sum(abs(w) for w in final_weights.values())
        idm = calculate_idm(final_weights, corr_matrix, normalize=True)

        gross_leverage_list.append(gross_lev)
        idm_list.append(idm)

    # Create DataFrames
    constrained_weights_df = pd.DataFrame(constrained_weights_data, index=weights_df.index)
    gross_leverage_series = pd.Series(gross_leverage_list, index=weights_df.index)
    idm_series = pd.Series(idm_list, index=weights_df.index)

    return constrained_weights_df, gross_leverage_series, idm_series
