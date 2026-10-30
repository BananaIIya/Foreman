"""Execute a no-tools worker's proposed batch once, retaining actual failures."""
import json
from hybrid_agent import parse_json
from .execution import observe_command

SCHEMA={'type':'object','additionalProperties':False,
        'required':['status','commands','report'],
        'properties':{'status':{'type':'string','enum':['commands','needs_advisor']},
                      'commands':{'type':'array','maxItems':2,'items':{'type':'string','maxLength':20000}},
                      'report':{'type':'string','maxLength':2000}}}
POLICY='''Implement only the supplied bounded work order using its actual current files and precise procedure. Return the required JSON in one no-tools model turn. At most two commands will execute in the remote /app task workspace using Bash -e -o pipefail. Use programs to read actual input data. For implementation preserve the given specialist choices. For an experiment, implement the supplied algorithm, measure actual observations and report the evidence needed by decision_rule, including counterexamples and failures. The hypothesis may be FALSE; do not manufacture observations to support it, silently tune until it appears true, or turn a hypothesis into a given fact. Discovering that the hypothesis is false is a useful experiment result, not permission to invent a replacement algorithm. If the procedure itself is underspecified, return needs_advisor with no commands and identify that missing implementation detail. No hidden grader, historical answers, other models or delegation. Describe intended checks honestly; actual results return to the same expert for interpretation. Do not claim an unexecuted check passed.'''

POLICY += '\nBefore commissioning or implementing expensive computation, account for actual input scale and the 300-second command deadline. State in execution_plan what scale is already observed or how it will be measured, the dominant time/memory growth, existing measured data to reuse, and a bounded performance check when needed. The worker may select a mathematically equivalent efficient library implementation of the prescribed procedure without inventing a specialist conclusion. Avoid computing an entire pairwise matrix, unrestricted search, or full output range when the work order asks for a limited subset. Reuse actual intermediate files rather than repeating data conversion or preprocessing. For expensive or uncertain scaling, first measure a small representative workload or use a defensible operation count, in the same two-command budget. Such a probe establishes runtime feasibility only, never original-task correctness: complete the requested full-input work afterward. Do not silently reduce resolution, discard original inputs, change the scientific method or weaken acceptance to fit time. If full work cannot fit, retain actual partial observations and state the precise bottleneck for the expert; do not wait for a large known-infeasible computation to time out.'

async def execute_batch(result, run_command):
    observed=[];skipped=[]
    try:
        import jsonschema
        proposed=parse_json(result['evidence']['text'])
        jsonschema.validate(proposed,SCHEMA)
        commands=proposed['commands']
        if commands and proposed['status']!='commands':raise ValueError('Non-command status with commands')
        if any(not c.strip() for c in commands):raise ValueError('Empty command')
    except (ValueError,TypeError,KeyError,jsonschema.ValidationError):
        result['status']='invalid_worker_protocol'
        return result
    for index,command in enumerate(commands):
        row=await observe_command(command,run_command)
        observed.append(row)
        if row['exit_code']!=0:
            skipped=commands[index+1:];break
    result.update(status='preparation_observed' if observed else 'needs_advisor',
        evidence={'text':json.dumps({'worker_report_before_execution':proposed['report'],
            'actual_command_results':observed,'unexecuted_commands':skipped,'correctness_proven':False}),
            'commands':observed,'command_count':len(observed)},native_call_limit=1,command_limit=2)
    return result
