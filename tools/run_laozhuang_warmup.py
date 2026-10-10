# -*- coding: utf-8 -*-
"""Replay the adopted LaoZhuang boundaries from an observation-conditioned hot start."""
import argparse,ctypes,hashlib,json
from datetime import datetime
import numpy as np
import pandas as pd
from run_fujialing_reconstruction_comparison import ROOT,run

PREVIOUS=ROOT/'data/dayudu/output/laozhuang_delivery_5pct'

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--hours',type=int,default=168)
 parser.add_argument('--warmup-seconds',type=int,default=21600)
 parser.add_argument('--initial-tolerance-m',type=float,default=.005)
 parser.add_argument('--max-warmup-seconds',type=int,default=86400)
 parser.add_argument('--kp',type=float,default=3)
 parser.add_argument('--ki',type=float,default=.5)
 parser.add_argument('--kd',type=float,default=.1)
 parser.add_argument('--output',type=str,default='data/dayudu/output/laozhuang_pid_warmup')
 args=parser.parse_args();out=ROOT/args.output;out.mkdir(parents=True,exist_ok=True)
 seed=pd.read_csv(PREVIOUS/'reconstruction_audit.csv').iloc[:args.hours].copy()
 seed['q_base_known_m3s']=seed.q_base_planned_m3s
 seed['first_round_depth_relative_error']=abs(seed.h_sim_m-seed.h_target_m)/abs(seed.h_target_m)
 seed['refinement_trigger']=False
 def verify(cl,execute,continuous,summary):
  compare=cl._dll.compare_snapshot_sim
  compare.argtypes=[ctypes.c_void_p,ctypes.c_char_p];compare.restype=ctypes.c_int
  final_clock=json.loads(cl.get_time_param());blocks=[]
  for begin in range(0,len(seed),6):
   cl.set_states(f'periodic_{begin}')
   assert compare(cl.obj,f'periodic_{begin}'.encode())==1
   end=min(begin+6,len(seed))
   for k in range(begin,end):
    row=seed.iloc[k]
    r=execute(k,float(row.q_command_m3s),float(row.opening_command_m),float(row.source_Q_m3s))
    assert abs(r['h_up']-continuous.h_sim_m.iloc[k])<1e-9
    assert abs(r['diversion_Q']-continuous.q_executed_m3s.iloc[k])<1e-9
   assert compare(cl.obj,f'periodic_{end}'.encode())==1
   blocks.append(dict(from_cycle=begin,to_cycle=end,full_native_state_equal=True))
  assert json.loads(cl.get_time_param())==final_clock
  report=dict(passed=True,interval_cycles=6,blocks=blocks,scope='same DLL object memory',
              formal_initial_contains_warmup_state=True)
  (out/'snapshot_equivalence.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
  np.testing.assert_array_equal(continuous.q_command_m3s,seed.q_command_m3s)
  np.testing.assert_array_equal(continuous.opening_command_m,seed.opening_command_m)
  assert abs(continuous.boundary_cumulative_mass_residual_m3.iloc[-1])<.1
  print('PASS: hot-start snapshot replay equals continuous state; controls unchanged',flush=True)
  return report
 result=run(ROOT/'data/dayudu',out,datetime.fromisoformat(seed.time.iloc[0]),len(seed),True,
  float(seed.opening_observed_m.max())*1.2,pool_id=0,diversion_series_name=None,
  use_observed_source=True,opening_file='gate_e_td.csv',amplitude_cap=.9,relative_opening=True,
  boundary_filename='laozhuang_boundaries.csv',downstream_stage_file='stage2_td.csv',
  refinement=seed,snapshot_interval=6,after_run=verify,
  gate_mu={'Uef':.63,'Ues':.63,'Ucf':.315,'Ucs':.84},
  warmup=dict(duration_seconds=args.warmup_seconds,max_duration_seconds=args.max_warmup_seconds,
              step_seconds=300,depth_tolerance_m=args.initial_tolerance_m,kp=args.kp,ki=args.ki,kd=args.kd))
 result['dll_sha256']=hashlib.sha256((ROOT/'tools/OcisMILPNet.dll').read_bytes()).hexdigest()
 result['reference_audit']=str(PREVIOUS/'reconstruction_audit.csv')
 (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(dict(metrics=result['metrics'],warmup=result['warmup']),ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
