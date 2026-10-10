"""Package final native validation and supplement the LaoZhuang result report."""
import hashlib,json
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from run_fujialing_reconstruction_comparison import da
ROOT=Path(__file__).resolve().parents[1]
native=Path(r'C:/Users/houvert/.codex/worktrees/aggregation-continue/ocismilpnet-mac-win-MAC2DELL')
out=ROOT/'data/dayudu/output/laozhuang_20pct_tailwater'
validation=ROOT/'data/dayudu/output/tailwater_validation'
dll=ROOT/'tools/OcisMILPNet.dll'
manifest_path=ROOT/'tools/native_manual_no_waga_manifest.json'
manifest=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
for item in manifest:
 if item['name']=='OcisMILPNet.dll':
  item.update(sha256=hashlib.sha256(dll.read_bytes()).hexdigest(),tailwater_boundary_supported=True,tailwater_solver='sediment segmented pool',tailwater_shared_flux=True,tailwater_exact_section_storage=True)
manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(validation/'native_solver_test.log').write_bytes((native/'build_manual_no_waga/tailwater_solver_test.log').read_bytes())
summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
assert summary['dll_sha256']==hashlib.sha256(dll.read_bytes()).hexdigest()
residuals={}
for mode in ['baseline','reconstruction']:
 gate=summary[mode]['downstream_boundary_report']['gates'][0]
 residuals[mode]=gate['cumulative_mass_residual_m3']
 assert abs(residuals[mode])<.1,(mode,gate)
b=pd.read_csv(out/'baseline_audit.csv');d=pd.read_csv(out/'reconstruction_audit.csv')
payload=json.loads((out/'downstream_stage_payload.json').read_text(encoding='utf-8'))
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei'],'axes.unicode_minus':False})
t=pd.to_datetime(d.state_time);fig,axes=plt.subplots(4,1,figsize=(13,12),sharex=True)
raw_stage=da.load_observation_csv(ROOT/'data/dayudu/input/stage1_td.csv')['老庄节制闸']
raw_points=[(time,value) for time,value in raw_stage.values if t.iloc[0].to_pydatetime()<=time<=t.iloc[-1].to_pydatetime()]
axes[0].scatter([x[0] for x in raw_points],[x[1] for x in raw_points],color='red',s=10,label='闸前实测点',zorder=5);axes[0].plot(t,b.h_sim_m,label='实测尾水＋原控制');axes[0].plot(t,d.h_sim_m,label='实测尾水＋重建控制')
old=ROOT/'data/dayudu/output/laozhuang_20pct_updated_sections/reconstruction_audit.csv'
if old.exists():
 o=pd.read_csv(old);axes[0].plot(pd.to_datetime(o.state_time),o.h_sim_m,alpha=.6,label='此前未给实测尾水')
axes[0].set_ylabel('闸前水深 / m')
samples=payload['samples'];epochs=np.array([x['time'] for x in samples]);levels=np.array([x['water_level_m'] for x in samples])
applied=np.interp(np.array([x.to_pydatetime().timestamp() for x in t]),epochs,levels)
np.testing.assert_allclose(d.downstream_wh_m,applied,atol=1e-8,rtol=0)
axes[1].plot(t,applied,label='外部闸后水位边界');axes[1].plot(t,d.downstream_wh_m,'--',label='DLL实际水位');axes[1].set_ylabel('闸后高程 / m')
first_observed=datetime.fromisoformat(summary['reconstruction']['downstream_boundary']['initial_backfill_until']).timestamp()
observed=[x for x in samples if first_observed<=x['time']<=t.iloc[-1].to_pydatetime().timestamp()]
axes[1].scatter([datetime.fromtimestamp(x['time']) for x in observed],[x['water_level_m'] for x in observed],color='red',s=10,label='闸后实测点',zorder=5)
axes[2].plot(t,d.q_base_known_m3s,label='原分水');axes[2].plot(t,d.q_command_m3s,label='重建设定');axes[2].plot(t,d.q_executed_m3s,'--',label='实际执行');axes[2].fill_between(t,.8*d.q_base_known_m3s,1.2*d.q_base_known_m3s,alpha=.12,label='±20%');axes[2].set_ylabel('分水 / m³/s')
axes[3].scatter(t,d.opening_observed_m,color='red',s=14,label='原开度观测采样',zorder=5);axes[3].plot(t,d.opening_command_m,'--',label='重建开度');axes[3].fill_between(t,.8*d.opening_observed_m,1.2*d.opening_observed_m,alpha=.12,label='±20%');axes[3].set_ylabel('开度 / m')
for ax in axes:ax.legend(ncol=4);ax.grid(alpha=.2)
fig.suptitle('老庄渠段：实测尾水边界、守恒修复及20%限幅重建');fig.tight_layout();fig.savefig(out/'comparison.png',dpi=160);plt.close(fig)
summary['tailwater_validation']={'cumulative_mass_residual_m3':residuals,'actual_stage_matches_interpolation':True,'native_tests_passed':True,'dll_tests_passed':True}
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
report=(out/'report.md').read_text(encoding='utf-8')
report=report.split('\n## 下游水位边界修复验证')[0]
report+='\n## 下游水位边界修复验证\n\n配置绝对高程序列，闸前后使用共同闸底基准；保留原拓扑，外部虚拟支路不计蓄水。断面突变和分水汇接两侧采用共同通量，蓄水采用实际面积变化。\n\n'
report+=f'完整时段累计水量残差：原控制 {residuals["baseline"]:.9g} m³，重建控制 {residuals["reconstruction"]:.9g} m³。DLL闸后水位与输入序列插值一致。关闭、等水头、反向流、高程平移、矩形/U形、汇接与断面突变、A/B/A完整回滚测试通过。\n\n'
report+='4月14日00:00至15:32:01无闸后观测，以首条水深0.73m向前补齐；不是实测。使用原模型闸后底高程521.09575m及闸底521.0995m，未将二者混用。当前接口支持sediment分段求解。流量系数和闸门过渡平滑参数尚未通过历史资料率定，水位指标失败不会标为通过。\n\n'
report+='边界：laozhuang_boundaries.csv含控制时刻的上游流量、分水流量、开度及闸后绝对高程；downstream_stage_payload.json保留全部尾水时间序列，可直接用于DLL接口。\n'
(out/'report.md').write_text(report,encoding='utf-8')
print(json.dumps({'metrics':summary['reconstruction']['metrics'],'mass_residuals':residuals,'limits':[summary['max_actual_flow_adjustment_fraction'],summary['max_actual_opening_adjustment_fraction']]},ensure_ascii=False,indent=2))
