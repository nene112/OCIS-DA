# coding: utf-8
import sys,json
from pathlib import Path
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt,matplotlib.dates as mdates
sys.path[:0]=['tools','tools/GPT-Reconstruction']
import reach_missing_data_assimilation as da
p=Path('data/dayudu');out=p/'output/partial_pool0_20260414_20260421';d=pd.read_csv(out/'0/assimilated_series.csv');t=pd.to_datetime(d.time)
base=da.load_observation_csv(p/'input/action_td.csv');eo=da.load_observation_csv(p/'input/gate_e_td.csv');ho=da.load_observation_csv(p/'input/stage1_td.csv')
b=pd.DataFrame({'tm':t})
for name in ['二级站出口']+[f'd{i}' for i in range(11)]:b[name]=[base.get(name,da.ObservationSeries()).value_at(x.to_pydatetime(),86400,'causal') for x in t]
b['d0']=d.q_boundary_analysis;b.to_csv(out/'flow_boundaries_m3s.csv',index=False,encoding='utf-8-sig')
e=pd.DataFrame({'tm':t})
for name,s in eo.items():
 e[name]=[s.value_at(x.to_pydatetime(),86400,'causal') for x in t]
 e[name]=e[name].ffill().bfill()/1000
# Only LaoZhuang is reconstructed; the remaining observed openings are preserved.
e['老庄节制闸']=d.gate_opening_analysis
for name in ['杨元坝节制闸','麻园节制闸','17号桥节制闸']:e[name]=.768571
e.to_csv(out/'opening_boundaries_m.csv',index=False,encoding='utf-8-sig')
combined=b.copy()
for col in e.columns[1:]:combined[col+'_e_m']=e[col]
combined.to_csv(out/'action_reconstruction_partial.csv',index=False,encoding='utf-8-sig')
d[['time','gate_q_analysis']].rename(columns={'gate_q_analysis':'老庄节制闸_Q_m3s'}).to_csv(out/'gate_discharge_diagnostics_m3s.csv',index=False,encoding='utf-8-sig')
plt.rcParams['font.sans-serif']=['Microsoft YaHei'];plt.rcParams['axes.unicode_minus']=False
fig,axs=plt.subplots(3,1,figsize=(14,10),sharex=True)
pts=[(x,y) for x,y in ho['老庄节制闸'].values if t.iloc[0]<=x<=t.iloc[-1]]
axs[0].plot([x for x,y in pts],[y for x,y in pts],'-o',lw=.7,ms=2,color='black',label='实测闸前水深');axs[0].plot(t,d.h1_forecast,color='grey',label='原边界模拟');axs[0].plot(t,d.h1_analysis,color='#0072b2',label='重建边界模拟');axs[0].set_ylabel('水深 (m)')
axs[1].plot(t,d.q_boundary_forecast,'-o',lw=.7,ms=2,label='原分水流量');axs[1].plot(t,d.q_boundary_analysis,label='重建 d0');axs[1].set_ylabel('流量 (m³/s)')
pts=[(x,y/1000) for x,y in eo['老庄节制闸'].values if t.iloc[0]<=x<=t.iloc[-1]]
axs[2].plot([x for x,y in pts],[y for x,y in pts],'-o',lw=.7,ms=2,label='实测开度');axs[2].plot(t,d.gate_opening_analysis,label='重建开度');axs[2].set_ylabel('开度 (m)')
for ax in axs:ax.grid(alpha=.2);ax.legend();ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
fig.suptitle('Dayudu 老庄渠段候选结果（未通过验证，请勿作为最终边界使用）\n2026-04-14 00:00 — 2026-04-21 00:00，北京时间',fontsize=15);fig.tight_layout(rect=[0,0,1,.93]);fig.savefig(out/'partial_pool0_comparison.png',dpi=150)
valid=d.h1_control_target.notna();y=d.loc[valid,'h1_control_target'];a=d.loc[valid,'h1_analysis'];f=d.loc[valid,'h1_forecast']
audit={'status':'REJECTED candidate; native rollback does not restore solver clock','reconstructed_pools':[0],'unreconstructed_pools':list(range(1,11)),'window':['2026-04-14T00:00:00+08:00','2026-04-21T00:00:00+08:00'],'dt_seconds':3600,'rows':len(t),'baseline_rmse_m':float(np.sqrt(np.mean((f-y)**2))),'reconstructed_rmse_m':float(np.sqrt(np.mean((a-y)**2))),'within_2cm_fraction':float(np.mean(abs(a-y)<=.02)),'original_source':r'D:\Algorithm dev\0_DA\DYD-DA\DA\data\dayudu_new-d0','flow_columns':'二级站出口,d0...d10: m3/s; only d0 reconstructed','opening_columns':'m; observations converted from mm; only LaoZhuang reconstructed; last three unobserved gates use skill default .768571m','diagnostic_gate_flow_is_not_a_boundary':True,'blocked_reason':'native DLL rollback omits solver clock and gate runtime state; repeated trials advance cumulative time; opening command execution also fails verification','initial_depth':'nearest observation within 5 minutes','validation':'per-reach in-sample fit, not coupled replay or held-out validation'}
(out/'boundary_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(audit,ensure_ascii=False,indent=2))
assert len(t)==169 and t.iloc[-1]==pd.Timestamp('2026-04-21')
assert np.isfinite(b.iloc[:,1:].to_numpy(dtype=float)).all()
assert np.isfinite(e.iloc[:,1:].to_numpy(dtype=float)).all()
