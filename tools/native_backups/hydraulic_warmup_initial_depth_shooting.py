# -*- coding: utf-8 -*-
"""Condition a hot start with frozen starting boundaries and real solver steps.

Only the cold initial depth is calibrated. No post-simulation depth overwrite is
allowed. Warmup uses pseudo time, not invented historical observations.
"""
import json
from copy import deepcopy
import numpy as np
import pandas as pd


def prepare_warm_start(client, execute, *, target_depth, gate_metadata,
                       formal_payload, controls, out, settings):
    if formal_payload is None:
        raise ValueError('warmup requires the validated external tailwater path')
    duration=int(settings.get('duration_seconds',3600))
    step=int(settings.get('step_seconds',300))
    tolerance=float(settings.get('depth_tolerance_m',.02))
    if duration<step or duration%step or step<=0 or tolerance<=0:
        raise ValueError('invalid warmup duration/step/tolerance')
    clock_before=json.loads(client.get_time_param())
    if clock_before['solver_elapsed_seconds']!=0:
        raise ValueError('warmup must begin before any formal solver steps')
    def nodes():
        return {key:json.loads(client.get_stepdata_sim(key)) for key in ['node_h','node_Q']}
    def depth():return float(nodes()['node_h'][gate_metadata['nIndexUp']])
    native_initial_depth=depth()
    # Rebase the wrapper clock and epoch while retaining hydraulic arrays.
    client.set_time_param(dict(start_time=clock_before['start_time'],
                               step_dt=step,T=clock_before['T']))
    client.set_delta_t(step)
    warm_payload=deepcopy(formal_payload)
    start=clock_before['start_t']
    times=[s['time'] for s in formal_payload['samples']]
    levels=[s['water_level_m'] for s in formal_payload['samples']]
    level=float(np.interp(start,times,levels))
    warm_payload['samples']=[{'time':start+k*step,'water_level_m':level}
                            for k in range(duration//step+2)]
    client.set_GatesDownstreamStage_sim(warm_payload)
    client.save_states('warmup_cold')
    trials=[]; traces=[];best=None
    q,e,source=controls
    def attempt(initial_depth):
        nonlocal best
        client.set_states('warmup_cold')
        client.set_all_inih(float(initial_depth))
        trial_id=len(trials)
        trace=[]
        try:
            for k in range(duration//step):
                before=json.loads(client.get_time_param())['solver_elapsed_seconds']
                result=execute(k,q,e,source)
                after=json.loads(client.get_time_param())['solver_elapsed_seconds']
                assert abs(after-before-step)<1e-9
                trace.append(dict(trial_id=trial_id,initial_seed_depth_m=initial_depth,
                    elapsed_seconds=after,h_up_m=result['h_up'],target_depth_m=target_depth,
                    gate_Q_m3s=result['gate_Q'],source_Q_m3s=source,diversion_set_m3s=q,
                    diversion_executed_m3s=result['diversion_Q'],opening_m=e,
                    cumulative_mass_residual_m3=result['boundary_cumulative_mass_residual_m3']))
            actual=depth();error=actual-target_depth
            record=dict(trial_id=trial_id,initial_seed_depth_m=initial_depth,
                        end_depth_m=actual,error_m=error,failed=False)
            if best is None or abs(error)<abs(best['error_m']):
                best=record.copy();client.save_states('warmup_selected')
            print(f'warmup trial {trial_id}: seed={initial_depth:.4f}, end={actual:.4f}, error={error:+.4f}',flush=True)
        except RuntimeError as exc:
            record=dict(trial_id=trial_id,initial_seed_depth_m=initial_depth,failed=True,error=str(exc))
        trials.append(record);traces.extend(trace)
        return None if record['failed'] else record
    center=attempt(target_depth)
    if center is None or abs(center['error_m'])>tolerance:
        lower=float(settings.get('initial_depth_min_m',max(.05,.5*target_depth)))
        upper=float(settings.get('initial_depth_max_m',1.5*target_depth))
        low=attempt(lower);high=attempt(upper)
        if low is not None and high is not None and low['error_m']*high['error_m']<=0:
            for _ in range(int(settings.get('max_initial_depth_trials',10))):
                middle=(lower+upper)/2;result=attempt(middle)
                if result is None:break
                if abs(result['error_m'])<=tolerance:break
                if low['error_m']*result['error_m']<=0:
                    upper=middle;high=result
                else:lower=middle;low=result
    pd.DataFrame(trials).to_csv(out/'warmup_trials.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(traces).to_csv(out/'warmup_trace.csv',index=False,encoding='utf-8-sig')
    if best is None:raise RuntimeError('all warmup attempts failed; formal simulation not started')
    client.set_states('warmup_selected')
    hydraulic_before=nodes()
    warm_clock=json.loads(client.get_time_param())
    warm_report=client.get_downstream_stage_report_sim()
    client.set_time_param(dict(start_time=clock_before['start_time'],
                               step_dt=clock_before['step_dt'],T=clock_before['T']))
    client.set_delta_t(clock_before['step_dt'])
    client.set_GatesDownstreamStage_sim(formal_payload)
    hydraulic_after=nodes()
    for key in hydraulic_before:
        np.testing.assert_array_equal(hydraulic_before[key],hydraulic_after[key])
    reset_clock=json.loads(client.get_time_param())
    assert reset_clock['solver_elapsed_seconds']==0
    assert reset_clock['start_t']==clock_before['start_t']
    assert client.get_downstream_stage_report_sim()['gates'][0]['cumulative_mass_residual_m3']==0
    selected_trace=[row for row in traces if row['trial_id']==best['trial_id']]
    pd.DataFrame(selected_trace).to_csv(out/'warmup_selected_trace.csv',index=False,encoding='utf-8-sig')
    initial=dict(enabled=True,pseudo_time=True,method='frozen starting boundaries; cold initial-depth shooting',
        duration_seconds=duration,step_seconds=step,target_depth_m=target_depth,
        cold_depth_before_warmup_m=native_initial_depth,selected_trial=best,
        initial_match_passed=abs(best['error_m'])<=tolerance,depth_tolerance_m=tolerance,
        formal_initial_depth_m=depth(),frozen_controls=dict(source_Q_m3s=source,diversion_Q_m3s=q,opening_m=e,
            downstream_water_level_m=level),trial_count=len(trials),hydraulics_preserved_on_clock_reset=True,
        formal_clock_reset=reset_clock,warmup_clock=warm_clock,warmup_boundary_report=warm_report,
        formal_boundary_accounting_reset=True,
        stationary_state_claimed=False,last_step_depth_change_m=(selected_trace[-1]['h_up_m']-selected_trace[-2]['h_up_m'])
            if len(selected_trace)>1 else None)
    (out/'warmup_summary.json').write_text(json.dumps(initial,ensure_ascii=False,indent=2),encoding='utf-8')
    if settings.get('require_initial_match',True) and not initial['initial_match_passed']:
        raise RuntimeError('warmup did not match initial observed depth; see warmup_summary.json')
    client.save_states('formal_initial')
    pd.DataFrame({'node_id':np.arange(len(hydraulic_after['node_h'])),
                  'h_m':hydraulic_after['node_h'],'Q_m3s':hydraulic_after['node_Q'],
                  'bed_elevation_m':json.loads(client.get_stepdata_sim('node_zb'))}).to_csv(
                      out/'formal_initial_state.csv',index=False,encoding='utf-8-sig')
    return initial
