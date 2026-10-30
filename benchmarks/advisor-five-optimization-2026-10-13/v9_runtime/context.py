"""Send only new evidence when resuming this task's native Sol conversation."""

def continuation_payload(payload, previous_file_hashes):
    result={k:v for k,v in payload.items() if k not in ('original_task','response_schema')}
    result['continuation_of_same_task']=True
    result['workspace_excerpts']=[dict(row) for row in payload.get('workspace_excerpts',[])]
    for row in result['workspace_excerpts']:
        if row.get('sha256') and previous_file_hashes.get(row['path'])==row['sha256']:
            row.pop('text',None)
            row['unchanged_from_your_previous_file_contents']=True
    return result
