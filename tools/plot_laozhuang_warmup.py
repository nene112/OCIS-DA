# -*- coding: utf-8 -*-
"""Keep the four-panel audit plot and show warmup as a separate artifact."""
import argparse,json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from run_fujialing_reconstruction_comparison import ROOT,da

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=str,default='data/dayudu/output/laozhuang_pid_warmup')
args=parser.parse_args();out=ROOT/args.output
s=json.loads((out/'summary.json').read_text(encoding='utf-8'));w=s['warmup']
d=pd.read_csv(out/'reconstruction_audit.csv')
old=pd.read_csv(ROOT/'data/dayudu/output/laozhuang_delivery_5pct/reconstruction_audit.csv')
t=pd.to_datetime(d.state_time);ct=pd.to_datetime(d.time)
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei'],'axes.unicode_minus':False})
fig,axes=plt.subplots(4,1,figsize=(13,12),sharex=True)
def measured(ax,file,factor=1,offset=0,label='实测'):
 raw=da.load_observation_csv(ROOT/'data/dayudu/input'/file)['老庄节制闸']
 points=[(tm,v*factor+offset) for tm,v in raw.values if ct.iloc[0]<=tm<=t.iloc[-1]]
 if points:ax.scatter([p[0] for p in points],[p[1] for p in points],color='red',s=10,label=label,zorder=5)
measured(axes[0],'stage1_td.csv',label='实测水深')
axes[0].plot(t,old.h_sim_m,label='预热前，同一分水边界')
axes[0].plot([ct.iloc[0],*t],[w['formal_initial_depth_m'],*d.h_sim_m],label='PID预热后候选')
axes[0].set_ylabel('闸前水深 / m')
err=axes[0].twinx();relative=abs(d.h_sim_m-d.h_target_m)/abs(d.h_target_m)*100
err.bar(t,relative,width=.006,color='gray',alpha=.35,linewidth=0,label='水深相对误差')
err.set_ylabel('水深相对误差 / %');err.set_ylim(0,max(20,relative.max()*1.4));err.legend(loc='upper right')
axes[1].plot(t,d.downstream_wh_m,label='DLL闸后水位')
measured(axes[1],'stage2_td.csv',offset=s['downstream_boundary']['downstream_bed_m'],label='闸后实测点')
axes[1].set_ylabel('闸后高程 / m')
axes[2].plot(ct,d.q_base_planned_m3s,label='原始分水边界')
axes[2].plot(ct,d.q_command_m3s,label='既有90%档分水边界')
axes[2].plot(ct,d.q_executed_m3s,'--',label='预热后执行流量');axes[2].set_ylabel('分水流量 / m³/s')
measured(axes[3],'gate_e_td.csv',factor=.001,label='原始开度实测点')
axes[3].plot(ct,d.opening_command_m,'--',label='开度边界')
axes[3].fill_between(ct,.8*d.opening_observed_m,1.2*d.opening_observed_m,alpha=.12,label='原边界±20%')
axes[3].set_ylabel('开度 / m')
for ax in axes:ax.legend(ncol=4);ax.grid(alpha=.2)
fig.suptitle('老庄渠段：预热前后对比，正式边界及过流系数保持相同')
fig.tight_layout();fig.savefig(out/'comparison.png',dpi=160);plt.close(fig)

trace=pd.read_csv(out/'warmup_selected_trace.csv')
fig,ax=plt.subplots(2,1,figsize=(10,6),sharex=True)
pseudo_time=trace.elapsed_seconds/3600-w['duration_seconds']/3600
ax[0].plot([-w['duration_seconds']/3600,*pseudo_time],
           [w['cold_depth_before_warmup_m'],*trace.h_up_m],label='PID预热水深')
ax[0].scatter([0],[w['target_depth_m']],color='red',s=30,label='正式起点实测',zorder=5)
ax[0].fill_between(pseudo_time,w['target_depth_m']-w['depth_tolerance_m'],
                   w['target_depth_m']+w['depth_tolerance_m'],alpha=.15,color='gray',label=f'PID目标±{w["depth_tolerance_m"]*100:g} cm')
ax[0].set_ylabel('闸前水深 / m');ax[0].set_title('PID预热：水位与入流响应')
ax[1].plot(pseudo_time,trace.source_Q_m3s,label='PID预热入流设定')
ax[1].axhline(w['formal_source_Q_m3s'],linestyle='--',color='gray',label='正式起点入流（切回值）')
ax[1].set_ylabel('上游入流 / m³/s');ax[1].set_xlabel('距正式起点的预热伪时间 / h')
for a in ax:a.grid(alpha=.2);a.legend()
fig.tight_layout();fig.savefig(out/'warmup_comparison.png',dpi=160);plt.close(fig)

from run_fujialing_reconstruction_comparison import metrics
old_metrics=metrics(old.h_target_m,old.h_sim_m);new_metrics=s['metrics']
table='\n'.join(f'|{key}|{old_metrics[key]:.6f}|{new_metrics[key]:.6f}|' for key in ['RMSE_m','NRMSE','NSE','R2','KGE'])
report=f'''# 老庄预热初态验证

正式时段2026-04-14 00:00至2026-04-21 00:00。正式分水、开度、上游流量、尾水、断面和过流系数均与上一轮采用边界一致，仅增加预热；本次不重新优化边界。

预热采用项目现有PIDController，每{w['step_seconds']}秒根据实测起点水深与当前模拟水深的误差，调节临时上游入流。分水、开度及尾水保持起点值，统一初始水深{w['cold_depth_before_warmup_m']:.6f} m不再试配。最低预热{w['minimum_duration_seconds']/3600:g}小时，允许最长{w['maximum_duration_seconds']/3600:g}小时，本次实际{w['duration_seconds']/3600:g}小时。预热末{w['formal_initial_depth_m']:.6f} m，实测{w['target_depth_m']:.6f} m，误差{abs(w['selected_trial']['error_m'])*100:.3f} cm。

PID参数Kp={w['pid']['kp']}、Ki={w['pid']['ki']}、Kd={w['pid']['kd']}；积分与微分以小时计。入流数值范围[{w['pid']['q_min']},{w['pid']['q_max']}] m³/s，每步最大变化{w['pid']['q_max_step']} m³/s，积分限幅{w['pid']['integral_limit']} m·h，抗饱和反馈系数{w['pid']['anti_windup_gain']}。这些为预热数值控制设置，不是实测设备能力。

停止条件：过去连续{w['stable_window_seconds']/60:g}分钟，所有水深误差≤{w['depth_tolerance_m']*100:g} cm，水深极差≤{w['stable_depth_range_m']*100:g} cm，入流极差≤{w['stable_source_range_m3s']} m³/s。实际最大误差{w['achieved_max_abs_error_m']*100:.3f} cm，水深极差{w['achieved_depth_range_m']*100:.3f} cm，入流极差{w['achieved_source_range_m3s']:.6f} m³/s。通过={w['initial_match_passed']}，没有仅凭一次命中目标选取快照。

预热为PID控制下的伪时间初态准备，不宣称是实测历史或完全稳态；最后一个预热步水深变化{w['last_step_depth_change_m']:.6f} m。没有把目标水位强制写入预热末状态。warmup_trace.csv记录完整连续PID过程和P/I/D、限幅反馈；formal_initial_state.csv保存正式起点各节点水深与流量。

开始正式模拟时保留预热后的节点水深和流量，重新设定正式起点时钟与3600秒交互周期，恢复正式尾水序列并重置尾水累计量。将上游入流从预热末{w['final_pid_source_Q_m3s']:.6f}切回正式起点{w['formal_source_Q_m3s']:.6f} m³/s，PID只在预热阶段起作用。全节点h/Q数组在时钟切换前后完全一致；正式周期末时钟为3600、7200…秒。预热不进入正式水深评分及正式尾水守恒统计。

每6个正式周期重建内存快照，并验证28个六小时块由快照重演后完整原生状态与连续模拟一致；详情snapshot_equivalence.json。这些快照仅在同一DLL对象中可用。

|指标|预热前|预热后|
|---|---:|---:|
{table}

水深固定四项验收通过={new_metrics['passed']}；预热后候选累计水量残差{s['downstream_boundary_report']['gates'][0]['cumulative_mass_residual_m3']:.9g} m³。起点匹配通过不等于全时段精度通过。

边界文件laozhuang_boundaries.csv及downstream_stage_payload.json；图comparison.png保持四面板、实测红色离散点、水深相对误差为副轴细条形图，独立预热过程图为warmup_comparison.png。
'''
(out/'report.md').write_text(report,encoding='utf-8')
print(table)
