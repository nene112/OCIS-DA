"""Continuous replay, named snapshot equivalence, then pre-window flow refinement."""
import argparse,ctypes,json,hashlib
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from run_fujialing_reconstruction_comparison import ROOT,run,metrics

CASE=ROOT/'data/dayudu'
PREVIOUS=CASE/'output/laozhuang_gate_coefficient_trials/scale_1.05'
FIRST=CASE/'output/laozhuang_20pct_tailwater'
OUT=CASE/'output/laozhuang_delivery_5pct'
INTERVAL=6;PREFIX=5
import itertools
TIERS=itertools.count(6)
MU={'Uef':.63,'Ues':.63,'Ucf':.315,'Ucs':.84}

def main():
 global OUT
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--warmup-seconds',type=int,default=21600)
 parser.add_argument('--no-warmup',action='store_true',help='explicitly reproduce the historical cold-start experiment')
 parser.add_argument('--initial-tolerance-m',type=float,default=.005)
 parser.add_argument('--output',type=Path,default=None)
 args=parser.parse_args()
 if args.no_warmup:args.warmup_seconds=0
 if args.output is not None:OUT=args.output if args.output.is_absolute() else ROOT/args.output
 elif args.warmup_seconds:OUT=CASE/'output/laozhuang_delivery_5pct_pid_warmup'
 OUT.mkdir(parents=True,exist_ok=True)
 first=json.loads((FIRST/'summary.json').read_text(encoding='utf-8'))
 seed=pd.read_csv(PREVIOUS/'reconstruction_audit.csv')
 seed['q_base_known_m3s']=seed.q_base_planned_m3s
 seed['first_round_depth_relative_error']=abs(seed.h_sim_m-seed.h_target_m)/abs(seed.h_target_m)
 seed['refinement_trigger']=False
 original_intervals=pd.read_csv(CASE/'output/laozhuang_round2_refinement/refinement_intervals.csv')
 source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [PREVIOUS/'reconstruction_audit.csv',FIRST/'reconstruction_audit.csv']}

 def refine(cl,execute,continuous,base_summary):
  compare=cl._dll.compare_snapshot_sim;compare.argtypes=[ctypes.c_void_p,ctypes.c_char_p];compare.restype=ctypes.c_int
  def same(name):return compare(cl.obj,name.encode())==1
  def clock():return json.loads(cl.get_time_param())
  final_clock=clock();native_snapshot_count=len(seed)//INTERVAL+1
  # Every saved checkpoint is restored and compared before replaying its next six cycles.
  # Compare the entire native node/gate structs, clock and boundary/individual-opening maps.
  verification=[]
  for begin in range(0,len(seed),INTERVAL):
   cl.set_states(f'periodic_{begin}');assert same(f'periodic_{begin}')
   end=min(begin+INTERVAL,len(seed))
   for k in range(begin,end):
    row=seed.iloc[k];result=execute(k,float(row.q_command_m3s),float(row.opening_command_m),float(row.source_Q_m3s))
    assert abs(result['h_up']-continuous.h_sim_m.iloc[k])<1e-9
    assert abs(result['diversion_Q']-continuous.q_executed_m3s.iloc[k])<1e-9
   assert same(f'periodic_{end}'),f'snapshot resume differs at {end}'
   verification.append({'from_cycle':begin,'to_cycle':end,'full_native_state_equal':True})
  assert clock()==final_clock
  (OUT/'snapshot_equivalence.json').write_text(json.dumps({'passed':True,'blocks':verification,'tolerance_m':1e-9,'native_node_gate_comparison':'exact full-struct equality','interval_cycles':INTERVAL},indent=2),encoding='utf-8')
  print('PASS: every six-cycle snapshot replay equals continuous native state',flush=True)
  index=[{'cycle':k,'name':f'periodic_{k}','elapsed_seconds':k*3600,'scope':'same DLL object only; index is not serialized state'} for k in range(0,len(seed)+1,INTERVAL)]
  (OUT/'snapshot_index.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
  best=continuous.copy();intervals=[]
  for _,entry in original_intervals.iterrows():
   mask=(best.time>=entry.start)&(best.time<entry.end)
   indices=np.flatnonzero(mask.to_numpy())
   if not len(indices):continue
   target=best.loc[mask,'h_target_m'];sim=best.loc[mask,'h_sim_m'];valid=target.notna()&sim.notna()
   score=metrics(target,sim);bias=float((sim[valid]-target[valid]).mean())
   if bias>0 and not score['passed']:
    intervals.append({'start_cycle':int(indices[0]),'end_cycle':int(indices[-1]+1),
        'control_start_cycle':max(0,int(indices[0])-PREFIX),'initial_mean_depth_bias_m':bias,
        'status':'active','initial_metrics':score})
  pd.DataFrame(intervals).to_csv(OUT/'target_intervals.csv',index=False,encoding='utf-8-sig')
  print(f'High-depth failing intervals: {len(intervals)}; prefix {PREFIX}; start 60%, range grows by factor1.5; execution threshold5%',flush=True)
  history=[];all_used=np.zeros(len(seed),bool)
  plateau=0;has_accepted_snapshots=False
  for tier in TIERS:
   cap=round(.6*1.5**(tier-6),10)
   active=[i for i in intervals if i['status']=='active']
   if not active:break
   mask=np.zeros(len(seed),bool)
   for item in active:mask[item['control_start_cycle']:item['end_cycle']]=True
   all_used|=mask
   begin=int(np.flatnonzero(mask)[0]) if mask.any() else 0
   checkpoint=begin//INTERVAL*INTERVAL
   # Native periodic snapshots before this earliest changed control remain valid.
   # If prior iterations changed controls before this checkpoint, use their snapshots.
   name=f'accepted_{checkpoint}' if has_accepted_snapshots else f'periodic_{checkpoint}'
   cl.set_states(name);assert same(name)
   rows=best.iloc[:checkpoint].to_dict('records');trials=0;failed_probes=[]
   for k in range(checkpoint,len(seed)):
    old=best.iloc[k];original=float(old.q_base_planned_m3s);qseed=float(old.q_command_m3s)
    opening=float(old.opening_command_m);source=float(old.source_Q_m3s);target=old.h_target_m
    if k%INTERVAL==0:cl.save_states(f'candidate_{k}')
    cl.save_states();before=clock();chosen=qseed;chosen_result=None
    if mask[k] and pd.notna(target):
     lo=max(0.,(1-cap)*original);hi=(1+cap)*original
     candidates=[]
     for q in sorted({qseed,hi}):
      cl.set_states();assert clock()==before
      try:
       result=execute(k,q,opening,source);trials+=1
      except RuntimeError as error:
       failed_probes.append({'cycle':k,'q':q,'error':str(error)})
       continue
      candidates.append((abs(result['h_up']-target),abs(q-qseed),q,result))
     if not candidates:raise RuntimeError('no stable candidate at cycle '+str(k))
     chosen_item=max(candidates,key=lambda x:x[2]);chosen=chosen_item[2];chosen_result=chosen_item[3]
     if not candidates:raise RuntimeError('no stable candidate at cycle '+str(k))
     if False:
      hl=candidates[0][3]['h_up']
      for _ in range(6):
       q=(lo+hi)/2;cl.set_states();assert clock()==before
       result=execute(k,q,opening,source);trials+=1
       candidate=(abs(result['h_up']-target),abs(q-qseed),q,result)
       if candidate[:2]<chosen_item[:2]:chosen_item=candidate;chosen=q;chosen_result=result
       if (hl-target)*(result['h_up']-target)<=0:hi=q
       else:lo=q;hl=result['h_up']
    cl.set_states();result=execute(k,chosen,opening,source)
    assert clock()['solver_elapsed_seconds']==before['solver_elapsed_seconds']+3600
    if chosen_result is not None:assert abs(result['h_up']-chosen_result['h_up'])<1e-9
    row=old.to_dict();row.update(q_command_m3s=chosen,q_executed_m3s=result['diversion_Q'],h_sim_m=result['h_up'],
       h_down_sim_m=result['h_down'],gate_Q_sim_m3s=result['gate_Q'],gate_capacity_Q_m3s=result['capacity_Q'],
       boundary_out_volume_m3=result['boundary_out_volume_m3'],boundary_mass_residual_m3=result['boundary_mass_residual_m3'],
       boundary_cumulative_mass_residual_m3=result['boundary_cumulative_mass_residual_m3'],downstream_wh_m=result['downstream_wh_m'],
       refinement_trigger=bool(mask[k]),snapshot_refinement_cap=cap if mask[k] else 0)
    actual=result['diversion_Q'];row['diversion_relative_error']=(chosen-actual)/actual if actual>1e-9 else (0. if chosen<=1e-9 else float('inf'))
    rows.append(row)
    if k%24==0:print(f'cap {cap:.0%}: cycle {k}/{len(seed)}, h={result["h_up"]:.4f}',flush=True)
   candidate=pd.DataFrame(rows);cl.save_states(f'candidate_{len(seed)}')
   score=metrics(candidate.h_target_m,candidate.h_sim_m)
   prev_score=metrics(best.h_target_m,best.h_sim_m)
   # Preserve the better full-period candidate; record every tier even when rejected.
   accepted=score['RMSE_m']<=prev_score['RMSE_m']+1e-12
   trial_report=[]
   for item in active:
    part=candidate.iloc[item['start_cycle']:item['end_cycle']]
    local=metrics(part.h_target_m,part.h_sim_m)
    execution=float(part.diversion_relative_error.mean())
    reason='execution_error_over_5pct' if execution>.05 else ('water_accuracy_passed' if local['passed'] else 'continue')
    if reason!='continue':item['status']=reason
    trial_report.append({'start_cycle':item['start_cycle'],'end_cycle':item['end_cycle'],'flow_execution_mean_relative_error':execution,'metrics':local,'stop_reason':reason})
   if any(x['flow_execution_mean_relative_error']>.05 for x in trial_report):accepted=False
   candidate.to_csv(OUT/f'tier_{int(cap*100)}_audit.csv',index=False,encoding='utf-8-sig')
   history.append({'cap':cap,'snapshot_cycle':checkpoint,'trials':trials,'metrics':score,'accepted':accepted,'intervals':trial_report,'failed_probes':failed_probes})
   identical=bool(failed_probes) and all(x['flow_execution_mean_relative_error']<=.05 for x in trial_report)
   plateau=plateau+1 if identical else 0
   if accepted:
    best=candidate;has_accepted_snapshots=True
    # Copy accepted snapshot states into stable names, preserving the candidate series.
    for k in range(checkpoint,len(seed)+1,INTERVAL):
     cl.set_states(f'candidate_{k}');cl.save_states(f'accepted_{k}')
   else:
    # Keep the best full-period branch but allow wider future search.
    pass
   print(f'cap {cap:.0%} finished, accepted={accepted}, RMSE={score["RMSE_m"]:.6f}',flush=True)
   (OUT/'progress.json').write_text(json.dumps({'history':history,'intervals':intervals},ensure_ascii=False,indent=2),encoding='utf-8')
   best.to_csv(OUT/'best_so_far_audit.csv',index=False,encoding='utf-8-sig')
   if plateau>=3:
    for item in intervals:
     if item['status']=='active':item['status']='search_plateau_without_5pct_execution_error'
    break
  best.to_csv(OUT/'reconstruction_audit.csv',index=False,encoding='utf-8-sig')
  boundaries=pd.read_csv(PREVIOUS/'fixed_boundaries.csv');boundaries['d0_Q_m3s']=best.q_command_m3s.to_numpy()
  boundaries.to_csv(OUT/'laozhuang_boundaries.csv',index=False,encoding='utf-8-sig')
  (OUT/'downstream_stage_payload.json').write_bytes((PREVIOUS/'downstream_stage_payload.json').read_bytes())
  np.testing.assert_allclose(best.opening_command_m,seed.opening_command_m,atol=1e-10,rtol=0)
  np.testing.assert_allclose(best.loc[~all_used,'q_command_m3s'],seed.loc[~all_used,'q_command_m3s'],atol=1e-10,rtol=0)
  assert abs(best.boundary_cumulative_mass_residual_m3.iloc[-1])<.1
  return {'snapshot_equivalence_passed':True,'interval_cycles':INTERVAL,'prefix_cycles':PREFIX,'flow_tiers':[entry['cap'] for entry in history],'execution_error_threshold':.05,
       'storage':'object-owned memory snapshots; snapshot_index.json is metadata only',
       'original_metrics':metrics(seed.h_target_m,seed.h_sim_m),'continuous_reference_metrics':base_summary['metrics'],
       'final_metrics':metrics(best.h_target_m,best.h_sim_m),'tier_history':history,'intervals':intervals,
       'mass_residual_m3':float(best.boundary_cumulative_mass_residual_m3.iloc[-1]),'opening_unchanged':True,
       'nonwindow_flow_unchanged':True}

 result=run(CASE,OUT,datetime.fromisoformat(seed.time.iloc[0]),len(seed),True,first['input_derived_opening_ceiling_m'],
      pool_id=0,diversion_series_name=None,use_observed_source=True,opening_file='gate_e_td.csv',amplitude_cap=.6,
      relative_opening=True,downstream_stage_file='stage2_td.csv',refinement=seed,snapshot_interval=INTERVAL,after_run=refine,gate_mu=MU,
      warmup=dict(duration_seconds=args.warmup_seconds,step_seconds=300,depth_tolerance_m=args.initial_tolerance_m) if args.warmup_seconds else None)
 for path,digest in source_hashes.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
 result.update(dll_sha256=hashlib.sha256((ROOT/'tools/OcisMILPNet.dll').read_bytes()).hexdigest(),source_files_sha256=source_hashes)
 (OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(result['snapshot_refinement'],ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
