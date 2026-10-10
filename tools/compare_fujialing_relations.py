# -*- coding: utf-8 -*-
"""Compare historical calculated discharge to the actual DLL at identical heads."""
from pathlib import Path
import json, ctypes, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from run_fujialing_reconstruction_comparison import bind_law, metrics, ROOT

out=ROOT/'data/dayudu/output/fujialing_reconstruction_comparison'
summary_path=out/'reconstruction_summary.json'
if not summary_path.exists():summary_path=ROOT/'data/dayudu/output/fujialing_reconstruction_smoke/reconstruction_summary.json'
summary=json.loads(summary_path.read_text(encoding='utf-8'))
gate=summary['baseline']['model_gate']
from HD_Roe import OcisMILPNet
client=OcisMILPNet()
law=bind_law(client)
hist=pd.read_csv(out/'historical_relation_valid.csv')
hist['q_model_same_state_m3s']=[sum(law(r.h_up_record_m,r.h_down_record_m,e,gate['Bwid_m'],gate['mu']) for e in [r.opening_1_m,r.opening_2_m]) for r in hist.itertuples()]
hist['q_difference_m3s']=hist.q_model_same_state_m3s-hist.q_reference_calculated_m3s
hist['q_model_if_3m_is_total_two_hole_width_m3s']=hist.q_model_same_state_m3s/2
hist['has_full_open_hole']=(hist.opening_1_m>=hist.h_up_record_m)|(hist.opening_2_m>=hist.h_up_record_m)
hist['regime']=np.where(hist.h_down_record_m/hist.h_up_record_m<=.7,'free','submerged')
hist.to_csv(out/'historical_model_comparison.csv',index=False,encoding='utf-8-sig')
regimes=[]
for name,g in hist.groupby('regime'):
 regimes.append({'regime':name,'n':len(g),'mean_reference_m3s':float(g.q_reference_calculated_m3s.mean()),'mean_model_m3s':float(g.q_model_same_state_m3s.mean()),'least_squares_reference_over_model':float(np.dot(g.q_reference_calculated_m3s,g.q_model_same_state_m3s)/np.dot(g.q_model_same_state_m3s,g.q_model_same_state_m3s))})
pd.DataFrame(regimes).to_csv(out/'relation_regime_diagnostics.csv',index=False)
comparison={'same_state_all':metrics(hist.q_reference_calculated_m3s,hist.q_model_same_state_m3s),'same_state_orifice_only':metrics(hist.loc[~hist.has_full_open_hole,'q_reference_calculated_m3s'],hist.loc[~hist.has_full_open_hole,'q_model_same_state_m3s']), 'width_m':gate['Bwid_m'],'mu':gate['mu'],'historical_flow_type':'calculated reference, not independent measured discharge','historical_heads':'recorded values treated as depths; sill datum unverified','full_open_formula':'current DLL free full-open expression lacks an additional head-length factor; unchanged for comparison','historical_two_holes':'individual capacities summed','simulation_opening':'2026 aggregate opening treated as one equivalent opening; individual openings unavailable'}
comparison['width_provenance']='native configured/default Bwid=3 m per opening; historical workbook does not verify hole width. Alternate total-width hypothesis retained in point CSV; width and coefficient errors cannot be distinguished here.'
for key in ['same_state_all','same_state_orifice_only']:
 for field in ['RMSE','bias']:comparison[key][field+'_m3s']=comparison[key].pop(field+'_m')
(out/'relation_comparison_summary.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
(out/'relation_comparison_summary.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
if '--history-only' in sys.argv:
 print(json.dumps(comparison,ensure_ascii=False,indent=2));sys.exit(0)
sim=pd.read_csv(out/'reconstruction_audit.csv')
base=pd.read_csv(out/'baseline_audit.csv')
sim.to_csv(out/'simulation_relation.csv',index=False,encoding='utf-8-sig')
common=hist.h_up_record_m.between(sim.h_sim_m.min(),sim.h_sim_m.max()) & hist.opening_sum_m.between(sim.opening_command_m.min(),sim.opening_command_m.max()) & hist.h_down_record_m.between(sim.h_down_sim_m.min(),sim.h_down_sim_m.max())
comparison['historical_records_inside_simulated_head_and_opening_ranges']=int(common.sum())
comparison['simulated_negative_gate_flux_cycles']=int((sim.gate_Q_sim_m3s<0).sum())
(out/'relation_comparison_summary.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei'],'axes.unicode_minus':False,'font.size':10})
t=pd.to_datetime(sim.state_time)
fig,axs=plt.subplots(3,1,figsize=(13,10),sharex=True)
axs[0].plot(t,sim.h_target_m,label='历史水位（2026）',color='black');axs[0].plot(t,base.h_sim_m,label='原边界仿真',alpha=.8);axs[0].plot(t,sim.h_sim_m,label='重建仿真');axs[0].set_ylabel('闸前水深 / m');axs[0].legend(ncol=3)
axs[1].plot(t,sim.q_base_observed_m3s,label='富家岭提水观测流量');axs[1].plot(t,sim.q_command_m3s,label='重建分水流量');axs[1].fill_between(t,sim.q_lower,sim.q_upper,alpha=.15,label='当前分级范围');axs[1].set_ylabel('分水流量 / m³/s');axs[1].legend(ncol=3)
axs[2].plot(t,sim.opening_observed_m,label='记录开度');axs[2].plot(t,sim.opening_command_m,'--',label='仿真开度');axs[2].set_ylabel('开度 / m');axs[2].legend();fig.suptitle('15号桥—富家岭闸前渠段边界重建：2026-04-14 至 2026-04-21')
for a in axs:a.grid(alpha=.2)
fig.tight_layout();fig.savefig(out/'reconstruction_timeseries.png',dpi=160);plt.close(fig)
fig,axs=plt.subplots(1,2,figsize=(13,5.4))
colors=np.where(hist.has_full_open_hole,'#d55e00','#0072b2')
axs[0].scatter(hist.q_reference_calculated_m3s,hist.q_model_same_state_m3s,c=colors,s=9,alpha=.4)
lim=max(hist.q_reference_calculated_m3s.max(),hist.q_model_same_state_m3s.max());axs[0].plot([0,lim],[0,lim],'k--');axs[0].set(xlabel='历史表计算流量 / m³/s',ylabel='DLL 同工况流量 / m³/s',title='相同水位、两孔开度：蓝=孔流，橙=存在全开孔')
axs[1].scatter(hist.opening_sum_m,hist.q_reference_calculated_m3s,s=8,alpha=.22,label='2025 历史计算关系（两孔总开度）');axs[1].scatter(sim.opening_command_m,sim.gate_Q_sim_m3s,s=14,label='2026 重建仿真（等效开度）',color='#d55e00');axs[1].set(xlabel='开度 / m',ylabel='流量 / m³/s',title='运行范围对比：跨时段轨迹不可直接视为误差');axs[1].legend()
for a in axs:a.grid(alpha=.2)
fig.tight_layout();fig.savefig(out/'historical_model_relation.png',dpi=180);plt.close(fig)
fig=plt.figure(figsize=(12,6));a=fig.add_subplot(121,projection='3d');b=fig.add_subplot(122,projection='3d')
for ax,q,title in [(a,hist.q_reference_calculated_m3s,'历史计算关系'),(b,hist.q_model_same_state_m3s,'DLL 同工况关系')]:
 ax.scatter(hist.h_up_record_m,hist.opening_sum_m,q,c=hist.h_down_record_m,cmap='viridis',s=5,alpha=.35);ax.set(xlabel='闸前水位 / m',ylabel='两孔总开度 / m',zlabel='流量 / m³/s',title=title)
b.scatter(sim.h_sim_m,sim.opening_command_m,sim.gate_Q_sim_m3s,c='red',s=12,label='重建轨迹');b.legend();fig.subplots_adjust(left=.04,right=.90,bottom=.12,top=.85,wspace=.22);fig.suptitle('水位—开度—流量关系；点色表示闸后水位，红色为重建轨迹',y=.98);fig.savefig(out/'head_opening_discharge.png',dpi=160,bbox_inches='tight',pad_inches=.3);plt.close(fig)
print(json.dumps(comparison,ensure_ascii=False,indent=2))
bm=summary['baseline']['metrics'];rm=summary['reconstruction']['metrics'];hm=comparison['same_state_all']
acceptance_label='通过' if rm['passed'] else '未通过；边界文件为候选结果，不应作为达标成果'
report=f'''# 富家岭闸前渠段重建与过流关系对比

重建范围：15号桥节制闸→富家岭节制闸（pool 3），2026-04-14 00:00至2026-04-21 00:00，168个一小时周期。水位指标固定，不放宽验收标准。

|水位指标|原边界|重建|要求|
|---|---:|---:|---:|
|RMSE / m|{bm['RMSE_m']:.4f}|{rm['RMSE_m']:.4f}|—|
|NRMSE|{bm['NRMSE']:.2%}|{rm['NRMSE']:.2%}|<5%|
|NSE|{bm['NSE']:.4f}|{rm['NSE']:.4f}|>0.9|
|R²|{bm['R2']:.4f}|{rm['R2']:.4f}|>0.9|
|KGE|{bm['KGE']:.4f}|{rm['KGE']:.4f}|>0.9|

水位验收：{acceptance_label}。开度触发周期数为{summary['reconstruction']['opening_eligible_cycles']}，实际调节周期数为{summary['reconstruction']['opening_adjusted_cycles']}。最初六周期分水能准确执行，随后出现执行不足，第7周期的五周期平均执行偏差为3.82%，首次触发开度调节。五周期执行偏差未达到2%时不能触发；这个触发条件与水位拟合误差是两项不同指标。重建使用的分水基准是富家岭提水观测，上游流量来自现有action文件。候选流量范围及层级在reconstruction_audit.csv逐周期记录。

历史表时间为2025-07-24至2025-09-01，共3776条记录，2239条有效。历史表流量为“计算闸门过流量”，未作为独立实测验证。历史关系对比在相同闸前/后水位、两孔分别记录的开度下调用同一个DLL，两孔容量求和。

同工况流量对比：RMSE={hm['RMSE_m3s']:.4f} m³/s，平均偏差（DLL−历史表）={hm['bias_m3s']:.4f} m³/s，R²={hm['R2']:.4f}，NRMSE={hm['NRMSE']:.2%}。历史与2026仿真三个输入（水位、闸后水位、开度）范围同时重叠的记录有{int(common.sum())}条，跨时期运行轨迹不可直接相减当作模型误差。

当前求解器闸后边通量有{comparison['simulated_negative_gate_flux_cycles']}个周期为负，图和CSV保留有符号值；它与非负的端点水头公式容量分列记录，未截断来美化结果，应在进一步率定前核查逆流或数值通量处理。

参数及数据限制：当前DLL净宽Bwid=3m，孔流系数0.6，两种堰流系数0.3和0.8。历史表未核实每孔净宽或闸底水位基准，因此不能将偏差全部归结于系数。CSV另保留“3m为两孔总净宽”假设的流量列供核对，未改变实际模型。自由与淹没出流的分区统计在relation_regime_diagnostics.csv。2026只提供总开度，仿真采用单孔等效开度；0.38m上限来自现有2026配置，未核实机械行程。历史单孔最大开度1.6m。

模型确认：已导入断面查表JSON，底高程保持原值，富家岭节点关联DYD016。该断面原始尺寸存在顶部宽度不一致告警，当前用半径和边坡构形。修复了零渠底宽误作闸门宽度、开度更新节点索引，以及增加当前周期流量导出。未率定或改变过流系数。当前全开自由出流分支缺少长度因子的问题保留并标记，有效历史中仅2条存在全开孔，不能解释主要孔流偏差。

状态检查：候选试算完整回滚水力状态及求解器时钟；选定控制后只推进一次，8周期A/B/A回归测试通过；本次168周期试算也检查回放一致和每次最终时钟推进3600秒。DLL SHA256：{summary['dll_sha256']}。

输出：fujialing_boundaries.csv（流量与开度类型明确）、historical_model_comparison.csv、simulation_relation.csv、各审计与summary文件。图见reconstruction_timeseries.png、historical_model_relation.png及head_opening_discharge.png。
'''
(out/'report.md').write_text(report,encoding='utf-8')
