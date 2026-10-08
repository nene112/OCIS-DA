# coding: utf-8
# coding: utf-8
from pathlib import Path
import sys,json
import pandas as pd,numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt,matplotlib.dates as mdates
sys.path[:0]=['tools','tools/GPT-Reconstruction']
import reach_missing_data_assimilation as da
root=Path('data/dayudu');out=root/'output/laozhuang_ordered_test';obs=da.load_observation_csv(root/'input/stage1_td.csv')['老庄节制闸'];end=pd.Timestamp('2026-04-21')
labels=['week_baseline','week_diversion_double','week_diversion_triple','week_diversion_quadruple'];frames={};metrics=[]
for name in labels:
 d=pd.read_csv(out/(name+'.csv'));d['time']=pd.to_datetime(d.time);d['state_time']=d.time+pd.Timedelta(hours=1);d['target_at_state_time']=[obs.value_at(t.to_pydatetime(),1800,'linear') for t in d.state_time];d['q_executed_aligned']=d.q_executed.shift(-1)
 evalmask=(d.state_time<=end)&d.target_at_state_time.notna();err=d.loc[evalmask,'h_sim']-d.loc[evalmask,'target_at_state_time'];metric={'scenario':name,'rmse_m':float(np.sqrt(np.mean(err**2))),'mae_m':float(np.mean(abs(err))),'n':int(evalmask.sum()),'opening_command_max_error_m':float(max(abs(d.e_executed_m-d.e_command_m))),'flow_command_max_error_after_1h_alignment_m3s':float(max(abs(d.q_executed_aligned.iloc[:-1]-d.q_command.iloc[:-1]))),'opening_adjustment_m':0.0};metrics.append(metric);frames[name]=d
pd.DataFrame(metrics).to_csv(out/'metrics.csv',index=False,encoding='utf-8-sig');qualified=[m for m in metrics if m['flow_command_max_error_after_1h_alignment_m3s']<1e-6];best=min(qualified,key=lambda m:m['rmse_m'])['scenario'];d=frames[best]
bound=pd.DataFrame({'tm':d.time,'二级站出口_Q_m3s':d.source_Q,'d0_Q_m3s':d.q_command,'老庄节制闸_e_m':d.e_command_m});bound.to_csv(out/'laozhuang_boundary_candidate.csv',index=False,encoding='utf-8-sig')
plt.rcParams['font.sans-serif']=['Microsoft YaHei'];plt.rcParams['axes.unicode_minus']=False
fig,axs=plt.subplots(3,1,figsize=(14,10),sharex=True)
pts=[(t,v) for t,v in obs.values if pd.Timestamp('2026-04-14')<=t<=end];axs[0].plot([t for t,v in pts],[v for t,v in pts],'-o',ms=1.7,lw=.6,color='black',label='实测水深')
colors=['#888888','#56b4e9','#0072b2','#d55e00'];texts=['原分水','分水 ×2','分水 ×3（执行不一致，排除）','分水 ×4（执行不一致，排除）']
for name,color,text in zip(labels,colors,texts):
 f=frames[name];valid=f.state_time<=end;axs[0].plot(f.loc[valid,'state_time'],f.loc[valid,'h_sim'],color=color,lw=1,label=text)
axs[0].set_ylabel('闸前水深 (m)');axs[0].set_title('固定同一实测开度，仅改变 d0 分水流量')
axs[1].plot(frames['week_baseline'].time,frames['week_baseline'].q_command,'-o',ms=2,lw=.7,label='原分水');axs[1].plot(d.time,d.q_command,label='所选候选分水：'+best);axs[1].set_ylabel('分水流量 (m³/s)')
axs[2].plot(d.time,d.e_observed_m,'-o',ms=3,lw=.8,label='实测开度（毫米换算成米）');axs[2].plot(d.time,d.e_command_m,'--',lw=1.3,label='执行开度：保持实测过程');axs[2].set_ylabel('开度 (m)')
for ax in axs:ax.grid(alpha=.2);ax.legend(fontsize=9);ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'));ax.set_xlim(pd.Timestamp('2026-04-14'),end)
fig.suptitle('老庄渠段单段测试：先分水，未触发开度调节\n2026-04-14 至 2026-04-21，北京时间',fontsize=15);fig.tight_layout(rect=[0,0,1,.93]);fig.savefig(out/'laozhuang_ordered_comparison.png',dpi=150)
audit={'window':['2026-04-14T00:00:00+08:00','2026-04-21T00:00:00+08:00'],'hours':168,'boundary_rows':len(bound),'selected_scenario':best,'method':'fresh independent runs; same observed opening profile; only diversion scale changed','twin_max_difference_m':0.0,'flow_response_first12h_zero_to_double_max_m':0.6239946365298015,'rejected_scenarios':[m['scenario'] for m in metrics if m['flow_command_max_error_after_1h_alignment_m3s']>=1e-6],'opening_stage_triggered':False,'reason':'diversion has measurable hydraulic influence and improves fitting; unmet 2cm target alone is not an opening trigger','state_timestamp':'native post-step depths mapped to interval end; command at interval start','flow_diagnostic_timestamp':'DLL reported diversion discharge delayed 1h; verified after alignment','gate_capacity_m3s':10.3,'native_calculation_types':{'diversion':0,'hydraulic_gate':1},'limitations':'single reach in-sample parameter screen, not a completed adaptive reconstruction; observed openings unchanged; 2cm accuracy not reached','metrics':metrics}
(out/'test_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8');print(pd.DataFrame(metrics).to_string(index=False));print('SELECTED',best)
assert max(m['opening_command_max_error_m'] for m in metrics)<1e-8
assert max(m['flow_command_max_error_after_1h_alignment_m3s'] for m in qualified)<1e-6
assert max(abs(d.e_command_m-d.e_observed_m))<1e-8

