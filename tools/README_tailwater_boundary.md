# 分段求解下游闸门水位边界

适用：`sediment` 的 `stepSolver_sim_Roe_only_pool`，每个渠段一个外部尾水闸门。通过 `set_GatesDownstreamStage_sim(config)` 配置，不能仅置旧标志位而不提供时间序列。

```python
client.set_GatesDownstreamStage_sim({
    'gate_id': 1, 'enabled': True,
    'sill_elevation_m': 521.0995,
    'allow_reverse': True, 'max_gap_seconds': 21600,
    'samples': [
        {'time': 1776096000, 'water_level_m': 521.82575},
        {'time': 1776099600, 'water_level_m': 521.82575},
    ]
})
```

水位是绝对高程，不能直接传水深。闸前、闸后均相对同一闸底高程计算水头；关闭或两侧等水头时流量为零，允许反向流。全开自由流量补足水头因子，保证体积流量量纲。淹没比例0.65—0.75、开度/水头0.9—1.0采用平滑过渡；这些数值为数值正则化设置，流量系数仍需要历史数据率定。

样本按秒时间戳严格递增，线性插值，超出覆盖时间或最大缺测间隔报错；非法导入不修改已配置边界。保存/恢复状态同时包含时间序列、时钟、边界累计量及闸门状态。

闸后外部虚拟支路不计入渠段蓄水；闸前实体单元保留，使用闸门水头关系计算外部通量。已知流量分水出口同样处理。每个物理界面两侧使用相同通量，连续方程采用实际断面面积变化，报告包含单步与累计水量残差。永久节点连接和闸门索引保持不变。

验证命令：

```powershell
python tools/test_tailwater_boundary.py
python tools/run_laozhuang_20pct.py --downstream-stage --output data/dayudu/output/laozhuang_20pct_tailwater
```

老庄4月14日零时至首条闸后观测15:32:01缺少记录，使用首条值向前补齐，每小时生成一个点，数量和补齐时段记录在summary的downstream_boundary中；不是实测资料。后续使用原闸后水深加原模型闸后底高程。该假设及高程基准需要与现场资料核实，结果报告不放宽水位验收条件。
