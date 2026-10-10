---
name: boundary-reconstruction
description: >-
  为 1D 水动力 Roe 求解器案例重建渠段边界条件（分水流量 + 节制闸控制量）。
  运行逐渠段跟踪，采用分水优先、开度条件触发的分级控制。
  dayudu第一轮采用原值±10%、±20%限幅；第二轮按水深相对误差超过10%的时段局部扩大分水范围精修。
  节制闸支持两种边界模式：flow（直接给闸门流量，
  如 sj_zonggan-d0）与 opening（给闸门开度 e，如 dayudu）。用于边界重建、渠段重建、
  分水/节制闸调节、生成 action_reconstruction.csv 或对比实测水深等场景。
---

# 边界重建（分水 + 节制闸分级调节）

## 工作定位：模型率定的边界重建

用户明确当前PID预热＋RK-MPC流程属于模型率定工作，主要重建历史边界。完整流程维护在[OCIS-RK-MPC率定skill](../../../../skills/dayudu-calibration-validation/SKILL.md)：冻结模型与原始边界 → PID准备热态 → 恢复正式时钟/实测来水并停止PID → 同热态对照 → DLL转移辨识RK-Koopman → RK-MPC多步核验与逐步重建 → 六小时快照验证 → 四项评分及文件交付。本skill继续维护分水精修规则，真实接口与预热条件由相邻边界处理skill维护。

实测水深为拟合目标，不作为闸前强制水位边界；重建流量/开度为率定候选，不冒充实测。静态断面、糙率、床高程和过流系数在边界重建期间冻结。使用未来历史目标为离线率定回算，不宣称独立预测验证；四项验收未通过时不能仅以RMSE改善宣布率定成功。已有20%首轮、局部扩大、2%开度触发与5%扩大停止规则仍按下文执行。

## 当前dayudu规则优先

用户已要求**PID预热**，取代初始水深试配。后续老庄正式重算先按[边界条件处理skill的PID预热规则](../hydraulic-boundary-handling/SKILL.md)准备初态：每300秒用PID调节临时上游入流，起始分水/开度/尾水固定；最低6小时、最多24小时，连续30分钟水深误差≤0.5 cm、水深极差≤0.25 cm且入流极差≤0.05 m³/s才进入正式时段。保持初始水深原设置不试配、不覆盖预热末水位；随后切回正式入流，PID不用于替代正式重建规则。`run_laozhuang_delivery_5pct.py`默认启用PID预热并另存`laozhuang_delivery_5pct_pid_warmup`；`--no-warmup`仅用于历史冷启动复现。每次更改初态均重新生成并验证快照，旧初态5%阈值须重新计算。

先读取[边界条件处理skill](../hydraulic-boundary-handling/SKILL.md)和[绘图skill](../hydraulic-plotting/SKILL.md)。当前水位验收固定为NRMSE<5%、NSE/R²/KGE均>0.9；第一轮流量与开度相对当时原值±10%到±20%。第二轮仅按下述局部精修规则扩大分水范围，开度仍不超过20%，过去5周期平均有符号分水执行误差>2%才可启动开度。实测一律红色离散点，无连线。

## 第一轮结果与第二轮分水精修

用户指定刚完成的`data/dayudu/output/laozhuang_20pct_tailwater`为第一轮。冻结其`reconstruction_audit.csv`、`laozhuang_boundaries.csv`、`downstream_stage_payload.json`及`summary.json`；保留DLL/断面哈希、初态、时间轴、观测处理和缺测假设，不能覆盖第一轮文件。

1. 根据第一轮每个有有效观测的周期计算水深相对误差`r_h=abs(h_sim-h_obs)/abs(h_obs)`。这是水深拟合误差，不是开度触发使用的分水执行误差。阈值严格为`r_h>0.10`，等于10%不触发。h_obs=0且h_sim=0计0，h_obs=0且h_sim不为0计无穷；缺测/非有限值不用于自动扩大范围。
2. 以审计`state_time`的周期末水深判定，映射到对应`time`至`state_time`的控制区间；将连续触发周期合并为局部精修时段。不能直接把周期末时间当成边界动作开始时间。误差未超阈值的时段维持第一轮分水控制，不全时段扩大。
3. 第二轮以第一轮已重建分水为候选初值，在触发时段增大分水允许调节范围。所有新范围依然以该时刻**原始已知分水流量Q0**为锚，不以第一轮调节值重复叠加百分比。允许范围为`[max(0,(1-a)*Q0),(1+a)*Q0]`（当前非负分水口径）；Q0=0仍保持0。
4. 局部扩大幅度用显式配置`refinement_flow_tiers`和`refinement_flow_max`，各档必须大于第一轮20%且不超过配置上限。本次用户要求启动计算但未指定具体扩大上限，已向用户说明采用最小新增档±30%，当前老庄第二轮配置为`refinement_flow_tiers=[0.30]`、`refinement_flow_max=0.30`。这是本次实施选择，不是用户明确给出的数值，不再自行增至40%或更大。第一轮20%硬限幅仅在这些触发时段被第二轮规则取代。
5. 第二轮精修对象是分水流量。默认保留第一轮开度、上游流量、尾水边界及断面/系数；如另行调开度，仍须满足五周期有符号执行误差>2%的条件和原值20%限幅。不能用水深误差>10%直接启动开度。
6. 每个候选恢复该交互周期完整初态和时钟，选择后恢复并只推进一次。第二轮从第一轮相同的全程初态重演整个时段，携带前面精修后的真实状态推进；不得逐个异常窗口独立冷启动或沿用第一轮窗口末态假装连续。
7. 比较第一轮/第二轮的全程四项指标及局部误差；记录每周期原Q0、第一轮Q1、第二轮Q2、r_h、精修触发、幅度档和执行量，检查非触发区间Q2=Q1、局部锚定限幅、完整回滚及水量守恒。局部动作会影响后续水深，未修改的时段也需重新评分。无改善则保留第一轮候选，禁止为贴合水深擅自扩大其他边界或放宽验收。

第二轮输出到独立目录，交付边界CSV、尾水JSON、精修时段表、逐周期审计、两轮指标对比和图。绘图实测红色离散点，第一轮/第二轮不同颜色曲线；标出局部精修时段。新增规则是工作流要求，未执行第二轮时不能称为已验证或已跑完。

### 后续精修：偏大区间与前置调节

最新用户指令：继续扩大大误差部分的分水范围，直到区间平均设定/模拟分水相对误差**>5%**；此5%覆盖本节下方旧的2%流量扩大停止阈值，**不改变开度触发使用的过去五周期2%**。不再沿用先前50%上限。当前试验固定候选过流系数Uef/Ues=0.63、Ucf=0.315、Ucs=0.84，窗口前5周期及原始流量锚不变，从60%起，允许幅度每档乘1.5继续搜索。运行`tools/run_laozhuang_delivery_5pct.py`。

每档记录原区间内`mean((Qset-Qexecuted)/Qexecuted)`，严格>0.05才标记执行阈值触发。设定需求不得因限流被覆盖成执行量；失败试算必须恢复完整状态，不能把求解失败当作>5%。连续扩大仍无改善或遇到无法稳定计算的控制，保留稳定候选及失败证据，明确报告阈值尚未达到，而不是伪造达到条件。

本轮已完成六档60%、90%、135%、202.5%、303.75%、455.625%。四个原区间均首次测到>5%：周期41—49在303.75%档为32.20%，57—73在202.5%档为17.20%，102—105和126—131在455.625%档分别为34.07%、5.19%；无失败试算。这里执行流量为各小时周期末值，不是子步积分量。阈值试探单独留档，最终保留水深RMSE较优的90%档候选，RMSE 0.196863 m、NRMSE 16.6551%，四项水深验收未通过。文件位于`data/dayudu/output/laozhuang_delivery_5pct`；`laozhuang_boundaries.csv`为保留边界，`execution_limit_summary.csv`及`tier_*_audit.csv`为试探证据，不得混用。

用户最新规则优先于上面仅扩大原区间的第二轮方案：当精修区间内模拟水深偏大，且区间水深未满足固定精度指标时，尝试同时扩大该区间内及区间前的分水调节范围，以提前降低后续水深。保留相同原始流量锚定、全程连续重演和完整周期试算回滚。

以区间内有效观测的平均有符号水深偏差>0判定区间整体偏大，同时报告偏大周期数量，不能把偏低区间也自动加入。区间四项精度指标仍为NRMSE<5%、NSE/R²/KGE>0.9；样本不足或观测无变化导致指标无法计算时单独标注，不把其当作验证通过。

每一档完成区间及前置周期的重演后，计算**原精修区间内**的平均有符号分水执行相对误差`mean((Qset-Qexecuted)/Qexecuted)`。当该值严格>2%时，停止扩大流量调节范围，记录“分水执行能力不足”；不要继续无限增大。该区间均值不是过去五周期均值，水深相对误差也不是此执行误差。零流量规则沿用前述处理。

水深满足精度要求时也停止扩大；无有效执行样本不判为已达到2%。上限达到仍未触发2%时标注“到达配置上限”，不能假称执行能力不足。用户要求重算且未指定前置长度和新上限，本次已告知采用前5周期、30%→40%→50%最大50%的实施配置。重叠前置窗口取并集，保留原区间用于水深评分和执行误差判定。使用`tools/run_laozhuang_snapshot_refinement.py`；快照与连续性规则见边界处理skill，每6周期保存命名内存快照，必须通过续算与连续模拟的效果一致验证，再开始精修。

图保持已有对比布局，水深相对误差放水深图副轴，细条形图表示，不能另开误差子图。每档记录原区间、前置控制区间、限幅、区间精度、执行均值和停止原因。

下文旧PID驱动的100%全量程放松、水位误差直接触发开度和旧默认容差属于历史实现说明，**不适用于当前dayudu重建**。使用当前`tools/run_laozhuang_20pct.py`及边界处理skill指定的验证路径，不能沿用旧默认值。

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
