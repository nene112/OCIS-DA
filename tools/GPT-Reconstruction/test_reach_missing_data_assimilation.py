from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

import reach_missing_data_assimilation as da
import pid_reconstruction as pid_reconstruction
import parallel_pid_reconstruction as parallel_pid
import rolling_nsga_reconstruction as rolling
import enkf_plotting


class FakeClient:
    def __init__(self):
        self.state = {"h": 1.0, "q": 2.0, "ues": 0.6, "uef": 0.6, "e": 0.2}

    def read_data_MILP_c_str(self, payload): pass
    def set_Algorithm_sim(self, value): pass
    def read_data_sim_Roe_c_str_pools(self, payload): pass
    def set_all_inih(self, value): self.state["h"] = float(value)
    def set_all_zb(self, value): pass
    def get_gate_info_sim(self):
        return json.dumps({"0": {"id": 0, "name": "d0"}, "1": {"id": 1, "name": "上游闸"}, "2": {"id": 2, "name": "目标闸"}}, ensure_ascii=False)
    def get_time_param(self):
        return json.dumps({"start_time": "2026-01-01 00:00:00", "step_dt": 3600})
    def get_Total_T_step_count(self): return 3
    def save_states(self, filename):
        Path(filename).write_text(json.dumps(self.state), encoding="utf-8")
    def set_states(self, filename):
        self.state = json.loads(Path(filename).read_text(encoding="utf-8"))
    def update_BC_sim_only(self, k): pass
    def set_GatesFlow_byID_sim(self, gate_id, flow): self.state["q"] = float(flow)
    def set_GatesFlow_mu_byID_sim_multi(self, gate_id, payload):
        self.state["ues"] = float(payload["Ues"])
        self.state["uef"] = float(payload["Uef"])
    def set_GatesFlow_mu_byID_sim(self, gate_id, mu):
        self.state["ues"] = self.state["uef"] = float(mu)
    def stepSolver_sim_Roe_only_pool(self, k, pool_id):
        mu = 0.5 * (self.state["ues"] + self.state["uef"])
        self.state["h"] = 0.15 * self.state["h"] + 1.0 + 0.45 * self.state["q"] + 0.8 * (mu - 0.6)
        self.state["e"] = max(0.0, min(1.0, self.state["q"] / 10.0))
    def check_nan_sim(self): return False
    def get_stepdata_sim(self, datatype):
        if datatype == "gates_h1": return json.dumps({"2": self.state["h"]})
        if datatype == "gates_h2": return json.dumps({"2": 0.8 * self.state["h"]})
        if datatype == "gates_Q": return json.dumps({"0": self.state["q"], "1": 0.95 * self.state["q"], "2": 0.9 * self.state["q"]})
        if datatype == "gates_e": return json.dumps({"2": self.state["e"]})
        if datatype == "gates_mu": return json.dumps({"2": {"Ues": self.state["ues"], "Uef": self.state["uef"]}})
        return "{}"


def write_wide(path: Path, header: list[str], rows: list[list[object]]):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def make_case(tmp_path: Path) -> Path:
    case = tmp_path / "case"
    (case / "input").mkdir(parents=True)
    (case / "mesh").mkdir()
    (case / "OCIS_dataConfig.json").write_text(json.dumps({
        "selected_model": "SIMMODEL",
        "SIMMODEL": {"case_name": "case", "dirpath": "../"},
        "SIM": {},
    }), encoding="utf-8")
    write_wide(case / "mesh" / "edges.csv", ["id", "source", "target", "type", "canal"], [[0, "上游闸", "目标闸", "canal", "测试渠"]])
    write_wide(case / "input" / "action.csv", ["tm", "d0", "目标闸"], [
        ["2026/01/01 00:00", 2.0, 1.8],
        ["2026/01/01 01:00", "", ""],
        ["2026/01/01 02:00", 3.0, 2.7],
    ])
    # 中间时刻 h1 偏高，应推动缺失 q 的分析估计提高。
    write_wide(case / "input" / "stage1_td.csv", ["tm", "目标闸"], [
        ["2026/01/01 00:00", 2.2],
        ["2026/01/01 01:00", 2.75],
        ["2026/01/01 02:00", 2.7],
    ])
    write_wide(case / "input" / "stage2_td.csv", ["tm", "目标闸"], [
        ["2026/01/01 00:00", 1.76],
        ["2026/01/01 01:00", ""],
        ["2026/01/01 02:00", 2.16],
    ])
    write_wide(case / "input" / "gate_e_td.csv", ["tm", "目标闸"], [
        ["2026/01/01 00:00", 0.2],
        ["2026/01/01 01:00", ""],
        ["2026/01/01 02:00", 0.3],
    ])
    return case


def test_denkf_missing_observation_is_noop():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(len(da.STATE_NAMES), 8))
    result = da.DEnKF().update(x, {}, {})
    np.testing.assert_allclose(result.analysis_ensemble, x)
    assert result.used_observations == ()


def test_rolling_nsga_helpers():
    np.testing.assert_allclose(
        rolling.interpolate_control_nodes([1.0, 2.0, 3.0], 5),
        [1.0, 1.5, 2.0, 2.5, 3.0],
    )
    decision, objectives = rolling.select_pareto_compromise(
        np.array([[1.0, 2.0], [3.0, 4.0]]),
        np.array([[0.0, 1.0, 1.0], [1.0, 0.0, 0.0]]),
    )
    np.testing.assert_allclose(decision, [3.0, 4.0])
    np.testing.assert_allclose(objectives, [1.0, 0.0, 0.0])


def test_pid_controller_inverse_action_and_limits():
    config = pid_reconstruction.PIDConfig(
        kp=2.0,
        ki=0.1,
        kd=0.0,
        action_sign=-1.0,
        q_max_step=1.0,
    )
    controller = pid_reconstruction.PIDController(config, dt_hours=1.0)
    correction, integral, derivative = controller.correction(0.5)
    assert correction < 0.0
    assert integral == 0.5
    assert derivative == 0.0
    assert pid_reconstruction._clamp_next_q(20.0, 5.0, config) == 6.0


def test_pid_uses_dll_flow_as_actual_action_feedback():
    assert pid_reconstruction._flow_feedback(5.0, 4.5, True) == 4.5
    assert pid_reconstruction._flow_feedback(5.0, 4.5, False) == 5.0
    with pytest.raises(da.AssimilationError):
        pid_reconstruction._flow_feedback(5.0, None, True)


def test_pid_back_calculation_reduces_unachievable_command():
    config = pid_reconstruction.PIDConfig(
        ki=2.0,
        action_sign=-1.0,
        anti_windup_gain=0.2,
    )
    controller = pid_reconstruction.PIDController(config, dt_hours=1.0)
    controller.integral = -20.0
    adjustment = controller.track_execution(commanded_q=10.0, actual_q=6.0)
    assert np.isclose(adjustment, 0.4)
    assert np.isclose(controller.integral, -19.6)


def test_pid_resolves_unreported_downstream_gate_flow():
    assert pid_reconstruction._resolve_downstream_q(0.0, 24.43, 2.78, 0.35) == 21.65
    assert pid_reconstruction._resolve_downstream_q(18.0, 24.43, 2.78, 0.35) == 18.0
    assert pid_reconstruction._resolve_downstream_q(0.0, 24.43, 2.78, 0.0) == 0.0


def test_parallel_pid_selects_reaches_with_enough_measured_water_levels():
    reaches = [
        da.ReachSpec(0, "上游闸A", "目标闸A", "上游闸A", "d0", "渠段A"),
        da.ReachSpec(1, "上游闸B", "目标闸B", "上游闸B", "d1", "渠段B"),
    ]
    observations = {
        da._normalize_name("目标闸A"): da.ObservationSeries(
            [(datetime(2026, 1, 1), 2.0), (datetime(2026, 1, 2), 2.1)]
        ),
        da._normalize_name("目标闸B"): da.ObservationSeries(
            [(datetime(2026, 1, 1), 3.0)]
        ),
    }
    selected, inventory = parallel_pid.select_reaches_with_water_levels(
        reaches, observations
    )
    assert [reach.pool_id for reach in selected] == [0]
    assert inventory[0]["water_level_observation_count"] == 2
    assert inventory[0]["selected"] is True
    assert inventory[1]["selected"] is False
    assert parallel_pid._worker_count(2, 8) == 2


def test_parallel_pid_rejects_missing_model_object_or_initial_flow():
    reaches = [
        da.ReachSpec(0, "上游闸A", "目标闸A", "上游闸A", "d0", "渠段A"),
        da.ReachSpec(1, "上游闸B", "目标闸B", "上游闸B", "d1", "渠段B"),
    ]
    times = [datetime(2026, 1, 1), datetime(2026, 1, 2)]
    water_levels = {
        da._normalize_name(reach.target_gate_name): da.ObservationSeries(
            list(zip(times, [2.0, 2.1]))
        )
        for reach in reaches
    }
    selected, inventory = parallel_pid.select_reaches_with_water_levels(
        reaches,
        water_levels,
        boundary_flow={"d1": da.ObservationSeries(list(zip(times, [3.0, 3.1])))},
        model_gate_names={"目标闸a", "目标闸b", "d1"},
        model_start_time=times[0],
    )
    assert [reach.pool_id for reach in selected] == [1]
    assert "DLL 模型缺少对象: d0" in inventory[0]["reason"]
    assert "模型起始时刻缺少基础分水流量" in inventory[0]["reason"]
    assert inventory[1]["initial_boundary_flow"] == 3.0


def test_plot_marks_only_isolated_measured_flow_spike():
    values = [20.0, 21.0, 22.0, 23.0, 100.0, 24.0, 25.0, 30.0, 35.0]
    normal, outliers = enkf_plotting._split_isolated_outliers(values)
    assert np.isnan(normal[4])
    assert outliers[4] == 100.0
    assert np.isfinite(normal[7])
    assert np.isnan(outliers[7])


def test_pid_ignores_flow_outlier_but_preserves_raw_bundle():
    times = [datetime(2026, 1, day) for day in range(1, 10)]
    raw_series = da.ObservationSeries(
        list(zip(times, [10, 11, 12, 13, 100, 14, 15, 16, 17]))
    )
    raw = da.ObservationBundle(boundary_flow={"gate": raw_series})
    cleaned = pid_reconstruction._clean_flow_observations(raw)
    assert raw.boundary_flow["gate"].value_at(times[4], 86400, "exact") == 100.0
    assert cleaned.boundary_flow["gate"].value_at(times[4], 86400, "exact") == 13.5


def test_pid_fake_hydraulic_hourly_tracking(tmp_path: Path):
    case = make_case(tmp_path)
    result = pid_reconstruction.run_pid_reconstruction(
        case,
        da.discover_reaches(case)[0],
        config=pid_reconstruction.PIDConfig(
            steps=3,
            kp=0.5,
            ki=0.0,
            kd=0.0,
            action_sign=1.0,
            initial_water_depth=1.0,
        ),
        output_root=tmp_path / "pid",
        client_factory=FakeClient,
    )
    assert result["method"] == "hourly PID"
    assert Path(result["output_csv"]).exists()
    assert Path(result["output_plot"]).exists()
    rows = list(csv.DictReader(Path(result["output_csv"]).open(encoding="utf-8-sig")))
    assert len(rows) == 4
    assert all(row["used_observations"] == "h1_pid_target" for row in rows[:3])
    assert float(rows[1]["q_boundary_analysis"]) != float(rows[1]["q_boundary_forecast"])
    assert float(rows[1]["q_boundary_dll_forecast"]) == float(rows[1]["q_boundary_forecast"])
    assert float(rows[1]["q_boundary_dll_analysis"]) == float(rows[1]["q_boundary_analysis"])
    assert float(rows[1]["q_boundary_execution_error"]) == 0.0
    assert float(rows[1]["h1_control_target"]) == float(rows[1]["h1_obs"])


def test_rolling_nsga_fake_hydraulic_window(tmp_path: Path):
    case = make_case(tmp_path)
    result = rolling.run_rolling_nsga_reconstruction(
        case,
        da.discover_reaches(case)[0],
        config=rolling.RollingNSGAConfig(
            steps=3,
            window_steps=2,
            node_count=3,
            population_size=4,
            generations=1,
            initial_water_depth=1.0,
        ),
        output_root=tmp_path / "rolling",
        client_factory=FakeClient,
    )
    assert result["method"] == "rolling NSGA-II"
    assert len(result["windows"]) == 1
    assert Path(result["output_csv"]).exists()
    assert Path(result["output_plot"]).exists()
    rows = list(csv.DictReader(Path(result["output_csv"]).open(encoding="utf-8-sig")))
    assert len(rows) == 4
    assert float(rows[1]["h1_obs"]) == 2.75


def test_denkf_cross_covariance_updates_missing_q():
    q = np.linspace(1.0, 3.0, 20)
    h = 1.0 + 0.5 * q
    x = np.zeros((len(da.STATE_NAMES), q.size))
    x[da.STATE_INDEX["q_boundary"]] = q
    x[da.STATE_INDEX["ues"]] = 0.6
    x[da.STATE_INDEX["uef"]] = 0.6
    x[da.STATE_INDEX["h1"]] = h
    result = da.DEnKF(inflation=1.0).update(x, {"h1": 2.8}, {"h1": 0.05})
    assert np.mean(result.analysis_ensemble[da.STATE_INDEX["q_boundary"]]) > np.mean(q)


def test_h1_only_assimilation_does_not_use_flow_observations(tmp_path: Path):
    case = make_case(tmp_path)
    reach = da.discover_reaches(case)[0]
    cfg = da.AssimilationConfig(
        ensemble_size=6,
        steps=1,
        initial_water_depth=1.0,
        observation_match="exact",
        assimilated_observations=("h1",),
        initial_std_ues=0.0,
        initial_std_uef=0.0,
        process_std_ues=0.0,
        process_std_uef=0.0,
        isolate_case_per_reach=False,
    )
    summary = da.run_reach_assimilation(
        "case",
        case,
        reach,
        config=cfg,
        output_root=tmp_path / "h1_only",
        client_factory=FakeClient,
    )
    row = next(csv.DictReader(Path(summary["output_csv"]).open(encoding="utf-8-sig")))
    assert row["used_observations"] == "h1"
    assert row["ues_analysis"] == row["ues_forecast"]
    assert row["uef_analysis"] == row["uef_forecast"]


def test_configured_boundary_flow_path_has_priority(tmp_path: Path):
    case = make_case(tmp_path)
    config_path = case / "OCIS_dataConfig.json"
    config_data = json.loads(config_path.read_text(encoding="utf-8"))
    config_data["SIM"]["boundary_flow_path"] = "input/action_obs.csv"
    config_path.write_text(json.dumps(config_data, ensure_ascii=False), encoding="utf-8")
    write_wide(
        case / "input" / "action_obs.csv",
        ["tm", "上游闸", "目标闸"],
        [["2026/01/01 00:00", 7.5, 6.0]],
    )
    summary = da.run_reach_assimilation(
        "case",
        case,
        da.discover_reaches(case)[0],
        config=da.AssimilationConfig(
            ensemble_size=6,
            steps=1,
            initial_water_depth=1.0,
            observation_match="exact",
            isolate_case_per_reach=False,
            anchor_q_to_observation=True,
        ),
        output_root=tmp_path / "configured_boundary",
        client_factory=FakeClient,
    )
    row = next(csv.DictReader(Path(summary["output_csv"]).open(encoding="utf-8-sig")))
    assert float(row["q_boundary_obs"]) == 1.5
    assert np.isclose(float(row["q_boundary_forecast"]), 1.5)
    assert summary["observation_files"]["boundary_flow"].endswith("action_obs.csv")


def test_observation_modes():
    series = da.ObservationSeries([
        (da.datetime(2026, 1, 1, 0), 1.0),
        (da.datetime(2026, 1, 1, 2), 3.0),
    ])
    target = da.datetime(2026, 1, 1, 1, 30)
    assert series.value_at(target, 7200, "nearest") == 3.0
    assert series.value_at(target, 7200, "causal") == 1.0
    assert series.value_at(target, 7200, "linear") == 2.5
    assert series.value_at(target, 7200, "exact") is None


def test_discover_and_run_reach(tmp_path: Path):
    case = make_case(tmp_path)
    reaches = da.discover_reaches(case)
    assert len(reaches) == 1
    assert reaches[0].target_gate_name == "目标闸"
    assert reaches[0].boundary_name == "上游闸"
    assert reaches[0].boundary_model_name == "d0"

    cfg = da.AssimilationConfig(
        ensemble_size=10,
        steps=3,
        initial_water_depth=1.0,
        process_std_q=0.2,
        initial_std_q=0.4,
        observation_match="exact",
        isolate_case_per_reach=False,
        q_max_step=2.0,
    )
    out = tmp_path / "out"
    summary = da.run_reach_assimilation(
        "case",
        case,
        reaches[0],
        config=cfg,
        output_root=out,
        client_factory=FakeClient,
    )
    assert summary["steps"] == 3
    csv_path = Path(summary["output_csv"])
    assert csv_path.exists()
    assert Path(summary["output_plot"]).exists()
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    assert len(rows) == 3
    assert rows[1]["q_boundary_obs"] == ""
    assert float(rows[1]["q_boundary_completed"]) > 0.0
    assert rows[1]["h2_obs"] == ""
    assert float(rows[1]["h2_completed"]) > 0.0
    assert "h1" in rows[1]["used_observations"]
    assert float(rows[0]["upstream_gate_q_forecast"]) > 0.0
    assert np.isclose(
        float(rows[0]["net_gate_q_forecast"]),
        float(rows[0]["upstream_gate_q_forecast"]) - float(rows[0]["gate_q_forecast"]),
    )


def test_no_replay_is_valid():
    cfg = da.AssimilationConfig(analysis_replay=False)
    cfg.validate()


def test_public_parallel_entry_sequential(tmp_path: Path, monkeypatch):
    case = make_case(tmp_path)
    monkeypatch.setattr(da.HD_Roe, "create_client", lambda dll_path=None: FakeClient())
    cfg = da.AssimilationConfig(
        ensemble_size=6,
        steps=2,
        initial_water_depth=1.0,
        observation_match="exact",
        isolate_case_per_reach=False,
    )
    results = da.run_case_reaches_parallel(
        "case",
        case,
        config=cfg,
        output_root=tmp_path / "parallel_out",
        max_workers=1,
    )
    assert len(results) == 1
    assert "error" not in results[0]
    assert Path(results[0]["output_csv"]).exists()


def test_connection_type_topology_selects_reach_five(tmp_path: Path, monkeypatch):
    case = make_case(tmp_path)
    write_wide(
        case / "mesh" / "edges.csv",
        ["id", "source", "target", "ConnectionType", "type", "canal"],
        [[idx, f"source{idx}", f"target{idx}", "indirect", 4, "canal"] for idx in range(6)]
        + [[6, "outlet", -1, "direct", 4, "canal"]],
    )
    reaches = da.discover_reaches(case)
    assert [reach.pool_id for reach in reaches] == list(range(6))
    assert reaches[5].segment_name == "source5-target5"

    monkeypatch.setattr(
        da,
        "_run_reach_worker",
        lambda payload: {
            "pool_id": payload["reach"]["pool_id"],
            "segment": payload["reach"]["segment_name"],
        },
    )
    results = da.run_case_reaches_parallel(
        "case",
        case,
        reach_indices=[5],
        config=da.AssimilationConfig(ensemble_size=6, steps=1),
        max_workers=1,
    )
    assert results == [{"pool_id": 5, "segment": "source5-target5"}]


class FakeMissingDiagnosticsClient(FakeClient):
    def get_stepdata_sim(self, datatype):
        if datatype in {"gates_h2", "gates_e"}:
            return "{}"
        return super().get_stepdata_sim(datatype)


def test_missing_optional_model_diagnostics_do_not_abort(tmp_path: Path):
    case = make_case(tmp_path)
    reach = da.discover_reaches(case)[0]
    cfg = da.AssimilationConfig(
        ensemble_size=6,
        steps=1,
        initial_water_depth=1.0,
        observation_match="exact",
        isolate_case_per_reach=False,
    )
    summary = da.run_reach_assimilation(
        "case",
        case,
        reach,
        config=cfg,
        output_root=tmp_path / "missing_out",
        client_factory=FakeMissingDiagnosticsClient,
    )
    row = next(csv.DictReader(Path(summary["output_csv"]).open(encoding="utf-8-sig")))
    assert row["h2_analysis"] == ""
    assert row["gate_opening_analysis"] == ""
