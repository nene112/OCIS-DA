# coding: utf-8
# coding: utf-8
"""LaoZhuang replay with the five-cycle signed flow-execution guard."""
from collections import deque
import math
import sys,json,ctypes
from pathlib import Path
from datetime import datetime,timedelta
import numpy as np,pandas as pd
sys.path[:0]=['tools','tools/GPT-Reconstruction']
import reach_missing_data_assimilation as da
from run_dayudu_reconstruction import _config_for_reach

class FlowGuard:
 def __init__(self):self.errors=deque(maxlen=5)
 def add(self,requested,simulated):
  if abs(simulated)<1e-9:err=0.0 if abs(requested)<1e-9 else float('inf')
  else:err=(requested-simulated)/simulated
  self.errors.append(err)
  mean=float(np.mean(self.errors)) if len(self.errors)==5 else None
  return err,mean,mean is not None and mean>0.10+1e-12

def main():
 p=Path('data/dayudu').resolve();out=p/'output/laozhuang_five_cycle_guard';out.mkdir(parents=True,exist_ok=True)
 start=datetime(2026,4,14);end=datetime(2026,4,21);reach=da.discover_reaches(p)[0];config=_config_for_reach(1.5)
 b=da.load_observation_csv(p/'input/action_td.csv');es=da.load_observation_csv(p/'input/gate_e_td.csv')['老庄节制闸'];hs=da.load_observation_csv(p/'input/stage1_td.csv')['老庄节制闸'];upobs=da.load_observation_csv(p/'input/action_obs_td.csv')['二级站出口']
 def held(s,t):
  v=s.value_at(t,86400,'causal')
  return s.value_at(t,300,'nearest') if v is None else v
 initial=hs.value_at(start,300,'nearest');cfg=da.AssimilationConfig(initial_water_depth=initial)
 clients,copies=da._load_isolated_clients(None,1,da._build_runtime_config(p/'OCIS_dataConfig.json',p,reach,cfg),cfg);cl=clients[0]
 cl.set_outputfile_Label(0);cl.Auto_correct_endpoint();cl.Auto_correct_gates_calculationType()
 _,names=da._parse_gate_info(cl.get_gate_info_sim());source=names[da._normalize_name('二级站出口')];gate=names[da._normalize_name('老庄节制闸')];div=names['d0']
 ctype=cl._dll.set_GatesFlow_calculationType_byID_sim;ctype.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int];ctype.restype=None;ctype(cl.obj,int(div),0);ctype(cl.obj,int(gate),1)
 ea=cl._dll.set_GatesFlow_e_byID_sim_array;ea.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(ctypes.c_double),ctypes.c_int];ea.restype=None
 guard=FlowGuard();eligible=False;level=0.;offset=0.;previous=None;previous_h=None;previous_depth_error=None;rows=[]
 try:
  for k in range(169):
   t=start+timedelta(hours=k);baseq=held(b['d0'],t);q=float(np.clip(2*baseq,0,config.q_max));eb=held(es,t)/1000;target=hs.value_at(t,1800,'linear');before_offset=offset;activated=eligible and k<168
   if activated and previous_h is not None and target is not None:
    error=previous_h-target
    if level==0:level=config.check_gate_amplitude_start
    elif previous_depth_error is not None and abs(error)>=abs(previous_depth_error)-1e-6:level=min(config.check_gate_amplitude_max,level+config.check_gate_amplitude_step)
    # One physically bounded tier per interaction; no unbounded within-cycle search.
    correction=float(np.clip(config.kp*error,-level*1.5,level*1.5));offset+=correction;previous_depth_error=error
   elif not eligible:level=0.
   anchor=float(np.clip(eb+before_offset,0,1.5));ec=float(np.clip(eb+offset,0,1.5));up=held(upobs,t)
   cl.update_BC_sim_only(k);cl.set_GatesFlow_byID_sim(source,float(up));cl.set_GatesFlow_byID_sim(div,q);cl.set_GatesFlow_byID_sim(gate,10.3);cl.set_GatesFlow_e_byID_sim(gate,ec);ea(cl.obj,int(gate),(ctypes.c_double*1)(ec),1);cl.set_GatesFlow_mu_byID_sim_multi(gate,{'Ues':.6,'Uef':.6});cl.stepSolver_sim_Roe_only_pool(k,0)
   get=lambda typ,idx:da._extract_gate_value(da._safe_model_data(cl,typ),idx)
   simulated=get('gates_Q',div);err=mean=None;eligible=False
   if previous is not None:err,mean,eligible=guard.add(previous,simulated)
   depth=get('gates_h1',gate);state_time=t+timedelta(hours=1)
   rows.append({'time':t.isoformat(),'state_time':state_time.isoformat(),'q_base':baseq,'q_command':q,'q_reported_previous_cycle':simulated,'previous_cycle_q_command':previous,'previous_cycle_signed_relative_error':err,'completed_cycle_window_count':len(guard.errors),'past5_mean_signed_relative_error':mean,'opening_eligible_next_cycle':eligible,'opening_stage_active_this_cycle':activated,'opening_amplitude_tier':level,'opening_anchor_m':anchor,'e_observed_m':eb,'e_command_m':ec,'e_executed_m':get('gates_e',gate),'h_sim':depth,'h_target_at_state_time':hs.value_at(state_time,1800,'linear'),'source_Q':up})
   previous=q;previous_h=depth
 finally:da._release_isolated_clients(clients,copies)
 d=pd.DataFrame(rows);d.to_csv(out/'interaction_audit.csv',index=False,encoding='utf-8-sig')
 pd.DataFrame({'tm':d.time,'二级站出口_Q_m3s':d.source_Q,'d0_Q_m3s':d.q_command,'老庄节制闸_e_m':d.e_command_m}).to_csv(out/'laozhuang_boundaries.csv',index=False,encoding='utf-8-sig')
 valid=(pd.to_datetime(d.state_time)<=end)&d.h_target_at_state_time.notna();error=d.loc[valid,'h_sim']-d.loc[valid,'h_target_at_state_time']
 audit={'criterion':'mean((Q_set-Q_sim)/Q_sim) over previous 5 completed interaction cycles > 0.10','zero_flow_rule':'both zero -> 0; requested positive and simulated zero -> infinity','incomplete_window':'no opening activation before 5 samples','flow_report_delay_hours':1,'dt_seconds':3600,'window':[start.isoformat(),end.isoformat()],'diversion_strategy':'same previous candidate: twice observed d0, clipped at 15m3/s','opening_tiers':[.1*i for i in range(1,11)],'opening_tier_basis':'fraction of 0-1.5m physical range; at most one tier per interaction','eligible_cycles':int(d.opening_eligible_next_cycle.sum()),'opening_active_cycles':int(d.opening_stage_active_this_cycle.sum()),'max_five_cycle_mean':float(d.past5_mean_signed_relative_error.max()),'rmse_m':float(np.sqrt(np.mean(error**2))),'mae_m':float(np.mean(abs(error))),'evaluated_samples':int(valid.sum()),'max_opening_execution_error_m':float(max(abs(d.e_executed_m-d.e_command_m))),'state_rollback_used':False,'limits':'single-reach candidate replay, not final calibrated reconstruction; 2cm depth target not achieved'}
 (out/'summary.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(audit,ensure_ascii=False,indent=2))
 assert max(abs(d.e_executed_m-d.e_command_m))<1e-6
 assert not d.loc[d.completed_cycle_window_count<5,'opening_eligible_next_cycle'].any()
 assert ((~d.opening_eligible_next_cycle)|(d.past5_mean_signed_relative_error>.1)).all()
 assert all(not r['opening_stage_active_this_cycle'] or rows[i-1]['opening_eligible_next_cycle'] for i,r in enumerate(rows))
if __name__=='__main__':
 g=FlowGuard();assert all(not g.add(1.,.8)[2] for _ in range(4));assert g.add(1.,.8)[2]
 g=FlowGuard();assert not any(g.add(1.1,1.)[2] for _ in range(5))
 main()
