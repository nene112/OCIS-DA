---
name: boundary-reconstruction
description: >-
  为 1D 水动力 Roe 求解器案例重建渠段边界条件（分水流量 + 节制闸控制量）。
  运行逐渠段 PID 跟踪，采用两阶段控制：先仅调分水做二分搜索，分水无法把目标水位
  误差降到容差以内时启动节制闸（目标闸）调节，节制闸动作幅度上限按 10%→20%→30%→…→100%
  逐级放松，仍无法改善就继续放大。节制闸支持两种边界模式：flow（直接给闸门流量，
  如 sj_zonggan-d0）与 opening（给闸门开度 e，如 dayudu）。用于边界重建、渠段重建、
  分水/节制闸调节、生成 action_reconstruction.csv 或对比实测水深等场景。
---

# 边界重建（分水 + 节制闸分级调节）

## 何时使用

- 用户要求"边界重建"、"重建某渠段"、"把有数据的渠段都重建一遍"。
- 用户要求"调节节制闸 / 分水"、按幅度逐级放大动作上限。
- 用户要求生成或更新 `action_reconstruction.csv`、对比水深与实测水深。
- 关键词：分水、节制闸、目标闸、边界重建、渠段、流量跟踪、水位跟踪、开度。

## 背景与关键文件

本工程用 1D 水动力 Roe 求解器（`tools/OcisMILPNet.dll`，Python 包装 `tools/HD_Roe.py`）
分池段（pool）孤立仿真。边界控制通过两个 DLL 接口实现：

- `set_GatesFlow_byID_sim(gate_id, q)` —— 设置流量边界（分水、或 flow 模式节制闸）。
- `set_GatesFlow_e_byID_sim(gate_id, e)` —— 设置节制闸**开度**边界（opening 模式，
  单位米）。Roe 求解器按孔口/堰流公式 Q=Cd·b·e·√(2gΔH) 反算过闸流量，从而改变闸前水位。

### 两种节制闸边界模式

| 模式 | `check_gate_mode` | 控制量 | DLL 接口 | 代表案例 |
|---|---|---|---|---|
| 流量边界 | `"flow"` | 闸门流量 Q (m³/s) | `set_GatesFlow_byID_sim` | `sj_zonggan-d0` |
| 开度边界 | `"opening"` | 闸门开度 e (m) | `set_GatesFlow_e_byID_sim` | `dayudu` |

关键差异：flow 模式下直接给定节制闸流量；opening 模式下给开度，流量由孔口公式反算，
开度增大 → 过闸流量增大 → 闸前（上游）水位下降。`dayudu` 的多个节制闸
（老庄、圪塔、15号桥、富家岭、一级路南、刘原、阳院…）都是开度边界。

| 文件 | 作用 |
|---|---|
| `tools/GPT-Reconstruction/run_checkgate_reconstruction.py` | sj_zonggan-d0 主驱动：720 步，并行重建池 5/6/7/8 |
| `tools/GPT-Reconstruction/run_dayudu_reconstruction.py` | dayudu 主驱动：46 步，opening 模式，读 `gate_hole_counts.csv` 取每闸开度上限，输出完整边界表 |
| `tools/GPT-Reconstruction/pid_reconstruction.py` | 核心：`PIDConfig`、两阶段搜索 `_select_flow_and_gate_by_one_step_trials`、`_set_check_gate_control` 按模式派发 flow/opening |
| `tools/GPT-Reconstruction/write_checkgate_action_csv.py` | 生成统一边界文件 `action_reconstruction.csv` |
| `tools/GPT-Reconstruction/reach_missing_data_assimilation.py` | `da.*` 工具（观测加载、渠段发现、DLL 客户端等） |
| `tools/GPT-Reconstruction/parallel_pid_reconstruction.py` | 选段逻辑 `select_reaches_with_water_levels`、`write_action_reconstruction` |
| `data/sj_zonggan-d0/input/action_obs.csv` | 分水/闸门流量观测 |
| `data/sj_zonggan-d0/input/stage_obs_24.csv` | 目标闸水位观测 |
| `data/sj_zonggan-d0/input/action.csv` | 原始边界条件（重建文件的骨架） |
| `data/dayudu/input/stage1_td.csv` | dayudu 目标闸水位观测（GBK 编码） |
| `data/dayudu/input/gate_e_td.csv` | dayudu 闸门开度观测（**毫米 mm**） |
| `data/dayudu/input/gate_hole_counts.csv` | 每闸闸孔数 + 开度上限（mm/m） |

## 快速开始

在仓库根目录（`E:\code test\ocis-da`）执行：

```bash
# sj_zonggan-d0（flow 模式，约 40 分钟，4 进程并行）
python tools/GPT-Reconstruction/run_checkgate_reconstruction.py
python tools/GPT-Reconstruction/write_checkgate_action_csv.py

# dayudu（opening 模式，46 步）
python tools/GPT-Reconstruction/run_dayudu_reconstruction.py
```

Windows 下需要以**非沙箱**方式运行（DLL 为 ctypes 加载，依赖本机文件系统）。

## 两阶段控制逻辑（每一步）

`pid_reconstruction._select_flow_and_gate_by_one_step_trials` 对每个仿真步执行：

1. **第一阶段：仅调分水**。节制闸控制量保持当前值，分水流量在
   `[q_min, q_max]` 内二分搜索逼近目标水位 `target_h1`。
2. 若分水最优处误差 `error <= tolerance`（默认 `flow_tracking_tolerance`），
   直接采纳，节制闸不动。
3. **第二阶段：启动节制闸**。当 `error > tolerance` 且 `check_gate_enabled=True` 时：
   - 固定分水在最优值，对节制闸控制量在全量程 `[flow_min, flow_max]`（opening 模式
     为 `[opening_min, opening_max]`）扫一维网格；
   - 动作幅度上限从 `amplitude_start` 起步，每级在当前控制量 ± `amplitude × 全量程`
     窗口内选最接近目标的控制量；
   - **无法改善就放大**：`amplitude += amplitude_step` 继续放宽窗口，直到
     `error <= tolerance` 或 `amplitude > amplitude_max`。

## 节制闸启动条件与分级放松约束

**启动条件**：仅调分水（含二分搜索）后目标水位误差仍大于容差，即判定
"分水无法改善"，转入节制闸调节。

**分级放松**（`check_gate_amplitude_start/step/max`，默认 `0.10/0.10/1.0`）：

| 幅度上限档 | 窗口（相对当前节制闸控制量） | 何时进入 |
|---|---|---|
| 10% | ±10% × 全量程 | 默认起始 |
| 20% | ±20% | 上一档窗口内无法达标 |
| 30% … 100% | ±30% … ±100% | 每档仍无法达标就继续放宽 |

**配套约束**：

- flow 模式：`check_gate_flow_auto_range=True` 时，节制闸流量上界按目标闸实测最大
  流量 × `(1 + check_gate_flow_range_margin)` 自动扩界，各渠段独立，避免大流量渠段
  被钳制壅水。
- opening 模式：`check_gate_opening_max` 取自 `gate_hole_counts.csv` 每闸物理上限
  （单位换算见下）。
- 初始锚定：节制闸控制量初值优先取第 0 步实测值（flow 取 `gates_Q`，opening 取
  `gates_e`），再退回模型当前值；均不可得才用下限。不可退回 0，否则闸从第 0 步闭死。

## 关键配置（`PIDConfig` 中的节制闸字段）

```python
# —— flow 模式（sj_zonggan-d0）——
check_gate_mode = "flow"
check_gate_flow_min = 0.0
check_gate_flow_max = 30.0          # 兜底上界，auto_range 时会被实测最大值覆盖
check_gate_flow_auto_range = True
check_gate_flow_range_margin = 0.10

# —— opening 模式（dayudu）——
check_gate_mode = "opening"
check_gate_opening_min = 0.0
check_gate_opening_max = 1.5        # 米，来自 gate_hole_counts.csv 每闸上限
check_gate_opening_obs_unit = "mm"  # gate_e_td.csv 是毫米，内部换算成米
check_gate_opening_auto_range = False
check_gate_opening_range_margin = 0.10

# —— 两级通用 ——
check_gate_enabled = True
check_gate_amplitude_start = 0.10   # 幅度上限起始（10%）
check_gate_amplitude_step  = 0.10   # 每级递增步长（10%）
check_gate_amplitude_max   = 1.0    # 幅度上限上限（100%）
check_gate_search_points = 21        # 节制闸控制量一维网格点数
flow_tracking_tolerance = 0.02      # 容差（米），触发节制闸的阈值
```

## 输出文件

逐渠段（`data/<case>/output/pid_*_reconstruction/{pool_id}/`）：

- `assimilation_result.png` —— 三面板结果图：水位、分水流量、渠段净流量。
- `assimilated_series.csv` —— 逐步明细（含 `check_gate_selected_flow` /
  `check_gate_selected_opening`、`check_gate_amplitude_used`、`check_gate_adjusted`）。
- `summary.json` —— RMSE、闸门 ID、输出路径。

全局（`data/<case>/output/pid_*_reconstruction/`）：

- `opening_reconstruction_summary.json`（dayudu）/ `checkgate_summary.json`
  （sj_zonggan-d0）—— 渠段清单 + 各成功结果汇总。
- `action_reconstruction.csv` —— **统一完整边界文件**。dayudu 列为：
  `tm, 二级站出口(入流), d0~d10(分水), {各节制闸}_e_m, {各节制闸}_Q_m3s`；
  未重建闸（杨元坝/麻原/17号桥）保持默认开度。

## 关键坑点

- **模式选择**：flow 模式直接设闸门流量；opening 模式设开度，流量由孔口公式反算。
  分池段模式下 flow 案例开度 `set_GatesFlow_e_byID_sim` 对水位无效，必须直接设流量；
  opening 案例反过来，必须用开度接口，不能塞流量进去。
- **开度单位**：`gate_e_td.csv` 记录为毫米（mm），DLL 开度单位为米（m），
  通过 `check_gate_opening_obs_unit="mm"` + `_opening_to_m` 换算。
- **初始水深必须对齐实测**（dayudu 关键坑）：`run_pid_reconstruction` 内整池初始水深
  取 `targets[0]`（首步实测水位），而非默认 `config.initial_water_depth`。dayudu 实测
  ~1.3~1.75 m，若仍用默认 2.2 m 会令模拟水位长期偏高达 0.7 m（pool0 RMSE 0.70 m），
  对齐后 pool0 RMSE 降到 ~0.29 m。
- flow 模式固定节制闸流量上界（如 30）会把池 5/6/7（天然 66~120 m³/s）壅死，
  务必开 `check_gate_flow_auto_range`。
- 改动核心逻辑后，先跑单池小步验证（如单 `pool_id=0`、`steps=46`），
  确认 RMSE 正常再跑全量。
- 全量运行会产生大量 DLL 子进程与文件句柄，避免频繁轮询/打开结果文件，
  用 summary 里 `success_count/failed_count` 判断是否完成。
- `stage1_td.csv` 是 GBK 编码（`da.load_observation_csv` 已处理），不要用 UTF-8 强读。

## 维护说明

本 skill 在 `.cursor/skills/boundary-reconstruction/SKILL.md` 与
`.codex/skills/boundary-reconstruction/SKILL.md` 各保留一份且内容必须一致；
修改时两处同步更新。
