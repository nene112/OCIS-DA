from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

import pid_reconstruction
import plot_reach_flow_balance
import reach_missing_data_assimilation as da


# ---------------------------------------------------------------------------
# 一键运行设置：通常只需要改这里
# ---------------------------------------------------------------------------
CASE_NAME = "sj_zonggan-d0"
REACH_ID = 6
STEPS = 720
RUN_BASELINE = False
USE_TUNED_PID_PARAMETERS = True
WRITE_PLOT = True

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
    run_baseline=RUN_BASELINE,
    adaptive_gains=False,
    write_plot=WRITE_PLOT,
    # Only d6 flow is changed.  The DLL state is never force-corrected.
    bias_estimator_enabled=False,
    flow_tracking_enabled=True,
    flow_tracking_tolerance=0.01,
    flow_tracking_max_trials=8,
    # If the water-level target is infeasible under the downstream-gate flow
    # protection, apply the least-error feasible d6 flow and continue.
    flow_tracking_strict=False,
    enforce_diversion_not_above_downstream_gate=True,
)

SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = TOOLS_DIR.parent
DLL_PATH = TOOLS_DIR / "OcisMILPNet.dll"


def _load_tuned_parameters(pool_id: int, case_dir: Path) -> dict[str, Any]:
    """从 NSGA-II 调优结果读取该渠段 PID 参数；不存在或无效时返回空字典。"""
    summary_path = case_dir / "output" / "pid_nsga2_tuning" / str(pool_id) / "tuning_summary.json"
    if not summary_path.is_file():
        return {}
    try:
        tuning = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if tuning.get("final_error"):
        return {}

    selected = tuning.get("selected_parameters", {})
    parameters = {
        "kp": da._finite_float(selected.get("kp")),
        "ki": da._finite_float(selected.get("ki")),
        "q_max": da._finite_float(selected.get("q_max")),
    }
    if any(value is None for value in parameters.values()):
        return {}
    if parameters["kp"] < 0.0 or parameters["ki"] < 0.0 or parameters["q_max"] <= 0.0:
        return {}
    parameters["adaptive_gains"] = True
    parameters["source"] = str(summary_path)
    return parameters


def run_single_reach(
    pool_id: int,
    *,
    case_path: str | Path,
    output_root: str | Path,
    dll_path: str | Path,
    steps: int | None = None,
    run_baseline: bool | None = None,
    use_tuned: bool | None = None,
) -> dict[str, Any]:
    case_dir = Path(case_path).resolve()
    reaches = da.discover_reaches(case_dir)
    reach = next((item for item in reaches if item.pool_id == pool_id), None)
    if reach is None:
        raise ValueError(
            f"未找到 pool_id={pool_id} 的渠段，当前共发现 {len(reaches)} 个渠段"
        )
    reach = reach.normalized()

    config = replace(PID_CONFIG)
    if steps is not None:
        config.steps = int(steps)
    if run_baseline is not None:
        config.run_baseline = bool(run_baseline)

    use_tuned = USE_TUNED_PID_PARAMETERS if use_tuned is None else bool(use_tuned)
    parameter_source = "PID_CONFIG"
    if use_tuned:
        tuned = _load_tuned_parameters(reach.pool_id, case_dir)
        if tuned:
            config.kp = float(tuned["kp"])
            config.ki = float(tuned["ki"])
            config.q_max = float(tuned["q_max"])
            config.adaptive_gains = True
            parameter_source = tuned["source"]

    print(
        f"SINGLE_REACH_START pool={reach.pool_id} segment={reach.segment_name} "
        f"parameters={parameter_source} kp={config.kp} ki={config.ki} "
        f"q_max={config.q_max} adaptive={config.adaptive_gains}"
    )
    result = pid_reconstruction.run_pid_reconstruction(
        case_dir,
        reach,
        config=config,
        output_root=output_root,
        dll_path=dll_path,
    )
    print(f"RESULT_CSV={Path(result['output_csv']).resolve()}")
    if result.get("output_plot"):
        print(f"RESULT_PLOT={Path(result['output_plot']).resolve()}")
    print(f"RESULT_SUMMARY={Path(result['output_summary']).resolve()}")

    flow_plot = Path(result["output_csv"]).parent / "reach_flow_balance.png"
    plot_reach_flow_balance.main(
        ["--csv", str(result["output_csv"]), "--out", str(flow_plot)],
        gate_info=result.get("gate_info"),
        boundary_gate_id=result.get("gate_ids", {}).get("boundary"),
    )
    print(f"RESULT_FLOW_PLOT={flow_plot.resolve()}")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="单渠段 PID 边界流量重建一键脚本"
    )
    parser.add_argument("--case-name", default=CASE_NAME, help="案例名称")
    parser.add_argument("--pool-id", type=int, default=REACH_ID, help="要重建的渠段 id")
    parser.add_argument("--steps", type=int, default=None, help="覆盖默认计算步数")
    parser.add_argument(
        "--baseline",
        dest="run_baseline",
        action="store_true",
        default=RUN_BASELINE,
        help="运行 baseline 对比",
    )
    parser.add_argument(
        "--no-baseline",
        dest="run_baseline",
        action="store_false",
        help="不运行 baseline 对比",
    )
    parser.add_argument(
        "--no-tuned",
        dest="use_tuned",
        action="store_false",
        default=USE_TUNED_PID_PARAMETERS,
        help="不使用 NSGA-II 调优后的 PID 参数",
    )
    parser.add_argument("--output-root", type=Path, default=None, help="输出根目录")
    parser.add_argument("--dll-path", type=Path, default=None, help="水力 DLL 路径")
    args = parser.parse_args(argv)

    case_path = (PROJECT_DIR / "data" / args.case_name).resolve()
    output_root = args.output_root or case_path / "output" / "pid_single_reach_reconstruction"
    dll_path = args.dll_path or DLL_PATH

    try:
        run_single_reach(
            args.pool_id,
            case_path=case_path,
            output_root=output_root,
            dll_path=dll_path,
            steps=args.steps,
            run_baseline=args.run_baseline,
            use_tuned=args.use_tuned,
        )
    except Exception as exc:
        # pid_reconstruction writes the partial water-level/PID figure before it
        # raises.  Draw the matching flow-balance figure here as well, so a DLL
        # failure never leaves only a text diagnostic.
        partial_dir = Path(output_root) / str(args.pool_id)
        partial_csv = partial_dir / "partial_series.csv"
        partial_water_plot = partial_dir / "partial_assimilation_result.png"
        partial_flow_plot = partial_dir / "partial_reach_flow_balance.png"
        if partial_csv.is_file():
            try:
                plot_reach_flow_balance.main(
                    ["--csv", str(partial_csv), "--out", str(partial_flow_plot)]
                )
                print(f"PARTIAL_FLOW_PLOT={partial_flow_plot.resolve()}")
            except Exception as plot_exc:
                print(
                    "PARTIAL_FLOW_PLOT_FAILED "
                    f"{type(plot_exc).__name__}: {plot_exc}"
                )
        if partial_water_plot.is_file():
            print(f"PARTIAL_WATER_PLOT={partial_water_plot.resolve()}")
        print(f"SINGLE_REACH_FAILED {type(exc).__name__}: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
