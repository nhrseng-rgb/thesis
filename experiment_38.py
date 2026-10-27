"""Section 3.8 controller: five phases, shared evaluation, analysis and audit.

Run: .venv/Scripts/python experiment_38.py --config configs/walkthrough.json
The walkthrough exercises the complete software flow with partial demo physics.
"""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import time
import os
os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).resolve().parent/'.cache'/'matplotlib'))
import numpy as np
from optimizers import optimize
from study_backend import DemoBackend, ResearchBackend, constraints, GROUPS, cell_id, electrical_distances
from system import write_json, ROOT


def seed_for(cell,run):
    # Stable across processes, machines and algorithms; Python hash() is not stable.
    return int.from_bytes(hashlib.sha256(f'{cell_id(cell)}:{run}'.encode()).digest()[:4],'big')


def core_cells():
    return [dict(grid=g,state=s,re=r,d=d) for g in 'ABC'
            for s in (['islanded'] if g=='A' else ['islanded','interconnected'])
            for r in (.2,.4,.6,.8) for d in (.2,.4,.6,.8) if d<=r]


def compositions(total,n):
    if n==1:
        if total>=1: yield (total,)
        return
    for value in range(1,total-n+2):
        for tail in compositions(total-value,n-1): yield (value,)+tail


def best(rows):
    passing=[r for r in rows if r['eligible']]
    return min(passing,key=lambda r:tuple(r['objectives'])) if passing else None


class Experiment:
    def __init__(self,config):
        self.config=config; self.out=ROOT/config['output']; self.out.mkdir(parents=True,exist_ok=True)
        if (self.out/'audit.jsonl').exists():
            raise ValueError('Output already contains an audit; choose a new output directory')
        self.backend=(ResearchBackend if config['mode']=='research' else DemoBackend)(config,self.out)
        self.events=[]; self.evaluations=0; self.references={}; self.candidates={}; self.classes={}
        self.surface=[]; self.runs=[]; self.phase='START'; self.algorithm='none'; self.run_seed=None
        self.started=time.perf_counter(); self.tie_breaker={}; self.failures=[]
        write_json(self.out/'config.json',config)

    def event(self,phase,status,detail=''):
        self.phase=phase
        self.events.append(dict(phase=phase,status=status,detail=detail))
        write_json(self.out/'workflow.json',self.events)
        print(f'{phase}: {status} {detail}',flush=True)

    def site_sets(self,grid,n,dispersion):
        key=(grid,n)
        if key not in self.classes:
            buses=self.candidates[grid]; distance=self.distances[grid]
            sets=list(itertools.combinations(buses,n))
            if n==1:
                self.classes[key]={'none':sets}
            else:
                def score(s): return np.mean([distance[a,b] for a,b in itertools.combinations(s,2)])
                sets.sort(key=lambda s:(score(s),s))
                # Rank terciles give deterministic, nonempty dispersion classes.
                chunks=np.array_split(np.arange(len(sets)),3)
                self.classes[key]={name:[sets[int(i)] for i in indices]
                                   for name,indices in zip(('Low','Medium','High'),chunks)}
        return self.classes[key].get('none' if n==1 else dispersion,[])

    def decode(self,x,grid,n,dispersion):
        buses=self.candidates[grid]; used=[]
        for value in x[:n]:
            index=int(np.floor(value*(len(buses)-1)+.5))
            for offset in range(len(buses)):
                bus=buses[(index+offset)%len(buses)]
                if bus not in used: used.append(bus); break
        allowed=self.site_sets(grid,n,dispersion)
        if not allowed: raise ValueError('Empty site/dispersion class')
        if tuple(sorted(used)) not in {tuple(sorted(s)) for s in allowed}:
            # Repair into the required class; order is then fixed for share association.
            used=list(min(allowed,key=lambda s:(len(set(s)^set(used)),tuple(s))))
        weights=np.maximum(np.array(x[n:2*n]),1e-6)
        return dict(sites=list(map(int,used)),shares=list(map(float,weights/weights.sum())),
                    rho=float(x[-1]*self.config['rho_max']))

    def evaluate(self,cell,design,changes=None):
        self.evaluations+=1; before=self.backend.dynamic_runs
        try:
            evidence=self.backend.evaluate(cell,design,changes)
            thresholds=dict(self.config['thresholds'])
            if changes and 'gscr_boundary' in changes and self.backend.research:
                boundary=float(self.backend.manifest['strength_boundary'][cell['grid']])
                thresholds['gscr']=boundary+(.1 if changes['gscr_boundary']=='boundary_plus_0.1' else 0.)
            residuals=constraints(evidence.get('metrics',{}),thresholds)
            missing=[g for g,v in residuals.items() if v is None]
            observed=[v for v in residuals.values() if v is not None]
            cv=sum(max(0.,v) for v in observed)
            complete=not missing and evidence.get('physical_limits_verified',False)
            eligible=bool(evidence.get('static_pass',False) and observed and cv<=1e-10)
            if self.backend.research: eligible=eligible and complete
            if not evidence.get('static_pass',False): cv+=1
            if self.backend.research and not complete: cv+=1e6
            spread=evidence.get('metrics',{}).get('spread_hz')
            if self.tie_breaker.get(cell['grid'],False) and spread is None:
                eligible=False; cv+=1e6
            row=dict(cell=cell,design=design,evidence=evidence,residuals=residuals,missing_constraints=missing,
                     cv=float(cv),eligible=eligible,research_feasible=bool(eligible and complete and self.backend.research),
                     objectives=[design['rho'],float(spread or 0) if self.tie_breaker.get(cell['grid'],False) else 0.],
                     status='evaluated',physical_complete=complete)
        except Exception as exc:
            row=dict(cell=cell,design=design,cv=1e12,eligible=False,research_feasible=False,
                     objectives=[design['rho'],1e6],status='failed',error=str(exc),residuals={g:None for g in sum(GROUPS.values(),())})
        row.update(evaluation_id=self.evaluations,phase=self.phase,algorithm=self.algorithm,seed=self.run_seed,
                   dynamic_runs=self.backend.dynamic_runs-before,changes=changes or {})
        with (self.out/'audit.jsonl').open('a',encoding='utf-8') as file:
            file.write(json.dumps(row,default=lambda x:x.item() if isinstance(x,np.generic) else str(x))+'\n')
        return row

    def search(self,cell,n,dispersion,algorithm='RNSGA-II',run=0,changes=None):
        self.algorithm=algorithm; self.run_seed=seed_for(cell,run)
        return optimize(algorithm,lambda x:self.evaluate(cell,self.decode(x,cell['grid'],n,dispersion),changes),
                        2*n+1,self.config['evaluations_per_run'],self.run_seed,self.config['population'])

    def enumerate(self,cell,n,dispersion):
        self.algorithm='finite-grid enumeration'; self.run_seed=None
        sites=self.site_sets(cell['grid'],n,dispersion)
        limit=self.config.get('enumeration_site_limit')
        if limit: sites=sites[:limit]
        denominator=self.config['share_denominator']
        shares=[tuple(v/denominator for v in c) for c in compositions(denominator,n)]
        if self.config['mode']=='walkthrough': shares=[tuple([1/n]*n)]
        selected=None; count=0
        for site_set in sites:
            for share in shares:
                for ratio in self.config['enumeration_ratios']:
                    row=self.evaluate(cell,dict(sites=list(site_set),shares=list(share),rho=ratio)); count+=1
                    if row['eligible'] and (selected is None or tuple(row['objectives'])<tuple(selected['objectives'])):
                        selected=row
        return dict(best=selected,evaluations=count,complete_site_enumeration=not limit or limit>=len(self.site_sets(cell['grid'],n,dispersion)),
                    share_grid='equal only' if self.config['mode']=='walkthrough' else f'positive multiples of 1/{denominator}',
                    ratio_grid=self.config['enumeration_ratios'],global_continuous_optimum=False)

    def phase1(self):
        self.distances={}; baselines=[]
        for attempt in range(1,self.config['baseline_attempts']+1):
            try:
                for grid in 'ABC':
                    for re in (.2,.4,.6,.8):
                        net,record=self.backend.baseline(grid,re)
                        if not record['converged'] or not np.isfinite(net.res_bus.vm_pu).all(): raise RuntimeError('Baseline did not converge to finite bus voltages')
                        baselines.append(dict(grid=grid,re=re,**record))
                    if self.backend.research: self.backend.current_grid=grid
                    scores=self.backend.site_scores(net)
                    self.candidates[grid]=sorted(scores,key=lambda b:(-scores[b],b))[:10]
                    if len(self.candidates[grid])!=10: raise ValueError('Need ten eligible candidate buses per grid')
                    buses,d=electrical_distances(net)
                    self.distances[grid]={(a,b):float(d[i,j]) for i,a in enumerate(buses) for j,b in enumerate(buses)}
                    spreads=[r['frequency_spread_hz'] for r in baselines if r['grid']==grid and r['frequency_spread_hz'] is not None]
                    self.tie_breaker[grid]=bool(spreads and max(spreads)>self.config['frequency_noise_hz'])
                write_json(self.out/'phase1_baselines.json',baselines)
                write_json(self.out/'phase1_sites.json',dict(candidates=self.candidates,tie_breaker=self.tie_breaker,
                           distance_definition='DC effective reactance; rank-tercile dispersion classes'))
                self.event('Phase 1','completed',f'12 baselines saved; convergence gate YES (attempt {attempt})')
                return
            except Exception as exc:
                baselines=[]
                self.event('Phase 1','retry',str(exc))
        raise RuntimeError('Baseline gate NO: retry limit reached; downstream phases not executed')

    def phase2(self):
        self.event('Phase 2','running','Five count/dispersion configurations, finite-grid enumeration')
        results=[]
        for grid in 'ABC':
            cell=dict(grid=grid,re=.4,d=.4,state='islanded')
            options=[]
            for n,d in ((1,'none'),(3,'Medium'),(5,'Medium'),(3,'Low'),(3,'High')):
                options.append(dict(n=n,dispersion=d,**self.enumerate(cell,n,d)))
            passing=[o for o in options if o['best']]
            if not passing: raise RuntimeError(f'No passing reference configuration for grid {grid}')
            ratios=[o['best']['design']['rho'] for o in passing]
            variation=(max(ratios)-min(ratios))/max(max(ratios),1e-12)
            default=next((o for o in passing if o['n']==3 and o['dispersion']=='Medium'),passing[0])
            decisions={}
            # A missing configuration cannot establish insensitivity.
            for threshold in (.01,.02,.05):
                chosen=default if len(passing)==5 and variation<threshold else max(passing,key=lambda o:o['best']['design']['rho'])
                decisions[str(threshold)]=dict(n=chosen['n'],dispersion=chosen['dispersion'])
            self.references[grid]=decisions['0.02']
            results.append(dict(grid=grid,options=options,relative_variation=variation,decisions=decisions,
                                all_configurations_pass=len(passing)==5))
        write_json(self.out/'phase2_reference_screening.json',results)
        self.event('Phase 2','completed','Reference N and dispersion selected; 1%, 2%, 5% decisions retained')

    def decompose(self,cell,selected):
        design=selected['design']; upper=design['rho']; results={}
        self.algorithm='fixed-configuration decomposition'; self.run_seed=None
        cache={}
        def evaluate(ratio):
            key=round(float(ratio),10)
            if key not in cache: cache[key]=self.evaluate(cell,dict(design,rho=key))
            return cache[key]
        for group,keys in GROUPS.items():
            def passed(r):
                residuals=evaluate(r)['residuals']; values=[residuals.get(k) for k in keys]
                return None if any(v is None for v in values) else all(v<=0 for v in values)
            ratios=np.linspace(0,upper,self.config['monotonicity_samples'])
            flags=[passed(float(r)) for r in ratios]
            if any(v is None for v in flags):
                results[group]=dict(ratio=None,status='unmeasured',method=None); continue
            monotone=all(not a or b for a,b in zip(flags,flags[1:]))
            if group!='STAB' and monotone and flags[-1]:
                lo,hi=0.,upper
                if flags[0]: hi=0.
                while hi-lo>self.config['bisection_tolerance']:
                    mid=(hi+lo)/2
                    if passed(mid): hi=mid
                    else: lo=mid
                value=hi; method='sampled-monotonicity bisection'
            else:
                grid=np.unique(np.append(np.arange(0,upper,self.config['stability_ratio_step']),upper))
                passing=[float(r) for r in grid if passed(float(r))]
                value=min(passing) if passing else None; method='grid (no monotonicity assumption)'
            results[group]=dict(ratio=value,status='measured',method=method,sampled_monotone=monotone)
        known={g:r['ratio'] for g,r in results.items() if r['ratio'] is not None}
        lower=max(known.values()) if known else None
        complete=len(known)==6
        if lower is not None and upper+self.config['bisection_tolerance']<lower:
            raise RuntimeError('Decomposition exceeds selected joint ratio')
        return dict(groups=results,complete=complete,ratio_lower_bound=lower,
                    binding_group=max(known,key=known.get) if known else None,
                    binding_is_partial=not complete,interaction_gap_percent=100*(upper-lower)/upper if upper and lower is not None else None,
                    joint_rechecked=selected['eligible'])

    def phase3(self):
        self.event('Phase 3','running','Seeded RNSGA-II; fixed-site decomposition; three benchmark cells')
        cells=core_cells()
        if self.config.get('quick'): cells=[c for c in cells if c['re']==c['d']==.6 and c['state']=='islanded']
        for index,cell in enumerate(cells):
            reference=self.references[cell['grid']]; run_solutions=[]
            for run in range(self.config['seeds']):
                rows=self.search(cell,**reference,run=run)
                solution=best(rows)
                self.runs.append(dict(cell=cell,algorithm='RNSGA-II',run=run,seed=seed_for(cell,run),
                                      evaluations=len(rows),best=solution))
                if solution: run_solutions.append(solution)
            selected=best(run_solutions)
            ratios=[r['design']['rho'] for r in run_solutions]
            self.surface.append(dict(cell=cell,best=selected,
                decomposition=self.decompose(cell,selected) if selected else None,
                run_ratios=ratios,range=max(ratios)-min(ratios) if ratios else None,
                site_agreement_count=sum(set(r['design']['sites'])==set(selected['design']['sites']) for r in run_solutions) if selected else 0))
            write_json(self.out/'phase3_surface.json',self.surface)
            if (index+1)%10==0: print(f'  Core cells: {index+1}/{len(cells)}',flush=True)
        benchmarks=[]
        for grid in 'ABC':
            cell=dict(grid=grid,state='islanded',re=.6,d=.6); reference=self.references[grid]
            enumerated=self.enumerate(cell,**reference)
            comparison=[]
            for prior in self.runs:
                if cell_id(prior['cell'])!=cell_id(cell): continue
                solution=prior['best']; target=enumerated['best']
                comparison.append(dict(algorithm='RNSGA-II',run=prior['run'],seed=prior['seed'],best=solution,
                    ratio_difference_from_grid=solution['design']['rho']-target['design']['rho'] if solution and target else None,
                    same_site_set=bool(solution and target and set(solution['design']['sites'])==set(target['design']['sites']))))
            for algorithm in ('MOPSO','AMOSA'):
                for run in range(self.config['seeds']):
                    rows=self.search(cell,**reference,algorithm=algorithm,run=run)
                    solution=best(rows); target=enumerated['best']
                    comparison.append(dict(algorithm=algorithm,run=run,seed=seed_for(cell,run),best=solution,
                        ratio_difference_from_grid=solution['design']['rho']-target['design']['rho'] if solution and target else None,
                        same_site_set=bool(solution and target and set(solution['design']['sites'])==set(target['design']['sites']))))
            benchmarks.append(dict(cell=cell,finite_grid=enumerated,comparisons=comparison))
        write_json(self.out/'phase3_runs.json',self.runs)
        write_json(self.out/'phase3_benchmarks.json',benchmarks)
        self.event('Phase 3','completed',f'{len(cells)} core cells; results limited to the tested search spaces')

    def critical(self,grid):
        choices=[x for x in self.surface if x['cell']['grid']==grid and x['best']]
        # Highest ratio, with weakest measured strength as a tie-break.
        return max(choices,key=lambda x:(x['best']['design']['rho'],-x['best']['evidence']['metrics'].get('gscr',1e6))) if choices else None

    def phase4(self):
        if not self.config['spacing']:
            self.event('Phase 4','skipped','Computing-time decision NO; spatial refinement is a reported limitation'); return
        self.event('Phase 4','running','Free sizing then strictly matched MW/MWh at Low/Medium/High dispersion')
        results=[]
        for grid in 'ABC':
            critical=self.critical(grid)
            if not critical:
                results.append(dict(grid=grid,status='no passing core design')); continue
            cell=critical['cell']; fixed=critical['best']['design']['rho']; matched=[]; free=[]
            for n in self.config['spacing_site_counts']:
                classes=['none'] if n==1 else ['Low','Medium','High']
                row=best(self.search(cell,n,'none' if n==1 else 'Medium'))
                free.append(dict(n=n,best=row))
                self.algorithm='matched-capacity enumeration'; self.run_seed=None
                for dispersion in classes:
                    options=self.site_sets(grid,n,dispersion)
                    limit=self.config.get('enumeration_site_limit')
                    if limit: options=options[:limit]
                    for sites in options:
                        row=self.evaluate(cell,dict(sites=list(sites),shares=[1/n]*n,rho=fixed))
                        matched.append(dict(n=n,dispersion=dispersion,result=row))
            capacities=[r['result'].get('evidence',{}).get('total_mw') for r in matched]
            energies=[r['result'].get('evidence',{}).get('total_mwh') for r in matched]
            known=[x for x in capacities if x is not None]; energy=[x for x in energies if x is not None]
            if known and max(known)-min(known)>1e-6: raise RuntimeError('Matched MW invariant failed')
            if energy and max(energy)-min(energy)>1e-6: raise RuntimeError('Matched MWh invariant failed')
            saturation=None
            for a,b in zip(free,free[1:]):
                if a['best'] and b['best']:
                    previous=a['best']['design']['rho']; now=b['best']['design']['rho']
                    improvement=(previous-now)/max(previous,1e-12)
                    if 0<=improvement<.02 and saturation is None: saturation=b['n']
            spatial_saturation={}
            for dispersion in ('Low','Medium','High'):
                spatial_saturation[dispersion]={}
                for metric,higher in (('gscr',True),('spread_hz',False),('local_rocof_spread_hz_s',False)):
                    samples=[]
                    for n in self.config['spacing_site_counts']:
                        rows=[r['result'] for r in matched if r['n']==n and (n==1 or r['dispersion']==dispersion)]
                        vals=[r['evidence']['metrics'].get(metric) for r in rows if r.get('evidence')]
                        vals=[v for v in vals if v is not None and np.isfinite(v)]
                        if vals: samples.append((n,max(vals) if higher else min(vals)))
                    count=None
                    for (na,a),(nb,b) in zip(samples,samples[1:]):
                        if abs(a)<=self.config['frequency_noise_hz']: continue
                        improvement=(b-a)/abs(a) if higher else (a-b)/abs(a)
                        if 0<=improvement<.02: count=nb; break
                    spatial_saturation[dispersion][metric]=dict(site_count=count,measured_samples=samples)
            results.append(dict(grid=grid,cell=cell,free_capacity=free,matched_capacity=matched,
                                saturation_site_count=saturation,spatial_saturation=spatial_saturation,
                                saturation_scope='tested adjacent counts only; best measured metric per class/count'))
        write_json(self.out/'phase4_spacing.json',results)
        self.event('Phase 4','completed','Spatial results kept separate from core surface')

    def phase5(self):
        self.event('Phase 5','running','Critical cells: detailed-model validation and eleven one-seed sensitivities')
        s=self.config['sensitivity']
        cases=[('virtual_inertia_low',{'virtual_h_s':s['virtual_h_s'][0]}),
               ('virtual_inertia_mid',{'virtual_h_s':s['virtual_h_s'][1]}),
               ('virtual_inertia_high',{'virtual_h_s':s['virtual_h_s'][2]}),
               ('current_limit',{'current_limit_pu':s['current_limit_pu']}),
               ('strength_boundary',{'gscr_boundary':'boundary'}),
               ('strength_boundary_plus_0p1',{'gscr_boundary':'boundary_plus_0.1'}),
               ('hvdc_half',{'hvdc_factor':.5}),('hvdc_one_and_half',{'hvdc_factor':1.5}),
               ('hvdc_reverse',{'hvdc_factor':-.5}),('re_ramp',{'re_ramp':True}),
               ('opposite_retirement',{'retirement_order':'opposite'})]
        results=[]
        for grid in 'ABC':
            critical=self.critical(grid)
            if not critical:
                results.append(dict(grid=grid,status='no passing core design',cases=[])); continue
            cell=critical['cell']; design=critical['best']['design']
            emt=self.backend.emt(cell,design)
            comparison=dict(status='unavailable',passed=False)
            if emt.get('status')=='completed':
                rms=critical['best']['evidence']['metrics']; mm=emt['metrics']
                differences={k:abs(mm[k]-rms[k])/max(abs(rms[k]),1e-9) for k in ('nadir_hz','rocof_hz_s')}
                same_sites=set(emt['selected_sites'])==set(design['sites'])
                comparison=dict(status='completed',relative_differences=differences,
                                same_sites=same_sites,passed=same_sites and max(differences.values())<=.05)
            sensitivities=[]
            for name,changes in cases:
                unsupported=(not self.backend.research and any(k in changes for k in ('current_limit_pu','gscr_boundary','retirement_order','hvdc_factor')))
                if unsupported:
                    sensitivities.append(dict(name=name,changes=changes,status='unavailable in aggregate demonstration',best=None)); continue
                rows=self.search(cell,**self.references[grid],run=0,changes=changes)
                sensitivities.append(dict(name=name,changes=changes,status='executed',seed=seed_for(cell,0),best=best(rows)))
            results.append(dict(grid=grid,cell=cell,emt=emt,comparison=comparison,cases=sensitivities))
        write_json(self.out/'phase5_validation_sensitivity.json',results)
        self.validation_passed=bool(self.backend.research and len(results)==3 and all(
            x.get('comparison',{}).get('passed',False) and len(x['cases'])==11 and all(c.get('best') for c in x['cases']) for x in results))
        self.event('Phase 5','completed' if self.validation_passed else 'partial',
                   'Validation passed' if self.validation_passed else 'Missing physical validation remains explicit; software walkthrough can finish')

    def analysis(self):
        self.event('Data Analysis','running','Capacity caps, handovers, run ranges and failure maps')
        caps={str(cap):[dict(cell=x['cell'],within_cap=bool(x['best'] and x['best']['design']['rho']<=cap),
                             research_feasible=bool(x['best'] and x['best']['research_feasible'])) for x in self.surface]
              for cap in (1.,1.25,1.5,2.)}
        handovers=[]
        for grid,state,re in itertools.product('ABC',('islanded','interconnected'),(.2,.4,.6,.8)):
            path=sorted([x for x in self.surface if x['cell']['grid']==grid and x['cell']['state']==state and x['cell']['re']==re],key=lambda x:x['cell']['d'])
            previous=None
            for x in path:
                group=(x['decomposition'] or {}).get('binding_group')
                if group and previous and group!=previous:
                    handovers.append(dict(cell=x['cell'],previous=previous,current=group,partial=x['decomposition']['binding_is_partial']))
                if group: previous=group
        counts={g:dict(evaluated=0,failed=0,unknown=0) for g in GROUPS}
        with (self.out/'audit.jsonl').open() as file:
            for line in file:
                row=json.loads(line)
                for group,keys in GROUPS.items():
                    vals=[row['residuals'].get(k) for k in keys]
                    if any(v is None for v in vals): counts[group]['unknown']+=1
                    else:
                        counts[group]['evaluated']+=1; counts[group]['failed']+=int(any(v>0 for v in vals))
        limits=[]
        for cap,rows in caps.items():
            for grid in 'ABC':
                for state in (['islanded'] if grid=='A' else ['islanded','interconnected']):
                    good=[r['cell'] for r in rows if r['within_cap'] and r['cell']['grid']==grid and r['cell']['state']==state]
                    limits.append(dict(cap=float(cap),grid=grid,state=state,
                        maximum_tested_displacement=max((c['d'] for c in good),default=None),
                        maximum_tested_re=max((c['re'] for c in good),default=None),demonstration=not self.backend.research))
        write_json(self.out/'analysis.json',dict(cap_sensitivity=caps,tested_limits=limits,handovers=handovers,
                    failure_counts=counts,failure_denominator='measured evaluations per group; unknown reported separately'))
        self.plot(counts)
        from section38_flow import draw
        draw(self.out)
        self.event('Data Analysis','completed','No mean/std from three seeds; limits are within tested bounds')

    def plot(self,counts):
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(1,2,figsize=(13,4))
        for grid in 'ABC':
            data=[x for x in self.surface if x['cell']['grid']==grid and x['best']]
            axes[0].scatter([100*x['cell']['d'] for x in data],[x['best']['design']['rho'] for x in data],label=grid)
        axes[0].set(xlabel='SG displacement (%)',ylabel='Replacement ratio',title='Selected run minima'); axes[0].legend()
        axes[1].bar(list(counts),[v['failed']/v['evaluated'] if v['evaluated'] else 0 for v in counts.values()])
        for index,record in enumerate(counts.values()):
            if not record['evaluated']: axes[1].text(index,.02,'N/A',ha='center',fontsize=9)
        axes[1].set(ylabel='Failure fraction among measured evaluations',title='Unknown groups excluded; see analysis.json')
        fig.suptitle('SECTION 3.8 — '+('RESEARCH MODEL' if self.backend.research else 'DEMONSTRATION PHYSICS'))
        fig.tight_layout(); fig.savefig(self.out/'analysis.png',dpi=160); plt.close(fig)

    def run(self):
        self.event('START','running',self.config['mode'])
        try:
            for stage in (self.phase1,self.phase2,self.phase3,self.phase4,self.phase5,self.analysis): stage()
            self.event('END','validated' if self.validation_passed else 'walkthrough completed; research validation incomplete')
        except Exception as exc:
            self.event(self.phase,'failed',str(exc)); raise
        finally:
            write_json(self.out/'summary.json',dict(mode=self.config['mode'],evaluations=self.evaluations,
                        actual_dynamic_runs=self.backend.dynamic_runs,core_cells=len(self.surface),
                        research_validated=getattr(self,'validation_passed',False),seconds=time.perf_counter()-self.started))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='configs/walkthrough.json')
    parser.add_argument('--quick',action='store_true',help='Three benchmark cells; exercises all five stages')
    parser.add_argument('--output',help='Fresh output directory')
    args=parser.parse_args(); config=json.loads((ROOT/args.config).read_text())
    if args.output: config['output']=args.output
    if args.quick: config['quick']=True
    if config['mode']=='research':
        if config.get('quick') or config['seeds']!=3 or config['evaluations_per_run']!=2000 or config.get('enumeration_site_limit'):
            parser.error('Research protocol requires 50 cells, 3 seeds, 2000 evaluations, and complete site enumeration')
        if not config.get('calibration_manifest'): parser.error('Research mode requires a calibrated model manifest')
    Experiment(config).run()


if __name__=='__main__': main()
