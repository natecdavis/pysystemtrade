#!/usr/bin/env python3
"""
Comprehensive vol-gap diagnostic for the crypto perps backtest.

Diagnoses why realized portfolio vol (~10%) undershoots the 25% target, across
10 structural causes, and produces a VOL GAP ATTRIBUTION TABLE.

Usage:
    # Single backtest (research 6yr)
    python scripts/diagnose_vol_targeting.py \
        --backtest-dir out/fee_fix_1k \
        --data data/dataset_538registry_6yr_jagged.parquet

    # Side-by-side: research (6yr) vs advisory (4yr)
    python scripts/diagnose_vol_targeting.py \
        --backtest-dir out/vol_diag_1k \
        --data data/dataset_538registry_6yr_jagged.parquet \
        --compare out/paper_20260328/backtest_latest \
        --compare-data out/paper_20260328/dataset_latest.parquet

    # --quick: skip slow IDM recomputation if already printed
    python scripts/diagnose_vol_targeting.py --backtest-dir out/vol_diag_1k --quick
"""

import argparse
import sys
from pathlib import Path
import numpy as np
import pandas as pd

# IDM computation parameters (must match diagnose_idm.py)
IDM_CAP = 2.5
CORR_SPAN = 60
CORR_MIN_PERIODS = 20


# ---------------------------------------------------------------------------
# Helper: load diagnostics + prices
# ---------------------------------------------------------------------------

def load_backtest(backtest_dir: Path, data_path: Path):
    """Load diagnostics.parquet + price panel from dataset parquet."""
    diag_path = backtest_dir / "diagnostics.parquet"
    pnl_path = backtest_dir / "pnl.parquet"

    if not diag_path.exists():
        print(f"ERROR: {diag_path} not found. Run backtest first.", file=sys.stderr)
        sys.exit(1)

    diag = pd.read_parquet(diag_path)
    diag["date"] = pd.to_datetime(diag["date"])

    # Load pnl for realized vol
    pnl = None
    if pnl_path.exists():
        pnl = pd.read_parquet(pnl_path)
        pnl.index = pd.to_datetime(pnl.index)
    else:
        # Fall back to daily_returns.csv (output of run_dynamic_universe_backtest.py)
        returns_csv = backtest_dir / "daily_returns.csv"
        if returns_csv.exists():
            pnl = pd.read_csv(returns_csv, index_col="date", parse_dates=True)["net_return"]

    # Load prices from dataset
    prices = None
    if data_path and data_path.exists():
        raw = pd.read_parquet(data_path)
        if "instrument" in raw.columns and "close" in raw.columns:
            # Long format
            raw["date"] = pd.to_datetime(raw["date"])
            prices = raw.pivot_table(index="date", columns="instrument", values="close")
        elif isinstance(raw.index, pd.MultiIndex):
            prices = raw["close"].unstack("instrument")
        else:
            # Wide format: expect date index, instrument columns
            prices = raw
        prices.index = pd.to_datetime(prices.index)

    return diag, pnl, prices


def pnl_to_realized_vol(pnl: pd.Series) -> float:
    """Annualized realized vol from daily returns series (fractional)."""
    return pnl.std() * np.sqrt(252)


def equity_from_pnl(pnl_df) -> pd.Series:
    """Extract daily returns series from pnl/returns input."""
    if pnl_df is None:
        return None
    if isinstance(pnl_df, pd.Series):
        return pnl_df
    if "net_return" in pnl_df.columns:
        return pnl_df["net_return"]
    if "pnl" in pnl_df.columns:
        return pnl_df["pnl"]
    if "equity" in pnl_df.columns:
        eq = pnl_df["equity"]
        return eq.pct_change().fillna(0)
    return pnl_df.iloc[:, 0]


# ---------------------------------------------------------------------------
# Section 0 — Structural vol gap formula
# ---------------------------------------------------------------------------

def section0_structural(diag: pd.DataFrame, label: str,
                        target_vol: float = 0.25,
                        mean_weight: float = None,
                        idm_mean: float = None,
                        forecast_mean_abs: float = None,
                        fdm_mean: float = None):
    print(f"\n{'='*70}")
    print(f"SECTION 0: Structural Vol Gap Formula  [{label}]")
    print(f"{'='*70}")
    print("Position sizing formula:")
    print("  pos_notional = capital × IDM × weight × target_vol × (forecast/10) / inst_vol")
    print()
    print("Implied portfolio vol = target_vol × IDM × avg_weight × avg|forecast|/10 × FDM")
    print("  × (1 - truncation_frac) × (1 - netting_frac)")
    print()

    # Get weight from diagnostics
    active_all = diag[diag["instrument_weight"] > 0]
    if mean_weight is None:
        mean_weight = active_all["instrument_weight"].mean() if len(active_all) else np.nan
    mean_n = active_all.groupby("date")["instrument"].count().mean() if len(active_all) else np.nan

    print(f"  target_vol        = {target_vol:.1%}")
    print(f"  avg_weight        = {mean_weight:.4f}  (= 1/N, N≈{mean_n:.0f})")
    print()
    print("  KEY IDENTITY: IDM × avg_weight × corr_factor = 1  (by IDM calibration design)")
    print("  → portfolio_vol ≈ target_vol × avg|fc|/10  (when IDM is uncapped & calibrated)")
    print("  → Remaining gap from: forecast calibration shortfall, truncation, netting")
    print()
    print("  (IDM cap at 2.5 can break this identity for low-correlation universes,")
    print("   but for ρ≈0.5-0.7, N=30, theoretical IDM ≈ 1.2-1.8 — cap rarely binds)")
    if forecast_mean_abs is not None:
        print()
        structural_vol = target_vol * forecast_mean_abs / 10.0
        print(f"  Structural vol (before truncation/netting) ≈ {target_vol:.1%} × {forecast_mean_abs:.2f}/10 = {structural_vol:.1%}")
    print()
    print("  (Full attribution in Section 10 after all causes measured)")


# ---------------------------------------------------------------------------
# Section 1 — IDM time series
# ---------------------------------------------------------------------------

def section1_idm(diag: pd.DataFrame, prices: pd.DataFrame, label: str, quick: bool = False):
    print(f"\n{'='*70}")
    print(f"SECTION 1: IDM Time Series  [{label}]")
    print(f"{'='*70}")

    if quick:
        print("  [--quick mode: skipping IDM computation. Pass without --quick for full IDM analysis.]")
        return None

    if prices is None:
        print("  [No prices provided; skipping IDM computation]")
        return None

    # Import the reusable function
    sys.path.insert(0, str(Path(__file__).parent))
    from diagnose_idm import compute_idm_from_diagnostics

    # Write temp diag path if needed — use a temp file trick for in-memory
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        tmp_diag = f.name
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        tmp_prices = f.name

    try:
        diag.to_parquet(tmp_diag)
        prices.to_parquet(tmp_prices)
        idm_df = compute_idm_from_diagnostics(Path(tmp_diag), Path(tmp_prices))
    finally:
        os.unlink(tmp_diag)
        os.unlink(tmp_prices)

    idm = idm_df["idm"]
    n_active = idm_df["n_active_instruments"]
    print(f"  Period: {idm_df['date'].min().date()} → {idm_df['date'].max().date()}  ({len(idm_df)} days)")
    print(f"  Mean IDM:    {idm.mean():.3f}")
    print(f"  Median IDM:  {idm.median():.3f}")
    print(f"  Min IDM:     {idm.min():.3f}")
    print(f"  Max IDM:     {idm.max():.3f}")
    print(f"  Mean N:      {n_active.mean():.1f}")

    idm_df_copy = idm_df.copy()
    idm_df_copy["year"] = pd.to_datetime(idm_df_copy["date"]).dt.year
    annual = idm_df_copy.groupby("year").agg(idm_mean=("idm", "mean"), n_mean=("n_active_instruments", "mean"))
    print(f"\n  {'Year':>6}  {'Mean IDM':>9}  {'Mean N':>7}")
    for year, row in annual.iterrows():
        print(f"  {year:>6}  {row['idm_mean']:>9.3f}  {row['n_mean']:>7.1f}")

    mean_idm = idm.mean()
    mean_n = n_active.mean()
    implied_rho = (mean_n / mean_idm**2 - 1) / (mean_n - 1) if mean_n > 1 else np.nan
    print(f"\n  Implied pairwise correlation: {implied_rho:.3f}")
    print(f"  Theoretical IDM at rho={implied_rho:.2f}, N={mean_n:.0f}: "
          f"{np.sqrt(mean_n / (1 + (mean_n - 1) * implied_rho)):.3f}")

    return idm_df


# ---------------------------------------------------------------------------
# Section 2 — Average |combined_forecast| vs assumed 10.0
# ---------------------------------------------------------------------------

def section2_forecast_mean(diag: pd.DataFrame, label: str):
    print(f"\n{'='*70}")
    print(f"SECTION 2: Average |combined_forecast| vs Assumed 10.0  [{label}]")
    print(f"{'='*70}")

    fc = diag["combined_forecast"].dropna()
    abs_fc = fc.abs()

    mean_abs = abs_fc.mean()
    median_abs = abs_fc.median()
    pct5 = abs_fc.quantile(0.05)
    pct95 = abs_fc.quantile(0.95)
    pct_zero = (fc.abs() < 0.5).mean()

    print(f"  Mean |forecast|:   {mean_abs:.3f}  (assumed 10.0 → factor {mean_abs/10:.3f})")
    print(f"  Median |forecast|: {median_abs:.3f}")
    print(f"  5th pct:           {pct5:.3f}")
    print(f"  95th pct:          {pct95:.3f}")
    print(f"  % near-zero (<0.5): {pct_zero:.1%}")

    # By year
    diag_copy = diag.copy()
    diag_copy["year"] = diag_copy["date"].dt.year
    annual = diag_copy.groupby("year")["combined_forecast"].apply(lambda x: x.abs().mean())
    print(f"\n  By year:")
    for year, val in annual.items():
        bar = "↑" if val >= 10 else "↓"
        print(f"    {year}: {val:.3f}  {bar}")

    if mean_abs < 9.0:
        print(f"\n  WARNING: mean|forecast|={mean_abs:.2f} < 9.0 — walk-forward scalar calibration undershooting")
    elif mean_abs > 11.0:
        print(f"\n  NOTE: mean|forecast|={mean_abs:.2f} > 11 — scalars slightly overshooting (minor)")
    else:
        print(f"\n  OK: mean|forecast|={mean_abs:.2f} — close to target 10.0")

    return mean_abs


# ---------------------------------------------------------------------------
# Section 3 — FDM analysis
# ---------------------------------------------------------------------------

def section3_fdm(diag: pd.DataFrame, label: str):
    print(f"\n{'='*70}")
    print(f"SECTION 3: FDM (Forecast Diversification Multiplier)  [{label}]")
    print(f"{'='*70}")

    if "fdm" not in diag.columns or diag["fdm"].isna().all():
        print("  [fdm column not present in diagnostics — re-run backtest with patched runner]")
        return None

    fdm = diag["fdm"].dropna()
    print(f"  Mean FDM:    {fdm.mean():.3f}")
    print(f"  Median FDM:  {fdm.median():.3f}")
    print(f"  Min FDM:     {fdm.min():.3f}")
    print(f"  Max FDM:     {fdm.max():.3f}")

    diag_copy = diag.copy()
    diag_copy["year"] = diag_copy["date"].dt.year
    annual = diag_copy.groupby("year")["fdm"].mean()
    print(f"\n  By year:")
    for year, val in annual.items():
        print(f"    {year}: {val:.3f}")

    # Per-instrument mean FDM
    per_inst = diag_copy.groupby("instrument")["fdm"].mean().sort_values()
    print(f"\n  Per-instrument FDM range: {per_inst.min():.3f} – {per_inst.max():.3f}")
    print(f"  Instruments with FDM < 1.0: {(per_inst < 1.0).sum()}")

    mean_fdm = fdm.mean()
    if mean_fdm < 1.0:
        print(f"\n  WARNING: mean FDM={mean_fdm:.3f} < 1.0 — unexpected for 35-rule stack, investigate")
    else:
        print(f"\n  OK: mean FDM={mean_fdm:.3f} ≥ 1.0 (FDM boosts positions, not a gap cause)")

    return mean_fdm


# ---------------------------------------------------------------------------
# Section 4 — Position truncation from min_notional
# ---------------------------------------------------------------------------

def section4_truncation(diag: pd.DataFrame, prices: pd.DataFrame, label: str,
                        min_notional: float = 1.0, capital: float = 1000.0,
                        target_vol: float = 0.25):
    print(f"\n{'='*70}")
    print(f"SECTION 4: Position Truncation from min_notional=${min_notional:.0f}  [{label}]")
    print(f"{'='*70}")
    print(f"  NOTE: With lot_size_notional_override=1.0, position values ARE USD notional directly.")
    print(f"  min_notional_position={min_notional} → positions < ${min_notional} are truncated to 0.")

    diag_copy = diag.copy()
    active = diag_copy[diag_copy["instrument_weight"] > 0].copy()

    # Position IS in USD notional (lot_size_notional_override=1.0 → 1 lot = 1 USD)
    # Truncated = |position| < min_notional  (effectively zero after rounding)
    truncated = active[active["position"].abs() < min_notional]
    total_obs = len(active)
    trunc_frac = len(truncated) / total_obs if total_obs > 0 else 0.0

    non_trunc = active[active["position"].abs() >= min_notional]
    mean_notional = active["position"].abs().mean()
    mean_non_trunc = non_trunc["position"].abs().mean() if len(non_trunc) else 0.0
    median_non_trunc = non_trunc["position"].abs().median() if len(non_trunc) else 0.0

    print(f"\n  Total active instrument-days: {total_obs:,}")
    print(f"  Positions below ${min_notional} (truncated): {len(truncated):,}  ({trunc_frac:.1%})")
    print(f"  Mean |position| (all active):   ${mean_notional:.2f}")
    print(f"  Mean |position| (non-trunc):    ${mean_non_trunc:.2f}")
    print(f"  Median |position| (non-trunc):  ${median_non_trunc:.2f}")

    # Notional lost to truncation: fraction of theoretical gross notional that's zeroed out
    # Approximate: positions that ARE above threshold contribute mean_non_trunc
    # Positions below contribute ~0 (truncated)
    # Lost notional fraction ≈ trunc_frac (simplified, assuming equal distribution)
    print(f"\n  Estimated gross notional lost to truncation: ~{trunc_frac:.1%}")
    print(f"  (This is the primary capital constraint at $1K — many theoretical positions are < $1)")

    # Distribution of truncated position sizes (to understand how much was lost)
    if len(truncated) > 0:
        trunc_sizes = truncated["position"].abs()
        print(f"\n  Truncated position size distribution:")
        print(f"    Median: ${trunc_sizes.median():.3f}")
        print(f"    75th pct: ${trunc_sizes.quantile(0.75):.3f}")
        print(f"    Max: ${trunc_sizes.max():.3f}")

    return trunc_frac


# ---------------------------------------------------------------------------
# Section 5 — Buffering inertia
# ---------------------------------------------------------------------------

def section5_buffering(diag: pd.DataFrame, label: str, entry_buffer: float = 3.0, exit_buffer: float = 15.0):
    print(f"\n{'='*70}")
    print(f"SECTION 5: Buffering Inertia (entry={entry_buffer}, exit={exit_buffer})  [{label}]")
    print(f"{'='*70}")

    # We can't directly compare desired vs actual without desired positions
    # But we can measure how often position == 0 despite positive weight+forecast (signal lost)
    # and how often position stays flat when forecast changes significantly (inertia)

    diag_copy = diag.copy()
    active = diag_copy[diag_copy["instrument_weight"] > 0].copy()

    # Days where forecast is non-trivial (|fc| > entry_buffer threshold) but position is zero
    has_signal = active["combined_forecast"].abs() > entry_buffer
    no_position = active["position"].abs() < 0.01

    signal_not_taken = (has_signal & no_position).sum()
    signal_total = has_signal.sum()
    signal_not_taken_frac = signal_not_taken / signal_total if signal_total > 0 else 0

    print(f"  Instrument-days with |forecast| > {entry_buffer} (entry threshold): {signal_total:,}")
    print(f"  Of those, position = 0: {signal_not_taken:,}  ({signal_not_taken_frac:.1%})")

    # Day-over-day position change vs forecast change (inertia measure)
    active_pivot = active.pivot_table(index="date", columns="instrument", values="position")
    fc_pivot = active.pivot_table(index="date", columns="instrument", values="combined_forecast")

    pos_changes = active_pivot.diff().abs()
    fc_changes = fc_pivot.diff().abs()

    # Fraction of large forecast changes (>exit_buffer) not accompanied by position change
    large_fc_change = fc_changes > exit_buffer
    no_pos_change = pos_changes < 0.01

    inertia_events = (large_fc_change & no_pos_change).sum().sum()
    large_fc_total = large_fc_change.sum().sum()
    inertia_frac = inertia_events / large_fc_total if large_fc_total > 0 else 0

    print(f"\n  Instrument-days with |Δforecast| > {exit_buffer} (exit threshold): {large_fc_total:,}")
    print(f"  Of those, position unchanged: {inertia_events:,}  ({inertia_frac:.1%})")
    print(f"\n  Buffering inertia estimated vol reduction: ~{(signal_not_taken_frac + inertia_frac)/2:.1%}")

    return (signal_not_taken_frac + inertia_frac) / 2


# ---------------------------------------------------------------------------
# Section 6 — Long/short netting
# ---------------------------------------------------------------------------

def section6_netting(diag: pd.DataFrame, prices: pd.DataFrame, label: str):
    print(f"\n{'='*70}")
    print(f"SECTION 6: Long/Short Netting  [{label}]")
    print(f"{'='*70}")

    if prices is None:
        print("  [No prices — computing netting from position units only]")
        # Use position directly
        pos_pivot = diag.pivot_table(index="date", columns="instrument", values="position").fillna(0)
        gross = pos_pivot.abs().sum(axis=1)
        net = pos_pivot.sum(axis=1).abs()
    else:
        # Convert to notional using prices
        instruments = diag["instrument"].unique()
        avail = [i for i in instruments if i in prices.columns]

        if not avail:
            print("  [Instrument names don't match price columns — using position units]")
            pos_pivot = diag.pivot_table(index="date", columns="instrument", values="position").fillna(0)
            gross = pos_pivot.abs().sum(axis=1)
            net = pos_pivot.sum(axis=1).abs()
        else:
            price_long = prices[avail].stack().reset_index()
            price_long.columns = ["date", "instrument", "price"]
            price_long["date"] = pd.to_datetime(price_long["date"])
            merged = diag.merge(price_long, on=["date", "instrument"], how="left")
            merged["notional"] = merged["position"] * merged["price"].fillna(1.0)

            notional_pivot = merged.pivot_table(index="date", columns="instrument", values="notional").fillna(0)
            gross = notional_pivot.abs().sum(axis=1)
            net = notional_pivot.sum(axis=1).abs()

    gross_mean = gross.mean()
    net_mean = net.mean()
    netting_ratio = gross_mean / net_mean if net_mean > 0 else np.nan
    netting_loss_frac = 1 - (net_mean / gross_mean) if gross_mean > 0 else 0

    print(f"  Mean gross exposure: {gross_mean:.1f} (units/USD)")
    print(f"  Mean net exposure:   {net_mean:.1f} (units/USD)")
    print(f"  Gross/net ratio:     {netting_ratio:.2f}x")
    print(f"  Netting loss:        {netting_loss_frac:.1%}  (long and short positions partially cancel)")

    # By year
    dates = gross.index
    years = pd.to_datetime(dates).year
    print(f"\n  By year:")
    for yr in sorted(set(years)):
        mask = years == yr
        gr = gross[mask].mean()
        ne = net[mask].mean()
        ratio = gr / ne if ne > 0 else np.nan
        print(f"    {yr}: gross={gr:.1f}  net={ne:.1f}  ratio={ratio:.2f}")

    return netting_loss_frac


# ---------------------------------------------------------------------------
# Section 7 — Forecast distribution
# ---------------------------------------------------------------------------

def section7_forecast_dist(diag: pd.DataFrame, label: str):
    print(f"\n{'='*70}")
    print(f"SECTION 7: Forecast Distribution  [{label}]")
    print(f"{'='*70}")

    fc = diag["combined_forecast"].dropna()

    pcts = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    print(f"  {'Percentile':>11}  {'Forecast':>10}")
    for p in pcts:
        print(f"  {p:>10}th  {np.percentile(fc, p):>10.2f}")

    # Kurtosis (excess)
    kurt = fc.kurtosis()
    skew = fc.skew()
    print(f"\n  Skewness (excess):  {skew:.3f}")
    print(f"  Kurtosis (excess):  {kurt:.3f}  (normal=0; heavy tails → higher)")

    # Fraction of time forecast is capped at ±20
    capped_frac = (fc.abs() >= 19.5).mean()
    print(f"  Fraction at cap (|fc|≥19.5): {capped_frac:.1%}")

    # Mean of top decile vs overall mean
    top_decile = fc[fc.abs() >= fc.abs().quantile(0.9)].abs().mean()
    print(f"\n  Top-decile |forecast| mean:    {top_decile:.2f}  (expected ≈ 15-20 if normally capped)")
    print(f"  Overall mean |forecast|:       {fc.abs().mean():.2f}")

    if kurt > 3:
        print(f"\n  Heavy tails (kurtosis={kurt:.1f}) — many near-zero forecasts suppressing mean")
    elif kurt < 0:
        print(f"\n  Light tails (kurtosis={kurt:.1f}) — forecast distribution relatively flat")
    else:
        print(f"\n  Normal-ish tails (kurtosis={kurt:.1f})")


# ---------------------------------------------------------------------------
# Section 8 — Walk-forward warm-up
# ---------------------------------------------------------------------------

def section8_warmup(diag: pd.DataFrame, pnl, label: str):
    print(f"\n{'='*70}")
    print(f"SECTION 8: Walk-forward Warm-up (Early Years)  [{label}]")
    print(f"{'='*70}")

    diag_copy = diag.copy()
    diag_copy["year"] = diag_copy["date"].dt.year

    # Mean |forecast| by year (already shown in Section 2 but repeat contextually)
    fc_by_year = diag_copy.groupby("year")["combined_forecast"].apply(lambda x: x.abs().mean())

    # Number of active instruments by year
    n_by_year = diag_copy[diag_copy["instrument_weight"] > 0].groupby("year")["instrument"].nunique()

    print(f"  {'Year':>6}  {'Mean|fc|':>9}  {'N instr':>8}")
    for yr in sorted(fc_by_year.index):
        n = n_by_year.get(yr, 0)
        print(f"  {yr:>6}  {fc_by_year[yr]:>9.3f}  {n:>8}")

    # Realized vol by year from pnl if available
    if pnl is not None:
        daily_ret = equity_from_pnl(pnl)
        if daily_ret is not None:
            daily_ret = daily_ret[daily_ret != 0.0]
            ret_by_year = daily_ret.groupby(daily_ret.index.year).std() * np.sqrt(252)
            print(f"\n  Realized vol by year:")
            for yr, v in ret_by_year.items():
                print(f"    {yr}: {v:.1%}")
        else:
            print("\n  (No returns series available for annual vol breakdown)")

    # Early vs mature comparison
    all_years = sorted(fc_by_year.index)
    if len(all_years) >= 4:
        early = all_years[:2]
        mature = all_years[2:]
        early_fc = fc_by_year[early].mean()
        mature_fc = fc_by_year[mature].mean()
        print(f"\n  Early years ({early[0]}-{early[-1]}) mean|fc|: {early_fc:.3f}")
        print(f"  Mature years ({mature[0]}-{mature[-1]}) mean|fc|: {mature_fc:.3f}")
        if early_fc < mature_fc * 0.85:
            print(f"  → Walk-forward warm-up drag detected: early scalars {(1-early_fc/mature_fc):.0%} below mature")
        else:
            print(f"  → Minimal warm-up drag (early/mature ratio={early_fc/mature_fc:.2f})")


# ---------------------------------------------------------------------------
# Section 9 — Vol estimator lag
# ---------------------------------------------------------------------------

def section9_vol_lag(diag: pd.DataFrame, prices: pd.DataFrame, label: str, vol_days: int = 63):
    print(f"\n{'='*70}")
    print(f"SECTION 9: Vol Estimator Lag (EWMA-{vol_days} vs Realized)  [{label}]")
    print(f"{'='*70}")

    if prices is None:
        print("  [No prices — skipping vol estimator analysis]")
        return None

    instruments = diag["instrument"].unique()
    avail = [i for i in instruments if i in prices.columns][:20]  # sample 20 for speed

    if not avail:
        print("  [Instrument names don't match price columns — skipping]")
        return None

    ratios = []
    for inst in avail:
        px = prices[inst].dropna()
        if len(px) < vol_days * 2:
            continue
        log_ret = np.log(px / px.shift(1)).dropna()

        # EWMA vol (pysystemtrade uses robust_vol_calc with EWMA span=vol_days)
        ewma_var = log_ret.ewm(span=vol_days, min_periods=vol_days // 2).var()
        ewma_vol = (ewma_var ** 0.5) * np.sqrt(252)

        # Realized vol: rolling std over same window
        real_vol = log_ret.rolling(vol_days, min_periods=vol_days // 2).std() * np.sqrt(252)

        # Ratio: EWMA / Realized (if EWMA > realized, positions are undersized)
        ratio = ewma_vol / real_vol.replace(0, np.nan)
        ratios.append(ratio.dropna())

    if not ratios:
        print("  [Insufficient data for vol lag analysis]")
        return None

    all_ratios = pd.concat(ratios)
    mean_ratio = all_ratios.mean()
    median_ratio = all_ratios.median()

    print(f"  EWMA-{vol_days} vol / Realized vol:")
    print(f"    Mean ratio:   {mean_ratio:.3f}")
    print(f"    Median ratio: {median_ratio:.3f}")
    print(f"    5th pct:      {all_ratios.quantile(0.05):.3f}")
    print(f"    95th pct:     {all_ratios.quantile(0.95):.3f}")
    print()

    if mean_ratio > 1.05:
        print(f"  EWMA vol overshoots realized by {(mean_ratio-1):.1%} on average")
        print(f"  → Positions undersized by ~{(mean_ratio-1):.1%} (EWMA denominator too large)")
    elif mean_ratio < 0.95:
        print(f"  EWMA vol undershoots realized by {(1-mean_ratio):.1%} on average")
        print(f"  → Positions oversized by ~{(1-mean_ratio):.1%} (EWMA denominator too small)")
    else:
        print(f"  EWMA/Realized ratio ≈ 1.0 — vol estimator lag is not a significant gap cause")

    return mean_ratio


# ---------------------------------------------------------------------------
# Section 10 — Capital multiplier sanity check + FULL ATTRIBUTION TABLE
# ---------------------------------------------------------------------------

def section10_attribution(
    label: str,
    target_vol: float = 0.25,
    capital: float = 1000.0,
    idm_mean: float = None,
    mean_weight: float = None,
    mean_abs_forecast: float = None,
    fdm_mean: float = None,
    truncation_frac: float = None,
    buffering_frac: float = None,
    netting_loss_frac: float = None,
    vol_estimator_ratio: float = None,
    actual_realized_vol: float = None,
):
    print(f"\n{'='*70}")
    print(f"SECTION 10: VOL GAP ATTRIBUTION TABLE  [{label}]")
    print(f"{'='*70}")
    print()
    print("  Key insight: IDM × avg_weight × corr_factor = 1 BY DESIGN.")
    print("  When IDM is properly calibrated, portfolio_vol = target_vol × avg|fc|/10.")
    print("  (FDM is already embedded in combined_forecast values.)")
    print("  Remaining gap causes: forecast calibration, truncation, netting, buffering.")
    print()

    rows = []
    cumulative = target_vol
    rows.append(("Target vol", f"{target_vol:.1%}", f"{cumulative:.1%}"))

    # Forecast shortfall (main calibration factor)
    if mean_abs_forecast is not None:
        factor = mean_abs_forecast / 10.0
        cumulative *= factor
        note = ""
        if mean_abs_forecast < 8:
            note = "  ← walk-forward warm-up + forecast capping"
        rows.append((f"× avg|fc|/10  ({mean_abs_forecast:.2f}/10){note}",
                      f"× {factor:.4f}", f"→ {cumulative:.1%}"))

    # IDM cap effect (only if IDM known AND it differs meaningfully from theoretical)
    if idm_mean is not None and mean_weight is not None:
        mean_n = round(1.0 / mean_weight)
        # Theoretical uncapped IDM for given N
        # IDM_theoretical_uncapped would require knowing ρ; just flag if IDM < sqrt(N)/2 (rough check)
        rows.append((f"  (IDM={idm_mean:.2f}, N≈{mean_n}; cancels with corr factor by design)",
                      "", ""))

    # Vol estimator ratio (inverted: if EWMA > realized, positions are 1/ratio of target)
    if vol_estimator_ratio is not None and abs(vol_estimator_ratio - 1.0) > 0.02:
        factor = 1.0 / vol_estimator_ratio
        cumulative *= factor
        rows.append((f"× vol-estimator correction  (EWMA/realized={vol_estimator_ratio:.3f})",
                      f"× {factor:.4f}", f"→ {cumulative:.1%}"))

    # Truncation
    if truncation_frac is not None and truncation_frac > 0.005:
        factor = 1.0 - truncation_frac
        cumulative *= factor
        rows.append((f"× (1 - truncation)  ({truncation_frac:.1%} positions zeroed, min=$1)",
                      f"× {factor:.4f}", f"→ {cumulative:.1%}"))

    # Buffering
    if buffering_frac is not None and buffering_frac > 0.01:
        factor = 1.0 - buffering_frac
        cumulative *= factor
        rows.append((f"× (1 - buffering)  ({buffering_frac:.1%} signal delayed/suppressed)",
                      f"× {factor:.4f}", f"→ {cumulative:.1%}"))

    # Netting
    if netting_loss_frac is not None and netting_loss_frac > 0.005:
        factor = 1.0 - netting_loss_frac
        cumulative *= factor
        rows.append((f"× (1 - netting)  ({netting_loss_frac:.1%} gross cancelled by L/S)",
                      f"× {factor:.4f}", f"→ {cumulative:.1%}"))

    rows.append(("= Explained realized vol", "", f"{cumulative:.1%}"))
    if actual_realized_vol is not None:
        rows.append(("Actual realized vol", "", f"{actual_realized_vol:.1%}"))
        residual = actual_realized_vol - cumulative
        rows.append(("Unexplained residual", "", f"{residual:+.1%}"))

    # Print table
    col1_w = max(len(r[0]) for r in rows) + 2
    print(f"  {'Cause':<{col1_w}}  {'Factor':>14}  {'Cumulative':>12}")
    print(f"  {'-'*col1_w}  {'-'*14}  {'-'*12}")
    for cause, factor, cum in rows:
        print(f"  {cause:<{col1_w}}  {factor:>14}  {cum:>12}")
    print()

    if actual_realized_vol is not None:
        vs_target = actual_realized_vol / target_vol
        print(f"  Actual/target ratio: {vs_target:.2f}  ({actual_realized_vol:.1%} / {target_vol:.1%})")
        print()
        if abs(actual_realized_vol - cumulative) / target_vol < 0.05:
            print(f"  ✓ Attribution explains realized vol within 5pp of target vol")
        elif actual_realized_vol > cumulative * 1.1:
            print(f"  NOTE: actual ({actual_realized_vol:.1%}) > explained ({cumulative:.1%}) — "
                  f"some upward factor not captured (e.g., IDM overcorrection)")
        else:
            print(f"  NOTE: actual ({actual_realized_vol:.1%}) < explained ({cumulative:.1%}) — "
                  f"additional drag not fully decomposed")

    print()
    print("  LEVERS TO CLOSE THE VOL GAP:")
    suggestions = []
    if mean_abs_forecast is not None and mean_abs_forecast < 9.0:
        suggestions.append(f"  • Forecast calibration: avg|fc|={mean_abs_forecast:.2f} — "
                           f"longer warm-up period or pre-seeded scalars would help")
    if truncation_frac is not None and truncation_frac > 0.20:
        suggestions.append(f"  • Capital: {truncation_frac:.0%} of positions below $1 min — "
                           f"higher capital OR leverage would reduce truncation dramatically")
    if netting_loss_frac is not None and netting_loss_frac > 0.20:
        suggestions.append(f"  • L/S netting ({netting_loss_frac:.0%}): structural for a long/short strategy — "
                           f"long-only bias or sector constraints would reduce this")
    suggestions.append(f"  • Leverage: directly scales positions (not affected by correlation/IDM logic)")
    for s in suggestions:
        print(s)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_diagnosis(backtest_dir: Path, data_path: Path, quick: bool,
                  capital: float, target_vol: float, label: str):
    """Run all 10 sections and return key metrics dict."""
    print(f"\n{'#'*70}")
    print(f"# VOL GAP DIAGNOSIS: {label}")
    print(f"# backtest-dir: {backtest_dir}")
    print(f"{'#'*70}")

    diag, pnl, prices = load_backtest(backtest_dir, data_path)

    # Realized vol from returns
    actual_vol = None
    if pnl is not None:
        daily_ret = equity_from_pnl(pnl)
        if daily_ret is not None:
            # Drop leading zeros (warm-up period before any positions)
            daily_ret = daily_ret[daily_ret != 0.0]
            actual_vol = daily_ret.std() * np.sqrt(252)
            print(f"\nActual realized vol (from returns): {actual_vol:.1%}")
    else:
        print(f"\nActual realized vol: [no returns data found in {backtest_dir}]")

    # Active instrument mean weight
    active = diag[diag["instrument_weight"] > 0]
    mean_weight = active["instrument_weight"].mean() if len(active) else np.nan
    mean_n = active.groupby("date")["instrument"].count().mean() if len(active) else np.nan

    print(f"Mean active instruments: {mean_n:.1f}")
    print(f"Mean instrument weight: {mean_weight:.4f}  (1/N ≈ {1/mean_weight:.1f})" if mean_weight else "")

    # Run sections
    section0_structural(diag, label, target_vol=target_vol,
                        mean_weight=mean_weight)

    idm_df = section1_idm(diag, prices, label, quick=quick)
    idm_mean = idm_df["idm"].mean() if idm_df is not None else None

    mean_abs_forecast = section2_forecast_mean(diag, label)
    fdm_mean = section3_fdm(diag, label)
    truncation_frac = section4_truncation(diag, prices, label, capital=capital, target_vol=target_vol)
    buffering_frac = section5_buffering(diag, label)
    netting_loss_frac = section6_netting(diag, prices, label)
    section7_forecast_dist(diag, label)
    section8_warmup(diag, pnl, label)
    vol_ratio = section9_vol_lag(diag, prices, label)

    section10_attribution(
        label=label,
        target_vol=target_vol,
        capital=capital,
        idm_mean=idm_mean,
        mean_weight=mean_weight,
        mean_abs_forecast=mean_abs_forecast,
        fdm_mean=fdm_mean,
        truncation_frac=truncation_frac,
        buffering_frac=buffering_frac,
        netting_loss_frac=netting_loss_frac,
        vol_estimator_ratio=vol_ratio,
        actual_realized_vol=actual_vol,
    )

    return {
        "label": label,
        "actual_vol": actual_vol,
        "idm_mean": idm_mean,
        "mean_weight": mean_weight,
        "mean_n": mean_n,
        "mean_abs_forecast": mean_abs_forecast,
        "fdm_mean": fdm_mean,
        "truncation_frac": truncation_frac,
        "buffering_frac": buffering_frac,
        "netting_loss_frac": netting_loss_frac,
        "vol_ratio": vol_ratio,
    }


def print_comparison(results_a: dict, results_b: dict):
    """Side-by-side comparison table."""
    label_a = results_a["label"]
    label_b = results_b["label"]

    print(f"\n{'='*70}")
    print(f"COMPARISON: {label_a}  vs  {label_b}")
    print(f"{'='*70}")

    metrics = [
        ("Actual realized vol", "actual_vol", ".1%"),
        ("Mean IDM", "idm_mean", ".3f"),
        ("Mean instrument weight", "mean_weight", ".4f"),
        ("Mean N active", "mean_n", ".1f"),
        ("Mean |combined_forecast|", "mean_abs_forecast", ".3f"),
        ("Mean FDM", "fdm_mean", ".3f"),
        ("Truncation frac", "truncation_frac", ".1%"),
        ("Buffering frac", "buffering_frac", ".1%"),
        ("Netting loss frac", "netting_loss_frac", ".1%"),
        ("EWMA/Realized vol ratio", "vol_ratio", ".3f"),
    ]

    col_w = max(len(m[0]) for m in metrics) + 2
    print(f"\n  {'Metric':<{col_w}}  {label_a:>20}  {label_b:>20}")
    print(f"  {'-'*col_w}  {'-'*20}  {'-'*20}")

    for name, key, fmt in metrics:
        va = results_a.get(key)
        vb = results_b.get(key)
        sa = f"{va:{fmt}}" if va is not None else "N/A"
        sb = f"{vb:{fmt}}" if vb is not None else "N/A"
        print(f"  {name:<{col_w}}  {sa:>20}  {sb:>20}")


def main():
    parser = argparse.ArgumentParser(description="Vol gap diagnostic for crypto perps backtest")
    parser.add_argument("--backtest-dir", type=Path,
                        default=Path("out/fee_fix_1k"),
                        help="Primary backtest output dir (contains diagnostics.parquet)")
    parser.add_argument("--data", type=Path,
                        default=Path("data/dataset_538registry_6yr_jagged.parquet"),
                        help="Dataset parquet file (for prices)")
    parser.add_argument("--compare", type=Path, default=None,
                        help="Optional second backtest dir for side-by-side comparison")
    parser.add_argument("--compare-data", type=Path, default=None,
                        help="Dataset parquet for --compare backtest (defaults to --data)")
    parser.add_argument("--quick", action="store_true",
                        help="Skip slow IDM recomputation (Section 1)")
    parser.add_argument("--capital", type=float, default=1000.0,
                        help="Trading capital (default: 1000)")
    parser.add_argument("--target-vol", type=float, default=0.25,
                        help="Annual vol target as fraction (default: 0.25)")
    args = parser.parse_args()

    results_a = run_diagnosis(
        backtest_dir=args.backtest_dir,
        data_path=args.data,
        quick=args.quick,
        capital=args.capital,
        target_vol=args.target_vol,
        label="Research 6yr" if "6yr" in str(args.backtest_dir) else args.backtest_dir.name,
    )

    if args.compare is not None:
        compare_data = args.compare_data or args.data
        results_b = run_diagnosis(
            backtest_dir=args.compare,
            data_path=compare_data,
            quick=args.quick,
            capital=args.capital,
            target_vol=args.target_vol,
            label="Advisory 4yr" if "paper" in str(args.compare) else args.compare.name,
        )
        print_comparison(results_a, results_b)


if __name__ == "__main__":
    main()
