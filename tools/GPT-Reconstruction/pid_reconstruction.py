from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

import reach_missing_data_assimilation as da
import enkf_plotting


@dataclass
class PIDConfig:
    steps: int = 72
    kp: float = 40.0
    ki: float = 2.0
    kd: float = 0.0
    action_sign: float = -1.0
    integral_limit: float = 150.0
    q_min: float = 0.0
    q_max: float | None = None
    q_max_step: float = 2.0
    initial_water_depth: float = 2.2
    initial_ues: float = 0.6
    initial_uef: float = 0.6
    filter_flow_outliers: bool = False
    use_dll_flow_feedback: bool = True
    anti_windup_gain: float = 0.2
    run_baseline: bool = True
    minimum_valid_water_level: float = 0.2

    def validate(self) -> None:
        if self.steps <= 0:
            raise ValueError("steps 必须大于 0")
        if min(self.kp, self.ki, self.kd) < 0.0:
            raise ValueError("PID 增益不能为负")
        if self.action_sign not in (-1.0, 1.0):
            raise ValueError("action_sign 只能为 -1 或 1")
        if self.integral_limit <= 0.0 or self.q_max_step <= 0.0:
            raise ValueError("integral_limit 和 q_max_step 必须大于 0")
        if self.q_max is not None and self.q_max <= self.q_min:
            raise ValueError("q_max 必须大于 q_min")
        if not 0.0 <= self.anti_windup_gain <= 1.0:
            raise ValueError("anti_windup_gain 必须位于 [0, 1]")
        if self.minimum_valid_water_level < 0.0:
            raise ValueError("minimum_valid_water_level 不能为负")


class PIDController:
    def __init__(self, config: PIDConfig, dt_hours: float) -> None:
        self.config = config
        self.dt_hours = float(dt_hours)
        self.integral = 0.0
        self.previous_error: float | None = None

    def track_execution(self, commanded_q: float, actual_q: float) -> float:
        if self.config.ki <= 0.0 or self.config.anti_windup_gain <= 0.0:
            return 0.0
        integral_delta = self.config.anti_windup_gain * (
            (float(actual_q) - float(commanded_q))
            / (self.config.action_sign * self.config.ki)
        )
        previous_integral = self.integral
        self.integral = float(
            np.clip(
                self.integral + integral_delta,
                -self.config.integral_limit,
                self.config.integral_limit,
            )
        )
        return self.integral - previous_integral

    def correction(self, error: float) -> tuple[float, float, float]:
        self.integral = float(
            np.clip(
                self.integral + float(error) * self.dt_hours,
                -self.config.integral_limit,
                self.config.integral_limit,
            )
        )
        derivative = (
            0.0
            if self.previous_error is None
            else (float(error) - self.previous_error) / self.dt_hours
        )
        self.previous_error = float(error)
        output = self.config.action_sign * (
            self.config.kp * float(error)
            + self.config.ki * self.integral
            + self.config.kd * derivative
        )
        return float(output), self.integral, float(derivative)


def _clamp_next_q(value: float, current: float, config: PIDConfig) -> float:
    bounded = float(
        np.clip(value, current - config.q_max_step, current + config.q_max_step)
    )
    upper = float("inf") if config.q_max is None else config.q_max
    return float(np.clip(bounded, config.q_min, upper))


def _flow_feedback(
    commanded_q: float,
    dll_q: float | None,
    use_dll_flow_feedback: bool,
) -> float:
    if not use_dll_flow_feedback:
        return float(commanded_q)
    if dll_q is None or not np.isfinite(dll_q):
        raise da.AssimilationError("DLL 未返回有效分水流量，无法进行实际动作闭环")
    return float(dll_q)


def _resolve_downstream_q(
    dll_q: float | None,
    upstream_q: float | None,
    diversion_q: float,
    gate_opening: float | None,
) -> float | None:
    if dll_q is not None and (abs(dll_q) > 1e-12 or not gate_opening):
        return float(dll_q)
    if upstream_q is not None:
        return float(upstream_q) - float(diversion_q)
    return dll_q


def _clean_flow_observations(bundle: da.ObservationBundle) -> da.ObservationBundle:
    def cleaned(mapping: dict[str, da.ObservationSeries]):
        result = {}
        for name, observation_series in mapping.items():
            values = [value for _, value in observation_series.values]
            normal, outliers = enkf_plotting._split_isolated_outliers(values)
            numeric = np.asarray(normal, dtype=float)
            valid = np.flatnonzero(np.isfinite(numeric))
            flagged = np.flatnonzero(np.isfinite(np.asarray(outliers, dtype=float)))
            if valid.size >= 2 and flagged.size:
                numeric[flagged] = np.interp(flagged, valid, numeric[valid])
            result[name] = da.ObservationSeries(
                [
                    (timestamp, value)
                    for (timestamp, _), value in zip(observation_series.values, numeric)
                    if np.isfinite(value)
                ]
            )
        return result

    return da.ObservationBundle(
        boundary_flow=cleaned(bundle.boundary_flow),
        gate_h1=bundle.gate_h1,
        gate_h2=bundle.gate_h2,
        gate_flow=cleaned(bundle.gate_flow),
        gate_opening=bundle.gate_opening,
    )


def filter_valid_water_levels(
    observations: dict[str, da.ObservationSeries],
    minimum: float,
) -> dict[str, da.ObservationSeries]:
    return {
        name: da.ObservationSeries(
            [(timestamp, value) for timestamp, value in series.values if value >= minimum]
        )
        for name, series in observations.items()
    }


def run_pid_reconstruction(
    case_path: str | Path,
    reach: da.ReachSpec,
    *,
    config: PIDConfig | None = None,
    output_root: str | Path | None = None,
    dll_path: str | Path | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    config = config or PIDConfig()
    config.validate()
    case_dir = Path(case_path).resolve()
    reach = reach.normalized()
    config_path = da._resolve_config_path(case_dir, None)
    boundary_path = da._resolve_config_data_path(
        config_path, case_dir, "SIM", "boundary_flow_path"
    )
    stage_path = da._resolve_config_data_path(
        config_path, case_dir, "SIM", "boundary_stage_path"
    )
    files = da._resolve_observation_files(
        case_dir,
        da.ObservationFiles(
            boundary_flow=boundary_path,
            gate_h1=stage_path,
        ),
    )
    raw_observations = da._load_observation_bundle(files)
    raw_observations.gate_h1 = filter_valid_water_levels(
        raw_observations.gate_h1, config.minimum_valid_water_level
    )
    observations = (
        _clean_flow_observations(raw_observations)
        if config.filter_flow_outliers
        else raw_observations
    )
    hydraulic_config = da.AssimilationConfig(
        ensemble_size=3,
        steps=config.steps + 1,
        initial_water_depth=config.initial_water_depth,
        initial_ues=config.initial_ues,
        initial_uef=config.initial_uef,
        observation_match="linear",
        max_obs_gap_minutes=24 * 60,
        assimilated_observations=("h1",),
    )
    runtime_payload = da._build_runtime_config(
        config_path, case_dir, reach, hydraulic_config
    )
    resolved_dll = Path(dll_path or da.TOOLS_DIR / "OcisMILPNet.dll").resolve()

    client_count = 2 if config.run_baseline else 1
    if client_factory is None:
        clients, dll_copies = da._load_isolated_clients(
            resolved_dll, client_count, runtime_payload, hydraulic_config
        )
    else:
        clients = [
            da._load_client(runtime_payload, hydraulic_config, client_factory)
            for _ in range(client_count)
        ]
        dll_copies = []
    baseline_client = clients[0] if config.run_baseline else None
    pid_client = clients[-1]
    try:
        _, name_to_id = da._parse_gate_info(pid_client.get_gate_info_sim())
        target_gate_id = name_to_id.get(da._normalize_name(reach.target_gate_name))
        upstream_gate_id = name_to_id.get(da._normalize_name(reach.upstream_gate_name))
        boundary_gate_id = name_to_id.get(da._normalize_name(reach.boundary_model_name))
        if target_gate_id is None or boundary_gate_id is None:
            raise da.AssimilationError("模型中无法解析 PID 所需的目标闸或分水边界")
        start_time, step_seconds = da._parse_time_param(pid_client.get_time_param())
        model_steps = int(pid_client.get_Total_T_step_count())
        steps = min(config.steps, model_steps) if model_steps > 0 else config.steps
        profile_size = steps + 1

        def timestamp_at(step: int) -> datetime | None:
            if start_time is None:
                return None
            return datetime.fromtimestamp(start_time.timestamp() + step * step_seconds)

        base_q = np.empty(profile_size, dtype=np.float64)
        targets: list[float | None] = []
        previous_q: float | None = None
        for step in range(profile_size):
            obs = da._collect_observations(
                observations, reach, timestamp_at(step), hydraulic_config
            )
            q_value = obs["q_boundary"]
            if q_value is None:
                if previous_q is None:
                    raise da.AssimilationError(f"第 {step} 步缺少基础分水流量")
                q_value = previous_q
            base_q[step] = float(q_value)
            previous_q = float(q_value)
            targets.append(obs["h1"])

        initial_raw = da._collect_observations(
            raw_observations,
            reach,
            timestamp_at(0),
            hydraulic_config,
            mode_override="exact",
        )
        initial_h1 = initial_raw["h1"] or config.initial_water_depth
        for client in clients:
            try:
                client.set_inih_1_byGates({int(target_gate_id): float(initial_h1)})
            except Exception:
                client.set_all_inih(float(initial_h1))

        pid = PIDController(config, step_seconds / 3600.0)
        applied_q = base_q.copy()
        rows: list[dict[str, Any]] = []
        baseline_raw_rmse_pairs: list[tuple[float, float]] = []
        raw_rmse_pairs: list[tuple[float, float]] = []
        baseline_tracking_pairs: list[tuple[float, float]] = []
        tracking_pairs: list[tuple[float, float]] = []

        for step in range(profile_size):
            outputs: list[dict[str, float | None]] = []
            trajectories = [(pid_client, applied_q[step])]
            if baseline_client is not None:
                trajectories.insert(0, (baseline_client, base_q[step]))
            for client, q_value in trajectories:
                client.update_BC_sim_only(step)
                client.set_GatesFlow_byID_sim(boundary_gate_id, float(q_value))
                da._set_mu(
                    client,
                    target_gate_id,
                    config.initial_ues,
                    config.initial_uef,
                )
                client.stepSolver_sim_Roe_only_pool(step, reach.pool_id)
                try:
                    if client.check_nan_sim():
                        trajectory = "基准" if client is baseline_client else "PID"
                        raise da.AssimilationError(
                            f"渠段 {reach.pool_id} 第 {step} 步{trajectory}水动力计算出现 NaN"
                        )
                except AttributeError:
                    pass
                q_data = da._safe_model_data(client, "gates_Q")
                upstream_q = (
                    da._extract_gate_value(q_data, upstream_gate_id)
                    if upstream_gate_id is not None
                    else None
                )
                gate_opening = da._extract_gate_value(
                    da._safe_model_data(client, "gates_e"), target_gate_id
                )
                outputs.append(
                    {
                        "h1": da._extract_gate_value(
                            da._safe_model_data(client, "gates_h1"), target_gate_id
                        ),
                        "h2": da._extract_gate_value(
                            da._safe_model_data(client, "gates_h2"), target_gate_id
                        ),
                        "gate_q": _resolve_downstream_q(
                            da._extract_gate_value(q_data, target_gate_id),
                            upstream_q,
                            float(q_value),
                            gate_opening,
                        ),
                        "upstream_q": upstream_q,
                        "boundary_q_dll": da._extract_gate_value(
                            q_data, boundary_gate_id
                        ),
                        "opening": gate_opening,
                    }
                )
            controlled = outputs[-1]
            baseline = outputs[0] if baseline_client is not None else {
                "h1": None,
                "h2": None,
                "gate_q": None,
                "upstream_q": None,
                "boundary_q_dll": None,
                "opening": None,
            }
            target = targets[step]
            error = (
                float(target) - float(controlled["h1"])
                if target is not None and controlled["h1"] is not None
                else None
            )
            correction = integral = derivative = None
            actual_q = _flow_feedback(
                float(applied_q[step]),
                controlled["boundary_q_dll"],
                config.use_dll_flow_feedback,
            )
            anti_windup = pid.track_execution(float(applied_q[step]), actual_q)
            if error is not None:
                correction, integral, derivative = pid.correction(error)
                if baseline["h1"] is not None:
                    baseline_tracking_pairs.append((float(target), float(baseline["h1"])))
                tracking_pairs.append((float(target), float(controlled["h1"])))
                if step + 1 < profile_size:
                    applied_q[step + 1] = _clamp_next_q(
                        float(base_q[step + 1]) + correction,
                        actual_q,
                        config,
                    )

            raw = da._collect_observations(
                raw_observations,
                reach,
                timestamp_at(step),
                hydraulic_config,
                mode_override="exact",
            )
            row: dict[str, Any] = {
                "step": step,
                "time": timestamp_at(step).strftime("%Y/%m/%d %H:%M:%S") if timestamp_at(step) else f"step_{step}",
                "pool_id": reach.pool_id,
                "segment": reach.segment_name,
                "upstream_gate_name": reach.upstream_gate_name,
                "target_gate_name": reach.target_gate_name,
                "boundary_name": reach.boundary_name,
                "used_observations": "h1_pid_target" if target is not None else "",
                "q_boundary_obs": raw["q_boundary"],
                "q_boundary_forecast": float(base_q[step]),
                "q_boundary_analysis": float(applied_q[step]),
                "q_boundary_completed": float(applied_q[step]),
                "q_boundary_dll_forecast": baseline["boundary_q_dll"],
                "q_boundary_dll_analysis": controlled["boundary_q_dll"],
                "q_boundary_execution_error": (
                    actual_q - float(applied_q[step])
                ),
                "h1_obs": raw["h1"],
                "h1_control_target": target,
                "h1_forecast": baseline["h1"],
                "h1_analysis": controlled["h1"],
                "h1_completed": raw["h1"] if raw["h1"] is not None else controlled["h1"],
                "h2_obs": raw["h2"],
                "h2_forecast": baseline["h2"],
                "h2_analysis": controlled["h2"],
                "h2_completed": raw["h2"] if raw["h2"] is not None else controlled["h2"],
                "gate_q_obs": raw["gate_q"],
                "gate_q_forecast": baseline["gate_q"],
                "gate_q_analysis": controlled["gate_q"],
                "gate_q_completed": raw["gate_q"] if raw["gate_q"] is not None else controlled["gate_q"],
                "upstream_gate_q_forecast": baseline["upstream_q"],
                "upstream_gate_q_analysis": controlled["upstream_q"],
                "net_gate_q_forecast": (
                    baseline["upstream_q"] - baseline["gate_q"]
                    if baseline["upstream_q"] is not None and baseline["gate_q"] is not None
                    else None
                ),
                "net_gate_q_analysis": (
                    controlled["upstream_q"] - controlled["gate_q"]
                    if controlled["upstream_q"] is not None and controlled["gate_q"] is not None
                    else None
                ),
                "gate_opening_obs": raw["gate_opening"],
                "gate_opening_forecast": baseline["opening"],
                "gate_opening_analysis": controlled["opening"],
                "gate_opening_completed": raw["gate_opening"] if raw["gate_opening"] is not None else controlled["opening"],
                "ues_forecast": config.initial_ues,
                "ues_analysis": config.initial_ues,
                "uef_forecast": config.initial_uef,
                "uef_analysis": config.initial_uef,
                "pid_error": error,
                "pid_correction": correction,
                "pid_integral": integral,
                "pid_derivative": derivative,
                "pid_anti_windup": anti_windup,
            }
            if raw["h1"] is not None and controlled["h1"] is not None:
                if baseline["h1"] is not None:
                    baseline_raw_rmse_pairs.append(
                        (float(raw["h1"]), float(baseline["h1"]))
                    )
                raw_rmse_pairs.append((float(raw["h1"]), float(controlled["h1"])))
            rows.append(row)
    finally:
        baseline_client = None
        pid_client = None
        if dll_copies:
            da._release_isolated_clients(clients, dll_copies)

    output_dir = Path(output_root or case_dir / "output" / "pid_reconstruction") / str(reach.pool_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "assimilated_series.csv"
    plot_path = output_dir / "assimilation_result.png"
    summary_path = output_dir / "summary.json"
    da._write_rows_csv(csv_path, rows)
    enkf_plotting.write_reconstruction_png(
        plot_path,
        rows,
        segment_name=reach.segment_name,
        mark_flow_outliers=config.filter_flow_outliers,
        show_baseline=config.run_baseline,
    )
    execution_errors = np.asarray(
        [
            float(row["q_boundary_execution_error"])
            for row in rows
            if row.get("q_boundary_execution_error") is not None
        ],
        dtype=float,
    )
    summary = {
        "case_path": str(case_dir),
        "pool_id": reach.pool_id,
        "segment": reach.segment_name,
        "method": "hourly PID",
        "config": asdict(config),
        "baseline_rmse_h1_at_raw_observations": da._rmse(baseline_raw_rmse_pairs),
        "rmse_h1_at_raw_observations": da._rmse(raw_rmse_pairs),
        "baseline_rmse_h1_tracking_target": da._rmse(baseline_tracking_pairs),
        "rmse_h1_tracking_target": da._rmse(tracking_pairs),
        "rmse_q_boundary_execution": (
            float(np.sqrt(np.mean(execution_errors**2)))
            if execution_errors.size
            else None
        ),
        "max_abs_q_boundary_execution_error": (
            float(np.max(np.abs(execution_errors)))
            if execution_errors.size
            else None
        ),
        "observation_files": {
            key: str(value) if value is not None else None
            for key, value in asdict(files).items()
        },
        "output_csv": str(csv_path),
        "output_plot": str(plot_path),
        "output_summary": str(summary_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
