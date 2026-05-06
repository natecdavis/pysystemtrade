"""Unit tests for the manifest hash chain."""

import json
from pathlib import Path

import pytest

from sysdata.crypto.manifest_chain import (
    REQUIRED_STAGES,
    ManifestChainError,
    append_stage,
    file_sha256,
    find_latest_complete_run,
    load_chain,
    new_run_id,
    save_chain,
    verify_chain,
    verify_input_against_upstream,
)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_complete_run(
    chain: Path,
    *,
    dataset: Path,
    positions: Path,
    trade_plan: Path,
    run_id: str,
) -> None:
    """Append a full dataset_build → backtest → trade_plan run to the chain."""
    append_stage(chain, "dataset_build", outputs={"dataset": dataset}, run_id=run_id)
    append_stage(
        chain,
        "backtest",
        inputs={"dataset": dataset},
        outputs={"positions": positions},
        run_id=run_id,
    )
    append_stage(
        chain,
        "trade_plan",
        inputs={"positions": positions},
        outputs={"trade_plan": trade_plan},
        run_id=run_id,
    )


class TestFileSha256:
    def test_deterministic(self, tmp_path):
        target = tmp_path / "x.bin"
        _write(target, b"hello world")
        first = file_sha256(target)
        second = file_sha256(target)
        assert first == second
        assert len(first) == 64

    def test_changes_with_content(self, tmp_path):
        target = tmp_path / "x.bin"
        _write(target, b"a")
        a_hash = file_sha256(target)
        _write(target, b"b")
        b_hash = file_sha256(target)
        assert a_hash != b_hash


class TestAppendStage:
    def test_records_outputs(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        _write(dataset, b"dataset-bytes")

        entry = append_stage(
            chain,
            stage="dataset_build",
            outputs={"dataset": dataset},
            extra={"config": "test.yaml"},
        )
        assert entry["stage"] == "dataset_build"
        assert entry["outputs"]["dataset"]["sha256"] == file_sha256(dataset)
        assert entry["extra"]["config"] == "test.yaml"

        loaded = load_chain(chain)
        assert len(loaded) == 1
        assert loaded[0]["outputs"]["dataset"]["sha256"] == entry["outputs"]["dataset"]["sha256"]

    def test_appends_multiple(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        positions = tmp_path / "positions.csv"
        _write(dataset, b"dataset-bytes")
        _write(positions, b"date,inst\n2026-04-30,BTC")

        append_stage(chain, stage="dataset_build", outputs={"dataset": dataset})
        append_stage(
            chain,
            stage="backtest",
            inputs={"dataset": dataset},
            outputs={"positions": positions},
        )

        loaded = load_chain(chain)
        assert [e["stage"] for e in loaded] == ["dataset_build", "backtest"]


class TestVerifyInputAgainstUpstream:
    def test_match_passes(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        _write(dataset, b"dataset-bytes")
        append_stage(chain, stage="dataset_build", outputs={"dataset": dataset})

        # Should not raise
        verify_input_against_upstream(chain, "dataset_build", "dataset", dataset)

    def test_mismatch_raises(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        _write(dataset, b"original-bytes")
        append_stage(chain, stage="dataset_build", outputs={"dataset": dataset})

        # Simulate a half-write or out-of-band replacement of the dataset
        _write(dataset, b"corrupted-bytes")

        with pytest.raises(ManifestChainError, match="Hash mismatch"):
            verify_input_against_upstream(chain, "dataset_build", "dataset", dataset)

    def test_missing_chain_raises(self, tmp_path):
        chain = tmp_path / "missing.json"
        dataset = tmp_path / "dataset.parquet"
        _write(dataset, b"x")

        with pytest.raises(ManifestChainError, match="not found"):
            verify_input_against_upstream(chain, "dataset_build", "dataset", dataset)

    def test_missing_upstream_stage_raises(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        _write(dataset, b"x")
        # Chain exists but doesn't contain the requested upstream stage
        append_stage(chain, stage="some_other_stage", outputs={"dataset": dataset})

        with pytest.raises(ManifestChainError, match="did not record"):
            verify_input_against_upstream(chain, "dataset_build", "dataset", dataset)


class TestVerifyChain:
    def test_clean_complete_run_passes(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        positions = tmp_path / "positions.csv"
        trade_plan = tmp_path / "trade_plan.csv"
        _write(dataset, b"d")
        _write(positions, b"p")
        _write(trade_plan, b"t")

        rid = new_run_id()
        _build_complete_run(
            chain,
            dataset=dataset,
            positions=positions,
            trade_plan=trade_plan,
            run_id=rid,
        )

        result = verify_chain(chain)
        assert result["passed"]
        assert result["stages"] == 3
        assert result["run_id"] == rid
        assert sorted(result["stages_in_latest_run"]) == sorted(REQUIRED_STAGES)
        assert result["legacy_skipped"] == 0

    def test_detects_post_run_drift(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        positions = tmp_path / "positions.csv"
        trade_plan = tmp_path / "trade_plan.csv"
        _write(dataset, b"d")
        _write(positions, b"p")
        _write(trade_plan, b"t")
        rid = new_run_id()
        _build_complete_run(
            chain,
            dataset=dataset,
            positions=positions,
            trade_plan=trade_plan,
            run_id=rid,
        )

        # Mutate dataset after the chain was sealed
        _write(dataset, b"different")

        result = verify_chain(chain)
        assert not result["passed"]
        assert any(i["issue"] == "hash_mismatch" for i in result["issues"])

    def test_no_complete_run_fails(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        _write(dataset, b"d")
        rid = new_run_id()
        # Only dataset_build — no backtest, no trade_plan
        append_stage(chain, "dataset_build", outputs={"dataset": dataset}, run_id=rid)

        result = verify_chain(chain)
        assert not result["passed"]
        assert result["run_id"] is None
        assert any(i["issue"] == "no_complete_run" for i in result["issues"])

    def test_verify_only_validates_latest_run(self, tmp_path):
        """
        Two distinct pipeline runs in the same chain. Only the latest run's files
        match disk; the earlier run's outputs were overwritten. Verifier must pass
        because we only validate the latest fully-complete run.
        """
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        positions = tmp_path / "positions.csv"
        trade_plan = tmp_path / "trade_plan.csv"

        # Run 1 writes initial bytes
        _write(dataset, b"v1-dataset")
        _write(positions, b"v1-positions")
        _write(trade_plan, b"v1-trade-plan")
        rid1 = new_run_id()
        _build_complete_run(
            chain,
            dataset=dataset,
            positions=positions,
            trade_plan=trade_plan,
            run_id=rid1,
        )

        # Run 2 overwrites the artifacts and appends new chain entries
        _write(dataset, b"v2-dataset")
        _write(positions, b"v2-positions")
        _write(trade_plan, b"v2-trade-plan")
        rid2 = new_run_id()
        _build_complete_run(
            chain,
            dataset=dataset,
            positions=positions,
            trade_plan=trade_plan,
            run_id=rid2,
        )

        result = verify_chain(chain)
        assert result["passed"], result["issues"]
        assert result["run_id"] == rid2
        assert result["stages"] == 3

    def test_legacy_entries_without_run_id_skipped(self, tmp_path):
        """Legacy entries (pre run_id) are kept in the file but not validated."""
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        positions = tmp_path / "positions.csv"
        trade_plan = tmp_path / "trade_plan.csv"
        _write(dataset, b"d")
        _write(positions, b"p")
        _write(trade_plan, b"t")

        # Hand-craft a legacy entry referencing a now-missing path. If verify_chain
        # walked it, this would fail; instead it's skipped.
        legacy_entry = {
            "stage": "dataset_build",
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
            "inputs": {},
            "outputs": {
                "dataset": {
                    "path": str(tmp_path / "ghost_dataset.parquet"),
                    "sha256": "deadbeef" * 8,
                    "size_bytes": 0,
                }
            },
        }
        save_chain(chain, [legacy_entry])

        rid = new_run_id()
        _build_complete_run(
            chain,
            dataset=dataset,
            positions=positions,
            trade_plan=trade_plan,
            run_id=rid,
        )

        result = verify_chain(chain)
        assert result["passed"], result["issues"]
        assert result["run_id"] == rid
        assert result["legacy_skipped"] == 1


class TestRunIdPropagation:
    def test_append_stage_default_generates_run_id(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        _write(dataset, b"d")
        entry = append_stage(chain, "dataset_build", outputs={"dataset": dataset})
        assert entry["run_id"], "append_stage should auto-generate a run_id"

    def test_append_stage_uses_provided_run_id(self, tmp_path):
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        _write(dataset, b"d")
        rid = new_run_id()
        entry = append_stage(
            chain, "dataset_build", outputs={"dataset": dataset}, run_id=rid
        )
        assert entry["run_id"] == rid

    def test_run_id_propagates_through_stages(self, tmp_path):
        """
        Simulate the orchestrator: a single run_id is threaded through all three
        stages. find_latest_complete_run() should return that one run_id and all
        three entries.
        """
        chain = tmp_path / "chain.json"
        dataset = tmp_path / "dataset.parquet"
        positions = tmp_path / "positions.csv"
        trade_plan = tmp_path / "trade_plan.csv"
        _write(dataset, b"d")
        _write(positions, b"p")
        _write(trade_plan, b"t")

        rid = new_run_id()
        _build_complete_run(
            chain,
            dataset=dataset,
            positions=positions,
            trade_plan=trade_plan,
            run_id=rid,
        )

        entries = load_chain(chain)
        assert all(e["run_id"] == rid for e in entries)

        found_rid, group = find_latest_complete_run(entries)
        assert found_rid == rid
        assert {e["stage"] for e in group} == set(REQUIRED_STAGES)
