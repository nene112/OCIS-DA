# -*- coding: utf-8 -*-
"""Actual DLL/pool 0: tailwater response, A/B/A replay and atomic schedule import."""
import ctypes,json
from datetime import datetime
import numpy as np
import HD_Roe
from run_fujialing_reconstruction_comparison import ROOT,da

def main():
    case=ROOT/'data/dayudu';out=case/'output/tailwater_validation';out.mkdir(parents=True,exist_ok=True)
    cfg=da.AssimilationConfig(initial_water_depth=1.5)
    runtime=da._build_runtime_config(case/'OCIS_dataConfig.json',case,da.discover_reaches(case)[0],cfg)
    clients,copies=da._load_isolated_clients(None,1,runtime,cfg);cl=clients[0]
    try:
        cl.set_outputfile_Label(0);cl.Auto_correct_endpoint();cl.Auto_correct_gates_calculationType()
        info=json.loads(cl.get_gate_info_sim());_,names=da._parse_gate_info(cl.get_gate_info_sim())
        target=int(names[da._normalize_name('老庄节制闸')]);source=int(names[da._normalize_name('二级站出口')]);div=int(names['d0'])
        mode=cl._dll.set_GatesFlow_calculationType_byID_sim;mode.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int];mode.restype=None
        mode(cl.obj,target,1);mode(cl.obj,source,0);mode(cl.obj,div,0)
        z=json.loads(cl.get_stepdata_sim('node_zb'));g=info[str(target)];clock=json.loads(cl.get_time_param());start=clock['start_t']
        def payload(depth):return {'gate_id':target,'sill_elevation_m':z[g['nIndex']],
            'samples':[{'time':start+k*3600,'water_level_m':z[g['nIndexDown']]+depth} for k in range(9)],'max_gap_seconds':7200}
        cl.set_GatesDownstreamStage_sim(payload(.5));cl.save_states()
        def get(key,id):return da._extract_gate_value(da._safe_model_data(cl,key),id)
        def trial(source_q=2.625,div_q=.7,opening=.25):
            cl.update_BC_sim_only(0);cl.set_GatesFlow_byID_sim(source,source_q);cl.set_GatesFlow_byID_sim(div,div_q)
            cl.set_GatesFlow_e_byID_sim(target,opening)
            array=cl._dll.set_GatesFlow_e_byID_sim_array;array.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(ctypes.c_double),ctypes.c_int];array.restype=None
            array(cl.obj,target,(ctypes.c_double*1)(opening),1)
            cl.stepSolver_sim_Roe_only_pool(0,0)
            report=cl.get_downstream_stage_report_sim()
            assert not cl.check_nan_sim(),report
            return {'h_up':get('gates_h1',target),'h_down':get('gates_h2',target),'Q':get('gates_Q_current',target),
                'boundary':report['gates'][0],'h':json.loads(cl.get_stepdata_sim('node_h')),
                'clock':json.loads(cl.get_time_param())}
        a=trial();cl.set_states();assert json.loads(cl.get_time_param())==clock
        assert abs(a['boundary']['cumulative_mass_residual_m3'])<.1,a['boundary']
        cl.set_GatesDownstreamStage_sim(payload(1.0));b=trial()
        assert b['h_up']>a['h_up'],(a['h_up'],b['h_up'])
        assert abs(a['h_down']-.5)<1e-9 and abs(b['h_down']-1)<1e-9
        assert abs(b['boundary']['cumulative_mass_residual_m3'])<.1,b['boundary']
        cl.set_states();a2=trial()
        assert np.max(abs(np.array(a['h'])-np.array(a2['h'])))<1e-9
        assert a['boundary']==a2['boundary'] and a['clock']==a2['clock']
        assert a['clock']['solver_elapsed_seconds']==3600
        assert json.loads(cl.get_gate_info_sim())[str(target)]['nIndexDown']==g['nIndexDown']
        cl.set_states();closed=trial(0,0,0);assert closed['Q']==0 and closed['boundary']['net_out_volume_m3']==0
        assert abs(closed['boundary']['cumulative_mass_residual_m3'])<.1,closed['boundary']
        cl.set_states();cl.set_GatesDownstreamStage_sim(payload(2.0));reverse=trial(0,0,.25)
        assert reverse['Q']<0 and reverse['boundary']['net_out_volume_m3']<0
        assert abs(reverse['boundary']['cumulative_mass_residual_m3'])<.1,reverse['boundary']
        cl.set_states();bad=payload(.5);bad['samples'][1]['time']=bad['samples'][0]['time']
        try:cl.set_GatesDownstreamStage_sim(bad);raise AssertionError('duplicate timestamp accepted')
        except HD_Roe.OcisError:pass
        cl.set_states();bad=payload(.5);bad['max_gap_seconds']=60
        try:cl.set_GatesDownstreamStage_sim(bad);raise AssertionError('excessive stage gap accepted')
        except HD_Roe.OcisError:pass
        cl.set_states();a3=trial();assert np.max(abs(np.array(a['h'])-np.array(a3['h'])))<1e-9
        result={'passed':True,'tailwater_low_h_up_m':a['h_up'],'tailwater_high_h_up_m':b['h_up'],
            'prescribed_stage_exact':True,'topology_preserved':True,'schedule_and_clock_ABA_replay':True,
            'closed_gate_Q_m3s':closed['Q'],'reverse_gate_Q_m3s':reverse['Q'],
            'invalid_timestamp_and_gap_rejected':True,'low_tailwater_boundary_report':a['boundary'],
            'closed_gate_mass_residual_m3':closed['boundary']['cumulative_mass_residual_m3'],
            'reverse_flow_mass_residual_m3':reverse['boundary']['cumulative_mass_residual_m3']}
        (out/'dll_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:da._release_isolated_clients(clients,copies)

if __name__=='__main__':main()
