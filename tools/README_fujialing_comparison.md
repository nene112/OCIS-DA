# 富家岭闸前渠段重建与历史关系对比

运行范围是 pool 3，15号桥节制闸至富家岭节制闸，2026-04-14 00:00 至 2026-04-21 00:00，共168个一小时交互周期。每次候选控制试算恢复完整周期初状态及求解器时钟，选定后只推进一次。

```powershell
python tools/extract_fujialing_history.py
python tools/run_fujialing_reconstruction_comparison.py --hours 168
python tools/compare_fujialing_relations.py
```

输出目录：`data/dayudu/output/fujialing_reconstruction_comparison`。

- `fujialing_boundaries.csv`：上游15号桥流量边界、d3分水流量边界、富家岭开度边界，单位分别为m³/s、m³/s、m。
- `baseline_audit.csv` / `reconstruction_audit.csv`：每周期的输入、范围、执行值、开度触发条件、周期末水位/流量及恢复后的时钟。
- `historical_model_comparison.csv`：每条历史有效记录的两孔开度、闸前/后水位、历史计算流量和同工况DLL计算流量，保留原工作表行号。
- `simulation_relation.csv`：重建仿真水位—等效开度—流量轨迹。
- `reconstruction_summary.json`：水位验收与DLL哈希；`relation_comparison_summary.json`：历史关系对比；`report.md`：结果说明。

水位验收固定为NRMSE<5%、NSE>0.9、R²>0.9、KGE>0.9。NRMSE按观测极差归一化。分水以富家岭提水观测为基准，从±10%开始逐级放松；模型中该提水对应d3。开度仅在过去五个已完成周期的有符号平均执行偏差(Qset−Qexecuted)/Qexecuted超过2%时允许调节，并分级限幅。上游流量使用现有action文件，未获得独立上游观测。

2025历史文件有两孔独立开度，按两孔容量分别计算后求和；2026文件只有总开度，使用单孔等效开度，不能据此推定两孔实际分配。历史流量列是“计算闸门过流量”，不是独立实测；“二级出水口流量”也不是富家岭闸流量。历史水位相对闸底的基准尚未记录，当前按水深解释。负开度记录剔除，保留源值及有效性标记。

本轮DLL修复：闸门公式使用闸门净宽Bwid而不是圆底断面的零底宽；开度更新使用闸门拓扑上下游节点；新增gates_Q_current获取当前周期执行流量。未利用历史表改变过流系数。当前全开自由出流分支公式尚有长度维度问题，保留在此次对比中，不把历史表对比当作独立实测验证。

仿真已经加载断面查表JSON；底高程保留原模型。富家岭所处DYD016断面的顶部宽度与给定半径、边坡存在不一致，目前按半径和边坡构形，原数据告警仍保留。开度上限0.38m沿用2026配置，并非已核实的机械最大行程；2025单孔开度可达1.6m。
