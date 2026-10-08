"""dayudu 案例边界重建：节制闸为“开度 e”边界。

与 sj_zonggan-d0 的关键差异：

* dayudu 多个节制闸（老庄、圪塔、15号桥、富家岭、一级路南、刘原、阳院节制闸）
  的边界是“闸门开度 e”，而不是直接给定流量。DLL 通过 set_GatesFlow_e_byID_sim
  直接设置开度，Roe 求解器按孔口/堰流公式 Q=Cd·b·e·sqrt(2g·ΔH) 反算过闸流量，
  从而改变闸前水位 h1。分水（d{pool_id}）仍然是流量边界。

重建策略（两级、逐级放宽容差）：

1. 第一阶段只调分水 d{pool_id}（流量边界，二分逼近目标 h1）。
2. 分水无法改善时，第二阶段开始调节制闸（目标闸）**开度 e**：
   动作幅度上限按 [0, e_max] 全量程的百分比逐级递增（10%→20%→30%…），
   每级在当前开度 ± 幅度 窗口内选取最接近目标的开度；无法改善就继续加大幅度，
   直到达标或达到 100% 上限。e_max 来自 gate_hole_counts.csv 每闸物理上限。

输出：action_reconstruction.csv 包含完整边界条件——
  二级站出口入流、d0~d10 分水流量、各节制闸开度 e（米）。
"""
from __future__ import annotations

import csv
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import pid_reconstruction
import reach_missing_data_assimilation as da
import parallel_pid_reconstruction as parallel_pid

CASE_NAME = "dayudu"
STEPS = 46  # 模型 T=46（dt=3600）
Q_MAX = 15.0  # 分水流量搜索上界（d0~d10 量级 0~5 m³/s）
FLOW_TRACKING_TOLERANCE = 0.02  # 2 cm 跟踪容差

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
CASE_PATH = PROJECT_DIR / "data" / CASE_NAME
DLL_PATH = SCRIPT_DIR.parent / "OcisMILPNet.dll"
OUTPUT_ROOT = CASE_PATH / "output" / "pid_opening_reconstruction"

# 闸孔数量与开度上限（米），来自 input/gate_hole_counts.csv。
_GATE_OPENING_MAX_M = {
    "老庄节制闸": 1.5,
    "圪塔节制闸": 1.2,
    "15号桥节制闸": 0.5,
    "富家岭节制闸": 0.38,
    "一级路南节制闸": 0.5,
    "刘原节制闸": 0.8,
    "阳院节制闸": 0.5,
    "杨元坝节制闸": 0.768571,
    "麻原节制闸": 0.768571,
    "17号桥节制闸": 0.768571,
}


def _gate_opening_max_m() -> dict[str, float]:
    """从 gate_hole_counts.csv 读取每闸开度上限（米），失败则退回内置表。"""
    csv_path = CASE_PATH / "input" / "gate_hole_counts.csv"
    result: dict[str, float] = dict(_GATE_OPENING_MAX_M)
    try:
        text = da._read_text(csv_path)
        for row in csv.DictReader(text.splitlines()):
            name = str(row.get("gate", "") or "").strip()
            max_m = da._finite_float(row.get("max_m"))
            if name and max_m is not None and max_m > 0:
                result[name] = float(max_m)
    except Exception:
        pass
    return result


def _config_for_reach(opening_max_m: float) -> pid_reconstruction.PIDConfig:
    return pid_reconstruction.PIDConfig(
        steps=STEPS,
        kp=0.15,
        ki=0.35,
        kd=0.0,
        integral_limit=150.0,
        anti_windup_gain=0.2,
        q_max_step=2.0,
        q_max=Q_MAX,
        initial_water_depth=2.2,
        filter_flow_outliers=False,
        use_dll_flow_feedback=True,
        run_baseline=False,
        minimum_valid_water_level=0.2,
        write_plot=True,
        adaptive_gains=False,
        bias_estimator_enabled=False,
        flow_tracking_enabled=True,
        flow_tracking_tolerance=FLOW_TRACKING_TOLERANCE,
        flow_tracking_max_trials=8,
        flow_tracking_strict=False,
        enforce_diversion_not_above_downstream_gate=False,
        # 节制闸（目标闸）按“开度 e”调节，动作幅度 10% -> 20% -> ... 逐级递增。
        check_gate_enabled=True,
        check_gate_mode="opening",
        check_gate_amplitude_start=0.10,
        check_gate_amplitude_step=0.10,
        check_gate_amplitude_max=1.0,
        check_gate_opening_min=0.0,
        check_gate_opening_max=opening_max_m,
        check_gate_opening_obs_unit="mm",
        check_gate_opening_auto_range=False,
        check_gate_search_points=21,
    )


def _run_one_worker(args):
    case_path, reach, config, output_root, dll_path, files = args
    return pid_reconstruction.run_pid_reconstruction(
        case_path,
        reach,
        config=config,
        output_root=output_root,
        dll_path=dll_path,
        observation_files=files,
    )


def _load_result_series(output_csv: str, key: str) -> dict[datetime, float]:
    """从某渠段结果 CSV 抽取指定列的时序。"""
    series: dict[datetime, float] = {}
    with Path(output_csv).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            timestamp = da._parse_datetime(row.get("time"))
            value = da._finite_float(row.get(key))
            if timestamp is not None and value is not None:
                series[timestamp] = value
    return series


def write_action_reconstruction(
    results: list[dict[str, Any]],
    reaches: list[da.ReachSpec],
    opening_max_m: dict[str, float],
    timeline: list[datetime],
) -> Path:
    """写出完整边界条件。

    列：tm, 二级站出口(流量), d0~d10(分水流量), 各节制闸开度 e(m)。
    """
    successful = {
        int(result["pool_id"]): result
        for result in results
        if result.get("status") == "success" and result.get("output_csv")
    }
    if not successful:
        raise da.AssimilationError("没有成功的渠段结果，无法生成重构边界条件")

    # 基准边界（二级站出口入流、d0~d10 分水）来自 input/action_td.csv。
    base_action = da.load_observation_csv(CASE_PATH / "input" / "action_td.csv")

    reach_by_pool = {reach.pool_id: reach.normalized() for reach in reaches}

    diversion: dict[str, dict[datetime, float]] = {}
    opening: dict[str, dict[datetime, float]] = {}
    gate_flow: dict[str, dict[datetime, float]] = {}
    for pool_id, result in successful.items():
        reach = reach_by_pool.get(pool_id)
        if reach is None:
            continue
        csv_path = result["output_csv"]
        diversion[reach.boundary_model_name] = _load_result_series(
            csv_path, "q_boundary_analysis"
        )
        # 目标闸即节制闸；开度取模型实际执行后的 gates_e（米）。
        opening[reach.target_gate_name] = _load_result_series(
            csv_path, "gate_opening_analysis"
        )
        gate_flow[reach.target_gate_name] = _load_result_series(
            csv_path, "gate_q_analysis"
        )

    output_path = OUTPUT_ROOT / "action_reconstruction.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    header = ["tm", "二级站出口"]
    header += [f"d{pool_id}" for pool_id in range(11)]
    gate_names = [
        "老庄节制闸", "圪塔节制闸", "15号桥节制闸", "富家岭节制闸",
        "一级路南节制闸", "刘原节制闸", "阳院节制闸",
        "杨元坝节制闸", "麻原节制闸", "17号桥节制闸",
    ]
    for name in gate_names:
        header.append(f"{name}_e_m")
        header.append(f"{name}_Q_m3s")

    # 未重构闸（杨元坝、麻原、17号桥）保持默认开度。
    default_opening = {
        name: opening_max_m.get(name, 0.768571)
        for name in gate_names
        if name not in opening
    }

    def value_at(series: dict[datetime, float], ts: datetime) -> float | None:
        if not series:
            return None
        values = sorted(series)
        if ts < values[0] or ts > values[-1]:
            return None
        # 线性插值
        for i in range(len(values) - 1):
            if values[i] <= ts <= values[i + 1]:
                dt = (values[i + 1] - values[i]).total_seconds()
                if dt == 0:
                    return series[values[i]]
                frac = (ts - values[i]).total_seconds() / dt
                return series[values[i]] * (1 - frac) + series[values[i + 1]] * frac
        return None

    inlet_series = base_action.get("二级站出口")
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        for ts in timeline:
            row: list[Any] = [ts.strftime("%Y-%m-%d %H:%M:%S")]
            inlet = inlet_series.value_at(ts, 366 * 24 * 3600, "linear") if inlet_series else None
            row.append("" if inlet is None else round(inlet, 6))
            for pool_id in range(11):
                series = diversion.get(f"d{pool_id}", {})
                value = value_at(series, ts)
                if value is None:
                    base = base_action.get(f"d{pool_id}")
                    value = base.value_at(ts, 366 * 24 * 3600, "linear") if base else None
                row.append("" if value is None else round(value, 6))
            for name in gate_names:
                series = opening.get(name)
                e = value_at(series, ts) if series else None
                if e is None and name in default_opening:
                    e = default_opening[name]
                row.append("" if e is None else round(e, 6))
                q_series = gate_flow.get(name)
                q = value_at(q_series, ts) if q_series else None
                row.append("" if q is None else round(q, 6))
            writer.writerow(row)
    return output_path


def main() -> int:
    if not DLL_PATH.is_file():
        raise FileNotFoundError(f"水动力 DLL 不存在: {DLL_PATH}")

    config_path = da._resolve_config_path(CASE_PATH, None)
    # 分水流量边界用 action_td.csv（含 d0~d10 列）；h1 用 stage1_td.csv（GBK）；
    # 开度观测用 gate_e_td.csv（mm）。
    files = da._resolve_observation_files(
        CASE_PATH,
        da.ObservationFiles(
            boundary_flow=CASE_PATH / "input" / "action_td.csv",
            gate_h1=CASE_PATH / "input" / "stage1_td.csv",
            gate_h2=CASE_PATH / "input" / "stage2_td.csv",
            gate_flow=CASE_PATH / "input" / "action_td.csv",
            gate_opening=CASE_PATH / "input" / "gate_e_td.csv",
        ),
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

    opening_max_m = _gate_opening_max_m()
    worker_args = []
    for reach in selected:
        target_max = opening_max_m.get(reach.target_gate_name, 1.5)
        worker_args.append(
            (CASE_PATH, reach, _config_for_reach(target_max), OUTPUT_ROOT, DLL_PATH, files)
        )

    results: list[dict[str, Any]] = []
    if worker_args:
        with ProcessPoolExecutor(max_workers=min(4, len(worker_args))) as pool:
            futures = {pool.submit(_run_one_worker, args): args[1] for args in worker_args}
            for future in as_completed(futures):
                reach = futures[future]
                print(
                    f"RECONSTRUCT_START pool={reach.pool_id} segment={reach.segment_name} "
                    f"target={reach.target_gate_name} boundary={reach.boundary_model_name}"
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
                            "target_gate_name": reach.target_gate_name,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    print(f"RECONSTRUCT_FAILED pool={reach.pool_id} {type(exc).__name__}: {exc}")
    results.sort(key=lambda item: int(item.get("pool_id", -1)))

    action_path: Path | None = None
    if any(item["status"] == "success" for item in results):
        # 时间轴取自首个成功结果。
        first = next(
            item for item in results if item.get("status") == "success" and item.get("output_csv")
        )
        timeline = sorted(_load_result_series(first["output_csv"], "q_boundary_analysis"))
        action_path = write_action_reconstruction(results, reaches, opening_max_m, timeline)

    report = {
        "case_path": str(CASE_PATH),
        "water_level_file": str(files.gate_h1) if files.gate_h1 else None,
        "opening_file": str(files.gate_opening) if files.gate_opening else None,
        "output_root": str(OUTPUT_ROOT),
        "configured_steps": STEPS,
        "q_max": Q_MAX,
        "flow_tracking_tolerance": FLOW_TRACKING_TOLERANCE,
        "method": "opening-based check-gate fallback (10/20/30%...)",
        "reach_count": len(reaches),
        "selected_count": len(selected),
        "success_count": sum(item["status"] == "success" for item in results),
        "failed_count": sum(item["status"] == "failed" for item in results),
        "action_reconstruction": str(action_path) if action_path else None,
        "reach_inventory": inventory,
        "results": results,
    }
    report_path = OUTPUT_ROOT / "opening_reconstruction_summary.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"可用渠段 {report['selected_count']}/{report['reach_count']}，"
        f"成功 {report['success_count']}，失败 {report['failed_count']}"
    )
    print(f"RESULT_REPORT={report_path}")
    if action_path:
        print(f"RESULT_ACTION={action_path}")
    for item in results:
        if item["status"] == "failed":
            print(f"FAILED pool={item['pool_id']} {item['segment']}: {item['error']}")
    return 0 if report["failed_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
