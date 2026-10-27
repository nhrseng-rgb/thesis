"""Create the complete-budget configuration without starting a research run."""
import json
from pathlib import Path
root=Path(__file__).resolve().parent
config=json.loads((root/'configs/walkthrough.json').read_text())
config.update(mode='research',output='results_research',seeds=3,evaluations_per_run=2000,
              population=64,enumeration_site_limit=None,calibration_manifest=None)
config['enumeration_ratios']=[round(i*.05,2) for i in range(41)]
config['assumptions'].append('Finite enumeration resolution must be documented; it is not a continuous global optimum.')
(root/'configs/research.json').write_text(json.dumps(config,indent=2))
levels=(20,40,60,80)
manifest={
    'description':'SCHEMA EXAMPLE ONLY. Replace placeholders with calibrated networks and evidence.',
    'baseline_networks':{f'{g}_re{re}':dict(path=f'../data/baselines/{g}_re{re}.json',
        reference_solution_verified=False,g1_removed=False,sg_retained=False,
        dynamic_seconds=None,frequency_spread_hz=None)
        for g in 'ABC' for re in levels},
    'cell_networks':{f'{g}_{s}_re{re}_d{d}':dict(path=f'../data/cells/{g}_{s}_re{re}_d{d}.json',
        retired_sg_mw=None,contingency_mw=None)
        for g in 'ABC' for s in (['islanded'] if g=='A' else ['islanded','interconnected'])
        for re in levels for d in levels if d<=re},
    'site_evidence':{g:[dict(bus=i,short_circuit_mva=None,participation_factor=None) for i in range(39)] for g in 'ABC'},
    'strength_boundary':{g:None for g in 'ABC'},
    'models':dict(model_folder='C:/path/to/calibrated/models',rms_function='my_rms_model',
                  fault_function='my_fault_model',emt_function='my_emt_model')}
(root/'configs/calibration_manifest.example.json').write_text(json.dumps(manifest,indent=2))
print('Created configs/research.json. Supply calibration_manifest and confirm research inputs before running.')
