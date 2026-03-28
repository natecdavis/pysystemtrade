#!/usr/bin/env python3
"""
Rule Correlation Clustering — Carver Post 8 Diagnostic

Empirically tests whether trading speed (fast/medium/slow) dominates rule type
(EWMAC vs Breakout vs TSMOM etc.) in the forecast-return correlation structure,
as predicted by Carver's "Portfolio Construction" post.

Computes risk-contribution-weighted P&L for each active rule via
pandl_for_trading_rule_weighted(), builds the correlation matrix, and clusters
hierarchically. Outputs dendrogram, heatmap, and a text summary.

Usage:
    python scripts/analyze_rule_correlations.py \\
        --config config/crypto_perps_full_rules.yaml \\
        --data data/dataset_538registry_6yr_jagged.parquet \\
        --outdir out/rule_correlations

Runtime: ~15-30 min (one full system build + per-rule P&L extraction).
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rule metadata: family + speed tier
# Speed tiers: fast (lookback ≤20d), medium (21–64d), slow (>64d),
#              carry_input, carry, xs, skew
# ---------------------------------------------------------------------------

RULE_META = {
    # --- EWMAC ---
    "ewmac_8":   {"family": "ewmac",         "speed": "fast"},
    "ewmac_16":  {"family": "ewmac",         "speed": "medium"},
    "ewmac_32":  {"family": "ewmac",         "speed": "medium"},
    "ewmac_64":  {"family": "ewmac",         "speed": "slow"},
    # --- Breakout ---
    "breakout_20":  {"family": "breakout",   "speed": "fast"},
    "breakout_40":  {"family": "breakout",   "speed": "medium"},
    "breakout_80":  {"family": "breakout",   "speed": "medium"},
    "breakout_160": {"family": "breakout",   "speed": "slow"},
    # --- Normmom (normalised momentum) ---
    "normmom_8":   {"family": "normmom",     "speed": "fast"},
    "normmom_16":  {"family": "normmom",     "speed": "medium"},
    "normmom_32":  {"family": "normmom",     "speed": "medium"},
    "normmom_64":  {"family": "normmom",     "speed": "slow"},
    # --- Accel ---
    "accel_16":  {"family": "accel",         "speed": "fast"},
    "accel_32":  {"family": "accel",         "speed": "medium"},
    "accel_64":  {"family": "accel",         "speed": "slow"},
    # --- Assettrend ---
    "assettrend_8":   {"family": "assettrend", "speed": "fast"},
    "assettrend_16":  {"family": "assettrend", "speed": "medium"},
    "assettrend_32":  {"family": "assettrend", "speed": "medium"},
    "assettrend_64":  {"family": "assettrend", "speed": "slow"},
    # --- Relmomentum (cross-sectional rank) ---
    "relmomentum_20": {"family": "relmomentum", "speed": "fast"},
    "relmomentum_40": {"family": "relmomentum", "speed": "medium"},
    "relmomentum_80": {"family": "relmomentum", "speed": "slow"},
    # --- Residual momentum ---
    "residual_momentum_16": {"family": "residual_momentum", "speed": "medium"},
    "residual_momentum_32": {"family": "residual_momentum", "speed": "medium"},
    "residual_momentum_64": {"family": "residual_momentum", "speed": "slow"},
    # --- Gated carry ---
    "gated_carry_10": {"family": "gated_carry", "speed": "carry"},
    "gated_carry_30": {"family": "gated_carry", "speed": "carry"},
    "gated_carry_60": {"family": "gated_carry", "speed": "carry"},
    # --- Vol-norm carry inputs (additive, weight may be 0.01 or 0) ---
    "vol_norm_carry_10": {"family": "carry_input", "speed": "carry"},
    "vol_norm_carry_30": {"family": "carry_input", "speed": "carry"},
    "vol_norm_carry_60": {"family": "carry_input", "speed": "carry"},
    # --- Cross-sectional ---
    "xs_carry":     {"family": "xs",  "speed": "xs"},
    "xs_activity":  {"family": "xs",  "speed": "xs"},
    "xs_val":       {"family": "xs",  "speed": "xs"},
    "inter_sector": {"family": "xs",  "speed": "xs"},
    # --- Skew ---
    "skew_abs_90":  {"family": "skew_abs", "speed": "skew"},
    "skew_abs_180": {"family": "skew_abs", "speed": "skew"},
    "skew_abs_365": {"family": "skew_abs", "speed": "skew"},
    "skew_rv_90":   {"family": "skew_rv",  "speed": "skew"},
    "skew_rv_180":  {"family": "skew_rv",  "speed": "skew"},
    "skew_rv_365":  {"family": "skew_rv",  "speed": "skew"},
}

# Colour palette per family (matplotlib named colours)
FAMILY_COLORS = {
    "ewmac":               "#1f77b4",   # blue
    "breakout":            "#ff7f0e",   # orange
    "normmom":             "#2ca02c",   # green
    "accel":               "#d62728",   # red
    "assettrend":          "#9467bd",   # purple
    "relmomentum":         "#8c564b",   # brown
    "residual_momentum":   "#e377c2",   # pink
    "gated_carry":         "#17becf",   # cyan
    "carry_input":         "#bcbd22",   # yellow-green
    "xs":                  "#7f7f7f",   # grey
    "skew_abs":            "#aec7e8",   # light blue
    "skew_rv":             "#ffbb78",   # light orange
}

SPEED_LABELS = {
    "fast": "F",
    "medium": "M",
    "slow": "S",
    "carry": "C",
    "xs": "X",
    "skew": "K",
}


# ---------------------------------------------------------------------------
# System build
# ---------------------------------------------------------------------------

def build_system(config_path: str, data_path: str):
    """Build system with auto-discovery of optional auxiliary data files."""
    import yaml
    import os

    from sysdata.config.configdata import Config
    from sysdata.crypto.parquet_perps_sim_data import parquetCryptoPerpsSimData
    from systems.crypto_perps.crypto_portfolio_oi_overlay import CryptoDynamicPortfolioWithOIOverlay
    from systems.provided.crypto_example.core.dynamic_portfolio import CryptoDynamicPortfolio
    from systems.basesystem import System
    from systems.forecasting import Rules
    from systems.crypto_perps.forecast_combine_gated import ForecastCombineGated
    from systems.forecast_scale_cap import ForecastScaleCap
    from systems.rawdata import RawData
    from systems.positionsizing import PositionSizing
    from systems.accounts.accounts_stage import Account
    from syscore.constants import arg_not_supplied

    logger.info(f"Loading config: {config_path}")
    with open(config_path) as f:
        config_dict = yaml.safe_load(f)
    config = Config(config_dict)

    logger.info(f"Loading dataset: {data_path}")
    data_dir = Path(data_path).parent
    env_root_str = os.environ.get("LIVE_OPS_ENV_ROOT")
    env_root = Path(env_root_str) if env_root_str else Path.cwd()

    def _discover(name):
        p = data_dir / name
        if p.exists():
            logger.info(f"  Auto-discovered: {p}")
            return str(p)
        return arg_not_supplied

    data = parquetCryptoPerpsSimData(
        dataset_path=data_path,
        config_path=config_path,
        env_root=env_root,
        use_dynamic_universe=True,
        macro_data_path=_discover("macro_factors.parquet"),
        oi_data_path=_discover("binance_oi_processed.parquet"),
        sector_map_path=_discover("sector_map.json"),
        fg_data_path=_discover("fg_index.parquet"),
        mvrv_data_path=_discover("mvrv_index.parquet"),
        active_addresses_data_path=_discover("active_addresses.parquet"),
        market_cap_data_path=_discover("market_cap.parquet"),
    )

    use_oi = config.get_element_or_default("use_oi_overlay", False)
    use_fg = config.get_element_or_default("use_fg_overlay", False)
    use_mvrv = config.get_element_or_default("use_mvrv_overlay", False)
    use_any_overlay = use_oi or use_fg or use_mvrv

    portfolio_stage = (
        CryptoDynamicPortfolioWithOIOverlay() if use_any_overlay
        else CryptoDynamicPortfolio()
    )

    system = System(
        stage_list=[
            Account(),
            portfolio_stage,
            PositionSizing(),
            ForecastCombineGated(),
            ForecastScaleCap(),
            Rules(),
            RawData(),
        ],
        data=data,
        config=config,
    )

    logger.info("System built.")
    return system


# ---------------------------------------------------------------------------
# P&L extraction
# ---------------------------------------------------------------------------

def extract_rule_returns(system) -> pd.DataFrame:
    """
    Extract risk-contribution-weighted daily return series for each active rule.

    Returns a DataFrame (dates × rules).
    """
    forecast_weights = getattr(system.config, "forecast_weights", {}) or {}
    active_rules = [r for r, w in forecast_weights.items() if w > 0]
    logger.info(f"Active rules ({len(active_rules)}): {active_rules}")

    rule_returns = {}
    for i, rule in enumerate(active_rules):
        logger.info(f"  Extracting P&L for rule {i+1}/{len(active_rules)}: {rule}")
        try:
            curve_group = system.accounts.pandl_for_trading_rule_weighted(rule)
            df = curve_group.to_frame()
            # Sum across instruments → single daily series for this rule
            rule_returns[rule] = df.sum(axis=1)
        except Exception as e:
            logger.warning(f"    Failed for {rule}: {e}")

    if not rule_returns:
        raise RuntimeError("No rule returns could be extracted.")

    returns_df = pd.DataFrame(rule_returns)
    returns_df = returns_df.dropna(how="all")
    logger.info(
        f"Returns DataFrame: {len(returns_df)} dates × {len(returns_df.columns)} rules"
    )
    return returns_df


# ---------------------------------------------------------------------------
# Correlation + clustering
# ---------------------------------------------------------------------------

def compute_correlation_and_linkage(returns_df: pd.DataFrame):
    """
    Returns (corr, dist, linkage) where:
      corr     : correlation matrix (DataFrame)
      dist     : condensed distance vector (for scipy)
      linkage  : hierarchical linkage matrix (ward method)
    """
    import scipy.cluster.hierarchy as sch
    import scipy.spatial.distance as ssd

    corr = returns_df.corr(min_periods=252)
    # Replace NaN with 0 in correlation for distance calc (NaN pairs → neutral distance)
    corr_filled = corr.fillna(0.0)
    # Symmetry + diagonal = 1
    np.fill_diagonal(corr_filled.values, 1.0)

    dist = 1.0 - corr_filled
    # Ensure distance matrix is valid (non-negative, symmetric)
    dist_np = np.clip(dist.values, 0.0, 2.0)
    np.fill_diagonal(dist_np, 0.0)
    dist_np = (dist_np + dist_np.T) / 2.0  # enforce symmetry

    condensed = ssd.squareform(dist_np)
    linkage = sch.linkage(condensed, method="ward")

    return corr, dist_np, linkage


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def save_dendrogram(linkage, rule_names, outdir: Path):
    """Save hierarchical dendrogram with rules colour-coded by family."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import scipy.cluster.hierarchy as sch

    fig, ax = plt.subplots(figsize=(14, max(8, len(rule_names) * 0.35)))

    def _leaf_color(rule):
        meta = RULE_META.get(rule, {})
        family = meta.get("family", "unknown")
        return FAMILY_COLORS.get(family, "#333333")

    # Build colour map for leaf labels
    label_colors = {rule: _leaf_color(rule) for rule in rule_names}

    dend = sch.dendrogram(
        linkage,
        labels=rule_names,
        orientation="left",
        ax=ax,
        leaf_font_size=9,
        color_threshold=0.7 * max(linkage[:, 2]),
    )

    # Colour leaf labels by family
    ax_labels = ax.get_ymajorticklabels() if hasattr(ax, "get_ymajorticklabels") else ax.get_yticklabels()
    for lbl in ax_labels:
        text = lbl.get_text()
        color = label_colors.get(text, "#333333")
        lbl.set_color(color)

    ax.set_title("Rule Correlation Dendrogram (Ward linkage, colour = family)", fontsize=12)
    ax.set_xlabel("Distance (1 − correlation)")

    # Legend for families
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color=color, linewidth=4, label=family)
        for family, color in FAMILY_COLORS.items()
        if any(RULE_META.get(r, {}).get("family") == family for r in rule_names)
    ]
    ax.legend(handles=legend_elements, loc="lower right", fontsize=7, title="Family")

    plt.tight_layout()
    out_path = outdir / "dendrogram.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"  Saved: {out_path}")


def save_heatmap(corr: pd.DataFrame, linkage, outdir: Path):
    """Save correlation heatmap sorted by cluster order."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns
    import scipy.cluster.hierarchy as sch

    rules = list(corr.columns)
    family_colors = [
        FAMILY_COLORS.get(RULE_META.get(r, {}).get("family", "unknown"), "#333333")
        for r in rules
    ]
    row_colors = pd.Series(family_colors, index=rules, name="Family")

    # Use precomputed linkage for row_linkage
    g = sns.clustermap(
        corr,
        row_linkage=linkage,
        col_linkage=linkage,
        row_colors=row_colors,
        col_colors=row_colors,
        cmap="RdYlGn",
        center=0.0,
        vmin=-1.0,
        vmax=1.0,
        figsize=(max(14, len(rules) * 0.45), max(12, len(rules) * 0.4)),
        annot=False,
        xticklabels=True,
        yticklabels=True,
    )
    g.ax_heatmap.tick_params(axis="x", labelsize=7, rotation=90)
    g.ax_heatmap.tick_params(axis="y", labelsize=7, rotation=0)
    g.fig.suptitle("Rule Return Correlations (clustered)", y=1.01, fontsize=12)

    # Family legend
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor=color, label=family)
        for family, color in FAMILY_COLORS.items()
        if any(RULE_META.get(r, {}).get("family") == family for r in rules)
    ]
    g.ax_heatmap.legend(
        handles=legend_elements,
        loc="upper left",
        bbox_to_anchor=(1.15, 1.0),
        fontsize=7,
        title="Family",
    )

    out_path = outdir / "heatmap.png"
    g.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close("all")
    logger.info(f"  Saved: {out_path}")


def _get_cluster_labels(linkage, n_rules: int, n_clusters: int = 8):
    """Cut dendrogram into n_clusters groups; return array of cluster ids."""
    from scipy.cluster.hierarchy import fcluster
    return fcluster(linkage, t=n_clusters, criterion="maxclust")


def build_summary(
    corr: pd.DataFrame,
    linkage,
    returns_df: pd.DataFrame,
    outdir: Path,
) -> str:
    """Build summary text, save to summary.txt, and return as string."""
    import scipy.cluster.hierarchy as sch

    rules = list(corr.columns)
    n = len(rules)

    lines = []
    lines.append("=" * 72)
    lines.append("RULE CORRELATION CLUSTERING SUMMARY")
    lines.append(f"Dataset: {len(returns_df)} trading days  |  {n} active rules")
    lines.append("=" * 72)

    # --- Top / bottom correlated pairs ---
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            r_i, r_j = rules[i], rules[j]
            c = corr.iloc[i, j]
            if not np.isnan(c):
                pairs.append((r_i, r_j, c))

    pairs_sorted = sorted(pairs, key=lambda x: x[2], reverse=True)

    lines.append("\nTop 10 most correlated pairs (highest correlation):")
    for r_i, r_j, c in pairs_sorted[:10]:
        m_i = RULE_META.get(r_i, {})
        m_j = RULE_META.get(r_j, {})
        tag = ""
        if m_i.get("speed") == m_j.get("speed") and m_i.get("family") != m_j.get("family"):
            tag = " ← cross-family same-speed"
        lines.append(f"  {r_i:<28} {r_j:<28} {c:+.3f}{tag}")

    lines.append("\nTop 10 least correlated pairs (lowest / most negative):")
    for r_i, r_j, c in pairs_sorted[-10:][::-1]:
        lines.append(f"  {r_i:<28} {r_j:<28} {c:+.3f}")

    # --- Cluster membership ---
    n_clusters = min(8, n // 2)
    cluster_labels = _get_cluster_labels(linkage, n, n_clusters)

    lines.append(f"\nHierarchical cluster membership (Ward, {n_clusters} clusters):")
    cluster_to_rules = {}
    for rule, cl in zip(rules, cluster_labels):
        cluster_to_rules.setdefault(cl, []).append(rule)

    for cl_id in sorted(cluster_to_rules):
        members = cluster_to_rules[cl_id]
        families_in = set(RULE_META.get(r, {}).get("family", "?") for r in members)
        speeds_in = set(RULE_META.get(r, {}).get("speed", "?") for r in members)
        lines.append(f"\n  Cluster {cl_id} ({len(members)} rules):")
        for r in members:
            meta = RULE_META.get(r, {})
            lines.append(
                f"    {r:<30}  family={meta.get('family','?'):<22}  speed={meta.get('speed','?')}"
            )
        lines.append(f"    → Families present: {sorted(families_in)}")
        lines.append(f"    → Speed tiers present: {sorted(speeds_in)}")

    # --- Speed-dominance verdict ---
    lines.append("\n" + "=" * 72)
    lines.append("CARVER HYPOTHESIS: Do fast rules cluster across families?")
    lines.append("(i.e., does trading speed dominate rule type in correlation structure?)")
    lines.append("")

    # Compute median within-speed-tier correlation vs within-family correlation
    speed_pairs_corrs = []
    family_pairs_corrs = []
    cross_both_corrs = []

    for i in range(n):
        for j in range(i + 1, n):
            r_i, r_j = rules[i], rules[j]
            c = corr.iloc[i, j]
            if np.isnan(c):
                continue
            m_i, m_j = RULE_META.get(r_i, {}), RULE_META.get(r_j, {})
            same_speed = m_i.get("speed") == m_j.get("speed")
            same_family = m_i.get("family") == m_j.get("family")

            if same_speed and not same_family:
                speed_pairs_corrs.append((r_i, r_j, c))
            if same_family and not same_speed:
                family_pairs_corrs.append((r_i, r_j, c))
            if not same_speed and not same_family:
                cross_both_corrs.append((r_i, r_j, c))

    # Filter to trend-only rules for this analysis (exclude carry/xs/skew)
    trend_speeds = {"fast", "medium", "slow"}

    def _trend_only(pairs_list):
        return [
            (r_i, r_j, c) for r_i, r_j, c in pairs_list
            if RULE_META.get(r_i, {}).get("speed") in trend_speeds
            and RULE_META.get(r_j, {}).get("speed") in trend_speeds
        ]

    speed_trend = _trend_only(speed_pairs_corrs)
    family_trend = _trend_only(family_pairs_corrs)
    cross_trend = _trend_only(cross_both_corrs)

    med_speed = float(np.median([c for _, _, c in speed_trend])) if speed_trend else float("nan")
    med_family = float(np.median([c for _, _, c in family_trend])) if family_trend else float("nan")
    med_cross = float(np.median([c for _, _, c in cross_trend])) if cross_trend else float("nan")

    lines.append(f"Median correlation (trend rules only, excl. carry/xs/skew):")
    lines.append(f"  Same-speed, cross-family : {med_speed:+.3f}  (n={len(speed_trend)} pairs)")
    lines.append(f"  Same-family, diff-speed  : {med_family:+.3f}  (n={len(family_trend)} pairs)")
    lines.append(f"  Cross-speed, cross-family: {med_cross:+.3f}  (n={len(cross_trend)} pairs)")
    lines.append("")

    speed_dominant = (
        not np.isnan(med_speed) and not np.isnan(med_family)
        and med_speed > med_family
    )

    verdict = "YES" if speed_dominant else "NO"
    margin = abs(med_speed - med_family) if not np.isnan(med_speed) and not np.isnan(med_family) else float("nan")

    lines.append(f"VERDICT: Speed-dominant clustering? {verdict}")
    if not np.isnan(margin):
        lines.append(
            f"  (same-speed median={med_speed:+.3f} vs same-family median={med_family:+.3f}, "
            f"margin={margin:.3f})"
        )
        if speed_dominant:
            lines.append(
                "  Carver's hypothesis SUPPORTED: fast rules correlate more with fast rules "
                "of other families than with slow rules of the same family."
            )
        else:
            lines.append(
                "  Carver's hypothesis NOT supported: family membership is more predictive "
                "of correlation than speed tier."
            )

    # --- Comparison to current weighting structure ---
    lines.append("\n" + "=" * 72)
    lines.append("CLUSTER STRUCTURE vs CURRENT WEIGHTING")
    lines.append("-" * 72)

    # Group current weights by cluster
    forecast_weights_raw = {}
    try:
        # Try to read from system if available globally
        pass
    except Exception:
        pass

    for cl_id in sorted(cluster_to_rules):
        members = cluster_to_rules[cl_id]
        speeds_in = sorted(set(RULE_META.get(r, {}).get("speed", "?") for r in members))
        families_in = sorted(set(RULE_META.get(r, {}).get("family", "?") for r in members))
        lines.append(f"\nCluster {cl_id}: {', '.join(members[:4])}{'...' if len(members)>4 else ''}")
        lines.append(f"  Speed tiers: {speeds_in}")
        lines.append(f"  Families: {families_in}")
        if len(speeds_in) == 1 and len(families_in) > 1:
            lines.append("  ✓ Speed-homogeneous, cross-family → Carver clustering confirmed")
        elif len(families_in) == 1 and len(speeds_in) > 1:
            lines.append("  ✓ Family-homogeneous, cross-speed → traditional family structure confirmed")
        elif len(speeds_in) == 1 and len(families_in) == 1:
            lines.append("  — Single family+speed group")
        else:
            lines.append("  ~ Mixed cluster (both speed and family mix)")

    lines.append("\n" + "=" * 72)

    summary = "\n".join(lines)
    out_path = outdir / "summary.txt"
    out_path.write_text(summary)
    logger.info(f"  Saved: {out_path}")
    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Rule correlation clustering diagnostic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/crypto_perps_full_rules.yaml"),
        help="Path to config YAML",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/dataset_538registry_6yr_jagged.parquet"),
        help="Path to parquet dataset",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("out/rule_correlations"),
        help="Output directory for PNG and summary files",
    )
    args = parser.parse_args()

    if not args.config.exists():
        logger.error(f"Config not found: {args.config}")
        sys.exit(1)
    if not args.data.exists():
        logger.error(f"Data file not found: {args.data}")
        sys.exit(1)

    args.outdir.mkdir(parents=True, exist_ok=True)

    # --- Build system ---
    logger.info("Building system (this may take a few minutes)...")
    system = build_system(str(args.config), str(args.data))

    # --- Extract rule P&L ---
    logger.info("\nExtracting per-rule P&L (this is the slow step ~10-20 min)...")
    returns_df = extract_rule_returns(system)

    n_rules = len(returns_df.columns)
    n_dates = len(returns_df)
    logger.info(f"  returns_df shape: {n_dates} × {n_rules}")

    # Verify
    assert returns_df.shape[1] > 0, "No rule returns extracted"

    # --- Correlation + clustering ---
    logger.info("\nComputing correlations and hierarchical clustering...")
    corr, dist_np, linkage = compute_correlation_and_linkage(returns_df)

    # Sanity checks
    diag = np.diag(corr.values)
    assert np.allclose(diag[~np.isnan(diag)], 1.0, atol=1e-6), "Diagonal != 1"
    assert np.allclose(corr.values, corr.values.T, equal_nan=True, atol=1e-10), "Corr not symmetric"
    logger.info("  Correlation matrix: valid (symmetric, diagonal=1)")

    rule_names = list(corr.columns)

    # --- Save outputs ---
    logger.info("\nSaving outputs...")
    save_dendrogram(linkage, rule_names, args.outdir)
    save_heatmap(corr, linkage, args.outdir)
    summary = build_summary(corr, linkage, returns_df, args.outdir)

    # Print summary to stdout
    print("\n" + summary)

    logger.info("\nDone. Outputs in: " + str(args.outdir))
    logger.info("  dendrogram.png")
    logger.info("  heatmap.png")
    logger.info("  summary.txt")


if __name__ == "__main__":
    main()
