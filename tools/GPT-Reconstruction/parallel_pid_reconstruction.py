from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import pid_reconstruction
import reach_missing_data_assimilation as da


# 一键运行设置：需要调整案例、计算时长或 PID 参数时，只修改本区。
CASE_NAME = "sj_zonggan-d0"
STEPS = 720
MIN_WATER_LEVEL_OBSERVATIONS = 2
# 每个任务会加载两份 DLL；默认限制为 2，避免过多实例并发初始化不稳定。
MAX_WORKERS: int | None = 2
PID_CONFIG = pid_reconstruction.PIDConfig(
    steps=STEPS,
    kp=40.0,
    ki=2.0,
    kd=0.0,
    integral_limit=150.0,
    anti_windup_gain=0.2,
    q_max_step=2.0,
    initial_water_depth=2.2,
    filter_flow_outliers=False,
    use_dll_flow_feedback=True,
)

SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = TOOLS_DIR.parent
CASE_PATH = PROJECT_DIR / "data" / CASE_NAME
DLL_PATH = TOOLS_DIR / "OcisMILPNet.dll"
OUTPUT_ROOT = CASE_PATH / "output" / "pid_parallel_reconstruction"


def select_reaches_with_water_levels(
    reaches: list[da.ReachSpec],
    gate_h1: dict[str, da.ObservationSeries],
    minimum_observations: int = 2,
    *,
    boundary_flow: dict[str, da.ObservationSeries] | None = None,
    model_gate_names: set[str] | None = None,
    model_start_time: datetime | None = None,
) -> tuple[list[da.ReachSpec], list[dict[str, Any]]]:
    if minimum_observations < 2:
        raise ValueError("minimum_observations 必须至少为 2，以支持线性插值")

    selected: list[da.ReachSpec] = []
    inventory: list[dict[str, Any]] = []
    for reach in reaches:
        normalized = reach.normalized()
        target_key = da._normalize_name(normalized.target_gate_name)
        segment_key = da._normalize_name(normalized.segment_name)
        series = gate_h1.get(target_key) or gate_h1.get(segment_key) or da.ObservationSeries()
        count = len(series.values)
        reasons: list[str] = []
        if count < minimum_observations:
            reasons.append(f"有效实测水位少于 {minimum_observations} 条")
        if model_gate_names is not None:
            boundary_key = da._normalize_name(normalized.boundary_model_name)
            missing_model_objects = [
                name
                for name, key in (
                    (normalized.target_gate_name, target_key),
                    (normalized.boundary_model_name, boundary_key),
                )
                if key not in model_gate_names
            ]
            if missing_model_objects:
                reasons.append(f"DLL 模型缺少对象: {', '.join(missing_model_objects)}")
        initial_boundary_q: float | None = None
        if boundary_flow is not None and model_start_time is not None:
            initial_boundary_q = da._collect_observations(
                da.ObservationBundle(boundary_flow=boundary_flow),
                normalized,
                model_start_time,
                da.AssimilationConfig(
                    ensemble_size=3,
                    observation_match="linear",
                    max_obs_gap_minutes=24 * 60,
                ),
            )["q_boundary"]
            if initial_boundary_q is None:
                reasons.append("模型起始时刻缺少基础分水流量")
        available = not reasons
        if available:
            selected.append(normalized)
        inventory.append(
            {
                "pool_id": normalized.pool_id,
                "segment": normalized.segment_name,
                "target_gate_name": normalized.target_gate_name,
                "water_level_observation_count": count,
                "water_level_start": _format_time(series.values[0][0]) if count else None,
                "water_level_end": _format_time(series.values[-1][0]) if count else None,
                "initial_boundary_flow": initial_boundary_q,
                "selected": available,
                "reason": None if available else "; ".join(reasons),
            }
        )
    return selected, inventory


def _format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _worker(payload: dict[str, Any]) -> dict[str, Any]:
    reach = da.ReachSpec(**payload["reach"])
    try:
        result = pid_reconstruction.run_pid_reconstruction(
            payload["case_path"],
            reach,
            config=pid_reconstruction.PIDConfig(**payload["pid_config"]),
            output_root=payload["output_root"],
            dll_path=payload["dll_path"],
        )
        return {"status": "success", **result}
    except Exception as exc:
        return {
            "status": "failed",
            "pool_id": reach.pool_id,
            "segment": reach.segment_name,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _worker_count(task_count: int, requested: int | None) -> int:
    if task_count <= 0:
        return 0
    if requested is not None:
        if requested <= 0:
            raise ValueError("MAX_WORKERS 必须大于 0 或设为 None")
        return min(task_count, requested)
    cpu_count = os.cpu_count() or 1
    return min(task_count, max(1, cpu_count // 2))


def _inspect_model(
    case_path: Path,
    config_path: Path,
    reach: da.ReachSpec,
) -> tuple[set[str], datetime | None]:
    hydraulic_config = da.AssimilationConfig(
        ensemble_size=3,
        steps=1,
        initial_water_depth=PID_CONFIG.initial_water_depth,
    )
    runtime_payload = da._build_runtime_config(
        config_path, case_path, reach, hydraulic_config
    )
    clients, dll_copies = da._load_isolated_clients(
        DLL_PATH.resolve(), 1, runtime_payload, hydraulic_config
    )
    try:
        client = clients[0]
        _, name_to_id = da._parse_gate_info(client.get_gate_info_sim())
        start_time, _ = da._parse_time_param(client.get_time_param())
        return set(name_to_id), start_time
    finally:
        da._release_isolated_clients(clients, dll_copies)


def run_parallel_pid() -> dict[str, Any]:
    case_path = CASE_PATH.resolve()
    output_root = OUTPUT_ROOT.resolve()
    if not case_path.is_dir():
        raise FileNotFoundError(f"案例目录不存在: {case_path}")
    if not DLL_PATH.is_file():
        raise FileNotFoundError(f"水动力 DLL 不存在: {DLL_PATH}")

    config_path = da._resolve_config_path(case_path, None)
    boundary_path = da._resolve_config_data_path(
        config_path, case_path, "SIM", "boundary_flow_path"
    )
    stage_path = case_path / "input" / "stage.csv"
    files = da._resolve_observation_files(
        case_path,
        da.ObservationFiles(
            boundary_flow=boundary_path,
            gate_h1=stage_path if stage_path.is_file() else None,
        ),
    )
    gate_h1 = da.load_observation_csv(files.gate_h1)
    boundary_flow = da.load_observation_csv(files.boundary_flow)
    reaches = da.discover_reaches(case_path)
    model_gate_names, model_start_time = _inspect_model(
        case_path, config_path, reaches[0]
    )
    selected, inventory = select_reaches_with_water_levels(
        reaches,
        gate_h1,
        MIN_WATER_LEVEL_OBSERVATIONS,
        boundary_flow=boundary_flow,
        model_gate_names=model_gate_names,
        model_start_time=model_start_time,
    )
    workers = _worker_count(len(selected), MAX_WORKERS)
    output_root.mkdir(parents=True, exist_ok=True)

    payloads = [
        {
            "case_path": str(case_path),
            "reach": asdict(reach),
            "pid_config": asdict(PID_CONFIG),
            "output_root": str(output_root),
            "dll_path": str(DLL_PATH.resolve()),
        }
        for reach in selected
    ]
    results: list[dict[str, Any]] = []
    if workers == 1:
        results = [_worker(payload) for payload in payloads]
    elif workers > 1:
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=get_context("spawn")
        ) as executor:
            future_map = {
                executor.submit(_worker, payload): payload for payload in payloads
            }
            for future in as_completed(future_map):
                payload = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    reach = payload["reach"]
                    results.append(
                        {
                            "status": "failed",
                            "pool_id": reach["pool_id"],
                            "segment": reach["segment_name"],
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
    results.sort(key=lambda item: int(item.get("pool_id", -1)))

    report = {
        "case_path": str(case_path),
        "water_level_file": str(files.gate_h1) if files.gate_h1 else None,
        "output_root": str(output_root),
        "configured_steps": PID_CONFIG.steps,
        "worker_count": workers,
        "reach_count": len(reaches),
        "selected_count": len(selected),
        "success_count": sum(item["status"] == "success" for item in results),
        "failed_count": sum(item["status"] == "failed" for item in results),
        "reach_inventory": inventory,
        "results": results,
    }
    report_path = output_root / "parallel_pid_summary.json"
    report["report_path"] = str(report_path)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    report = run_parallel_pid()
    print(
        f"可用渠段 {report['selected_count']}/{report['reach_count']}，"
        f"成功 {report['success_count']}，失败 {report['failed_count']}"
    )
    print(f"RESULT_REPORT={report['report_path']}")
    print(f"RESULT_DIR={report['output_root']}")
    for result in report["results"]:
        if result["status"] == "failed":
            print(f"FAILED pool={result['pool_id']} {result['segment']}: {result['error']}")
    return 0 if report["failed_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())