# coding: utf-8
# coding: utf-8
import sys,json,ctypes,argparse
from pathlib import Path
from datetime import datetime,timedelta
import pandas as pd,numpy as np
sys.path[:0]=['tools','tools/GPT-Reconstruction']
import reach_missing_data_assimilation as da
p=Path('data/dayudu').resolve();out=p/'output/laozhuang_ordered_test';out.mkdir(parents=True,exist_ok=True)
ap=argparse.ArgumentParser();ap.add_argument('--factor',type=float,default=1);ap.add_argument('--opening-offset',type=float,default=0);ap.add_argument('--label',required=True);ap.add_argument('--hours',type=int,default=12);args=ap.parse_args()
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
try:
 for k in range(args.hours):
  t=start+timedelta(hours=k);qb=sample(b['d0'],t);q=max(0,min(15,qb*args.factor));eb=sample(e,t)/1000;ec=float(np.clip(eb+args.opening_offset,0,1.5));up=sample(srcobs,t)
  cl.update_BC_sim_only(k);cl.set_GatesFlow_byID_sim(source,float(up));cl.set_GatesFlow_byID_sim(div,q)
  # Hydraulic gate capacity is the case physical maximum, not an imposed discharge.
  cl.set_GatesFlow_byID_sim(gate,10.3);cl.set_GatesFlow_e_byID_sim(gate,ec);setarray(cl.obj,int(gate),(ctypes.c_double*1)(ec),1)
  cl.set_GatesFlow_mu_byID_sim_multi(gate,{'Ues':.6,'Uef':.6});cl.stepSolver_sim_Roe_only_pool(k,0)
  get=lambda typ,idx:da._extract_gate_value(da._safe_model_data(cl,typ),idx)
  rows.append({'time':t.isoformat(),'q_base':qb,'q_command':q,'q_executed':get('gates_Q',div),'e_observed_m':eb,'e_command_m':ec,'e_executed_m':get('gates_e',gate),'h_target':h.value_at(t,1800,'linear'),'h_sim':get('gates_h1',gate),'gate_Q_diagnostic':get('gates_Q',gate),'source_Q':up})
 d=pd.DataFrame(rows);d.to_csv(out/(args.label+'.csv'),index=False,encoding='utf-8-sig');print('RESULT',args.label,'RMSE',float(np.sqrt(np.mean((d.h_sim-d.h_target)**2))),'Q_exec_err',float(max(abs(d.q_command-d.q_executed))),'E_exec_err',float(max(abs(d.e_command_m-d.e_executed_m))))
finally:da._release_isolated_clients(clients,copies)
