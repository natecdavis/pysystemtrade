#!/usr/bin/env python3
"""
Generate promotion shortlist from dataset manifests and backtest results.

Purpose:
    Rank candidate instruments based on:
    - Data quality (from manifest)
    - Performance contribution (from backtest results)
    - Diversification benefit

Output:
    Ranked shortlist of candidates for potential promotion to production.
    NO AUTO-PROMOTION - reporting only.

Usage:
    python scripts/generate_promotion_shortlist.py \
        --manifest research/datasets/candidates_20_2024Q4.manifest.json \
        --backtest-dir research/backtests/2024Q4_20inst \
        --baseline-dir research/backtests/2024Q4_5inst \
        --output research/reports/promotion_shortlist_2024Q4.json
"""

import argparse
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Dict, List
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_manifest(manifest_path: Path) -> dict:
    """Load dataset manifest."""
    with open(manifest_path) as f:
        return json.load(f)


def load_backtest_metrics(backtest_dir: Path) -> dict:
    """
    Load backtest results and compute per-instrument metrics.

    Returns:
        {
            'portfolio_sharpe': float,
            'instruments': {
                inst_id: {
                    'avg_position': float,
                    'total_pnl': float,
                    'sharpe_contribution': float,  # Estimated
                    'active_days': int
                }
            }
        }
    """
    # Load equity curve
    eq_path = backtest_dir / 'equity_curve.csv'
    if not eq_path.exists():
        raise FileNotFoundError(f"Equity curve not found: {eq_path}")

    eq_df = pd.read_csv(eq_path)
    eq_df['date'] = pd.to_datetime(eq_df['date'])
    eq_df = eq_df.set_index('date')

    # Compute portfolio metrics
    eq_df['returns'] = eq_df['equity'].pct_change()
    portfolio_sharpe = eq_df['returns'].mean() / eq_df['returns'].std() * (252 ** 0.5) \
        if eq_df['returns'].std() > 0 else 0.0

    # Load positions
    pos_path = backtest_dir / 'positions.csv'
    if not pos_path.exists():
        raise FileNotFoundError(f"Positions file not found: {pos_path}")

    pos_df = pd.read_csv(pos_path)
    pos_df['date'] = pd.to_datetime(pos_df['date'])

    # Compute per-instrument metrics
    instruments = {}
    for inst_id in pos_df['instrument'].unique():
        inst_pos = pos_df[pos_df['instrument'] == inst_id]

        instruments[inst_id] = {
            'avg_position': float(inst_pos['position'].abs().mean()),
            'active_days': int((inst_pos['position'] != 0).sum()),
            'max_position': float(inst_pos['position'].abs().max()),
        }

    # Load PnL breakdown if available
    pnl_path = backtest_dir / 'pnl_breakdown.csv'
    if pnl_path.exists():
        pnl_df = pd.read_csv(pnl_path)
        pnl_df['date'] = pd.to_datetime(pnl_df['date'])

        for inst_id in instruments.keys():
            if inst_id in pnl_df.columns:
                inst_pnl = pnl_df[inst_id]
                instruments[inst_id]['total_pnl'] = float(inst_pnl.sum())
                instruments[inst_id]['pnl_volatility'] = float(inst_pnl.std())
            else:
                instruments[inst_id]['total_pnl'] = 0.0
                instruments[inst_id]['pnl_volatility'] = 0.0

    return {
        'portfolio_sharpe': portfolio_sharpe,
        'instruments': instruments
    }


def compute_data_quality_scores(manifest: dict) -> Dict[str, dict]:
    """
    Compute data quality scores from manifest.

    Returns:
        {
            inst_id: {
                'coverage_pct': float,
                'funding_coverage_pct': float,
                'coverage_days': int,
                'quality_score': float  # 0-100
            }
        }
    """
    scores = {}

    for inst_id, metadata in manifest['instruments']['included'].items():
        coverage_pct = metadata['coverage_pct']
        funding_coverage_pct = metadata['funding_coverage_pct']
        coverage_days = metadata['coverage_days']

        # Quality score: weighted average
        # - 50%: coverage_pct (data completeness)
        # - 30%: funding_coverage_pct (funding data availability)
        # - 20%: coverage_days >= 730 (2 years minimum)
        history_score = min(coverage_days / 730, 1.0) if coverage_days > 0 else 0.0
        quality_score = (
            0.50 * coverage_pct +
            0.30 * funding_coverage_pct +
            0.20 * history_score
        ) * 100

        scores[inst_id] = {
            'coverage_pct': coverage_pct,
            'funding_coverage_pct': funding_coverage_pct,
            'coverage_days': coverage_days,
            'quality_score': quality_score
        }

    return scores


def compute_marginal_contribution(
    baseline_sharpe: float,
    expanded_sharpe: float,
    num_new_instruments: int
) -> float:
    """
    Compute estimated marginal Sharpe contribution per new instrument.

    Args:
        baseline_sharpe: Sharpe of baseline portfolio (e.g., 5 instruments)
        expanded_sharpe: Sharpe of expanded portfolio (e.g., 20 instruments)
        num_new_instruments: Number of new instruments added (e.g., 15)

    Returns:
        Estimated marginal Sharpe contribution per instrument
    """
    if num_new_instruments == 0:
        return 0.0

    sharpe_delta = expanded_sharpe - baseline_sharpe
    marginal_contribution = sharpe_delta / num_new_instruments

    return marginal_contribution


def generate_shortlist(
    manifest: dict,
    expanded_metrics: dict,
    baseline_metrics: dict = None,
    min_quality_score: float = 70.0,
    min_active_days: int = 100
) -> List[dict]:
    """
    Generate ranked shortlist of candidate instruments.

    Ranking criteria:
    1. Data quality score (from manifest)
    2. Activity level (active_days from backtest)
    3. PnL contribution (total_pnl from backtest)
    4. Position sizing (avg_position - indicates strategy confidence)

    Returns:
        List of dicts (ranked by composite score, descending)
    """
    # Get baseline instrument set
    baseline_instruments = set()
    if baseline_metrics:
        baseline_instruments = set(baseline_metrics['instruments'].keys())

    # Compute data quality scores
    quality_scores = compute_data_quality_scores(manifest)

    # Compute marginal contribution (if baseline provided)
    marginal_sharpe = 0.0
    if baseline_metrics:
        num_new = len(expanded_metrics['instruments']) - len(baseline_instruments)
        marginal_sharpe = compute_marginal_contribution(
            baseline_metrics['portfolio_sharpe'],
            expanded_metrics['portfolio_sharpe'],
            num_new
        )

    # Build candidate list
    candidates = []
    for inst_id, perf in expanded_metrics['instruments'].items():
        # Skip baseline instruments (already in production)
        if inst_id in baseline_instruments:
            continue

        # Get quality scores
        quality = quality_scores.get(inst_id, {})
        quality_score = quality.get('quality_score', 0.0)

        # Skip if quality too low
        if quality_score < min_quality_score:
            logger.info(f"Skipping {inst_id}: quality score {quality_score:.1f} < {min_quality_score}")
            continue

        # Skip if too inactive
        active_days = perf.get('active_days', 0)
        if active_days < min_active_days:
            logger.info(f"Skipping {inst_id}: active days {active_days} < {min_active_days}")
            continue

        # Compute composite score
        # - 40%: data quality
        # - 30%: activity level (active_days / total_days)
        # - 20%: PnL contribution (normalized)
        # - 10%: avg position (strategy confidence)

        # Normalize metrics
        total_days = manifest['date_range']['total_days']
        activity_score = min(active_days / total_days, 1.0) * 100
        pnl_score = max(min(perf.get('total_pnl', 0) / 1000, 1.0), 0.0) * 100  # Cap at $1000
        position_score = min(perf.get('avg_position', 0) * 10, 100)  # Cap at 10 contracts

        composite_score = (
            0.40 * quality_score +
            0.30 * activity_score +
            0.20 * pnl_score +
            0.10 * position_score
        )

        candidates.append({
            'instrument': inst_id,
            'composite_score': composite_score,
            'quality_score': quality_score,
            'activity_score': activity_score,
            'pnl_score': pnl_score,
            'position_score': position_score,
            'data_quality': {
                'coverage_pct': quality.get('coverage_pct', 0.0),
                'funding_coverage_pct': quality.get('funding_coverage_pct', 0.0),
                'coverage_days': quality.get('coverage_days', 0)
            },
            'performance': {
                'avg_position': perf.get('avg_position', 0.0),
                'active_days': active_days,
                'total_pnl': perf.get('total_pnl', 0.0),
                'max_position': perf.get('max_position', 0.0)
            }
        })

    # Sort by composite score (descending)
    candidates.sort(key=lambda x: x['composite_score'], reverse=True)

    return candidates


def main():
    parser = argparse.ArgumentParser(
        description='Generate promotion shortlist from manifests and backtest results'
    )
    parser.add_argument(
        '--manifest',
        type=Path,
        required=True,
        help='Path to dataset manifest JSON'
    )
    parser.add_argument(
        '--backtest-dir',
        type=Path,
        required=True,
        help='Path to expanded backtest results directory (e.g., 20 instruments)'
    )
    parser.add_argument(
        '--baseline-dir',
        type=Path,
        help='Path to baseline backtest results directory (e.g., 5 instruments). Optional.'
    )
    parser.add_argument(
        '--output',
        type=Path,
        required=True,
        help='Path to save shortlist JSON'
    )
    parser.add_argument(
        '--min-quality-score',
        type=float,
        default=70.0,
        help='Minimum data quality score (0-100, default: 70)'
    )
    parser.add_argument(
        '--min-active-days',
        type=int,
        default=100,
        help='Minimum active trading days (default: 100)'
    )

    args = parser.parse_args()

    logger.info("Loading manifest...")
    manifest = load_manifest(args.manifest)
    logger.info(f"Manifest loaded: {manifest['summary']['included_count']} included instruments")

    logger.info("Loading expanded backtest metrics...")
    expanded_metrics = load_backtest_metrics(args.backtest_dir)
    logger.info(f"Expanded portfolio Sharpe: {expanded_metrics['portfolio_sharpe']:.2f}")

    baseline_metrics = None
    if args.baseline_dir:
        logger.info("Loading baseline backtest metrics...")
        baseline_metrics = load_backtest_metrics(args.baseline_dir)
        logger.info(f"Baseline portfolio Sharpe: {baseline_metrics['portfolio_sharpe']:.2f}")

    logger.info("Generating shortlist...")
    shortlist = generate_shortlist(
        manifest,
        expanded_metrics,
        baseline_metrics,
        min_quality_score=args.min_quality_score,
        min_active_days=args.min_active_days
    )

    # Build output report
    report = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'manifest_path': str(args.manifest),
        'backtest_dir': str(args.backtest_dir),
        'baseline_dir': str(args.baseline_dir) if args.baseline_dir else None,
        'filters': {
            'min_quality_score': args.min_quality_score,
            'min_active_days': args.min_active_days
        },
        'portfolio_metrics': {
            'expanded_sharpe': expanded_metrics['portfolio_sharpe'],
            'baseline_sharpe': baseline_metrics['portfolio_sharpe'] if baseline_metrics else None,
            'sharpe_improvement': expanded_metrics['portfolio_sharpe'] - baseline_metrics['portfolio_sharpe']
                if baseline_metrics else None
        },
        'shortlist_count': len(shortlist),
        'shortlist': shortlist
    }

    # Save report
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(report, f, indent=2)

    logger.info(f"Shortlist saved: {args.output}")
    logger.info(f"Shortlisted {len(shortlist)} candidates")

    # Print top 5
    print("\n=== Top 5 Candidates ===")
    for i, candidate in enumerate(shortlist[:5], 1):
        print(f"\n{i}. {candidate['instrument']}")
        print(f"   Composite Score: {candidate['composite_score']:.1f}/100")
        print(f"   Quality Score:   {candidate['quality_score']:.1f}/100")
        print(f"   Activity:        {candidate['performance']['active_days']} days")
        print(f"   Total PnL:       ${candidate['performance']['total_pnl']:.2f}")
        print(f"   Avg Position:    {candidate['performance']['avg_position']:.3f} contracts")

    print(f"\nFull report: {args.output}")


if __name__ == '__main__':
    main()
