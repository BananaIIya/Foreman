"""Native Cursor worker with controller-enforced command/call boundaries.

Cursor SDK performs inference only. The controller executes returned commands
in Daytona and returns observed outputs; no generated command runs on the host.
"""
import json
from pathlib import Path
from v3_runtime.sdk import advisor_turn

PROTOCOL = '''You are the executing worker. You have no direct tools in this model call. Return JSON only with status (commands, completed, or needs_advisor), commands (array of shell command strings), and report (object with summary, relevant_paths, critical_question, checks, unresolved_assumptions). The controller executes your commands in the task's /app directory and returns actual stdout/stderr/exit codes. Use shell here-documents when creating files. Never claim commands ran until their results are returned. Commands may be batched, but keep each bounded and its output concise. Files and installed dependencies persist, shell variables do not. No delegation, other model calls, hidden grading, or searching benchmark solutions. Only original task inputs and current execution evidence are available. If the remaining call/command budget is insufficient, return needs_advisor with a concise factual report. Implement an explicitly specified expert procedure, including an experiment whose hypothesis may be false. Escalate missing algorithmic choices; do not invent them or treat an unproven hypothesis as a fact. Finish easy integration in the few allowed commands. Original images, when provided, are attached directly to this worker call. Inspect them visually; request specialist interpretation only for a concrete uncertainty. Do not spend commands decoding an already attached image into text.'''

async def bounded_worker(folder, prompt, effort, cap, run_command, native=advisor_turn, execution_label='Daytona task workspace only'):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    discovery=True  # Every V46 worker phase is one bounded two-command batch.
    max_commands=2 if discovery else 3
    max_calls=1 if discovery else 4
    request={'model':'grok-4.7-'+effort,'provider':'cursor-sdk-bounded','effort':effort,
        'prompt':prompt,'budget_usd':cap,'command_limit':max_commands,'native_call_limit':max_calls,
        'execution_environment':execution_label,'tools':[]}
    (folder/'request.json').write_text(json.dumps(request,indent=2)+'\n')
    history=[];commands=[];calls=[];cost=0.0;complete=True;last_report={}
    counters={k:0 for k in ['inputTokens','outputTokens','cacheReadTokens','cacheWriteTokens']}
    terminal='controller_call_limit'
    for index in range(max_calls):
        if index and (not complete or cost>=cap):
            terminal='controller_cost_boundary';break
        remaining=max_commands-len(commands)
        if remaining <= 0:
            # Actual outputs already travel to the gate. Another worker call
            # cannot execute anything and can consume the expert's budget.
            terminal='controller_command_limit'
            break
        payload={'stage':prompt,'current_phase_history':history,'commands_remaining':remaining,
            'model_calls_remaining_including_this':max_calls-index,'remaining_cost_target_usd':max(0,cap-cost)}
        initial_note=(' This bounded work unit has one model call. Return a bounded command batch; the cheap decision model receives actual results directly afterward. Your report describes intended checks, not their outcome. Do not claim success before execution.' if discovery else '')
        reply=await native(folder/'rounds'/str(index),PROTOCOL+initial_note+'\n'+json.dumps(payload,ensure_ascii=False),effort)
        c=reply['usage']['cost_usd'];n=reply['usage'].get('native_usage')
        if c is None or n is None:complete=False
        else:
            cost+=c
            for k in counters:counters[k]+=n[k]
        calls.append({'round':index,'status':reply['status'],'cost_usd':c,'native_model':reply.get('native_model'),
            'native_error':reply.get('native_error'),'text_origin':reply.get('text_origin')})
        answer=reply.get('patch')
        if not isinstance(answer,dict) or answer.get('status') not in ('commands','completed','needs_advisor') or not isinstance(answer.get('commands'),list) or not isinstance(answer.get('report'),dict):
            history.append({'protocol_error':'Expected status, commands array and report object','public_answer':reply['evidence']['text']})
            terminal='invalid_worker_protocol';continue
        last_report=answer['report']
        batch=answer['commands']
        if batch and answer['status']!='commands':
            history.append({'protocol_error':'Non-command status cannot execute commands','answer':answer});continue
        if answer['status']!='commands':
            terminal=answer['status'];break
        if not batch or len(batch)>remaining or any(not isinstance(x,str) or not x.strip() or len(x)>20000 for x in batch):
            history.append({'protocol_error':'Command list exceeds remaining limit or is invalid','answer':answer})
            terminal='controller_command_limit';break
        observed=[]
        for command in batch:
            output=await run_command(command)
            row={'command':command,'exit_code':output.return_code,'stdout':(output.stdout or '')[:14000],
                'stderr':(output.stderr or '')[:4000],
                'output_truncated':getattr(output,'stdout_truncated',False) or getattr(output,'stderr_truncated',False) or len(output.stdout or '')>14000 or len(output.stderr or '')>4000}
            commands.append(row);observed.append(row)
            if output.return_code!=0:break
        history.append({'worker_report_before_execution':last_report,'actual_command_results':observed,
                        'unexecuted_commands':batch[len(observed):]})
        if discovery:
            terminal='preparation_observed'
            break
    evidence={'status':terminal,'summary':last_report.get('summary'),
        'critical_question':last_report.get('critical_question'),
        'relevant_paths':last_report.get('relevant_paths',[]),
        'reported_checks':last_report.get('checks',[]),'actual_command_results':commands,
        'unresolved_assumptions':last_report.get('unresolved_assumptions',[]),
        'controller_boundaries':{'commands_executed':len(commands),'native_calls':len(calls),'command_limit':max_commands,'call_limit':max_calls},
        'protocol_history':history,
        'preparation_batch_handoff':discovery,
        'report_is_before_execution':discovery and bool(commands),
        'handoff_note':'Actual command outputs are evidence for the decision model; preparation_observed is not a correctness or task-completion claim.' if discovery else None,
        'native_errors':[c for c in calls if c['status']!='completed']}
    result={'status':terminal,'native_model':'Grok 4.7 256K '+effort.capitalize(),
        'usage':{'cost_usd':cost if complete else None,'native_usage':counters if complete else None,
            'known_cost_lower_bound_usd':cost},'native_calls':calls,
        'evidence':{'text':json.dumps(evidence,ensure_ascii=False),'commands':commands,'command_count':len(commands)}}
    (folder/'phase-result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
