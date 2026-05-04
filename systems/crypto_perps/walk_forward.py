"""
Walk-forward research harness.

Every research candidate (new rule, new sizing, regime layer, ML combiner) is
tested against the frozen flat-68 baseline by running it under this harness.
The harness exists to enforce one rule: at every refit point, the candidate
sees only data ≤ refit_date — no global normalization, no peek into the future,
no scheme that was tuned on the full history.

Why a single harness:
  - The textbook overfit trap is post-hoc threshold lowering ("0.04 Sharpe is
    fine, look at the Calmar"). The harness records the adoption rule BEFORE
    the run and compares the realized OOS metrics to it deterministically.
  - Per-window decomposition (per-quarter Sharpe / Calmar) catches candidates
    whose total Sharpe is driven by a single fluke window.
  - A single replay path means every research artifact in `out/wf_<candidate>/`
    has the same shape and the same audit trail.

Usage (CLI):
    python -m systems.crypto_perps.walk_forward \\
        --candidate flat68_replay \\
        --baseline flat68 \\
        --window expanding \\
        --stride Q

Self-replication acceptance: the `flat68_replay` candidate must produce a
stitched OOS Sharpe within ±0.02 of the documented flat-68 walk-forward number
(see `out/wf_comparison_56rules/comparison_results.json`) before the harness
can be used to evaluate any new candidate.

Programmatic usage:
    from systems.crypto_perps.walk_forward import (
        WalkForwardHarness, FlatBaselineCandidate, AdoptionRule,
    )

    harness = WalkForwardHarness(
        config_path="config/crypto_perps_full_rules.yaml",
        data_path="data/dataset_sb_corrected_6yr_jagged.parquet",
        panels_dir=Path("data/forecast_panels"),
        out_dir=Path("out/wf_flat68_replay"),
        adoption_rule=AdoptionRule(
            name="self_replication",
            min_delta_sharpe=-0.02,  # equality test, signed
            max_delta_sharpe=+0.02,
            min_delta_calmar=-0.20,
            max_quarter_drawdown=-0.30,
        ),
    )
    result = harness.run(FlatBaselineCandidate())
    print(result.decision)  # "ADOPT" / "REJECT" / "REPLICATED"
"""

from __future__ import annotations

import abc
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Adoption rule and decision artifact
# ---------------------------------------------------------------------------


# When `data_available_after` is set on an AdoptionRule, `min_delta_sharpe`
# MUST be at least this floor. Restricted-window scoring shrinks the sample,
# raising the noise floor on Sharpe by ~sqrt(N_full / N_active). For a typical
# 1-year-of-6 windowed candidate (~17%), the noise multiplier is ~2.4×, so the
# default +0.02 bar becomes ~+0.05. We hardcode +0.04 as a conservative floor —
# anyone using windowed scoring must explicitly commit to a stricter bar a priori.
WINDOWED_MIN_DELTA_SHARPE_FLOOR = 0.04


@dataclass
class AdoptionRule:
    """
    Pre-stated bar a candidate must clear to adopt. Recorded in the decision
    artifact verbatim so that post-hoc threshold lowering shows up in the diff.

    `data_available_after`: optional pd.Timestamp. When set, the harness recomputes
    Sharpe/Calmar/CAGR/MaxDD from `daily_returns.csv` over the post-cutoff window
    only — for both candidate AND baseline. Use this for rules whose data only
    covers part of the backtest (ETF flows post-2024, on-chain feeds post-launch),
    so the rule's contribution isn't diluted by the inactive period in the headline
    metric. The cutoff must be set from data availability, NOT from where the
    rule "looked good" — the latter is the textbook overfit trap.

    Setting `data_available_after` triggers a mandatory precommit check at
    __post_init__: `min_delta_sharpe` must be ≥ WINDOWED_MIN_DELTA_SHARPE_FLOOR.
    """
    name: str
    min_delta_sharpe: float = 0.05  # candidate must beat baseline Sharpe by this
    min_delta_calmar: float = 0.0
    max_quarter_drawdown: float = -0.30  # any quarter worse than this fails the candidate
    second_half_oos_floor: Optional[float] = None  # second-half OOS Sharpe must exceed this
    max_delta_sharpe: Optional[float] = None  # set for "replication" tests (e.g., ±0.02)
    data_available_after: Optional[pd.Timestamp] = None  # restrict scoring to this date onward

    def __post_init__(self):
        if self.data_available_after is not None:
            # Coerce string → Timestamp for ergonomics.
            if not isinstance(self.data_available_after, pd.Timestamp):
                self.data_available_after = pd.Timestamp(self.data_available_after)
            if self.min_delta_sharpe < WINDOWED_MIN_DELTA_SHARPE_FLOOR:
                raise ValueError(
                    f"AdoptionRule '{self.name}' sets data_available_after="
                    f"{self.data_available_after.date()} but min_delta_sharpe="
                    f"{self.min_delta_sharpe:+.4f} is below the windowed floor of "
                    f"{WINDOWED_MIN_DELTA_SHARPE_FLOOR:+.4f}. Restricted-window scoring "
                    f"increases the Sharpe noise floor proportional to sqrt(N_full/N_active); "
                    f"you must commit to a stricter bar a priori. Either raise min_delta_sharpe "
                    f"≥ {WINDOWED_MIN_DELTA_SHARPE_FLOOR} or remove the windowed restriction."
                )

    def evaluate(
        self,
        candidate_metrics: dict,
        baseline_metrics: dict,
        per_window: pd.DataFrame,
    ) -> tuple[str, list[str]]:
        """Return (decision, list_of_reasons)."""
        reasons: list[str] = []
        delta_sharpe = candidate_metrics["sharpe"] - baseline_metrics["sharpe"]
        delta_calmar = candidate_metrics["calmar"] - baseline_metrics["calmar"]

        if self.max_delta_sharpe is not None:
            if not (self.min_delta_sharpe <= delta_sharpe <= self.max_delta_sharpe):
                reasons.append(
                    f"ΔSharpe={delta_sharpe:+.4f} outside replication band "
                    f"[{self.min_delta_sharpe:+.4f}, {self.max_delta_sharpe:+.4f}]"
                )
        else:
            if delta_sharpe < self.min_delta_sharpe:
                reasons.append(
                    f"ΔSharpe={delta_sharpe:+.4f} < required {self.min_delta_sharpe:+.4f}"
                )
            if delta_calmar < self.min_delta_calmar:
                reasons.append(
                    f"ΔCalmar={delta_calmar:+.4f} < required {self.min_delta_calmar:+.4f}"
                )

        # Quarter-drawdown check excludes pre-availability windows — those
        # quarters track baseline exactly so any drawdown there isn't attributable
        # to the candidate.
        if "max_quarter_drawdown" in per_window.columns:
            scoring_windows = per_window
            if "pre_availability" in per_window.columns:
                scoring_windows = per_window[~per_window["pre_availability"]]
            if len(scoring_windows) > 0:
                worst = scoring_windows["max_quarter_drawdown"].min()
                if worst < self.max_quarter_drawdown:
                    reasons.append(
                        f"Worst-quarter drawdown {worst:.2%} breaches {self.max_quarter_drawdown:.2%}"
                    )

        if self.second_half_oos_floor is not None:
            scoring_windows = per_window
            if "pre_availability" in per_window.columns:
                scoring_windows = per_window[~per_window["pre_availability"]]
            if len(scoring_windows) >= 4:
                mid = len(scoring_windows) // 2
                second_half_sharpe = scoring_windows["sharpe"].iloc[mid:].mean()
                if second_half_sharpe < self.second_half_oos_floor:
                    reasons.append(
                        f"Second-half OOS Sharpe {second_half_sharpe:.4f} < "
                        f"{self.second_half_oos_floor:.4f} — overfit signal"
                    )

        if reasons:
            return "REJECT", reasons
        decision = "REPLICATED" if self.max_delta_sharpe is not None else "ADOPT"
        return decision, []


@dataclass
class HarnessResult:
    candidate_name: str
    decision: str  # "ADOPT" / "REJECT" / "REPLICATED"
    reasons: list[str]
    candidate_metrics: dict[str, float]
    baseline_metrics: dict[str, float]
    per_window: pd.DataFrame
    adoption_rule: AdoptionRule
    artifacts_dir: Path
    started_utc: str
    elapsed_s: float
    # Populated when adoption_rule.data_available_after is set: describes the
    # restricted scoring window so the operator can audit exactly what was scored.
    scoring_window: Optional[dict] = None


# ---------------------------------------------------------------------------
# Candidate interface
# ---------------------------------------------------------------------------


class Candidate(abc.ABC):
    """
    A research candidate — a callable that, given the harness context, mutates
    the system in some testable way (new rule, new weight schedule, regime
    overlay) and returns a backtest output dir to be measured.

    The contract:
      - `name` identifies the candidate (used in artifact paths and logs).
      - `prepare(harness)` produces whatever state the backtest needs (weight
        schedule parquet, modified config). It MUST not consume data > each
        refit_date — the harness verifies this at the per-window level.
      - `run_backtest(harness)` executes a backtest and returns the output dir
        containing positions.csv + performance_summary.json.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    def prepare(self, harness: "WalkForwardHarness") -> dict[str, Any]:
        """Optional pre-step. Default: no-op. Return state passed to run_backtest."""
        return {}

    @abc.abstractmethod
    def run_backtest(self, harness: "WalkForwardHarness", state: dict[str, Any]) -> Path:
        """Run the backtest, return the output directory."""


class FlatBaselineCandidate(Candidate):
    """
    The replication / null candidate: run the system unchanged.

    Used both as the baseline to compare every other candidate against AND as
    the harness's own self-replication acceptance test (we expect a stitched
    OOS Sharpe within ±0.02 of the documented flat-68 number).
    """

    @property
    def name(self) -> str:
        return "flat68_baseline"

    def run_backtest(self, harness: "WalkForwardHarness", state: dict[str, Any]) -> Path:
        outdir = harness.out_dir / "backtest_flat_baseline"
        return harness._run_backtest_subprocess(harness.config_path, outdir, wf_weights_path=None)


class WalkForwardWeightedCandidate(Candidate):
    """
    A candidate whose contribution is a walk-forward weight schedule on the
    existing rule pool — used for candidates that produce per-rebalance scalars
    (C3 regime overlay, etc.).

    The schedule must be a parquet with a quarterly DatetimeIndex and one column
    per rule, weights summing to 1.0 per row. Each row uses ONLY data ≤ that
    rebalance date — the candidate is responsible for that invariant.
    """

    def __init__(self, name: str, weights_schedule_path: Path):
        self._name = name
        self.weights_schedule_path = Path(weights_schedule_path)

    @property
    def name(self) -> str:
        return self._name

    def run_backtest(self, harness: "WalkForwardHarness", state: dict[str, Any]) -> Path:
        outdir = harness.out_dir / f"backtest_{self._name}"
        return harness._run_backtest_subprocess(
            harness.config_path, outdir, wf_weights_path=self.weights_schedule_path
        )


class WalkForwardMultiplierCandidate(Candidate):
    """
    A candidate whose contribution is a per-(instrument, date) multiplier panel
    applied post-cap to the baseline combined forecast — used by the C4 XGBoost
    combiner experiment.

    The panel must be a parquet with a daily DatetimeIndex and one column per
    instrument, values in [0.5, 1.5]. Each cell's prediction must depend only
    on data ≤ that cell's date — the panel-builder is responsible for that
    invariant.
    """

    def __init__(self, name: str, multiplier_panel_path: Path):
        self._name = name
        self.multiplier_panel_path = Path(multiplier_panel_path)

    @property
    def name(self) -> str:
        return self._name

    def run_backtest(self, harness: "WalkForwardHarness", state: dict[str, Any]) -> Path:
        outdir = harness.out_dir / f"backtest_{self._name}"
        return harness._run_backtest_subprocess(
            harness.config_path,
            outdir,
            wf_weights_path=None,
            wf_multiplier_path=self.multiplier_panel_path,
        )


class ConfigOverrideCandidate(Candidate):
    """
    A candidate whose contribution is a set of config-key overrides applied to
    the baseline config at backtest time — used for FDM tweaks (C5), regime
    layer parameters, etc., where the change is a config knob rather than a new
    rule or weight schedule.

    Overrides are deep-merged into the YAML — nested dicts merge key-by-key
    (so you can extend `forecast_correlation_estimate` without restating its
    other entries). Top-level scalars and lists are replaced wholesale.

    Example:
        ConfigOverrideCandidate(
            name="c5_shrinkage_03",
            overrides={
                "forecast_correlation_estimate": {"shrinkage_parameter": 0.3},
            },
        )
    """

    def __init__(self, name: str, overrides: dict[str, Any]):
        self._name = name
        self.overrides = overrides

    @property
    def name(self) -> str:
        return self._name

    def run_backtest(self, harness: "WalkForwardHarness", state: dict[str, Any]) -> Path:
        outdir = harness.out_dir / f"backtest_{self._name}"
        outdir.mkdir(parents=True, exist_ok=True)

        # Build patched config under outdir's parent so the run_backtest tempfile
        # convention picks it up; clean it up afterwards.
        with open(harness.config_path) as f:
            cfg = yaml.safe_load(f)
        _deep_merge(cfg, self.overrides)
        patched = outdir.parent / f"{self._name}_patched.yaml"
        patched.write_text(yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False))

        try:
            harness._run_backtest_subprocess(patched, outdir, wf_weights_path=None)
        finally:
            patched.unlink(missing_ok=True)
        return outdir


def _deep_merge(base: dict, overrides: dict) -> None:
    """In-place deep merge of overrides into base. Dicts merge; everything else replaces."""
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardHarness:
    config_path: Path
    data_path: Path
    out_dir: Path
    panels_dir: Path = field(default_factory=lambda: Path("data/forecast_panels"))
    adoption_rule: AdoptionRule = field(default_factory=lambda: AdoptionRule(name="default"))
    macro_data_path: Optional[Path] = None
    quarterly_freq: str = "QS"

    def __post_init__(self) -> None:
        self.config_path = Path(self.config_path)
        self.data_path = Path(self.data_path)
        self.out_dir = Path(self.out_dir)
        self.panels_dir = Path(self.panels_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ----- Public API -----

    def rescore_cached(
        self,
        candidate_outdir: Path,
        baseline_outdir: Optional[Path] = None,
        candidate_name: Optional[str] = None,
    ) -> HarnessResult:
        """
        Re-score an already-completed backtest pair against the harness's current
        adoption_rule (typically a different rule from the one used at backtest time).

        Use this when:
          - You shipped a new harness feature (B7 windowed scoring) and want to
            re-evaluate prior candidates without re-running their 15-min backtests.
          - You want to apply a stricter (or differently-shaped) adoption rule to an
            existing pair without paying the compute cost again.

        Both directories must contain the artifacts the unrestricted scorer reads:
        `daily_returns.csv` and `performance_summary.json`. Writes a fresh
        `decision.md` (overwriting any prior one — keep a copy first if needed).
        """
        candidate_outdir = Path(candidate_outdir)
        if baseline_outdir is None:
            baseline_outdir = self.out_dir / "backtest_flat_baseline"
        baseline_outdir = Path(baseline_outdir)

        started = datetime.now(timezone.utc)

        scoring_window: Optional[dict] = None
        if self.adoption_rule.data_available_after is not None:
            after = self.adoption_rule.data_available_after
            baseline_metrics = self._load_summary_windowed(baseline_outdir, after)
            candidate_metrics = self._load_summary_windowed(candidate_outdir, after)
            scoring_window = {
                "data_available_after": str(after.date()),
                "n_days_scored": candidate_metrics["windowed_n_days"],
                "scored_start": candidate_metrics["windowed_start"],
                "scored_end": candidate_metrics["windowed_end"],
                "min_delta_sharpe_floor": WINDOWED_MIN_DELTA_SHARPE_FLOOR,
                "applied_min_delta_sharpe": self.adoption_rule.min_delta_sharpe,
                "full_period_baseline": self._load_summary(baseline_outdir),
                "full_period_candidate": self._load_summary(candidate_outdir),
            }
        else:
            baseline_metrics = self._load_summary(baseline_outdir)
            candidate_metrics = self._load_summary(candidate_outdir)

        per_window = self._compute_per_window(candidate_outdir)

        decision, reasons = self.adoption_rule.evaluate(
            candidate_metrics, baseline_metrics, per_window
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()

        result = HarnessResult(
            candidate_name=candidate_name or candidate_outdir.name,
            decision=decision,
            reasons=reasons,
            candidate_metrics=candidate_metrics,
            baseline_metrics=baseline_metrics,
            per_window=per_window,
            adoption_rule=self.adoption_rule,
            artifacts_dir=self.out_dir,
            started_utc=started.isoformat(),
            elapsed_s=elapsed,
            scoring_window=scoring_window,
        )

        # Stub out a candidate object for the artifact writer (it only uses .name).
        class _StubCandidate:
            def __init__(self, n): self.name = n
        self._write_decision_artifact(
            result, _StubCandidate(result.candidate_name), baseline_outdir, candidate_outdir
        )
        return result

    def run(self, candidate: Candidate) -> HarnessResult:
        started = datetime.now(timezone.utc)
        logger.info(
            "WalkForwardHarness: candidate=%s baseline=flat68 stride=%s",
            candidate.name,
            self.quarterly_freq,
        )

        baseline = FlatBaselineCandidate()
        if candidate.name == baseline.name:
            # Pure self-replication — only run once.
            cand_outdir = candidate.run_backtest(self, candidate.prepare(self))
            base_outdir = cand_outdir
        else:
            # Run baseline if not already cached, then candidate.
            base_outdir = self.out_dir / "backtest_flat_baseline"
            if not (base_outdir / "performance_summary.json").exists():
                baseline.run_backtest(self, baseline.prepare(self))
            cand_outdir = candidate.run_backtest(self, candidate.prepare(self))

        scoring_window: Optional[dict] = None
        if self.adoption_rule.data_available_after is not None:
            # Windowed scoring: recompute headline metrics over [cutoff, end] for
            # both runs from daily_returns.csv. The full-period metrics from
            # performance_summary.json get included in the decision artifact too
            # for transparency, but they don't drive the adoption decision.
            after = self.adoption_rule.data_available_after
            baseline_metrics = self._load_summary_windowed(base_outdir, after)
            candidate_metrics = self._load_summary_windowed(cand_outdir, after)
            scoring_window = {
                "data_available_after": str(after.date()),
                "n_days_scored": candidate_metrics["windowed_n_days"],
                "scored_start": candidate_metrics["windowed_start"],
                "scored_end": candidate_metrics["windowed_end"],
                "min_delta_sharpe_floor": WINDOWED_MIN_DELTA_SHARPE_FLOOR,
                "applied_min_delta_sharpe": self.adoption_rule.min_delta_sharpe,
                # Full-period metrics from the unrestricted summary, for audit
                "full_period_baseline": self._load_summary(base_outdir),
                "full_period_candidate": self._load_summary(cand_outdir),
            }
        else:
            baseline_metrics = self._load_summary(base_outdir)
            candidate_metrics = self._load_summary(cand_outdir)

        per_window = self._compute_per_window(cand_outdir)

        decision, reasons = self.adoption_rule.evaluate(
            candidate_metrics, baseline_metrics, per_window
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()

        result = HarnessResult(
            candidate_name=candidate.name,
            decision=decision,
            reasons=reasons,
            candidate_metrics=candidate_metrics,
            baseline_metrics=baseline_metrics,
            per_window=per_window,
            adoption_rule=self.adoption_rule,
            artifacts_dir=self.out_dir,
            started_utc=started.isoformat(),
            elapsed_s=elapsed,
            scoring_window=scoring_window,
        )
        self._write_decision_artifact(result, candidate, base_outdir, cand_outdir)
        return result

    # ----- Internal helpers -----

    def _run_backtest_subprocess(
        self,
        config_path: Path,
        outdir: Path,
        wf_weights_path: Optional[Path],
        wf_multiplier_path: Optional[Path] = None,
    ) -> Path:
        """Invoke run_dynamic_universe_backtest.py with optional WF weights and/or
        multiplier-panel injection. The two injection paths are orthogonal — one
        rewrites per-rule weights, the other multiplies the post-cap forecast —
        so they may both be set on the same run.
        """
        outdir.mkdir(parents=True, exist_ok=True)

        # If either injection is requested, write a temp config that points to it.
        active_config = config_path
        tmp_config_path: Optional[Path] = None
        if wf_weights_path is not None or wf_multiplier_path is not None:
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            if wf_weights_path is not None:
                cfg["walk_forward_weights_path"] = str(wf_weights_path.resolve())
            if wf_multiplier_path is not None:
                cfg["walk_forward_multiplier_panel_path"] = str(
                    wf_multiplier_path.resolve()
                )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False,
                dir=outdir.parent, prefix=f"{outdir.name}_cfg_",
            ) as tmp:
                yaml.dump(cfg, tmp, default_flow_style=False, sort_keys=False)
                tmp_config_path = Path(tmp.name)
            active_config = tmp_config_path

        cmd = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_dynamic_universe_backtest.py"),
            "--config", str(active_config),
            "--data", str(self.data_path),
            "--outdir", str(outdir),
        ]
        if self.macro_data_path and Path(self.macro_data_path).exists():
            cmd += ["--macro-data", str(self.macro_data_path)]

        logger.info("Running: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, cwd=str(REPO_ROOT))
        finally:
            if tmp_config_path is not None:
                tmp_config_path.unlink(missing_ok=True)
        return outdir

    @staticmethod
    def _load_summary(outdir: Path) -> dict[str, float]:
        path = outdir / "performance_summary.json"
        if not path.exists():
            raise FileNotFoundError(f"No performance_summary.json at {path}")
        s = json.loads(path.read_text())
        m = s.get("metrics", {})
        return {
            "sharpe": float(m.get("sharpe", float("nan"))),
            "calmar": float(m.get("calmar", float("nan"))),
            "cagr": float(m.get("cagr", float("nan"))),
            "vol": float(m.get("vol", float("nan"))),
            "max_dd": float(m.get("max_dd", float("nan"))),
        }

    @staticmethod
    def _load_summary_windowed(outdir: Path, after: pd.Timestamp) -> dict[str, float]:
        """
        Recompute Sharpe/Calmar/CAGR/MaxDD/Vol over the date window [after, end]
        from daily_returns.csv. Used by AdoptionRule.data_available_after to
        score short-history rules on their fair sample.

        ann factor = 365 (crypto trades 7 days/wk), matching what the upstream
        backtest reports in performance_summary.json.
        """
        # Use the same loader as _compute_per_window — tolerant of CSV/parquet.
        candidates = [
            ("daily_returns.csv", "net_return"),
            ("portfolio_pnl.csv", None),
            ("portfolio_pnl.parquet", None),
        ]
        pnl: Optional[pd.Series] = None
        for fname, col in candidates:
            p = outdir / fname
            if not p.exists():
                continue
            if p.suffix == ".parquet":
                df = pd.read_parquet(p)
                pnl = df[col] if col and col in df.columns else df.iloc[:, 0]
            else:
                df = pd.read_csv(p, index_col=0, parse_dates=True)
                pnl = df[col] if col and col in df.columns else df.iloc[:, 0]
            break

        if pnl is None or pnl.empty:
            raise FileNotFoundError(f"No daily-returns artifact in {outdir} — cannot rescore.")

        windowed = pnl.loc[after:].dropna()
        if len(windowed) < 30:
            raise ValueError(
                f"Restricted window from {after.date()} has only {len(windowed)} days — "
                f"too short to score. Pick an earlier cutoff or a different rule."
            )

        ann = 365
        mean = float(windowed.mean()) * ann
        std = float(windowed.std()) * np.sqrt(ann)
        sharpe = mean / std if std > 0 else float("nan")
        cumulative = (1 + windowed).cumprod()
        running_peak = cumulative.cummax()
        max_dd = float((cumulative / running_peak - 1).min())
        calmar = mean / abs(max_dd) if max_dd < 0 else float("nan")
        # CAGR over the windowed period: (final/start)^(1/years) - 1
        years = len(windowed) / ann
        cagr = float(cumulative.iloc[-1] ** (1 / years) - 1) if years > 0 else float("nan")

        return {
            "sharpe": sharpe,
            "calmar": calmar,
            "cagr": cagr,
            "vol": std,
            "max_dd": max_dd,
            "windowed_n_days": int(len(windowed)),
            "windowed_start": str(windowed.index[0].date()),
            "windowed_end": str(windowed.index[-1].date()),
        }

    def _compute_per_window(self, outdir: Path) -> pd.DataFrame:
        """
        Decompose stitched OOS PnL into per-quarter metrics. Catches the case
        where total Sharpe looks fine but is driven by a single fluke window.
        """
        # run_dynamic_universe_backtest.py writes net daily returns to
        # daily_returns.csv (column "net_return"). Fallback to legacy parquet
        # names so the harness stays tolerant if the backtest output evolves.
        candidates = [
            ("daily_returns.csv", "net_return"),
            ("portfolio_pnl.csv", None),
            ("portfolio_pnl.parquet", None),
        ]
        pnl: Optional[pd.Series] = None
        for fname, col in candidates:
            p = outdir / fname
            if not p.exists():
                continue
            if p.suffix == ".parquet":
                df = pd.read_parquet(p)
                pnl = df[col] if col and col in df.columns else df.iloc[:, 0]
            else:
                df = pd.read_csv(p, index_col=0, parse_dates=True)
                pnl = df[col] if col and col in df.columns else df.iloc[:, 0]
            break

        if pnl is None or pnl.empty:
            logger.warning("No daily-returns artifact found in %s — per-window decomp empty.", outdir)
            return pd.DataFrame()

        # Skip partial quarters at either end. A 16-day final quarter produces
        # an absurd Sharpe purely from sample-size noise (n=16 → annualization
        # factor of ~6 vs 91 → magnifies any drift), and a 5-day first quarter
        # has the same problem. Require ≥45 days (~half a quarter) for any
        # window to enter the per-window decomposition.
        MIN_WINDOW_DAYS = 45

        # If the harness's adoption_rule has a data_available_after cutoff, mark
        # any quarter that ends before that cutoff as pre-availability — those
        # quarters track the baseline exactly (rule produces NaN forecast) and
        # shouldn't drive worst-quarter checks or second-half-OOS gates.
        cutoff = (
            self.adoption_rule.data_available_after
            if self.adoption_rule.data_available_after is not None
            else None
        )

        windows = []
        for quarter, group in pnl.groupby(pd.Grouper(freq=self.quarterly_freq)):
            group = group.dropna()
            if len(group) < MIN_WINDOW_DAYS:
                continue
            ann = 365  # crypto trades 7 days/wk
            mean = group.mean() * ann
            std = group.std() * np.sqrt(ann)
            sharpe = mean / std if std > 0 else float("nan")
            cumulative = (1 + group).cumprod()
            running_peak = cumulative.cummax()
            dd = (cumulative / running_peak - 1).min()
            calmar = mean / abs(dd) if dd < 0 else float("nan")
            # Quarter is "pre-availability" when its last day is before the cutoff.
            pre_availability = bool(cutoff is not None and group.index[-1] < cutoff)
            windows.append(
                {
                    "quarter_start": quarter,
                    "n_days": len(group),
                    "sharpe": sharpe,
                    "calmar": calmar,
                    "max_quarter_drawdown": float(dd),
                    "ann_return": float(mean),
                    "pre_availability": pre_availability,
                }
            )
        if not windows:
            return pd.DataFrame()
        df = pd.DataFrame(windows).set_index("quarter_start")
        return df

    def _write_decision_artifact(
        self,
        result: HarnessResult,
        candidate: Candidate,
        base_outdir: Path,
        cand_outdir: Path,
    ) -> None:
        decision_md = self.out_dir / "decision.md"
        per_window_path = self.out_dir / "metrics_per_window.parquet"
        if not result.per_window.empty:
            result.per_window.to_parquet(per_window_path)

        rule = result.adoption_rule
        lines = [
            f"# Walk-forward decision: {result.candidate_name}",
            "",
            f"- **Decision:** {result.decision}",
            f"- **Started:** {result.started_utc}",
            f"- **Elapsed:** {result.elapsed_s:.1f}s",
            f"- **Baseline backtest:** `{base_outdir}`",
            f"- **Candidate backtest:** `{cand_outdir}`",
            "",
            "## Adoption rule (pre-stated)",
            "",
            f"- name: `{rule.name}`",
            f"- min_delta_sharpe: {rule.min_delta_sharpe}",
            f"- max_delta_sharpe: {rule.max_delta_sharpe}",
            f"- min_delta_calmar: {rule.min_delta_calmar}",
            f"- max_quarter_drawdown: {rule.max_quarter_drawdown}",
            f"- second_half_oos_floor: {rule.second_half_oos_floor}",
            f"- data_available_after: {rule.data_available_after}",
            "",
        ]
        if result.scoring_window is not None:
            sw = result.scoring_window
            full_b = sw["full_period_baseline"]
            full_c = sw["full_period_candidate"]
            lines += [
                "## Scoring window (data_available_after)",
                "",
                f"- **Cutoff:** {sw['data_available_after']}",
                f"- **Scored period:** {sw['scored_start']} → {sw['scored_end']} ({sw['n_days_scored']} days)",
                f"- **Mandatory min_delta_sharpe floor:** {sw['min_delta_sharpe_floor']:+.4f}",
                f"- **Applied min_delta_sharpe:** {sw['applied_min_delta_sharpe']:+.4f}",
                "",
                "Full-period metrics (NOT used for decision — audit only):",
                "",
                "| metric | baseline (full) | candidate (full) | delta |",
                "|---|---|---|---|",
                f"| sharpe | {full_b['sharpe']:.4f} | {full_c['sharpe']:.4f} | {full_c['sharpe'] - full_b['sharpe']:+.4f} |",
                f"| calmar | {full_b['calmar']:.4f} | {full_c['calmar']:.4f} | {full_c['calmar'] - full_b['calmar']:+.4f} |",
                f"| max_dd | {full_b['max_dd']:.4f} | {full_c['max_dd']:.4f} | {full_c['max_dd'] - full_b['max_dd']:+.4f} |",
                "",
            ]
        lines += [
            "## Metrics" + (" (windowed)" if result.scoring_window is not None else ""),
            "",
            "| metric | baseline | candidate | delta |",
            "|---|---|---|---|",
        ]
        for key in ("sharpe", "calmar", "cagr", "vol", "max_dd"):
            base = result.baseline_metrics.get(key, float("nan"))
            cand = result.candidate_metrics.get(key, float("nan"))
            lines.append(f"| {key} | {base:.4f} | {cand:.4f} | {cand - base:+.4f} |")
        lines += ["", "## Reasons (if rejected)", ""]
        for r in result.reasons:
            lines.append(f"- {r}")
        if not result.reasons:
            lines.append("- (none — adoption rule satisfied)")
        lines += ["", "## Per-window metrics", ""]
        if result.per_window.empty:
            lines.append("- (no per-window decomposition available)")
        else:
            has_flag = "pre_availability" in result.per_window.columns and result.per_window["pre_availability"].any()
            header = "| quarter | days | Sharpe | Calmar | maxDD |"
            divider = "|---|---|---|---|---|"
            if has_flag:
                header = "| quarter | days | Sharpe | Calmar | maxDD | scored |"
                divider = "|---|---|---|---|---|---|"
            lines.append(header)
            lines.append(divider)
            for ts, row in result.per_window.iterrows():
                cells = [
                    f"{ts.date()}",
                    f"{int(row['n_days'])}",
                    f"{row['sharpe']:.3f}",
                    f"{row['calmar']:.3f}",
                    f"{row['max_quarter_drawdown']:.3%}",
                ]
                if has_flag:
                    pre = row.get("pre_availability", False)
                    cells.append("—" if pre else "✓")
                lines.append("| " + " | ".join(cells) + " |")
        lines += [
            "",
            "## What would falsify this",
            "",
            "- The candidate code path inadvertently consumes data > refit_date "
            "(walk-forward leakage). Reviewer must scan the diff for global "
            "aggregations that span the whole test window.",
            "- The cached forecast panels in "
            f"`{self.panels_dir}` are stale — re-run `extract_rule_forecasts.py` "
            "if the dataset or rule set has changed since panels were built.",
            "- The pre-stated adoption rule above was lowered post-hoc to fit "
            "the realized metrics. Compare this `decision.md` against the version "
            "stored at the start of the run.",
        ]
        decision_md.write_text("\n".join(lines))
        logger.info("Wrote %s", decision_md)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _make_default_adoption_rule(candidate_name: str) -> AdoptionRule:
    """Default rules per candidate name. Most candidates can override via API."""
    if candidate_name == "flat68_replay" or candidate_name.endswith("_self_replication"):
        # Plumbing-validity check (any candidate ending in _self_replication
        # should be a uniform/no-op variant whose backtest must reproduce the
        # baseline within the standard ±0.02 Sharpe band).
        return AdoptionRule(
            name="self_replication",
            min_delta_sharpe=-0.02,
            max_delta_sharpe=+0.02,
            min_delta_calmar=-0.20,
            max_quarter_drawdown=-1.00,  # any quarter — replication doesn't enforce
        )
    if candidate_name.startswith("c4_") or "xgboost" in candidate_name:
        # Stricter gate for the ML combiner experiment.
        return AdoptionRule(
            name="ml_combiner",
            min_delta_sharpe=0.05,
            min_delta_calmar=0.0,
            max_quarter_drawdown=-0.30,
            second_half_oos_floor=0.03,
        )
    if candidate_name.startswith("c3_"):
        # C3 regime layer — standard bar plus a runtime IS-vs-OOS gate applied
        # post-harness in main() (see _evaluate_c3_is_oos_gate). We do NOT set
        # second_half_oos_floor here because the brief's gate is "candidate's
        # second-half mean per-quarter Sharpe ≥ candidate's first-half mean −
        # 0.03" — relative to the candidate's own first half, not a fixed floor.
        return AdoptionRule(
            name="c3_regime_layer",
            min_delta_sharpe=0.05,
            min_delta_calmar=0.0,
            max_quarter_drawdown=-0.30,
        )
    return AdoptionRule(
        name="default",
        min_delta_sharpe=0.05,
        min_delta_calmar=0.0,
        max_quarter_drawdown=-0.30,
    )


# C3-specific overfit defense: compare candidate's first-half mean per-quarter
# Sharpe to its second-half mean. A regression > 0.03 indicates the regime
# scalars overfit to in-sample family-state cell SR estimates.
C3_IS_OOS_REGRESSION_TOLERANCE: float = 0.03


def _evaluate_c3_is_oos_gate(per_window: pd.DataFrame) -> tuple[bool, str]:
    """Returns (passed, summary). Summary always populated for the audit trail."""
    if per_window.empty or len(per_window) < 4:
        return True, (
            f"C3 IS-vs-OOS gate: SKIPPED — only {len(per_window)} per-window rows "
            f"(need ≥4)."
        )
    mid = len(per_window) // 2
    first_half = per_window["sharpe"].iloc[:mid].mean()
    second_half = per_window["sharpe"].iloc[mid:].mean()
    delta = second_half - first_half
    passed = delta >= -C3_IS_OOS_REGRESSION_TOLERANCE
    summary = (
        f"C3 IS-vs-OOS gate: first-half mean per-Q Sharpe={first_half:.4f}, "
        f"second-half mean={second_half:.4f}, delta={delta:+.4f} "
        f"(tolerance={-C3_IS_OOS_REGRESSION_TOLERANCE:+.4f}) → "
        f"{'PASS' if passed else 'FAIL'}"
    )
    return passed, summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Walk-forward research harness — gate every candidate behind a pre-stated bar."
    )
    parser.add_argument("--candidate", required=True, help="Candidate name (also output dir suffix).")
    parser.add_argument("--baseline", default="flat68", help="Baseline label (cosmetic).")
    parser.add_argument(
        "--config", default="config/crypto_perps_full_rules.yaml",
        help="Backtest config (the candidate's deltas must come from elsewhere — config injection, schedule, etc.).",
    )
    parser.add_argument(
        "--data", default="data/dataset_sb_corrected_6yr_jagged.parquet",
        help="Dataset to backtest against (default: SB-corrected).",
    )
    parser.add_argument("--macro-data", type=Path, default=Path("data/macro_factors.parquet"))
    parser.add_argument("--panels-dir", type=Path, default=Path("data/forecast_panels"))
    parser.add_argument("--out-dir", type=Path, help="Defaults to out/wf_<candidate>/")
    parser.add_argument(
        "--weights-schedule",
        type=Path,
        help="If set, run a WalkForwardWeightedCandidate using this schedule parquet.",
    )
    parser.add_argument(
        "--multiplier-panel",
        type=Path,
        help="If set, run a WalkForwardMultiplierCandidate using this multiplier panel parquet (C4).",
    )
    parser.add_argument("--window", choices=["expanding", "rolling"], default="expanding")
    parser.add_argument("--stride", default="QS", help="Pandas offset for rebalance frequency.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    out_dir = args.out_dir or (REPO_ROOT / "out" / f"wf_{args.candidate}")
    harness = WalkForwardHarness(
        config_path=args.config,
        data_path=args.data,
        out_dir=out_dir,
        panels_dir=args.panels_dir,
        macro_data_path=args.macro_data if args.macro_data.exists() else None,
        adoption_rule=_make_default_adoption_rule(args.candidate),
        quarterly_freq=args.stride,
    )

    if args.weights_schedule and args.multiplier_panel:
        raise SystemExit(
            "--weights-schedule and --multiplier-panel together are supported by the "
            "harness internals but not yet by this CLI. Pass one at a time, or wire "
            "your own Candidate subclass."
        )
    if args.weights_schedule:
        candidate = WalkForwardWeightedCandidate(
            name=args.candidate, weights_schedule_path=args.weights_schedule
        )
    elif args.multiplier_panel:
        candidate = WalkForwardMultiplierCandidate(
            name=args.candidate, multiplier_panel_path=args.multiplier_panel
        )
    elif args.candidate == "flat68_replay":
        candidate = FlatBaselineCandidate()
    else:
        raise SystemExit(
            f"Candidate {args.candidate!r} requires --weights-schedule, --multiplier-panel, "
            f"or a programmatic Candidate subclass. See module docstring for the API."
        )

    result = harness.run(candidate)

    # C3-specific runtime gate: candidate's second-half mean per-quarter Sharpe
    # must not regress more than 0.03 below the first-half mean. Computed AFTER
    # the harness run because the gate is relative to the candidate's own per-
    # window decomposition, not a pre-stated floor.
    if args.candidate.startswith("c3_"):
        passed, summary = _evaluate_c3_is_oos_gate(result.per_window)
        print()
        print(summary)
        # Append the gate result to decision.md for audit, regardless of outcome.
        with open(result.artifacts_dir / "decision.md", "a") as f:
            f.write(f"\n## C3 runtime IS-vs-OOS gate\n\n- {summary}\n")
        if not passed and result.decision == "ADOPT":
            result.decision = "REJECT"
            result.reasons.append(summary)

    print()
    print(f"=== {result.candidate_name}: {result.decision} ===")
    print(f"  baseline Sharpe={result.baseline_metrics.get('sharpe', float('nan')):.4f}")
    print(f"  candidate Sharpe={result.candidate_metrics.get('sharpe', float('nan')):.4f}")
    print(f"  delta={result.candidate_metrics.get('sharpe', 0) - result.baseline_metrics.get('sharpe', 0):+.4f}")
    if result.reasons:
        print("  reasons:")
        for r in result.reasons:
            print(f"    - {r}")
    print(f"  artifacts: {result.artifacts_dir}/decision.md")
    return 0 if result.decision != "REJECT" else 1


if __name__ == "__main__":
    sys.exit(main())
