"""Normalize mixed native usage for Harbor without silently dropping a role."""
import json
from pathlib import Path


def aggregate_phases(logs):
    logs = Path(logs)
    requests = sorted(logs.glob('*/request.json'))
    models = {}
    roles = {}
    complete = bool(requests)
    for request_path in requests:
        request = json.loads(request_path.read_text())
        result_path = request_path.with_name('phase-result.json')
        if not result_path.exists():
            complete = False
            continue
        phase = json.loads(result_path.read_text())
        model = request['model']
        role = request_path.parent.name.split('-')[0]
        usage = phase['usage']
        cost = usage.get('cost_usd')
        if cost is None:
            complete = False
            continue
        if 'per_response_usage' in usage:
            responses = usage['per_response_usage']
            inp = sum(u['inputTokens'] for u in responses)
            cached = sum(u['cachedInputTokens'] for u in responses)
            out = sum(u['outputTokens'] for u in responses)
        else:
            u = usage['native_usage']
            inp = u['inputTokens'] + u['cacheReadTokens'] + u['cacheWriteTokens']
            cached = u['cacheReadTokens']
            out = u['outputTokens']
        row = models.setdefault(model, dict(n_input_tokens=0, n_cache_tokens=0,
                                           n_output_tokens=0, cost_usd=0.0))
        for k, v in [('n_input_tokens', inp), ('n_cache_tokens', cached),
                     ('n_output_tokens', out), ('cost_usd', cost)]:
            row[k] += v
        roles[role] = roles.get(role, 0.0) + cost
    return {'complete': complete, 'models': models, 'role_cost_usd': roles,
            'cost_usd': sum(r['cost_usd'] for r in models.values()) if complete else None,
            'known_cost_lower_bound_usd': sum(r['cost_usd'] for r in models.values())}
