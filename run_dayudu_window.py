import sys,json,argparse
from pathlib import Path
from dataclasses import replace
sys.path[:0]=[str(Path(__file__).resolve().parent/'tools'),str(Path(__file__).resolve().parent/'tools/GPT-Reconstruction')]
import run_dayudu_reconstruction as driver
import reach_missing_data_assimilation as da
import pid_reconstruction as pid
original_load_client = da._load_client
def load_corrected_client(runtime_config, config, client_factory):
    client = original_load_client(runtime_config, config, client_factory)
    client.Auto_correct_endpoint()
    client.Auto_correct_gates_calculationType()
    return client
da._load_client = load_corrected_client

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--steps',type=int,default=168); ap.add_argument('--pools',default='0,1,2,3,4,5,6'); ap.add_argument('--output-name',default='reconstruction_20260414_20260421'); a=ap.parse_args()
 case=Path(__file__).resolve().parent/'data/dayudu'; out=case/'output'/a.output_name; out.mkdir(parents=True,exist_ok=True)
 files=da.ObservationFiles(boundary_flow=case/'input/action_td.csv',gate_h1=case/'input/stage1_td.csv',gate_h2=case/'input/stage2_td.csv',gate_flow=case/'input/action_td.csv',gate_opening=case/'input/gate_e_td.csv')
 reaches=da.discover_reaches(case); results=[]
 for r in reaches:
  if r.pool_id not in [int(x) for x in a.pools.split(',')]: continue
  name=r.target_gate_name.replace('疙瘩','圪塔').replace('麻园','麻原')
  cfg=replace(driver._config_for_reach(driver._GATE_OPENING_MAX_M.get(name,1.5)),steps=a.steps,run_baseline=True)
  print('START',r.pool_id,r.target_gate_name,flush=True)
  result=pid.run_pid_reconstruction(case,r,config=cfg,output_root=out,dll_path=da.TOOLS_DIR/'OcisMILPNet.dll',observation_files=files)
  results.append({'status':'success',**result}); print('DONE',r.pool_id,json.dumps(result,ensure_ascii=False),flush=True)
  (out/'run_summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__': main()
