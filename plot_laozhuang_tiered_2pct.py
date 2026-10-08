# coding: utf-8
from pathlib import Path
import json
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt,matplotlib.dates as mdates
p=Path('data/dayudu/output/laozhuang_tiered_2pct');d=pd.read_csv(p/'interaction_audit.csv');summary=json.loads((p/'summary.json').read_text(encoding='utf-8'));d.time=pd.to_datetime(d.time);d.state_time=pd.to_datetime(d.state_time);end=pd.Timestamp('2026-04-21');v=d.state_time<=end
plt.rcParams['font.sans-serif']=['Microsoft YaHei'];plt.rcParams['axes.unicode_minus']=False
fig,axs=plt.subplots(4,1,figsize=(14,12),sharex=True)
axs[0].plot(d.loc[v,'state_time'],d.loc[v,'h_target_at_state_time'],'k.-',ms=3,label='实测目标（水深）');axs[0].plot(d.loc[v,'state_time'],d.loc[v,'h_sim'],label='分级边界重建模拟');axs[0].set_ylabel('水深 (m)')
axs[1].fill_between(d.time,d.q_lower,d.q_upper,alpha=.18,label='当前分水分级窗口');axs[1].plot(d.time,d.q_base,label='已知分水边界');axs[1].plot(d.time,d.q_command,label='重建分水流量');axs[1].plot(d.time.iloc[:-1],d.q_reported_previous_cycle.iloc[1:].to_numpy(),'--',label='模拟分水（滞后对齐）');axs[1].set_ylabel('流量 (m³/s)')
axs[2].plot(d.time,d.e_observed_m,label='已知开度边界');axs[2].plot(d.time,d.e_command_m,'--',label='重建开度');axs[2].set_ylabel('开度 (m)')
axs[3].plot(d.time,d.past5_mean_signed_relative_error_used*100,label='过去五周期平均相对偏差');axs[3].axhline(2,color='red',ls='--',label='开度触发阈值 2%');axs[3].plot(d.time,d.q_amplitude_tier*100,':',label='分水幅度档位 (%)');axs[3].set_ylabel('偏差 / 档位 (%)')
for ax in axs:ax.grid(alpha=.2);ax.legend(fontsize=9);ax.set_xlim(d.time.iloc[0],end);ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
m=summary['metrics'];fig.suptitle(f"老庄单渠段测试：分水分级限幅，五周期分水偏差 >2% 才调开度\nNRMSE={m['NRMSE']*100:.2f}%，NSE={m['NSE']:.3f}，R²={m['R2']:.3f}，KGE={m['KGE']:.3f}",fontsize=14);fig.tight_layout(rect=[0,0,1,.94]);fig.savefig(p/'comparison.png',dpi=150)
print('Plot generated')
