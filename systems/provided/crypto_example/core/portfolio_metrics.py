"""
Portfolio Metrics Calculator
=============================
Comprehensive metrics for evaluating trading strategies and portfolios.

Includes:
- Core metrics: CAGR, vol, Sharpe, max drawdown, Calmar
- Distribution metrics: Skew, kurtosis, worst month/week
- Crisis performance: Returns during specific market regimes
- Market exposure: Correlation, beta to BTC
- Marginal contribution analysis
"""

import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis


DAYS_PER_YEAR = 365
WEEKS_PER_YEAR = 52
MONTHS_PER_YEAR = 12


def calculate_core_metrics(returns: pd.Series, name: str = "Strategy") -> dict:
    """
    Calculate core performance metrics.

    Args:
        returns: Daily percentage returns (as decimals, not %)
        name: Strategy name for display

    Returns:
        dict with keys:
            - name: Strategy name
            - cagr: Compound annual growth rate
            - ann_return: Annualized return (arithmetic mean)
            - ann_vol: Annualized volatility
            - sharpe: Sharpe ratio
            - max_dd: Maximum drawdown
            - calmar: Calmar ratio (CAGR / abs(max_dd))
            - skew: Return skewness
            - kurtosis: Return kurtosis
            - days: Number of trading days
            - start_date: First date
            - end_date: Last date
    """

    returns_clean = returns.dropna()

    if len(returns_clean) < 20:
        return {
            'name': name,
            'error': 'Insufficient data (< 20 days)'
        }

    # Cumulative returns
    cum_returns = (1 + returns_clean).cumprod()

    # CAGR
    total_return = cum_returns.iloc[-1] - 1
    years = len(returns_clean) / DAYS_PER_YEAR
    cagr = (1 + total_return) ** (1 / years) - 1

    # Annualized metrics
    ann_return = returns_clean.mean() * DAYS_PER_YEAR
    ann_vol = returns_clean.std() * np.sqrt(DAYS_PER_YEAR)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0

    # Drawdown
    running_max = cum_returns.cummax()
    drawdown = (cum_returns - running_max) / running_max
    max_dd = drawdown.min()

    # Calmar ratio
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    # Distribution moments
    ret_skew = skew(returns_clean)
    ret_kurt = kurtosis(returns_clean)

    return {
        'name': name,
        'cagr': cagr,
        'ann_return': ann_return,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'skew': ret_skew,
        'kurtosis': ret_kurt,
        'days': len(returns_clean),
        'start_date': returns_clean.index.min(),
        'end_date': returns_clean.index.max()
    }


def calculate_tail_metrics(returns: pd.Series) -> dict:
    """
    Calculate tail risk metrics.

    Args:
        returns: Daily percentage returns

    Returns:
        dict with keys:
            - worst_day: Worst single day return
            - worst_week: Worst weekly return (compounded)
            - worst_month: Worst monthly return (compounded)
            - pct_5: 5th percentile (worst 5% of days)
            - pct_95: 95th percentile (best 5% of days)
    """

    returns_clean = returns.dropna()

    # Worst single day
    worst_day = returns_clean.min()

    # Worst week (rolling 7-day COMPOUNDED)
    weekly_rets = returns_clean.rolling(7).apply(lambda x: (1 + x).prod() - 1, raw=True)
    worst_week = weekly_rets.min() if len(weekly_rets) > 0 else np.nan

    # Worst month (rolling 30-day COMPOUNDED)
    monthly_rets = returns_clean.rolling(30).apply(lambda x: (1 + x).prod() - 1, raw=True)
    worst_month = monthly_rets.min() if len(monthly_rets) > 0 else np.nan

    # Percentiles
    pct_5 = returns_clean.quantile(0.05)
    pct_95 = returns_clean.quantile(0.95)

    return {
        'worst_day': worst_day,
        'worst_week': worst_week,
        'worst_month': worst_month,
        'pct_5': pct_5,
        'pct_95': pct_95
    }


def calculate_expected_shortfall(returns: pd.Series, confidence: float = 0.95) -> dict:
    """
    Calculate VaR and Expected Shortfall (CVaR).

    Expected Shortfall (ES) is the mean return given that the loss exceeds VaR.
    It's a more robust tail risk measure than VaR alone because it captures
    the average severity of tail losses, not just the threshold.

    Args:
        returns: Daily percentage returns (as decimals)
        confidence: Confidence level (0.95 = 95%, 0.99 = 99%)

    Returns:
        dict with keys:
            - var: Value at Risk (percentile threshold)
            - es: Expected Shortfall (mean of tail beyond VaR)
    """
    returns_clean = returns.dropna()

    if len(returns_clean) < 20:
        return {'var': np.nan, 'es': np.nan}

    # VaR: percentile threshold
    var = returns_clean.quantile(1 - confidence)

    # ES: mean of returns below VaR
    tail_returns = returns_clean[returns_clean <= var]
    es = tail_returns.mean() if len(tail_returns) > 0 else var

    return {'var': var, 'es': es}


def calculate_drawdown_duration(returns: pd.Series) -> dict:
    """
    Calculate drawdown duration metrics.

    Drawdown duration measures how long it takes to recover from losses,
    which is often more important than drawdown depth for practical trading.
    Long drawdown durations indicate slow recovery and can be psychologically
    difficult even if the maximum drawdown isn't severe.

    Args:
        returns: Daily percentage returns (as decimals)

    Returns:
        dict with keys:
            - max_dd_duration: Longest drawdown period (days)
            - avg_dd_duration: Average drawdown period (days)
            - num_drawdowns: Number of distinct drawdown periods
    """
    returns_clean = returns.dropna()

    if len(returns_clean) < 20:
        return {'max_dd_duration': 0, 'avg_dd_duration': 0, 'num_drawdowns': 0}

    cum_returns = (1 + returns_clean).cumprod()
    running_max = cum_returns.cummax()

    # Identify drawdown periods (when below running max)
    in_drawdown = cum_returns < running_max

    # Find continuous drawdown periods
    drawdown_starts = (~in_drawdown.shift(1, fill_value=False)) & in_drawdown
    drawdown_ends = in_drawdown & (~in_drawdown.shift(-1, fill_value=False))

    # Calculate durations
    durations = []
    start_indices = in_drawdown[drawdown_starts].index
    end_indices = in_drawdown[drawdown_ends].index

    for start, end in zip(start_indices, end_indices):
        duration = (end - start).days
        durations.append(duration)

    if len(durations) == 0:
        return {'max_dd_duration': 0, 'avg_dd_duration': 0, 'num_drawdowns': 0}

    return {
        'max_dd_duration': max(durations),
        'avg_dd_duration': sum(durations) / len(durations),
        'num_drawdowns': len(durations)
    }


def calculate_crisis_performance(
    returns: pd.Series,
    crisis_start: str = '2022-01-01',
    crisis_end: str = '2022-12-31',
    crisis_name: str = '2022 Crypto Bear'
) -> dict:
    """
    Calculate performance during a specific crisis window.

    Args:
        returns: Daily percentage returns
        crisis_start: Start date of crisis window
        crisis_end: End date of crisis window
        crisis_name: Name of crisis for display

    Returns:
        dict with keys:
            - crisis_name: Name of crisis window
            - crisis_return: Total return during crisis
            - crisis_vol: Annualized vol during crisis
            - crisis_sharpe: Sharpe during crisis
            - crisis_max_dd: Max drawdown during crisis
            - crisis_days: Number of days in crisis window
    """

    # Filter to crisis window
    crisis_rets = returns[(returns.index >= crisis_start) & (returns.index <= crisis_end)]

    if len(crisis_rets) < 10:
        return {
            'crisis_name': crisis_name,
            'crisis_return': np.nan,
            'crisis_vol': np.nan,
            'crisis_sharpe': np.nan,
            'crisis_max_dd': np.nan,
            'crisis_days': 0
        }

    # Total return
    crisis_total_return = (1 + crisis_rets).prod() - 1

    # Vol and Sharpe
    crisis_vol = crisis_rets.std() * np.sqrt(DAYS_PER_YEAR)
    crisis_sharpe = (crisis_rets.mean() * DAYS_PER_YEAR) / crisis_vol if crisis_vol > 0 else 0

    # Drawdown
    crisis_cum = (1 + crisis_rets).cumprod()
    crisis_running_max = crisis_cum.cummax()
    crisis_dd = (crisis_cum - crisis_running_max) / crisis_running_max
    crisis_max_dd = crisis_dd.min()

    return {
        'crisis_name': crisis_name,
        'crisis_return': crisis_total_return,
        'crisis_vol': crisis_vol,
        'crisis_sharpe': crisis_sharpe,
        'crisis_max_dd': crisis_max_dd,
        'crisis_days': len(crisis_rets)
    }


def calculate_market_exposure(
    strategy_returns: pd.Series,
    market_returns: pd.Series,
    market_name: str = 'BTC'
) -> dict:
    """
    Calculate correlation and beta to a market benchmark.

    Beta = Cov(Strategy, Market) / Var(Market)
    - Beta > 1: Strategy is more volatile than market
    - Beta ≈ 1: Strategy moves with market
    - Beta ≈ 0: Strategy is market-neutral
    - Beta < 0: Strategy is inversely correlated

    Args:
        strategy_returns: Daily percentage returns for strategy
        market_returns: Daily percentage returns for market (e.g., BTC)
        market_name: Name of market for display

    Returns:
        dict with keys:
            - market_name: Market benchmark name
            - correlation: Correlation coefficient
            - beta: Beta coefficient
            - days: Number of overlapping days
    """

    # Align on common dates
    common_dates = strategy_returns.index.intersection(market_returns.index)
    strat_aligned = strategy_returns.loc[common_dates]
    mkt_aligned = market_returns.loc[common_dates]

    if len(common_dates) < 20:
        return {
            'market_name': market_name,
            'correlation': np.nan,
            'beta': np.nan,
            'days': 0
        }

    # Correlation
    correlation = strat_aligned.corr(mkt_aligned)

    # Beta
    covariance = strat_aligned.cov(mkt_aligned)
    variance_mkt = mkt_aligned.var()
    beta = covariance / variance_mkt if variance_mkt > 0 else 0

    return {
        'market_name': market_name,
        'correlation': correlation,
        'beta': beta,
        'days': len(common_dates)
    }


def calculate_marginal_contribution(
    combined_metrics: dict,
    baseline_metrics: dict,
    added_strategy_name: str = 'Added Strategy'
) -> dict:
    """
    Calculate the marginal contribution of adding a strategy to a portfolio.

    Marginal metrics show how much a strategy improves (or hurts) the portfolio:
    - Marginal Sharpe = Combined Sharpe - Baseline Sharpe
    - Marginal Max DD = Combined Max DD - Baseline Max DD (negative is better)
    - Marginal CAGR = Combined CAGR - Baseline CAGR

    Args:
        combined_metrics: Metrics for baseline + added strategy
        baseline_metrics: Metrics for baseline only
        added_strategy_name: Name of added strategy

    Returns:
        dict with keys:
            - strategy_name: Name of added strategy
            - marginal_sharpe: Change in Sharpe
            - marginal_cagr: Change in CAGR
            - marginal_max_dd: Change in max drawdown (negative is better)
            - marginal_calmar: Change in Calmar
            - marginal_vol: Change in volatility
    """

    return {
        'strategy_name': added_strategy_name,
        'marginal_sharpe': combined_metrics['sharpe'] - baseline_metrics['sharpe'],
        'marginal_cagr': combined_metrics['cagr'] - baseline_metrics['cagr'],
        'marginal_max_dd': combined_metrics['max_dd'] - baseline_metrics['max_dd'],
        'marginal_calmar': combined_metrics['calmar'] - baseline_metrics['calmar'],
        'marginal_vol': combined_metrics['ann_vol'] - baseline_metrics['ann_vol']
    }


def calculate_all_metrics(
    returns: pd.Series,
    name: str = "Strategy",
    market_returns: pd.Series = None,
    market_name: str = 'BTC'
) -> dict:
    """
    Calculate all metrics for a strategy.

    Args:
        returns: Daily percentage returns
        name: Strategy name
        market_returns: Optional market benchmark returns for beta/correlation
        market_name: Name of market benchmark

    Returns:
        dict with all metrics combined
    """

    # Core metrics
    core = calculate_core_metrics(returns, name)

    # Tail metrics (fixed with compounded returns)
    tail = calculate_tail_metrics(returns)

    # Expected Shortfall at 95% and 99%
    es95 = calculate_expected_shortfall(returns, confidence=0.95)
    es99 = calculate_expected_shortfall(returns, confidence=0.99)

    # Drawdown duration
    dd_duration = calculate_drawdown_duration(returns)

    # Crisis metrics (2022 crypto bear)
    crisis = calculate_crisis_performance(returns)

    # Combine
    all_metrics = {
        **core,
        **tail,
        'var95': es95['var'],
        'es95': es95['es'],
        'var99': es99['var'],
        'es99': es99['es'],
        **dd_duration,
        **crisis
    }

    # Market exposure (if provided)
    if market_returns is not None:
        market = calculate_market_exposure(returns, market_returns, market_name)
        all_metrics.update(market)

    return all_metrics


def format_metrics_table(metrics_list: list, format: str = 'markdown') -> str:
    """
    Format a list of metrics dicts as a table.

    Args:
        metrics_list: List of metrics dicts from calculate_all_metrics()
        format: 'markdown' or 'csv'

    Returns:
        str: Formatted table
    """

    if len(metrics_list) == 0:
        return "No metrics to display"

    # Define columns to display
    columns = [
        ('name', 'Case'),
        ('cagr', 'CAGR'),
        ('ann_vol', 'Vol'),
        ('sharpe', 'Sharpe'),
        ('max_dd', 'MaxDD'),
        ('max_dd_duration', 'MaxDD Days'),
        ('es95', 'ES95'),
        ('es99', 'ES99'),
        ('worst_month', 'Worst Mo'),
        ('crisis_return', 'Crisis Ret'),
    ]

    # Check if market exposure is available
    if 'correlation' in metrics_list[0]:
        columns.append(('correlation', 'Corr BTC'))
        columns.append(('beta', 'Beta BTC'))

    if format == 'csv':
        # CSV format
        header = ','.join([col[1] for col in columns])
        rows = []
        for m in metrics_list:
            row_vals = []
            for key, _ in columns:
                val = m.get(key, '')
                if isinstance(val, float):
                    if 'sharpe' in key or 'calmar' in key or 'beta' in key or 'correlation' in key or 'skew' in key:
                        row_vals.append(f"{val:.2f}")
                    elif 'dd' in key or 'vol' in key or 'cagr' in key or 'return' in key or 'es' in key or 'var' in key or 'worst' in key or 'pct' in key:
                        row_vals.append(f"{val*100:.1f}%")
                    else:
                        row_vals.append(f"{val:.2f}")
                elif isinstance(val, int) or (isinstance(val, float) and 'duration' in key):
                    row_vals.append(f"{int(val)}")
                else:
                    row_vals.append(str(val))
            rows.append(','.join(row_vals))
        return header + '\n' + '\n'.join(rows)

    else:
        # Markdown format
        header_names = [col[1] for col in columns]
        separator = ['---' for _ in columns]

        lines = []
        lines.append('| ' + ' | '.join(header_names) + ' |')
        lines.append('| ' + ' | '.join(separator) + ' |')

        for m in metrics_list:
            row_vals = []
            for key, _ in columns:
                val = m.get(key, '')
                if isinstance(val, float):
                    if 'sharpe' in key or 'calmar' in key or 'beta' in key or 'correlation' in key or 'skew' in key:
                        row_vals.append(f"{val:.2f}")
                    elif 'dd' in key or 'vol' in key or 'cagr' in key or 'return' in key or 'es' in key or 'var' in key or 'worst' in key or 'pct' in key:
                        row_vals.append(f"{val*100:.1f}%")
                    else:
                        row_vals.append(f"{val:.2f}")
                elif isinstance(val, int) or (isinstance(val, float) and 'duration' in key):
                    row_vals.append(f"{int(val)}")
                else:
                    row_vals.append(str(val))
            lines.append('| ' + ' | '.join(row_vals) + ' |')

        return '\n'.join(lines)


# =============================================================================
# MAIN (for testing)
# =============================================================================

if __name__ == "__main__":
    print("=" * 90)
    print("TESTING PORTFOLIO METRICS")
    print("=" * 90)

    # Create dummy returns
    dates = pd.date_range('2020-01-01', '2025-12-31', freq='D')
    np.random.seed(42)

    # Strategy with Sharpe 1.5, 25% vol
    returns = pd.Series(
        np.random.normal(0.001, 0.013, len(dates)),
        index=dates
    )

    # Market (BTC proxy) with Sharpe 0.8, 70% vol
    market = pd.Series(
        np.random.normal(0.0008, 0.037, len(dates)),
        index=dates
    )

    # Calculate all metrics
    print("\nCalculating all metrics...")
    metrics = calculate_all_metrics(
        returns=returns,
        name='Test Strategy',
        market_returns=market,
        market_name='BTC'
    )

    # Display
    print("\n" + "-" * 90)
    for key, val in metrics.items():
        if isinstance(val, float):
            if 'date' in key:
                continue
            elif 'sharpe' in key or 'calmar' in key or 'beta' in key or 'correlation' in key:
                print(f"  {key:20s}: {val:7.2f}")
            elif 'dd' in key or 'vol' in key or 'cagr' in key or 'return' in key or 'pct' in key or 'worst' in key:
                print(f"  {key:20s}: {val*100:6.1f}%")
            else:
                print(f"  {key:20s}: {val}")
        elif 'date' in key:
            print(f"  {key:20s}: {val.date()}")
        else:
            print(f"  {key:20s}: {val}")

    # Test table formatting
    print("\n" + "=" * 90)
    print("MARKDOWN TABLE:")
    print("=" * 90)
    table = format_metrics_table([metrics], format='markdown')
    print(table)

    print("\n" + "=" * 90)
    print("✓ Portfolio metrics tests complete")
    print("=" * 90)
