"""Gate coefficient comparison, retaining four-panel layout and secondary error bars."""
import json
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from run_fujialing_reconstruction_comparison import ROOT,da
out=ROOT/'data/dayudu/output/laozhuang_gate_coefficient_trials'
s=json.loads((out/'summary.json').read_text(encoding='utf-8'));scale=s['best']['scale']
old=pd.read_csv(out/'scale_1.00/reconstruction_audit.csv');d=pd.read_csv(out/f'scale_{scale:.2f}/reconstruction_audit.csv')
t=pd.to_datetime(d.state_time);ct=pd.to_datetime(d.time)
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei'],'axes.unicode_minus':False})
fig,axes=plt.subplots(4,1,figsize=(13,12),sharex=True)
for panel,file,column,offset in [(0,'stage1_td.csv','h_sim_m',0),(1,'stage2_td.csv','downstream_wh_m',s['details']['1.0']['downstream_boundary']['downstream_bed_m'])]:
 series=da.load_observation_csv(ROOT/'data/dayudu/input'/file)['老庄节制闸']
 points=[(tm,h+offset) for tm,h in series.values if ct.iloc[0]<=tm<=t.iloc[-1]]
 axes[panel].scatter([v[0] for v in points],[v[1] for v in points],color='red',s=10,label='实测',zorder=5)
 axes[panel].plot(t,old[column],label='原过流系数');axes[panel].plot(t,d[column],label=f'系数×{scale:g}')
axes[0].set_ylabel('闸前水深 / m');axes[1].set_ylabel('闸后高程 / m')
error=axes[0].twinx();relative=abs(d.h_sim_m-d.h_target_m)/abs(d.h_target_m)*100
error.bar(t,relative,width=.006,color='gray',alpha=.35,linewidth=0,label='水深相对误差');error.set_ylabel('水深相对误差 / %');error.set_ylim(0,max(20,relative.max()*1.4));error.legend(loc='upper right')
axes[2].plot(ct,d.q_command_m3s,label='固定分水设定');axes[2].plot(ct,d.q_executed_m3s,'--',label='分水实际执行');axes[2].set_ylabel('分水 / m³/s')
axes[3].scatter(ct,d.opening_observed_m,color='red',s=14,label='原开度观测采样',zorder=5);axes[3].plot(ct,d.opening_command_m,'--',label='固定开度');axes[3].set_ylabel('开度 / m')
for ax in axes:ax.legend(ncol=3);ax.grid(alpha=.2)
fig.suptitle('老庄过流系数试验：分水、开度和尾水固定');fig.tight_layout();fig.savefig(out/'comparison.png',dpi=160);plt.close(fig)
table=pd.read_csv(out/'coefficient_sweep.csv')
lines='\n'.join(f'|{r.scale:g}|{r.RMSE_m:.4f}|{r.NRMSE:.2%}|{r.NSE:.4f}|{r.R2:.4f}|{r.KGE:.4f}|' for r in table.itertuples())
report=f'''# 老庄过流系数敏感性试验

固定最近快照精修得到的上游、分水、开度和尾水边界，相同初态及断面。四个原系数统一乘以{s['tested_scales']}，各组整段固定；没有逐周期追踪修改系数。原系数为{s['original_coefficients']}。

|倍数|RMSE/m|NRMSE|NSE|R²|KGE|
|---|---:|---:|---:|---:|---:|
{lines}

最低全程RMSE的倍率为{scale:g}；对应系数为Uef={s['best']['Uef']},Ues={s['best']['Ues']},Ucf={s['best']['Ucf']},Ucs={s['best']['Ucs']}。固定水深验收通过：{s['best']['passed']}。

1倍系数逐周期水深与此前结果一致至1e-9；所有组检查分水、开度及尾水保持一致，累计水量残差<0.1m³。各组完整审计在scale_*子目录。没有覆盖原案例或将候选参数自动写入默认配置。

这属于水深拟合敏感性试验，尚无独立过闸流量资料验证。不能据此将系数认定为完成物理率定，闸孔宽度、高程或观测时序误差也可能被系数补偿。
'''
(out/'report.md').write_text(report,encoding='utf-8');print(lines)
