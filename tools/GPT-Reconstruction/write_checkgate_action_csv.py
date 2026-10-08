"""Write the combined action_reconstruction.csv for the check-gate run.

Overwrites, in the base action.csv:
  * the reconstructed diversion boundary columns d{pool_id} (from
    ``q_boundary_analysis``), and
  * the reconstructed check-gate (节制闸) flow columns (target gate name,
    from ``check_gate_selected_flow`` forward-filled across steps).

All other columns are preserved from the base action.csv.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import reach_missing_data_assimilation as da

CASE_NAME = "sj_zonggan-d0"
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
CASE_PATH = PROJECT_DIR / "data" / CASE_NAME
OUTPUT_ROOT = CASE_PATH / "output" / "pid_checkgate_reconstruction"
SUMMARY = OUTPUT_ROOT / "checkgate_summary.json"


def _format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _applied_check_gate_flow(
    rows: list[dict[str, Any]],
) -> dict[datetime, float]:
    """Reconstruct the check-gate flow actually applied at every step.

    节制闸流量只在流量试算（存在实测水位目标）的步被记录为
    ``check_gate_selected_flow``；其余步沿用上一步的已施加值（无观测步）。在首个
    观测步之前，节制闸一直保持初始锚定值（即首个观测步的
    ``check_gate_current_flow``）。这里把它补齐成逐步完整序列。
    """
    ordered = sorted(rows, key=lambda r: int(r["step"]))
    first_selected = next(
        (r for r in ordered if r.get("check_gate_selected_flow") not in ("", None)),
        None,
    )
    last: float | None = None
    if first_selected is not None:
        last = da._finite_float(first_selected.get("check_gate_current_flow"))
    applied: dict[datetime, float] = {}
    for r in ordered:
        selected = da._finite_float(r.get("check_gate_selected_flow"))
        if selected is not None:
            last = selected
        ts = da._parse_datetime(r.get("time"))
        if ts is not None and last is not None:
            applied[ts] = float(last)
    return applied


def main() -> int:
    report = json.loads(SUMMARY.read_text(encoding="utf-8"))
    results = report["results"]
    reaches = da.discover_reaches(CASE_PATH)
    reach_by_pool = {reach.pool_id: reach.normalized() for reach in reaches}

    source_path = CASE_PATH / "input" / "action.csv"
    source_rows = list(csv.reader(da._read_text(source_path).splitlines()))
    header = [name.strip() for name in source_rows[0]]
    while header and not header[-1]:
        header.pop()

    source_series: dict[str, da.ObservationSeries] = {}
    for column_index, name in enumerate(header[1:], start=1):
        vals: list[tuple[datetime, float]] = []
        for row in source_rows[1:]:
            ts = da._parse_datetime(row[0] if row else None)
            v = da._finite_float(row[column_index] if column_index < len(row) else None)
            if ts is not None and v is not None:
                vals.append((ts, v))
        source_series[name] = da.ObservationSeries(vals)

    # Reconstructed diversion (分水) and check-gate (节制闸) actions.
    reconstructed: dict[str, dict[datetime, float]] = {}
    timeline: list[datetime] | None = None
    for result in results:
        if result.get("status") != "success" or not result.get("output_csv"):
            continue
        pool_id = int(result["pool_id"])
        reach = reach_by_pool.get(pool_id)
        if reach is None:
            continue
        boundary_name = reach.boundary_model_name
        gate_name = reach.target_gate_name
        diversion: dict[datetime, float] = {}
        step_timeline: list[datetime] = []
        pool_rows: list[dict[str, Any]] = []
        with Path(result["output_csv"]).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                ts = da._parse_datetime(row.get("time"))
                if ts is None:
                    continue
                step_timeline.append(ts)
                pool_rows.append(row)
                d = da._finite_float(row.get("q_boundary_analysis"))
                if d is not None:
                    diversion[ts] = d
        if timeline is None:
            timeline = step_timeline
        elif step_timeline != timeline:
            raise da.AssimilationError(f"渠段 {pool_id} 时间轴不一致")
        if boundary_name not in header:
            raise da.AssimilationError(f"action.csv 缺少重构分水列 {boundary_name}")
        if gate_name not in header:
            raise da.AssimilationError(f"action.csv 缺少节制闸列 {gate_name}")
        reconstructed[boundary_name] = diversion
        reconstructed[gate_name] = _applied_check_gate_flow(pool_rows)

    if not timeline:
        raise da.AssimilationError("缺少有效时间轴")

    output = OUTPUT_ROOT / "action_reconstruction.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        for t in timeline:
            row: list[Any] = [_format_time(t)]
            for name in header[1:]:
                if name in reconstructed and t in reconstructed[name]:
                    row.append(reconstructed[name][t])
                else:
                    # 未重构到该步（例如节制闸在首个观测前无动作）时回退到基础边界值。
                    v = source_series[name].value_at(t, 366 * 24 * 3600, "linear")
                    row.append("" if v is None else v)
            writer.writerow(row)
    print("RESULT_ACTION=" + str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
