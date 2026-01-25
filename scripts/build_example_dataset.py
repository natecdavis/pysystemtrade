#!/usr/bin/env python3
"""
Build example crypto perpetual futures dataset

For Phase 1 MVP, generates synthetic data with realistic characteristics.
In production, this would load from raw data files in data/raw/

Output: data/example_crypto_perps.parquet with schema:
    - date: UTC date
    - instrument: instrument code (e.g., BTCUSDT_PERP)
    - close: close price
    - funding_rate: funding rate (applies from close(t-1) to close(t))
    - adv_notional: average daily volume (notional)
    - spread_frac: bid-ask spread as fraction (fixed placeholder for Phase 1)
    - taker_fee_frac: taker fee as fraction (fixed placeholder for Phase 1)
"""

import pandas as pd
import numpy as np
from pathlib import Path


def generate_synthetic_crypto_data(
    instruments: list,
    start_date: str = "2023-01-01",
    end_date: str = "2024-12-31",
    seed: int = 42
) -> pd.DataFrame:
    """
    Generate synthetic crypto perpetual futures data for testing

    Args:
        instruments: List of instrument codes
        start_date: Start date for data
        end_date: End date for data
        seed: Random seed for reproducibility

    Returns:
        DataFrame with all required fields in long format
    """
    np.random.seed(seed)

    # Generate daily date range (UTC)
    dates = pd.date_range(start=start_date, end=end_date, freq='D', tz='UTC')
    dates = dates.tz_localize(None)  # Remove timezone for simplicity

    # Initial prices for each instrument (approximate realistic values)
    initial_prices = {
        'BTCUSDT_PERP': 20000.0,
        'ETHUSDT_PERP': 1500.0,
        'BNBUSDT_PERP': 300.0,
        'SOLUSDT_PERP': 20.0,
        'XRPUSDT_PERP': 0.4
    }

    # Generate data for each instrument
    all_data = []

    for inst in instruments:
        n_days = len(dates)
        initial_price = initial_prices.get(inst, 100.0)

        # Generate realistic daily returns (crypto-like volatility)
        # Annual vol ~80%, daily vol ~5%
        daily_vol = 0.05
        daily_returns = np.random.normal(0.0001, daily_vol, n_days)  # Slight upward drift

        # Generate price series
        log_prices = np.cumsum(daily_returns)
        prices = initial_price * np.exp(log_prices)

        # Generate funding rates (typically small, mean-reverting around 0.01% per 8h)
        # Daily funding = 3x 8-hour funding periods
        # Typical range: -0.05% to +0.15% per day (annualized ~-20% to +50%)
        funding_mean = 0.0001  # 0.01% per day
        funding_vol = 0.0005   # Small volatility
        funding_rates = np.random.normal(funding_mean, funding_vol, n_days)

        # Generate ADV (average daily volume in notional)
        # Larger for BTC/ETH, smaller for others
        base_adv = {
            'BTCUSDT_PERP': 1e10,  # $10B
            'ETHUSDT_PERP': 5e9,   # $5B
            'BNBUSDT_PERP': 1e9,   # $1B
            'SOLUSDT_PERP': 5e8,   # $500M
            'XRPUSDT_PERP': 3e8    # $300M
        }
        mean_adv = base_adv.get(inst, 1e8)
        # Add some variation (±30%)
        adv_notional = mean_adv * (1 + np.random.uniform(-0.3, 0.3, n_days))

        # Fixed cost parameters for Phase 1 (placeholders)
        spread_frac = np.full(n_days, 0.0003)  # 3 bps
        taker_fee_frac = np.full(n_days, 0.0004)  # 4 bps (typical Binance taker fee)

        # Create DataFrame for this instrument
        inst_df = pd.DataFrame({
            'date': dates,
            'instrument': inst,
            'close': prices,
            'funding_rate': funding_rates,
            'adv_notional': adv_notional,
            'spread_frac': spread_frac,
            'taker_fee_frac': taker_fee_frac
        })

        all_data.append(inst_df)

    # Concatenate all instruments
    df = pd.concat(all_data, ignore_index=True)

    return df


def main():
    """
    Build example dataset and save to parquet
    """
    # Define Layer A instruments (top 5 by ADV for Phase 1)
    instruments = [
        'BTCUSDT_PERP',
        'ETHUSDT_PERP',
        'BNBUSDT_PERP',
        'SOLUSDT_PERP',
        'XRPUSDT_PERP'
    ]

    print("Generating synthetic crypto perpetual futures data...")
    df = generate_synthetic_crypto_data(instruments)

    # Save to parquet
    output_path = Path(__file__).parent.parent / 'data' / 'example_crypto_perps.parquet'
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Saving to {output_path}...")
    df.to_parquet(output_path, index=False)

    print(f"Dataset created successfully!")
    print(f"  Date range: {df['date'].min()} to {df['date'].max()}")
    print(f"  Instruments: {df['instrument'].unique().tolist()}")
    print(f"  Total rows: {len(df)}")
    print(f"  Rows per instrument: {len(df) // len(instruments)}")


if __name__ == '__main__':
    main()
