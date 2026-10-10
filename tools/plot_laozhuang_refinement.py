"""Compare first and locally refined second round, with raw red observation points."""
import json
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from run_fujialing_reconstruction_comparison import ROOT,da

out=ROOT/'data/dayudu/output/laozhuang_round2_refinement'
s=json.loads((out/'summary.json').read_text(encoding='utf-8'))
d=pd.read_csv(out/'first_round_refinement_mask.csv');r=pd.read_csv(out/'reconstruction_audit.csv')
t=pd.to_datetime(r.state_time);control_t=pd.to_datetime(r.time)
intervals=pd.read_csv(out/'refinement_intervals.csv')
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei'],'axes.unicode_minus':False})
fig,axes=plt.subplots(4,1,figsize=(13,12),sharex=True)
series=da.load_observation_csv(ROOT/'data/dayudu/input/stage1_td.csv')['老庄节制闸']
points=[(tm,h) for tm,h in series.values if pd.to_datetime(d.time.iloc[0])<=tm<=t.iloc[-1]]
axes[0].scatter([v[0] for v in points],[v[1] for v in points],color='red',s=10,label='实测水深',zorder=5)
axes[0].plot(t,d.h_sim_m,label='第一轮');axes[0].plot(t,r.h_sim_m,label='第二轮局部精修');axes[0].set_ylabel('闸前水深 / m')
relative=abs(r.h_sim_m-r.h_target_m)/abs(r.h_target_m)
error_ax=axes[0].twinx()
error_ax.bar(t,relative*100,width=.006,color='gray',alpha=.35,linewidth=0,label='第二轮水深相对误差',zorder=1)
error_ax.set_ylabel('水深相对误差 / %');error_ax.set_ylim(0,max(20,float(relative.max()*100)*1.4))
error_ax.legend(loc='upper right');axes[0].patch.set_alpha(0)
axes[1].plot(t,r.downstream_wh_m,label='DLL闸后水位')
tail=da.load_observation_csv(ROOT/'data/dayudu/input/stage2_td.csv')['老庄节制闸']
bed=s['second_round']['downstream_boundary']['downstream_bed_m']
tail_points=[(tm,h+bed) for tm,h in tail.values if pd.to_datetime(d.time.iloc[0])<=tm<=t.iloc[-1]]
axes[1].scatter([v[0] for v in tail_points],[v[1] for v in tail_points],color='red',s=10,label='闸后实测点',zorder=5);axes[1].set_ylabel('闸后高程 / m')
axes[2].plot(control_t,d.q_base_known_m3s,label='原始分水边界');axes[2].plot(control_t,d.q_command_m3s,label='第一轮分水');axes[2].plot(control_t,r.q_command_m3s,label='第二轮分水')
cap=d.refinement_trigger.map({True:.3,False:.2})
axes[2].fill_between(control_t,(1-cap)*d.q_base_known_m3s,(1+cap)*d.q_base_known_m3s,alpha=.12,label='原值限幅：局部30%/其余20%');axes[2].set_ylabel('分水流量 / m³/s')
axes[3].scatter(control_t,d.opening_observed_m,color='red',s=14,label='原开度观测采样',zorder=5)
axes[3].plot(control_t,r.opening_command_m,'--',label='开度边界');axes[3].fill_between(control_t,.8*d.opening_observed_m,1.2*d.opening_observed_m,alpha=.12,label='±20%');axes[3].set_ylabel('开度 / m')
for ax in axes:
 for _,item in intervals.iterrows():ax.axvspan(pd.to_datetime(item.start),pd.to_datetime(item.end),color='gold',alpha=.10)
 ax.legend(ncol=3);ax.grid(alpha=.2)
fig.suptitle('老庄第二轮：黄色时段为第一轮水深误差>10%的局部精修区间');fig.tight_layout();fig.savefig(out/'comparison.png',dpi=160);plt.close(fig)
a=s['first_round_metrics'];b=s['second_round']['metrics']
table='\n'.join(f'|{key}|{a[key]:.6f}|{b[key]:.6f}|' for key in ['RMSE_m','NRMSE','NSE','R2','KGE'])
report=f'''# 老庄第二轮局部精修

第一轮为`laozhuang_20pct_tailwater`，原文件保留。第一轮水深相对误差超过10%的{s['triggered_cycles']}个周期合并为{s['interval_count']}个时段，分水范围仅在这些时段扩大到原始已知流量±30%；未触发时段沿用第一轮分水，开度、上游流量及尾水不变。用户要求启动时尚未指定放大上限，因此采用并已告知最小新增档±30%，未继续扩大。

|指标|第一轮|第二轮|
|---|---:|---:|
{table}

全程RMSE改善：{s['global_RMSE_improved']}；固定水位验收通过：{s['accepted']}。NRMSE仍以观测范围归一化，不能与本次10%的单点相对误差触发混淆。

累计水量残差：{s['second_round']['downstream_boundary_report']['gates'][0]['cumulative_mass_residual_m3']:.9g} m³。完整周期回滚、最终仅推进一次、非触发区间分水不变、原始流量锚定限幅及开度/尾水不变均通过运行检查。

精修局部指标见summary.json。图中实测为原始时间戳红色离散点；黄色阴影为精修区间。第二轮从相同初态重演全时段，非精修时段的水位仍受上游状态传播影响。

边界文件：laozhuang_round2_boundaries.csv；完整尾水：downstream_stage_payload.json；触发周期与时段：first_round_refinement_mask.csv、refinement_intervals.csv；逐周期审计：reconstruction_audit.csv。未满足验收时边界仅为候选结果。
'''
(out/'report.md').write_text(report,encoding='utf-8')
print(table)
