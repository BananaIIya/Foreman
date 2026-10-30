"""Reference exact prior expert observations only within the same retained thread."""
import copy
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def resume_delta(payload, memory, thread_id):
    if not memory or memory['thread_id'] != thread_id or memory.get('compacted'):
        return payload, None
    known = memory['evidence']
    evidence = payload.get('worker_evidence')
    reference = {'same_native_thread_reference': {
        'thread_id':thread_id,'turn_id':memory['turn_id'],'source_phase':memory['phase'],
        'evidence_sha256':digest(known),
        'note':'Exact observations already retained in this native task thread; the new routing contract and any new execution checks remain in this request.'},
        'native_status':known.get('native_status'),'interrupted':known.get('interrupted')}
    if evidence == known:
        replacement = reference
        location = 'worker_evidence'
    else:
        if not isinstance(evidence,dict):return payload,None
        try:report=json.loads(evidence.get('text',''))
        except (TypeError,ValueError):return payload,None
        if not isinstance(report,dict) or report.get('expert_evidence') != known:
            return payload,None
        if json.dumps(report,ensure_ascii=False)!=evidence['text']:
            return payload,None
        report['expert_evidence'] = reference
        replacement = dict(evidence,text=json.dumps(report,ensure_ascii=False))
        location = 'worker_evidence.text.expert_evidence'
    result = copy.deepcopy(payload)
    result['worker_evidence'] = replacement
    receipt = {'location':location,'reference':reference,
               'original_evidence_sha256':digest(evidence),
               'result_evidence_sha256':digest(replacement),
               'original_evidence_characters':len(json.dumps(evidence,ensure_ascii=False)),
               'result_evidence_characters':len(json.dumps(replacement,ensure_ascii=False)),
               'raw_logs_unchanged':True,'new_model_call':False}
    return result,receipt


def restore_evidence(evidence, receipt, known):
    if receipt['reference']['same_native_thread_reference']['evidence_sha256'] != digest(known):
        raise ValueError('Prior evidence does not match the reference')
    if digest(evidence) != receipt['result_evidence_sha256']:
        raise ValueError('Compact evidence does not match the receipt')
    if receipt['location']=='worker_evidence':
        restored=known
    elif receipt['location']=='worker_evidence.text.expert_evidence':
        report=json.loads(evidence['text'])
        if report.get('expert_evidence')!=receipt['reference']:raise ValueError('Changed evidence reference')
        report['expert_evidence']=known
        restored=dict(evidence,text=json.dumps(report,ensure_ascii=False))
    else:raise ValueError('Unknown reference location')
    if digest(restored)!=receipt['original_evidence_sha256']:
        raise ValueError('Original evidence cannot be reconstructed exactly')
    return restored
