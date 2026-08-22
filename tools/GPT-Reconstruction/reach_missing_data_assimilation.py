"""渠系分渠段缺失数据同化工具。

仅依赖 ``HD_Roe.py`` 与 NumPy，集中保留以下功能：

1. 分水流量边界缺失值同化；
2. 节制闸水位、流量、开度缺失值同化；
3. 闸门流量系数 Ues/Uef 的联合估计；
4. 基于独立 DLL 模块的集合成员连续传播；
5. 分渠段多进程并行；
6. CSV 与 JSON 结果输出。

实现采用确定性 EnKF（DEnKF）分析更新。每个集合成员均由独立 DLL 模块连续推进，
避免 DLL 内部状态在成员之间串扰。控制量分析值从下一时间步起写回模型。

说明：当前 DLL 未提供任意断面水深/流量状态向量的批量写回接口，因此本工具属于
“边界—参数增强状态同化”：分水流量与闸门参数会写回模型，闸前/闸后水位、闸门
流量和开度作为诊断变量和缺失值估计输出。
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import os
import re
import shutil
import sys
import tempfile
import uuid
import warnings
from bisect import bisect_left
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import HD_Roe
import enkf_plotting


# 直接点击运行本文件时使用的最小案例参数。
DIRECT_RUN_CASE_NAME = "sj_zonggan-d0"
DIRECT_RUN_REACH_INDEX = 6
DIRECT_RUN_STEPS = 720
DIRECT_RUN_ENSEMBLE_SIZE = 6
DIRECT_RUN_INITIAL_WATER_DEPTH = 2.2
DIRECT_RUN_OBSERVATION_MATCH = "linear"
DIRECT_RUN_MAX_OBS_GAP_MINUTES = 24 * 60
DIRECT_RUN_NSGA_WINDOW_STEPS = 24
DIRECT_RUN_NSGA_NODE_COUNT = 3
DIRECT_RUN_NSGA_POPULATION_SIZE = 8
DIRECT_RUN_NSGA_GENERATIONS = 4
DIRECT_RUN_PID_KP = 40.0
DIRECT_RUN_PID_KI = 2.0
DIRECT_RUN_PID_KD = 0.0
DIRECT_RUN_PID_INTEGRAL_LIMIT = 150.0
DIRECT_RUN_PID_ANTI_WINDUP_GAIN = 0.2
DIRECT_RUN_PID_Q_MAX_STEP = 20.0
DIRECT_RUN_FILTER_FLOW_OUTLIERS = False
DIRECT_RUN_USE_DLL_FLOW_FEEDBACK = True
DIRECT_RUN_PID_RUN_BASELINE = False


__all__ = [
    "AssimilationConfig",
    "ObservationFiles",
    "ReachSpec",
    "DEnKF",
    "discover_reaches",
    "get_case_topology",
    "get_case_parallel_plan",
    "load_observation_csv",
    "run_reach_assimilation",
    "run_case_enkf",
    "run_case_reaches_parallel",
]


# 增强状态向量顺序。前三项会写回 DLL，其余项用于观测算子和缺失值输出。
STATE_NAMES: tuple[str, ...] = (
    "q_boundary",
    "ues",
    "uef",
    "h1",
    "h2",
    "gate_q",
    "gate_opening",
)
STATE_INDEX = {name: idx for idx, name in enumerate(STATE_NAMES)}
CONTROL_NAMES = STATE_NAMES[:3]
DIAGNOSTIC_NAMES = STATE_NAMES[3:]


class AssimilationError(RuntimeError):
    """数据同化运行错误。"""


@dataclass(frozen=True)
class ReachSpec:
    """单个渠段的数据同化定义。"""

    pool_id: int
    upstream_gate_name: str
    target_gate_name: str
    boundary_name: str
    boundary_model_name: str | None = None
    segment_name: str = ""
    edge_id: str | int | None = None
    canal_name: str = ""

    def normalized(self) -> "ReachSpec":
        boundary_model_name = self.boundary_model_name or self.boundary_name
        segment_name = self.segment_name or f"{self.upstream_gate_name}-{self.target_gate_name}"
        return ReachSpec(
            pool_id=int(self.pool_id),
            upstream_gate_name=str(self.upstream_gate_name).strip(),
            target_gate_name=str(self.target_gate_name).strip(),
            boundary_name=str(self.boundary_name).strip(),
            boundary_model_name=str(boundary_model_name).strip(),
            segment_name=str(segment_name).strip(),
            edge_id=self.edge_id,
            canal_name=str(self.canal_name or "").strip(),
        )


@dataclass(frozen=True)
class ObservationFiles:
    """观测文件路径；未提供的文件会在案例 input 目录中自动查找。"""

    boundary_flow: Path | None = None
    gate_h1: Path | None = None
    gate_h2: Path | None = None
    gate_flow: Path | None = None
    gate_opening: Path | None = None


@dataclass
class AssimilationConfig:
    """数据同化参数。

    观测标准差和过程标准差均使用对应变量的物理单位。所有边界和参数限值在每次
    分析更新后强制执行，以避免集合成员进入非物理解域。
    """

    ensemble_size: int = 24
    random_seed: int = 42

    # DEnKF 数值设置
    inflation: float = 1.03
    covariance_ridge: float = 1.0e-9
    analysis_replay: bool = False

    # 过程噪声：持久性模型 + 随机游走
    process_std_q: float = 0.15
    process_std_ues: float = 0.002
    process_std_uef: float = 0.002

    # 初始集合离散度
    initial_std_q: float = 0.30
    initial_std_ues: float = 0.006
    initial_std_uef: float = 0.006

    # 新边界观测到达时，将集合均值锚定到给定流量过程，再由水位观测进行修正。
    anchor_q_to_observation: bool = False

    # 观测误差
    obs_std_q_boundary: float = 0.10
    obs_std_h1: float = 0.05
    obs_std_h2: float = 0.05
    obs_std_gate_q: float = 0.10
    obs_std_gate_opening: float = 0.02

    # 物理约束
    q_min: float = 0.0
    q_max: float | None = None
    q_max_step: float | None = None
    mu_min: float = 0.40
    mu_max: float = 1.00
    mu_max_step: float = 0.01
    gate_opening_min: float = 0.0
    gate_opening_max: float | None = None

    # 初值和模型设置
    initial_boundary_flow: float | None = None
    initial_ues: float = 0.60
    initial_uef: float = 0.60
    initial_water_depth: float | None = 2.0
    initialize_h1_from_observation: bool = True
    # Keep the bed elevations loaded from the case/DLL unless the caller
    # explicitly requests an artificial uniform bed level.
    initial_bed_level: float | None = None
    sim_solver_type: str = "sediment"

    # 观测匹配。nearest 保持原脚本行为；causal 不使用未来观测。
    max_obs_gap_minutes: int = 30
    observation_match: str = "nearest"
    assimilated_observations: tuple[str, ...] = (
        "q_boundary",
        "h1",
        "h2",
        "gate_q",
        "gate_opening",
    )

    # 运行设置
    steps: int | None = None
    isolate_case_per_reach: bool = True
    keep_workdir: bool = False
    fail_fast: bool = False

    def validate(self) -> None:
        if self.ensemble_size < 3:
            raise ValueError("ensemble_size 必须至少为 3")
        if self.inflation <= 0.0:
            raise ValueError("inflation 必须大于 0")
        if self.covariance_ridge < 0.0:
            raise ValueError("covariance_ridge 不能为负")
        if self.mu_min >= self.mu_max:
            raise ValueError("mu_min 必须小于 mu_max")
        if self.q_max is not None and self.q_max <= self.q_min:
            raise ValueError("q_max 必须大于 q_min")
        if self.mu_max_step <= 0.0:
            raise ValueError("mu_max_step 必须大于 0")
        if self.max_obs_gap_minutes < 0:
            raise ValueError("max_obs_gap_minutes 不能为负")
        if self.observation_match not in {"nearest", "causal", "linear", "exact"}:
            raise ValueError("observation_match 仅支持 nearest、causal、linear 或 exact")
        invalid_observations = set(self.assimilated_observations) - set(self.observation_std)
        if invalid_observations:
            raise ValueError(f"不支持的同化观测: {sorted(invalid_observations)}")
        for name in (
            "process_std_q",
            "process_std_ues",
            "process_std_uef",
            "initial_std_q",
            "initial_std_ues",
            "initial_std_uef",
            "obs_std_q_boundary",
            "obs_std_h1",
            "obs_std_h2",
            "obs_std_gate_q",
            "obs_std_gate_opening",
        ):
            if float(getattr(self, name)) < 0.0:
                raise ValueError(f"{name} 不能为负")

    @property
    def observation_std(self) -> dict[str, float]:
        return {
            "q_boundary": float(self.obs_std_q_boundary),
            "h1": float(self.obs_std_h1),
            "h2": float(self.obs_std_h2),
            "gate_q": float(self.obs_std_gate_q),
            "gate_opening": float(self.obs_std_gate_opening),
        }


@dataclass
class ObservationSeries:
    """带时间索引的一维观测序列。"""

    values: list[tuple[datetime, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        cleaned: dict[datetime, float] = {}
        for timestamp, value in self.values:
            value_float = _finite_float(value)
            if value_float is not None:
                cleaned[timestamp] = value_float
        self.values = sorted(cleaned.items(), key=lambda item: item[0])
        self._timestamps = [item[0].timestamp() for item in self.values]
        self._numbers = [item[1] for item in self.values]

    def value_at(self, target: datetime | None, max_gap_seconds: int, mode: str = "nearest") -> float | None:
        if target is None or not self._timestamps:
            return None
        t = target.timestamp()
        idx = bisect_left(self._timestamps, t)

        if mode == "exact":
            if idx < len(self._timestamps) and self._timestamps[idx] == t:
                return self._numbers[idx]
            return None

        if mode == "causal":
            candidate = idx if idx < len(self._timestamps) and self._timestamps[idx] == t else idx - 1
            if candidate < 0:
                return None
            gap = t - self._timestamps[candidate]
            return self._numbers[candidate] if 0.0 <= gap <= max_gap_seconds else None

        if mode == "linear":
            if idx < len(self._timestamps) and self._timestamps[idx] == t:
                return self._numbers[idx]
            if idx == 0 or idx >= len(self._timestamps):
                return None
            before_t = self._timestamps[idx - 1]
            after_t = self._timestamps[idx]
            if t - before_t > max_gap_seconds or after_t - t > max_gap_seconds:
                return None
            weight = (t - before_t) / (after_t - before_t)
            return self._numbers[idx - 1] + weight * (
                self._numbers[idx] - self._numbers[idx - 1]
            )

        candidates: list[int] = []
        if idx < len(self._timestamps):
            candidates.append(idx)
        if idx - 1 >= 0:
            candidates.append(idx - 1)
        if not candidates:
            return None
        best = min(candidates, key=lambda j: abs(self._timestamps[j] - t))
        gap = abs(self._timestamps[best] - t)
        return self._numbers[best] if gap <= max_gap_seconds else None


@dataclass
class ObservationBundle:
    boundary_flow: dict[str, ObservationSeries] = field(default_factory=dict)
    gate_h1: dict[str, ObservationSeries] = field(default_factory=dict)
    gate_h2: dict[str, ObservationSeries] = field(default_factory=dict)
    gate_flow: dict[str, ObservationSeries] = field(default_factory=dict)
    gate_opening: dict[str, ObservationSeries] = field(default_factory=dict)

    def get(self, category: str, name: str) -> ObservationSeries:
        mapping = getattr(self, category)
        return mapping.get(_normalize_name(name), ObservationSeries())


@dataclass
class DEnKFResult:
    analysis_ensemble: np.ndarray
    gain: np.ndarray
    innovation: np.ndarray
    used_observations: tuple[str, ...]


class DEnKF:
    """确定性集合卡尔曼滤波分析器。

    对缺失观测采用动态掩码：每个时刻仅构造实际可用观测对应的 H 和 R。未提供
    观测时直接返回膨胀后的预报集合；观测是否插值由运行配置决定。
    """

    def __init__(self, inflation: float = 1.03, covariance_ridge: float = 1.0e-9) -> None:
        if inflation <= 0.0:
            raise ValueError("inflation 必须大于 0")
        if covariance_ridge < 0.0:
            raise ValueError("covariance_ridge 不能为负")
        self.inflation = float(inflation)
        self.covariance_ridge = float(covariance_ridge)

    def update(
        self,
        forecast_ensemble: np.ndarray,
        observations: Mapping[str, float | None],
        observation_std: Mapping[str, float],
    ) -> DEnKFResult:
        x_f = np.asarray(forecast_ensemble, dtype=np.float64)
        if x_f.ndim != 2:
            raise ValueError("forecast_ensemble 必须为二维数组 [state, member]")
        n_state, n_member = x_f.shape
        if n_member < 3:
            raise ValueError("集合成员数必须至少为 3")
        if n_state != len(STATE_NAMES):
            raise ValueError(f"状态维数必须为 {len(STATE_NAMES)}")
        if not np.all(np.isfinite(x_f)):
            raise ValueError("forecast_ensemble 包含非有限值")

        mean_f = np.mean(x_f, axis=1, keepdims=True)
        anomalies_f = (x_f - mean_f) * self.inflation
        x_f_inflated = mean_f + anomalies_f

        used = tuple(
            name
            for name in STATE_NAMES
            if name in observations and _finite_float(observations[name]) is not None
        )
        if not used:
            return DEnKFResult(
                analysis_ensemble=x_f.copy(),
                gain=np.zeros((n_state, 0), dtype=np.float64),
                innovation=np.zeros(0, dtype=np.float64),
                used_observations=(),
            )

        obs_indices = np.array([STATE_INDEX[name] for name in used], dtype=int)
        y = np.array([float(observations[name]) for name in used], dtype=np.float64)
        std = np.array([float(observation_std[name]) for name in used], dtype=np.float64)
        if np.any(std <= 0.0):
            raise ValueError("参与同化的观测标准差必须大于 0")

        h_mean = mean_f[obs_indices, 0]
        h_anomalies = anomalies_f[obs_indices, :]
        denom = float(n_member - 1)
        p_xy = anomalies_f @ h_anomalies.T / denom
        p_yy = h_anomalies @ h_anomalies.T / denom
        r = np.diag(std * std)
        system = p_yy + r
        if self.covariance_ridge > 0.0:
            system = system + np.eye(system.shape[0]) * self.covariance_ridge

        gain = _right_solve(p_xy, system)
        innovation = y - h_mean
        mean_a = mean_f[:, 0] + gain @ innovation

        # DEnKF：分析异常采用一半观测修正，避免传统无扰动 EnKF 的方差过度收缩。
        anomalies_a = anomalies_f - 0.5 * gain @ h_anomalies
        analysis = mean_a[:, None] + anomalies_a
        return DEnKFResult(
            analysis_ensemble=analysis,
            gain=gain,
            innovation=innovation,
            used_observations=used,
        )


@dataclass
class _MemberOutput:
    vector: np.ndarray
    state_file: Path
    upstream_gate_q: float | None


@dataclass
class _ResolvedObservationFiles:
    boundary_flow: Path | None
    gate_h1: Path | None
    gate_h2: Path | None
    gate_flow: Path | None
    gate_opening: Path | None


# ---------------------------------------------------------------------------
# 通用读写与配置
# ---------------------------------------------------------------------------


def _normalize_name(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_json_comments(text: str) -> str:
    # 仅删除整行或前有空白的 // 注释，避免破坏 URL 中的 //。
    return re.sub(r"(^|\s)//.*$", "", text, flags=re.MULTILINE)


def _read_json_file(path: Path) -> dict[str, Any]:
    data = json.loads(_strip_json_comments(_read_text(path)))
    if not isinstance(data, dict):
        raise AssimilationError(f"JSON 顶层必须是对象: {path}")
    return data


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    formats = (
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def load_observation_csv(path: str | Path | None) -> dict[str, ObservationSeries]:
    """读取 tidy 或 wide 形式的观测 CSV。

    tidy 格式要求列名 ``tm,obj,value``；wide 格式第一列或 ``tm`` 列为时间，其余列
    作为对象名称。空值、NaN 和无穷值会被视为缺失。
    """

    if path is None:
        return {}
    csv_path = Path(path)
    if not csv_path.exists():
        return {}

    reader = csv.DictReader(_read_text(csv_path).splitlines())
    fieldnames = [str(name or "").strip() for name in (reader.fieldnames or [])]
    if not fieldnames:
        return {}
    lower_to_original = {name.lower(): name for name in fieldnames}
    tidy = {"tm", "obj", "value"}.issubset(lower_to_original)
    rows: dict[str, list[tuple[datetime, float]]] = {}

    if tidy:
        tm_key = lower_to_original["tm"]
        obj_key = lower_to_original["obj"]
        value_key = lower_to_original["value"]
        for row in reader:
            timestamp = _parse_datetime(row.get(tm_key))
            name = _normalize_name(row.get(obj_key))
            number = _finite_float(row.get(value_key))
            if timestamp is not None and name and number is not None:
                rows.setdefault(name, []).append((timestamp, number))
    else:
        tm_key = lower_to_original.get("tm", fieldnames[0])
        value_keys = [name for name in fieldnames if name != tm_key]
        for row in reader:
            timestamp = _parse_datetime(row.get(tm_key))
            if timestamp is None:
                continue
            for name in value_keys:
                number = _finite_float(row.get(name))
                if number is not None:
                    rows.setdefault(_normalize_name(name), []).append((timestamp, number))

    return {name: ObservationSeries(values) for name, values in rows.items()}


def _first_existing(directory: Path, candidates: Sequence[str]) -> Path | None:
    for candidate in candidates:
        path = directory / candidate
        if path.exists() and path.is_file():
            return path
    return None


def _resolve_observation_files(case_dir: Path, files: ObservationFiles | None) -> _ResolvedObservationFiles:
    files = files or ObservationFiles()
    input_dir = case_dir / "input"

    boundary = files.boundary_flow or _first_existing(
        input_dir, ("action.csv", "action_obs_td.csv", "action_obs.csv", "action_td.csv")
    )
    h1 = files.gate_h1 or _first_existing(
        input_dir, ("stage_obs_24.csv", "stage1_td.csv", "stage_td.csv", "stage.csv")
    )
    h2 = files.gate_h2 or _first_existing(
        input_dir, ("stage2_td.csv", "stage2.csv", "stage_td.csv", "stage.csv")
    )
    gate_q = files.gate_flow or boundary
    opening = files.gate_opening or _first_existing(
        input_dir, ("gate_e_td.csv", "gate_e_sum_td.csv", "gate_opening.csv")
    )
    return _ResolvedObservationFiles(
        boundary_flow=Path(boundary).resolve() if boundary else None,
        gate_h1=Path(h1).resolve() if h1 else None,
        gate_h2=Path(h2).resolve() if h2 else None,
        gate_flow=Path(gate_q).resolve() if gate_q else None,
        gate_opening=Path(opening).resolve() if opening else None,
    )


def _load_observation_bundle(files: _ResolvedObservationFiles) -> ObservationBundle:
    return ObservationBundle(
        boundary_flow=load_observation_csv(files.boundary_flow),
        gate_h1=load_observation_csv(files.gate_h1),
        gate_h2=load_observation_csv(files.gate_h2),
        gate_flow=load_observation_csv(files.gate_flow),
        gate_opening=load_observation_csv(files.gate_opening),
    )


def _resolve_config_path(case_dir: Path, config_path: str | Path | None) -> Path:
    if config_path is not None:
        resolved = Path(config_path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"配置文件不存在: {resolved}")
        return resolved
    local = case_dir / "OCIS_dataConfig.json"
    if local.exists():
        return local.resolve()
    raise FileNotFoundError(
        f"案例目录中未找到 OCIS_dataConfig.json，请通过 config_path 显式指定: {case_dir}"
    )


def _resolve_config_data_path(
    config_path: Path,
    case_dir: Path,
    section_name: str,
    field_name: str,
) -> Path | None:
    cfg = _read_json_file(config_path)
    section = cfg.get(section_name)
    if not isinstance(section, dict):
        return None
    relative = str(section.get(field_name, "") or "").strip().lstrip("/\\")
    if not relative:
        return None
    path = case_dir / relative
    return path.resolve() if path.is_file() else None


def _build_runtime_config(
    config_path: Path,
    case_dir: Path,
    reach: ReachSpec,
    config: AssimilationConfig,
) -> str:
    cfg = _read_json_file(config_path)
    selected_model = str(cfg.get("selected_model", "")).strip()
    if not selected_model:
        raise AssimilationError(f"配置文件缺少 selected_model: {config_path}")
    selected_cfg = cfg.get(selected_model)
    if not isinstance(selected_cfg, dict):
        raise AssimilationError(f"配置文件缺少模型配置 {selected_model}: {config_path}")

    # 工作目录中的案例配置使用相对路径，避免依赖项目固定层级。
    runtime = dict(cfg)
    model_cfg = dict(selected_cfg)
    model_cfg.update(
        {
            "case_name": case_dir.name,
            "dirpath": f"{case_dir.parent.as_posix()}/",
            "target_gate_name": reach.target_gate_name,
            "upstream_gate_name": reach.upstream_gate_name,
            "control_action_name": reach.boundary_name,
            "control_model_name": reach.boundary_model_name,
            "pool_segment_name": reach.segment_name,
        }
    )
    runtime[selected_model] = model_cfg

    sim_cfg = dict(cfg.get("SIM", {})) if isinstance(cfg.get("SIM"), dict) else {}
    sim_cfg.update(
        {
            "dirpath": f"{case_dir.as_posix()}/",
            "pool_id": int(reach.pool_id),
            "target_gate_name": reach.target_gate_name,
            "upstream_gate_name": reach.upstream_gate_name,
            "control_action_name": reach.boundary_name,
            "control_model_name": reach.boundary_model_name,
            "pool_segment_name": reach.segment_name,
            "use_pool_runtime": True,
            "sim_solver_type": config.sim_solver_type,
        }
    )
    runtime["SIM"] = sim_cfg

    if isinstance(cfg.get("WATERALLOCATION"), dict):
        wa_cfg = dict(cfg["WATERALLOCATION"])
        wa_cfg["dirpath"] = f"{case_dir.as_posix()}/"
        runtime["WATERALLOCATION"] = wa_cfg
    return json.dumps(runtime, ensure_ascii=False, indent=2)


def _parse_time_param(raw: str) -> tuple[datetime | None, int]:
    try:
        data = json.loads(raw)
    except Exception:
        return None, 3600
    if not isinstance(data, dict):
        return None, 3600
    step_dt = max(1, int(data.get("step_dt") or 3600))
    start_t = data.get("start_t")
    if start_t is not None:
        try:
            return datetime.fromtimestamp(int(start_t)), step_dt
        except (TypeError, ValueError, OSError):
            pass
    return _parse_datetime(data.get("start_time")), step_dt


def _parse_gate_info(raw: str) -> tuple[dict[str, str], dict[str, str]]:
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise AssimilationError("get_gate_info_sim 返回值不是有效 JSON") from exc
    if not isinstance(data, dict):
        raise AssimilationError("get_gate_info_sim 返回值必须是对象")
    id_to_name: dict[str, str] = {}
    for key, item in data.items():
        if not isinstance(item, dict):
            continue
        gate_id = str(item.get("id", key))
        gate_name = str(item.get("name", gate_id))
        id_to_name[gate_id] = gate_name
    name_to_id = {_normalize_name(name): gate_id for gate_id, name in id_to_name.items()}
    return id_to_name, name_to_id


def _safe_model_data(client: Any, data_type: str) -> dict[str, Any]:
    try:
        raw = client.get_stepdata_sim(data_type)
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_gate_value(data: Mapping[str, Any], gate_id: str | int) -> float | None:
    return _finite_float(data.get(str(gate_id)))


def _extract_mu(data: Mapping[str, Any], gate_id: str | int, fallback: tuple[float, float]) -> tuple[float, float]:
    item = data.get(str(gate_id))
    if not isinstance(item, dict):
        return fallback
    ues = _finite_float(item.get("Ues"))
    uef = _finite_float(item.get("Uef"))
    return (fallback[0] if ues is None else ues, fallback[1] if uef is None else uef)


def _set_mu(client: Any, gate_id: str | int, ues: float, uef: float) -> None:
    payload = {"Ues": float(ues), "Uef": float(uef)}
    try:
        client.set_GatesFlow_mu_byID_sim_multi(gate_id, payload)
        return
    except Exception:
        pass
    # 兼容仅支持单一 mu 的 DLL。
    client.set_GatesFlow_mu_byID_sim(gate_id, 0.5 * (float(ues) + float(uef)))


def _right_solve(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """计算 left @ inv(right)，优先线性求解并在奇异时退化为伪逆。"""

    try:
        return np.linalg.solve(right.T, left.T).T
    except np.linalg.LinAlgError:
        return left @ np.linalg.pinv(right)


def _clip_control_ensemble(
    ensemble: np.ndarray,
    previous: np.ndarray | None,
    config: AssimilationConfig,
) -> np.ndarray:
    result = np.asarray(ensemble, dtype=np.float64).copy()
    result[STATE_INDEX["q_boundary"]] = np.maximum(result[STATE_INDEX["q_boundary"]], config.q_min)
    if config.q_max is not None:
        result[STATE_INDEX["q_boundary"]] = np.minimum(
            result[STATE_INDEX["q_boundary"]], config.q_max
        )
    result[STATE_INDEX["ues"]] = np.clip(result[STATE_INDEX["ues"]], config.mu_min, config.mu_max)
    result[STATE_INDEX["uef"]] = np.clip(result[STATE_INDEX["uef"]], config.mu_min, config.mu_max)

    if previous is not None:
        if config.q_max_step is not None:
            prev_q = previous[STATE_INDEX["q_boundary"]]
            result[STATE_INDEX["q_boundary"]] = np.clip(
                result[STATE_INDEX["q_boundary"]],
                prev_q - config.q_max_step,
                prev_q + config.q_max_step,
            )
            result[STATE_INDEX["q_boundary"]] = np.maximum(
                result[STATE_INDEX["q_boundary"]], config.q_min
            )
        for name in ("ues", "uef"):
            idx = STATE_INDEX[name]
            result[idx] = np.clip(
                result[idx],
                previous[idx] - config.mu_max_step,
                previous[idx] + config.mu_max_step,
            )
            result[idx] = np.clip(result[idx], config.mu_min, config.mu_max)
    return result


def _completed_value(observed: float | None, estimate: float | None) -> float | None:
    return observed if observed is not None else estimate


def _rmse(pairs: Sequence[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return float(math.sqrt(sum((estimate - obs) ** 2 for obs, estimate in pairs) / len(pairs)))


# ---------------------------------------------------------------------------
# 拓扑解析
# ---------------------------------------------------------------------------


_COLUMN_ALIASES = {
    "edge_id": ("edge_id", "edgeid", "id", "index", "nindex", "编号", "边编号"),
    "connection_type": ("ConnectionType", "connection_type", "connection", "连接类型"),
    "source": (
        "source",
        "from",
        "upstream",
        "source_name",
        "start",
        "起点",
        "上游",
        "上游节点",
        "进口",
    ),
    "target": (
        "target",
        "to",
        "downstream",
        "target_name",
        "end",
        "终点",
        "下游",
        "下游节点",
        "出口",
    ),
    "canal": ("canal", "canal_name", "channel", "name", "渠名", "渠道", "渠段名称"),
    "type": ("type", "edge_type", "object_type", "kind", "类型", "对象类型"),
}


def _normalized_header(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "")).lower()


def _pick_column(fieldnames: Sequence[str], aliases: Sequence[str]) -> str | None:
    normalized = {_normalized_header(name): name for name in fieldnames}
    for alias in aliases:
        found = normalized.get(_normalized_header(alias))
        if found is not None:
            return found
    return None


def discover_reaches(case_path: str | Path) -> list[ReachSpec]:
    """从 ``mesh/edges.csv`` 识别渠段。

    若工程 edges.csv 字段命名特殊，建议直接向 ``run_case_reaches_parallel`` 传入
    ``reach_specs``，避免依赖自动识别。
    """

    case_dir = Path(case_path).resolve()
    edges_path = case_dir / "mesh" / "edges.csv"
    if not edges_path.exists():
        raise FileNotFoundError(f"缺少拓扑文件: {edges_path}")

    reader = csv.DictReader(_read_text(edges_path).splitlines())
    fieldnames = [str(name or "").strip() for name in (reader.fieldnames or [])]
    source_col = _pick_column(fieldnames, _COLUMN_ALIASES["source"])
    target_col = _pick_column(fieldnames, _COLUMN_ALIASES["target"])
    if source_col is None or target_col is None:
        raise AssimilationError(
            f"无法在 {edges_path} 中识别 source/target 字段，现有字段: {fieldnames}"
        )
    edge_col = _pick_column(fieldnames, _COLUMN_ALIASES["edge_id"])
    canal_col = _pick_column(fieldnames, _COLUMN_ALIASES["canal"])
    type_col = _pick_column(fieldnames, _COLUMN_ALIASES["type"])
    connection_type_col = _pick_column(fieldnames, _COLUMN_ALIASES["connection_type"])

    reaches: list[ReachSpec] = []
    for row_index, row in enumerate(reader):
        source = str(row.get(source_col, "") or "").strip()
        target = str(row.get(target_col, "") or "").strip()
        if not source or not target or source == "-1" or target == "-1":
            continue
        connection_type = (
            str(row.get(connection_type_col, "") or "").strip().lower()
            if connection_type_col
            else ""
        )
        if connection_type_col and connection_type != "indirect":
            continue
        object_type = str(row.get(type_col, "") or "").strip().lower() if type_col else ""
        if any(token in object_type for token in ("pump", "泵", "reservoir", "水库")):
            continue
        if not connection_type_col and object_type and not any(
            token in object_type for token in ("canal", "channel", "reach", "pool", "渠", "段")
        ):
            # 类型明确且不是渠段时跳过；类型为空时保留，兼容旧表。
            continue
        edge_id: str | int | None = str(row.get(edge_col, "") or "").strip() if edge_col else row_index
        canal = str(row.get(canal_col, "") or "").strip() if canal_col else ""
        pool_id = len(reaches)
        reaches.append(
            ReachSpec(
                pool_id=pool_id,
                upstream_gate_name=source,
                target_gate_name=target,
                boundary_name=source,
                boundary_model_name=f"d{pool_id}",
                segment_name=f"{source}-{target}",
                edge_id=edge_id,
                canal_name=canal,
            )
        )
    if not reaches:
        raise AssimilationError(f"未从 {edges_path} 识别出任何渠段")
    return reaches


def get_case_topology(case_path: str | Path) -> dict[str, Any]:
    reaches = discover_reaches(case_path)
    gates = sorted(
        {name for reach in reaches for name in (reach.upstream_gate_name, reach.target_gate_name)}
    )
    canals = sorted({reach.canal_name for reach in reaches if reach.canal_name})
    return {
        "case_path": str(Path(case_path).resolve()),
        "edges_path": str(Path(case_path).resolve() / "mesh" / "edges.csv"),
        "gate_names": gates,
        "canal_names": canals,
        "relations": [asdict(reach) | {"is_canal_reach": True} for reach in reaches],
        "upstream_of": {reach.target_gate_name: reach.upstream_gate_name for reach in reaches},
        "downstream_of": {reach.upstream_gate_name: reach.target_gate_name for reach in reaches},
        "canal_gate_pairs": [
            {
                "canal": reach.canal_name,
                "source": reach.upstream_gate_name,
                "target": reach.target_gate_name,
            }
            for reach in reaches
        ],
    }


def get_case_parallel_plan(
    case_path: str | Path,
    *,
    cpu_count: int | None = None,
    max_workers_limit: int | None = None,
    reach_specs: Sequence[ReachSpec] | None = None,
) -> dict[str, Any]:
    reaches = [reach.normalized() for reach in (reach_specs or discover_reaches(case_path))]
    available_cpu = max(1, int(cpu_count or os.cpu_count() or 1))
    worker_limit = max(1, int(max_workers_limit)) if max_workers_limit is not None else available_cpu
    suggested = max(1, min(len(reaches), available_cpu, worker_limit))
    return {
        "case_path": str(Path(case_path).resolve()),
        "reach_count": len(reaches),
        "gate_count": len(
            {name for reach in reaches for name in (reach.upstream_gate_name, reach.target_gate_name)}
        ),
        "canal_count": len({reach.canal_name for reach in reaches if reach.canal_name}),
        "cpu_count": available_cpu,
        "max_workers_limit": worker_limit,
        "suggested_max_workers": suggested,
        "reach_segments": [asdict(reach) for reach in reaches],
    }


# ---------------------------------------------------------------------------
# DLL 运行与集合传播
# ---------------------------------------------------------------------------


def _prepare_case_workdir(
    source_case: Path,
    config_path: Path,
    reach: ReachSpec,
    isolate: bool,
) -> tuple[Path, Path, Path | None]:
    if not isolate:
        return source_case, config_path, None

    work_root = Path(tempfile.mkdtemp(prefix=f"ocis_da_pool_{reach.pool_id}_"))
    staged_case = work_root / source_case.name
    shutil.copytree(
        source_case,
        staged_case,
        ignore=shutil.ignore_patterns("output", "Temp", "__pycache__", "*.pyc"),
    )
    staged_config = staged_case / "OCIS_dataConfig.json"
    if config_path.resolve() != (source_case / "OCIS_dataConfig.json").resolve():
        shutil.copy2(config_path, staged_config)
    elif not staged_config.exists():
        shutil.copy2(config_path, staged_config)
    return staged_case, staged_config, work_root


def _load_client(
    runtime_config: str,
    config: AssimilationConfig,
    client_factory: Callable[[], Any],
) -> Any:
    client = client_factory()
    client.read_data_MILP_c_str(runtime_config)
    client.set_Algorithm_sim(config.sim_solver_type)
    try:
        client.read_data_sim_Roe_c_str_pools(runtime_config)
    except Exception as exc:
        raise AssimilationError(
            "当前 DLL 不支持分渠段接口 read_data_sim_Roe_c_str_pools"
        ) from exc
    if config.initial_water_depth is not None:
        client.set_all_inih(float(config.initial_water_depth))
    if config.initial_bed_level is not None:
        try:
            client.set_all_zb(float(config.initial_bed_level))
        except Exception:
            warnings.warn("DLL 未提供 set_all_zb，已跳过初始渠底高程设置", RuntimeWarning)
    return client


def _advance_member(
    client: Any,
    previous_state_file: Path | None,
    output_state_file: Path,
    step_index: int,
    pool_id: int,
    boundary_gate_id: str | int,
    upstream_gate_id: str | int | None,
    target_gate_id: str | int,
    controls: np.ndarray,
) -> _MemberOutput:
    if previous_state_file is not None:
        client.set_states(str(previous_state_file))
    client.update_BC_sim_only(int(step_index))
    q_value, ues, uef = (float(controls[0]), float(controls[1]), float(controls[2]))
    client.set_GatesFlow_byID_sim(boundary_gate_id, q_value)
    _set_mu(client, target_gate_id, ues, uef)
    client.stepSolver_sim_Roe_only_pool(int(step_index), int(pool_id))
    try:
        if client.check_nan_sim():
            raise AssimilationError(f"第 {step_index} 步水动力模型出现 NaN")
    except AttributeError:
        pass

    h1_data = _safe_model_data(client, "gates_h1")
    h2_data = _safe_model_data(client, "gates_h2")
    q_data = _safe_model_data(client, "gates_Q")
    e_data = _safe_model_data(client, "gates_e")
    mu_data = _safe_model_data(client, "gates_mu")
    ues_model, uef_model = _extract_mu(mu_data, target_gate_id, (ues, uef))

    vector = np.array(
        [
            q_value,
            ues_model,
            uef_model,
            _extract_gate_value(h1_data, target_gate_id),
            _extract_gate_value(h2_data, target_gate_id),
            _extract_gate_value(q_data, target_gate_id),
            _extract_gate_value(e_data, target_gate_id),
        ],
        dtype=np.float64,
    )
    if not np.all(np.isfinite(vector[:3])):
        missing = [STATE_NAMES[i] for i, value in enumerate(vector[:3]) if not np.isfinite(value)]
        raise AssimilationError(
            f"第 {step_index} 步渠段 {pool_id} 控制量包含非有限值: {missing}"
        )
    client.save_states(str(output_state_file))
    upstream_gate_q = (
        _extract_gate_value(q_data, upstream_gate_id)
        if upstream_gate_id is not None
        else None
    )
    return _MemberOutput(
        vector=vector,
        state_file=output_state_file,
        upstream_gate_q=upstream_gate_q,
    )




def _sanitize_model_ensemble(ensemble: np.ndarray) -> tuple[np.ndarray, dict[str, bool]]:
    """填补成员级偶发缺值，并返回模型变量可用性。

    控制变量必须对所有成员有效。诊断变量若部分成员缺失，以有效成员均值补齐；若
    全部成员缺失，则该变量不参与当步同化，内部临时填 0 仅为保持固定状态维数。
    """

    values = np.asarray(ensemble, dtype=np.float64).copy()
    availability: dict[str, bool] = {}
    for name in CONTROL_NAMES:
        row = values[STATE_INDEX[name]]
        if not np.all(np.isfinite(row)):
            raise AssimilationError(f"控制变量 {name} 的集合包含非有限值")
        availability[name] = True
    for name in DIAGNOSTIC_NAMES:
        idx = STATE_INDEX[name]
        row = values[idx]
        finite = np.isfinite(row)
        if not np.any(finite):
            values[idx] = 0.0
            availability[name] = False
            continue
        if not np.all(finite):
            values[idx, ~finite] = float(np.mean(row[finite]))
        availability[name] = True
    return values, availability


def _initial_control_ensemble(
    q_center: float,
    ues_center: float,
    uef_center: float,
    config: AssimilationConfig,
    rng: np.random.Generator,
) -> np.ndarray:
    ensemble = np.zeros((len(STATE_NAMES), config.ensemble_size), dtype=np.float64)
    ensemble[STATE_INDEX["q_boundary"]] = rng.normal(
        q_center, config.initial_std_q, size=config.ensemble_size
    )
    ensemble[STATE_INDEX["ues"]] = rng.normal(
        ues_center, config.initial_std_ues, size=config.ensemble_size
    )
    ensemble[STATE_INDEX["uef"]] = rng.normal(
        uef_center, config.initial_std_uef, size=config.ensemble_size
    )
    return _clip_control_ensemble(ensemble, previous=None, config=config)


def _forecast_controls(
    previous_analysis: np.ndarray,
    config: AssimilationConfig,
    rng: np.random.Generator,
) -> np.ndarray:
    forecast = previous_analysis.copy()
    forecast[STATE_INDEX["q_boundary"]] += rng.normal(
        0.0, config.process_std_q, size=config.ensemble_size
    )
    forecast[STATE_INDEX["ues"]] += rng.normal(
        0.0, config.process_std_ues, size=config.ensemble_size
    )
    forecast[STATE_INDEX["uef"]] += rng.normal(
        0.0, config.process_std_uef, size=config.ensemble_size
    )
    return _clip_control_ensemble(forecast, previous=previous_analysis, config=config)


def _collect_observations(
    bundle: ObservationBundle,
    reach: ReachSpec,
    timestamp: datetime | None,
    config: AssimilationConfig,
    *,
    mode_override: str | None = None,
) -> dict[str, float | None]:
    gap = int(config.max_obs_gap_minutes * 60)
    mode = mode_override or config.observation_match
    h1 = bundle.get("gate_h1", reach.target_gate_name).value_at(timestamp, gap, mode)
    if h1 is None:
        h1 = bundle.get("gate_h1", reach.segment_name).value_at(timestamp, gap, mode)
    q_boundary = None
    if reach.boundary_model_name:
        q_boundary = bundle.get("boundary_flow", reach.boundary_model_name).value_at(
            timestamp, gap, mode
        )
    if q_boundary is None:
        upstream_q = bundle.get("boundary_flow", reach.upstream_gate_name).value_at(
            timestamp, gap, mode
        )
        target_q = bundle.get("boundary_flow", reach.target_gate_name).value_at(
            timestamp, gap, mode
        )
        if upstream_q is not None and target_q is not None:
            q_boundary = upstream_q - target_q
        elif upstream_q is not None:
            q_boundary = upstream_q
    if q_boundary is None:
        q_boundary = bundle.get("boundary_flow", reach.boundary_name).value_at(
            timestamp, gap, mode
        )
    return {
        "q_boundary": q_boundary,
        "h1": h1,
        "h2": bundle.get("gate_h2", reach.target_gate_name).value_at(timestamp, gap, mode),
        "gate_q": bundle.get("gate_flow", reach.target_gate_name).value_at(timestamp, gap, mode),
        "gate_opening": bundle.get("gate_opening", reach.target_gate_name).value_at(
            timestamp, gap, mode
        ),
    }


def _write_rows_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "step",
        "time",
        "pool_id",
        "segment",
        "upstream_gate_name",
        "target_gate_name",
        "boundary_name",
        "boundary_model_name",
        "upstream_gate_id",
        "target_gate_id",
        "boundary_gate_id",
        "used_observations",
        "q_boundary_obs",
        "q_boundary_forecast",
        "q_boundary_analysis",
        "q_boundary_completed",
        "q_boundary_dll_forecast",
        "q_boundary_dll_analysis",
        "q_boundary_execution_error",
        "h1_obs",
        "h1_control_target",
        "h1_forecast",
        "h1_analysis",
        "h1_completed",
        "h2_obs",
        "h2_forecast",
        "h2_analysis",
        "h2_completed",
        "gate_q_obs",
        "gate_q_forecast",
        "gate_q_analysis",
        "gate_q_completed",
        "upstream_gate_q_forecast",
        "upstream_gate_q_analysis",
        "net_gate_q_forecast",
        "net_gate_q_analysis",
        "gate_opening_obs",
        "gate_opening_forecast",
        "gate_opening_analysis",
        "gate_opening_completed",
        "ues_forecast",
        "ues_analysis",
        "uef_forecast",
        "uef_analysis",
        "pid_error",
        "pid_raw_error",
        "pid_water_level_bias",
        "h1_analysis_bias_corrected",
        "pid_correction",
        "pid_integral",
        "pid_derivative",
        "pid_kp",
        "pid_ki",
        "pid_kd",
        "pid_safety_mode",
        "pid_water_level_step",
        "pid_q_max",
        "downstream_gate_diversion_q_max",
        "downstream_gate_diversion_constraint_active",
        "flow_tracking_target_h1",
        "flow_tracking_selected_h1",
        "flow_tracking_abs_error",
        "flow_tracking_reachable",
        "flow_tracking_trials",
        "flow_tracking_q_min",
        "flow_tracking_q_max",
        "flow_tracking_downstream_gate_q_max",
        "pid_anti_windup",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def _create_client_factory(dll_path: str | Path | None) -> Callable[[], Any]:
    return lambda: HD_Roe.create_client(dll_path)


def _load_isolated_clients(
    dll_path: str | Path | None,
    count: int,
    runtime_config: str,
    config: AssimilationConfig,
) -> tuple[list[Any], list[Path]]:
    source = Path(dll_path or TOOLS_DIR / "OcisMILPNet.dll").resolve()
    clients: list[Any] = []
    copies: list[Path] = []
    try:
        for member in range(count):
            copied_dll = source.with_name(
                f"{source.stem}_enkf_{os.getpid()}_{member}_{uuid.uuid4().hex}{source.suffix}"
            )
            shutil.copy2(source, copied_dll)
            copies.append(copied_dll)
            clients.append(
                _load_client(
                    runtime_config,
                    config,
                    _create_client_factory(copied_dll),
                )
            )
    except Exception:
        _release_isolated_clients(clients, copies)
        raise
    return clients, copies


def _release_isolated_clients(clients: list[Any], copies: Sequence[Path]) -> None:
    handles = [client._dll._handle for client in clients if hasattr(client, "_dll")]
    directory_handles = [
        handle
        for client in clients
        for handle in getattr(client, "_dll_dir_handles", ())
    ]
    clients.clear()
    gc.collect()
    if os.name == "nt":
        import _ctypes

        for handle in handles:
            _ctypes.FreeLibrary(handle)
    for handle in directory_handles:
        try:
            handle.close()
        except Exception:
            pass
    for copied_dll in copies:
        try:
            copied_dll.unlink()
        except OSError:
            pass


def run_reach_assimilation(
    case_name: str,
    case_path: str | Path,
    reach_spec: ReachSpec,
    *,
    config: AssimilationConfig | None = None,
    config_path: str | Path | None = None,
    observation_files: ObservationFiles | None = None,
    output_root: str | Path | None = None,
    dll_path: str | Path | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """运行单个渠段的缺失数据同化。

    ``client_factory`` 主要用于测试；正常使用时由 ``dll_path`` 创建 HD_Roe 客户端。
    """

    config = config or AssimilationConfig()
    config.validate()
    reach = reach_spec.normalized()
    source_case = Path(case_path).resolve()
    if not source_case.is_dir():
        raise FileNotFoundError(f"案例目录不存在: {source_case}")
    if not (source_case / "input").is_dir() or not (source_case / "mesh").is_dir():
        raise FileNotFoundError(f"案例目录必须包含 input 和 mesh: {source_case}")

    source_config = _resolve_config_path(source_case, config_path)
    case_dir, runtime_config_path, work_root = _prepare_case_workdir(
        source_case, source_config, reach, config.isolate_case_per_reach
    )
    output_dir = Path(output_root).resolve() if output_root else source_case / "output" / "data_assimilation"
    reach_output_dir = output_dir / str(reach.pool_id)
    reach_output_dir.mkdir(parents=True, exist_ok=True)
    state_dir = (work_root or Path(tempfile.mkdtemp(prefix=f"ocis_da_state_{reach.pool_id}_"))) / "states"
    state_dir.mkdir(parents=True, exist_ok=True)
    transient_state_root = None if work_root is not None else state_dir.parent

    resolved_files = _resolve_observation_files(source_case, observation_files)
    if observation_files is None or observation_files.boundary_flow is None:
        configured_boundary = _resolve_config_data_path(
            source_config, source_case, "SIM", "boundary_flow_path"
        )
        if configured_boundary is not None:
            resolved_files.boundary_flow = configured_boundary
            resolved_files.gate_flow = configured_boundary
    observations = _load_observation_bundle(resolved_files)
    runtime_payload = _build_runtime_config(runtime_config_path, case_dir, reach, config)
    rng = np.random.default_rng(config.random_seed + 10007 * int(reach.pool_id))
    filter_impl = DEnKF(config.inflation, config.covariance_ridge)

    rows: list[dict[str, Any]] = []
    validation_pairs: dict[str, list[tuple[float, float]]] = {
        "q_boundary": [],
        "h1": [],
        "h2": [],
        "gate_q": [],
        "gate_opening": [],
    }

    member_clients: list[Any] = []
    isolated_dll_copies: list[Path] = []
    client: Any | None = None
    try:
        if client_factory is None:
            member_clients, isolated_dll_copies = _load_isolated_clients(
                dll_path, config.ensemble_size, runtime_payload, config
            )
        else:
            member_clients = [
                _load_client(runtime_payload, config, client_factory)
                for _ in range(config.ensemble_size)
            ]
        client = member_clients[0]
        id_to_name, name_to_id = _parse_gate_info(client.get_gate_info_sim())
        target_gate_id = name_to_id.get(_normalize_name(reach.target_gate_name))
        upstream_gate_id = name_to_id.get(_normalize_name(reach.upstream_gate_name))
        boundary_gate_id = name_to_id.get(_normalize_name(reach.boundary_model_name))
        if boundary_gate_id is None:
            boundary_gate_id = name_to_id.get(_normalize_name(reach.upstream_gate_name))
        if target_gate_id is None:
            raise AssimilationError(f"模型中未找到目标节制闸: {reach.target_gate_name}")
        if boundary_gate_id is None:
            raise AssimilationError(
                f"模型中未找到分水边界 {reach.boundary_model_name} 或上游闸 {reach.upstream_gate_name}"
            )

        start_time, step_seconds = _parse_time_param(client.get_time_param())
        initial_obs = _collect_observations(observations, reach, start_time, config)
        if config.initialize_h1_from_observation and initial_obs["h1"] is not None:
            for member_client in member_clients:
                try:
                    member_client.set_inih_1_byGates(
                        {int(target_gate_id): float(initial_obs["h1"])}
                    )
                except Exception:
                    # 兼容旧 DLL：无法逐闸门设置时退化为当前渠段观测水位。
                    member_client.set_all_inih(float(initial_obs["h1"]))

        model_steps = int(client.get_Total_T_step_count())
        steps = int(config.steps) if config.steps is not None else model_steps
        if steps <= 0:
            raise AssimilationError("同化步数必须大于 0")
        if model_steps > 0:
            steps = min(steps, model_steps)

        initial_state = state_dir / "initial.state"
        client.save_states(str(initial_state))

        q_center = initial_obs["q_boundary"]
        if q_center is None:
            q_center = config.initial_boundary_flow
        if q_center is None:
            q_center = _extract_gate_value(_safe_model_data(client, "gates_Q"), boundary_gate_id)
        if q_center is None:
            raise AssimilationError(
                "首时刻分水流量缺失，且未设置 initial_boundary_flow，模型也未返回初始边界流量"
            )

        mu_data = _safe_model_data(client, "gates_mu")
        ues_center, uef_center = _extract_mu(
            mu_data, target_gate_id, (config.initial_ues, config.initial_uef)
        )
        analysis_ensemble = _initial_control_ensemble(
            q_center, ues_center, uef_center, config, rng
        )
        restore_member_states = client_factory is not None
        previous_state_files: list[Path | None] = (
            [initial_state] * config.ensemble_size
            if restore_member_states
            else [None] * config.ensemble_size
        )

        for step in range(steps):
            prior_state_files = [path for path in previous_state_files if path is not None]
            timestamp = (
                datetime.fromtimestamp(start_time.timestamp() + step * step_seconds)
                if start_time is not None
                else None
            )
            assimilation_obs = _collect_observations(observations, reach, timestamp, config)
            raw_obs = _collect_observations(
                observations,
                reach,
                timestamp,
                config,
                mode_override="exact",
            )
            if step == 0:
                forecast_controls = analysis_ensemble.copy()
            else:
                forecast_controls = _forecast_controls(analysis_ensemble, config, rng)
            if config.anchor_q_to_observation and assimilation_obs["q_boundary"] is not None:
                q_index = STATE_INDEX["q_boundary"]
                forecast_controls[q_index] += float(assimilation_obs["q_boundary"]) - float(
                    np.mean(forecast_controls[q_index])
                )

            forecast_vectors = np.empty((len(STATE_NAMES), config.ensemble_size), dtype=np.float64)
            forecast_upstream_q = np.full(config.ensemble_size, np.nan, dtype=np.float64)
            forecast_state_files: list[Path] = []
            for member in range(config.ensemble_size):
                output_state = state_dir / f"forecast_{step:06d}_{member:04d}.state"
                member_output = _advance_member(
                    member_clients[member],
                    previous_state_files[member],
                    output_state,
                    step,
                    reach.pool_id,
                    boundary_gate_id,
                    upstream_gate_id,
                    target_gate_id,
                    forecast_controls[:3, member],
                )
                forecast_vectors[:, member] = member_output.vector
                if member_output.upstream_gate_q is not None:
                    forecast_upstream_q[member] = member_output.upstream_gate_q
                forecast_state_files.append(member_output.state_file)

            forecast_vectors, forecast_available = _sanitize_model_ensemble(forecast_vectors)
            filter_observations = {
                name: value if name in config.assimilated_observations else None
                for name, value in assimilation_obs.items()
            }
            for name in DIAGNOSTIC_NAMES:
                if not forecast_available[name]:
                    filter_observations[name] = None
            update = filter_impl.update(
                forecast_vectors, filter_observations, config.observation_std
            )
            raw_analysis = update.analysis_ensemble
            clipped_analysis = _clip_control_ensemble(
                raw_analysis, previous=forecast_vectors, config=config
            )

            # DEnKF 对诊断变量给出统计分析值；控制变量则必须与模型状态重演一致。
            analysis_controls = clipped_analysis[:3, :]
            analysis_vectors = clipped_analysis.copy()
            analysis_upstream_q = forecast_upstream_q.copy()
            analysis_state_files = forecast_state_files
            if restore_member_states and config.analysis_replay and update.used_observations:
                analysis_state_files = []
                replay_clients = [
                    _load_client(runtime_payload, config, client_factory)
                    for _ in range(config.ensemble_size)
                ]
                for member in range(config.ensemble_size):
                    output_state = state_dir / f"analysis_{step:06d}_{member:04d}.state"
                    member_output = _advance_member(
                        replay_clients[member],
                        previous_state_files[member],
                        output_state,
                        step,
                        reach.pool_id,
                        boundary_gate_id,
                        upstream_gate_id,
                        target_gate_id,
                        analysis_controls[:, member],
                    )
                    analysis_vectors[:, member] = member_output.vector
                    if member_output.upstream_gate_q is not None:
                        analysis_upstream_q[member] = member_output.upstream_gate_q
                    analysis_state_files.append(member_output.state_file)
                member_clients = replay_clients

            analysis_vectors, analysis_available = _sanitize_model_ensemble(analysis_vectors)
            for name in DIAGNOSTIC_NAMES:
                analysis_available[name] = (
                    analysis_available[name] and forecast_available[name]
                )
            forecast_mean = np.mean(forecast_vectors, axis=1)
            analysis_mean = np.mean(analysis_vectors, axis=1)
            analysis_ensemble = analysis_vectors.copy()
            previous_state_files = (
                analysis_state_files
                if restore_member_states
                else [None] * config.ensemble_size
            )

            # 预报状态在分析重演后不再需要；上一时刻状态在所有成员完成传播后释放。
            if restore_member_states and config.analysis_replay and update.used_observations:
                for path in set(forecast_state_files):
                    if path not in previous_state_files:
                        path.unlink(missing_ok=True)
            for path in set(prior_state_files):
                if path not in previous_state_files:
                    path.unlink(missing_ok=True)

            row: dict[str, Any] = {
                "step": step,
                "time": timestamp.strftime("%Y/%m/%d %H:%M:%S") if timestamp else f"step_{step}",
                "pool_id": reach.pool_id,
                "segment": reach.segment_name,
                "upstream_gate_name": reach.upstream_gate_name,
                "target_gate_name": reach.target_gate_name,
                "boundary_name": reach.boundary_name,
                "used_observations": ",".join(update.used_observations),
            }
            for name in ("q_boundary", "h1", "h2", "gate_q", "gate_opening"):
                idx = STATE_INDEX[name]
                obs_value = raw_obs[name]
                forecast_value = (
                    float(forecast_mean[idx]) if forecast_available.get(name, True) else None
                )
                analysis_value = (
                    float(analysis_mean[idx]) if analysis_available.get(name, True) else None
                )
                row[f"{name}_obs"] = obs_value
                row[f"{name}_forecast"] = forecast_value
                row[f"{name}_analysis"] = analysis_value
                row[f"{name}_completed"] = _completed_value(obs_value, analysis_value)
                if obs_value is not None and analysis_value is not None:
                    validation_pairs[name].append((float(obs_value), analysis_value))
            row["ues_forecast"] = float(forecast_mean[STATE_INDEX["ues"]])
            row["ues_analysis"] = float(analysis_mean[STATE_INDEX["ues"]])
            row["uef_forecast"] = float(forecast_mean[STATE_INDEX["uef"]])
            row["uef_analysis"] = float(analysis_mean[STATE_INDEX["uef"]])
            upstream_q_forecast = (
                float(np.nanmean(forecast_upstream_q))
                if np.any(np.isfinite(forecast_upstream_q))
                else None
            )
            upstream_q_analysis = (
                float(np.nanmean(analysis_upstream_q))
                if np.any(np.isfinite(analysis_upstream_q))
                else None
            )
            target_q_forecast = row["gate_q_forecast"]
            target_q_analysis = row["gate_q_analysis"]
            row["upstream_gate_q_forecast"] = upstream_q_forecast
            row["upstream_gate_q_analysis"] = upstream_q_analysis
            row["net_gate_q_forecast"] = (
                upstream_q_forecast - target_q_forecast
                if upstream_q_forecast is not None and target_q_forecast is not None
                else None
            )
            row["net_gate_q_analysis"] = (
                upstream_q_analysis - target_q_analysis
                if upstream_q_analysis is not None and target_q_analysis is not None
                else None
            )
            rows.append(row)

        csv_path = reach_output_dir / "assimilated_series.csv"
        plot_path = reach_output_dir / "assimilation_result.png"
        summary_path = reach_output_dir / "summary.json"
        _write_rows_csv(csv_path, rows)
        enkf_plotting.write_reconstruction_png(
            plot_path,
            rows,
            segment_name=reach.segment_name,
        )
        summary = {
            "case_name": case_name,
            "case_path": str(source_case),
            "pool_id": reach.pool_id,
            "segment": reach.segment_name,
            "reach": asdict(reach),
            "steps": steps,
            "step_dt_sec": step_seconds,
            "ensemble_size": config.ensemble_size,
            "method": "DEnKF with member-wise hydraulic propagation and analysis replay",
            "state_variables": list(STATE_NAMES),
            "write_back_variables": list(CONTROL_NAMES),
            "diagnostic_variables": list(DIAGNOSTIC_NAMES),
            "rmse": {name: _rmse(pairs) for name, pairs in validation_pairs.items()},
            "observation_files": {
                key: str(value) if value is not None else None
                for key, value in asdict(resolved_files).items()
            },
            "output_csv": str(csv_path),
            "output_plot": str(plot_path),
            "output_summary": str(summary_path),
            "workdir": str(work_root) if work_root and config.keep_workdir else None,
            "gate_count": len(id_to_name),
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    finally:
        client = None
        if isolated_dll_copies:
            _release_isolated_clients(member_clients, isolated_dll_copies)
        if work_root is not None and not config.keep_workdir:
            shutil.rmtree(work_root, ignore_errors=True)
        if transient_state_root is not None and not config.keep_workdir:
            shutil.rmtree(transient_state_root, ignore_errors=True)


def run_case_enkf(
    case_name: str,
    case_path: str | Path,
    *,
    pool_id: int,
    target_gate_name: str,
    upstream_gate_name: str,
    control_action_name: str,
    control_model_name: str | None = None,
    pool_segment_name: str = "",
    output_root: str | Path | None = None,
    steps: int | None = None,
    max_obs_gap_min: int | None = None,
    sim_solver_type: str = "sediment",
    config_path: str | Path | None = None,
    observation_files: ObservationFiles | None = None,
    dll_path: str | Path | None = None,
    config: AssimilationConfig | None = None,
    **_: Any,
) -> dict[str, Any]:
    """兼容旧调用名称；内部仅执行单渠段 DEnKF 缺失数据同化。"""

    cfg = config or AssimilationConfig()
    if steps is not None:
        cfg.steps = int(steps)
    if max_obs_gap_min is not None:
        cfg.max_obs_gap_minutes = int(max_obs_gap_min)
    cfg.sim_solver_type = str(sim_solver_type)
    reach = ReachSpec(
        pool_id=pool_id,
        upstream_gate_name=upstream_gate_name,
        target_gate_name=target_gate_name,
        boundary_name=control_action_name,
        boundary_model_name=control_model_name or control_action_name,
        segment_name=pool_segment_name,
    )
    return run_reach_assimilation(
        case_name,
        case_path,
        reach,
        config=cfg,
        config_path=config_path,
        observation_files=observation_files,
        output_root=output_root,
        dll_path=dll_path,
    )


def _run_reach_worker(payload: Mapping[str, Any]) -> dict[str, Any]:
    reach = ReachSpec(**payload["reach"])
    config = AssimilationConfig(**payload["config"])
    files_payload = payload.get("observation_files")
    observation_files = (
        ObservationFiles(
            **{
                key: Path(value) if value is not None else None
                for key, value in files_payload.items()
            }
        )
        if files_payload
        else None
    )
    return run_reach_assimilation(
        payload["case_name"],
        payload["case_path"],
        reach,
        config=config,
        config_path=payload.get("config_path"),
        observation_files=observation_files,
        output_root=payload.get("output_root"),
        dll_path=payload.get("dll_path"),
    )


def run_case_reaches_parallel(
    case_name: str,
    case_path: str | Path,
    *,
    reach_specs: Sequence[ReachSpec] | None = None,
    reach_indices: Iterable[int] | None = None,
    config: AssimilationConfig | None = None,
    config_path: str | Path | None = None,
    observation_files: ObservationFiles | None = None,
    output_root: str | Path | None = None,
    dll_path: str | Path | None = None,
    max_workers: int | None = None,
    control_name_template: str = "d{pool_id}",
    steps: int | None = None,
    max_obs_gap_min: int | None = None,
    sim_solver_type: str | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """按渠段多进程并行运行缺失数据同化。"""

    cfg = config or AssimilationConfig()
    if steps is not None:
        cfg.steps = int(steps)
    if max_obs_gap_min is not None:
        cfg.max_obs_gap_minutes = int(max_obs_gap_min)
    if sim_solver_type is not None:
        cfg.sim_solver_type = str(sim_solver_type)
    cfg.validate()

    reaches = [reach.normalized() for reach in (reach_specs or discover_reaches(case_path))]
    if reach_indices is not None:
        selected = {int(index) for index in reach_indices}
        reaches = [reach for reach in reaches if reach.pool_id in selected]
    adjusted: list[ReachSpec] = []
    for reach in reaches:
        boundary_name = reach.boundary_name or control_name_template.format(
            pool_id=reach.pool_id, edge_id=reach.edge_id or ""
        )
        adjusted.append(
            ReachSpec(
                pool_id=reach.pool_id,
                upstream_gate_name=reach.upstream_gate_name,
                target_gate_name=reach.target_gate_name,
                boundary_name=boundary_name,
                boundary_model_name=reach.boundary_model_name or boundary_name,
                segment_name=reach.segment_name,
                edge_id=reach.edge_id,
                canal_name=reach.canal_name,
            ).normalized()
        )
    reaches = adjusted
    if not reaches:
        return []

    worker_count = max(1, min(len(reaches), int(max_workers or os.cpu_count() or 1)))
    files_payload = (
        {
            key: str(value) if value is not None else None
            for key, value in asdict(observation_files).items()
        }
        if observation_files
        else None
    )
    payloads = [
        {
            "case_name": case_name,
            "case_path": str(Path(case_path).resolve()),
            "reach": asdict(reach),
            "config": asdict(cfg),
            "config_path": str(Path(config_path).resolve()) if config_path else None,
            "observation_files": files_payload,
            "output_root": str(Path(output_root).resolve()) if output_root else None,
            "dll_path": str(Path(dll_path).resolve()) if dll_path else None,
        }
        for reach in reaches
    ]

    if worker_count == 1:
        results: list[dict[str, Any]] = []
        for payload in payloads:
            try:
                results.append(_run_reach_worker(payload))
            except Exception as exc:
                if cfg.fail_fast:
                    raise
                results.append(
                    {
                        "case_name": case_name,
                        "pool_id": payload["reach"]["pool_id"],
                        "segment": payload["reach"]["segment_name"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return sorted(results, key=lambda item: int(item.get("pool_id", -1)))

    results = []
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        future_map = {executor.submit(_run_reach_worker, payload): payload for payload in payloads}
        for future in as_completed(future_map):
            payload = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                if cfg.fail_fast:
                    for pending in future_map:
                        pending.cancel()
                    raise
                results.append(
                    {
                        "case_name": case_name,
                        "pool_id": payload["reach"]["pool_id"],
                        "segment": payload["reach"]["segment_name"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    return sorted(results, key=lambda item: int(item.get("pool_id", -1)))


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def _parse_indices(text: str | None) -> list[int] | None:
    if not text:
        return None
    return [int(item.strip()) for item in text.split(",") if item.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    direct_run = argv is None and len(sys.argv) == 1
    if direct_run:
        project_dir = TOOLS_DIR.parent
        case_path = project_dir / "data" / DIRECT_RUN_CASE_NAME
        from pid_reconstruction import PIDConfig, run_pid_reconstruction

        reach = discover_reaches(case_path)[DIRECT_RUN_REACH_INDEX]
        pid_parameters: dict[str, Any] = {
            "kp": DIRECT_RUN_PID_KP,
            "ki": DIRECT_RUN_PID_KI,
            "q_max_step": DIRECT_RUN_PID_Q_MAX_STEP,
            "adaptive_gains": False,
        }
        tuning_path = (
            case_path
            / "output"
            / "pid_nsga2_tuning"
            / str(reach.pool_id)
            / "tuning_summary.json"
        )
        parameter_source = "DIRECT_RUN_DEFAULT"
        if tuning_path.is_file():
            tuning = json.loads(tuning_path.read_text(encoding="utf-8"))
            selected = tuning.get("selected_parameters", {})
            tuned_values = {
                "kp": _finite_float(selected.get("kp")),
                "ki": _finite_float(selected.get("ki")),
                "q_max": _finite_float(selected.get("q_max")),
                "q_max_step": _finite_float(
                    tuning.get("config", {}).get("q_max_step")
                ),
            }
            if (
                not tuning.get("final_error")
                and all(value is not None for value in tuned_values.values())
                and tuned_values["kp"] >= 0.0
                and tuned_values["ki"] >= 0.0
                and tuned_values["q_max"] > 0.0
                and tuned_values["q_max_step"] > 0.0
            ):
                pid_parameters.update(tuned_values)
                pid_parameters["adaptive_gains"] = True
                parameter_source = str(tuning_path.resolve())
        print(
            f"PID_PARAMETERS pool={reach.pool_id} source={parameter_source} "
            f"kp={pid_parameters['kp']} ki={pid_parameters['ki']} "
            f"q_max={pid_parameters.get('q_max')} "
            f"q_max_step={pid_parameters['q_max_step']} "
            f"adaptive={pid_parameters['adaptive_gains']}"
        )
        result = run_pid_reconstruction(
            case_path,
            reach,
            config=PIDConfig(
                steps=DIRECT_RUN_STEPS,
                kp=pid_parameters["kp"],
                ki=pid_parameters["ki"],
                kd=DIRECT_RUN_PID_KD,
                integral_limit=DIRECT_RUN_PID_INTEGRAL_LIMIT,
                anti_windup_gain=DIRECT_RUN_PID_ANTI_WINDUP_GAIN,
                q_max=pid_parameters.get("q_max"),
                q_max_step=pid_parameters["q_max_step"],
                initial_water_depth=DIRECT_RUN_INITIAL_WATER_DEPTH,
                filter_flow_outliers=DIRECT_RUN_FILTER_FLOW_OUTLIERS,
                use_dll_flow_feedback=DIRECT_RUN_USE_DLL_FLOW_FEEDBACK,
                run_baseline=DIRECT_RUN_PID_RUN_BASELINE,
                adaptive_gains=pid_parameters["adaptive_gains"],
            ),
            output_root=case_path / "output" / "data_assimilation_direct_run",
            dll_path=TOOLS_DIR / "OcisMILPNet.dll",
        )
        print(f"RESULT_CSV={Path(result['output_csv']).resolve()}")
        print(f"RESULT_PLOT={Path(result['output_plot']).resolve()}")
        return 0

    parser = argparse.ArgumentParser(description="分渠段分水边界与节制闸缺失数据同化")
    parser.add_argument("case_path", type=Path)
    parser.add_argument("--case-name", default=None)
    parser.add_argument("--config-path", type=Path, default=None)
    parser.add_argument("--dll-path", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--reach-indices", default=None, help="逗号分隔，例如 0,2,3")
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument("--ensemble-size", type=int, default=24)
    parser.add_argument("--max-workers", type=int, default=None)
    parser.add_argument("--max-obs-gap-min", type=int, default=30)
    parser.add_argument(
        "--observation-match",
        choices=("nearest", "causal", "linear", "exact"),
        default="nearest",
    )
    parser.add_argument("--keep-workdir", action="store_true")
    args = parser.parse_args(argv)

    config = AssimilationConfig(
        ensemble_size=args.ensemble_size,
        steps=args.steps,
        max_obs_gap_minutes=args.max_obs_gap_min,
        observation_match=args.observation_match,
        keep_workdir=args.keep_workdir,
        initial_water_depth=DIRECT_RUN_INITIAL_WATER_DEPTH if direct_run else 2.0,
        assimilated_observations=("h1",) if direct_run else AssimilationConfig.assimilated_observations,
        initial_std_ues=0.0 if direct_run else 0.006,
        initial_std_uef=0.0 if direct_run else 0.006,
        process_std_ues=0.0 if direct_run else 0.002,
        process_std_uef=0.0 if direct_run else 0.002,
        anchor_q_to_observation=direct_run,
    )
    observation_files = None
    if direct_run:
        observation_files = ObservationFiles(gate_h1=args.case_path / "input" / "stage.csv")
    results = run_case_reaches_parallel(
        case_name=args.case_name or args.case_path.name,
        case_path=args.case_path,
        reach_indices=_parse_indices(args.reach_indices),
        config=config,
        config_path=args.config_path,
        observation_files=observation_files,
        output_root=args.output_root,
        dll_path=args.dll_path,
        max_workers=args.max_workers,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    for item in results:
        output_plot = item.get("output_plot")
        if output_plot:
            print(f"RESULT_PLOT={Path(output_plot).resolve()}")
    return 0 if all("error" not in item for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
