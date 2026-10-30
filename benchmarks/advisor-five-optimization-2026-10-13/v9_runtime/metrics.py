"""Include known native subcall costs even if a bounded phase has missing usage."""
import json
from pathlib import Path
from metrics import aggregate_phases as base_aggregate

def aggregate_phases(logs):
    logs=Path(logs);totals=base_aggregate(logs)
    for request in logs.glob('*/request.json'):
        phase=request.with_name('phase-result.json')
        value=json.loads(phase.read_text()) if phase.exists() else {}
        if value.get('usage',{}).get('cost_usd') is not None:
            continue
        known=0.0
        for sub in request.parent.glob('rounds/*/phase-result.json'):
            cost=json.loads(sub.read_text())['usage'].get('cost_usd')
            if cost is not None:known+=cost
        known=max(known,value.get('usage',{}).get('known_cost_lower_bound_usd',0.0))
        totals['known_cost_lower_bound_usd']+=known
        if known:
            role=request.parent.name.split('-')[0]
            totals['role_cost_usd'][role]=totals['role_cost_usd'].get(role,0)+known
    return totals
