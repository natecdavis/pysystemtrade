"""
C4. XGBoost forecast-multiplier combiner.

Trains a single XGBoost regressor on pooled (instrument, date) rows whose
features are the 122 per-rule capped forecasts plus per-instrument and
portfolio-state covariates, and whose label is the next-N-day vol-normalized
return. Output is a per-(instrument, date) multiplier in [0.5, 1.5] applied
post-cap to the kitchen_sink + Carver-cost-filter baseline forecast.

Walk-forward discipline: monthly retrains. At each retrain date `t`, only
training rows whose **label end** date `t_label_end <= t - 1` are eligible.
This is the explicit leakage-prevention invariant. The last 20% of training
rows (time-ordered, not random) is held out as the early-stopping validation
slice — random k-fold would leak future blocks into the validation set.

The squashing transform `multiplier = clip(1 + 0.5*tanh(y_hat/sigma), 0.5, 1.5)`
keeps the multiplier symmetric around 1.0 so a uniform predictor maps to the
identity (baseline reproduction). `sigma` is the IQR/2 of the training-time
prediction distribution at each fit, never the test-time distribution — this
keeps the squash self-calibrating without leaking test scale.

Anchored design: the model never replaces the baseline; even a catastrophic
y_hat can only halve or 1.5x a well-calibrated forecast. This is the residual
framing — "when does the consensus forecast over- or under-react?" — and
explicitly avoids replacing the linear combination that already extracts the
average signal across rules. Linear walk-forward weight schemes were rejected
(out/wf_comparison_56rules); a tree model is being given a strictly smaller
problem to solve, on top of that linear baseline.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd
from scipy.stats import norm as _scipy_norm

logger = logging.getLogger(__name__)

# Used by `assert_multiplier_panel_fresh` to anchor relative paths from config.
# Matches the resolution that trade_plan.py historically used.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Lazy import — xgboost is heavy and only needed when actually fitting
def _import_xgb():
    import xgboost as xgb
    return xgb


def _import_joblib():
    import joblib
    return joblib


class FitNotPersistedError(Exception):
    """Raised by load_latest_fit when no usable persisted fit is on disk
    (file missing, corrupt, or feature schema mismatched). Callers handle this
    by falling back to a full from-scratch rebuild.
    """


def assert_multiplier_panel_fresh(
    panel_path: "Path | str",
    max_age_hours: float = 30.0,
) -> Path:
    """Resolve `panel_path` (relative paths anchored at repo root) and assert
    the file exists and has been modified within the last `max_age_hours`.

    Both consumers of the C4 multiplier panel call this so a stale panel
    fails-closed at both checkpoints (audit F4, 2026-05-06):
      * `forecast_combine_gated._apply_walk_forward_multiplier` — fires once
        at backtest start so the backtest's `positions.csv` cannot be silently
        modulated by a stale panel.
      * `trade_plan.generate_trade_plan` — re-checks at trade-plan time so a
        panel that ages past the threshold between backtest and trade-plan
        (e.g. an unusually slow run) still fails-closed.

    Returns the resolved absolute Path on success.

    Raises:
        ValueError: if the file is missing or older than the threshold.
    """
    p = Path(panel_path)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    if not p.exists():
        raise ValueError(
            f"walk_forward_multiplier_panel_path is set in config but file is missing: "
            f"{p}. Run scripts/build_c4_multiplier_panel.py to rebuild, or "
            f"remove the config key to fall back to the baseline (no C4 multiplier)."
        )
    age_hours = (time.time() - p.stat().st_mtime) / 3600.0
    if age_hours > max_age_hours:
        raise ValueError(
            f"C4 multiplier panel at {p} is {age_hours:.1f}h old "
            f"(threshold {max_age_hours:g}h). Daily rebuild step appears to have failed. "
            f"Investigate scripts/extract_rule_forecasts.py + "
            f"scripts/build_c4_multiplier_panel.py before trading."
        )
    logger.info(
        "C4 multiplier panel: %s (%.1fh old, fresh)", p.name, age_hours
    )
    return p


# Tolerance band for "exactly 1.0" identity (floating-point) and for
# "essentially zero std" (portfolio-state-only modulation, where every
# instrument got the same prediction because tree splits were all on
# portfolio-state features). Tight enough to distinguish a real bias-only
# fit from sub-bp instrument-level variation.
_IDENTITY_TOL = 1e-6
_PORTFOLIO_ONLY_STD_TOL = 1e-9


def summarize_multiplier_row(
    panel: pd.DataFrame,
    row_date: Optional[pd.Timestamp] = None,
) -> dict:
    """Summarize a single date's multiplier row from a C4 multiplier panel.

    The summary is meant to give the operator a one-glance answer to
    "is C4 actually contributing today?" — surfacing the four observed
    states identified in the 2026-05-06 audit (Phase B Probe 6):
      * "identity"       — every instrument == 1.0 (model is bias-only,
                            i.e. last refit's `is_uninformative=True`).
      * "portfolio-only" — every instrument got the same multiplier
                            (model splits driven only by portfolio-state
                            features; no per-instrument resolution).
      * "modulated"      — genuine per-instrument modulation.
      * "no_data"        — row date has no non-NaN cells.

    Args:
        panel: DateTimeIndex × instrument-columns DataFrame in [0.5, 1.5].
        row_date: target date. Defaults to `panel.index.max()`.

    Returns dict suitable for both logging and JSON serialization.
    """
    if panel.empty:
        return {
            "as_of_date": None,
            "n_instruments": 0,
            "mean": None, "std": None, "min": None, "max": None,
            "frac_identity": None, "frac_at_floor": None, "frac_at_ceiling": None,
            "all_identity": False,
            "mode": "no_data",
        }

    if row_date is None:
        row_date = panel.index.max()
    else:
        row_date = pd.Timestamp(row_date)
        if row_date not in panel.index:
            row_date = panel.index.max()

    row = panel.loc[row_date].dropna()
    n = int(len(row))
    if n == 0:
        return {
            "as_of_date": str(pd.Timestamp(row_date).date()),
            "n_instruments": 0,
            "mean": None, "std": None, "min": None, "max": None,
            "frac_identity": None, "frac_at_floor": None, "frac_at_ceiling": None,
            "all_identity": False,
            "mode": "no_data",
        }

    arr = row.to_numpy(dtype=float)
    mean = float(arr.mean())
    std = float(arr.std()) if n > 1 else 0.0
    mn = float(arr.min())
    mx = float(arr.max())
    frac_identity = float(np.mean(np.abs(arr - 1.0) <= _IDENTITY_TOL))
    frac_at_floor = float(np.mean(arr <= 0.501))
    frac_at_ceiling = float(np.mean(arr >= 1.499))
    all_identity = bool(frac_identity == 1.0)

    if all_identity:
        mode = "identity"
    elif std <= _PORTFOLIO_ONLY_STD_TOL:
        mode = "portfolio-only"
    else:
        mode = "modulated"

    return {
        "as_of_date": str(pd.Timestamp(row_date).date()),
        "n_instruments": n,
        "mean": round(mean, 6),
        "std": round(std, 6),
        "min": round(mn, 6),
        "max": round(mx, 6),
        "frac_identity": round(frac_identity, 6),
        "frac_at_floor": round(frac_at_floor, 6),
        "frac_at_ceiling": round(frac_at_ceiling, 6),
        "all_identity": all_identity,
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# Constants — pre-stated per the C4 spec; do NOT sweep post-hoc.
# ---------------------------------------------------------------------------

XGB_PARAMS: dict = dict(
    objective="reg:squarederror",
    max_depth=3,
    n_estimators=100,
    learning_rate=0.1,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
    tree_method="hist",
)
EARLY_STOPPING_ROUNDS: int = 20
VALIDATION_SLICE_FRAC: float = 0.20

MULT_FLOOR: float = 0.5
MULT_CEILING: float = 1.5
# Sigma floor matches the natural noise scale of vol-normalized 5-20d returns
# (std≈1.0 by construction). A floor below ~0.1 lets random outlier predictions
# saturate the squash; we set 0.5 so a |y_hat|=0.5 prediction maps to mult≈1.38
# (well-defined modulation) and |y_hat|>=2 saturates only when signal is large.
TANH_SIGMA_MIN: float = 0.5

# Macro/state lookbacks — chosen to match C3 conventions where possible
PORTFOLIO_VOL_LOOKBACK_DAYS: int = 30
DXY_MOM_LOOKBACK_DAYS: int = 60
DXY_Z_LOOKBACK_DAYS: int = 252
STABLECOIN_LOGCHG_LOOKBACK_DAYS: int = 30
INSTR_VOL_LOOKBACK_DAYS: int = 30
INSTR_RET_LOOKBACK_DAYS: int = 30

# Cross-corr basket: instruments with at least this many non-NaN returns in
# the trailing 90d window get included in the pairwise correlation calc.
XCORR_LOOKBACK_DAYS: int = 30
XCORR_BASKET_LOOKBACK_DAYS: int = 90
XCORR_BASKET_MIN_OBS: int = 60


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_rule_forecast_panel(panels_dir: Path) -> pd.DataFrame:
    """Returns the cached post-cap rule-forecast panel.

    Index: DateTimeIndex (calendar daily).
    Columns: MultiIndex(level0='rule', level1='instrument'). Values in [-20, +20].
    NaN where a rule does not apply for that (instrument, date).
    """
    f = pd.read_parquet(panels_dir / "forecasts.parquet")
    if f.columns.names != ["rule", "instrument"]:
        raise ValueError(
            f"forecasts.parquet column MultiIndex names = {f.columns.names}, "
            f"expected ['rule', 'instrument']. Re-run extract_rule_forecasts.py."
        )
    return f


def load_returns_panel(panels_dir: Path) -> pd.DataFrame:
    """Returns the cached per-instrument daily-returns panel.

    Index: DateTimeIndex. Columns: instrument names. Values: daily log returns.
    NaN where the instrument does not have price data for that date.
    """
    return pd.read_parquet(panels_dir / "returns.parquet")


def load_baseline_diagnostics(baseline_dir: Path) -> pd.DataFrame:
    """Loads the baseline backtest's diagnostics.parquet — long form with
    columns ['date', 'instrument', 'position', 'combined_forecast',
    'instrument_weight', 'fdm', 'idm']. Used to extract the post-cap baseline
    combined_forecast as a feature so the model can learn deviations from
    consensus.
    """
    return pd.read_parquet(baseline_dir / "diagnostics.parquet")


def load_baseline_daily_returns(baseline_dir: Path) -> pd.Series:
    df = pd.read_csv(baseline_dir / "daily_returns.csv", index_col=0, parse_dates=True)
    return df["net_return"]


def load_macro_factors(path: Path) -> pd.DataFrame:
    """Loads the macro factors parquet (DXY, 10Y, 5Y, etc.). Forward-fills
    over crypto-only days (weekends/holidays without macro updates) so the
    feature is defined every day the model needs it.
    """
    m = pd.read_parquet(path)
    return m.sort_index().ffill()


# ---------------------------------------------------------------------------
# Portfolio-state features (broadcast same value to every instrument at a date)
# ---------------------------------------------------------------------------

def portfolio_vol_signal(
    daily_returns: pd.Series,
    lookback_days: int = PORTFOLIO_VOL_LOOKBACK_DAYS,
) -> pd.Series:
    """30-day rolling realized vol of baseline portfolio daily returns,
    annualized (sqrt(365)). NaN until lookback window has >=10 obs.

    Pure rolling computation — no look-ahead at any date.
    """
    s = daily_returns.sort_index()
    return s.rolling(window=lookback_days, min_periods=10).std() * np.sqrt(365.0)


def dxy_momentum_z(
    dxy: pd.Series,
    mom_lookback_days: int = DXY_MOM_LOOKBACK_DAYS,
    z_lookback_days: int = DXY_Z_LOOKBACK_DAYS,
) -> pd.Series:
    """DXY 60-day log-momentum z-scored over a 252-day rolling window.

    No look-ahead: at date t, uses log-prices through t to compute the
    60-day diff at t, then z-scores against the prior 252-day diff history.
    """
    log_dxy = np.log(dxy.sort_index())
    mom = log_dxy.diff(mom_lookback_days)
    rolling_mean = mom.rolling(window=z_lookback_days, min_periods=60).mean()
    rolling_std = mom.rolling(window=z_lookback_days, min_periods=60).std()
    z = (mom - rolling_mean) / rolling_std.replace(0.0, np.nan)
    return z


def stablecoin_supply_logchg(
    supply: pd.Series,
    lookback_days: int = STABLECOIN_LOGCHG_LOOKBACK_DAYS,
) -> pd.Series:
    """30-day log-change of total stablecoin supply. Trend signal — rising
    stablecoin supply is interpreted as risk-off in C2b literature; the model
    decides how to use it.
    """
    return np.log(supply.sort_index()).diff(lookback_days)


def realized_xcorr(
    returns: pd.DataFrame,
    lookback_days: int = XCORR_LOOKBACK_DAYS,
    basket_lookback_days: int = XCORR_BASKET_LOOKBACK_DAYS,
    basket_min_obs: int = XCORR_BASKET_MIN_OBS,
) -> pd.Series:
    """Rolling 30-day mean off-diagonal correlation across the data-rich basket.

    Basket selection rule (per date): instruments with >=basket_min_obs
    non-NaN returns in the trailing basket_lookback_days window. Avoids
    pulling young instruments with degenerate correlations into the matrix.

    Implementation: at each date we compute the basket, slice the trailing
    lookback_days returns over that basket, compute the corr matrix, return
    the mean of off-diagonal entries. NaN until the basket has >=2 members
    with full lookback coverage.

    No look-ahead: every operation at date t uses returns indexed <= t.
    """
    r = returns.sort_index()
    out: dict[pd.Timestamp, float] = {}
    dates = r.index
    for i, t in enumerate(dates):
        if i < basket_lookback_days:
            out[t] = np.nan
            continue
        basket_window = r.iloc[i - basket_lookback_days + 1 : i + 1]
        n_obs = basket_window.notna().sum()
        basket = n_obs[n_obs >= basket_min_obs].index.tolist()
        if len(basket) < 2:
            out[t] = np.nan
            continue
        corr_window = r.iloc[i - lookback_days + 1 : i + 1][basket]
        if corr_window.shape[0] < 10:
            out[t] = np.nan
            continue
        c = corr_window.corr()
        # Mean off-diagonal: subtract the trace (always 1s) and normalize by
        # the count of off-diagonal cells.
        n = c.shape[0]
        if n < 2:
            out[t] = np.nan
            continue
        total = c.values.sum() - n  # diag is 1.0 each, n entries
        off_diag_count = n * (n - 1)
        out[t] = float(total / off_diag_count) if off_diag_count > 0 else np.nan
    return pd.Series(out).sort_index()


# ---------------------------------------------------------------------------
# Per-instrument features
# ---------------------------------------------------------------------------

def per_instrument_vol(
    returns: pd.DataFrame,
    lookback_days: int = INSTR_VOL_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """Per-instrument 30-day rolling realized vol (annualized sqrt(365))."""
    return returns.rolling(window=lookback_days, min_periods=10).std() * np.sqrt(365.0)


def per_instrument_return(
    returns: pd.DataFrame,
    lookback_days: int = INSTR_RET_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """Per-instrument trailing 30-day cumulative return."""
    return returns.rolling(window=lookback_days, min_periods=10).sum()


def vol_normalized_forward_return(
    instr_returns: pd.Series,
    instr_vol_annualized: pd.Series,
    horizon_days: int,
) -> pd.Series:
    """Label: at date t, sum of returns over [t+1, t+horizon_days] divided by
    the instrument's per-period vol (vol_annualized * sqrt(horizon_days/365)).

    NaN where:
    - the rolling forward window doesn't fit (last horizon_days of the series)
    - vol at t is NaN or zero
    - any return in the forward window is NaN

    No look-ahead in the value of the label itself by definition, BUT the
    label *uses future returns* — so any (instrument, date) row whose label is
    needed at training time must have feature_date such that
    `feature_date + horizon_days <= refit_date - 1`. That gating is enforced
    in `fit_predict_walk_forward`, NOT here.
    """
    # rolling(H).sum() at index k = sum of returns at [k-H+1, k]
    # shift(-H) at index t pulls in the value at index t+H = sum [t+1, t+H]
    forward_sum = instr_returns.rolling(window=horizon_days, min_periods=horizon_days).sum().shift(-horizon_days)
    horizon_vol = instr_vol_annualized * np.sqrt(horizon_days / 365.0)
    horizon_vol = horizon_vol.where(horizon_vol > 0, np.nan)
    return forward_sum / horizon_vol


# ---------------------------------------------------------------------------
# Target transformations (Cakici-Zaremba 2026 experiment)
#
# Six variants. The default `VOL_NORM_PER_INSTR` reproduces the original C4
# label byte-for-byte — it does NOT route through `apply_target_transform`.
# The five cross-sectional variants compute their op within each calendar
# date across instruments, applied to the long-form panel AFTER per-instrument
# label assembly. Within-date NaN labels are excluded from the cross-section
# and remain NaN on output.
# ---------------------------------------------------------------------------

class TargetTransform(str, Enum):
    VOL_NORM_PER_INSTR = "vol_norm_per_instr"
    CS_DEMEANED = "cs_demeaned"
    CS_STANDARDIZED = "cs_standardized"
    CS_PERCENTILE = "cs_percentile"
    CS_RANK = "cs_rank"
    CS_GAUSSIAN_RANK = "cs_gaussian_rank"


# Minimum cross-section size for a date to be transformed. Dates with fewer
# valid labels emit all-NaN for that date (avoids degenerate single-instrument
# z-scores and Gaussianized-rank inf).
_CS_MIN_N: int = 5


def _cs_unbounded_op(group: pd.Series, op: str) -> pd.Series:
    """Cross-sectional demean / standardize within a single date."""
    valid = group.dropna()
    if len(valid) < _CS_MIN_N:
        return pd.Series(np.nan, index=group.index)
    if op == "demeaned":
        out = group - valid.mean()
    elif op == "standardized":
        sd = valid.std(ddof=0)
        if not np.isfinite(sd) or sd <= 0:
            return pd.Series(np.nan, index=group.index)
        out = (group - valid.mean()) / sd
    else:  # pragma: no cover - defensive
        raise ValueError(f"Unknown unbounded op: {op}")
    return out


def _cs_rank_op(group: pd.Series, variant: TargetTransform) -> pd.Series:
    """Cross-sectional rank / percentile / Gaussianized rank within one date.

    Percentile maps to [-1, 1] via `2 * pct_rank - 1`. Rank uses the paper's
    formula `2*(rank-1)/(N-1) - 1`. Gaussianized rank uses `Φ⁻¹(rank/(N+1))`,
    bounded away from the {0, 1} singularities by the (N+1) denominator.
    """
    valid = group.dropna()
    n = len(valid)
    if n < _CS_MIN_N:
        return pd.Series(np.nan, index=group.index)
    if variant is TargetTransform.CS_PERCENTILE:
        pct = valid.rank(method="average", pct=True)
        out_valid = 2.0 * pct - 1.0
    elif variant is TargetTransform.CS_RANK:
        rk = valid.rank(method="average")
        out_valid = 2.0 * (rk - 1.0) / (n - 1.0) - 1.0 if n > 1 else pd.Series(0.0, index=valid.index)
    elif variant is TargetTransform.CS_GAUSSIAN_RANK:
        rk = valid.rank(method="average")
        # rank/(N+1) keeps values in the open interval (0, 1) so Φ⁻¹ is finite
        out_valid = pd.Series(_scipy_norm.ppf(rk.values / (n + 1.0)), index=valid.index)
    else:  # pragma: no cover - defensive
        raise ValueError(f"Unknown rank variant: {variant}")
    return out_valid.reindex(group.index)


def apply_target_transform(
    labels: pd.Series,
    transform: TargetTransform,
) -> pd.Series:
    """Apply a target transformation to a long-form per-(date, instrument)
    label Series.

    `labels` must be indexed by (`__date__`, `__instrument__`) — the same shape
    as `FeatureBundle.df[label_col]`. Returns a Series with the same index.

    `VOL_NORM_PER_INSTR` is a no-op pass-through (the per-instrument
    vol-normalization is already in `labels` by construction). Cross-sectional
    variants group by date and apply the within-date op. Dates with fewer than
    `_CS_MIN_N` valid labels produce all-NaN rows for that date.
    """
    if transform is TargetTransform.VOL_NORM_PER_INSTR:
        return labels.copy()

    if not isinstance(labels.index, pd.MultiIndex) or labels.index.nlevels < 2:
        raise ValueError(
            "apply_target_transform requires a MultiIndex (date, instrument) "
            "Series; got "
            f"{type(labels.index).__name__} with nlevels="
            f"{getattr(labels.index, 'nlevels', 0)}."
        )

    grouped = labels.groupby(level=0, group_keys=False, sort=False)

    if transform in (TargetTransform.CS_DEMEANED, TargetTransform.CS_STANDARDIZED):
        op = "demeaned" if transform is TargetTransform.CS_DEMEANED else "standardized"
        return grouped.apply(lambda g: _cs_unbounded_op(g, op))

    if transform in (
        TargetTransform.CS_PERCENTILE,
        TargetTransform.CS_RANK,
        TargetTransform.CS_GAUSSIAN_RANK,
    ):
        return grouped.apply(lambda g: _cs_rank_op(g, transform))

    raise ValueError(f"Unknown TargetTransform: {transform}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Feature panel assembly
# ---------------------------------------------------------------------------

def _diagnostics_combined_forecast_panel(diag: pd.DataFrame) -> pd.DataFrame:
    """Pivot the long-form baseline diagnostics into a (date x instrument) wide
    panel of combined_forecast values.
    """
    wide = diag.pivot(index="date", columns="instrument", values="combined_forecast")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def _flatten_rule_forecasts_per_instrument(
    forecasts: pd.DataFrame,
    instrument: str,
) -> pd.DataFrame:
    """Slice the MultiIndex forecast panel to a (date x rule) DataFrame for one
    instrument. Missing rule-instrument pairs become NaN columns (XGBoost
    handles natively via tree-method='hist' missing splits).
    """
    if instrument not in forecasts.columns.get_level_values("instrument"):
        return pd.DataFrame(index=forecasts.index)
    return forecasts.xs(instrument, axis=1, level="instrument")


@dataclass
class FeatureBundle:
    """Long-form feature DataFrame plus column metadata for downstream use."""
    df: pd.DataFrame  # MultiIndex (date, instrument) -> columns
    rule_feature_cols: list[str]
    aggregate_feature_cols: list[str]  # combined_fc, dispersion, long_share
    instrument_feature_cols: list[str]  # vol_30d, ret_30d
    portfolio_feature_cols: list[str]  # vol regime, dxy_z, mvrv, stablecoin, xcorr
    label_col: str

    @property
    def feature_cols(self) -> list[str]:
        return (
            self.rule_feature_cols
            + self.aggregate_feature_cols
            + self.instrument_feature_cols
            + self.portfolio_feature_cols
        )


def build_feature_panel(
    forecasts: pd.DataFrame,
    returns: pd.DataFrame,
    baseline_diagnostics: pd.DataFrame,
    baseline_daily_returns: pd.Series,
    macro: pd.DataFrame,
    stablecoin_supply: Optional[pd.Series],
    horizon_days: int,
    instruments: Optional[Iterable[str]] = None,
    only_dates: Optional[pd.DatetimeIndex] = None,
    target_transform: TargetTransform = TargetTransform.VOL_NORM_PER_INSTR,
) -> FeatureBundle:
    """Assemble the long-form (date, instrument) -> features+label panel.

    Strict no-look-ahead: every feature at date t uses only data <= t. The
    label uses returns in [t+1, t+horizon_days] and the vol normalizer uses
    instrument vol at date t.

    `only_dates`: if set, the returned bundle is restricted to those dates
    (rolling-feature lookbacks still see the full history; we just emit
    fewer rows). Used by incremental inference for today-only feature
    construction.

    `target_transform`: optional label transformation applied AFTER per-
    instrument vol-normalization. Default preserves the original label.
    Cross-sectional variants (`CS_DEMEANED`, `CS_STANDARDIZED`,
    `CS_PERCENTILE`, `CS_RANK`, `CS_GAUSSIAN_RANK`) operate within each
    `__date__` across instruments — see `apply_target_transform`.
    """
    if instruments is None:
        instruments = sorted(set(forecasts.columns.get_level_values("instrument")) & set(returns.columns))
    instruments = list(instruments)

    rule_names = sorted(set(forecasts.columns.get_level_values("rule")))

    # Portfolio-state series (one value per date, broadcast across instruments)
    port_vol = portfolio_vol_signal(baseline_daily_returns)
    dxy_z = dxy_momentum_z(macro["dxy"])
    mvrv = (
        # Use the diagnostics' price proxy if present; otherwise carry NaN
        # through. MVRV will be carried in via the Series the caller passes.
        pd.Series(index=port_vol.index, dtype=float)
    )
    if "mvrv" in macro.columns:
        mvrv = macro["mvrv"].sort_index()
    stable_chg = (
        stablecoin_supply_logchg(stablecoin_supply)
        if stablecoin_supply is not None
        else pd.Series(dtype=float)
    )
    xcorr = realized_xcorr(returns[instruments].copy())

    # Per-instrument panels
    instr_vol = per_instrument_vol(returns[instruments])
    instr_ret = per_instrument_return(returns[instruments])

    # Baseline combined_forecast panel
    combined_fc_panel = _diagnostics_combined_forecast_panel(baseline_diagnostics)

    # Long-form assembly per instrument
    rows: list[pd.DataFrame] = []
    for instr in instruments:
        rule_fc = _flatten_rule_forecasts_per_instrument(forecasts, instr)
        if rule_fc.empty:
            continue

        # Reindex to the union of feature dates available for this instrument
        date_index = rule_fc.index

        rule_fc_renamed = rule_fc.add_prefix("rule_fc__")
        # Ensure every rule_name appears as a column (so the model's feature
        # vector is fixed across instruments — XGBoost requires column stability).
        for r in rule_names:
            col = f"rule_fc__{r}"
            if col not in rule_fc_renamed.columns:
                rule_fc_renamed[col] = np.nan
        rule_fc_renamed = rule_fc_renamed[[f"rule_fc__{r}" for r in rule_names]]

        agg = pd.DataFrame(index=date_index)
        agg["combined_fc"] = combined_fc_panel.get(instr, pd.Series(dtype=float)).reindex(date_index)
        # Aggregate across non-NaN rule cells
        rule_vals = rule_fc.reindex(date_index)
        agg["fc_dispersion"] = rule_vals.std(axis=1)
        agg["fc_long_share"] = (rule_vals > 0).sum(axis=1) / rule_vals.notna().sum(axis=1).replace(0, np.nan)

        instr_feats = pd.DataFrame(index=date_index)
        instr_feats["instr_vol_30d"] = instr_vol[instr].reindex(date_index) if instr in instr_vol.columns else np.nan
        instr_feats["instr_ret_30d"] = instr_ret[instr].reindex(date_index) if instr in instr_ret.columns else np.nan

        port_feats = pd.DataFrame(index=date_index)
        port_feats["portfolio_vol_30d"] = port_vol.reindex(date_index)
        port_feats["dxy_mom_z"] = dxy_z.reindex(date_index).ffill()
        port_feats["mvrv"] = mvrv.reindex(date_index).ffill()
        port_feats["stablecoin_logchg_30d"] = stable_chg.reindex(date_index).ffill() if not stable_chg.empty else np.nan
        port_feats["realized_xcorr_30d"] = xcorr.reindex(date_index)

        # Label: next-N-day vol-normalized return for this instrument
        instr_returns = returns[instr] if instr in returns.columns else pd.Series(dtype=float)
        instr_vol_for_instr = instr_vol[instr] if instr in instr_vol.columns else pd.Series(index=date_index, dtype=float)
        label = vol_normalized_forward_return(
            instr_returns, instr_vol_for_instr, horizon_days
        ).reindex(date_index)

        block = pd.concat(
            [rule_fc_renamed, agg, instr_feats, port_feats], axis=1
        )
        block["__label__"] = label
        block["__instrument__"] = instr
        block["__date__"] = block.index
        # If a date filter was passed, drop rows outside it BEFORE the concat
        # to keep memory low for the incremental path. Rolling features have
        # already been computed against the full history above.
        if only_dates is not None:
            mask = block["__date__"].isin(only_dates)
            block = block.loc[mask]
            if block.empty:
                continue
        rows.append(block.reset_index(drop=True))

    if not rows:
        raise ValueError("No (instrument, date) feature rows were produced.")

    full = pd.concat(rows, axis=0, ignore_index=True)
    full = full.set_index(["__date__", "__instrument__"]).sort_index()

    if target_transform is not TargetTransform.VOL_NORM_PER_INSTR:
        full["__label__"] = apply_target_transform(full["__label__"], target_transform)

    rule_cols = [f"rule_fc__{r}" for r in rule_names]
    return FeatureBundle(
        df=full,
        rule_feature_cols=rule_cols,
        aggregate_feature_cols=["combined_fc", "fc_dispersion", "fc_long_share"],
        instrument_feature_cols=["instr_vol_30d", "instr_ret_30d"],
        portfolio_feature_cols=[
            "portfolio_vol_30d",
            "dxy_mom_z",
            "mvrv",
            "stablecoin_logchg_30d",
            "realized_xcorr_30d",
        ],
        label_col="__label__",
    )


# ---------------------------------------------------------------------------
# Walk-forward train/predict
# ---------------------------------------------------------------------------

@dataclass
class FitArtifact:
    """One per refit. Persisted across refits for the audit trail."""
    refit_date: pd.Timestamp
    n_train_rows: int
    n_val_rows: int
    best_iteration: int
    best_val_rmse: float
    feature_importance: dict[str, float]  # gain-weighted, normalized to sum=1
    train_pred_iqr: float  # IQR of training-time predictions, used as tanh sigma
    is_uninformative: bool = False  # True iff best_iter==0 (model is bias-only)


# ---------------------------------------------------------------------------
# Persistence: save / load the latest monthly fit so the daily flow doesn't
# have to retrain all 65 monthly models from scratch each run.
#
# Storage layout:
#   {dir}/latest.joblib       — XGBRegressor pickled via joblib
#   {dir}/latest.meta.json    — sigma, is_uninformative, refit_date,
#                                feature_cols (for schema validation), and
#                                an audit-trail subset of the FitArtifact
#
# Atomic write: tmp + os.replace, same pattern as parquet writes elsewhere.
# Schema validation on load: feature_cols must match exactly. Mismatch
# raises FitNotPersistedError; callers fall back to full rebuild.
# ---------------------------------------------------------------------------

_META_SCHEMA_VERSION = 1


def save_fit(
    model,
    artifact: FitArtifact,
    feature_cols: list[str],
    out_dir: Path,
) -> None:
    """Persist a single fit (the most-recent monthly refit) to disk."""
    joblib = _import_joblib()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model_path = out_dir / "latest.joblib"
    meta_path = out_dir / "latest.meta.json"

    # Atomic model write
    tmp_model = model_path.with_suffix(".joblib.tmp")
    joblib.dump(model, tmp_model)
    os.replace(tmp_model, model_path)

    meta = {
        "schema_version": _META_SCHEMA_VERSION,
        "refit_date": artifact.refit_date.isoformat(),
        "train_pred_iqr": artifact.train_pred_iqr,
        "is_uninformative": artifact.is_uninformative,
        "best_iteration": artifact.best_iteration,
        "best_val_rmse": artifact.best_val_rmse,
        "n_train_rows": artifact.n_train_rows,
        "n_val_rows": artifact.n_val_rows,
        "feature_cols": list(feature_cols),
        "xgb_params": dict(XGB_PARAMS),
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    tmp_meta = meta_path.with_suffix(".meta.json.tmp")
    tmp_meta.write_text(json.dumps(meta, indent=2))
    os.replace(tmp_meta, meta_path)


def load_latest_fit(
    in_dir: Path,
    expected_feature_cols: Optional[list[str]] = None,
) -> tuple[object, FitArtifact, list[str]]:
    """Load the latest persisted fit. Returns (model, artifact, feature_cols).

    Raises FitNotPersistedError if:
      - either of latest.joblib / latest.meta.json is missing
      - the meta JSON is malformed or missing required keys
      - joblib.load fails (corrupt model file)
      - expected_feature_cols is provided and doesn't match the persisted set

    Callers should treat this as a clean signal to fall back to a full
    rebuild — never silently use a stale or wrong model.
    """
    joblib = _import_joblib()
    in_dir = Path(in_dir)
    model_path = in_dir / "latest.joblib"
    meta_path = in_dir / "latest.meta.json"

    if not model_path.exists() or not meta_path.exists():
        raise FitNotPersistedError(
            f"No persisted fit at {in_dir} (model exists={model_path.exists()}, "
            f"meta exists={meta_path.exists()})"
        )

    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise FitNotPersistedError(f"Cannot read meta {meta_path}: {exc}") from exc

    required_keys = {
        "schema_version", "refit_date", "train_pred_iqr",
        "is_uninformative", "feature_cols",
    }
    missing = required_keys - meta.keys()
    if missing:
        raise FitNotPersistedError(
            f"Meta {meta_path} missing required keys: {sorted(missing)}"
        )
    if meta["schema_version"] != _META_SCHEMA_VERSION:
        raise FitNotPersistedError(
            f"Meta {meta_path} has schema_version={meta['schema_version']}, "
            f"expected {_META_SCHEMA_VERSION}"
        )

    persisted_feature_cols = list(meta["feature_cols"])
    if expected_feature_cols is not None and persisted_feature_cols != list(expected_feature_cols):
        raise FitNotPersistedError(
            f"Persisted fit's feature schema does not match expected. "
            f"Persisted has {len(persisted_feature_cols)} features; "
            f"expected has {len(expected_feature_cols)}. "
            f"First mismatch: persisted={persisted_feature_cols[:3]}, "
            f"expected={list(expected_feature_cols)[:3]}"
        )

    try:
        model = joblib.load(model_path)
    except Exception as exc:
        raise FitNotPersistedError(
            f"joblib.load({model_path}) failed: {exc}"
        ) from exc

    artifact = FitArtifact(
        refit_date=pd.Timestamp(meta["refit_date"]),
        n_train_rows=int(meta.get("n_train_rows", 0)),
        n_val_rows=int(meta.get("n_val_rows", 0)),
        best_iteration=int(meta.get("best_iteration", 0)),
        best_val_rmse=float(meta.get("best_val_rmse", float("nan"))),
        feature_importance={},  # not persisted; reconstructable from model.feature_importances_
        train_pred_iqr=float(meta["train_pred_iqr"]),
        is_uninformative=bool(meta["is_uninformative"]),
    )
    return model, artifact, persisted_feature_cols


def predict_today_only(
    model,
    artifact: FitArtifact,
    X_today: pd.DataFrame,
) -> pd.Series:
    """Apply a single fit to today's feature rows and produce per-instrument
    multipliers using the same squash logic as predictions_to_multiplier_panel.

    `X_today` must be indexed by instrument (no date level — one row per
    instrument), with columns matching the fit's training feature_cols.

    Returns a Series indexed by instrument → multiplier in [0.5, 1.5].

    The squash here mirrors `predictions_to_multiplier_panel` byte-for-byte
    so today's row is identical to what the from-scratch path would produce.
    """
    if artifact.is_uninformative:
        # Bias-only model — emit identity, do not modulate.
        return pd.Series(1.0, index=X_today.index, name="multiplier")

    y_hat = np.asarray(model.predict(X_today))
    sigma = max(artifact.train_pred_iqr, TANH_SIGMA_MIN)
    multiplier = 1.0 + 0.5 * np.tanh(y_hat / sigma)
    multiplier = np.clip(multiplier, MULT_FLOOR, MULT_CEILING)
    return pd.Series(multiplier, index=X_today.index, name="multiplier")


def _label_end_date(feature_date: pd.Timestamp, horizon_days: int) -> pd.Timestamp:
    """Calendar end of the label window anchored at `feature_date`."""
    return feature_date + pd.Timedelta(days=horizon_days)


def _train_one_fit(
    X: pd.DataFrame,
    y: pd.Series,
    refit_date: pd.Timestamp,
    random_state: Optional[int] = None,
) -> tuple["xgb.XGBRegressor", FitArtifact]:
    """Time-ordered train/val split, fit, return model + audit artifact.

    `random_state` overrides XGB_PARAMS["random_state"] for this fit only —
    used by the seed-sensitivity sweep. Module-level XGB_PARAMS is unchanged.
    """
    xgb = _import_xgb()

    # Time-ordered split: last VALIDATION_SLICE_FRAC of train rows as val
    n = len(X)
    n_val = max(int(n * VALIDATION_SLICE_FRAC), 1)
    n_train = n - n_val

    X_tr, X_val = X.iloc[:n_train], X.iloc[n_train:]
    y_tr, y_val = y.iloc[:n_train], y.iloc[n_train:]

    xgb_params = (
        {**XGB_PARAMS, "random_state": int(random_state)}
        if random_state is not None
        else XGB_PARAMS
    )
    model = xgb.XGBRegressor(
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        eval_metric="rmse",
        **xgb_params,
    )
    model.fit(
        X_tr,
        y_tr,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    best_iter = int(getattr(model, "best_iteration", model.n_estimators - 1))
    is_uninformative = best_iter == 0  # bias-only — see TANH_SIGMA_MIN comment

    train_preds = model.predict(X_tr)
    iqr = float(np.subtract(*np.percentile(train_preds, [75, 25])))
    sigma = max(iqr / 2.0, TANH_SIGMA_MIN)

    importances = model.feature_importances_
    importances = importances / importances.sum() if importances.sum() > 0 else importances
    fi = dict(zip(X.columns, importances))

    artifact = FitArtifact(
        refit_date=refit_date,
        n_train_rows=n_train,
        n_val_rows=n_val,
        best_iteration=best_iter,
        best_val_rmse=float(model.best_score) if hasattr(model, "best_score") else float("nan"),
        feature_importance=fi,
        train_pred_iqr=sigma,
        is_uninformative=is_uninformative,
    )
    return model, artifact


def fit_predict_walk_forward(
    bundle: FeatureBundle,
    horizon_days: int,
    retrain_freq: str = "MS",
    min_train_rows: int = 5_000,
    random_state: Optional[int] = None,
    freeze_training_after: Optional[pd.Timestamp] = None,
) -> tuple[pd.Series, list[FitArtifact]]:
    """Run monthly-retrain walk-forward training over the full feature panel.

    Returns:
        oos_preds: Series indexed by (date, instrument) -> raw model prediction.
            Only OOS predictions are populated — the (in-sample) training rows
            for any given fit are excluded from the output.
        artifacts: per-refit audit records.

    Discipline: at refit date t, only training rows whose label window ends
    on or before t-1 are eligible. Predictions for dates [t, next_refit_date)
    use the model fit at t.

    `random_state` overrides the per-fit XGB seed (default: XGB_PARAMS["random_state"]).
    `freeze_training_after` truncates the refit_dates list to dates <= cutoff;
    predictions for all dates after the last eligible refit use that frozen
    model. This is the "no continued retraining" stress test.
    """
    # Training set: only rows with valid labels (forward returns observable).
    train_df = bundle.df.dropna(subset=[bundle.label_col]).copy()
    # Inference set: every row with valid features, INCLUDING the most-recent
    # ~horizon_days where forward returns aren't observable yet (i.e. the
    # label is NaN). For live deployment we need a multiplier for today; today
    # has no future returns by definition. Filtering to label-valid rows for
    # inference would mean today's multiplier never reaches the live panel.
    feature_only_cols = list(bundle.feature_cols)
    infer_df = bundle.df[feature_only_cols].dropna(how="all").copy()

    feature_cols = bundle.feature_cols

    all_dates = sorted(train_df.index.get_level_values(0).unique())
    if not all_dates:
        raise ValueError("Feature bundle has no usable rows after label dropna.")
    start, end = all_dates[0], all_dates[-1]
    # End of inference data may extend past end of training data (the last
    # ~horizon_days). Refit-date schedule is anchored to the inference end so
    # the final refit covers today even if the labels don't.
    infer_end = infer_df.index.get_level_values(0).max()

    refit_dates = pd.date_range(start, infer_end, freq=retrain_freq)
    if freeze_training_after is not None:
        refit_dates = refit_dates[refit_dates <= pd.Timestamp(freeze_training_after)]
    # Drop refit dates before we have enough history
    if len(refit_dates) == 0:
        raise ValueError("No refit dates produced — check date range vs retrain_freq.")

    artifacts: list[FitArtifact] = []
    pred_chunks: list[pd.Series] = []

    train_dates = train_df.index.get_level_values(0)
    infer_dates = infer_df.index.get_level_values(0)

    for i, t in enumerate(refit_dates):
        # Eligible training rows: label end <= t - 1 day
        cutoff = t - pd.Timedelta(days=1)
        max_feature_date_for_training = cutoff - pd.Timedelta(days=horizon_days)
        train_mask = train_dates <= max_feature_date_for_training
        if train_mask.sum() < min_train_rows:
            continue
        X_train = train_df.loc[train_mask, feature_cols]
        y_train = train_df.loc[train_mask, bundle.label_col]

        model, artifact = _train_one_fit(X_train, y_train, t, random_state=random_state)
        artifacts.append(artifact)

        # Inference window: [t, next_refit). The last refit's window extends
        # to infer_end+1day, so the most-recent refit naturally covers any
        # dates with valid features through today, even if labels are NaN.
        next_t = refit_dates[i + 1] if i + 1 < len(refit_dates) else infer_end + pd.Timedelta(days=1)
        infer_mask = (infer_dates >= t) & (infer_dates < next_t)
        if infer_mask.sum() == 0:
            continue
        X_infer = infer_df.loc[infer_mask, feature_cols]
        preds = pd.Series(model.predict(X_infer), index=X_infer.index, name="y_hat")
        pred_chunks.append(preds)

    if not pred_chunks:
        raise RuntimeError(
            f"No OOS predictions produced — min_train_rows={min_train_rows} "
            f"may be too high, or refit_freq too tight."
        )

    oos_preds = pd.concat(pred_chunks).sort_index()
    return oos_preds, artifacts


# ---------------------------------------------------------------------------
# Multiplier panel emission
# ---------------------------------------------------------------------------

def predictions_to_multiplier_panel(
    oos_preds: pd.Series,
    artifacts: list[FitArtifact],
) -> pd.DataFrame:
    """Squash y_hat -> multiplier with the per-fit tanh sigma, reshape into
    a (date x instrument) wide DataFrame.

    Each refit's sigma applies to predictions emitted by that refit's model.
    """
    if not artifacts:
        raise ValueError("No fit artifacts — cannot squash predictions.")

    # Build per-fit sigma + uninformative-flag tables keyed by refit_date.
    sigmas = pd.Series(
        {a.refit_date: a.train_pred_iqr for a in artifacts}
    ).sort_index()
    uninformative = pd.Series(
        {a.refit_date: a.is_uninformative for a in artifacts}
    ).sort_index().reindex(sigmas.index).fillna(False).astype(bool)
    refit_dates = sigmas.index

    df = oos_preds.to_frame("y_hat").copy()
    dates = df.index.get_level_values(0)
    # For each row, find the most recent refit date <= row date (the model
    # that generated it).
    fit_idx = np.searchsorted(refit_dates.values, dates.values, side="right") - 1
    fit_idx = np.clip(fit_idx, 0, len(refit_dates) - 1)
    sigmas_per_row = sigmas.values[fit_idx]
    uninformative_per_row = uninformative.values[fit_idx]

    multiplier = 1.0 + 0.5 * np.tanh(df["y_hat"].values / sigmas_per_row)
    # Hard identity for uninformative fits — preserves the anchored-to-baseline
    # invariant when the model has no signal beyond the bias term. Without
    # this, bias-only predictions cluster around a non-zero mean and tanh
    # saturates the entire window to a single cap value.
    multiplier = np.where(uninformative_per_row, 1.0, multiplier)
    multiplier = np.clip(multiplier, MULT_FLOOR, MULT_CEILING)
    df["multiplier"] = multiplier

    panel = df["multiplier"].unstack(level=1).sort_index()
    return panel


def uniform_multiplier_panel(reference_panel: pd.DataFrame) -> pd.DataFrame:
    """Self-replication scaffold: all multipliers = 1.0, same shape as a real
    panel. Backtesting against this MUST reproduce the baseline within
    +/-0.02 Sharpe — that's the mandatory plumbing-validity check.
    """
    out = pd.DataFrame(1.0, index=reference_panel.index, columns=reference_panel.columns)
    return out


# ---------------------------------------------------------------------------
# Falsification-trigger summaries
# ---------------------------------------------------------------------------

def multiplier_distribution_stats(panel: pd.DataFrame) -> dict[str, float]:
    """Summary of the realized multiplier distribution, used by
    training_report.md to flag the "saturated at the caps" failure mode.
    """
    flat = panel.values.ravel()
    flat = flat[~np.isnan(flat)]
    if flat.size == 0:
        return {"n": 0}
    eps = 1e-3
    return {
        "n": int(flat.size),
        "mean": float(flat.mean()),
        "std": float(flat.std()),
        "p05": float(np.percentile(flat, 5)),
        "p50": float(np.percentile(flat, 50)),
        "p95": float(np.percentile(flat, 95)),
        "frac_at_floor": float((flat <= MULT_FLOOR + eps).mean()),
        "frac_at_ceiling": float((flat >= MULT_CEILING - eps).mean()),
    }


def aggregate_feature_importance(artifacts: list[FitArtifact]) -> pd.DataFrame:
    """Average feature importance across refits, plus per-feature variability."""
    if not artifacts:
        return pd.DataFrame()
    fi = pd.DataFrame([a.feature_importance for a in artifacts])
    summary = pd.DataFrame(
        {
            "mean_gain": fi.mean(axis=0),
            "std_gain": fi.std(axis=0),
            "n_fits": fi.notna().sum(axis=0),
        }
    ).sort_values("mean_gain", ascending=False)
    return summary
