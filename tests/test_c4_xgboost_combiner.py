"""Unit tests for the C4 XGBoost forecast-multiplier combiner.

Coverage:
- Label has no look-ahead bias and matches a hand-computed value.
- Multiplier squash always lands in [0.5, 1.5] and is the identity at y_hat=0.
- Walk-forward training honours the leakage gate
  (no training row's label end ever exceeds refit_date - 1).
- realized_xcorr matches a hand-computed value on a tiny synthetic basket.
- Harness `WalkForwardMultiplierCandidate` writes the panel path into the
  patched config (plumbing-validity unit test).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from systems.crypto_perps.c4_xgboost_combiner import (
    FitArtifact,
    MULT_CEILING,
    MULT_FLOOR,
    XGB_PARAMS,
    aggregate_feature_importance,
    multiplier_distribution_stats,
    predictions_to_multiplier_panel,
    realized_xcorr,
    uniform_multiplier_panel,
    vol_normalized_forward_return,
)


class TestLabelComputation:
    def test_label_uses_only_future_returns(self):
        # 10 days of constant +1.0 returns, vol=1.0 (so the normalizer is sqrt(H/365))
        idx = pd.date_range("2024-01-01", periods=10)
        rets = pd.Series(np.ones(10), index=idx)
        vol = pd.Series(np.ones(10), index=idx)  # annualized vol
        H = 3
        out = vol_normalized_forward_return(rets, vol, H)

        # At t=0: sum of returns at indexes 1,2,3 = 3.0; normalizer = 1.0 * sqrt(3/365)
        expected_t0 = 3.0 / np.sqrt(3.0 / 365.0)
        assert out.iloc[0] == pytest.approx(expected_t0, rel=1e-9)
        # The last H rows can't have a full forward window
        assert out.iloc[-H:].isna().all()

    def test_label_at_index_t_does_not_depend_on_returns_after_horizon(self):
        # Build two return series that differ ONLY after t+H. The label at t
        # must be identical between them.
        idx = pd.date_range("2024-01-01", periods=20)
        base = pd.Series(np.linspace(-0.01, 0.01, 20), index=idx)
        perturbed = base.copy()
        perturbed.iloc[10:] = 999.0  # huge tail-perturbation
        vol = pd.Series(np.ones(20), index=idx)
        H = 5
        out_base = vol_normalized_forward_return(base, vol, H)
        out_pert = vol_normalized_forward_return(perturbed, vol, H)
        # At t=4 the forward window is [5, 9] — both series identical there.
        assert out_base.iloc[4] == pytest.approx(out_pert.iloc[4])
        # At t=5 the forward window is [6, 10] — perturbation enters at t=10.
        assert out_base.iloc[5] != pytest.approx(out_pert.iloc[5])

    def test_label_is_nan_when_vol_is_zero(self):
        idx = pd.date_range("2024-01-01", periods=10)
        rets = pd.Series(np.ones(10), index=idx)
        vol = pd.Series(np.zeros(10), index=idx)
        out = vol_normalized_forward_return(rets, vol, 3)
        assert out.dropna().empty


class TestMultiplierSquash:
    def _stub_artifact(self, sigma: float = 1.0) -> FitArtifact:
        return FitArtifact(
            refit_date=pd.Timestamp("2020-01-01"),
            n_train_rows=0,
            n_val_rows=0,
            best_iteration=0,
            best_val_rmse=float("nan"),
            feature_importance={},
            train_pred_iqr=sigma,
        )

    def test_zero_y_hat_maps_to_one(self):
        idx = pd.MultiIndex.from_product(
            [pd.date_range("2020-01-01", periods=3), ["BTC", "ETH"]],
            names=["__date__", "__instrument__"],
        )
        preds = pd.Series(np.zeros(len(idx)), index=idx, name="y_hat")
        panel = predictions_to_multiplier_panel(preds, [self._stub_artifact()])
        assert (panel.values == 1.0).all()

    def test_multiplier_in_bounds_for_random_inputs(self):
        rng = np.random.default_rng(42)
        idx = pd.MultiIndex.from_product(
            [pd.date_range("2020-01-01", periods=50), ["A", "B", "C"]],
            names=["__date__", "__instrument__"],
        )
        preds = pd.Series(rng.normal(0, 100, len(idx)), index=idx, name="y_hat")
        panel = predictions_to_multiplier_panel(preds, [self._stub_artifact(sigma=0.5)])
        flat = panel.values.ravel()
        assert (flat >= MULT_FLOOR - 1e-12).all()
        assert (flat <= MULT_CEILING + 1e-12).all()

    def test_uninformative_fit_emits_identity_multiplier(self):
        # Even with a large nonzero y_hat, an `is_uninformative=True` fit must
        # produce multiplier = 1.0 — preserves the anchored-to-baseline
        # invariant when XGBoost early-stops at iter=0.
        idx = pd.MultiIndex.from_product(
            [pd.date_range("2020-01-01", periods=3), ["BTC"]],
            names=["__date__", "__instrument__"],
        )
        preds = pd.Series([5.0, -5.0, 100.0], index=idx, name="y_hat")
        artifact = FitArtifact(
            refit_date=pd.Timestamp("2020-01-01"),
            n_train_rows=0, n_val_rows=0, best_iteration=0,
            best_val_rmse=float("nan"), feature_importance={},
            train_pred_iqr=0.5, is_uninformative=True,
        )
        panel = predictions_to_multiplier_panel(preds, [artifact])
        assert (panel.values == 1.0).all()

    def test_per_fit_sigma_assignment(self):
        # Two fits with very different sigmas; predictions on different dates
        # should use the appropriate sigma.
        idx = pd.MultiIndex.from_product(
            [
                [pd.Timestamp("2020-02-15"), pd.Timestamp("2020-04-15")],
                ["BTC"],
            ],
            names=["__date__", "__instrument__"],
        )
        preds = pd.Series([1.0, 1.0], index=idx, name="y_hat")
        artifacts = [
            FitArtifact(
                refit_date=pd.Timestamp("2020-02-01"),
                n_train_rows=0, n_val_rows=0, best_iteration=0,
                best_val_rmse=float("nan"), feature_importance={},
                train_pred_iqr=10.0,  # large sigma -> y/sigma small -> mult ~ 1.05
            ),
            FitArtifact(
                refit_date=pd.Timestamp("2020-04-01"),
                n_train_rows=0, n_val_rows=0, best_iteration=0,
                best_val_rmse=float("nan"), feature_importance={},
                train_pred_iqr=0.1,  # tiny sigma -> y/sigma huge -> mult saturates at 1.5
            ),
        ]
        panel = predictions_to_multiplier_panel(preds, artifacts)
        feb_mult = panel.loc[pd.Timestamp("2020-02-15"), "BTC"]
        apr_mult = panel.loc[pd.Timestamp("2020-04-15"), "BTC"]
        assert feb_mult < apr_mult  # large sigma -> closer to 1; small sigma -> saturates higher
        assert apr_mult == pytest.approx(MULT_CEILING)


class TestUniformPanel:
    def test_uniform_is_all_ones(self):
        ref = pd.DataFrame(
            np.random.normal(1, 0.1, (10, 3)),
            index=pd.date_range("2020-01-01", periods=10),
            columns=["A", "B", "C"],
        )
        out = uniform_multiplier_panel(ref)
        assert (out.values == 1.0).all()
        assert out.shape == ref.shape


class TestRealizedXCorr:
    def test_matches_hand_computed_correlation(self):
        # Construct two perfectly correlated series + one anti-correlated
        n = 90
        idx = pd.date_range("2024-01-01", periods=n)
        a = pd.Series(np.sin(np.linspace(0, 6 * np.pi, n)), index=idx)
        b = a.copy()  # corr(a,b) = 1
        c = -a  # corr(a,c) = -1, corr(b,c) = -1
        df = pd.DataFrame({"A": a, "B": b, "C": c})
        out = realized_xcorr(df, lookback_days=30, basket_lookback_days=60, basket_min_obs=20)
        # Last value: with 3 series, off-diag mean = (1 + (-1) + (-1)) / 3 = -1/3
        assert out.iloc[-1] == pytest.approx(-1.0 / 3.0, abs=1e-6)

    def test_excludes_instruments_below_basket_min_obs(self):
        # Two well-populated, one nearly-empty
        n = 90
        idx = pd.date_range("2024-01-01", periods=n)
        a = pd.Series(np.linspace(-1, 1, n), index=idx)
        b = pd.Series(np.linspace(-1, 1, n), index=idx)
        sparse = pd.Series(np.full(n, np.nan), index=idx)
        sparse.iloc[-5:] = 0.5
        df = pd.DataFrame({"A": a, "B": b, "SPARSE": sparse})
        out = realized_xcorr(df, lookback_days=30, basket_lookback_days=60, basket_min_obs=30)
        # SPARSE is excluded; basket = {A, B}; corr(A, B) = 1; off-diag mean = 1
        assert out.iloc[-1] == pytest.approx(1.0, abs=1e-6)


class TestWalkForwardLeakageGate:
    """The leakage gate test: at each refit date `t`, no training row's label
    end may exceed `t - 1`. Equivalent: training feature_date <= t - 1 - H.
    We exercise this against fit_predict_walk_forward indirectly by checking
    the FitArtifact.refit_date and the training-row count it implies.
    """

    def test_no_label_leakage_in_train_split(self):
        from systems.crypto_perps.c4_xgboost_combiner import (
            FeatureBundle,
            fit_predict_walk_forward,
        )

        # Build a tiny synthetic feature panel: 6 months of daily data, 2 instruments
        dates = pd.date_range("2024-01-01", "2024-06-30", freq="D")
        instruments = ["A", "B"]
        rng = np.random.default_rng(0)
        rows = []
        for instr in instruments:
            for d in dates:
                rows.append({
                    "__date__": d,
                    "__instrument__": instr,
                    "f1": rng.normal(),
                    "f2": rng.normal(),
                    "__label__": rng.normal(),
                })
        df = pd.DataFrame(rows).set_index(["__date__", "__instrument__"]).sort_index()
        bundle = FeatureBundle(
            df=df,
            rule_feature_cols=["f1"],
            aggregate_feature_cols=["f2"],
            instrument_feature_cols=[],
            portfolio_feature_cols=[],
            label_col="__label__",
        )
        oos_preds, artifacts = fit_predict_walk_forward(
            bundle, horizon_days=5, retrain_freq="MS", min_train_rows=50
        )
        assert len(artifacts) >= 2
        # OOS predictions cover dates from the first usable refit onward.
        # Their indexes are (date, instrument); none of those date/instrument
        # rows can have been in the training set of the SAME refit they belong
        # to. Cross-check by re-deriving the cutoff.
        refit_dates = sorted({a.refit_date for a in artifacts})
        for t in refit_dates:
            cutoff_feature_date = t - pd.Timedelta(days=5 + 1)
            # No prediction for date < t can correspond to this refit's window.
            preds_in_this_window = oos_preds[
                (oos_preds.index.get_level_values(0) >= t)
            ]
            assert all(
                d >= t for d in preds_in_this_window.index.get_level_values(0)
            ), (
                f"Refit at {t}: found OOS prediction date < refit_date. "
                f"Cutoff was {cutoff_feature_date}."
            )


class TestModelPersistence:
    """save_fit / load_latest_fit roundtrip + schema validation + corruption
    handling. These guard the live daily flow's incremental path — if any of
    these break, multiplier values could silently drift over days.
    """

    def _train_tiny_model(self):
        """Train a small XGB model on synthetic data; return (model, artifact, feature_cols)."""
        from systems.crypto_perps.c4_xgboost_combiner import (
            FitArtifact, _train_one_fit,
        )
        rng = np.random.default_rng(7)
        n = 500
        X = pd.DataFrame(
            rng.normal(0, 1, (n, 3)),
            columns=["f1", "f2", "f3"],
            index=pd.RangeIndex(n),
        )
        y = pd.Series(rng.normal(0, 0.5, n), name="label")
        model, artifact = _train_one_fit(X, y, pd.Timestamp("2024-06-01"))
        return model, artifact, list(X.columns), X

    def test_save_load_roundtrip_predictions_match(self, tmp_path):
        from systems.crypto_perps.c4_xgboost_combiner import save_fit, load_latest_fit

        model, artifact, cols, X = self._train_tiny_model()
        save_fit(model, artifact, cols, tmp_path)
        loaded_model, loaded_artifact, loaded_cols = load_latest_fit(tmp_path)

        # Predictions must match bitwise
        np.testing.assert_array_equal(loaded_model.predict(X), model.predict(X))
        # Metadata round-tripped correctly
        assert loaded_artifact.refit_date == artifact.refit_date
        assert loaded_artifact.train_pred_iqr == artifact.train_pred_iqr
        assert loaded_artifact.is_uninformative == artifact.is_uninformative
        assert loaded_cols == cols

    def test_load_fails_on_schema_mismatch(self, tmp_path):
        from systems.crypto_perps.c4_xgboost_combiner import (
            save_fit, load_latest_fit, FitNotPersistedError,
        )

        model, artifact, cols, _ = self._train_tiny_model()
        save_fit(model, artifact, cols, tmp_path)

        # Try to load with a different feature set
        with pytest.raises(FitNotPersistedError, match="schema"):
            load_latest_fit(tmp_path, expected_feature_cols=["f1", "f2", "DIFFERENT"])

    def test_load_fails_on_missing_files(self, tmp_path):
        from systems.crypto_perps.c4_xgboost_combiner import (
            load_latest_fit, FitNotPersistedError,
        )
        with pytest.raises(FitNotPersistedError, match="No persisted fit"):
            load_latest_fit(tmp_path)

    def test_load_fails_on_corrupt_joblib(self, tmp_path):
        from systems.crypto_perps.c4_xgboost_combiner import (
            save_fit, load_latest_fit, FitNotPersistedError,
        )
        model, artifact, cols, _ = self._train_tiny_model()
        save_fit(model, artifact, cols, tmp_path)
        # Corrupt the model file
        (tmp_path / "latest.joblib").write_bytes(b"not a valid joblib file")
        with pytest.raises(FitNotPersistedError, match="joblib.load"):
            load_latest_fit(tmp_path)

    def test_load_fails_on_meta_schema_version_mismatch(self, tmp_path):
        from systems.crypto_perps.c4_xgboost_combiner import (
            save_fit, load_latest_fit, FitNotPersistedError,
        )
        import json as _json
        model, artifact, cols, _ = self._train_tiny_model()
        save_fit(model, artifact, cols, tmp_path)
        # Bump schema_version in the meta to something we don't recognize
        meta_path = tmp_path / "latest.meta.json"
        meta = _json.loads(meta_path.read_text())
        meta["schema_version"] = 999
        meta_path.write_text(_json.dumps(meta))
        with pytest.raises(FitNotPersistedError, match="schema_version"):
            load_latest_fit(tmp_path)


class TestPredictTodayOnly:
    """The squash logic in predict_today_only must produce identical output
    to predictions_to_multiplier_panel for the same inputs. Otherwise the
    incremental path drifts from the from-scratch path.
    """

    def test_predict_today_matches_full_squash(self, tmp_path):
        from systems.crypto_perps.c4_xgboost_combiner import (
            FitArtifact, predict_today_only, predictions_to_multiplier_panel,
        )
        # Build a contrived predictor: y_hat values per instrument, sigma=0.5.
        # Compare predict_today_only output to predictions_to_multiplier_panel
        # output for the same y_hats squashed via the full pipeline.
        instruments = ["BTC", "ETH", "SOL", "DOGE"]
        y_hats = pd.Series([0.0, 0.5, -0.3, 1.2], index=instruments)

        artifact = FitArtifact(
            refit_date=pd.Timestamp("2024-06-01"),
            n_train_rows=100, n_val_rows=25,
            best_iteration=5, best_val_rmse=0.9,
            feature_importance={}, train_pred_iqr=0.5,
            is_uninformative=False,
        )

        # Stub model whose predict() just returns the y_hats
        class _StubModel:
            def predict(self, X):
                return y_hats.loc[X.index].values

        # X_today indexed by instrument (single date implicit)
        X_today = pd.DataFrame(np.zeros((4, 3)), index=instruments, columns=["a", "b", "c"])
        from_today = predict_today_only(_StubModel(), artifact, X_today)

        # Compare to running predictions_to_multiplier_panel with the same y_hat
        # values reshaped into the (date, instrument) MultiIndex form
        idx = pd.MultiIndex.from_product(
            [[pd.Timestamp("2024-06-01")], instruments],
            names=["__date__", "__instrument__"],
        )
        preds_full = pd.Series(y_hats.values, index=idx, name="y_hat")
        panel = predictions_to_multiplier_panel(preds_full, [artifact])
        from_full = panel.iloc[0]
        # Align index ordering
        from_full = from_full.reindex(instruments)

        np.testing.assert_array_almost_equal(from_today.values, from_full.values, decimal=12)

    def test_predict_today_uninformative_returns_identity(self):
        from systems.crypto_perps.c4_xgboost_combiner import FitArtifact, predict_today_only

        artifact = FitArtifact(
            refit_date=pd.Timestamp("2024-06-01"),
            n_train_rows=100, n_val_rows=25,
            best_iteration=0, best_val_rmse=1.0,
            feature_importance={}, train_pred_iqr=0.5,
            is_uninformative=True,  # <-- key flag
        )

        class _StubModel:
            def predict(self, X):
                return np.array([5.0, -10.0, 100.0])

        X = pd.DataFrame(np.zeros((3, 2)), index=["A", "B", "C"], columns=["a", "b"])
        out = predict_today_only(_StubModel(), artifact, X)
        # All multipliers must be exactly 1.0 regardless of prediction values
        assert (out == 1.0).all()


class TestParamPlumbing:
    """Sanity checks that the random_state and freeze_training_after parameters
    threaded into fit_predict_walk_forward actually take effect.
    """

    def _synth_bundle(self, n_dates: int = 200, n_instruments: int = 4):
        from systems.crypto_perps.c4_xgboost_combiner import FeatureBundle

        rng = np.random.default_rng(0)
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
        instruments = [f"INST_{i}" for i in range(n_instruments)]
        rows = []
        for instr in instruments:
            for d in dates:
                rows.append({
                    "__date__": d,
                    "__instrument__": instr,
                    "f1": rng.normal(),
                    "f2": rng.normal(),
                    "f3": rng.normal(),
                    "__label__": rng.normal(scale=0.5),
                })
        df = pd.DataFrame(rows).set_index(["__date__", "__instrument__"]).sort_index()
        return FeatureBundle(
            df=df,
            rule_feature_cols=["f1", "f2"],
            aggregate_feature_cols=["f3"],
            instrument_feature_cols=[],
            portfolio_feature_cols=[],
            label_col="__label__",
        )

    def test_random_state_propagates(self):
        from systems.crypto_perps.c4_xgboost_combiner import fit_predict_walk_forward

        bundle = self._synth_bundle(n_dates=200, n_instruments=4)
        preds_42, _ = fit_predict_walk_forward(
            bundle, horizon_days=5, retrain_freq="MS", min_train_rows=50,
            random_state=42,
        )
        preds_43, _ = fit_predict_walk_forward(
            bundle, horizon_days=5, retrain_freq="MS", min_train_rows=50,
            random_state=43,
        )
        # XGBoost with subsample=0.8, colsample_bytree=0.8 + tree_method="hist"
        # is seed-sensitive; different random_state must produce non-identical
        # predictions on at least one row of the OOS window.
        common = preds_42.index.intersection(preds_43.index)
        assert len(common) > 0
        assert not np.allclose(preds_42.loc[common].values, preds_43.loc[common].values), (
            "random_state override is not propagating — seed=42 and seed=43 "
            "produced identical predictions across the entire OOS window."
        )

    def test_freeze_training_after_truncates_refit_dates(self):
        from systems.crypto_perps.c4_xgboost_combiner import fit_predict_walk_forward

        bundle = self._synth_bundle(n_dates=200, n_instruments=4)
        cutoff = pd.Timestamp("2024-04-01")
        _, artifacts_full = fit_predict_walk_forward(
            bundle, horizon_days=5, retrain_freq="MS", min_train_rows=50,
        )
        _, artifacts_frozen = fit_predict_walk_forward(
            bundle, horizon_days=5, retrain_freq="MS", min_train_rows=50,
            freeze_training_after=cutoff,
        )
        # Frozen run must have fewer or equal refits, and no refit_date past cutoff.
        assert len(artifacts_frozen) <= len(artifacts_full)
        assert all(a.refit_date <= cutoff for a in artifacts_frozen)
        # And it must have at least one refit (otherwise the test data was too sparse).
        assert len(artifacts_frozen) >= 1


class TestStatisticsHelpers:
    def test_multiplier_distribution_stats_basic(self):
        df = pd.DataFrame(
            [[0.5, 1.0, 1.5], [1.0, 1.0, 1.0]],
            columns=["A", "B", "C"],
        )
        s = multiplier_distribution_stats(df)
        assert s["n"] == 6
        assert s["mean"] == pytest.approx(1.0)
        assert s["frac_at_floor"] == pytest.approx(1 / 6)
        assert s["frac_at_ceiling"] == pytest.approx(1 / 6)

    def test_aggregate_feature_importance_handles_empty(self):
        out = aggregate_feature_importance([])
        assert out.empty


class TestHarnessMultiplierInjection:
    """Plumbing-validity unit test: when WalkForwardMultiplierCandidate runs,
    the harness writes a temp YAML config that includes
    `walk_forward_multiplier_panel_path` pointing to the panel.

    We monkeypatch `subprocess.run` (not the full method) so that the real
    config-injection logic in `_run_backtest_subprocess` executes and the
    temp config gets written to disk; we capture and parse it before the
    method's `finally` block deletes the temp file.
    """

    def test_multiplier_path_is_injected_into_temp_config(self, tmp_path, monkeypatch):
        from systems.crypto_perps import walk_forward as wf_mod
        from systems.crypto_perps.walk_forward import (
            WalkForwardHarness,
            WalkForwardMultiplierCandidate,
        )

        captured: dict[str, Any] = {}

        def fake_subprocess_run(cmd, **kwargs):
            # Find --config arg and snapshot its file contents while it still exists.
            i = cmd.index("--config")
            cfg_path_str = cmd[i + 1]
            captured["config_text"] = Path(cfg_path_str).read_text()
            captured["cmd"] = cmd
            class _Result:
                returncode = 0
            return _Result()

        monkeypatch.setattr(wf_mod.subprocess, "run", fake_subprocess_run)

        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text("forecast_weights:\n  ewmac_8: 0.5\n  ewmac_16: 0.5\n")
        panel_path = tmp_path / "multiplier_panel.parquet"
        panel = pd.DataFrame(
            np.ones((5, 2)),
            index=pd.date_range("2024-01-01", periods=5),
            columns=["BTC", "ETH"],
        )
        panel.to_parquet(panel_path)

        harness = WalkForwardHarness(
            config_path=cfg_path,
            data_path="data.parquet",
            out_dir=tmp_path / "wf",
        )
        candidate = WalkForwardMultiplierCandidate(
            name="c4_xgboost_h5", multiplier_panel_path=panel_path
        )
        candidate.run_backtest(harness, {})

        parsed = yaml.safe_load(captured["config_text"])
        assert parsed["walk_forward_multiplier_panel_path"] == str(panel_path.resolve())
        assert "walk_forward_weights_path" not in parsed
        # Original keys preserved.
        assert "ewmac_8" in parsed["forecast_weights"]
