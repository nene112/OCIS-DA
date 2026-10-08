import sys,json,csv
from pathlib import Path
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt,matplotlib.dates as mdates
sys.path[:0]=['tools','tools/GPT-Reconstruction']
import reach_missing_data_assimilation as da
root=Path('data/dayudu'); out=root/'output/reconstruction_20260414_20260421'
plt.rcParams['font.sans-serif']=['Microsoft YaHei']; plt.rcParams['axes.unicode_minus']=False
frames=[pd.read_csv(out/str(i)/'assimilated_series.csv') for i in range(7)]
for d in frames:d['time']=pd.to_datetime(d['time'])
base=da.load_observation_csv(root/'input/action_td.csv'); obs_e=da.load_observation_csv(root/'input/gate_e_td.csv'); obs_h=da.load_observation_csv(root/'input/stage1_td.csv')
times=frames[0]['time']; flows=pd.DataFrame({'tm':times}); opening=pd.DataFrame({'tm':times}); diagnostics=pd.DataFrame({'tm':times})
def values(s):
 return [s.value_at(t.to_pydatetime(),86400,'causal') for t in times]
for name in ['二级站出口']+[f'd{i}' for i in range(11)]:
 flows[name]=values(base.get(name,da.ObservationSeries()))
for i,d in enumerate(frames):
 flows[f'd{i}']=d['q_boundary_analysis']; name=d['target_gate_name'].iloc[0]; opening[name]=d['gate_opening_analysis']; diagnostics[name+'_Q_m3s']=d['gate_q_analysis']
for name in ['杨元坝节制闸','麻园节制闸','17号桥节制闸']:opening[name]=0.768571
flows.to_csv(out/'flow_boundaries_m3s.csv',index=False,encoding='utf-8-sig');opening.to_csv(out/'opening_boundaries_m.csv',index=False,encoding='utf-8-sig');diagnostics.to_csv(out/'gate_discharge_diagnostics_m3s.csv',index=False,encoding='utf-8-sig')
combined=flows.copy()
for col in opening.columns[1:]:combined[col+'_e_m']=opening[col]
for col in diagnostics.columns[1:]:combined[col]=diagnostics[col]
combined.to_csv(out/'action_reconstruction.csv',index=False,encoding='utf-8-sig')
metrics=[]
fig,axs=plt.subplots(4,2,figsize=(17,14),sharex=True)
for i,d in enumerate(frames):
 name=d['target_gate_name'].iloc[0]; ax=axs.flat[i]; o=obs_h[name]; pairs=[(t,v) for t,v in o.values if times.iloc[0]<=t<=times.iloc[-1]]
 ax.plot([t for t,v in pairs],[v for t,v in pairs],'-o',ms=1.5,lw=.7,color='black',label='实测')
 ax.plot(times,d['h1_forecast'],color='#a8a8a8',label='原边界模拟');ax.plot(times,d['h1_analysis'],color='#0072b2',label='重建边界模拟')
 valid=np.isfinite(d['h1_control_target']); y=d.loc[valid,'h1_control_target']; a=d.loc[valid,'h1_analysis']; b=d.loc[valid,'h1_forecast']
 m={'pool':i,'gate':name,'n':int(valid.sum()),'baseline_rmse_m':float(np.sqrt(np.mean((b-y)**2))),'reconstructed_rmse_m':float(np.sqrt(np.mean((a-y)**2))),'reconstructed_mae_m':float(np.mean(abs(a-y))),'within_2cm_fraction':float(np.mean(abs(a-y)<=.02))}
 metrics.append(m);ax.set_title(f'{name} | RMSE {m["baseline_rmse_m"]:.3f} → {m["reconstructed_rmse_m"]:.3f} m');ax.set_ylabel('闸前水深 (m)');ax.grid(alpha=.2);ax.legend(fontsize=8)
axs.flat[7].axis('off')
for ax in axs.flat:ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'));ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
fig.suptitle('Dayudu 边界重建：2026-04-14 至 2026-04-21（北京时间，逐渠段拟合）',fontsize=16);fig.tight_layout(rect=[0,0,1,.97]);fig.savefig(out/'water_depth_comparison.png',dpi=150);plt.close(fig)
fig,axs=plt.subplots(7,2,figsize=(17,19),sharex=True)
for i,d in enumerate(frames):
 name=d['target_gate_name'].iloc[0]
 axs[i,0].plot(times,d['q_boundary_forecast'],'-o',ms=2,lw=.7,label='原分水流量');axs[i,0].plot(times,d['q_boundary_analysis'],lw=1,label='重建分水流量');axs[i,0].set_title(f'd{i} → {name}');axs[i,0].set_ylabel('分水流量 (m³/s)')
 e=obs_e.get(name,da.ObservationSeries()); pts=[(t,v/1000) for t,v in e.values if times.iloc[0]<=t<=times.iloc[-1]]
 axs[i,1].plot([t for t,v in pts],[v for t,v in pts],'-o',ms=1.5,lw=.7,label='实测开度');axs[i,1].plot(times,d['gate_opening_analysis'],lw=1,label='重建开度');axs[i,1].set_title(name);axs[i,1].set_ylabel('开度 (m)')
 for ax in axs[i]:ax.grid(alpha=.2);ax.legend(fontsize=8);ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'));ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
fig.suptitle('重建的流量边界与开度边界（控制量分开）',fontsize=16);fig.tight_layout(rect=[0,0,1,.98]);fig.savefig(out/'boundary_comparison.png',dpi=140);plt.close(fig)
pd.DataFrame(metrics).to_csv(out/'metrics.csv',index=False,encoding='utf-8-sig')
audit={'window':['2026-04-14T00:00:00+08:00','2026-04-21T00:00:00+08:00'],'dt_seconds':3600,'rows':len(times),'method':'project boundary-reconstruction skill, per-reach PID/bisection with opening fallback','source_case':r'D:\Algorithm dev\0_DA\DYD-DA\DA\data\dayudu_new-d0','aliases':{'圪塔节制闸':'疙瘩节制闸','麻原节制闸':'麻园节制闸'},'initial_observation_anchor':'first observation within 5 minutes of start','flow_units':'m3/s','opening_units':'m; observations mm divided by 1000','diagnostic_gate_flow_is_not_a_boundary':True,'unreconstructed_pools':[7,8,9,10],'unreconstructed_openings':'fixed 0.768571m per skill fallback, no observed depths','validation':'in-sample isolated reach reconstruction, not a coupled-network replay or held-out validation','metrics':metrics}
(out/'boundary_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
assert len(times)==169 and times.iloc[0]==pd.Timestamp('2026-04-14') and times.iloc[-1]==pd.Timestamp('2026-04-21')
assert np.isfinite(flows.iloc[:,1:].to_numpy(dtype=float)).all(), 'Missing flow boundary values'
assert np.isfinite(opening.iloc[:,1:].to_numpy(dtype=float)).all(), 'Missing opening boundary values'
print(pd.DataFrame(metrics).to_string(index=False)); print('Output',out.resolve())
