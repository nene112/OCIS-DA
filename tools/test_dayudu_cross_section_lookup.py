"""Native U-reach integration: finite advancement, geometry and rollback.

This real reach has free outfalls; closed-lake and volume checks are performed
by the native cross_section_solver_test on a periodic mesh.
"""
from __future__ import annotations
import bisect
import ctypes
import json
import math
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'tools/GPT-Reconstruction')]
import HD_Roe
import reach_missing_data_assimilation as da


def expected(rows, h):
    heights = [r[0] for r in rows]
    if h >= heights[-1]:
        p=rows[-1]; d=h-p[0]
        return p[1]+p[2]*d, p[2], p[3]+2*d, p[4]+p[1]*d+p[2]*d*d/2
    index = max(0, bisect.bisect_right(heights, h)-1)
    p, q = rows[index], rows[index+1]
    d=h-p[0]; slope=(q[2]-p[2])/(q[0]-p[0])
    return p[1]+p[2]*d+slope*d*d/2, p[2]+slope*d, p[3]+(q[3]-p[3])*d/(q[0]-p[0]), p[4]+p[1]*d+p[2]*d*d/2+slope*d**3/6


def main():
    case=ROOT/'data/dayudu'
    source=case/'input/CrossSection_dayudu_hydraulic_tables.geojson'
    root=json.loads(source.read_text(encoding='utf-8'))
    features=root['features']
    results=[]
    for algorithm in ['sediment', 'Roe_new']:
        cfg=da.AssimilationConfig(initial_water_depth=1.0, initial_bed_level=0.0, sim_solver_type=algorithm)
        reach=da.discover_reaches(case)[6]  # Contains the three U-shaped reaches.
        runtime=json.loads(da._build_runtime_config(case/'OCIS_dataConfig.json',case,reach,cfg))
        runtime['SIM']['cross_section_path']=str(source)
        cl=da._load_client(json.dumps(runtime),cfg,lambda: HD_Roe.create_client(ROOT/'tools/OcisMILPNet.dll'))
        cl.set_outputfile_Label(0)
        gate_names, _=da._parse_gate_info(cl.get_gate_info_sim())
        mode=cl._dll.set_GatesFlow_calculationType_byID_sim
        mode.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int];mode.restype=None
        for gate in gate_names:
            mode(cl.obj,int(gate),0)
            cl.set_GatesFlow_byID_sim(int(gate),0.0)
        cl.set_all_gates_byType(0.0,0)
        def read():
            return {key:json.loads(cl.get_stepdata_sim(key)) for key in
                ['node_h','node_Q','node_A','node_B','node_P','node_HR','node_pressure_integral','node_section_id','node_zb']}
        before=read()
        assert max(abs(q) for q in before['node_Q'])<1e-12, 'lake-at-rest initial discharge must be zero'
        cl.save_states()
        cl.stepSolver_sim_Roe_only_pool(0,6)
        after=read()
        error=max(abs(h-1) for h in after['node_h'])
        q_error=max(abs(q) for q in after['node_Q'])
        assert not cl.check_nan_sim()
        assert all(math.isfinite(v) for values in after.values() for v in values)
        geometry_error=0.0; checked=0
        for feature in features:
            p=feature['properties']
            if p['section_type']!='u_shaped':continue
            for node in p['model_node_ids']:
                ref=expected(p['hydraulic_lookup']['rows'],after['node_h'][node])
                actual=[after[key][node] for key in ['node_A','node_B','node_P','node_pressure_integral']]
                geometry_error=max(geometry_error,max(abs(a-b) for a,b in zip(actual,ref)))
                assert after['node_section_id'][node]==features.index(feature)+1
                checked+=1
        assert checked>0 and geometry_error<1e-9
        cl.set_states()
        assert read()==before
        # Give this reach a small nonzero inflow and verify deterministic trial replay.
        upstream=da._parse_gate_info(cl.get_gate_info_sim())[1][da._normalize_name(reach.upstream_gate_name)]
        cl.set_GatesFlow_byID_sim(upstream,.5)
        cl.save_states()
        cl.stepSolver_sim_Roe_only_pool(0,6)
        dynamic=read()
        assert not cl.check_nan_sim()
        assert all(math.isfinite(v) for key, values in dynamic.items() for v in values)
        assert dynamic!=before
        cl.set_states();cl.stepSolver_sim_Roe_only_pool(0,6)
        assert read()==dynamic
        # Invalid table must be rejected before mutation, with errors propagated by Python.
        bad=json.loads(json.dumps(root));bad['features'][0]['properties']['hydraulic_lookup']['rows'][1][1]=-1
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'bad.geojson';file.write_text(json.dumps(bad),encoding='utf-8')
            try:cl.set_CrossSection(file)
            except ValueError:pass
            else:raise AssertionError('bad table silently accepted')
        assert read()==dynamic
        results.append({'solver':algorithm,'pool':6,'u_nodes_checked':checked,'free_outfall_depth_change':error,
            'free_outfall_discharge_change':q_error,'geometry_error':geometry_error,'rollback_equal':True,
            'dynamic_trial_equal':True,'invalid_import_atomic':True})
    out=case/'output/cross_section_lookup';out.mkdir(parents=True,exist_ok=True)
    (out/'u_reach_regression.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
