"""Physical model boundary for the Section 3.8 experiment controller."""
from pathlib import Path
import copy
import json
import subprocess
import time
import numpy as np
import pandapower as pp
import system as legacy

GROUPS={'FFR':('g1',),'RoCoF':('g2',),'PR':('g3','g4'),
        'Q':('g5','g6'),'STAB':('g7','g8','g9'),'REG':('g10',)}


def cell_id(cell):
    return f"{cell['grid']}_{cell['state']}_re{round(cell['re']*100)}_d{round(cell['d']*100)}"


def constraints(metrics, thresholds):
    """Signed dimensionless residuals. None means not measured, never a pass."""
    t=thresholds; m=metrics
    definitions={
        'g1':('nadir_hz',lambda v:(t['nadir_hz']-v)/.1),
        'g2':('rocof_hz_s',lambda v:(v-t['rocof_hz_s'])/.1),
        'g3':('settling_hz',lambda v:(t['settling_low_hz']-v)/.1),
        'g4':('settling_hz',lambda v:(v-t['settling_high_hz'])/.1),
        'g5':('q_shortfall_mvar',lambda v:v/t['q_scale_mvar']),
        'g6':('voltage_deviation_pu',lambda v:(v-t['voltage_deviation_pu'])/.01),
        'g7':('damping_ratio',lambda v:(t['damping_ratio']-v)/.01),
        'g8':('angle_coi_deviation_deg',lambda v:(v-t['angle_separation_deg'])/10),
        'g9':('gscr',lambda v:(t['gscr']-v)/.1),
        'g10':('reserve_shortfall_mw',lambda v:v/t['reserve_scale_mw'])}
    return {k:float(f(m[name])) if name in m and m[name] is not None and np.isfinite(m[name]) else None
            for k,(name,f) in definitions.items()}


def electrical_distances(net):
    """DC effective-reactance distance using the solved network's active branches."""
    branch=net._ppc['branch']; n=len(net._ppc['bus'])
    lap=np.zeros((n,n))
    for row in branch:
        if row[10].real <= 0 or abs(row[3]) < 1e-12: continue
        a,b=int(row[0].real),int(row[1].real); w=1/abs(row[3].real)
        lap[a,a]+=w; lap[b,b]+=w; lap[a,b]-=w; lap[b,a]-=w
    inv=np.linalg.pinv(lap,hermitian=True)
    lookup=net._pd2ppc_lookups['bus']; buses=list(map(int,net.bus.index))
    ix=np.array([int(lookup[b]) for b in buses])
    d=np.maximum(0,np.diag(inv)[:,None]+np.diag(inv)[None,:]-2*inv)
    return buses,d[np.ix_(ix,ix)]


class DemoBackend:
    research=False
    def __init__(self, config, output):
        self.config,self.output=config,Path(output)
        self.cache={}; self.dynamic_runs=0

    def baseline(self, grid, re):
        start=time.perf_counter()
        net,p=legacy.prepare(grid,re,0.,'islanded',[0],0.)
        t,f,b=legacy.dynamics(p)
        self.dynamic_runs+=1
        path=self.output/'baselines'/f'{grid}_re{round(re*100)}.json'
        path.parent.mkdir(parents=True,exist_ok=True); pp.to_json(net,str(path))
        return net,dict(path=str(path),converged=bool(net.converged),sg_retained=True,
                        dynamic_seconds=time.perf_counter()-start,frequency_spread_hz=None,
                        reference_solution_verified=False)

    def site_scores(self, net):
        # No invented SC or participation factors: this ranking is demo only.
        return {int(b):float(abs(v-1)) for b,v in net.res_bus.vm_pu.items()}

    def evaluate(self, cell, design, changes=None):
        changes=changes or {}
        g=cell['grid']; original=legacy.GRIDS[g]
        # Aggregate demo changes are explicit; unsupported physical changes stay unknown.
        net,p=legacy.prepare(g,cell['re'],cell['d'],cell['state'],design['sites'],design['rho'])
        if 'virtual_h_s' in changes: p['virtual_h_s']=changes['virtual_h_s']
        if 'hvdc_factor' in changes and cell['state']=='interconnected':
            p['loss_mw']=max(original[1],abs(original[2]*changes['hvdc_factor']))
        key=json.dumps(p,sort_keys=True)
        if key not in self.cache:
            self.cache[key]=legacy.dynamics(p); self.dynamic_runs+=1
        t,f,b=self.cache[key]
        m,_=legacy.metrics(t,f,b,p,net)
        demand=np.array(self.config['profiles']['demand_pu'])*p['load_mw']
        reserve=.04*demand
        if changes.get('re_ramp'):
            pv=np.array(self.config['profiles']['pv_pu'])*cell['re']*p['load_mw']
            reserve=np.maximum(reserve,np.abs(np.diff(demand-pv,append=(demand-pv)[-1])))
        m['reserve_shortfall_mw']=float(reserve.max()-p['capacity_mw'])
        m['spread_hz']=0.  # Aggregate model: explicitly demoted as tie-breaker.
        unsupported=[k for k in changes if k in ('current_limit_pu','gscr_boundary','retirement_order')]
        return dict(metrics=m,static_pass=bool(np.max(abs(net.res_bus.vm_pu-1))<=.05),
                    physical_limits_verified=False,unsupported_changes=unsupported,
                    displaced_sg_mw=p['load_mw']*cell['d'],
                    total_mw=p['capacity_mw'],total_mwh=p['capacity_mw'],
                    fault_status='unavailable',emt_status='unavailable',
                    dynamic_runs=self.dynamic_runs)

    def emt(self, cell, design):
        return dict(status='unavailable',reason='No detailed switching model in demonstration mode')


class ResearchBackend:
    """Load calibrated pandapower snapshots and call a supplied Simulink adapter.

    No synthetic dynamics are substituted when calibration or tools are absent.
    The manifest example documents the actual data boundary.
    """
    research=True
    def __init__(self, config, output):
        self.config,self.output=config,Path(output); self.dynamic_runs=0
        path=Path(config['calibration_manifest']).resolve()
        self.manifest=json.loads(path.read_text()); self.base=path.parent
        self.networks={}
        for name in ('baseline_networks','cell_networks','site_evidence','models'):
            if name not in self.manifest: raise ValueError(f'Manifest missing {name}')
        for g in 'ABC':
            for re in (20,40,60,80):
                key=f'{g}_re{re}'
                if key not in self.manifest['baseline_networks']: raise ValueError(f'Missing baseline {key}')
            evidence=self.manifest['site_evidence'].get(g,[])
            if len(evidence)<10: raise ValueError(f'Need at least ten screened-bus evidence rows for {g}')
            for row in evidence:
                for field in ('short_circuit_mva','participation_factor'):
                    if row.get(field) is None or not np.isfinite(row[field]): raise ValueError(f'Uncalibrated {g} site evidence: {field}')
            boundary=self.manifest.get('strength_boundary',{}).get(g)
            if boundary is None or not np.isfinite(boundary): raise ValueError(f'Missing measured strength boundary for {g}')
            for state in (['islanded'] if g=='A' else ['islanded','interconnected']):
                for re in (20,40,60,80):
                    for d in (20,40,60,80):
                        if d>re: continue
                        key=f'{g}_{state}_re{re}_d{d}'
                        record=self.manifest['cell_networks'].get(key)
                        if not record: raise ValueError(f'Missing calibrated cell {key}')
                        if record.get('retired_sg_mw') is None or record['retired_sg_mw']<=0:
                            raise ValueError(f'Missing actual retired SG MW for {key}')
                        if not (self.base/record['path']).is_file(): raise ValueError(f'Calibrated network not found: {record["path"]}')

    def load(self,path):
        key=str((self.base/path).resolve())
        if key not in self.networks:
            net=pp.from_json(key); pp.runpp(net,numba=False)
            self.networks[key]=net
        return copy.deepcopy(self.networks[key])

    def baseline(self, grid, re):
        record=self.manifest['baseline_networks'][f'{grid}_re{round(re*100)}']
        net=self.load(record['path'])
        for required in ('reference_solution_verified','g1_removed','sg_retained','dynamic_seconds','frequency_spread_hz'):
            if required not in record: raise ValueError(f'Baseline metadata missing {required}')
        if not record['reference_solution_verified'] or not record['g1_removed'] or not record['sg_retained']:
            raise ValueError('Calibrated baseline evidence does not pass Phase 1')
        if record['dynamic_seconds'] is None or record['dynamic_seconds']<=0 or record['frequency_spread_hz'] is None:
            raise ValueError('Measured baseline timing and nodal-frequency spread are required')
        return net,dict(record,converged=bool(net.converged))

    def site_scores(self, net):
        # Grid-specific evidence is selected by the controller before this call.
        evidence=self.manifest['site_evidence'][self.current_grid]
        sc=np.array([x['short_circuit_mva'] for x in evidence],float)
        pf=np.array([x['participation_factor'] for x in evidence],float)
        # Higher score = weaker bus and higher participation. Document this ranking.
        return {int(x['bus']):float((sc.max()-s)/max(np.ptp(sc),1e-9)+(p-pf.min())/max(np.ptp(pf),1e-9))
                for x,s,p in zip(evidence,sc,pf)}

    def call(self, request):
        from uuid import uuid4
        name=uuid4().hex; folder=self.output/'exchange'; folder.mkdir(exist_ok=True)
        inp,out=folder/f'{name}_input.json',folder/f'{name}_output.json'
        legacy.write_json(inp,request)
        def quote(p): return str(Path(p).resolve()).replace('\\','/').replace("'","''")
        command=f"addpath('{quote(legacy.ROOT/'matlab')}'); evaluate_research('{quote(inp)}','{quote(out)}');"
        result=subprocess.run(['matlab','-batch',command],capture_output=True,text=True,timeout=self.config['matlab_timeout_s'])
        if result.returncode: raise RuntimeError(result.stderr or result.stdout)
        if not out.exists(): raise RuntimeError('MATLAB adapter did not produce its result file')
        return json.loads(out.read_text())

    def evaluate(self,cell,design,changes=None):
        snapshot=self.manifest['cell_networks'][cell_id(cell)]
        net=self.load(snapshot['path'])
        retired=float(snapshot['retired_sg_mw'])
        if retired<=0: raise ValueError('Actual retired SG MW must be positive')
        capacity=retired*design['rho']
        for bus,share in zip(design['sites'],design['shares']):
            pp.create_sgen(net,int(bus),p_mw=0,q_mvar=0,sn_mva=capacity*share,name='GFM BESS')
        pp.runpp(net,numba=False)
        voltage=float(max(abs(net.res_bus.vm_pu-1)))
        common=dict(static_pass=voltage<=self.config['thresholds']['voltage_deviation_pu'],
                    displaced_sg_mw=retired,total_mw=capacity,total_mwh=capacity*self.config['duration_h'])
        if not common['static_pass']:
            return dict(common,metrics={'voltage_deviation_pu':voltage},physical_limits_verified=False)
        folder=self.output/'exchange'; folder.mkdir(exist_ok=True)
        network_file=folder/'operating_network.json'; pp.to_json(net,str(network_file))
        response=self.call(dict(action='evaluate',cell=cell,design=design,changes=changes or {},
                                network_json=str(network_file.resolve()),snapshot=snapshot,
                                models=self.manifest['models'],thresholds=self.config['thresholds'],
                                nominal_parameters=self.config['physical_parameters']))
        self.dynamic_runs+=int(response.get('dynamic_runs',1))
        response['metrics']['voltage_deviation_pu']=max(voltage,response['metrics'].get('voltage_deviation_pu',voltage))
        common['static_pass']=common['static_pass'] and response.get('static_pass',True)
        return dict(response,**common)

    def emt(self,cell,design):
        snapshot=dict(self.manifest['cell_networks'][cell_id(cell)])
        snapshot['path']=str((self.base/snapshot['path']).resolve())
        return self.call(dict(action='emt',cell=cell,design=design,snapshot=snapshot,models=self.manifest['models'],
                              nominal_parameters=self.config['physical_parameters']))
