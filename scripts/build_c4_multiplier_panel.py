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
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from systems.crypto_perps.c4_xgboost_combiner import (  # noqa: E402
    XGB_PARAMS,
    EARLY_STOPPING_ROUNDS,
    aggregate_feature_importance,
    build_feature_panel,
    fit_predict_walk_forward,
    load_baseline_daily_returns,
    load_baseline_diagnostics,
    load_macro_factors,
    load_returns_panel,
    load_rule_forecast_panel,
    multiplier_distribution_stats,
    predictions_to_multiplier_panel,
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
        default="data/macro_factors.parquet",
        help="Macro factors parquet (must contain dxy column).",
    )
    parser.add_argument(
        "--stablecoin-data",
        default="data/stablecoin_supply.parquet",
        help="Stablecoin supply parquet (optional — feature is NaN if missing).",
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
    args = parser.parse_args()
    freeze_after = (
        pd.Timestamp(args.freeze_training_after)
        if args.freeze_training_after else None
    )

    panels_dir = REPO_ROOT / args.panels_dir
    baseline_dir = REPO_ROOT / args.baseline_dir
    macro_path = REPO_ROOT / args.macro_data
    stable_path = REPO_ROOT / args.stablecoin_data

    suffix = "_self_replication" if args.uniform_multipliers else ""
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else REPO_ROOT / f"out/wf_c4_xgboost_h{args.horizon}{suffix}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== build_c4_multiplier_panel ===")
    print(f"Horizon:      {args.horizon}d")
    print(f"Mode:         {'UNIFORM (self-replication)' if args.uniform_multipliers else 'XGBoost'}")
    print(f"Panels dir:   {panels_dir}")
    print(f"Baseline dir: {baseline_dir}")
    print(f"Macro data:   {macro_path}")
    print(f"Stablecoin:   {stable_path} (optional)")
    print(f"Output dir:   {out_dir}")
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
    bundle = build_feature_panel(
        forecasts=forecasts,
        returns=returns,
        baseline_diagnostics=diag,
        baseline_daily_returns=base_returns,
        macro=macro,
        stablecoin_supply=stable,
        horizon_days=args.horizon,
        instruments=instruments,
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
    print(f"\nDone in {elapsed:.0f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
