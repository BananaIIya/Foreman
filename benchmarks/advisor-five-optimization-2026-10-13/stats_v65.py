"""V65 cheap-first / verify-first table. Status from the verifier result; costs and routing from v65-usage.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS = ['wal-recovery-ordering', 'cad-model', 'hof-topology-interpenetration', 'satb-audio-transcription', 'atrx-vep-crispr']
# Mean cost of the correct-or-reference solo runs measured earlier (Sol for CAD/HOF/SATB, Grok for WAL/ATRX).
SOLO_MEAN = {'cad-model': 0.184954, 'hof-topology-interpenetration': 0.32619346666666665,
             'satb-audio-transcription': 0.49993213333333336, 'wal-recovery-ordering': 1.3269653333333333,
             'atrx-vep-crispr': 1.545018}


def row(task):
    name = f'cheapfirst-v65-{task}-r1'
    paths = list((ROOT/'raw/jobs'/name).glob('*/result.json'))
    if not paths:
        return {'task': task, 'status': 'pending'}
    res = json.loads(paths[0].read_text())
    ctrf = paths[0].parent/'verifier'/'ctrf.json'
    tests = json.loads(ctrf.read_text())['results']['summary'] if ctrf.exists() else {}
    reward = ((res.get('verifier_result') or {}).get('rewards') or {}).get('reward')
    status = 'invalid' if res.get('exception_info') or reward is None else ('correct' if reward == 1 else 'wrong')
    out = {'task': task, 'status': status, 'passed': tests.get('passed'), 'tests': tests.get('tests')}
    u_path = paths[0].parent/'agent'/'v65-usage.json'
    if u_path.exists():
        u = json.loads(u_path.read_text())
        haiku, sol = u.get('haiku', {}), u.get('sol', {})
        out.update(haiku_cost=haiku.get('cost_usd'), haiku_stop=[r['subtype'] for r in haiku.get('results', [])],
                   haiku_models=haiku.get('models_in_session'), native_server_tools=haiku.get('native_server_tool_calls'),
                   sol_cost=sol.get('cost_usd'), sol_interrupted=sol.get('interrupted'), sol_commands=sol.get('commands'),
                   sol_changed_files=sol.get('changed_file_count'), accepted_without_edits=u.get('accepted_without_edits'),
                   total=u.get('total_cost_usd'), haiku_error=u.get('haiku_error'))
        if out['total'] is not None:
            out['ratio_vs_solo_mean'] = out['total'] / SOLO_MEAN[task]
    return out


def main():
    rows = [row(t) for t in TASKS]
    (ROOT/'stats-v65.json').write_text(json.dumps({'rows': rows, 'solo_mean_reference': SOLO_MEAN,
                                                   'note': 'n=1 per task'}, indent=2) + '\n')
    for r in rows:
        print(f"{r['task']:30s} {r['status']:8s} {r.get('passed')}/{r.get('tests')} haiku ${r.get('haiku_cost')} {r.get('haiku_stop')} "
              f"| sol ${r.get('sol_cost')} cmds {r.get('sol_commands')} changed {r.get('sol_changed_files')} "
              f"| total ${r.get('total')} ratio {r.get('ratio_vs_solo_mean')}")
    print('known total', sum(r.get('total') or 0 for r in rows))


if __name__ == '__main__':
    main()
