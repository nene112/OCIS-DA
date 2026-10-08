# coding: utf-8
from pathlib import Path
import json,sys
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt,matplotlib.dates as mdates
sys.path[:0]=['tools','tools/GPT-Reconstruction']
import reach_missing_data_assimilation as da
p=Path('data/dayudu');out=p/'output/laozhuang_five_cycle_guard';d=pd.read_csv(out/'interaction_audit.csv');d.time=pd.to_datetime(d.time);d.state_time=pd.to_datetime(d.state_time);base=pd.read_csv(p/'output/laozhuang_ordered_test/week_baseline.csv');bt=pd.to_datetime(base.time)+pd.Timedelta(hours=1);end=pd.Timestamp('2026-04-21');obs=da.load_observation_csv(p/'input/stage1_td.csv')['老庄节制闸'];pts=[(t,v) for t,v in obs.values if d.time.iloc[0]<=t<=end]
plt.rcParams['font.sans-serif']=['Microsoft YaHei'];plt.rcParams['axes.unicode_minus']=False
fig,axs=plt.subplots(4,1,figsize=(14,12),sharex=True)
axs[0].plot([t for t,v in pts],[v for t,v in pts],'-o',ms=1.5,lw=.6,color='black',label='实测水深');axs[0].plot(bt[bt<=end],base.loc[bt<=end,'h_sim'],color='grey',label='原边界模拟');valid=d.state_time<=end;axs[0].plot(d.loc[valid,'state_time'],d.loc[valid,'h_sim'],color='#0072b2',label='五周期判据重算（分水加倍）');axs[0].set_ylabel('水深 (m)')
axs[1].plot(d.time,d.q_base,'-o',ms=2,lw=.7,label='原分水');axs[1].plot(d.time,d.q_command,label='d0 设定流量');axs[1].plot(d.time.iloc[:-1],d.q_reported_previous_cycle.iloc[1:].to_numpy(),'--',label='模拟分水流量（对齐滞后）');axs[1].set_ylabel('流量 (m³/s)')
axs[2].plot(d.time,d.e_observed_m,'-o',ms=2,lw=.7,label='实测开度');axs[2].plot(d.time,d.e_command_m,'--',label='执行开度：未触发调节');axs[2].set_ylabel('开度 (m)')
axs[3].plot(d.time,d.past5_mean_signed_relative_error*100,color='#0072b2',label='过去五周期平均相对偏差');axs[3].axhline(10,color='#d55e00',ls='--',label='启动阈值：严格大于 10%');axs[3].set_ylabel('偏差 (%)');axs[3].set_ylim(-1,12)
for ax in axs:ax.grid(alpha=.2);ax.legend(fontsize=9);ax.set_xlim(d.time.iloc[0],end);ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
fig.suptitle('老庄渠段重算：分水执行偏差过去五周期均值 >10% 才调开度\n2026-04-14 至 2026-04-21，北京时间；本次触发 0 次',fontsize=15);fig.tight_layout(rect=[0,0,1,.94]);fig.savefig(out/'laozhuang_five_cycle_comparison.png',dpi=150)
summary=json.loads((out/'summary.json').read_text(encoding='utf-8'));assert summary['eligible_cycles']==0;assert max(abs(d.q_command.iloc[:-1].to_numpy()-d.q_reported_previous_cycle.iloc[1:].to_numpy()))<1e-6;assert max(abs(d.e_command_m-d.e_observed_m))<1e-8;print('Plot and replay verification passed')
