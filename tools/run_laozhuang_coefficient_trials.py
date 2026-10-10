"""Fixed boundary full-period gate coefficient sensitivity experiment."""
import argparse,json,hashlib
from datetime import datetime
import numpy as np
import pandas as pd
from run_fujialing_reconstruction_comparison import ROOT,run

case=ROOT/'data/dayudu';previous=case/'output/laozhuang_snapshot_refinement'
out=case/'output/laozhuang_gate_coefficient_trials';out.mkdir(parents=True,exist_ok=True)
seed=pd.read_csv(previous/'reconstruction_audit.csv')
seed['q_base_known_m3s']=seed.q_base_planned_m3s
seed['first_round_depth_relative_error']=abs(seed.h_sim_m-seed.h_target_m)/abs(seed.h_target_m)
seed['refinement_trigger']=False
old=json.loads((previous/'summary.json').read_text(encoding='utf-8'))
original=old['model_gate']['mu'];records=[];details={}
parser=argparse.ArgumentParser();parser.add_argument('--refine',action='store_true');args=parser.parse_args()
if args.refine:
 existing=json.loads((out/'summary.json').read_text(encoding='utf-8'))
 records=pd.read_csv(out/'coefficient_sweep.csv').to_dict('records');details=existing['details']
for scale in ([1.03,1.05] if args.refine else [1.,.8,.9,1.1,1.2]):
 folder=out/f'scale_{scale:.2f}';folder.mkdir(exist_ok=True)
 mu={key:value*scale for key,value in original.items()}
 result=run(case,folder,datetime.fromisoformat(seed.time.iloc[0]),len(seed),True,1.2,
     pool_id=0,diversion_series_name=None,use_observed_source=True,opening_file='gate_e_td.csv',
     amplitude_cap=.5,relative_opening=True,downstream_stage_file='stage2_td.csv',refinement=seed,gate_mu=mu,
     boundary_filename='fixed_boundaries.csv')
 audit=pd.read_csv(folder/'reconstruction_audit.csv')
 np.testing.assert_allclose(audit.q_command_m3s,seed.q_command_m3s,atol=1e-10,rtol=0)
 np.testing.assert_allclose(audit.opening_command_m,seed.opening_command_m,atol=1e-10,rtol=0)
 assert (folder/'downstream_stage_payload.json').read_bytes()==(previous/'downstream_stage_payload.json').read_bytes()
 mass=result['downstream_boundary_report']['gates'][0]['cumulative_mass_residual_m3'];assert abs(mass)<.1
 if scale==1:np.testing.assert_allclose(audit.h_sim_m,seed.h_sim_m,atol=1e-9,rtol=0)
 record={'scale':scale,**mu,**result['metrics'],'mass_residual_m3':mass}
 records.append(record);details[str(scale)]=result
 pd.DataFrame(records).to_csv(out/'coefficient_sweep.csv',index=False,encoding='utf-8-sig')
 print(f'coefficient scale {scale}: RMSE={record["RMSE_m"]:.6f}',flush=True)
best=min(records,key=lambda r:r['RMSE_m'])
summary={'original_coefficients':original,'tested_scales':[r['scale'] for r in records],'best':best,
 'details':details,'boundary_controls_frozen':True,'coefficient_policy':'four existing coefficients scaled together, constant throughout whole period; all <1',
 'status':'water-depth fit sensitivity; no independent discharge validation; no baseline overwrite',
 'dll_sha256':hashlib.sha256((ROOT/'tools/OcisMILPNet.dll').read_bytes()).hexdigest()}
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(best,ensure_ascii=False,indent=2))
