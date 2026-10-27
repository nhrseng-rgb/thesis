"""Check saved artifacts and independently compare MATLAB/Python ODE traces."""
import json
from pathlib import Path
import numpy as np

root = Path(__file__).resolve().parent
out = root/'results'
summary = json.loads((out/'summary.json').read_text())
records = json.loads((out/'evaluations.json').read_text())
assert summary['evaluations'] == len(records)
assert not summary['research_ready']
assert all(not r['research_feasible'] for r in records)
assert any(r['demo_pass'] for r in records)
assert all('metrics' not in r for r in records if r['status']=='static_screen_failed')
spacing = json.loads((out/'spacing.json').read_text())
for grid in ('A','B','C'):
    matched = [x['matched_capacity_mw'] for x in spacing if x['grid']==grid]
    assert matched and max(matched)-min(matched) < 1e-9
    assert all(x['matched_capacity_mw']==x['matched_energy_mwh'] for x in spacing if x['grid']==grid)
python = np.loadtxt(out/'python_reference.csv',delimiter=',',skiprows=1)
matlab = np.loadtxt(out/'matlab_reference.csv',delimiter=',',skiprows=1)
assert np.allclose(python[:,0],matlab[:,0],rtol=0,atol=1e-9)
error_hz = float(np.max(abs(python[:,1]-matlab[:,1])))
error_mw = float(np.max(abs(python[:,2]-matlab[:,2])))
assert error_hz < 1e-5 and error_mw < 0.01
result = dict(python_workflow='passed',matched_capacity='passed',research_claims='correctly disabled',
              matlab_equation_comparison='passed',max_frequency_difference_hz=error_hz,
              max_power_difference_mw=error_mw,simulink_execution='blocked: incomplete installation')
matrix = root/'results_matrix'
if (matrix/'summary.json').exists():
    summary50 = json.loads((matrix/'summary.json').read_text())
    cells = json.loads((matrix/'surface.json').read_text())
    events = json.loads((matrix/'workflow.json').read_text())
    assert summary50['cells']==len(cells)==50
    assert len({tuple(e['cell']) for e in cells})==50
    assert sum(e['cell'][0]=='A' for e in cells)==10
    assert sum(e['cell'][0]=='B' for e in cells)==20
    assert sum(e['cell'][0]=='C' for e in cells)==20
    assert all(e['cell'][2]<=e['cell'][1] for e in cells)
    assert any(e['phase']=='Phase 4' and e['status']=='skipped' for e in events)
    result['fifty_cell_matrix_and_spacing_skip']='passed'
(out/'verification.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
