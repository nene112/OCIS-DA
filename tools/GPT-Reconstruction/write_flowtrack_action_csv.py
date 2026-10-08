"""Write the combined action_reconstruction.csv for the flow-track run.

Reuses parallel_pid.write_action_reconstruction so the reconstructed
diversion boundary columns (d5/d6/d7/d8) overwrite the base action.csv and
everything else is preserved, matching the parallel PID run's convention.
"""
from __future__ import annotations

import json
from pathlib import Path

import reach_missing_data_assimilation as da
import parallel_pid_reconstruction as parallel_pid

CASE_NAME = "sj_zonggan-d0"
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
CASE_PATH = PROJECT_DIR / "data" / CASE_NAME
OUTPUT_ROOT = CASE_PATH / "output" / "pid_flowtrack_reconstruction"
SUMMARY = OUTPUT_ROOT / "flowtrack_summary.json"


def main() -> int:
    report = json.loads(SUMMARY.read_text(encoding="utf-8"))
    results = report["results"]
    reaches = da.discover_reaches(CASE_PATH)
    source = CASE_PATH / "input" / "action.csv"
    out = parallel_pid.write_action_reconstruction(
        source, OUTPUT_ROOT / "action_reconstruction.csv", results, reaches
    )
    print("RESULT_ACTION=" + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
