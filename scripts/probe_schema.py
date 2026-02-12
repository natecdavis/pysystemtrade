#!/usr/bin/env python3
"""
Schema Probe Script

Verifies available fields in backtest output files before writing analysis code.
Prevents assumptions about what fields exist in diagnostics/PnL breakdown.

Usage:
    python scripts/probe_schema.py --backtest-dir out/stage1_baseline
"""

import argparse
from pathlib import Path
import pandas as pd
import json


def probe_parquet(file_path: Path) -> dict:
    """Probe a parquet file and return schema info."""
    if not file_path.exists():
        return {"exists": False}

    df = pd.read_parquet(file_path)

    return {
        "exists": True,
        "rows": len(df),
        "columns": len(df.columns),
        "fields": df.columns.tolist(),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "sample": df.head(3).to_dict() if len(df) > 0 else {}
    }


def probe_csv(file_path: Path) -> dict:
    """Probe a CSV file and return schema info."""
    if not file_path.exists():
        return {"exists": False}

    df = pd.read_csv(file_path)

    return {
        "exists": True,
        "rows": len(df),
        "columns": len(df.columns),
        "fields": df.columns.tolist(),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "sample": df.head(3).to_dict() if len(df) > 0 else {}
    }


def probe_json(file_path: Path) -> dict:
    """Probe a JSON file and return schema info."""
    if not file_path.exists():
        return {"exists": False}

    with open(file_path, 'r') as f:
        data = json.load(f)

    return {
        "exists": True,
        "type": type(data).__name__,
        "keys": list(data.keys()) if isinstance(data, dict) else None,
        "sample": data
    }


def main():
    parser = argparse.ArgumentParser(
        description='Probe backtest output schema'
    )
    parser.add_argument(
        '--backtest-dir',
        required=True,
        help='Path to backtest output directory (e.g., out/stage1_baseline)'
    )

    args = parser.parse_args()
    backtest_dir = Path(args.backtest_dir)

    if not backtest_dir.exists():
        print(f"ERROR: Directory not found: {backtest_dir}")
        return 1

    print(f"=== Schema Probe: {backtest_dir} ===\n")

    # Probe diagnostics.parquet
    diagnostics_path = backtest_dir / "diagnostics.parquet"
    print("diagnostics.parquet:")
    diag_info = probe_parquet(diagnostics_path)
    if diag_info["exists"]:
        print(f"  Rows: {diag_info['rows']}")
        print(f"  Columns: {diag_info['columns']}")
        print(f"  Fields:")
        for field in diag_info["fields"]:
            dtype = diag_info["dtypes"][field]
            print(f"    - {field} ({dtype})")
    else:
        print("  NOT FOUND")
    print()

    # Probe pnl_breakdown.csv
    pnl_path = backtest_dir / "pnl_breakdown.csv"
    print("pnl_breakdown.csv:")
    pnl_info = probe_csv(pnl_path)
    if pnl_info["exists"]:
        print(f"  Rows: {pnl_info['rows']}")
        print(f"  Columns: {pnl_info['columns']}")
        print(f"  Fields:")
        for field in pnl_info["fields"]:
            dtype = pnl_info["dtypes"][field]
            print(f"    - {field} ({dtype})")
    else:
        print("  NOT FOUND")
    print()

    # Probe equity_curve.csv
    equity_path = backtest_dir / "equity_curve.csv"
    print("equity_curve.csv:")
    equity_info = probe_csv(equity_path)
    if equity_info["exists"]:
        print(f"  Rows: {equity_info['rows']}")
        print(f"  Columns: {equity_info['columns']}")
        print(f"  Fields:")
        for field in equity_info["fields"]:
            dtype = equity_info["dtypes"][field]
            print(f"    - {field} ({dtype})")
    else:
        print("  NOT FOUND")
    print()

    # Probe positions.csv
    positions_path = backtest_dir / "positions.csv"
    print("positions.csv:")
    positions_info = probe_csv(positions_path)
    if positions_info["exists"]:
        print(f"  Rows: {positions_info['rows']}")
        print(f"  Columns: {positions_info['columns']}")
        print(f"  Fields:")
        for field in positions_info["fields"]:
            dtype = positions_info["dtypes"][field]
            print(f"    - {field} ({dtype})")
    else:
        print("  NOT FOUND")
    print()

    # Probe metadata.json
    metadata_path = backtest_dir / "metadata.json"
    print("metadata.json:")
    metadata_info = probe_json(metadata_path)
    if metadata_info["exists"]:
        print(f"  Type: {metadata_info['type']}")
        if metadata_info["keys"]:
            print(f"  Keys:")
            for key in metadata_info["keys"]:
                print(f"    - {key}")
    else:
        print("  NOT FOUND")
    print()

    print("=== Schema Probe Complete ===")
    return 0


if __name__ == "__main__":
    exit(main())
