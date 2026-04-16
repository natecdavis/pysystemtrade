#!/usr/bin/env python3
"""
Compare two walk-forward portfolio selection methods against a fixed reference:

  1. wf_topk   — Walk-forward K/buffer selection: at each quarterly rebalance,
                 grid-search over (K, entry_buffer, exit_buffer) and pick the
                 combination with the highest expanding-window Calmar ratio.

  2. greedy    — Carver's integer-lot greedy algorithm (MrGreedyPortfolio)
                 with shadow_cost=100, long_only=False, lot_size_notional_override=10
                 (HL $10 minimum modelled as optimizer lot size).

The fixed reference ("flat" from compare_weighting_schemes results) uses K=30,
entry_buffer=3, exit_buffer=15 — parameters chosen retrospectively.

Walk-forward K/buffer selection uses a two-phase approach:
  Phase 1: Standalone simulation — fast NumPy hysteresis loop for all 54 combos
           (6 K values × 3 entry buffers × 3 exit buffers), scoring each combo
           on expanding-window Calmar using a simplified return proxy.
  Phase 2: Full system backtest — inject winning combo's eligibility schedule
           via `dynamic_universe.top_k_precomputed_eligibility_path` config key.

Usage:
    python scripts/compare_portfolio_selection.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --panels out/wf_comparison_56rules/forecast_panels \\
        --outdir out/portfolio_selection_comparison \\
        --schemes wf_topk greedy

    # Skip greedy (slow — runs greedy optimiser for every day × instrument):
    python scripts/compare_portfolio_selection.py --schemes wf_topk

    # Force re-run all steps:
    python scripts/compare_portfolio_selection.py --force

    # Show schedule diagnostics:
    python scripts/compare_portfolio_selection.py --show-schedule
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── grid search parameters ─────────────────────────────────────────────────
ALL_K = [15, 20, 25, 30, 35, 40]
ALL_EB = [1, 3, 5]     # entry buffers
ALL_EX = [10, 15, 20]  # exit buffers
COMBOS = [(K, eb, ex) for K in ALL_K for eb in ALL_EB for ex in ALL_EX]  # 54

ADV_WINDOW = 252        # rolling ADV window (calendar days)
MIN_HISTORY_DAYS = 365  # minimum instrument history to include in ranking
MIN_SCORING_DAYS = 500  # minimum expanding-window days before scoring starts
QUARTERLY_FREQ = "QS"  # quarterly rebalance schedule

DEFAULT_CONFIG = "config/crypto_perps_full_rules.yaml"
DEFAULT_DATA = "data/dataset_538registry_6yr_jagged.parquet"
DEFAULT_PANELS = "out/wf_comparison_56rules/forecast_panels"
DEFAULT_OUTDIR = "out/portfolio_selection_comparison"


# ── data loading ───────────────────────────────────────────────────────────

def load_dataset(data_path: str) -> tuple:
    """Return (adv_df, close_df, meta_df) as wide DataFrames (dates × instruments)."""
    df = pd.read_parquet(data_path)
    adv_df = df.pivot(index="date", columns="instrument", values="adv_notional")
    close_df = df.pivot(index="date", columns="instrument", values="close")
    meta_df = df.set_index(["date", "instrument"])
    return adv_df, close_df, meta_df


def get_hl_instruments(all_instruments: list) -> list:
    """Filter to Hyperliquid-listed instruments."""
    from sysdata.crypto.config_helpers import instrument_id_to_hl_symbol, load_hl_symbols
    hl_symbols = load_hl_symbols()
    return [i for i in all_instruments if instrument_id_to_hl_symbol(i) in hl_symbols]


def compute_combined_forecast(panels_dir, instruments: list) -> pd.DataFrame:
    """
    Load forecast panels and compute mean across all rules per instrument.
    Returns (dates × instruments) DataFrame.
    """
    fc = pd.read_parquet(Path(panels_dir) / "forecasts.parquet")
    # fc: MultiIndex columns (rule, instrument), date index
    # Mean across rules → (dates × instruments)
    fc_T = fc.T  # index = (rule, instrument)
    fc_T.index.names = ["rule", "instrument"]
    combined = fc_T.groupby("instrument").mean().T  # dates × instruments
    return combined.reindex(columns=instruments)


# ── rolling ADV rank ───────────────────────────────────────────────────────

def compute_adv_ranks(adv_df: pd.DataFrame, window: int = ADV_WINDOW) -> pd.DataFrame:
    """
    Rolling mean ADV then rank per day (1 = highest ADV).
    Instruments with fewer than 30 days of data get rank = N+1 (last).
    """
    rolling_adv = adv_df.rolling(window=window, min_periods=30).mean()
    return rolling_adv.rank(axis=1, ascending=False, na_option="bottom")


# ── stage-1 eligibility proxy ──────────────────────────────────────────────

def compute_stage1_proxy(close_df: pd.DataFrame, min_history: int = MIN_HISTORY_DAYS) -> pd.DataFrame:
    """
    Stage 1 eligibility proxy: instrument has >= min_history days of price data
    as of each date. This captures the 'enough history for cost estimation' gate
    without running the full cost pipeline.

    The live cost filter (SR-per-trade, annual SR threshold) also excludes very
    illiquid instruments. These are already low-ranked by ADV so the Stage 2
    hysteresis will not select them anyway. Using price availability as the
    Stage 1 proxy introduces negligible error in the ranking comparison.
    """
    # Cumulative count of non-NaN prices up to each date
    has_price = close_df.notna()
    cumcount = has_price.cumsum()
    return cumcount >= min_history


# ── fast hysteresis simulation ─────────────────────────────────────────────

def simulate_hysteresis(
    rank_df: pd.DataFrame,
    stage1_df: pd.DataFrame,
    K: int, eb: int, ex: int,
) -> pd.DataFrame:
    """
    Fast NumPy hysteresis simulation for one (K, entry_buffer, exit_buffer) combo.

    Entry: instrument not in tradable AND stage1-eligible AND rank <= K - eb
    Exit:  instrument in tradable AND (rank > K + ex OR not stage1-eligible)
    Hard cap at K if exceeded (keep best-ranked K).

    Returns boolean DataFrame (dates × instruments).
    """
    entry_thresh = K - eb
    exit_thresh = K + ex

    # Align to common dates × instruments
    common_dates = rank_df.index.intersection(stage1_df.index)
    ranks = rank_df.loc[common_dates].values.astype(float)
    stage1 = stage1_df.loc[common_dates].values.astype(bool)
    n_dates, n_instr = ranks.shape

    tradable = np.zeros(n_instr, dtype=bool)
    result = np.zeros((n_dates, n_instr), dtype=bool)

    for t in range(n_dates):
        rank_t = ranks[t]
        elig_t = stage1[t]

        # Entry: not in tradable, eligible, rank good enough
        entries = (~tradable) & elig_t & (rank_t <= entry_thresh)
        # Exit: rank dropped too far, or lost Stage 1 eligibility
        exits = tradable & ((rank_t > exit_thresh) | ~elig_t)

        tradable = (tradable | entries) & ~exits

        # Hard cap: keep top-K by current rank if count > K
        if tradable.sum() > K:
            masked_ranks = np.where(tradable, rank_t, np.inf)
            top_k_idx = np.argsort(masked_ranks)[:K]
            new_tradable = np.zeros(n_instr, dtype=bool)
            new_tradable[top_k_idx] = True
            tradable = new_tradable

        result[t] = tradable

    return pd.DataFrame(result, index=common_dates, columns=rank_df.columns)


# ── portfolio return proxy ─────────────────────────────────────────────────

def compute_portfolio_returns(
    eligibility_df: pd.DataFrame,
    combined_fc: pd.DataFrame,
    close_df: pd.DataFrame,
) -> pd.Series:
    """
    Simplified return proxy for walk-forward scoring.

    return[t] = mean_{selected i} clip(combined_fc[i,t] / 20, -1, 1)
                                  × (close[i,t+1]/close[i,t] - 1)

    This captures signal × return without full position sizing.  Used only for
    relative ranking of combos — absolute values do not matter.
    """
    # Forward return: price[t+1]/price[t] - 1, available for entry on day t
    raw_ret = close_df.pct_change()   # ret[t] = price[t]/price[t-1] - 1
    fwd_ret = raw_ret.shift(-1)       # fwd_ret[t] = price[t+1]/price[t] - 1

    common_dates = eligibility_df.index
    common_instr = eligibility_df.columns

    sel = eligibility_df.values.astype(float)  # (T, N)
    fc = combined_fc.reindex(index=common_dates, columns=common_instr).values / 20.0
    np.clip(fc, -1.0, 1.0, out=fc)
    ret = fwd_ret.reindex(index=common_dates, columns=common_instr).values

    np.nan_to_num(fc, copy=False)
    np.nan_to_num(ret, copy=False)

    n_sel = sel.sum(axis=1)
    port_ret = np.where(
        n_sel > 0,
        (sel * fc * ret).sum(axis=1) / np.where(n_sel > 0, n_sel, 1.0),
        0.0,
    )
    return pd.Series(port_ret, index=common_dates)


# ── Calmar computation ─────────────────────────────────────────────────────

def compute_calmar(returns: pd.Series, min_days: int = 200) -> float:
    """Calmar ratio from a daily returns series."""
    r = returns.dropna()
    if len(r) < min_days:
        return -np.inf
    n_years = len(r) / 365.0
    if n_years < 0.5:
        return -np.inf
    cum = (1.0 + r).cumprod()
    cagr = cum.iloc[-1] ** (1.0 / n_years) - 1.0
    max_dd = ((cum - cum.cummax()) / cum.cummax()).min()
    if abs(max_dd) < 1e-8:
        return np.inf
    return cagr / abs(max_dd)


# ── walk-forward K/buffer selection ───────────────────────────────────────

def walk_forward_k_selection(
    combo_returns: dict,
    min_scoring_days: int = MIN_SCORING_DAYS,
) -> tuple:
    """
    At each quarterly date, score all 54 combos on expanding-window Calmar.
    Return (winners dict, schedule DataFrame).

    winners: {quarterly_date: (K, eb, ex)}
    schedule_df: DataFrame with columns [K, eb, ex, calmar_winner, n_days]
    """
    # Build quarterly rebalance dates from the common index
    first_return = next(iter(combo_returns.values()))
    full_index = first_return.index
    quarterly = pd.date_range(
        start=full_index[0], end=full_index[-1], freq=QUARTERLY_FREQ
    )

    winners = {}
    schedule_rows = []
    all_combos = sorted(combo_returns.keys())
    default_combo = (30, 3, 15) if (30, 3, 15) in all_combos else all_combos[0]

    for q_date in quarterly:
        history = {combo: combo_returns[combo][:q_date] for combo in all_combos}
        n_days = max(len(r) for r in history.values())

        if n_days < min_scoring_days:
            winners[q_date] = default_combo
            schedule_rows.append({
                "date": q_date,
                "K": default_combo[0], "eb": default_combo[1], "ex": default_combo[2],
                "calmar": np.nan, "n_days": n_days, "note": "default (insufficient history)",
            })
            continue

        calmar_scores = {
            combo: compute_calmar(ret, min_days=min_scoring_days) if len(ret) >= min_scoring_days else -np.inf
            for combo, ret in history.items()
        }
        best = max(calmar_scores, key=calmar_scores.get)
        winners[q_date] = best
        schedule_rows.append({
            "date": q_date,
            "K": best[0], "eb": best[1], "ex": best[2],
            "calmar": calmar_scores[best], "n_days": n_days, "note": "",
        })

    return winners, pd.DataFrame(schedule_rows)


def build_wf_eligibility(
    combo_eligibilities: dict,
    winners: dict,
    quarterly_dates: list,
) -> pd.DataFrame:
    """
    Build the final walk-forward eligibility DataFrame.
    For period [quarter[i], quarter[i+1]), use the combo selected at quarter[i].
    """
    first_elig = next(iter(combo_eligibilities.values()))
    all_dates = first_elig.index
    all_instruments = first_elig.columns

    result = pd.DataFrame(False, index=all_dates, columns=all_instruments)

    q_list = sorted(quarterly_dates)
    for i, q_date in enumerate(q_list):
        next_q = q_list[i + 1] if i + 1 < len(q_list) else all_dates[-1] + pd.Timedelta(days=1)
        combo = winners.get(q_date, (30, 3, 15))
        elig = combo_eligibilities[combo]

        # Dates in this quarter period
        period_mask = (all_dates >= q_date) & (all_dates < next_q)
        period_dates = all_dates[period_mask]

        # Reindex combo eligibility to cover exactly the period dates
        period_elig = elig.reindex(index=period_dates, columns=all_instruments, fill_value=False)
        result.loc[period_dates] = period_elig.values

    return result


# ── full system backtest via subprocess ────────────────────────────────────

def run_backtest(
    config_path: str,
    data_path: str,
    outdir: Path,
    extra_config: dict = None,
) -> None:
    """Run backtest by injecting extra_config keys into a temp config."""
    outdir.mkdir(parents=True, exist_ok=True)

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    if extra_config:
        for k, v in extra_config.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                # Shallow merge: keep existing keys, add/overwrite new ones
                merged = dict(cfg[k])
                merged.update(v)
                cfg[k] = merged
            else:
                cfg[k] = v

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False,
        dir=outdir.parent, prefix="tmp_psel_config_",
    ) as tmp:
        yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
        tmp_config = Path(tmp.name)

    macro_path = Path("data/macro_factors.parquet")
    cmd = [
        sys.executable,
        "scripts/run_dynamic_universe_backtest.py",
        "--config", str(tmp_config),
        "--data", data_path,
        "--outdir", str(outdir),
    ]
    if macro_path.exists():
        cmd += ["--macro-data", str(macro_path)]

    try:
        subprocess.run(cmd, check=True)
    finally:
        tmp_config.unlink(missing_ok=True)


# ── results loading and display ────────────────────────────────────────────

def load_metrics(outdir: Path) -> dict:
    summary_path = outdir / "performance_summary.json"
    if not summary_path.exists():
        return {}
    with open(summary_path) as f:
        s = json.load(f)
    m = s.get("metrics", {})
    p = s.get("portfolio", {})
    return {
        "sharpe":   m.get("sharpe", float("nan")),
        "calmar":   m.get("calmar", float("nan")),
        "cagr":     m.get("cagr", float("nan")),
        "vol":      m.get("ann_vol", float("nan")),
        "max_dd":   m.get("max_dd", float("nan")),
        "turnover": p.get("annual_turnover", float("nan")),
        "avg_pos":  p.get("avg_active_positions", float("nan")),
    }


def print_comparison(results: dict, schedule_df: pd.DataFrame = None) -> None:
    """Print comparison table and optional schedule summary."""
    ref = results.get("reference", {})
    print(f"\n{'='*95}")
    print("PORTFOLIO SELECTION METHOD COMPARISON")
    print(f"{'='*95}")
    print(
        f"{'Method':<25} {'Sharpe':>7} {'ΔSharpe':>8} {'Calmar':>7} {'ΔCalmar':>8} "
        f"{'CAGR%':>7} {'MaxDD%':>7} {'Turn':>7} {'AvgPos':>7}"
    )
    print("-" * 95)

    def _row(label, r, ref):
        if not r:
            print(f"  {label:<23} — no results")
            return
        b_sr = ref.get("sharpe", float("nan"))
        b_ca = ref.get("calmar", float("nan"))
        d_sr = (r["sharpe"] - b_sr) / abs(b_sr) * 100 if ref else float("nan")
        d_ca = (r["calmar"] - b_ca) / abs(b_ca) * 100 if ref else float("nan")
        arrow = "↑" if d_sr > 1 else ("↓" if d_sr < -1 else "~")
        print(
            f"  {label:<23} {r['sharpe']:>7.4f} {d_sr:>+7.1f}% {arrow} "
            f"{r['calmar']:>7.4f} {d_ca:>+7.1f}%  "
            f"{r['cagr']*100:>6.2f}% {r['max_dd']*100:>6.2f}% "
            f"{r['turnover']:>7.1f} {r['avg_pos']:>7.1f}"
        )

    if ref:
        _row("reference (flat K=30)", ref, {})
        print("-" * 95)

    _row("wf_topk", results.get("wf_topk", {}), ref)
    print("-" * 95)
    for scheme, label in [
        ("greedy_daily",   "greedy (daily)"),
        ("greedy_weekly",  "greedy (weekly)"),
        ("greedy_monthly", "greedy (monthly)"),
        ("greedy",         "greedy (daily)"),   # legacy alias
    ]:
        if scheme in results:
            _row(label, results[scheme], ref)
    print("=" * 95)
    print("\nNote: reference uses K=30, entry_buffer=3, exit_buffer=15 — chosen retrospectively.")
    print("      wf_topk: expanding-window Calmar selects K/buffers quarterly (out-of-sample).")
    print("      greedy: Carver integer-lot optimizer, shadow_cost=100, long_only=False, lot=$10.\n")

    if schedule_df is not None and not schedule_df.empty:
        print("Walk-forward K/buffer schedule (quarterly winners):")
        print("-" * 65)
        print(f"{'Date':<14} {'K':>4} {'EB':>4} {'EX':>4} {'Calmar':>8} {'N_days':>7}  Note")
        for _, row in schedule_df.iterrows():
            calmar_str = f"{row['calmar']:>8.3f}" if pd.notna(row['calmar']) else "     N/A"
            print(
                f"  {str(row['date'].date()):<12} {int(row['K']):>4} {int(row['eb']):>4} "
                f"{int(row['ex']):>4} {calmar_str} {int(row['n_days']):>7}  {row.get('note','')}"
            )
        print()

        # Combo frequency summary
        won = schedule_df[schedule_df["note"] == ""].copy()
        if len(won) > 0:
            freq = won.groupby(["K", "eb", "ex"]).size().sort_values(ascending=False)
            print("Most frequently selected combos:")
            for (K, eb, ex), cnt in freq.head(8).items():
                print(f"  K={int(K)}, eb={int(eb)}, ex={int(ex)}: {cnt} quarters")
            print()


# ── main ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare walk-forward K/buffer vs greedy portfolio selection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config",  default=DEFAULT_CONFIG)
    parser.add_argument("--data",    default=DEFAULT_DATA)
    parser.add_argument("--panels",  default=DEFAULT_PANELS, help="Forecast panels directory")
    parser.add_argument("--outdir",  default=DEFAULT_OUTDIR)
    parser.add_argument(
        "--schemes", nargs="+",
        default=["wf_topk", "greedy_daily", "greedy_weekly", "greedy_monthly"],
        choices=["wf_topk", "greedy_daily", "greedy_weekly", "greedy_monthly", "greedy"],
        help="Schemes to run (greedy = alias for greedy_daily)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-run all steps even if results exist",
    )
    parser.add_argument(
        "--force-simulate", action="store_true",
        help="Re-run standalone hysteresis simulation (skips full backtest re-run)",
    )
    parser.add_argument(
        "--force-backtest", action="store_true",
        help="Re-run full system backtests (skips simulation re-run)",
    )
    parser.add_argument(
        "--show-schedule", action="store_true",
        help="Print quarterly K/buffer selection schedule",
    )
    parser.add_argument(
        "--reference-dir",
        default="out/wf_comparison_56rules/backtest_flat",
        help="Directory with reference backtest results (flat K=30 fixed)",
    )
    args = parser.parse_args()

    out_dir = Path(args.outdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    panels_dir = Path(args.panels)

    force_simulate = args.force or args.force_simulate
    force_backtest = args.force or args.force_backtest

    print(f"\nPortfolio selection comparison")
    print(f"  Config:   {args.config}")
    print(f"  Data:     {args.data}")
    print(f"  Panels:   {panels_dir}")
    print(f"  Outdir:   {out_dir}")
    print(f"  Schemes:  {args.schemes}")

    results = {}

    # ── Reference (flat K=30, fixed) ──────────────────────────────────────
    ref_dir = Path(args.reference_dir)
    if ref_dir.exists():
        results["reference"] = load_metrics(ref_dir)
        if results["reference"]:
            r = results["reference"]
            print(f"\nReference (flat K=30): Sharpe={r['sharpe']:.4f}, Calmar={r['calmar']:.4f}")
    else:
        print(f"\nReference dir not found: {ref_dir}")

    # ── WF TopK ───────────────────────────────────────────────────────────
    schedule_df = None
    if "wf_topk" in args.schemes:
        print(f"\n{'='*60}")
        print("WF-K: Walk-forward K/buffer selection")
        print(f"{'='*60}")

        elig_path = out_dir / "wf_topk_eligibility.parquet"
        schedule_path = out_dir / "wf_topk_schedule.csv"
        wf_backtest_dir = out_dir / "backtest_wf_topk"

        # ── Phase 1: Standalone simulation ─────────────────────────────
        if force_simulate or not elig_path.exists():
            print("\nPhase 1: Standalone hysteresis simulation")

            print("  Loading dataset...", end=" ", flush=True)
            adv_df, close_df, _ = load_dataset(args.data)
            print(f"done ({adv_df.shape[0]} days × {adv_df.shape[1]} instruments)")

            print("  Applying HL exchange filter...", end=" ", flush=True)
            all_instr = list(adv_df.columns)
            hl_instr = get_hl_instruments(all_instr)
            adv_df = adv_df[hl_instr]
            close_df = close_df[hl_instr]
            print(f"done ({len(hl_instr)}/{len(all_instr)} on HL)")

            print("  Computing Stage 1 eligibility proxy...", end=" ", flush=True)
            stage1_df = compute_stage1_proxy(close_df)
            print("done")

            print("  Computing rolling ADV ranks...", end=" ", flush=True)
            rank_df = compute_adv_ranks(adv_df, window=ADV_WINDOW)
            print("done")

            print(f"  Simulating {len(COMBOS)} combos", end=" ", flush=True)
            combo_eligibilities = {}
            for i, (K, eb, ex) in enumerate(COMBOS):
                combo_eligibilities[(K, eb, ex)] = simulate_hysteresis(
                    rank_df, stage1_df, K, eb, ex
                )
                if (i + 1) % 9 == 0:
                    print(f"[{i+1}/{len(COMBOS)}]", end=" ", flush=True)
            print("done")

            print("  Computing combined forecasts...", end=" ", flush=True)
            combined_fc = compute_combined_forecast(panels_dir, hl_instr)
            print("done")

            print("  Computing portfolio return proxies...", end=" ", flush=True)
            combo_returns = {}
            for combo, elig in combo_eligibilities.items():
                combo_returns[combo] = compute_portfolio_returns(elig, combined_fc, close_df)
            print("done")

            print("  Running walk-forward K/buffer scoring...", end=" ", flush=True)
            winners, schedule_df = walk_forward_k_selection(combo_returns)
            schedule_df.to_csv(schedule_path, index=False)
            print("done")

            print("  Building final WF eligibility schedule...", end=" ", flush=True)
            wf_elig = build_wf_eligibility(
                combo_eligibilities, winners, list(winners.keys())
            )
            wf_elig.to_parquet(elig_path)
            print(f"done — saved to {elig_path}")

        else:
            print(f"\nPhase 1: Loading existing simulation results from {out_dir}")
            if schedule_path.exists():
                schedule_df = pd.read_csv(schedule_path, parse_dates=["date"])

        # ── Phase 2: Full system backtest ───────────────────────────────
        summary_path = wf_backtest_dir / "performance_summary.json"
        if force_backtest or not summary_path.exists():
            print("\nPhase 2: Full system backtest (WF-K eligibility injected)")
            run_backtest(
                config_path=args.config,
                data_path=args.data,
                outdir=wf_backtest_dir,
                extra_config={
                    "dynamic_universe": {
                        "top_k_precomputed_eligibility_path": str(elig_path.resolve()),
                    }
                },
            )
        else:
            print(f"\nPhase 2: WF-K backtest results exist at {wf_backtest_dir}")

        results["wf_topk"] = load_metrics(wf_backtest_dir)
        if results["wf_topk"]:
            r = results["wf_topk"]
            print(f"WF-K result: Sharpe={r['sharpe']:.4f}, Calmar={r['calmar']:.4f}, "
                  f"MaxDD={r['max_dd']*100:.2f}%")

    # ── Greedy variants ────────────────────────────────────────────────────
    # "greedy" is a legacy alias for "greedy_daily"
    greedy_variants = {
        "greedy_daily":   ("D",  "daily"),
        "greedy_weekly":  ("W",  "weekly"),
        "greedy_monthly": ("ME", "month-end"),
        "greedy":         ("D",  "daily"),   # alias
    }
    base_greedy_config = {
        "use_greedy_portfolio": True,
        "lot_size_notional_override": 10,
        "greedy_params": {
            "shadow_cost": 100,
            "long_only": False,
            "tracking_error_buffer": 0.0125,
            "correlation_span": 60,
            "min_history_days": 30,
        },
    }

    for scheme in args.schemes:
        if scheme not in greedy_variants:
            continue

        freq, freq_label = greedy_variants[scheme]
        print(f"\n{'='*60}")
        print(f"Greedy ({freq_label}): shadow_cost=100, long_only=False, lot=$10")
        print(f"{'='*60}")
        if freq == "D":
            print("  WARNING: daily greedy runs the optimiser for every day. "
                  "This can take 2–4 hours.")

        greedy_dir = out_dir / f"backtest_{scheme}"
        summary_path = greedy_dir / "performance_summary.json"

        if force_backtest or not summary_path.exists():
            extra = {k: v for k, v in base_greedy_config.items()}
            # Deep-copy greedy_params and inject rebalance_freq
            extra["greedy_params"] = dict(base_greedy_config["greedy_params"])
            extra["greedy_params"]["rebalance_freq"] = freq
            run_backtest(
                config_path=args.config,
                data_path=args.data,
                outdir=greedy_dir,
                extra_config=extra,
            )
        else:
            print(f"  Results exist at {greedy_dir}")

        results[scheme] = load_metrics(greedy_dir)
        if results[scheme]:
            r = results[scheme]
            print(f"  Sharpe={r['sharpe']:.4f}, Calmar={r['calmar']:.4f}, "
                  f"MaxDD={r['max_dd']*100:.2f}%, Turnover={r['turnover']:.1f}x")

    # ── Print comparison ───────────────────────────────────────────────────
    print_comparison(results, schedule_df if args.show_schedule else None)

    # Save results
    results_path = out_dir / "comparison_results.json"
    with open(results_path, "w") as f:
        json.dump({k: v for k, v in results.items() if isinstance(v, dict)}, f, indent=2)
    print(f"Results saved to {results_path}")


if __name__ == "__main__":
    main()
