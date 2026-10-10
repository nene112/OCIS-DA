---
name: hydraulic-plotting
description: 为本项目绘制实测与仿真水位、流量、开度对比图，统一观测点样式，区分实测、补齐和控制边界。
---

# 水动力绘图约定

用户明确要求：**实测值始终使用红色离散点，不用连线表示实测。** 后续绘图默认沿用，除非用户明确改变要求。

- Matplotlib 使用 `ax.scatter(t, observed, color='red', s=14, label='实测…', zorder=5)`；或者仅 marker 的 `plot`，必须 `linestyle='None'`。不要把实测画成黑色线或红色连线。
- 仿真值、重建值用其他颜色的连续曲线；控制设定与执行曲线注明含义，不能标成实测。原开度/流量记录若属于实测，同样画红色点。
- 保留已有水深、尾水、分水和开度对比布局。水深相对误差放在**水深曲线图副轴**，用细条形图表示，不另设误差子图，不改成误差曲线。条形图用低透明度、窄宽度，不能遮住实测点和水深曲线。
- 原始实测时间戳保留；用于评分的插值值须标注为“观测插值”，不得伪装成原始采样点。缺测补齐值单独说明，不画成实测红点。
- 时间戳转换统一时区。Windows本项目原始时间为Asia/Shanghai；不要混用naive pandas Timestamp的UTC timestamp与Python本地datetime.timestamp。
- 标明水深/绝对水位、m/mm及m³/s；限幅带相对当时原控制值绘制。未通过验收的结果明确标为候选。
- 修改图后重新生成并查看图片，检查点样式、图例、坐标及时间范围；交付图片和生成脚本路径。

当前老庄图入口：`tools/run_laozhuang_20pct.py`、`tools/finalize_tailwater_validation.py`。本skill的规范也适用于项目其他新建或修改的科学对比图。
