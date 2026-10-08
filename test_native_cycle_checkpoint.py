# coding: utf-8
# coding: utf-8
import sys,json,ctypes,argparse
from pathlib import Path
from datetime import datetime,timedelta
import pandas as pd,numpy as np
sys.path[:0]=['tools','tools/GPT-Reconstruction']
import reach_missing_data_assimilation as da
parser=argparse.ArgumentParser();parser.add_argument('--cross-section',type=Path);args=parser.parse_args()
p=Path('data/dayudu').resolve()
reach=da.discover_reaches(p)[0];cfg=da.AssimilationConfig(initial_water_depth=1.61);clients,copies=da._load_isolated_clients(None,1,da._build_runtime_config(p/'OCIS_dataConfig.json',p,reach,cfg),cfg);cl=clients[0]
cl.set_outputfile_Label(0);cl.Auto_correct_endpoint();cl.Auto_correct_gates_calculationType()
json_bed=None;cross_section_report={}
if args.cross_section:
 before_bed=json.loads(cl.get_stepdata_sim('node_zb'))
 before_width=json.loads(cl.get_stepdata_sim('node_Width'))
 before_slope=json.loads(cl.get_stepdata_sim('node_tanb'))
 cl.set_CrossSection(args.cross_section,0,44)
 json_bed=json.loads(cl.get_stepdata_sim('node_zb'))
 assert before_bed!=json_bed
 assert json.loads(cl.get_stepdata_sim('node_Width'))==before_width
 assert json.loads(cl.get_stepdata_sim('node_tanb'))==before_slope
 cross_section_report={'input':str(args.cross_section.resolve()),'before_bed':before_bed,'imported_bed':json_bed,'shape_unchanged':True}

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

def execute(k,q,e):
 t=start+timedelta(hours=k)
 cl.update_BC_sim_only(k)
 cl.set_GatesFlow_byID_sim(source,float(sample(srcobs,t)))
 cl.set_GatesFlow_byID_sim(div,q)
 cl.set_GatesFlow_byID_sim(gate,10.3)
 cl.set_GatesFlow_e_byID_sim(gate,e)
 setarray(cl.obj,int(gate),(ctypes.c_double*1)(e),1)
 cl.set_GatesFlow_mu_byID_sim_multi(gate,{'Ues':.6,'Uef':.6})
 cl.stepSolver_sim_Roe_only_pool(k,0)
 if json_bed is not None:assert json.loads(cl.get_stepdata_sim('node_zb'))==json_bed, 'JSON bed reverted during hydraulic step'
 result={key:da._safe_model_data(cl,key) for key in ['gates_h1','gates_h2','gates_Q','gates_e']}
 for key in result:
  value=da._extract_gate_value(result[key],gate)
  assert value is not None and np.isfinite(value), (key,value)
 return result
def clock():return json.loads(cl.get_time_param())
checks=[]
try:
 for k in range(8):
  cl.save_states()
  before=clock()
  a=execute(k,1.2,.35);after=clock()
  cl.set_states()
  assert clock()==before, ('clock rollback',k,before,clock())
  if json_bed is not None:assert json.loads(cl.get_stepdata_sim('node_zb'))==json_bed
  alternate=execute(k,2.4,.5)
  assert alternate!=a, ('controls did not change result',k)
  cl.set_states()
  assert clock()==before
  replay=execute(k,1.2,.35)
  assert a==replay, ('trial contamination',k)
  assert clock()==after
  cl.set_states()
  assert clock()==before
  final=execute(k,1.2,.35)
  assert a==final
  assert abs(clock()['solver_elapsed_seconds']-before['solver_elapsed_seconds']-3600)<1e-8
  checks.append({'cycle':k,'before':before['solver_elapsed_seconds'],'after':clock()['solver_elapsed_seconds'],'repeat_equal':True})
 out=p/('output/checkpoint_regression_manual_no_waga_json' if args.cross_section else 'output/checkpoint_regression_manual_no_waga');out.mkdir(parents=True,exist_ok=True)
 (out/'result.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
 if args.cross_section:
  cross_section_report['retained_after_all_trials_and_8_committed_steps']=True
  cross_section_report['after_8_cycles_bed']=json.loads(cl.get_stepdata_sim('node_zb'))
  (out/'json_bed_simulation_verification.json').write_text(json.dumps(cross_section_report,indent=2),encoding='utf-8')
 print('PASS: eight cycles; A/B/A repeat equality, clock rollback and single final advance')
finally:da._release_isolated_clients(clients,copies)
