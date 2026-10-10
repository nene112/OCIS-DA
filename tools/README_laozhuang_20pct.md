# 老庄闸前渠段：分水与开度均不超过20%

```powershell
python tools/run_laozhuang_20pct.py
```

启用修复后的实测闸后水位边界：

```powershell
python tools/run_laozhuang_20pct.py --downstream-stage --output data/dayudu/output/laozhuang_20pct_tailwater
python tools/finalize_tailwater_validation.py
```

接口、绝对高程与缺测补齐说明见[README_tailwater_boundary.md](README_tailwater_boundary.md)。后处理检查最终DLL哈希、全时段累计水量残差和闸后边界实际执行，生成含尾水及此前结果的四面板图。

运行二级站出口至老庄节制闸（pool 0），2026-04-14 00:00至2026-04-21 00:00，共168周期，输出在`data/dayudu/output/laozhuang_20pct_updated_sections`。

分水基准是action_td.csv的d0，开度基准是gate_e_td.csv的老庄记录（mm转m）。采用±10%、±20%两级，相对每个时刻的原值计算，累计变化不得超过20%，原值0保持0。开度使用原测试的单孔记录口径；上游二级站出口采用action_obs_td.csv观测，保持不调节。

五周期平均有符号分水执行误差(Qset−Qexecuted)/Qexecuted超过2%才允许开度调节。执行量来自gates_Q_current，读取d0下游节点2079；不采用虚拟闸节点2078的Q，也不采用滞后一周期的gates_Q。未触发时使用当时原开度。水位验收固定为NRMSE<5%、NSE>0.9、R²>0.9、KGE>0.9，不放松。

每次候选试算恢复水力状态、闸门缓存及求解器时钟，最终选定后仅推进一次。运行中检查时钟、候选回放及两类20%约束，报告中保留分水设定/实际执行和缺测水位周期。基准和重建运行使用相同初始水深、最新补全断面文件和相同DLL。初始水深按起点老庄水位统一设置，不包含额外预热率定。

laozhuang_boundaries.csv区分上游流量、d0分水流量、老庄开度；baseline_audit.csv/reconstruction_audit.csv记录逐周期输入和执行；summary.json保留指标、最大实际调整比例及DLL/断面哈希，report.md及comparison.png用于查看结果。机械最大行程尚未核实，采用观测窗口最大开度×1.2作为数值上界，保证不静默改写原记录。

## 后续增加的预热入口

```powershell
python tools/run_laozhuang_warmup.py
python tools/plot_laozhuang_warmup.py
```

复用上一轮采用的分水边界做初态对比，不重新优化正式边界。最新方法为**PID预热**：每300秒以实测起点水深为目标调节临时上游入流，分水、开度、尾水保持起点值；统一初始水深设置不试配，不覆盖模拟水位。最低6小时、最多24小时，连续30分钟每个误差≤0.5 cm、水深极差≤0.25 cm及入流极差≤0.05 m³/s才停止。PID使用Kp=3、Ki=0.5、Kd=0.1，含积分抗饱和、入流限幅及速率限制；CLI可修改增益。预热为伪时间初态准备，未声明完全稳态。保存热态并切回正式入流、正式起点时钟与3600秒周期，重配尾水序列清零其累计量；重新验证六小时快照连续性。输出`data/dayudu/output/laozhuang_pid_warmup`，保留旧`laozhuang_warmup`初始水深试配结果。

如需在PID预热初态下重新做5%分水执行偏差扩大试验，运行`python tools/run_laozhuang_delivery_5pct.py --warmup-seconds 21600 --initial-tolerance-m 0.005`，输出另存`laozhuang_delivery_5pct_pid_warmup`。旧初态区间阈值不能直接作为新初态已验证的阈值。
