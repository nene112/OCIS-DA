# -*- coding: utf-8 -*-
"""PID preheating of water level; carry the evolved hydraulic state into forecast.

PID acts on temporary upstream inflow. Turnout, opening and downstream stage
stay at their starting values. No initial-depth fitting or depth overwrite.
"""
import json
from copy import deepcopy
import numpy as np
import pandas as pd
from pid_reconstruction import PIDConfig,PIDController


def prepare_warm_start(client, execute, *, target_depth, gate_metadata, source_gate_id,
                       formal_payload, controls, out, settings):
    if formal_payload is None:
        raise ValueError('PID warmup requires validated external tailwater')
    minimum=int(settings.get('duration_seconds',21600))
    maximum=int(settings.get('max_duration_seconds',max(minimum,86400)))
    step=int(settings.get('step_seconds',300))
    window=int(settings.get('stable_window_seconds',1800))
    tolerance=float(settings.get('depth_tolerance_m',.005))
    range_tolerance=float(settings.get('stable_depth_range_m',.0025))
    source_range_tolerance=float(settings.get('stable_source_range_m3s',.05))
    if step<=0 or minimum<step or maximum<minimum or window<step or any(v%step for v in [minimum,maximum,window]):
        raise ValueError('invalid PID warmup durations or step')
    if not np.isfinite(target_depth) or target_depth<=0 or tolerance<=0 or range_tolerance<=0:
        raise ValueError('invalid PID target or tolerances')
    clock_before=json.loads(client.get_time_param())
    if clock_before['solver_elapsed_seconds']!=0:
        raise ValueError('warmup must start before formal simulation')
    def nodes():
        return {key:json.loads(client.get_stepdata_sim(key)) for key in ['node_h','node_Q']}
    def depth():return float(nodes()['node_h'][gate_metadata['nIndexUp']])
    native_initial_depth=depth()
    q,e,source=map(float,controls)
    config=PIDConfig(kp=float(settings.get('kp',3)),ki=float(settings.get('ki',.5)),
        kd=float(settings.get('kd',.1)),action_sign=1,integral_limit=float(settings.get('integral_limit',2)),
        q_min=float(settings.get('source_min_m3s',0)),q_max=float(settings.get('source_max_m3s',max(1,source*2))),
        q_max_step=float(settings.get('source_max_step_m3s',max(.05,source*.1))),
        anti_windup_gain=.2,bias_estimator_enabled=False,adaptive_gains=False)
    config.validate();pid=PIDController(config,step/3600)
    client.set_time_param(dict(start_time=clock_before['start_time'],step_dt=step,T=clock_before['T']))
    client.set_delta_t(step)
    start=clock_before['start_t'];warm_payload=deepcopy(formal_payload)
    level=float(np.interp(start,[s['time'] for s in formal_payload['samples']],
                                [s['water_level_m'] for s in formal_payload['samples']]))
    warm_payload['samples']=[{'time':start+k*step,'water_level_m':level} for k in range(maximum//step+2)]
    client.set_GatesDownstreamStage_sim(warm_payload)
    previous_source=source;trace=[];converged=False;failure=None
    stable_count=window//step+1
    try:
        for k in range(maximum//step):
            before_depth=depth();error=target_depth-before_depth
            correction,integral,derivative=pid.correction(error)
            raw=source+correction
            applied=float(np.clip(np.clip(raw,previous_source-config.q_max_step,previous_source+config.q_max_step),
                                  config.q_min,config.q_max))
            antiwindup=pid.track_execution(raw,applied)
            client.save_states('warmup_step')
            before_clock=json.loads(client.get_time_param())['solver_elapsed_seconds']
            try:
                # Keep the monotone local clock; execute overrides this pool's
                # source, turnout and opening after the schedule update.
                result=execute(k,q,e,applied)
            except RuntimeError:
                client.set_states('warmup_step')
                raise
            after_clock=json.loads(client.get_time_param())['solver_elapsed_seconds']
            assert abs(after_clock-before_clock-step)<1e-9
            actual=depth();previous_source=applied
            trace.append(dict(trial_id=0,elapsed_seconds=after_clock,h_up_m=actual,target_depth_m=target_depth,
                error_before_m=error,error_after_m=target_depth-actual,source_Q_m3s=applied,
                formal_source_Q_m3s=source,pid_raw_source_Q_m3s=raw,pid_correction_m3s=correction,
                pid_integral=pid.integral,pid_derivative=derivative,pid_antiwindup=antiwindup,
                output_limited=abs(raw-applied)>1e-12,gate_Q_m3s=result['gate_Q'],
                diversion_set_m3s=q,diversion_executed_m3s=result['diversion_Q'],opening_m=e,
                cumulative_mass_residual_m3=result['boundary_cumulative_mass_residual_m3']))
            recent=trace[-stable_count:]
            if after_clock>=minimum and len(recent)==stable_count:
                errors=[abs(r['error_after_m']) for r in recent]
                values=[r['h_up_m'] for r in recent]
                converged=bool(max(errors)<=tolerance and np.ptp(values)<=range_tolerance
                               and np.ptp([r['source_Q_m3s'] for r in recent])<=source_range_tolerance)
            if k%12==0:print(f'PID warmup {after_clock/3600:.2f}h: h={actual:.4f}, error={target_depth-actual:+.4f}, source={applied:.4f}',flush=True)
            if converged:break
    except RuntimeError as exc:failure=str(exc)
    if not trace:raise RuntimeError(f'PID warmup failed before first completed step: {failure}')
    duration=int(trace[-1]['elapsed_seconds'])
    pd.DataFrame(trace).to_csv(out/'warmup_trace.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(trace).to_csv(out/'warmup_selected_trace.csv',index=False,encoding='utf-8-sig')
    last=dict(trial_id=0,initial_seed_depth_m=native_initial_depth,end_depth_m=depth(),
              error_m=depth()-target_depth,failed=failure is not None)
    pd.DataFrame([last]).to_csv(out/'warmup_trials.csv',index=False,encoding='utf-8-sig')
    warm_clock=json.loads(client.get_time_param());warm_report=client.get_downstream_stage_report_sim()
    recent=trace[-stable_count:]
    initial=dict(enabled=True,pseudo_time=True,method='PID upstream-inflow water-level warmup',
        actuator='temporary upstream inflow; formal inflow restored at handoff',
        duration_seconds=duration,minimum_duration_seconds=minimum,maximum_duration_seconds=maximum,
        step_seconds=step,target_depth_m=target_depth,cold_depth_before_warmup_m=native_initial_depth,
        selected_trial=last,initial_match_passed=converged,depth_tolerance_m=tolerance,
        stable_window_seconds=window,stable_depth_range_m=range_tolerance,
        stable_source_range_m3s=source_range_tolerance,
        achieved_source_range_m3s=float(np.ptp([r['source_Q_m3s'] for r in recent])),
        achieved_depth_range_m=float(np.ptp([r['h_up_m'] for r in recent])),
        achieved_max_abs_error_m=max(abs(r['error_after_m']) for r in recent),
        formal_initial_depth_m=depth(),trial_count=1,iteration_count=len(trace),
        pid=dict(kp=config.kp,ki=config.ki,kd=config.kd,dt_hours=step/3600,
                 q_min=config.q_min,q_max=config.q_max,q_max_step=config.q_max_step,
                 integral_limit=config.integral_limit,anti_windup_gain=config.anti_windup_gain),
        frozen_controls=dict(diversion_Q_m3s=q,opening_m=e,downstream_water_level_m=level),
        formal_source_Q_m3s=source,final_pid_source_Q_m3s=previous_source,
        warmup_clock=warm_clock,warmup_boundary_report=warm_report,failure=failure,
        stationary_state_claimed=False,last_step_depth_change_m=trace[-1]['h_up_m']-trace[-2]['h_up_m'] if len(trace)>1 else None)
    summary_path=out/'warmup_summary.json'
    summary_path.write_text(json.dumps(initial,ensure_ascii=False,indent=2),encoding='utf-8')
    if not converged or failure:
        raise RuntimeError('PID warmup did not continuously stabilize at target; see warmup_summary.json')
    assert abs(warm_report['gates'][0]['cumulative_mass_residual_m3'])<.1
    client.save_states('warmup_selected')
    hydraulic_before=nodes()
    client.set_time_param(dict(start_time=clock_before['start_time'],step_dt=clock_before['step_dt'],T=clock_before['T']))
    client.set_delta_t(clock_before['step_dt'])
    client.set_GatesDownstreamStage_sim(formal_payload)
    client.set_GatesFlow_byID_sim(source_gate_id,source)
    reset_clock=json.loads(client.get_time_param())
    for key,values in hydraulic_before.items():np.testing.assert_array_equal(values,nodes()[key])
    assert reset_clock['solver_elapsed_seconds']==0 and reset_clock['start_t']==start
    assert client.get_downstream_stage_report_sim()['gates'][0]['cumulative_mass_residual_m3']==0
    initial.update(hydraulics_preserved_on_clock_reset=True,formal_clock_reset=reset_clock,
                   formal_tailwater_accounting_reset=True,formal_source_command_restored=True)
    summary_path.write_text(json.dumps(initial,ensure_ascii=False,indent=2),encoding='utf-8')
    client.save_states('formal_initial')
    bed=json.loads(client.get_stepdata_sim('node_zb'))
    pd.DataFrame({'node_id':np.arange(len(bed)),'h_m':hydraulic_before['node_h'],
                  'Q_m3s':hydraulic_before['node_Q'],'bed_elevation_m':bed,
                  'water_surface_elevation_m':np.asarray(bed)+np.asarray(hydraulic_before['node_h'])}).to_csv(
                      out/'formal_initial_state.csv',index=False,encoding='utf-8-sig')
    return initial
