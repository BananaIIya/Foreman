"""Return actual worker observations once; retain full commands for on-demand reads."""
import copy
import hashlib
import json


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def command_ref(command):
    text=command if isinstance(command,str) else canonical(command)
    return {'command_sha256':hashlib.sha256(text.encode()).hexdigest(),'command_chars':len(text)}


def project(result,raw_file):
    """Only deduplicate exact observed records; never merge conflicting outcomes."""
    evidence=copy.deepcopy(result.get('evidence',{}))
    if not isinstance(evidence,dict):
        return {'representation':'unrecognized_worker_evidence','evidence':evidence,'raw_evidence_file':raw_file}
    text=evidence.get('text','')
    try:parsed=json.loads(text)
    except (TypeError,ValueError):parsed=None
    if not isinstance(parsed,dict):parsed={}
    skipped=[];history_notes=[];proposed_reports=[];sequences=[]
    def project_commands(items):
        observations=[]
        for command in items:
            if isinstance(command,dict):
                value=copy.deepcopy(command)
                if 'command' in value:value.update(command_ref(value.pop('command')))
                observations.append(value)
            else:observations.append({'unrecognized_observation':copy.deepcopy(command)})
        return observations
    for source,items in [('evidence.commands',evidence.get('commands')),
                         ('report.actual_command_results',parsed.get('actual_command_results'))]:
        if isinstance(items,list):sequences.append((source,items))
    history_commands=[]
    for row in parsed.get('protocol_history',[]):
        if not isinstance(row,dict):history_notes.append(copy.deepcopy(row));continue
        history_commands.extend(row.get('actual_command_results',[]))
        skipped.extend(command_ref(c) for c in row.get('unexecuted_commands',[]))
        if 'worker_report_before_execution' in row:
            proposed_reports.append(copy.deepcopy(row['worker_report_before_execution']))
        # The same report is present in the canonical report fields; keep errors and
        # unexpected fields from history, not copies of code and actual observations.
        note={k:copy.deepcopy(v) for k,v in row.items() if k not in (
            'actual_command_results','unexecuted_commands','worker_report_before_execution')}
        if note:history_notes.append(note)
    if history_commands:sequences.append(('report.protocol_history',history_commands))
    unique=[];seen_sequences=set()
    for source,items in sequences:
        marker=canonical(items)
        if marker in seen_sequences:continue
        seen_sequences.add(marker)
        unique.append({'source':source,'results':project_commands(items)})
    observations=unique[0]['results'] if unique else []
    skipped.extend(command_ref(c) for c in parsed.get('unexecuted_commands',[]))
    report={k:copy.deepcopy(v) for k,v in parsed.items() if k not in (
        'actual_command_results','protocol_history','unexecuted_commands')}
    if not parsed and text:report['unparsed_worker_report']=text
    extra={k:copy.deepcopy(v) for k,v in evidence.items() if k not in ('text','commands','command_count')}
    return {'representation':'worker_observations_once','worker_status':result.get('status'),
            'worker_report_before_execution':report,'report_is_unverified':True,
            'command_results':observations,'unexecuted_commands':skipped,
            'alternate_command_sequences':unique[1:],
            'conflicting_command_representations':len(unique)>1,
            'reported_command_count':evidence.get('command_count'),
            'protocol_notes':history_notes,'controller_evidence':extra,
            'worker_proposed_reports':proposed_reports,
            'raw_evidence_file':raw_file,'correctness_proven':False,
            'note':'Full command code and original public worker evidence remain in raw_evidence_file. '
                   'Read that current workspace file or the implementation files when needed. '
                   'Exit codes and observations are preserved here without duplicate command code. '
                   'Existing output_truncated flags remain authoritative; this projection does not recover omitted bytes.'}


async def publish(result,folder,remote_path,upload):
    """A failed evidence upload falls back to the unchanged full public payload."""
    folder.mkdir(parents=True,exist_ok=True)
    original=copy.deepcopy(result.get('evidence',{}))
    raw=canonical({'version':1,'phase':result.get('phase'),'status':result.get('status'),'evidence':original}).encode()
    path=folder/'worker-evidence-full.json';path.write_bytes(raw)
    reference={'path':remote_path,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'available':False}
    try:
        await upload(path,remote_path)
        reference['available']=True
        projected=project(result,reference)
    except Exception as exc:
        reference['error_type']=type(exc).__name__
        projected=original  # Never replace unreadable full evidence with hashes alone.
    receipt={'raw_evidence_file':reference,'raw_evidence_chars':len(canonical(original)),
             'returned_evidence_chars':len(canonical(projected)),
             'fallback_to_full_evidence':not reference['available'],'model_calls':0,
             'correctness_proven':False}
    (folder/'worker-evidence-returned.json').write_text(json.dumps(projected,ensure_ascii=False,indent=2)+'\n')
    (folder/'worker-evidence-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return projected
