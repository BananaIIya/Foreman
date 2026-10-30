"""Execute a no-tools worker's proposed batch once, retaining actual failures."""
import json
from hybrid_agent import parse_json

SCHEMA={'type':'object','additionalProperties':False,
        'required':['status','commands','report'],
        'properties':{'status':{'type':'string','enum':['commands','needs_advisor']},
                      'commands':{'type':'array','maxItems':2,'items':{'type':'string','maxLength':20000}},
                      'report':{'type':'string','maxLength':2000}}}
POLICY='''Implement only the supplied routine work order using its actual current files and precise procedure. Return the required JSON in one no-tools model turn. At most two commands will execute in the remote /app task workspace using Bash -e -o pipefail. Use programs to read actual input data. Preserve resolved specialist choices; if missing information requires specialist inference, return needs_advisor with no commands. No hidden grader, historical answers, other models or delegation. Describe intended checks honestly; their results will be returned to the same expert after execution. Do not claim an unexecuted check passed.'''


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
        response=await run_command(command)
        row={'command':command,'exit_code':response.return_code,
             'stdout':(response.stdout or '')[:14000],'stderr':(response.stderr or '')[:4000],
             'output_truncated':getattr(response,'stdout_truncated',False) or getattr(response,'stderr_truncated',False)
                or len(response.stdout or '')>14000 or len(response.stderr or '')>4000}
        observed.append(row)
        if response.return_code!=0:
            skipped=commands[index+1:];break
    result.update(status='preparation_observed' if observed else 'needs_advisor',
        evidence={'text':json.dumps({'worker_report_before_execution':proposed['report'],
            'actual_command_results':observed,'unexecuted_commands':skipped,'correctness_proven':False}),
            'commands':observed,'command_count':len(observed)},native_call_limit=1,command_limit=2)
    return result
