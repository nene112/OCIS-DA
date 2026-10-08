"""Boundary reconstruction for all reconstructable reaches with observed water levels.

Uses the robust one-step flow-tracking (binary search) mode instead of plain
PID, so the reconstructed diversion flow directly reproduces the observed
target-gate water level without PID overshoot/divergence.

Reach selection mirrors parallel_pid_reconstruction.py: target gate must have
>=2 valid water-level observations, the model must expose the target gate and
the reach diversion gate (d{pool_id}), and an initial boundary flow must be
derivable at the model start time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pid_reconstruction
import reach_missing_data_assimilation as da
import parallel_pid_reconstruction as parallel_pid

CASE_NAME = "sj_zonggan-d0"
STEPS = 720
Q_MAX = 50.0  # generous search upper bound for the diversion flow

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
CASE_PATH = PROJECT_DIR / "data" / CASE_NAME
DLL_PATH = SCRIPT_DIR.parent / "OcisMILPNet.dll"
OUTPUT_ROOT = CASE_PATH / "output" / "pid_flowtrack_reconstruction"


def _config(q_max: float) -> pid_reconstruction.PIDConfig:
    return pid_reconstruction.PIDConfig(
        steps=STEPS,
        kp=0.15,
        ki=0.35,
        kd=0.0,
        integral_limit=150.0,
        anti_windup_gain=0.2,
        q_max_step=2.0,
        q_max=q_max,
        initial_water_depth=2.2,
        filter_flow_outliers=False,
        use_dll_flow_feedback=True,
        run_baseline=False,
        minimum_valid_water_level=0.2,
        write_plot=True,
        adaptive_gains=False,
        bias_estimator_enabled=False,
        flow_tracking_enabled=True,
        flow_tracking_tolerance=0.01,
        flow_tracking_max_trials=8,
        flow_tracking_strict=False,
        enforce_diversion_not_above_downstream_gate=True,
    )


def _run_one_worker(case_path, reach, config, output_root, dll_path, files):
    """Top-level worker for ProcessPoolExecutor (must be picklable)."""
    return pid_reconstruction.run_pid_reconstruction(
        case_path,
        reach,
        config=config,
        output_root=output_root,
        dll_path=dll_path,
        observation_files=files,
    )


def main() -> int:
    if not DLL_PATH.is_file():
        raise FileNotFoundError(f"水动力 DLL 不存在: {DLL_PATH}")

    config_path = da._resolve_config_path(CASE_PATH, None)
    boundary_path = da._resolve_config_data_path(
        config_path, CASE_PATH, "SIM", "boundary_flow_path"
    )
    stage_path = da._resolve_config_data_path(
        config_path, CASE_PATH, "SIM", "boundary_stage_path"
    )
    files = da._resolve_observation_files(
        CASE_PATH,
        da.ObservationFiles(boundary_flow=boundary_path, gate_h1=stage_path),
    )
    gate_h1 = pid_reconstruction.filter_valid_water_levels(
        da.load_observation_csv(files.gate_h1), 0.2
    )
    boundary_flow = da.load_observation_csv(files.boundary_flow)
    reaches = da.discover_reaches(CASE_PATH)
    model_gate_names, model_start_time = parallel_pid._inspect_model(
        CASE_PATH, config_path, reaches[0]
    )

    selected, inventory = parallel_pid.select_reaches_with_water_levels(
        reaches,
        gate_h1,
        parallel_pid.MIN_WATER_LEVEL_OBSERVATIONS,
        boundary_flow=boundary_flow,
        model_gate_names=model_gate_names,
        model_start_time=model_start_time,
    )
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    from concurrent.futures import ProcessPoolExecutor, as_completed

    worker_args = [
        (CASE_PATH, reach, _config(Q_MAX), OUTPUT_ROOT, DLL_PATH, files)
        for reach in selected
    ]
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=min(4, len(worker_args))) as pool:
        futures = {pool.submit(_run_one_worker, *args): reach for args, reach in zip(worker_args, selected)}
        for future in as_completed(futures):
            reach = futures[future]
            print(
                f"RECONSTRUCT_START pool={reach.pool_id} segment={reach.segment_name} "
                f"boundary={reach.boundary_model_name}"
            )
            try:
                result = future.result()
                results.append({"status": "success", **result})
                print(
                    f"RECONSTRUCT_OK pool={reach.pool_id} "
                    f"rmse={result['rmse_h1_at_raw_observations']}"
                )
            except Exception as exc:
                results.append(
                    {
                        "status": "failed",
                        "pool_id": reach.pool_id,
                        "segment": reach.segment_name,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(f"RECONSTRUCT_FAILED pool={reach.pool_id} {type(exc).__name__}: {exc}")

    report = {
        "case_path": str(CASE_PATH),
        "water_level_file": str(files.gate_h1) if files.gate_h1 else None,
        "output_root": str(OUTPUT_ROOT),
        "configured_steps": STEPS,
        "q_max": Q_MAX,
        "method": "hourly PID + flow tracking",
        "reach_count": len(reaches),
        "selected_count": len(selected),
        "success_count": sum(r["status"] == "success" for r in results),
        "failed_count": sum(r["status"] == "failed" for r in results),
        "reach_inventory": inventory,
        "results": results,
    }
    report_path = OUTPUT_ROOT / "flowtrack_summary.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"可用渠段 {report['selected_count']}/{report['reach_count']}，"
        f"成功 {report['success_count']}，失败 {report['failed_count']}"
    )
    print(f"RESULT_REPORT={report_path}")
    return 0 if report["failed_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
