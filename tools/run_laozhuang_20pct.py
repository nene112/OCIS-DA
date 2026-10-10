# -*- coding: utf-8 -*-
"""Replay LaoZhuang with strict timestamp-original +/-20% flow/opening limits."""
import argparse,hashlib,json
from datetime import datetime,timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from run_fujialing_reconstruction_comparison import ROOT,run,held,da

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours',type=int,default=168)
    parser.add_argument('--downstream-stage',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'data/dayudu/output/laozhuang_20pct_updated_sections')
    args=parser.parse_args();case=ROOT/'data/dayudu';out=args.output;out.mkdir(parents=True,exist_ok=True)
    start=datetime(2026,4,14);end=start+timedelta(hours=args.hours)
    openings=da.load_observation_csv(case/'input/gate_e_td.csv')['老庄节制闸']
    # This is an input-derived numerical ceiling, not a verified mechanical stroke.
    max_opening=max(held(openings,start+timedelta(hours=k))/1000 for k in range(args.hours))*1.2
    kwargs=dict(pool_id=0,diversion_series_name=None,use_observed_source=True,
        opening_file='gate_e_td.csv',amplitude_cap=.2,relative_opening=True,
        boundary_filename='laozhuang_boundaries.csv',downstream_stage_file='stage2_td.csv' if args.downstream_stage else None)
    baseline=run(case,out,start,args.hours,False,max_opening,**kwargs)
    reconstruction=run(case,out,start,args.hours,True,max_opening,**kwargs)
    b=pd.read_csv(out/'baseline_audit.csv');d=pd.read_csv(out/'reconstruction_audit.csv')
    d=d.rename(columns={'q_base_observed_m3s':'q_base_known_m3s'})
    d['opening_lower_m']=d.opening_observed_m*(1-d.opening_tier)
    d['opening_upper_m']=d.opening_observed_m*(1+d.opening_tier)
    d.to_csv(out/'reconstruction_audit.csv',index=False,encoding='utf-8-sig')
    b=b.rename(columns={'q_base_observed_m3s':'q_base_known_m3s'})
    b.to_csv(out/'baseline_audit.csv',index=False,encoding='utf-8-sig')
    assert (abs(d.q_command_m3s-d.q_base_known_m3s)<=.2*abs(d.q_base_known_m3s)+1e-9).all()
    assert (abs(d.opening_command_m-d.opening_observed_m)<=.2*abs(d.opening_observed_m)+1e-9).all()
    assert ((d.opening_command_m>=d.opening_lower_m-1e-9)&(d.opening_command_m<=d.opening_upper_m+1e-9)).all()
    assert (d.loc[~d.opening_eligible,'opening_command_m']==d.loc[~d.opening_eligible,'opening_observed_m']).all()
    ratio=lambda changed,original: float(np.max(np.divide(abs(changed-original),abs(original),out=np.zeros(len(original)),where=abs(original)>1e-12)))
    summary={'reach':'二级站出口→老庄节制闸','pool_id':0,'period':[start.isoformat(),end.isoformat()],
        'baseline':baseline,'reconstruction':reconstruction,
        'max_actual_flow_adjustment_fraction':ratio(d.q_command_m3s,d.q_base_known_m3s),
        'max_actual_opening_adjustment_fraction':ratio(d.opening_command_m,d.opening_observed_m),
        'all_20pct_limits_verified':True,
        'known_flow_source':'action_td.csv d0; upstream source uses action_obs_td.csv 二级站出口 and is not reconstructed',
        'known_opening_source':'gate_e_td.csv 老庄节制闸 /1000 (same individual opening convention as previous LaoZhuang test)',
        'constraints':'both Q and e anchored to timestamp original values, +/-10% then +/-20%, zero original remains zero; no cumulative drift; no exceedance above20%',
        'opening_trigger':'signed mean((Qset-Qexecuted)/Qexecuted) over five previous completed cycles >2%',
        'water_acceptance':'NRMSE<5%, NSE>0.9, R2>0.9, KGE>0.9; NRMSE=RMSE/observation range',
        'input_derived_opening_ceiling_m':max_opening,'mechanical_stroke_verified':False,
        'dll_sha256':hashlib.sha256((ROOT/'tools/OcisMILPNet.dll').read_bytes()).hexdigest(),
        'section_file_sha256':hashlib.sha256((case/'input/CrossSection_dayudu_hydraulic_tables.geojson').read_bytes()).hexdigest()}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if args.hours<24:return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei'],'axes.unicode_minus':False})
    t=pd.to_datetime(d.state_time);fig,axes=plt.subplots(3,1,figsize=(13,10),sharex=True)
    axes[0].scatter(t,d.h_target_m,color='red',s=14,label='观测水位插值点',zorder=5);axes[0].plot(t,b.h_sim_m,label='原边界仿真');axes[0].plot(t,d.h_sim_m,label='重建仿真');axes[0].set_ylabel('老庄闸前水深 / m')
    axes[1].plot(t,d.q_base_known_m3s,label='原分水边界');axes[1].plot(t,d.q_command_m3s,label='重建分水设定');axes[1].plot(t,d.q_executed_m3s,'--',label='实际执行');axes[1].fill_between(t,.8*d.q_base_known_m3s,1.2*d.q_base_known_m3s,alpha=.12,label='±20%硬限幅');axes[1].set_ylabel('d0 流量 / m³/s')
    axes[2].scatter(t,d.opening_observed_m,color='red',s=14,label='原开度观测采样',zorder=5);axes[2].plot(t,d.opening_command_m,'--',label='重建开度');axes[2].fill_between(t,.8*d.opening_observed_m,1.2*d.opening_observed_m,alpha=.12,label='±20%硬限幅');axes[2].set_ylabel('开度 / m')
    for a in axes:a.legend(ncol=4);a.grid(alpha=.2)
    fig.suptitle('二级站出口—老庄闸前渠段重建：流量与开度累计调节均≤20%');fig.tight_layout();fig.savefig(out/'comparison.png',dpi=160);plt.close(fig)
    bm=baseline['metrics'];rm=reconstruction['metrics'];accepted='通过' if rm['passed'] else '未通过；边界文件为候选结果'
    report=f'''# 老庄闸前渠段，双边界20%硬限幅

时段：{start} 至 {end}。水位验收：{accepted}。

|指标|原边界|重建后|要求|
|---|---:|---:|---:|
|RMSE / m|{bm['RMSE_m']:.4f}|{rm['RMSE_m']:.4f}|—|
|NRMSE|{bm['NRMSE']:.2%}|{rm['NRMSE']:.2%}|<5%|
|NSE|{bm['NSE']:.4f}|{rm['NSE']:.4f}|>0.9|
|R²|{bm['R2']:.4f}|{rm['R2']:.4f}|>0.9|
|KGE|{bm['KGE']:.4f}|{rm['KGE']:.4f}|>0.9|

分水使用action_td.csv中d0已知边界。上游二级站出口使用action_obs_td.csv观测流量，保持不调节。开度使用gate_e_td.csv中老庄记录，mm转m。分别从±10%放松至±20%，始终相对当时原值限幅，原值为0则保持0。五周期平均有符号分水执行误差>2%才允许调开度；不满足条件时使用原开度。未放宽水位指标。

实际最大分水调整={summary['max_actual_flow_adjustment_fraction']:.2%}，实际最大开度调整={summary['max_actual_opening_adjustment_fraction']:.2%}。开度允许调节周期{reconstruction['opening_eligible_cycles']}，实际调整周期{reconstruction['opening_adjusted_cycles']}。未观测到目标水位的周期{reconstruction['missing_water_targets']}，不计入水位评分；仍推进和记录该周期。每个试算恢复完整周期初状态和时钟，选定控制推进一次，回放一致性和20%约束均通过检查。

使用最新断面文件，包括DYD003从DYD002借用边坡1:1、DYD034从DYD033借用矩形尺寸。DLL成功导入{reconstruction['section_import']['loaded_sections']}个断面。原底高程保留。DLL和断面文件哈希见summary.json，避免与补全前结果混淆。记录中出现的最大开度用于生成数值上界，未据此认定机械行程；历史异常值也未静默改写。

输出：laozhuang_boundaries.csv（上游流量、d0分水流量、老庄开度），baseline_audit.csv、reconstruction_audit.csv、comparison.png、summary.json。
'''
    (out/'report.md').write_text(report,encoding='utf-8')

if __name__=='__main__':main()
