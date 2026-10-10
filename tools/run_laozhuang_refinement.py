"""Second round: enlarge only first-round >10% depth-error cycles to +/-30%."""
from pathlib import Path
from datetime import datetime
import hashlib,json
import numpy as np
import pandas as pd
from run_fujialing_reconstruction_comparison import ROOT,run,metrics

def main():
    first=ROOT/'data/dayudu/output/laozhuang_20pct_tailwater'
    out=ROOT/'data/dayudu/output/laozhuang_round2_refinement';out.mkdir(parents=True,exist_ok=True)
    summary1=json.loads((first/'summary.json').read_text(encoding='utf-8'))
    assert summary1['dll_sha256']==hashlib.sha256((ROOT/'tools/OcisMILPNet.dll').read_bytes()).hexdigest()
    assert summary1['section_file_sha256']==hashlib.sha256((ROOT/'data/dayudu/input/CrossSection_dayudu_hydraulic_tables.geojson').read_bytes()).hexdigest()
    d=pd.read_csv(first/'reconstruction_audit.csv');obs=d.h_target_m.to_numpy();sim=d.h_sim_m.to_numpy()
    valid=np.isfinite(obs)&np.isfinite(sim)
    error=np.full(len(d),np.nan);nonzero=valid&(abs(obs)>0)
    error[nonzero]=abs(sim[nonzero]-obs[nonzero])/abs(obs[nonzero])
    error[valid&(obs==0)]=np.where(sim[valid&(obs==0)]==0,0,np.inf)
    d['first_round_depth_relative_error']=error;d['refinement_trigger']=valid&(error>.1)
    d.to_csv(out/'first_round_refinement_mask.csv',index=False,encoding='utf-8-sig')
    intervals=[];begin=None
    for k in range(len(d)+1):
        flag=k<len(d) and bool(d.refinement_trigger.iloc[k])
        if flag and begin is None:begin=k
        if not flag and begin is not None:
            intervals.append({'start':d.time.iloc[begin],'end':d.state_time.iloc[k-1],'cycles':k-begin,'flow_cap':.3});begin=None
    pd.DataFrame(intervals).to_csv(out/'refinement_intervals.csv',index=False,encoding='utf-8-sig')
    print(f'Round 2: {int(d.refinement_trigger.sum())}/{len(d)} cycles, {len(intervals)} intervals, cap +/-30%, opening frozen',flush=True)
    result=run(ROOT/'data/dayudu',out,datetime.fromisoformat(d.time.iloc[0]),len(d),True,
        summary1['input_derived_opening_ceiling_m'],pool_id=0,diversion_series_name=None,use_observed_source=True,
        opening_file='gate_e_td.csv',amplitude_cap=.3,relative_opening=True,
        boundary_filename='laozhuang_round2_boundaries.csv',downstream_stage_file='stage2_td.csv',refinement=d)
    r=pd.read_csv(out/'reconstruction_audit.csv');mask=d.refinement_trigger.to_numpy()
    np.testing.assert_allclose(r.loc[~mask,'q_command_m3s'],d.loc[~mask,'q_command_m3s'],atol=1e-10,rtol=0)
    np.testing.assert_allclose(r.opening_command_m,d.opening_command_m,atol=1e-10,rtol=0)
    assert (abs(r.q_command_m3s-d.q_base_known_m3s)<=.3*abs(d.q_base_known_m3s)+1e-9).all()
    assert (out/'downstream_stage_payload.json').read_bytes()==(first/'downstream_stage_payload.json').read_bytes()
    gate=result['downstream_boundary_report']['gates'][0];assert abs(gate['cumulative_mass_residual_m3'])<.1,gate
    first_files={name:hashlib.sha256((first/name).read_bytes()).hexdigest() for name in ['summary.json','reconstruction_audit.csv','laozhuang_boundaries.csv','downstream_stage_payload.json']}
    summary={'first_round_path':str(first),'first_round_files_sha256':first_files,'refinement_flow_tiers':[.3],
        'refinement_flow_max':.3,'amplitude_choice':'user requested start without specifying enlarged cap; minimum additional tier +/-30% adopted and announced',
        'trigger':'first-round abs(h_sim-h_obs)/abs(h_obs)>0.10; finite valid targets only',
        'triggered_cycles':int(mask.sum()),'interval_count':len(intervals),'first_round_metrics':summary1['reconstruction']['metrics'],
        'second_round':result,'local_first_round_metrics':metrics(d.loc[mask,'h_target_m'],d.loc[mask,'h_sim_m']),
        'local_second_round_metrics':metrics(r.loc[mask,'h_target_m'],r.loc[mask,'h_sim_m']),
        'nontrigger_controls_unchanged':True,'opening_unchanged':True,'tailwater_unchanged':True,
        'dll_sha256':summary1['dll_sha256'],'section_file_sha256':summary1['section_file_sha256']}
    summary['global_RMSE_improved']=result['metrics']['RMSE_m']<summary1['reconstruction']['metrics']['RMSE_m']
    summary['accepted']=result['metrics']['passed']
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
