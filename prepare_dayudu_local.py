import sys,json,shutil
from pathlib import Path
sys.path[:0]=['tools','tools/GPT-Reconstruction']
import reach_missing_data_assimilation as da
src=Path(r'D:\Algorithm dev\0_DA\DYD-DA\DA\data\dayudu_new-d0'); dst=Path('data/dayudu').resolve()
for folder in ['input','mesh']:
 shutil.copytree(src/folder,dst/folder,dirs_exist_ok=True)
# Match the observed names to the actual deployed local topology.
for p in (dst/'input').glob('*.csv'):
 text=da._read_text(p).replace('圪塔节制闸','疙瘩节制闸').replace('麻原节制闸','麻园节制闸')
 p.write_text(text,encoding='utf-8-sig')
fields={'dirpath':str(dst.parent)+'/', 'case_name':'dayudu','edges_path':'mesh/edges.csv','input_json_path':'input/input.json','boundary_flow_path':'input/action_td.csv','boundary_stage_path':'input/stage1_td.csv','output':'output/'}
cfg={'selected_model':'HDCM','HDCM':fields,'SIM':{**fields,'dirpath':str(dst)+'/', 'mesh_path':'mesh/','boundary_stage_path':'input/stage2_td.csv','gates_e_path':'input/gates_e_sum_td.csv','dt':3600,'output_label':1,'waterLevel_resultType':'wh'},'WATERALLOCATION':{**fields,'dirpath':str(dst)+'/'}}
(dst/'OCIS_dataConfig.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
print('prepared',dst)
import parallel_pid_reconstruction as pp
rs=da.discover_reaches(dst)
print(pp._inspect_model(dst,dst/'OCIS_dataConfig.json',rs[0]))
