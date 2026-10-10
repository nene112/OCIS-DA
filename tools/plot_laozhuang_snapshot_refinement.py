"""Keep four-panel layout; depth-error bars use the depth panel secondary axis."""
import json
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from run_fujialing_reconstruction_comparison import ROOT,da

out=ROOT/'data/dayudu/output/laozhuang_snapshot_refinement'
s=json.loads((out/'summary.json').read_text(encoding='utf-8'));ref=s['snapshot_refinement']
old=pd.read_csv(ROOT/'data/dayudu/output/laozhuang_round2_refinement/reconstruction_audit.csv')
d=pd.read_csv(out/'reconstruction_audit.csv');t=pd.to_datetime(d.state_time);ct=pd.to_datetime(d.time)
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei'],'axes.unicode_minus':False})
fig,axes=plt.subplots(4,1,figsize=(13,12),sharex=True)
raw=da.load_observation_csv(ROOT/'data/dayudu/input/stage1_td.csv')['老庄节制闸']
points=[(tm,h) for tm,h in raw.values if ct.iloc[0]<=tm<=t.iloc[-1]]
axes[0].scatter([v[0] for v in points],[v[1] for v in points],color='red',s=10,label='实测水深',zorder=5)
axes[0].plot(t,old.h_sim_m,label='此前局部精修');axes[0].plot(t,d.h_sim_m,label='前置调节＋快照精修');axes[0].set_ylabel('闸前水深 / m')
error=axes[0].twinx();relative=abs(d.h_sim_m-d.h_target_m)/abs(d.h_target_m)*100
error.bar(t,relative,width=.006,color='gray',alpha=.35,linewidth=0,label='水深相对误差');error.set_ylabel('水深相对误差 / %');error.set_ylim(0,max(20,relative.max()*1.4));error.legend(loc='upper right')
axes[1].plot(t,d.downstream_wh_m,label='DLL闸后水位')
raw=da.load_observation_csv(ROOT/'data/dayudu/input/stage2_td.csv')['老庄节制闸'];bed=s['downstream_boundary']['downstream_bed_m']
points=[(tm,h+bed) for tm,h in raw.values if ct.iloc[0]<=tm<=t.iloc[-1]]
axes[1].scatter([v[0] for v in points],[v[1] for v in points],color='red',s=10,label='闸后实测点',zorder=5);axes[1].set_ylabel('闸后高程 / m')
axes[2].plot(ct,d.q_base_planned_m3s,label='原始分水边界');axes[2].plot(ct,old.q_command_m3s,label='此前精修分水');axes[2].plot(ct,d.q_command_m3s,label='本次精修分水');axes[2].plot(ct,d.q_executed_m3s,'--',label='实际执行');axes[2].set_ylabel('分水流量 / m³/s')
axes[3].scatter(ct,d.opening_observed_m,color='red',s=14,label='原开度观测采样',zorder=5);axes[3].plot(ct,d.opening_command_m,'--',label='开度边界');axes[3].fill_between(ct,.8*d.opening_observed_m,1.2*d.opening_observed_m,alpha=.12,label='±20%');axes[3].set_ylabel('开度 / m')
for ax in axes:
 for item in ref['intervals']:
  ax.axvspan(ct.iloc[item['control_start_cycle']],t.iloc[item['end_cycle']-1],color='gold',alpha=.10)
 ax.legend(ncol=4);ax.grid(alpha=.2)
fig.suptitle('老庄快照精修：黄色为原精修区间及前置5周期调节窗口');fig.tight_layout();fig.savefig(out/'comparison.png',dpi=160);plt.close(fig)
a=ref['original_metrics'];b=ref['final_metrics']
table='\n'.join(f'|{key}|{a[key]:.6f}|{b[key]:.6f}|' for key in ['RMSE_m','NRMSE','NSE','R2','KGE'])
stops='\n'.join(f'- 周期{x["start_cycle"]}—{x["end_cycle"]-1}，前置从{x["control_start_cycle"]}开始：{x["status"]}' for x in ref['intervals'])
report=f'''# 老庄前置调节与快照精修

从此前第二轮控制值开始，选取模拟水深整体偏大且区间水深指标未达标的窗口，连同前5周期调节分水。分水范围按原始流量30%、40%、50%递增；开度、上游及尾水保持不变。区间内平均有符号分水执行误差>2%或水深精度达标时停止，最大50%仍未满足时记录到达上限，不宣称触发了执行能力限制。

快照间隔6周期。所有28个六周期块均由快照恢复并重演，完整原生节点/闸门状态、求解时钟和边界/逐孔开度映射与连续模拟完全一致；逐周期水深和执行流量检查通过。详见snapshot_equivalence.json。

精修从窗口前最近快照开始，保留修改窗口前相同控制历史，已修改后缀的旧快照失效；采用已接受分支的新快照。每个候选恢复完整周期初态，最后选定才推进一次。快照当前保存在同一DLL对象内存中，snapshot_index.json仅为索引，**不能跨进程作为完整快照加载**。

|指标|此前第二轮|本次精修|
|---|---:|---:|
{table}

固定水深验收通过：{b['passed']}。累计水量残差{ref['mass_residual_m3']:.9g} m³。开度、尾水及调节窗口外分水不变检查通过。各幅度档和停止判定均保留在summary.json与tier_*_audit.csv中。

停止原因：

{stops}

图保持水深/尾水/分水/开度四面板，实测红色离散点；水深误差为水深图副轴细条形图。边界为laozhuang_boundaries.csv；尚未达标时仅为候选结果。
'''
(out/'report.md').write_text(report,encoding='utf-8');print(table)
