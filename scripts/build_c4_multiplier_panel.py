#!/usr/bin/env python3
"""
Build the walk-forward XGBoost multiplier panel for the C4 candidate.

What this does:
  1. Load the cached 122-rule post-cap forecast panel + per-instrument returns
     panel from data/forecast_panels_122/.
  2. Load the kitchen_sink + Carver baseline backtest's diagnostics.parquet
     (for the combined_forecast feature) and daily_returns.csv (for the
     portfolio vol-state feature).
  3. Load macro factors (DXY, etc.) and stablecoin supply (if available).
  4. Build the long-form (date, instrument) -> features+label panel for the
     requested horizon.
  5. Fit/predict walk-forward (monthly retrain) — leakage-free per the
     discipline in c4_xgboost_combiner.fit_predict_walk_forward.
  6. Squash predictions through tanh -> multiplier in [0.5, 1.5].
  7. Write artifacts to out/wf_c4_xgboost_h{N}/:
       - multiplier_panel.parquet   (date x instrument multiplier)
       - oos_predictions.parquet    (raw y_hat, audit trail)
       - feature_importance.parquet (per-feature mean gain across refits)
       - training_report.md         (sample sizes, multiplier dist, falsifiers)

Self-replication mode:
  --uniform-multipliers writes a 1.0-everywhere panel of the same shape.
  Backtesting against this MUST reproduce the kitchen_sink baseline within
  ±0.02 Sharpe — that's the mandatory plumbing-validity check before
  interpreting any real C4 result.

Usage:
    python scripts/build_c4_multiplier_panel.py --horizon 5
    python scripts/build_c4_multiplier_panel.py --horizon 20
    python scripts/build_c4_multiplier_panel.py --horizon 5 --uniform-multipliers
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from systems.crypto_perps.c4_xgboost_combiner import (  # noqa: E402
    XGB_PARAMS,
    EARLY_STOPPING_ROUNDS,
    FitArtifact,
    FitNotPersistedError,
    TargetTransform,
    _train_one_fit,
    aggregate_feature_importance,
    build_feature_panel,
    fit_predict_walk_forward,
    load_baseline_daily_returns,
    load_baseline_diagnostics,
    load_latest_fit,
    load_macro_factors,
    load_returns_panel,
    load_rule_forecast_panel,
    multiplier_distribution_stats,
    predict_today_only,
    predictions_to_multiplier_panel,
    save_fit,
    uniform_multiplier_panel,
)


DEFAULT_BASELINE_DIR = (
    "out/wf_rule_selection/flat_122_kitchen_sink_carver_filter/"
    "backtest_flat_122_kitchen_sink_carver_filter"
)


def _load_stablecoin_supply(path: Path) -> pd.Series | None:
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    # The stablecoin_supply parquet has a single column of total USD supply.
    if df.shape[1] == 1:
        return df.iloc[:, 0].sort_index()
    # If multiple columns, assume "total" is the canonical one
    for c in ("total", "total_usd", "supply", "stablecoin_supply"):
        if c in df.columns:
            return df[c].sort_index()
    # Fallback: use the first column
    return df.iloc[:, 0].sort_index()


# ----------------------------------------------------------------------------
# Token-maturity penalty composition (live ADOPT 2026-05-19; user override).
#
# Maturity multiplier ∈ [1-β, 1] dampens positions for tokens listed within
# the last T days:
#     penalty(i, t)    = max(0, (T - days_since_listing(i, t)) / T)   ∈ [0, 1]
#     multiplier(i, t) = 1 - β × penalty(i, t)                         ∈ [1-β, 1]
#
# Composed into the live C4 panel here so the daily flow produces a single
# panel at data/c4_multiplier_panel_h20.parquet that the live runtime consumes
# via systems/crypto_perps/forecast_combine_gated.py:_apply_walk_forward_multiplier.
#
# B7 walk-forward verdict (2026-05-19): ΔSharpe -0.0076 / ΔCalmar -0.0103 /
# ΔMaxDD +0.72pp on the 124-rule kitchen-sink + Carver-filter baseline. Adopted
# under err-on-keeping precedent for the largest single-rule MaxDD improvement
# in project history. See project_maturity_penalty_adopted_2026-05-19.md.
#
# Launch_date is derived from the returns panel's first_valid_index() per
# instrument (= close-price launch_date + 1 day in lifecycle_df convention).
# The 1-day offset shifts the multiplier ramp by 1/365 of a step (~0.14% per
# cell). Negligible vs the WF test's lifecycle source.
# ----------------------------------------------------------------------------

MATURITY_BETA = 0.5
MATURITY_THRESHOLD_DAYS = 365


def _launch_dates_from_returns(returns: pd.DataFrame) -> pd.Series:
    """First non-NaN return date per instrument (launch_date + 1d proxy)."""
    return returns.apply(lambda c: c.first_valid_index())


def _apply_maturity_penalty(
    c4_panel: pd.DataFrame,
    returns: pd.DataFrame,
    beta: float = MATURITY_BETA,
    threshold_days: int = MATURITY_THRESHOLD_DAYS,
) -> pd.DataFrame:
    """Compose the C4 multiplier panel with the maturity multiplier element-wise.

    Instruments missing from `returns` (e.g., C4 covers more columns than the
    returns panel) get maturity=1.0 (no effect). Pre-launch cells also map to
    maturity=1.0 so the result is `c4_panel` unchanged at those cells — the
    consumer's `.fillna(1.0)` already handles missing-column cases, so the
    composed panel must NOT introduce new NaN.
    """
    dates = c4_panel.index
    cols = c4_panel.columns

    launch = _launch_dates_from_returns(returns).reindex(cols)

    # Build maturity multiplier on the (dates × cols) grid via vector ops.
    date_grid = np.tile(dates.values, (len(cols), 1)).T          # (T, N)
    launch_grid = np.tile(launch.values, (len(dates), 1))         # (T, N)
    delta_days = (date_grid - launch_grid).astype("timedelta64[D]").astype("float64")
    # Pre-launch OR unknown-launch cells: identity (multiplier=1).
    pre_launch = (delta_days < 0) | np.isnan(delta_days)
    days_since = np.where(pre_launch, np.nan, delta_days)
    penalty = np.clip((threshold_days - days_since) / threshold_days, 0.0, 1.0)
    mult = 1.0 - beta * penalty

    maturity_panel = pd.DataFrame(mult, index=dates, columns=cols).fillna(1.0)
    composed = c4_panel * maturity_panel

    print(
        f"  Applied maturity penalty (β={beta}, T={threshold_days}d): "
        f"affected {(maturity_panel < 1.0).any(axis=0).sum()}/{len(cols)} instruments "
        f"(min mult={maturity_panel.values.min():.4f}, mean={maturity_panel.values.mean():.4f})"
    )
    return composed


def _write_training_report(
    out_dir: Path,
    horizon_days: int,
    bundle_n_rows: int,
    n_instruments: int,
    artifacts: list,
    panel_stats: dict,
    fi_top: pd.DataFrame,
    uniform_override: bool,
    elapsed_s: float,
) -> None:
    lines = [
        f"# C4 XGBoost combiner — training report (h={horizon_days})",
        "",
        f"- **Mode:** {'UNIFORM (self-replication)' if uniform_override else 'XGBoost regime-conditioned'}",
        f"- **Horizon (label window):** {horizon_days} days",
        f"- **Feature rows after dropna(label):** {bundle_n_rows:,}",
        f"- **Instruments included:** {n_instruments}",
        f"- **Refits:** {len(artifacts)}",
        f"- **Wall time:** {elapsed_s:.0f}s",
        "",
        "## XGBoost hyperparameters (PRE-STATED — do not sweep post-hoc)",
        "",
        f"```",
        f"{json.dumps(XGB_PARAMS, indent=2)}",
        f"early_stopping_rounds = {EARLY_STOPPING_ROUNDS}",
        f"```",
        "",
        "## Per-refit summary",
        "",
        "| refit_date | n_train | n_val | best_iter | val_rmse | sigma |",
        "|---|---|---|---|---|---|",
    ]
    for a in artifacts:
        lines.append(
            f"| {a.refit_date.date()} | {a.n_train_rows:,} | {a.n_val_rows:,} "
            f"| {a.best_iteration} | {a.best_val_rmse:.4f} | {a.train_pred_iqr:.4f} |"
        )

    lines += [
        "",
        "## Multiplier distribution (post-tanh squash, OOS only)",
        "",
        f"- N: {panel_stats.get('n', 0):,}",
        f"- mean: {panel_stats.get('mean', float('nan')):.4f}",
        f"- std:  {panel_stats.get('std', float('nan')):.4f}",
        f"- p05/p50/p95: {panel_stats.get('p05', float('nan')):.4f} / "
        f"{panel_stats.get('p50', float('nan')):.4f} / "
        f"{panel_stats.get('p95', float('nan')):.4f}",
        f"- frac at floor (≤0.5+ε):   {panel_stats.get('frac_at_floor', float('nan')):.2%}",
        f"- frac at ceiling (≥1.5-ε): {panel_stats.get('frac_at_ceiling', float('nan')):.2%}",
        "",
        "## Top feature importance (mean gain across refits)",
        "",
        "| feature | mean_gain | std_gain | n_fits |",
        "|---|---|---|---|",
    ]
    for feat, row in fi_top.iterrows():
        lines.append(f"| {feat} | {row['mean_gain']:.4f} | {row['std_gain']:.4f} | {int(row['n_fits'])} |")

    lines += [
        "",
        "## Falsification triggers (review before claiming adoption)",
        "",
        f"- **Multiplier saturation** — frac_at_floor + frac_at_ceiling = "
        f"{(panel_stats.get('frac_at_floor', 0.0) + panel_stats.get('frac_at_ceiling', 0.0)):.2%}. "
        f"If >40%, model is just sign-flipping; increase tanh sigma or reject.",
        f"- **Macro-feature dominance** — sum of (portfolio_vol_30d, dxy_mom_z, mvrv, "
        f"stablecoin_logchg_30d, realized_xcorr_30d) gain ≥ 60% means the model is "
        f"acting as a regime layer, not a combiner. Compare to C3 result before claiming "
        f"ML-combiner credit.",
        f"- **combined_fc dominance** — combined_fc gain alone ≥ 50% means the model is "
        f"learning noise on top of consensus. Reject.",
        f"- **Per-quarter Sharpe stability** — see harness's per_window.parquet decomposition "
        f"in the corresponding wf_c4_* output dir; if 1-2 quarters drive >50% of ΔSharpe, REJECT.",
    ]
    (out_dir / "training_report.md").write_text("\n".join(lines))


def _run_incremental(
    args, panels_dir, baseline_dir, macro_path, stable_path,
    live_panel_path, model_store_dir,
) -> int:
    """Incremental path: load persisted latest fit, predict only today's row,
    append to the existing live multiplier panel.

    Falls back to full rebuild (returns -1) if:
      - the live multiplier panel doesn't exist (first-time bootstrap)
      - load_latest_fit raises FitNotPersistedError (no persisted fit, corrupt
        file, schema mismatch)
      - month boundary requires a fresh fit (we DO train inline in this case
        rather than fall back)

    The full-rebuild fallback path is the existing main() body — caller
    handles the -1 return code by re-running with args.incremental=False.
    """
    print("=== build_c4_multiplier_panel (INCREMENTAL) ===")
    print(f"Horizon:      {args.horizon}d")
    print(f"Live panel:   {live_panel_path}")
    print(f"Model store:  {model_store_dir}")
    print()

    t0 = time.time()

    # Live panel must exist for incremental
    if not live_panel_path.exists():
        print(f"[FALLBACK] Live multiplier panel missing at {live_panel_path}.")
        print("  Falling back to full rebuild (will create the panel + save latest fit).")
        return -1

    print("Loading existing live panel ...")
    live_panel = pd.read_parquet(live_panel_path)
    print(f"  shape: {live_panel.shape}, dates {live_panel.index.min().date()} → {live_panel.index.max().date()}")

    # Load all inputs (the rolling features need full history)
    print("Loading inputs ...")
    forecasts = load_rule_forecast_panel(panels_dir)
    returns = load_returns_panel(panels_dir)
    diag = load_baseline_diagnostics(baseline_dir)
    base_returns = load_baseline_daily_returns(baseline_dir)
    macro = load_macro_factors(macro_path)
    stable = _load_stablecoin_supply(stable_path)

    today = forecasts.index.max()
    print(f"Today (= forecast panel last date): {today.date()}")

    # Try to load the persisted latest fit
    instruments = sorted(
        set(forecasts.columns.get_level_values("instrument"))
        & set(returns.columns)
        & set(diag["instrument"].unique())
    )
    if args.max_instruments:
        instruments = instruments[: args.max_instruments]
    print(f"Instrument set: {len(instruments)}")

    # Build feature panel ONLY for today (rolling features still use full history)
    print(f"\nBuilding feature panel for today only ({today.date()}) ...")
    today_idx = pd.DatetimeIndex([today])
    bundle = build_feature_panel(
        forecasts=forecasts,
        returns=returns,
        baseline_diagnostics=diag,
        baseline_daily_returns=base_returns,
        macro=macro,
        stablecoin_supply=stable,
        horizon_days=args.horizon,
        instruments=instruments,
        only_dates=today_idx,
    )
    feature_cols = bundle.feature_cols
    print(f"  Today's rows: {len(bundle.df)} (feature_cols={len(feature_cols)})")

    # Try to load the persisted fit. If schema mismatch or missing → full rebuild.
    try:
        model, artifact, persisted_cols = load_latest_fit(
            model_store_dir, expected_feature_cols=feature_cols
        )
        print(f"Loaded persisted fit: refit_date={artifact.refit_date.date()}, "
              f"sigma={artifact.train_pred_iqr:.4f}, is_uninformative={artifact.is_uninformative}")
    except FitNotPersistedError as exc:
        print(f"[FALLBACK] {exc}")
        print("  Falling back to full rebuild.")
        return -1

    # Month-boundary check: does today's expected refit match what's persisted?
    today_expected_refit = pd.Timestamp(today).to_period("M").start_time
    if artifact.refit_date < today_expected_refit:
        print(f"\nMonth boundary: persisted fit is for {artifact.refit_date.date()}, "
              f"today's expected refit is {today_expected_refit.date()}.")
        print("  Training a new fit for this month ...")
        # Build full-history training panel (just for fitting)
        train_bundle = build_feature_panel(
            forecasts=forecasts, returns=returns,
            baseline_diagnostics=diag, baseline_daily_returns=base_returns,
            macro=macro, stablecoin_supply=stable,
            horizon_days=args.horizon, instruments=instruments,
        )
        train_df = train_bundle.df.dropna(subset=[train_bundle.label_col]).copy()
        cutoff_feature_date = today_expected_refit - pd.Timedelta(days=1 + args.horizon)
        train_mask = train_df.index.get_level_values(0) <= cutoff_feature_date
        if train_mask.sum() < args.min_train_rows:
            print(f"  Not enough training rows ({train_mask.sum()} < {args.min_train_rows}). "
                  f"Falling back to full rebuild.")
            return -1
        X_train = train_df.loc[train_mask, feature_cols]
        y_train = train_df.loc[train_mask, train_bundle.label_col]
        model, artifact = _train_one_fit(
            X_train, y_train, today_expected_refit, random_state=args.random_state
        )
        save_fit(model, artifact, feature_cols, model_store_dir)
        print(f"  Saved new fit: best_iter={artifact.best_iteration}, "
              f"sigma={artifact.train_pred_iqr:.4f}, is_uninformative={artifact.is_uninformative}")
    elif artifact.refit_date > today_expected_refit:
        print(f"[FALLBACK] Persisted fit's refit_date ({artifact.refit_date.date()}) is in the FUTURE "
              f"vs today's expected ({today_expected_refit.date()}). Suspicious — full rebuild.")
        return -1
    else:
        # Stale-model defensive log
        age_days = (today - artifact.refit_date).days
        if age_days > 35:
            print(f"WARNING: persisted fit is {age_days} days old (refit_date={artifact.refit_date.date()}). "
                  f"Month-boundary detection should have caught this.")

    # Today's features → today's multiplier
    print(f"\nApplying model to today's features ...")
    X_today = bundle.df[feature_cols]
    # Set index to instrument-only for the predict call
    X_today_by_instr = X_today.copy()
    X_today_by_instr.index = X_today_by_instr.index.get_level_values("__instrument__")
    today_multipliers = predict_today_only(model, artifact, X_today_by_instr)
    print(f"  N={len(today_multipliers)}, mean={today_multipliers.mean():.4f}, "
          f"std={today_multipliers.std():.4f}, "
          f"frac_at_floor={(today_multipliers <= 0.501).mean():.2%}, "
          f"frac_at_ceiling={(today_multipliers >= 1.499).mean():.2%}")

    # Append today's row to the live panel (idempotent: drop any existing today row)
    print(f"\nAppending today's row to live panel ...")
    today_row = today_multipliers.to_frame().T
    today_row.index = pd.DatetimeIndex([today])
    today_row.index.name = live_panel.index.name
    # Reindex to match panel's columns (panel may have instruments not predicted today)
    today_row = today_row.reindex(columns=live_panel.columns)

    # Compose with token-maturity multiplier (live ADOPT 2026-05-19).
    # CRITICAL: compose ONLY today's row — historical rows in live_panel were
    # composed at insertion time by previous incremental runs (or by the most
    # recent full rebuild). Composing the full panel here would multiply
    # historical rows by maturity AGAIN, giving C4 × maturity² — double-counted
    # penalty. To recompose historical rows after a code change, run a full
    # rebuild (drop --incremental).
    if not args.no_maturity_penalty:
        today_row = _apply_maturity_penalty(today_row, returns)

    new_panel = pd.concat([live_panel.loc[live_panel.index < today], today_row]).sort_index()
    new_panel = new_panel[~new_panel.index.duplicated(keep="last")]

    # Atomic write
    tmp_path = live_panel_path.with_suffix(".parquet.tmp")
    new_panel.to_parquet(tmp_path)
    os.replace(tmp_path, live_panel_path)
    print(f"  Wrote {live_panel_path} (shape={new_panel.shape}, last date={new_panel.index.max().date()})")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build C4 XGBoost multiplier panel")
    parser.add_argument("--horizon", type=int, default=5, choices=[5, 20], help="Label horizon (days).")
    parser.add_argument(
        "--panels-dir",
        default="data/forecast_panels_122",
        help="Directory containing forecasts.parquet + returns.parquet.",
    )
    parser.add_argument(
        "--baseline-dir",
        default=DEFAULT_BASELINE_DIR,
        help="Kitchen-sink baseline backtest output dir (contains diagnostics.parquet + daily_returns.csv).",
    )
    parser.add_argument(
        "--macro-data",
        default=None,
        help=("Macro factors parquet (must contain dxy column). "
              "Defaults to envs/dev/data/macro_factors.parquet, falling back to "
              "data/macro_factors.parquet."),
    )
    parser.add_argument(
        "--stablecoin-data",
        default=None,
        help=("Stablecoin supply parquet (optional — feature is NaN if missing). "
              "Defaults to envs/dev/data/stablecoin_supply.parquet, falling back to "
              "data/stablecoin_supply.parquet."),
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output dir. Defaults to out/wf_c4_xgboost_h{N}[_self_replication]/.",
    )
    parser.add_argument(
        "--uniform-multipliers",
        action="store_true",
        help="Self-replication mode: emit a 1.0-everywhere panel of the right shape. "
        "Mandatory plumbing-validity check before any real C4 backtest is interpretable.",
    )
    parser.add_argument(
        "--max-instruments",
        type=int,
        default=None,
        help="Limit instrument list (debug/dev only — production must use full panel).",
    )
    parser.add_argument(
        "--min-train-rows",
        type=int,
        default=5_000,
        help="Skip a refit unless at least this many training rows are available.",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=None,
        help="Override XGB random_state (default: XGB_PARAMS['random_state']=42). "
        "Used by the seed-sensitivity sweep — does NOT mutate module-level constants.",
    )
    parser.add_argument(
        "--freeze-training-after",
        type=str,
        default=None,
        help="YYYY-MM-DD. Truncate the monthly retrain schedule at this date — all "
        "predictions thereafter use the frozen final model. Used by the no-continued-"
        "adaptation stress test.",
    )
    parser.add_argument(
        "--target-transform",
        type=str,
        default=TargetTransform.VOL_NORM_PER_INSTR.value,
        choices=[t.value for t in TargetTransform],
        help="Label transformation. Default `vol_norm_per_instr` reproduces the "
        "original C4 label byte-for-byte. Non-default variants are the "
        "Cakici-Zaremba 2026 cross-sectional target sweep — when set to a "
        "non-default variant, the script REROUTES writes to "
        "`data/research/c4_target_transform/{variant}/` so the live panel and "
        "persisted production fit are not touched. Incompatible with "
        "--incremental and --uniform-multipliers.",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Incremental mode: load the persisted latest fit (data/c4_models/h20/), "
        "predict only today's row, append to the existing multiplier panel. ~5-15s "
        "instead of ~100s. Falls back to full rebuild if no persisted fit, schema "
        "mismatch, or month boundary crossed (in which case it trains a new fit and "
        "persists it). Default off.",
    )
    parser.add_argument(
        "--live-panel-path",
        type=str,
        default="data/c4_multiplier_panel_h20.parquet",
        help="Path to the live-consumed multiplier panel. Read for --incremental, "
        "written by the full-rebuild promotion at the end of run.",
    )
    parser.add_argument(
        "--model-store-dir",
        type=str,
        default="data/c4_models/h20",
        help="Directory where the persisted latest fit (latest.joblib + .meta.json) "
        "lives. Created on full rebuild; loaded by --incremental.",
    )
    parser.add_argument(
        "--no-maturity-penalty",
        action="store_true",
        help="Skip the token-maturity multiplier composition (research mode only — "
        "default-on for the live path per 2026-05-19 user-override ADOPT). "
        "β=0.5 / T=365 are hard-coded constants; use this flag to isolate pure "
        "C4 output for research backtests.",
    )
    args = parser.parse_args()
    freeze_after = (
        pd.Timestamp(args.freeze_training_after)
        if args.freeze_training_after else None
    )
    target_transform = TargetTransform(args.target_transform)
    is_research_variant = target_transform is not TargetTransform.VOL_NORM_PER_INSTR

    if is_research_variant:
        if args.incremental:
            parser.error("--incremental is incompatible with non-default --target-transform")
        if args.uniform_multipliers:
            parser.error("--uniform-multipliers is incompatible with non-default --target-transform")
        # Reroute all writes to a research scratch dir keyed by the variant
        # so live `data/c4_multiplier_panel_h20.parquet` and the persisted
        # production fit are NOT overwritten.
        variant = target_transform.value
        research_root = REPO_ROOT / "data" / "research" / "c4_target_transform" / variant
        # Only override defaults — caller can still pass explicit paths for testing.
        if args.live_panel_path == "data/c4_multiplier_panel_h20.parquet":
            args.live_panel_path = str(research_root / "multiplier_panel.parquet")
        if args.model_store_dir == "data/c4_models/h20":
            args.model_store_dir = str(research_root / "model")
        if args.out_dir is None:
            args.out_dir = str(
                REPO_ROOT / f"out/wf_c4_xgboost_h{args.horizon}_target_{variant}"
            )
        print(f"[target-transform] variant={variant}; writes rerouted under {research_root}")

    panels_dir = REPO_ROOT / args.panels_dir
    baseline_dir = REPO_ROOT / args.baseline_dir

    # env-first/repo-fallback resolution mirrors required_data._resolve_path.
    # Prevents the silent "FileNotFoundError → [3o] WARN-only → panel ages
    # past 30h → backtest fail-closed" cascade observed 2026-05-07 when the
    # auxiliary feeds migrated to envs/dev/data/ but defaults pointed at
    # repo data/ (audit F2-followup, 2026-05-08).
    def _env_first(arg_value: "str | None", filename: str) -> Path:
        if arg_value is not None:
            return REPO_ROOT / arg_value
        env_p = REPO_ROOT / "envs" / "dev" / "data" / filename
        if env_p.exists():
            return env_p
        return REPO_ROOT / "data" / filename

    macro_path = _env_first(args.macro_data, "macro_factors.parquet")
    stable_path = _env_first(args.stablecoin_data, "stablecoin_supply.parquet")
    live_panel_path = (Path(args.live_panel_path) if Path(args.live_panel_path).is_absolute()
                       else REPO_ROOT / args.live_panel_path)
    model_store_dir = (Path(args.model_store_dir) if Path(args.model_store_dir).is_absolute()
                       else REPO_ROOT / args.model_store_dir)

    suffix = "_self_replication" if args.uniform_multipliers else ""
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else REPO_ROOT / f"out/wf_c4_xgboost_h{args.horizon}{suffix}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------- Incremental mode: try fast path, fall through on failure ----------
    if args.incremental and not args.uniform_multipliers:
        rc = _run_incremental(
            args, panels_dir, baseline_dir, macro_path, stable_path,
            live_panel_path, model_store_dir,
        )
        if rc == 0:
            return 0
        # rc == -1 → fall through to full rebuild
        print("\n--- Falling through to full rebuild ---\n")

    print(f"=== build_c4_multiplier_panel ===")
    print(f"Horizon:      {args.horizon}d")
    print(f"Mode:         {'UNIFORM (self-replication)' if args.uniform_multipliers else 'XGBoost'}")
    print(f"Target:       {target_transform.value}")
    print(f"Panels dir:   {panels_dir}")
    print(f"Baseline dir: {baseline_dir}")
    print(f"Macro data:   {macro_path}")
    print(f"Stablecoin:   {stable_path} (optional)")
    print(f"Output dir:   {out_dir}")
    print(f"Live panel:   {live_panel_path}")
    print(f"Model store:  {model_store_dir}")
    print()

    t0 = time.time()

    # ---------- Inputs ----------
    print("Loading inputs ...")
    forecasts = load_rule_forecast_panel(panels_dir)
    returns = load_returns_panel(panels_dir)
    diag = load_baseline_diagnostics(baseline_dir)
    base_returns = load_baseline_daily_returns(baseline_dir)
    macro = load_macro_factors(macro_path)
    stable = _load_stablecoin_supply(stable_path)

    print(f"  forecasts: {forecasts.shape}, "
          f"{len(set(forecasts.columns.get_level_values('rule')))} rules, "
          f"{len(set(forecasts.columns.get_level_values('instrument')))} instruments")
    print(f"  returns:   {returns.shape}")
    print(f"  diag:      {diag.shape}")
    print(f"  base_ret:  {base_returns.shape}")
    print(f"  macro:     {macro.shape} cols={list(macro.columns)}")
    print(f"  stable:    {'loaded' if stable is not None else 'NOT FOUND (feature will be NaN)'}")

    # Instrument set: intersection of forecast panel and returns panel
    instruments = sorted(
        set(forecasts.columns.get_level_values("instrument"))
        & set(returns.columns)
        & set(diag["instrument"].unique())
    )
    if args.max_instruments:
        instruments = instruments[: args.max_instruments]
        print(f"  DEBUG: limited to {len(instruments)} instruments")
    print(f"Working instrument set: {len(instruments)}")

    # ---------- Feature panel ----------
    print(f"\nBuilding feature panel for horizon={args.horizon} ...")
    if is_research_variant:
        print(f"  target_transform: {target_transform.value} (cross-sectional)")
    bundle = build_feature_panel(
        forecasts=forecasts,
        returns=returns,
        baseline_diagnostics=diag,
        baseline_daily_returns=base_returns,
        macro=macro,
        stablecoin_supply=stable,
        horizon_days=args.horizon,
        instruments=instruments,
        target_transform=target_transform,
    )
    df_after_label = bundle.df.dropna(subset=[bundle.label_col])
    print(f"  Total rows: {len(bundle.df):,}; after label dropna: {len(df_after_label):,}")
    print(f"  Feature counts: rule={len(bundle.rule_feature_cols)}, "
          f"agg={len(bundle.aggregate_feature_cols)}, "
          f"instr={len(bundle.instrument_feature_cols)}, "
          f"port={len(bundle.portfolio_feature_cols)}")

    # ---------- Walk-forward fit/predict ----------
    if args.uniform_multipliers:
        print("\nUNIFORM mode — skipping XGBoost fit. Emitting 1.0-everywhere panel.")
        # Build a shell predictions Series so we can reuse the unstack pattern.
        idx = df_after_label.index
        oos_preds = pd.Series(0.0, index=idx, name="y_hat")  # raw y_hat=0 → multiplier=1.0
        # Force a single fake artifact with sigma=1 so the squash maps 0 -> 1.0
        from systems.crypto_perps.c4_xgboost_combiner import FitArtifact
        artifacts = [FitArtifact(
            refit_date=df_after_label.index.get_level_values(0).min(),
            n_train_rows=0,
            n_val_rows=0,
            best_iteration=0,
            best_val_rmse=float("nan"),
            feature_importance={},
            train_pred_iqr=1.0,
        )]
    else:
        print(f"\nWalk-forward fit/predict (monthly retrain) ...")
        if args.random_state is not None:
            print(f"  random_state override: {args.random_state}")
        if freeze_after is not None:
            print(f"  freeze_training_after: {freeze_after.date()}")
        oos_preds, artifacts = fit_predict_walk_forward(
            bundle,
            horizon_days=args.horizon,
            min_train_rows=args.min_train_rows,
            random_state=args.random_state,
            freeze_training_after=freeze_after,
        )
        print(f"  Refits: {len(artifacts)}; OOS predictions: {len(oos_preds):,}")

    # ---------- Multiplier panel ----------
    print(f"\nSquashing predictions -> multipliers ...")
    panel = predictions_to_multiplier_panel(oos_preds, artifacts)
    if args.uniform_multipliers:
        # Defensive: tanh(0)=0 → multiplier=1.0 already, but be explicit.
        panel = uniform_multiplier_panel(panel)
    print(f"  Panel shape: {panel.shape}")
    panel_stats = multiplier_distribution_stats(panel)
    print(f"  Mean multiplier: {panel_stats.get('mean', float('nan')):.4f}; "
          f"frac at floor: {panel_stats.get('frac_at_floor', 0):.2%}; "
          f"frac at ceiling: {panel_stats.get('frac_at_ceiling', 0):.2%}")

    # ---------- Persist ----------
    panel_path = out_dir / "multiplier_panel.parquet"
    panel.to_parquet(panel_path)
    print(f"\nWrote {panel_path}")

    if not args.uniform_multipliers:
        oos_path = out_dir / "oos_predictions.parquet"
        oos_preds.to_frame("y_hat").to_parquet(oos_path)
        print(f"Wrote {oos_path}")

        fi = aggregate_feature_importance(artifacts)
        fi_path = out_dir / "feature_importance.parquet"
        fi.to_parquet(fi_path)
        print(f"Wrote {fi_path}")
        fi_top = fi.head(20)
    else:
        fi_top = pd.DataFrame(columns=["mean_gain", "std_gain", "n_fits"])

    elapsed = time.time() - t0
    _write_training_report(
        out_dir=out_dir,
        horizon_days=args.horizon,
        bundle_n_rows=len(df_after_label),
        n_instruments=len(instruments),
        artifacts=artifacts,
        panel_stats=panel_stats,
        fi_top=fi_top,
        uniform_override=args.uniform_multipliers,
        elapsed_s=elapsed,
    )
    print(f"Wrote {out_dir / 'training_report.md'}")

    # Save the most-recent fit to the model store so future --incremental
    # runs can use it. Skip for uniform mode (fake artifact, not a real model).
    if not args.uniform_multipliers and artifacts:
        # The most-recent (last in the list) artifact corresponds to the
        # final monthly model. We need to retrain it briefly to grab the
        # actual model object — fit_predict_walk_forward currently doesn't
        # return models alongside artifacts. For now, retrain just the last
        # month's fit on the same training data and save that.
        print(f"\nRe-fitting latest month and persisting to {model_store_dir}/ ...")
        latest_artifact = artifacts[-1]
        cutoff_fd = latest_artifact.refit_date - pd.Timedelta(days=1 + args.horizon)
        train_df = bundle.df.dropna(subset=[bundle.label_col]).copy()
        mask = train_df.index.get_level_values(0) <= cutoff_fd
        X_tr = train_df.loc[mask, bundle.feature_cols]
        y_tr = train_df.loc[mask, bundle.label_col]
        latest_model, latest_artifact_refit = _train_one_fit(
            X_tr, y_tr, latest_artifact.refit_date, random_state=args.random_state
        )
        save_fit(latest_model, latest_artifact_refit, bundle.feature_cols, model_store_dir)
        print(f"  Persisted {model_store_dir}/latest.joblib (refit_date={latest_artifact_refit.refit_date.date()})")

    # Promote built panel to the live path (atomic).
    # Composes with the token-maturity multiplier (live ADOPT 2026-05-19) so the
    # live consumer sees a single combined panel — no second config key needed.
    if not args.uniform_multipliers:
        built_panel = out_dir / "multiplier_panel.parquet"
        if built_panel.exists() and built_panel != live_panel_path:
            composed = pd.read_parquet(built_panel)
            if not args.no_maturity_penalty:
                composed = _apply_maturity_penalty(composed, returns)
            tmp = live_panel_path.with_suffix(".parquet.tmp")
            live_panel_path.parent.mkdir(parents=True, exist_ok=True)
            composed.to_parquet(tmp)
            os.replace(tmp, live_panel_path)
            print(f"Promoted to live: {live_panel_path}")

    print(f"\nDone in {elapsed:.0f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
