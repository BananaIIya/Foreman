"""Keep the new native delegation development probe in the cumulative ledger."""
import contextlib,io,json
from pathlib import Path
from expenses_v32 import main as previous
from hybrid_native import account_codex,read_events
ROOT=Path(__file__).resolve().parent
def main():
    with contextlib.redirect_stdout(io.StringIO()):previous()
    out=json.loads((ROOT/'expenses.json').read_text())
    for folder in sorted((ROOT/'raw/preflight').glob('v51-native-in-turn-tool*')):
        result=folder/'probe.json'
        usage=account_codex(read_events(folder/'app-server.events.jsonl'),'gpt-6.1-sol')
        fee=usage['cost_usd']
        out['attempts'].append({'name':folder.name,'category':'development_preflight','cost_usd':fee,
            'known_cost_lower_bound_usd':fee or 0.,'complete':result.exists(),'result_path':str(result),
            'scope':'Synthetic client-tool protocol; worker is deterministic fixture, not a model'})
        total=out['totals']['development_preflight'];total['attempts']+=1
        total['known_cost_usd']+=fee or 0.
        total['unknown_or_pending_attempts']+=int(fee is None or not result.exists())
    (ROOT/'expenses.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out['totals']))
if __name__=='__main__':main()
