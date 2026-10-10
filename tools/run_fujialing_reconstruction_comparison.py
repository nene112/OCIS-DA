"""Reconstruct pool 3 with fixed acceptance rules; preserve a baseline and audit."""
from __future__ import annotations
import argparse
from collections import deque
import ctypes
from datetime import datetime,timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tools/GPT-Reconstruction')]
import reach_missing_data_assimilation as da


def metrics(obs,sim):
    obs=np.asarray(obs,float);sim=np.asarray(sim,float)
    valid=np.isfinite(obs)&np.isfinite(sim);obs,sim=obs[valid],sim[valid]
    result={'n':len(obs),'passed':False}
    if len(obs)<5 or np.ptp(obs)<1e-10 or np.std(sim)<1e-10:return result
    rmse=float(np.sqrt(np.mean((obs-sim)**2)));r=float(np.corrcoef(obs,sim)[0,1])
    result.update(RMSE_m=rmse,bias_m=float(np.mean(sim-obs)),NRMSE=rmse/float(np.ptp(obs)),
        NSE=1-float(np.sum((obs-sim)**2)/np.sum((obs-np.mean(obs))**2)),R2=r*r,
        KGE=1-float(np.sqrt((r-1)**2+(np.std(sim)/np.std(obs)-1)**2+(np.mean(sim)/np.mean(obs)-1)**2)))
    result['passed']=result['NRMSE']<.05 and all(result[k]>.9 for k in ['NSE','R2','KGE'])
    return result


def held(series,time):
    value=series.value_at(time,86400,'causal')
    if value is None:value=series.value_at(time,300,'nearest')
    if value is None:raise ValueError(f'missing boundary at {time}')
    return float(value)


def bind_law(client):
    fun=client._dll.GateFlow_hydraulic_multi
    pointer=ctypes.POINTER(ctypes.c_double)
    fun.argtypes=[pointer]*6;fun.restype=ctypes.c_double
    def call(h_up,h_down,opening,width,mu):
        if opening==0:return 0.0
        if h_up<=0 or h_down<0 or h_up<h_down:return float('nan')
        values=[ctypes.c_double(v) for v in [opening,width,h_up,h_down,0.0]]
        array=(ctypes.c_double*4)(*[mu[k] for k in ['Uef','Ues','Ucf','Ucs']])
        return float(fun(ctypes.byref(values[0]),array,ctypes.byref(values[1]),
            ctypes.byref(values[2]),ctypes.byref(values[3]),ctypes.byref(values[4])))
    return call


def run(case,out,start,hours,controlled,opening_max,*,pool_id=3,
        diversion_series_name='富家岭提水',use_observed_source=False,
        opening_file='gates_e_sum_td.csv',amplitude_cap=1.0,relative_opening=False,
        boundary_filename='fujialing_boundaries.csv',downstream_stage_file=None,refinement=None,
        snapshot_interval=0,after_run=None,gate_mu=None,warmup=None):
    reach=da.discover_reaches(case)[pool_id]
    stage=da.load_observation_csv(case/'input/stage1_td.csv')[reach.target_gate_name]
    openings=da.load_observation_csv(case/'input'/opening_file)[reach.target_gate_name]
    action=da.load_observation_csv(case/'input/action_td.csv')
    observation=da.load_observation_csv(case/'input/action_obs_td.csv')
    diversion=observation[diversion_series_name] if diversion_series_name else action[reach.boundary_model_name]
    source_series=observation[reach.upstream_gate_name] if use_observed_source else action[reach.upstream_gate_name]
    cfg=da.AssimilationConfig(initial_water_depth=held(stage,start))
    runtime=da._build_runtime_config(case/'OCIS_dataConfig.json',case,reach,cfg)
    clients,copies=da._load_isolated_clients(None,1,runtime,cfg);cl=clients[0]
    rows=[];trials=0
    try:
        cl.set_outputfile_Label(0);cl.Auto_correct_endpoint();cl.Auto_correct_gates_calculationType()
        gate_info=json.loads(cl.get_gate_info_sim());_,names=da._parse_gate_info(cl.get_gate_info_sim())
        source=int(names[da._normalize_name(reach.upstream_gate_name)])
        gate=int(names[da._normalize_name(reach.target_gate_name)]);div=int(names[reach.boundary_model_name])
        metadata=gate_info[str(gate)];assert metadata['Bwid_m']>0
        mode=cl._dll.set_GatesFlow_calculationType_byID_sim
        mode.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int];mode.restype=None
        mode(cl.obj,source,0);mode(cl.obj,div,0);mode(cl.obj,gate,1)
        if gate_mu is not None:
            cl.set_GatesFlow_mu_byID_sim_multi(gate,gate_mu)
            metadata=json.loads(cl.get_gate_info_sim())[str(gate)]
            assert all(abs(metadata['mu'][key]-value)<1e-12 for key,value in gate_mu.items())
        downstream_metadata=None
        if downstream_stage_file:
            series=da.load_observation_csv(case/'input'/downstream_stage_file)[reach.target_gate_name]
            end=start+timedelta(hours=hours)
            records=[(t,v) for t,v in series.values if start-timedelta(hours=6)<=t<=end+timedelta(hours=6)]
            if not records:raise ValueError('no downstream observations in reconstruction period')
            records.sort();backfill=[]
            if records[0][0]>start:
                time=start
                while time<records[0][0]:backfill.append((time,records[0][1]));time+=timedelta(hours=1)
            records=backfill+records
            if records[-1][0]<end:raise ValueError('downstream observations do not cover final state')
            bed=json.loads(cl.get_stepdata_sim('node_zb'))
            down_bed=bed[metadata['nIndexDown']];sill=bed[metadata['nIndex']]
            payload={'gate_id':gate,'sill_elevation_m':sill,'allow_reverse':True,'max_gap_seconds':21600,
                'samples':[{'time':int(t.timestamp()),'water_level_m':v+down_bed} for t,v in records]}
            cl.set_GatesDownstreamStage_sim(payload)
            downstream_metadata={'source':downstream_stage_file,'conversion':'observed depth + original downstream node bed elevation',
                'downstream_bed_m':down_bed,'sill_elevation_m':sill,'sill_source':'original model gate-node bed; not newly surveyed',
                'initial_backfill_points':len(backfill),'initial_backfill_until':records[len(backfill)][0].isoformat(),
                'initial_backfill_policy':'hold earliest available observed downstream depth backward to simulation start, explicitly imputed',
                'max_interpolation_gap_seconds':21600}
            (out/'downstream_stage_payload.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        setarray=cl._dll.set_GatesFlow_e_byID_sim_array
        setarray.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(ctypes.c_double),ctypes.c_int]
        setarray.restype=None
        law=bind_law(cl)
        section_ids=json.loads(cl.get_stepdata_sim('node_section_id'))
        assert section_ids[metadata['nIndex']]>0
        feedback=deque(maxlen=5);history_obs=[];history_sim=[]
        q_tier=.1;e_tier=0.;offset=0.
        def get(key,id):return da._extract_gate_value(da._safe_model_data(cl,key),id)
        def execute(k,q,e,source_q):
            cl.update_BC_sim_only(k)
            cl.set_GatesFlow_byID_sim(source,source_q)
            cl.set_GatesFlow_byID_sim(div,q)
            cl.set_GatesFlow_e_byID_sim(gate,e)
            setarray(cl.obj,gate,(ctypes.c_double*1)(e),1)
            cl.stepSolver_sim_Roe_only_pool(k,pool_id)
            if cl.check_nan_sim():
                error=cl.get_downstream_stage_report_sim() if downstream_stage_file else {}
                raise RuntimeError(f'native failure at cycle {k}: {error}')
            result={key:get(name,id) for key,name,id in [
                ('h_up','gates_h1',gate),('h_down','gates_h2',gate),('gate_Q','gates_Q_current',gate),
                ('diversion_Q','gates_Q_current',div),('opening','gates_e',gate)]}
            if any(v is None or not math.isfinite(v) for v in result.values()):raise RuntimeError(result)
            assert abs(result['opening']-e)<1e-10
            result['capacity_Q']=law(result['h_up'],result['h_down'],e,metadata['Bwid_m'],metadata['mu'])
            if downstream_stage_file:
                report=cl.get_downstream_stage_report_sim()
                if report['error']:raise RuntimeError(report['error'])
                boundary=next(g for g in report['gates'] if g['gate_id']==gate)
                result['capacity_Q']=boundary['capacity_flux_m3s']
                result['boundary_out_volume_m3']=boundary['net_out_volume_m3']
                result['boundary_mass_residual_m3']=boundary['last_mass_residual_m3']
                result['boundary_cumulative_mass_residual_m3']=boundary['cumulative_mass_residual_m3']
                result['downstream_wh_m']=boundary['water_level_m']
            return result
        warmup_summary=None
        if warmup:
            from hydraulic_warmup import prepare_warm_start
            first=refinement.iloc[0] if refinement is not None else None
            controls=(float(first.q_command_m3s) if first is not None else held(diversion,start),
                      float(first.opening_command_m) if first is not None else held(openings,start)/1000,
                      held(source_series,start))
            warmup_summary=prepare_warm_start(cl,execute,target_depth=held(stage,start),
                gate_metadata=metadata,source_gate_id=source,formal_payload=payload if downstream_stage_file else None,
                controls=controls,out=out,settings=warmup)
        for k in range(hours):
            if snapshot_interval and k%snapshot_interval==0:
                cl.save_states(f'periodic_{k}')
            time=start+timedelta(hours=k);state_time=time+timedelta(hours=1)
            qb=held(diversion,time);up_q=held(source_series,time)
            eb=held(openings,time)/1000
            if not 0<=eb<=opening_max+1e-9:raise ValueError(f'observed opening exceeds configured range: {eb}')
            target=stage.value_at(state_time,1800,'linear')
            rolling=metrics(history_obs[-5:],history_sim[-5:])
            mean=float(np.mean(feedback)) if len(feedback)==5 else None
            eligible=controlled and mean is not None and mean>.02
            refine_row=None
            if refinement is not None:
                refine_row=refinement.iloc[k]
                assert refine_row['time']==time.isoformat() and refine_row['state_time']==state_time.isoformat()
                assert abs(qb-refine_row['q_base_known_m3s'])<1e-9 and abs(up_q-refine_row['source_Q_m3s'])<1e-9
                eligible=False
            if controlled and len(history_obs)>=5 and not rolling['passed']:
                q_tier=min(amplitude_cap,round(q_tier+.1,10))
            if eligible:e_tier=.1 if e_tier==0 else (min(amplitude_cap,round(e_tier+.1,10)) if not rolling['passed'] else e_tier)
            else:e_tier=0.
            anchor=eb if relative_opening else (float(np.clip(eb+offset,0,opening_max)) if controlled else eb)
            qlo,qhi=(max(0.,qb*(1-q_tier)),min(15.,qb*(1+q_tier))) if controlled else (qb,qb)
            seedq=qb
            if refine_row is not None:
                seedq=float(refine_row['q_command_m3s']);anchor=float(refine_row['opening_command_m']);e_tier=0.
                q_tier=amplitude_cap if bool(refine_row['refinement_trigger']) else 0.
                qlo,qhi=(max(0.,qb*(1-amplitude_cap)),min(15.,qb*(1+amplitude_cap))) if bool(refine_row['refinement_trigger']) else (seedq,seedq)
            cl.save_states();clock_before=json.loads(cl.get_time_param())
            def trial(q,e):
                nonlocal trials
                cl.set_states();assert json.loads(cl.get_time_param())==clock_before
                trials+=1;return execute(k,q,e,up_q)
            bestq,beste=seedq,anchor;best=None
            if controlled and target is not None and (refine_row is None or bool(refine_row['refinement_trigger'])):
                candidates=[]
                for q in sorted({qlo,qb,qhi,seedq}):
                    result=trial(q,anchor);candidates.append((abs(result['h_up']-target),abs(q-seedq),q,anchor,result))
                low,high=qlo,qhi;hl=candidates[0][-1]['h_up'];hh=candidates[-1][-1]['h_up']
                if (hl-target)*(hh-target)<=0 and high>low:
                    for iteration in range(8):
                        q=(low+high)/2;result=trial(q,anchor)
                        candidates.append((abs(result['h_up']-target),abs(q-seedq),q,anchor,result))
                        if (hl-target)*(result['h_up']-target)<=0:high=q
                        else:low=q;hl=result['h_up']
                chosen=min(candidates,key=lambda x:x[:2]);bestq,beste,best=chosen[2:]
                if eligible:
                    reference=eb if relative_opening else opening_max
                    elo=max(0.,anchor-e_tier*reference);ehi=min(opening_max,anchor+e_tier*reference)
                    for e in np.linspace(elo,ehi,11):
                        result=trial(bestq,float(e))
                        if abs(result['h_up']-target)<abs(best['h_up']-target):beste,best=float(e),result
            cl.set_states();result=execute(k,bestq,beste,up_q)
            clock_after=json.loads(cl.get_time_param())
            assert abs(clock_after['solver_elapsed_seconds']-clock_before['solver_elapsed_seconds']-3600)<1e-8
            if best is not None:assert abs(result['h_up']-best['h_up'])<1e-9
            assert qlo-1e-9<=bestq<=qhi+1e-9
            if controlled:
                assert abs(beste-anchor)<=e_tier*(eb if relative_opening else opening_max)+1e-9
                if not eligible:assert beste==anchor
            executed=result['diversion_Q']
            error=(bestq-executed)/executed if executed>1e-9 else (0. if bestq<=1e-9 else float('inf'))
            feedback.append(error)
            if target is not None:history_obs.append(target);history_sim.append(result['h_up'])
            rows.append({'time':time.isoformat(),'state_time':state_time.isoformat(),'source_Q_m3s':up_q,
                'q_base_observed_m3s':qb,'q_base_planned_m3s':held(action[reach.boundary_model_name],time),
                'q_command_m3s':bestq,'q_executed_m3s':executed,'q_lower':qlo,'q_upper':qhi,
                'q_tier':q_tier if controlled else 0.,'past5_diversion_relative_error':mean,
                'diversion_relative_error':error,'opening_eligible':eligible,'opening_tier':e_tier,
                'opening_observed_m':eb,'opening_anchor_m':anchor,'opening_command_m':beste,
                'h_target_m':target,'h_sim_m':result['h_up'],'h_down_sim_m':result['h_down'],
                'gate_Q_sim_m3s':result['gate_Q'],'gate_capacity_Q_m3s':result['capacity_Q'],
                'solver_elapsed_seconds':clock_after['solver_elapsed_seconds']})
            offset=beste-eb
            if refine_row is not None:
                rows[-1].update(first_round_q_m3s=seedq,first_round_h_sim_m=float(refine_row['h_sim_m']),
                    first_round_depth_relative_error=float(refine_row['first_round_depth_relative_error']),
                    refinement_trigger=bool(refine_row['refinement_trigger']))
            if downstream_stage_file:
                rows[-1].update({key:result[key] for key in ['boundary_out_volume_m3','boundary_mass_residual_m3','boundary_cumulative_mass_residual_m3','downstream_wh_m']})
            if k%24==0:print(f'{"reconstruction" if controlled else "baseline"} cycle {k}/{hours}, h={result["h_up"]:.4f}, target={target}, q_exec={executed:.5f}',flush=True)
        label='reconstruction' if controlled else 'baseline'
        data=pd.DataFrame(rows);data.to_csv(out/f'{label}_audit.csv',index=False,encoding='utf-8-sig')
        if relative_opening:
            assert (abs(data.q_command_m3s-data.q_base_planned_m3s)<=amplitude_cap*abs(data.q_base_planned_m3s)+1e-9).all()
            assert (abs(data.opening_command_m-data.opening_observed_m)<=amplitude_cap*abs(data.opening_observed_m)+1e-9).all()
        (out/f'{label}_runtime_config.json').write_text(runtime,encoding='utf-8')
        summary={'metrics':metrics(history_obs,history_sim),'trial_count':trials,
            'opening_eligible_cycles':int(data.opening_eligible.sum()),
            'opening_adjusted_cycles':int((abs(data.opening_command_m-data.opening_anchor_m)>1e-9).sum()),
            'maximum_q_tier':float(data.q_tier.max()),'model_gate':metadata,
            'model_diversion':gate_info[str(div)],'model_source':gate_info[str(source)],
            'section_import':cl.get_cross_section_report(),'clock_rollback_verified':True,
            'state_and_controls_replay_verified':True,'missing_water_targets':hours-len(history_obs)}
        if downstream_stage_file:
            summary['downstream_boundary']=downstream_metadata
            summary['downstream_boundary_report']=cl.get_downstream_stage_report_sim()
        if warmup_summary is not None:summary['warmup']=warmup_summary
        if controlled:
            pd.DataFrame({'tm':data.time,reach.upstream_gate_name+'_Q_m3s':data.source_Q_m3s,
                reach.boundary_model_name+'_Q_m3s':data.q_command_m3s,reach.target_gate_name+'_e_m':data.opening_command_m}).to_csv(
                out/boundary_filename,index=False,encoding='utf-8-sig')
            if downstream_stage_file:
                boundaries=pd.read_csv(out/boundary_filename)
                boundaries[reach.target_gate_name+'_downstream_wh_m']=np.interp(
                    [datetime.fromisoformat(t).timestamp() for t in data.time],
                    [s['time'] for s in payload['samples']],[s['water_level_m'] for s in payload['samples']])
                boundaries.to_csv(out/boundary_filename,index=False,encoding='utf-8-sig')
        if snapshot_interval:cl.save_states(f'periodic_{hours}')
        if after_run is not None:
            summary['snapshot_refinement']=after_run(cl,execute,data,summary)
        return summary
    finally:da._release_isolated_clients(clients,copies)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours',type=int,default=168)
    parser.add_argument('--opening-max',type=float,default=.38)
    parser.add_argument('--output',type=Path,default=ROOT/'data/dayudu/output/fujialing_reconstruction_comparison')
    args=parser.parse_args();case=ROOT/'data/dayudu';out=args.output;out.mkdir(parents=True,exist_ok=True)
    start=datetime(2026,4,14)
    baseline=run(case,out,start,args.hours,False,args.opening_max)
    reconstruction=run(case,out,start,args.hours,True,args.opening_max)
    report={'pool_id':3,'reach':'15号桥节制闸→富家岭节制闸','period':[start.isoformat(),(start+timedelta(hours=args.hours)).isoformat()],
        'baseline':baseline,'reconstruction':reconstruction,
        'dll_sha256':hashlib.sha256((ROOT/'tools/OcisMILPNet.dll').read_bytes()).hexdigest(),
        'water_acceptance':'NRMSE<5%, NSE>0.9, R2>0.9, KGE>0.9',
        'NRMSE_definition':'RMSE/(observed maximum-observed minimum)',
        'opening_trigger':'mean((Qset-Qexecuted)/Qexecuted) over five completed cycles >2%',
        'q_tiers':'±10%, ±20%, ... ±100% of known diversion at each timestamp; one level per failed rolling five-cycle window',
        'known_diversion_source':'action_obs_td.csv 富家岭提水 (the named direct turnout of this pool); planned d3 retained in audit for comparison',
        'upstream_flow_source':'action_td.csv 15号桥节制闸; independent gate inflow observations unavailable',
        'opening_source':'gates_e_sum_td.csv 富家岭节制闸 /1000; equivalent total opening; hole-wise 2026 data unavailable',
        'opening_max_m':args.opening_max,'opening_max_provenance':'existing dayudu reconstruction configuration; historical openings reach 1.6 m, so this is not a verified physical stroke',
        'downstream_stage_source':'model evolves downstream nodes; no 富家岭 entry in 2026 stage2_td.csv',
        'hydraulic_changes':'gate formulas use g.Bwid, not zero bottom width of arc section; update_gates_e uses actual topology indices; coefficients not calibrated to history'}
    (out/'reconstruction_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
