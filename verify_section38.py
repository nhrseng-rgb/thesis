"""Verify end-to-end software artifacts, without claiming physical validation."""
import argparse
import json
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('directory',nargs='?',default='results_section38_full')
args=parser.parse_args(); out=Path(args.directory)
load=lambda name:json.loads((out/name).read_text())
summary=load('summary.json'); surface=load('phase3_surface.json')
audit=[json.loads(line) for line in (out/'audit.jsonl').read_text().splitlines()]
assert len(audit)==summary['evaluations']
assert [r['evaluation_id'] for r in audit]==list(range(1,len(audit)+1))
assert not summary['research_validated']
assert all(not r['research_feasible'] for r in audit)
assert all(r['status']!='failed' for r in audit)
assert len(load('phase1_baselines.json'))==12
assert len(surface)==summary['core_cells']
if not load('config.json').get('quick'):
    assert len(surface)==50
    assert [sum(x['cell']['grid']==g for x in surface) for g in 'ABC']==[10,20,20]
seeds=load('config.json')['seeds']; budget=load('config.json')['evaluations_per_run']
runs=load('phase3_runs.json')
assert len(runs)==len(surface)*seeds
assert all(r['evaluations']==budget for r in runs)
assert len(load('phase3_benchmarks.json'))==3
for result in load('phase5_validation_sensitivity.json'):
    assert len(result['cases'])==11
    assert len({c['name'] for c in result['cases']})==11
    assert not result['comparison']['passed']
    assert all(c['status']=='unavailable in aggregate demonstration' for c in result['cases']
               if c['name'] in ('current_limit','strength_boundary','strength_boundary_plus_0p1','opposite_retirement',
                                'hvdc_half','hvdc_one_and_half','hvdc_reverse'))
for result in load('phase4_spacing.json'):
    rows=[r['result']['evidence'] for r in result['matched_capacity']]
    for key in ('total_mw','total_mwh'):
        values=[r[key] for r in rows]
        assert max(values)-min(values)<1e-6
assert all(x['decomposition']['groups']['STAB']['status']=='unmeasured' for x in surface if x['best'])
result=dict(software_flow='passed',core_cells=len(surface),evaluations=len(audit),
            nominal_core_budget='passed',matched_capacity='passed',eleven_case_registry='passed',
            missing_metrics='correctly unmeasured',research_validation='not performed')
(out/'software_verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
