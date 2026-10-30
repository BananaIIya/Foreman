"""A progress checkpoint without completion cannot become a delivery verdict."""
import json


def unfinished_reason(result):
    text=result.get('evidence',{}).get('text','')
    reports=[]
    decoder=json.JSONDecoder()
    for pos,ch in enumerate(text):
        if ch!='{':continue
        try:report,_=decoder.raw_decode(text[pos:])
        except ValueError:continue
        if isinstance(report,dict) and report.get('status') in ('completed','needs_advisor','needs_context','blocked'):
            reports.append(report)
    status=reports[-1]['status'] if reports else None
    if status in ('needs_advisor','needs_context','blocked'):
        return 'expert_explicitly_reported_unfinished_work'
    if result.get('interrupted')=='budget_boundary_exceeded' and status!='completed':
        return 'native_checkpoint_without_completion_claim'
    return None
