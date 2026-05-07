"""Unit tests for the C2a/C2b portfolio-broadcast macro-signal rules."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from systems.crypto_perps.rules.rule_library import (
    basis_mr,
    btc_etf_flow_trend,
    stablecoin_supply_trend,
)


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_aux_data(filename: str) -> Path | None:
    """env-first, repo-fallback resolution mirroring production
    (`sysdata.crypto.required_data._resolve_path`). Production migrated
    auxiliary feeds from `data/` (repo root) to `envs/dev/data/` per
    `docs/AUXILIARY_DATA_FRESHNESS.md`; this resolver keeps the smoke
    tests pointed at whichever location actually has the file. Returns
    None when neither location has it (caller can pytest.skip).

    Pre-fix this resolver was not used and tests under `data/<file>`
    silently skipped on every dev run after the migration (audit F14,
    2026-05-06)."""
    env_path = _REPO_ROOT / "envs" / "dev" / "data" / filename
    if env_path.exists():
        return env_path
    repo_path = _REPO_ROOT / "data" / filename
    if repo_path.exists():
        return repo_path
    return None


def _price_index(start="2020-01-01", end="2026-05-01") -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq="D")


class TestStablecoinSupplyTrend:
    def test_empty_when_too_short(self):
        idx = _price_index()
        price = pd.Series(1.0, index=idx)
        # Only 50 days of supply data → 50 < 4*32, returns empty
        supply = pd.Series([1e10 + i * 1e6 for i in range(50)],
                           index=pd.date_range("2024-01-01", periods=50, freq="D"))
        fc = stablecoin_supply_trend(price, supply, Lfast=32)
        assert fc.empty or fc.isna().all()

    def test_growing_supply_yields_long_forecast(self):
        # 6-month linear ramp in supply → fast EMA > slow EMA → positive forecast
        n = 252
        supply = pd.Series(np.linspace(2e11, 3e11, n),
                           index=pd.date_range("2024-01-01", periods=n, freq="D"))
        price = pd.Series(1.0, index=supply.index)
        fc = stablecoin_supply_trend(price, supply, Lfast=32)
        recent = fc.dropna().iloc[-30:]
        assert (recent > 0).all(), "Growing supply should produce positive forecasts"

    def test_shrinking_supply_yields_short_forecast(self):
        n = 252
        supply = pd.Series(np.linspace(3e11, 2e11, n),
                           index=pd.date_range("2024-01-01", periods=n, freq="D"))
        price = pd.Series(1.0, index=supply.index)
        fc = stablecoin_supply_trend(price, supply, Lfast=32)
        recent = fc.dropna().iloc[-30:]
        assert (recent < 0).all(), "Shrinking supply should produce negative forecasts"

    def test_real_data_smoke(self):
        # The actual DefiLlama snapshot — a smoke test that the rule produces sane values.
        path = _resolve_aux_data("stablecoin_supply.parquet")
        if path is None:
            pytest.skip("stablecoin_supply.parquet not present in envs/dev/data/ or data/ (run download_stablecoin_supply.py)")
        supply = pd.read_parquet(path)["stablecoin_supply_usd"]
        price = pd.Series(1.0, index=_price_index())
        fc = stablecoin_supply_trend(price, supply, Lfast=32)
        assert not fc.empty
        assert fc.notna().sum() > 1000


class TestBtcEtfFlowTrend:
    def test_empty_when_too_short(self):
        idx = _price_index()
        price = pd.Series(1.0, index=idx)
        signed = pd.Series(np.random.randn(50) * 1e8,
                           index=pd.date_range("2024-01-11", periods=50, freq="D"))
        fc = btc_etf_flow_trend(price, signed, Lfast=20)
        assert fc.empty or fc.isna().all()

    def test_persistent_inflows_yield_long_forecast(self):
        n = 252
        # Strictly positive signed volume = persistent inflows over the window
        signed = pd.Series(np.abs(np.random.RandomState(0).randn(n)) * 1e9,
                           index=pd.date_range("2024-01-11", periods=n, freq="D"))
        price = pd.Series(1.0, index=signed.index)
        fc = btc_etf_flow_trend(price, signed, Lfast=20)
        recent = fc.dropna().iloc[-30:]
        assert (recent > 0).all(), "Persistent inflows should produce positive forecasts"

    def test_persistent_outflows_yield_short_forecast(self):
        n = 252
        signed = pd.Series(-np.abs(np.random.RandomState(1).randn(n)) * 1e9,
                           index=pd.date_range("2024-01-11", periods=n, freq="D"))
        price = pd.Series(1.0, index=signed.index)
        fc = btc_etf_flow_trend(price, signed, Lfast=20)
        recent = fc.dropna().iloc[-30:]
        assert (recent < 0).all(), "Persistent outflows should produce negative forecasts"

    def test_pre_launch_dates_are_nan_etf(self):
        # Forecast on a 2020 date should be NaN since ETF data starts 2024-01-11
        path = _resolve_aux_data("etf_flows.parquet")
        if path is None:
            pytest.skip("etf_flows.parquet not present in envs/dev/data/ or data/ (run download_etf_flows.py)")
        signed = pd.read_parquet(path)["btc_etf_signed_volume"]
        price = pd.Series(1.0, index=_price_index())
        fc = btc_etf_flow_trend(price, signed, Lfast=20)
        # Pre-2024 should all be NaN (no ETF data)
        pre_launch = fc.loc["2020-01-01":"2023-12-31"]
        assert pre_launch.isna().all(), "Pre-launch period should produce NaN forecasts"
        # Post-launch, after warmup, should have non-NaN values
        post_launch = fc.loc["2024-06-01":].dropna()
        assert len(post_launch) > 100, "Should have substantial coverage after warmup"
        # Forecasts are in the [-20, +20] range after the .clip(-2,2)*10 normalization
        assert post_launch.between(-20.01, 20.01).all()


class TestBasisMr:
    def test_empty_when_too_short(self):
        idx = _price_index()
        price = pd.Series(1.0, index=idx)
        # Only 5 days of basis data, lookback=5 → not enough warmup
        basis = pd.Series([0.001] * 5, index=pd.date_range("2024-01-01", periods=5, freq="D"))
        fc = basis_mr(price, basis, lookback=5, threshold_bp=50.0)
        assert fc.empty or fc.isna().all()

    def test_inside_deadband_zero_forecast(self):
        idx = pd.date_range("2024-01-01", periods=60, freq="D")
        price = pd.Series(1.0, index=idx)
        # Basis hovers at 20bp = 0.002 — inside ±50bp deadband
        basis = pd.Series(0.002, index=idx)
        fc = basis_mr(price, basis, lookback=5, threshold_bp=50.0)
        recent = fc.dropna().iloc[-30:]
        # Inside the deadband → forecast = 0 (sign of 0 is 0)
        assert (recent.abs() < 1e-9).all()

    def test_positive_basis_yields_short_forecast(self):
        idx = pd.date_range("2024-01-01", periods=60, freq="D")
        price = pd.Series(1.0, index=idx)
        # Basis at 200bp = 0.02 (well above 50bp threshold) → SHORT
        basis = pd.Series(0.02, index=idx)
        fc = basis_mr(price, basis, lookback=5, threshold_bp=50.0)
        recent = fc.dropna().iloc[-30:]
        assert (recent < 0).all(), "Persistent positive basis should produce SHORT forecasts"
        # 200bp is at 4× threshold; the rule saturates at +/-20 by 3× threshold (150bp)
        assert (recent.abs() == 20.0).all(), "Should saturate at ±20"

    def test_negative_basis_yields_long_forecast(self):
        idx = pd.date_range("2024-01-01", periods=60, freq="D")
        price = pd.Series(1.0, index=idx)
        # Basis at -100bp = -0.01 (above 50bp threshold in absolute value) → LONG
        basis = pd.Series(-0.01, index=idx)
        fc = basis_mr(price, basis, lookback=5, threshold_bp=50.0)
        recent = fc.dropna().iloc[-30:]
        assert (recent > 0).all(), "Persistent negative basis should produce LONG forecasts"

    def test_forecast_scales_linearly_in_excess(self):
        """At threshold: forecast=0. At 3×threshold: forecast=±20. Linear in between."""
        idx = pd.date_range("2024-01-01", periods=20, freq="D")
        price = pd.Series(1.0, index=idx)
        # Constant basis = 100bp = 2× threshold of 50bp
        # excess = (100 - 50) / 100 = 0.5 of the [threshold, 3×threshold] band
        # → magnitude = 0.5 × 20 = 10
        basis = pd.Series(0.01, index=idx)
        fc = basis_mr(price, basis, lookback=5, threshold_bp=50.0)
        recent = fc.dropna().iloc[-5:]
        assert (recent == -10.0).all(), f"Expected -10.0, got {recent.tolist()}"
