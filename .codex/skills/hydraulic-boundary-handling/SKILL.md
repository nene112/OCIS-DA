---
name: hydraulic-boundary-handling
description: 维护OCIS-DA水动力流量、开度和外部尾水边界的配置、周期状态恢复、守恒诊断及DLL验证；用于边界处理修改和渠段重建验证。
---

# 水动力边界处理

当前PID预热＋RK-MPC流程定位为**模型率定中的边界重建**。完整工作流由[OCIS-RK-MPC率定skill](../../../../skills/dayudu-calibration-validation/SKILL.md)维护；本skill负责真实边界接口、初态与时钟恢复、限幅触发、快照和守恒验证。不要将预测模型辨识等同于原生静态参数率定，或将历史回算等同于独立预测验证。

先读 `tools/README_tailwater_boundary.md` 获取JSON接口、时间序列与高程基准；重建老庄案例读 `tools/README_laozhuang_20pct.md`。绘图遵循相邻 `hydraulic-plotting/SKILL.md`。

## 当前实现与边界类型

- 用户选定native源码：`C:/Users/houvert/.codex/worktrees/aggregation-continue/ocismilpnet-mac-win-MAC2DELL`，编译目录`build_manual_no_waga`，目标`OcisMILPNetDll`。部署到项目`tools/OcisMILPNet.dll`，更新`tools/native_manual_no_waga_manifest.json`哈希。修改源码后不能只改Python而继续使用旧DLL。
- 实际已验证路径：`stepSolver_sim_Roe_only_pool` → `stepSolver_Roe_pool` → `step_tailwater_pool` → `SimGuass_sediment_pools`，solver为`sediment`。当前外部尾水接口每渠段支持一个尾水闸；其他求解路径不得直接宣称支持。
- 流量边界用`set_GatesFlow_byID_sim`，单位m³/s；开度用`set_GatesFlow_e_byID_sim`及必要的逐孔数组，单位m，dayudu原记录mm需转换。核对calculation_type，不能将开度当流量。
- 外部尾水用`set_GatesDownstreamStage_sim(config)`配置完整序列，不能只置`BC_STAGE_DOWN_LABEL=1`。水位传绝对高程，水深加对应闸后节点底高程；上下游水头统一减闸底高程，不混用上下游床高程。闸底实测与模型假设明确区分。
- 样本必须有限、严格递增，覆盖求解时刻且不超过配置的最大缺测间隔；线性插值，越界报错，非法导入保持原配置。首条观测向前补齐仅为显式假设，不标成实测。
- 闸关闭、等水头流量为零；反向流遵循allow_reverse。全开堰流保持体积流量量纲；流量系数和过渡平滑设置是否经率定须据实报告。
- 保留永久拓扑及节点索引。外部虚拟支路不计渠段蓄水，闸前实体单元保留；直接分水出口也用外部通量。物理界面两侧共享通量，蓄水使用实际面积变化，不以强制水位掩盖质量损失。
- 直接分水流量边界的设定需求必须独立保留；可用水量限制只影响执行通量和累计量，不能将被限流的执行量覆盖成后续子步设定值。限流、求解失败和实际执行偏差分别记录。

## 当前dayudu重建约束

- 水位验收固定：NRMSE<5%、NSE>0.9、R²>0.9、KGE>0.9；NRMSE为RMSE/观测范围。不得自行放宽。
- 第一轮先调分水流量：相对每个时刻原始已知流量，±10%再±20%，流量、开度累计偏离均不超过原值20%；原值零保持零，不以上一周期调节值重新锚定。用户指定已完成老庄结果为第一轮；第二轮仅在第一轮水深相对误差>10%的控制时段局部扩大分水范围，详读[分水重建精修规则](../boundary-reconstruction/SKILL.md)。第二轮具体幅度/上限使用明确配置，未指定前不自行确定；开度20%上限不变。
- 开度只有过去5个已完成交互周期的有符号平均执行误差`mean((Qset-Qexecuted)/Qexecuted)>2%`才允许调节，再按±10%、±20%分级。Qset和Qexecuted均零计零；正设定、零执行计无穷。不能仅因水位不达标就启动开度。
- 当前执行流量读取`gates_Q_current`，不要使用滞后一周期的gates_Q或虚拟闸节点流量。
- 每周期试算前保存完整状态；每个候选恢复该周期初态，包括求解时钟、闸门缓存、尾水序列和累计边界量；选定后恢复再推进一次。不得每周期重置到全程初态。

## 固定间隔快照及局部续算

### 本地 PID + RK-MPC 历史回算

用户要求老庄渠段增加PID预热后使用RK-MPC重建边界。跨项目入口为`OCIS-RK-MPC/experiments/dayudu_laozhuang_local_rk_mpc.py`，绘图入口`experiments/plot_laozhuang_local_rk_mpc.py`；输出本项目`data/dayudu/output/laozhuang_pid_rk_mpc`。同一热启动、断面、来水、尾水、系数下保留`pid_only_audit.csv`，不要把预热、静态率定和控制优化的影响混在一起。

本地适配器从原生节点水深/流量及RK4物理特征辨识EDMDc模型，四小时预测窗口滚动优化。模型用DLL模拟转移辨识，实测水深只作目标；当前使用历史未来目标且辨识覆盖回算工况，须标为离线回算，不能宣称独立预测验证。提出的控制序列经完整DLL多步核验，与既有序列比较后只执行第一步。每个候选恢复完整当前快照及求解时钟。

普通分水时段沿用已完成的20%档，既有精修时段沿用已接受的局部幅度，最大90%；本轮不扩大原有局部幅度，原值零仍零。开度解锁仍为过去五个完成周期平均有符号执行误差>2%，分级10%、20%，未解锁保持原始开度。新控制分支的6小时快照须重新生成并全状态重演验证。输出学习模型、逐步预测/优化审计、边界CSV及尾水JSON。

已完成168小时本地PID+RK-MPC回算：355组原生模拟转移辨识，1332次原生预测窗口步进，120周期采用RK提议，168次优化均报告成功；单步含偏差修正预测RMSE 0.077194 m。与同一PID初态对照相比，水深RMSE 0.193579→0.178875 m（降低7.6%），NRMSE 16.3773%→15.1333%，NSE 0.482890→0.558464；但R² 0.689899→0.674343、KGE 0.802798→0.797122略降，固定四项验收未通过，不能称全面精度提升。分水上限/下限分别命中56/103周期（零流量可同时命中），最大执行误差0，开度调整0周期。28个六小时快照块完整状态相等，累计守恒残差0.0348001 m³。结果为离线候选，原PID对照留存，不覆盖以前采用边界。

### 预热与正式初态

用户最新要求：预热必须用PID将模拟水位调到实测起点水位，取代上版初始水深试配。入口`python tools/run_laozhuang_warmup.py`，默认另存`laozhuang_pid_warmup`；同一既有90%档分水边界下仅比较初态，正式边界及系数不重新调节。复用项目PIDController，控制量为**仅预热阶段的上游入流**，分水、开度和尾水保持起点值；初始水深按原起点实测设置一次，不二分试配，也不在预热末覆盖水位。每300秒交互，最低预热6小时、最多24小时，未收敛或求解失败则保留记录并禁止进入正式时段。预热是伪时间初态准备，不冒充实测历史或完全稳态。

PID停止条件须同时满足：最近连续30分钟每个采样误差≤0.005 m，水深极差≤0.0025 m，入流设定极差≤0.05 m³/s；不能只凭一次命中目标停止。当前Kp=3、Ki=0.5、Kd=0.1，积分/微分以小时计，积分限幅2 m·h，抗饱和反馈0.2。老庄入流数值限幅0—5.25 m³/s、每300秒最多变化0.2625 m³/s；这些为预热控制设置，不是实测设备能力。所有参数可通过run(...,warmup={...})配置，入口提供kp/ki/kd及最长时长参数。临时入流范围不改正式边界的限幅规则。

`tools/hydraulic_warmup.py`记录连续PID演化，保存完整热态，使用`set_time_param`和`set_delta_t`切回正式起点与3600秒交互，同时将上游入流设定切回正式值；验证切换前后全节点h/Q不变、求解时钟归零，重新配置正式尾水序列并清零尾水累计量。预热PID不继续参与正式重建；正式五周期反馈从正式周期重新累计。预热不纳入正式评分；其他非零直接分水的累计量不能未经核实就宣称已清零。`set_delta_t`原生签名是int参数、void返回，Python不得声明成double/int。正式六小时快照在预热之后重新生成，验证28块续算与连续完整状态一致；旧冷启动快照不能复用。

`tools/run_fujialing_reconstruction_comparison.py::run(...,warmup={...})`提供公共入口；`python tools/run_laozhuang_delivery_5pct.py --warmup-seconds 21600 --initial-tolerance-m 0.005`可在PID预热初态下重新做局部分水扩大试验，默认另存`laozhuang_delivery_5pct_pid_warmup`，不得将旧初态阈值结果冒充新结果。绘图入口`tools/plot_laozhuang_warmup.py`默认读取`laozhuang_pid_warmup`，保留warmup_summary.json、完整warmup_trace.csv、warmup_selected_trace.csv及formal_initial_state.csv。

分水扩大入口默认启用PID预热，`--no-warmup`仅供历史冷启动复现。历史初始水深试配结果保留在`laozhuang_warmup`：1.611135 m及RMSE 0.194052 m属于旧方法，不得描述为PID结果；备份实现为tools/native_backups/hydraulic_warmup_initial_depth_shooting.py。新PID结果及快照一致性必须重新运行验证；固定边界初态对比不代表已经重新跑完PID初态的5%扩大试验。

已完成老庄PID预热验证：实际21小时10分钟（254步），预热末1.606708 m，目标1.610000 m；过去30分钟最大误差4.849 mm、水深极差1.557 mm、入流极差0.003663 m³/s，三项稳定条件通过。PID末入流2.535148 m³/s，正式起点切回2.625 m³/s。随后固定既有边界重算168小时，RMSE 0.193579 m、NRMSE 16.3773%、NSE 0.482890、R² 0.689899、KGE 0.802798，固定四项验收仍未通过；28个六小时快照块完整状态一致，正式累计水量残差0.0159107 m³。输出`laozhuang_pid_warmup`。

当前实现每6个交互周期（6小时）保存一个命名快照；间隔是本次实现选择，可配置。`save_states('periodic_36')`保存、`set_states('periodic_36')`恢复，同一DLL实例可同时持有多份，空名称保留原周期试算快照语义。原生快照包含完整节点、闸门、时钟、尾水序列、逐孔开度、蓄水及输出缓存；断面表指针仍由同一solver持有。

这些是**对象内存快照**，不是跨进程文件快照。`snapshot_index.json`仅为索引，不是可加载的完整状态；DLL实例销毁后不能恢复该实例的快照，禁止宣称支持重启后恢复。需要跨进程复用时须另行实现带版本/输入哈希检查和断面指针重绑定的序列化，并进行跨进程连续性验证。

精修从最早修改控制时刻之前最近的有效快照开始；快照之前的所有控制历史必须一致。修改区间后，旧后缀快照失效；接受候选后建立新分支快照，再用于下一档。不能用已改变前置控制之后的旧快照跳过其水动力影响。

运行`python tools/run_laozhuang_snapshot_refinement.py`：先按此前第二轮控制连续重演，按6周期保存快照；逐块恢复并用相同控制重演，比较周期水深、分水执行及完整原生节点/闸门状态、时钟、尾水和逐孔开度映射；当前案例要求全结构相等、数值误差<1e-9。失败则不进入精修。检验28个六周期块，而不是只比较重启瞬间。

前轮配置为前置5周期、分水30%→40%→50%。最新用户要求继续扩大，直到原区间平均有符号执行误差>5%；本轮从60%起按1.5倍扩大允许幅度，不沿用50%上限。开度触发仍为过去五周期2%，本轮开度/上游/尾水冻结，过流系数固定上一轮候选1.05倍。运行`tools/run_laozhuang_delivery_5pct.py`。直接试算增大需求，逐档保存试探和失败记录；超过5%的试探不作为最终采用边界，保留全程水深指标更好的稳定候选。求解失败不能当作达到5%，无法稳定继续时明确报告尚未达到。各档从控制历史一致的最近有效快照续算到期末。

## 修改后的验证与交付

过流系数试验使用`set_GatesFlow_mu_byID_sim_multi(gate_id, {'Uef':..., 'Ues':..., 'Ucf':..., 'Ucs':...})`；单值接口`set_GatesFlow_mu_byID_sim`只改Ues，不能认为它修改全部流态系数。Uef/Ues为自由/淹没孔流系数，Ucf/Ucs为全开自由/淹没流系数，实际含义以`src/tailwater_gate.h`为准。

试系数时冻结分水、开度、尾水、断面及初态，各组整段固定参数并从相同起点重演，回读系数确认生效；原系数组须复现已有结果。全时段系数改变意味着旧状态历史不再相同，不得直接用旧中途快照开始率定。`tools/run_laozhuang_coefficient_trials.py`粗扫，`--refine`补试细档；结果在`laozhuang_gate_coefficient_trials`。水深拟合最优系数是候选参数，未经独立过闸流量验证不能声称已完成物理率定；不自动覆盖默认系数。

按修改范围运行：native `cross_section_solver_test`（含尾水测试）、`python tools/test_tailwater_boundary.py`、`python test_native_cycle_checkpoint.py --cross-section data/dayudu/input/CrossSection_dayudu_hydraulic_tables.geojson`。检查关闭、等水头、高程平移、逆流、矩形/U形、断面突变/汇接、守恒和A/B/A回滚；保留失败证据，修复后再运行。

完整案例：`python tools/run_laozhuang_20pct.py --downstream-stage --output data/dayudu/output/laozhuang_20pct_tailwater`，随后`python tools/finalize_tailwater_validation.py`。后处理检查实际尾水与输入插值、DLL哈希及全程累计水量残差（当前案例要求绝对值<0.1m³；其他案例同时报告相对误差和采用阈值）。

交付控制边界CSV、完整尾水JSON、逐周期审计、指标和对比图。结果未满足四项指标必须标“候选，未通过”，不得用运行成功代替率定成功。维护本skill时同步真实接口与测试范围，不保留与用户新约束矛盾的旧规则。
