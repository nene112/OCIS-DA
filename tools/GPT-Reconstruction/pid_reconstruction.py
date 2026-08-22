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
    write_plot: bool = True
    adaptive_gains: bool = False
    adaptive_small_error: float = 0.1
    adaptive_large_error: float = 0.5
    adaptive_large_kp_factor: float = 1.5
    adaptive_small_kp_factor: float = 0.5
    adaptive_medium_ki_factor: float = 0.25
    adaptive_large_ki_factor: float = 0.05
    adaptive_large_integral_factor: float = 0.10
    adaptive_gain_smoothing: float = 0.2
    adaptive_integral_leak: float = 0.05
    adaptive_max_water_level_step: float = 1.0
    adaptive_safety_gain_factor: float = 0.5
    adaptive_q_max_margin: float = 0.5
    adaptive_q_max_error_gain: float = 2.0
    bias_estimator_enabled: bool = True
    bias_estimator_gain: float = 0.05
    bias_estimator_limit: float = 1.0
    flow_tracking_enabled: bool = False
    flow_tracking_tolerance: float = 0.01
    flow_tracking_max_trials: int = 8
    flow_tracking_strict: bool = True
    enforce_diversion_not_above_downstream_gate: bool = True

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
        if not 0.0 < self.adaptive_small_error < self.adaptive_large_error:
            raise ValueError("动态PID误差阈值必须满足 0 < small < large")
        if min(
            self.adaptive_large_kp_factor,
            self.adaptive_small_kp_factor,
            self.adaptive_medium_ki_factor,
            self.adaptive_large_ki_factor,
            self.adaptive_large_integral_factor,
            self.adaptive_safety_gain_factor,
        ) < 0.0:
            raise ValueError("动态PID增益系数不能为负")
        if not 0.0 < self.adaptive_gain_smoothing <= 1.0:
            raise ValueError("adaptive_gain_smoothing 必须位于 (0, 1]")
        if not 0.0 <= self.adaptive_integral_leak <= 1.0:
            raise ValueError("adaptive_integral_leak 必须位于 [0, 1]")
        if self.adaptive_max_water_level_step <= 0.0:
            raise ValueError("adaptive_max_water_level_step 必须大于 0")
        if self.adaptive_q_max_margin < 0.0 or self.adaptive_q_max_error_gain < 0.0:
            raise ValueError("动态分水上限参数不能为负")
        if not 0.0 < self.bias_estimator_gain <= 1.0:
            raise ValueError("bias_estimator_gain 必须位于 (0, 1]")
        if self.bias_estimator_limit < 0.0:
            raise ValueError("bias_estimator_limit 不能为负")
        if self.flow_tracking_tolerance <= 0.0:
            raise ValueError("flow_tracking_tolerance 必须大于 0")
        if self.flow_tracking_max_trials < 1:
            raise ValueError("flow_tracking_max_trials 必须至少为 1")


class PIDController:
    def __init__(self, config: PIDConfig, dt_hours: float) -> None:
        self.config = config
        self.dt_hours = float(dt_hours)
        self.integral = 0.0
        self.previous_error: float | None = None
        self.effective_kp = float(config.kp)
        self.effective_ki = float(config.ki)
        self.effective_kd = float(config.kd)
        self.water_level_bias = 0.0

    def update_water_level_bias(self, observed_h: float, model_h: float) -> float:
        """Estimate the slowly varying observed-minus-model water-level bias."""
        if not self.config.bias_estimator_enabled:
            self.water_level_bias = 0.0
            return self.water_level_bias
        innovation = float(observed_h) - float(model_h)
        self.water_level_bias = float(
            np.clip(
                self.water_level_bias
                + self.config.bias_estimator_gain * (innovation - self.water_level_bias),
                -self.config.bias_estimator_limit,
                self.config.bias_estimator_limit,
            )
        )
        return self.water_level_bias

    def track_execution(self, commanded_q: float, actual_q: float) -> float:
        active_ki = self.effective_ki if self.config.adaptive_gains else self.config.ki
        if active_ki <= 0.0 or self.config.anti_windup_gain <= 0.0:
            return 0.0
        integral_delta = self.config.anti_windup_gain * (
            (float(actual_q) - float(commanded_q))
            / (self.config.action_sign * active_ki)
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

    def _schedule_gains(self, error: float, safety_mode: bool) -> None:
        absolute_error = abs(float(error))
        if absolute_error >= self.config.adaptive_large_error:
            target_kp = self.config.kp * self.config.adaptive_large_kp_factor
            target_ki = self.config.ki * self.config.adaptive_large_ki_factor
        elif absolute_error > self.config.adaptive_small_error:
            target_kp = self.config.kp
            target_ki = self.config.ki * self.config.adaptive_medium_ki_factor
        else:
            target_kp = self.config.kp * self.config.adaptive_small_kp_factor
            target_ki = self.config.ki
        if safety_mode:
            target_kp *= self.config.adaptive_safety_gain_factor
            target_ki *= self.config.adaptive_safety_gain_factor
        smoothing = self.config.adaptive_gain_smoothing
        self.effective_kp += smoothing * (target_kp - self.effective_kp)
        self.effective_ki += smoothing * (target_ki - self.effective_ki)
        self.effective_kd = self.config.kd * (
            self.config.adaptive_safety_gain_factor if safety_mode else 1.0
        )
    def correction(
        self, error: float, safety_mode: bool = False
    ) -> tuple[float, float, float]:
        if self.config.adaptive_gains:
            self._schedule_gains(error, safety_mode)
            if safety_mode:
                self.integral *= 1.0 - self.config.adaptive_integral_leak
            else:
                integral_factor = (
                    self.config.adaptive_large_integral_factor
                    if abs(float(error)) >= self.config.adaptive_large_error
                    else 1.0
                )
                self.integral += integral_factor * float(error) * self.dt_hours
        else:
            self.integral += float(error) * self.dt_hours
        self.integral = float(
            np.clip(
                self.integral,
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
        kp = self.effective_kp if self.config.adaptive_gains else self.config.kp
        ki = self.effective_ki if self.config.adaptive_gains else self.config.ki
        kd = self.effective_kd if self.config.adaptive_gains else self.config.kd
        output = self.config.action_sign * (
            kp * float(error)
            + ki * self.integral
            + kd * derivative
        )
        return float(output), self.integral, float(derivative)


def _clamp_next_q(
    value: float,
    current: float,
    config: PIDConfig,
    q_max_override: float | None = None,
) -> float:
    bounded = float(
        np.clip(value, current - config.q_max_step, current + config.q_max_step)
    )
    configured_upper = config.q_max if q_max_override is None else q_max_override
    upper = float("inf") if configured_upper is None else configured_upper
    return float(np.clip(bounded, config.q_min, upper))


def _adaptive_q_max(base_q: float, error: float, config: PIDConfig) -> float | None:
    if not config.adaptive_gains or config.q_max is None:
        return config.q_max
    scheduled = (
        float(base_q)
        + config.adaptive_q_max_margin
        + config.adaptive_q_max_error_gain * abs(float(error))
    )
    return max(config.q_min, min(config.q_max, scheduled))


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


def _select_flow_by_one_step_trials(
    client: Any,
    *,
    state_path: Path,
    step: int,
    pool_id: int,
    boundary_gate_id: str | int,
    target_gate_id: str | int,
    target_h1: float,
    q_min: float,
    q_max: float,
    tolerance: float,
    max_trials: int,
) -> tuple[float, dict[str, Any]]:
    """Find a boundary flow using only reversible one-step DLL trials."""
    client.save_states(str(state_path))

    def trial(q_value: float) -> float:
        client.set_states(str(state_path))
        client.update_BC_sim_only(step)
        client.set_GatesFlow_byID_sim(boundary_gate_id, float(q_value))
        client.stepSolver_sim_Roe_only_pool(step, pool_id)
        h1 = da._extract_gate_value(
            da._safe_model_data(client, "gates_h1"), target_gate_id
        )
        if h1 is None:
            raise da.AssimilationError(
                f"试算第 {step} 步未返回目标闸水位"
            )
        return float(h1)

    try:
        low, high = float(q_min), float(q_max)
        h_low, h_high = trial(low), trial(high)
        candidates = [(abs(h_low - target_h1), low, h_low), (abs(h_high - target_h1), high, h_high)]
        bracketed = (h_low - target_h1) * (h_high - target_h1) <= 0.0
        if bracketed:
            for _ in range(max_trials):
                mid = 0.5 * (low + high)
                h_mid = trial(mid)
                candidates.append((abs(h_mid - target_h1), mid, h_mid))
                if abs(h_mid - target_h1) <= tolerance:
                    break
                if (h_low - target_h1) * (h_mid - target_h1) <= 0.0:
                    high, h_high = mid, h_mid
                else:
                    low, h_low = mid, h_mid
        error, selected_q, selected_h = min(candidates, key=lambda item: item[0])
        return float(selected_q), {
            "flow_tracking_target_h1": float(target_h1),
            "flow_tracking_selected_h1": float(selected_h),
            "flow_tracking_abs_error": float(error),
            "flow_tracking_reachable": int(bracketed),
            "flow_tracking_trials": len(candidates),
            "flow_tracking_q_min": float(q_min),
            "flow_tracking_q_max": float(q_max),
        }
    finally:
        # The caller performs the selected flow as the one and only committed step.
        client.set_states(str(state_path))


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


def _diversion_upper_bound_from_downstream_gate(
    upstream_q: float | None,
    downstream_q: float | None,
    config: PIDConfig,
) -> float | None:
    """Return a d6 command cap that preserves downstream-gate discharge.

    The direct downstream-gate discharge is an immediate cap.  Commands take
    effect with a one-step DLL delay, so Q_d6 <= Q_downstream ~=
    Q_upstream - Q_d6 also gives the forward-looking cap Q_d6 <= 0.5 Q_upstream.
    """
    if not config.enforce_diversion_not_above_downstream_gate:
        return None
    bounds: list[float] = []
    upstream = da._finite_float(upstream_q)
    if upstream is not None:
        bounds.append(max(config.q_min, 0.5 * max(0.0, upstream)))
    downstream = da._finite_float(downstream_q)
    if downstream is not None:
        bounds.append(max(config.q_min, downstream))
    return min(bounds) if bounds else None


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


def _valid_boundary_flow(value: Any, q_min: float) -> float | None:
    """Return a usable boundary-flow observation; negative values are missing."""
    number = da._finite_float(value)
    return number if number is not None and number >= q_min else None


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
        # PID reconstruction may adjust only the boundary flow.  Never
        # overwrite the case's discretized bed elevations during startup.
        initial_bed_level=None,
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
    output_dir = Path(output_root or case_dir / "output" / "pid_reconstruction") / str(reach.pool_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "partial_series.csv").unlink(missing_ok=True)
    (output_dir / "failure_diagnostics.json").unlink(missing_ok=True)
    trial_state_path = output_dir / "flow_tracking_trial.state"
    trial_state_path.unlink(missing_ok=True)
    rows: list[dict[str, Any]] = []
    failure_context: dict[str, Any] = {}

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
        # Read the static gate catalog once from the active DLL instance.  The
        # resolved names are written to the output CSV so downstream plotting
        # never has to create another DLL client.
        id_to_name, name_to_id = da._parse_gate_info(pid_client.get_gate_info_sim())
        target_gate_id = name_to_id.get(da._normalize_name(reach.target_gate_name))
        upstream_gate_id = name_to_id.get(da._normalize_name(reach.upstream_gate_name))
        boundary_gate_id = name_to_id.get(da._normalize_name(reach.boundary_model_name))
        if target_gate_id is None or boundary_gate_id is None:
            raise da.AssimilationError("模型中无法解析 PID 所需的目标闸或分水边界")
        target_gate_name = id_to_name.get(target_gate_id, reach.target_gate_name)
        upstream_gate_name = id_to_name.get(upstream_gate_id, reach.upstream_gate_name)
        boundary_gate_name = id_to_name.get(boundary_gate_id, reach.boundary_model_name)
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
            q_value = _valid_boundary_flow(obs["q_boundary"], config.q_min)
            if q_value is None:
                if previous_q is None:
                    raise da.AssimilationError(f"第 {step} 步缺少基础分水流量")
                q_value = previous_q
            base_q[step] = float(q_value)
            previous_q = float(base_q[step])
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
        applied_q = np.clip(base_q, config.q_min, None)
        baseline_raw_rmse_pairs: list[tuple[float, float]] = []
        raw_rmse_pairs: list[tuple[float, float]] = []
        baseline_tracking_pairs: list[tuple[float, float]] = []
        tracking_pairs: list[tuple[float, float]] = []
        previous_controlled_h1: float | None = None
        # get_gates_Q is not populated before the first solver call of a step.
        # Keep the last confirmed values for the next command's constraint.
        previous_controlled_upstream_q: float | None = None
        previous_controlled_downstream_q: float | None = None

        for step in range(profile_size):
            outputs: list[dict[str, float | None]] = []
            target = targets[step]
            flow_tracking: dict[str, Any] = {}
            downstream_constraint_q_max: float | None = None
            trajectories = [(pid_client, applied_q[step])]
            if baseline_client is not None:
                trajectories.insert(0, (baseline_client, base_q[step]))
            for client, q_value in trajectories:
                trajectory = "基准" if client is baseline_client else "PID"
                if (
                    client is pid_client
                    and config.flow_tracking_enabled
                    and target is not None
                ):
                    if config.q_max is None:
                        raise da.AssimilationError(
                            "流量试算跟踪需要设置 q_max 作为可搜索上界"
                        )
                    current_q_data = da._safe_model_data(client, "gates_Q")
                    current_upstream_q = (
                        da._extract_gate_value(current_q_data, upstream_gate_id)
                        if upstream_gate_id is not None
                        else None
                    )
                    current_opening = da._extract_gate_value(
                        da._safe_model_data(client, "gates_e"), target_gate_id
                    )
                    current_downstream_q = _resolve_downstream_q(
                        da._extract_gate_value(current_q_data, target_gate_id),
                        current_upstream_q,
                        float(applied_q[step]),
                        current_opening,
                    )
                    constraint_upstream_q = (
                        current_upstream_q
                        if da._finite_float(current_upstream_q) is not None
                        else previous_controlled_upstream_q
                    )
                    constraint_downstream_q = (
                        current_downstream_q
                        if da._finite_float(current_downstream_q) is not None
                        else previous_controlled_downstream_q
                    )
                    downstream_constraint_q_max = (
                        _diversion_upper_bound_from_downstream_gate(
                            constraint_upstream_q, constraint_downstream_q, config
                        )
                    )
                    trial_q_max = float(config.q_max)
                    if downstream_constraint_q_max is not None:
                        trial_q_max = min(trial_q_max, downstream_constraint_q_max)
                    q_value, flow_tracking = _select_flow_by_one_step_trials(
                        client,
                        state_path=trial_state_path,
                        step=step,
                        pool_id=reach.pool_id,
                        boundary_gate_id=boundary_gate_id,
                        target_gate_id=target_gate_id,
                        target_h1=float(target),
                        q_min=config.q_min,
                        q_max=max(config.q_min, trial_q_max),
                        tolerance=config.flow_tracking_tolerance,
                        max_trials=config.flow_tracking_max_trials,
                    )
                    flow_tracking["flow_tracking_downstream_gate_q_max"] = (
                        downstream_constraint_q_max
                    )
                    applied_q[step] = float(q_value)
                    if (
                        config.flow_tracking_strict
                        and flow_tracking["flow_tracking_abs_error"]
                        > config.flow_tracking_tolerance
                    ):
                        raise da.AssimilationError(
                            f"第 {step} 步流量试算不可达 1 cm 跟踪目标；"
                            f"最小误差={flow_tracking['flow_tracking_abs_error']:.6f} m"
                        )
                failure_context = {
                    "pool_id": reach.pool_id,
                    "segment": reach.segment_name,
                    "step": step,
                    "time": (
                        timestamp_at(step).strftime("%Y/%m/%d %H:%M:%S")
                        if timestamp_at(step)
                        else f"step_{step}"
                    ),
                    "trajectory": trajectory,
                    "commanded_boundary_q": float(q_value),
                    "base_boundary_q": float(base_q[step]),
                    "target_h1": targets[step],
                    "pid_integral_before_step": pid.integral,
                    "pid_previous_error": pid.previous_error,
                    "pid_effective_kp": pid.effective_kp,
                    "pid_effective_ki": pid.effective_ki,
                    "pid_effective_kd": pid.effective_kd,
                }
                client.update_BC_sim_only(step)
                client.set_GatesFlow_byID_sim(boundary_gate_id, float(q_value))
                client.stepSolver_sim_Roe_only_pool(step, reach.pool_id)
                try:
                    if client.check_nan_sim():
                        failure_context["dll_step_data"] = {
                            name: da._safe_model_data(client, name)
                            for name in (
                                "gates_h1",
                                "gates_h2",
                                "gates_Q",
                                "gates_e",
                                "gates_mu",
                            )
                        }
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
            raw_error = (
                float(target) - float(controlled["h1"])
                if target is not None and controlled["h1"] is not None
                else None
            )
            water_level_bias = (
                pid.update_water_level_bias(float(target), float(controlled["h1"]))
                if raw_error is not None
                else pid.water_level_bias
            )
            h1_bias_corrected = (
                float(controlled["h1"]) + water_level_bias
                if controlled["h1"] is not None
                else None
            )
            error = (
                float(target) - h1_bias_corrected
                if target is not None and h1_bias_corrected is not None
                else None
            )
            correction = integral = derivative = None
            water_level_step = (
                float(controlled["h1"]) - previous_controlled_h1
                if controlled["h1"] is not None and previous_controlled_h1 is not None
                else None
            )
            safety_mode = bool(
                config.adaptive_gains
                and water_level_step is not None
                and abs(water_level_step) > config.adaptive_max_water_level_step
            )
            if controlled["h1"] is not None:
                previous_controlled_h1 = float(controlled["h1"])
            if da._finite_float(controlled["upstream_q"]) is not None:
                previous_controlled_upstream_q = float(controlled["upstream_q"])
            if da._finite_float(controlled["gate_q"]) is not None:
                previous_controlled_downstream_q = float(controlled["gate_q"])
            actual_q = _flow_feedback(
                float(applied_q[step]),
                controlled["boundary_q_dll"],
                config.use_dll_flow_feedback,
            )
            anti_windup = pid.track_execution(float(applied_q[step]), actual_q)
            downstream_constraint_q_max = _diversion_upper_bound_from_downstream_gate(
                controlled["upstream_q"], controlled["gate_q"], config
            )
            if error is not None:
                correction, integral, derivative = pid.correction(error, safety_mode)
                if baseline["h1"] is not None:
                    baseline_tracking_pairs.append((float(target), float(baseline["h1"])))
                tracking_pairs.append((float(target), float(controlled["h1"])))
                if step + 1 < profile_size:
                    next_q_max = _adaptive_q_max(
                        float(base_q[step + 1]), error, config
                    )
                    if downstream_constraint_q_max is not None:
                        next_q_max = (
                            downstream_constraint_q_max
                            if next_q_max is None
                            else min(next_q_max, downstream_constraint_q_max)
                        )
                    applied_q[step + 1] = _clamp_next_q(
                        float(base_q[step + 1]) + correction,
                        actual_q,
                        config,
                        next_q_max,
                    )

            raw = da._collect_observations(
                raw_observations,
                reach,
                timestamp_at(step),
                hydraulic_config,
                mode_override="exact",
            )
            # A negative derived/observed diversion is physically invalid for
            # this controller.  Do not expose it as an observation or use it
            # to reset the next boundary-flow command.
            raw["q_boundary"] = _valid_boundary_flow(
                raw["q_boundary"], config.q_min
            )
            row: dict[str, Any] = {
                "step": step,
                "time": timestamp_at(step).strftime("%Y/%m/%d %H:%M:%S") if timestamp_at(step) else f"step_{step}",
                "pool_id": reach.pool_id,
                "segment": reach.segment_name,
                "upstream_gate_name": upstream_gate_name,
                "target_gate_name": target_gate_name,
                "boundary_name": reach.boundary_name,
                # Keep the configured model key separate from the actual
                # boundary-gate display name returned by the DLL.
                "boundary_model_name": reach.boundary_model_name,
                "boundary_gate_name": boundary_gate_name,
                "upstream_gate_id": upstream_gate_id,
                "target_gate_id": target_gate_id,
                "boundary_gate_id": boundary_gate_id,
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
                "pid_raw_error": raw_error,
                "pid_water_level_bias": water_level_bias,
                "h1_analysis_bias_corrected": h1_bias_corrected,
                "pid_correction": correction,
                "pid_integral": integral,
                "pid_derivative": derivative,
                "pid_kp": pid.effective_kp,
                "pid_ki": pid.effective_ki,
                "pid_kd": pid.effective_kd,
                "pid_safety_mode": int(safety_mode),
                "pid_water_level_step": water_level_step,
                "pid_q_max": (
                    _adaptive_q_max(float(base_q[step]), error, config)
                    if error is not None
                    else config.q_max
                ),
                "downstream_gate_diversion_q_max": downstream_constraint_q_max,
                "downstream_gate_diversion_constraint_active": int(
                    config.enforce_diversion_not_above_downstream_gate
                ),
                **flow_tracking,
                "pid_anti_windup": anti_windup,
            }
            if raw["h1"] is not None and controlled["h1"] is not None:
                if baseline["h1"] is not None:
                    baseline_raw_rmse_pairs.append(
                        (float(raw["h1"]), float(baseline["h1"]))
                    )
                raw_rmse_pairs.append((float(raw["h1"]), float(controlled["h1"])))
            rows.append(row)
    except Exception as exc:
        partial_path = output_dir / "partial_series.csv"
        partial_plot_path = output_dir / "partial_assimilation_result.png"
        diagnostics_path = output_dir / "failure_diagnostics.json"
        partial_plot_error: str | None = None
        if rows:
            da._write_rows_csv(partial_path, rows)
            # A failed trial must still leave an inspectable water-level/PID plot.
            # Do not let a plotting problem hide the original DLL/assimilation error.
            if config.write_plot:
                try:
                    enkf_plotting.write_reconstruction_png(
                        partial_plot_path,
                        rows,
                        segment_name=reach.segment_name,
                        mark_flow_outliers=config.filter_flow_outliers,
                        show_baseline=config.run_baseline,
                    )
                except Exception as plot_exc:
                    partial_plot_error = f"{type(plot_exc).__name__}: {plot_exc}"
        diagnostics = {
            "error": f"{type(exc).__name__}: {exc}",
            "case_path": str(case_dir),
            "pool_id": reach.pool_id,
            "segment": reach.segment_name,
            "config": asdict(config),
            "completed_step_count": len(rows),
            "partial_series": str(partial_path) if rows else None,
            "partial_plot": str(partial_plot_path) if partial_plot_path.is_file() else None,
            "partial_plot_error": partial_plot_error,
            "failure": failure_context,
            "recent_completed_steps": rows[-5:],
        }
        diagnostics_path.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise da.AssimilationError(
            f"{exc}；失败诊断: {diagnostics_path.resolve()}"
        ) from exc
    finally:
        baseline_client = None
        pid_client = None
        if dll_copies:
            da._release_isolated_clients(clients, dll_copies)
        trial_state_path.unlink(missing_ok=True)

    csv_path = output_dir / "assimilated_series.csv"
    plot_path = output_dir / "assimilation_result.png"
    summary_path = output_dir / "summary.json"
    da._write_rows_csv(csv_path, rows)
    if config.write_plot:
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
        "gate_info": id_to_name,
        "gate_ids": {
            "upstream": upstream_gate_id,
            "target": target_gate_id,
            "boundary": boundary_gate_id,
        },
        "output_csv": str(csv_path),
        "output_plot": str(plot_path) if config.write_plot else None,
        "output_summary": str(summary_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
