# coding: utf-8
# coding: utf-8
import sys,json,ctypes,argparse
from pathlib import Path
from datetime import datetime,timedelta
import pandas as pd,numpy as np
sys.path[:0]=['tools','tools/GPT-Reconstruction']
import reach_missing_data_assimilation as da
p=Path('data/dayudu').resolve();out=p/'output/laozhuang_tiered_2pct';out.mkdir(parents=True,exist_ok=True)
reach=da.discover_reaches(p)[0];cfg=da.AssimilationConfig(initial_water_depth=1.61);clients,copies=da._load_isolated_clients(None,1,da._build_runtime_config(p/'OCIS_dataConfig.json',p,reach,cfg),cfg);cl=clients[0]
cl.set_outputfile_Label(0);cl.Auto_correct_endpoint();cl.Auto_correct_gates_calculationType()
idnames,names=da._parse_gate_info(cl.get_gate_info_sim());source=names[da._normalize_name('二级站出口')];gate=names[da._normalize_name('老庄节制闸')];div=names['d0']
ctype=cl._dll.set_GatesFlow_calculationType_byID_sim;ctype.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int];ctype.restype=None
# Flow boundary for diversion, hydraulic opening boundary for the control gate.
ctype(cl.obj,int(div),0);ctype(cl.obj,int(gate),1)
setarray=cl._dll.set_GatesFlow_e_byID_sim_array;setarray.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(ctypes.c_double),ctypes.c_int];setarray.restype=None
b=da.load_observation_csv(p/'input/action_td.csv');e=da.load_observation_csv(p/'input/gate_e_td.csv')['老庄节制闸'];h=da.load_observation_csv(p/'input/stage1_td.csv')['老庄节制闸'];srcobs=da.load_observation_csv(p/'input/action_obs_td.csv')['二级站出口'];start=datetime(2026,4,14);rows=[]
def sample(s,t):
 v=s.value_at(t,86400,'causal')
 if v is None:v=s.value_at(t,300,'nearest')
 return v

from collections import deque

def metrics(obs,sim):
 obs=np.asarray(obs,float);sim=np.asarray(sim,float)
 if len(obs)<5 or np.ptp(obs)<=1e-12 or np.std(sim)<=1e-12:
  return {'n':len(obs),'NRMSE':None,'NSE':None,'R2':None,'KGE':None,'passed':False}
 rmse=float(np.sqrt(np.mean((sim-obs)**2)))
 r=float(np.corrcoef(obs,sim)[0,1]);alpha=float(np.std(sim)/np.std(obs));beta=float(np.mean(sim)/np.mean(obs))
 m={'n':len(obs),'RMSE_m':rmse,'NRMSE':rmse/float(np.ptp(obs)),'NSE':1-float(np.sum((sim-obs)**2)/np.sum((obs-np.mean(obs))**2)),'R2':r*r,'KGE':1-float(np.sqrt((r-1)**2+(alpha-1)**2+(beta-1)**2))}
 m['passed']=m['NRMSE']<.05 and all(m[x]>.9 for x in ['NSE','R2','KGE'])
 return m

def run_step(k,q,opening):
 t=start+timedelta(hours=k)
 cl.update_BC_sim_only(k);cl.set_GatesFlow_byID_sim(source,float(sample(srcobs,t)))
 cl.set_GatesFlow_byID_sim(div,float(q));cl.set_GatesFlow_byID_sim(gate,10.3)
 cl.set_GatesFlow_e_byID_sim(gate,float(opening));setarray(cl.obj,int(gate),(ctypes.c_double*1)(opening),1)
 cl.set_GatesFlow_mu_byID_sim_multi(gate,{'Ues':.6,'Uef':.6});cl.stepSolver_sim_Roe_only_pool(k,0)
 get=lambda typ,idx:da._extract_gate_value(da._safe_model_data(cl,typ),idx)
 values=(get('gates_h1',gate),get('gates_Q',div),get('gates_e',gate))
 assert all(v is not None and np.isfinite(v) for v in values),values
 return values

flow_errors=deque(maxlen=5);obs_hist=[];sim_hist=[]
q_tier=.1;e_tier=0.;offset=0.;previous_q=None;rows=[];trial_count=0
try:
 for k in range(169):
  t=start+timedelta(hours=k);state_time=t+timedelta(hours=1)
  qb=float(sample(b['d0'],t));eb=float(sample(e,t)/1000);target=h.value_at(state_time,1800,'linear')
  rolling=metrics(obs_hist[-5:],sim_hist[-5:])
  if k>=5 and not rolling['passed']:q_tier=min(1.,round(q_tier+.1,10))
  mean=float(np.mean(flow_errors)) if len(flow_errors)==5 else None
  eligible=mean is not None and mean>.02+1e-12
  anchor=float(np.clip(eb+offset,0,1.5))
  if eligible:
   e_tier=.1 if e_tier==0 else (min(1.,round(e_tier+.1,10)) if not rolling['passed'] else e_tier)
  else:e_tier=0.
  qlo=max(0.,qb*(1-q_tier));qhi=min(15.,qb*(1+q_tier))
  cl.save_states();clock_before=json.loads(cl.get_time_param())
  def trial(q,opening):
   global trial_count
   cl.set_states();assert json.loads(cl.get_time_param())==clock_before
   value=run_step(k,q,opening)[0];trial_count+=1
   return value
  bestq=float(np.clip(qb,qlo,qhi));beste=anchor;besth=None;score=float('inf')
  if target is not None and k<168:
   low,high=qlo,qhi;hl=trial(low,anchor);hh=trial(high,anchor)
   candidates=[(abs(hl-target),low,hl),(abs(hh-target),high,hh)]
   # Bisect only when observations are bracketed, otherwise retain best endpoint.
   if (hl-target)*(hh-target)<=0:
    for iteration in range(8):
     mid=(low+high)/2;hm=trial(mid,anchor);candidates.append((abs(hm-target),mid,hm))
     if (hl-target)*(hm-target)<=0:high=mid
     else:low=mid;hl=hm
   score,bestq,besth=min(candidates)
   if eligible:
    elo=max(0.,anchor-e_tier*1.5);ehi=min(1.5,anchor+e_tier*1.5)
    for ec in np.linspace(elo,ehi,11):
     hc=trial(bestq,float(ec))
     if abs(hc-target)<score:score=abs(hc-target);beste=float(ec);besth=hc
  cl.set_states();assert json.loads(cl.get_time_param())==clock_before
  depth,reported,actual_e=run_step(k,bestq,beste)
  clock_after=json.loads(cl.get_time_param());assert abs(clock_after['solver_elapsed_seconds']-clock_before['solver_elapsed_seconds']-3600)<1e-8
  if besth is not None:assert abs(depth-besth)<1e-9
  err=None
  if previous_q is not None:
   err=(previous_q-reported)/reported if abs(reported)>1e-9 else (0. if abs(previous_q)<1e-9 else float('inf'))
   flow_errors.append(err)
  assert qlo-1e-9<=bestq<=qhi+1e-9
  assert abs(beste-anchor)<=e_tier*1.5+1e-9
  assert abs(actual_e-beste)<1e-9
  if not eligible:assert beste==anchor
  if target is not None and k<168:obs_hist.append(target);sim_hist.append(depth)
  rows.append({'time':t.isoformat(),'state_time':state_time.isoformat(),'q_base':qb,'q_command':bestq,'q_lower':qlo,'q_upper':qhi,'q_amplitude_tier':q_tier,'q_reported_previous_cycle':reported,'previous_cycle_q_command':previous_q,'previous_cycle_signed_relative_error':err,'past5_mean_signed_relative_error_used':mean,'opening_eligible':eligible,'opening_amplitude_tier':e_tier,'opening_anchor_m':anchor,'e_observed_m':eb,'e_command_m':beste,'e_executed_m':actual_e,'h_sim':depth,'h_target_at_state_time':target,'source_Q':sample(srcobs,t),'solver_elapsed_seconds':clock_after['solver_elapsed_seconds'],'rolling5_passed_before_cycle':rolling['passed']})
  offset=beste-eb;previous_q=bestq
finally:da._release_isolated_clients(clients,copies)
d=pd.DataFrame(rows);d.to_csv(out/'interaction_audit.csv',index=False,encoding='utf-8-sig')
pd.DataFrame({'tm':d.time,'二级站出口_Q_m3s':d.source_Q,'d0_Q_m3s':d.q_command,'老庄节制闸_e_m':d.e_command_m}).to_csv(out/'laozhuang_boundaries.csv',index=False,encoding='utf-8-sig')
audit={'reach':'老庄 pool0','window':['2026-04-14','2026-04-21'],'metrics':metrics(obs_hist,sim_hist),'NRMSE_definition':'RMSE / (max(observed)-min(observed))','R2_definition':'squared Pearson correlation','KGE_definition':'2009 Pearson correlation, std ratio, mean ratio','flow_guard':'signed mean((Q_set-Q_sim)/Q_sim) over last five completed cycles > 2%; retains previous signed convention','zero_flow_rule':'both zero -> 0; nonzero requested with zero simulated -> infinity','flow_tiers':'10%-100% of known boundary Q; +/- range anchored to each timestamp known Q, clipped to 0..15m3/s','tier_breakthrough':'five valid depth pairs available and rolling five-pair metrics fail; at most one tier per cycle','opening_tiers':'10%-100% of 1.5m physical range, anchored to current held opening','opening_eligible_cycles':int(d.opening_eligible.sum()),'opening_adjusted_cycles':int((abs(d.e_command_m-d.opening_anchor_m)>1e-9).sum()),'max_q_tier':float(d.q_amplitude_tier.max()),'trial_count':trial_count,'clock_restore_verified':True,'terminal_diagnostic':'169th step obtains previous cycle diversion report; only first168 states evaluated','missing_targets':168-len(obs_hist)}
(out/'summary.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(audit,ensure_ascii=False,indent=2))
