"""Executable Fig. 3.1 workflow. Demonstration model, not calibrated thesis results."""
from __future__ import annotations
import argparse
import json
import subprocess
import copy
import os
from functools import lru_cache
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR', str(Path(__file__).resolve().parent/'.cache'/'matplotlib'))
import numpy as np
import pandapower as pp
import pandapower.networks as pn
from scipy.integrate import solve_ivp
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
GROUPS = ('FFR', 'RoCoF', 'PR', 'Q', 'REG')
GRIDS = {'A': (14016., 668., 0.), 'B': (2681., 150., 89.), 'C': (2577., 150., 23.)}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False,
                              default=lambda x: x.item() if isinstance(x, np.generic) else str(x)), encoding='utf-8')


@lru_cache(maxsize=3)
def _network(grid):
    """Scale all power and impedance bases consistently; retain IEEE39 topology.

    This deliberately retains the reference machine roster. Removing G1 and
    calibrating individual ratings/inertias requires the study's machine data.
    """
    net = pn.case39()
    scale = GRIDS[grid][0] / net.load.p_mw.sum()
    for table, cols in ((net.load, ('p_mw', 'q_mvar')), (net.gen, ('p_mw',)),
                        (net.ext_grid, ())):
        for col in cols:
            table[col] *= scale
        for col in ('min_p_mw', 'max_p_mw', 'min_q_mvar', 'max_q_mvar'):
            if col in table:
                table[col] *= scale
    net.line.r_ohm_per_km /= scale
    net.line.x_ohm_per_km /= scale
    net.line.c_nf_per_km *= scale
    net.line.max_i_ka *= scale
    net.trafo.sn_mva *= scale
    net.sn_mva *= scale
    # Demo AVR targets: the unmodified IEEE39 case includes setpoints >1.05 pu.
    # Lower targets account for high line-charging voltage after RE redispatch.
    net.gen.vm_pu = 0.99
    net.ext_grid.vm_pu = 0.99
    pp.runpp(net, numba=False)
    return net


def network(grid):
    return copy.deepcopy(_network(grid))


@lru_cache(maxsize=100)
def static_case(grid, re, state):
    net = network(grid)
    load, _, imp = GRIDS[grid]
    imp = imp if state == 'interconnected' else 0.
    for _, row in net.load.iterrows():
        pp.create_sgen(net, int(row.bus), p_mw=float(row.p_mw * re), name='RE demo')
    net.gen.p_mw *= max(0.02, (1 - re) - imp / load)
    if imp:
        pp.create_sgen(net, 38, p_mw=imp, name='Scheduled HVDC (no inertia)')
    pp.runpp(net, numba=False, max_iteration=30)
    return net


def prepare(grid, re, displacement, state, sites, rho):
    net = copy.deepcopy(static_case(grid, re, state))
    load, largest, imp = GRIDS[grid]
    imp = imp if state == 'interconnected' else 0.
    displaced_mw = load * displacement  # Demo denominator; research uses retired SG MW.
    capacity = rho * displaced_mw
    for site in sites:
        pp.create_sgen(net, int(site), p_mw=0., q_mvar=0., sn_mva=capacity / len(sites), name='GFM reserve')
    # Idle GFM injections do not alter the solved static operating point.
    p = dict(load_mw=load, loss_mw=max(largest, imp), capacity_mw=capacity,
             h_s=5. * (1 - displacement), virtual_h_s=5., damping=1.,
             governor_gain=15. * (1 - displacement), governor_tau=1.,
             bess_gain=25., bess_tau=0.08, event_s=1., stop_s=120., dt=0.02)
    return net, p


def dynamics(p):
    """Aggregate swing + governor + limited BESS response, same equations as SLX."""
    L, C = p['load_mw'], p['capacity_mw']
    M = 2 * (p['h_s'] + p['virtual_h_s'] * C / L) / 60
    def rhs(t, x):
        df, gov, bess = x
        loss = p['loss_mw'] / L if t >= p['event_s'] else 0.
        cmd = np.clip(-p['bess_gain'] * C / L * df, -C / L, C / L)
        return [(gov + bess - loss - p['damping'] * df) / M,
                (-p['governor_gain'] * df - gov) / p['governor_tau'],
                (cmd - bess) / p['bess_tau']]
    # Integrate across the event explicitly, avoiding adaptive-step event smearing.
    t = np.arange(0, p['stop_s'] + p['dt']/2, p['dt'])
    before = t[t < p['event_s']]
    after = t[t >= p['event_s']]
    sol = solve_ivp(rhs, (p['event_s'], p['stop_s']), [0., 0., 0.],
                    t_eval=after, method='LSODA', rtol=1e-8, atol=1e-10, max_step=0.5)
    if not sol.success:
        raise RuntimeError(sol.message)
    x = np.vstack((np.zeros((len(before), 3)), sol.y.T))
    return t, x[:, 0] + 60, x[:, 2] * L


def metrics(t, f, bess, p, net):
    window = int(round(0.5 / p['dt']))
    rocof = float(np.max(np.abs((f[window:] - f[:-window]) / 0.5)))
    vm = net.res_bus.vm_pu.to_numpy()
    m = dict(nadir_hz=float(f.min()), rocof_hz_s=rocof,
             settling_hz=float(f[t >= 110].mean()), voltage_deviation_pu=float(np.max(abs(vm - 1))),
             reserve_mw=p['capacity_mw'], reserve_required_mw=0.04*p['load_mw'],
             discharge_mwh=float(np.trapezoid(np.maximum(bess, 0), t)/3600))
    passes = dict(FFR=m['nadir_hz'] >= 59.5, RoCoF=rocof <= 1.,
                  PR=59.7 <= m['settling_hz'] <= 60.3,
                  Q=m['voltage_deviation_pu'] <= 0.05,
                  REG=m['reserve_mw'] >= m['reserve_required_mw'])
    # Q only checks steady-state voltage here. No g5 dynamic Q or stability evidence.
    return m, passes


class Workflow:
    def __init__(self, args):
        self.args, self.out = args, ROOT / args.output
        self.out.mkdir(parents=True, exist_ok=True)
        self.events, self.records, self.cache = [], [], {}
        self.simulations = 0

    def event(self, phase, status, detail=''):
        self.events.append(dict(phase=phase, status=status, detail=detail))
        write_json(self.out/'workflow.json', self.events)
        print(f'{phase}: {status} {detail}', flush=True)

    def evaluate(self, cell, sites, rho):
        key = (tuple(cell), tuple(sites), round(float(rho), 8))
        if key in self.cache:
            return self.cache[key]
        g, re, d, state = cell
        row = dict(grid=g, re=re, displacement=d, state=state, sites=list(sites), rho=float(rho),
                   research_feasible=False, stability_status='not evaluated',
                   model='DEMONSTRATION: aggregate dynamics, incomplete constraint coverage')
        try:
            net, p = prepare(g, re, d, state, sites, rho)
            # Static failures count as evaluations and never invoke a dynamic solver.
            if not np.isfinite(net.res_bus.vm_pu).all():
                raise ValueError('Non-finite power-flow voltage')
            if max(abs(net.res_bus.vm_pu - 1)) > 0.05:
                row.update(status='static_screen_failed', demo_pass=False, passes={'Q': False})
            else:
                t, f, bess = self.dynamic_backend(p)
                self.simulations += 1
                m, passes = metrics(t, f, bess, p, net)
                row.update(status='evaluated', metrics=m, passes=passes, demo_pass=all(passes.values()))
        except Exception as exc:
            row.update(status='failed', error=str(exc), demo_pass=False)
        self.records.append(row)
        self.cache[key] = row
        return row

    def dynamic_backend(self, p):
        if self.args.backend == 'python':
            return dynamics(p)
        inp, out = self.out/'dynamic_input.json', self.out/'dynamic_output.csv'
        write_json(inp, p)
        def mq(path): return str(path.resolve()).replace("'", "''").replace('\\', '/')
        cmd = f"addpath('{mq(ROOT/'matlab')}'); run_case('{mq(inp)}','{mq(out)}');"
        subprocess.run(['matlab', '-batch', cmd], check=True, timeout=300, capture_output=True)
        a = np.loadtxt(out, delimiter=',', skiprows=1)
        return a[:, 0], a[:, 1], a[:, 2]

    def search(self, cell, sites):
        rows = [self.evaluate(cell, sites, r) for r in np.linspace(0, 2, self.args.ratio_points)]
        good = [r for r in rows if r['demo_pass']]
        return min(good, key=lambda r: r['rho']) if good else None

    def decompose(self, cell, selected):
        sites, top = selected['sites'], selected['rho']
        results = {}
        for group in GROUPS:
            samples = np.linspace(0, top, 9)
            def ok(r): return self.evaluate(cell, sites, r).get('passes', {}).get(group, False)
            flags = [ok(float(r)) for r in samples]
            monotone = all(not flags[i] or flags[i+1] for i in range(len(flags)-1))
            if not any(flags):
                results[group] = dict(ratio=None, method='no passing sample')
                continue
            if monotone:
                lo, hi = 0., float(top)
                if flags[0]: hi = 0.
                while hi-lo > 0.001:
                    mid = (lo+hi)/2
                    if ok(mid): hi = mid
                    else: lo = mid
                results[group] = dict(ratio=hi, method='sampled monotonicity then bisection')
            else:
                grid = np.append(np.arange(0, top, 0.01), top)
                feasible = [float(r) for r in grid if ok(float(r))]
                results[group] = dict(ratio=min(feasible) if feasible else None, method='nonmonotone grid 0.01')
        results['STAB'] = dict(ratio=None, method='unavailable: needs multimachine and converter models')
        known = {k: v['ratio'] for k, v in results.items() if v['ratio'] is not None}
        lower = max(known.values()) if known else None
        return dict(groups=results, partial_lower_bound=lower,
                    partial_binding_group=max(known, key=known.get) if known else None,
                    consistency_pass=lower is not None and top+0.001 >= lower,
                    partial_gap_percent=100*(top-lower)/top if top else 0., complete=False)

    def run(self):
        self.event('START', 'running')
        candidates = {}
        # Fig.3.1 NO branch is bounded: failed baseline never falls through to Phase 2.
        for attempt in range(1, 4):
            try:
                for g in GRIDS:
                    net = network(g)
                    pp.to_json(net, str(self.out/f'baseline_{g}.json'))
                    # Voltage-ranked shortlist is a DEMO substitute for SC/participation screening.
                    candidates[g] = list(map(int, net.res_bus.vm_pu.sort_values().index[:10]))
                self.event('Phase 1', 'passed', f'Attempt {attempt}: three converged scaled IEEE39 baselines saved')
                break
            except Exception as exc:
                self.event('Phase 1', 'retry', str(exc))
        else:
            raise RuntimeError('Baseline convergence gate failed after 3 attempts')
        refs = {}
        screening = []
        for g in GRIDS:
            # Five count/spacing configurations. Spacing uses index separation only in demo.
            b = candidates[g]
            configs = [('single', b[:1]), ('medium3', b[::3][:3]), ('medium5', b[::2][:5]),
                       ('low3', b[:3]), ('high3', [b[0], b[4], b[9]])]
            solutions = [(name, self.search((g, .4, .4, 'islanded'), sites)) for name, sites in configs]
            feasible = [(name, row) for name, row in solutions if row]
            if not feasible:
                raise RuntimeError(f'No passing demo reference for grid {g}; inspect evaluations')
            vals = [row['rho'] for _, row in feasible]
            variation = (max(vals)-min(vals))/max(max(vals), 1e-12)
            reference = next((row for name, row in feasible if name == 'medium3'), feasible[0][1])
            if variation >= .02:
                reference = max(feasible, key=lambda nr: nr[1]['rho'])[1]
            refs[g] = reference['sites']
            screening.append(dict(grid=g, variation=variation, selected_sites=refs[g],
                                  incomplete=len(feasible)!=5, configurations=solutions))
        write_json(self.out/'screening.json', screening)
        self.event('Phase 2', 'completed', 'Five demo configurations per grid; sampled search, not study enumeration')
        cells = [(g, .4, .4, 'islanded') for g in GRIDS]
        if self.args.full_matrix:
            cells = [(g, re, d, s) for g in GRIDS for s in
                     (['islanded'] if g == 'A' else ['islanded', 'interconnected'])
                     for re in (.2,.4,.6,.8) for d in (.2,.4,.6,.8) if d <= re]
        surface = []
        for cell in cells:
            best = self.search(cell, refs[cell[0]])
            entry = dict(cell=list(cell), best=best,
                         decomposition=self.decompose(cell, best) if best else None)
            surface.append(entry)
        write_json(self.out/'surface.json', surface)
        self.event('Phase 3', 'completed', f'{len(cells)} demo cells; no RNSGA-II or research feasibility claim')
        spacing = []
        if self.args.spacing:
            for g in GRIDS:
                valid = [x for x in surface if x['cell'][0] == g and x['best']]
                if not valid: continue
                critical = max(valid, key=lambda x: x['best']['rho'])
                cell, rating = critical['cell'], critical['best']['rho']
                for n in (1,3,5):
                    sites = candidates[g][:n]
                    spacing.append(dict(grid=g, n=n, free=self.search(cell, sites),
                                        matched=self.evaluate(cell, sites, rating),
                                        matched_capacity_mw=rating*GRIDS[g][0]*cell[2],
                                        matched_energy_mwh=rating*GRIDS[g][0]*cell[2]))
            write_json(self.out/'spacing.json', spacing)
            self.event('Phase 4', 'completed', 'Demo site-count sweep; matched total MW and 1-hour MWh')
        else:
            self.event('Phase 4', 'skipped', 'Spacing branch disabled by run configuration')
        sensitivity = []
        for entry in surface:
            if not entry['best']: continue
            cell, best = entry['cell'], entry['best']
            net, p = prepare(*cell, best['sites'], best['rho'])
            t, f, power = self.dynamic_backend(p)
            name = '_'.join(map(str,cell)).replace('.','p')
            np.savetxt(self.out/f'trace_{name}.csv', np.c_[t, f, power],
                       delimiter=',', header='time_s,frequency_hz,bess_mw', comments='')
            if not (self.out/'dynamic_input.json').exists(): write_json(self.out/'dynamic_input.json', p)
            for h in (2., 7., 10.):
                q = dict(p, virtual_h_s=h)
                tt, ff, bb = self.dynamic_backend(q)
                mm, flags = metrics(tt, ff, bb, q, net)
                sensitivity.append(dict(cell=cell, virtual_h_s=h, metrics=mm))
        write_json(self.out/'validation.json', dict(emt_status='NOT PERFORMED',
                   research_validation_pass=False, sensitivity=sensitivity,
                   limitation='Three virtual-inertia demonstrations only; not the eleven study cases'))
        self.event('Phase 5', 'partial', 'Inertia sensitivity complete; EMT and full research validation pending')
        self.report(surface)
        self.event('Data Analysis', 'completed', 'Charts and per-evaluation records saved')
        self.event('END', 'demo completed', 'Research validation remains incomplete')
        write_json(self.out/'evaluations.json', self.records)
        write_json(self.out/'summary.json', dict(mode='DEMONSTRATION', backend=self.args.backend,
                   evaluations=len(self.records), dynamic_evaluations=self.simulations,
                   pass_through_rate=self.simulations/max(len(self.records),1),
                   research_ready=False, cells=len(cells)))

    def report(self, surface):
        fig, ax = plt.subplots(figsize=(10, 4))
        labels, vals = [], []
        for entry in surface:
            if entry['best']:
                labels.append('/'.join(map(str,entry['cell'])))
                vals.append(entry['best']['rho'])
        ax.bar(range(len(vals)), vals, color='#267e91')
        ax.set_xticks(range(len(vals)), labels, rotation=70, fontsize=7)
        ax.set_ylabel('Sampled replacement ratio (demo denominator)')
        ax.set_title('DEMONSTRATION ONLY — incomplete constraint coverage')
        fig.tight_layout(); fig.savefig(self.out/'ratio_surface.png', dpi=160); plt.close(fig)
        if len(surface) > 3:
            panels = [('A','islanded'),('B','islanded'),('B','interconnected'),
                      ('C','islanded'),('C','interconnected')]
            fig, axes = plt.subplots(1,5,figsize=(17,4),sharey=True)
            levels = [.2,.4,.6,.8]
            for ax,(grid,state) in zip(axes,panels):
                a = np.full((4,4), np.nan)
                for entry in surface:
                    g,re,d,s=entry['cell']
                    if g==grid and s==state and entry['best']:
                        a[levels.index(d),levels.index(re)] = entry['best']['rho']
                ax.imshow(a,origin='lower',vmin=0,vmax=2,cmap='viridis')
                for i,j in np.ndindex(a.shape):
                    if np.isfinite(a[i,j]): ax.text(j,i,f'{a[i,j]:.2f}',ha='center',va='center',color='white')
                ax.set_xticks(range(4),['20','40','60','80'])
                ax.set_yticks(range(4),['20','40','60','80'])
                ax.set_xlabel('RE (%)'); ax.set_title(f'{grid}: {state}',fontsize=9)
            axes[0].set_ylabel('Displacement (%)')
            fig.suptitle('DEMONSTRATION sampled ratio matrix — blank = untested or no passing demo candidate')
            fig.tight_layout(); fig.savefig(self.out/'ratio_matrix.png',dpi=160); plt.close(fig)
        fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        traces = list(self.out.glob('trace_*.csv'))
        if len(traces) > 6:
            traces = []
            for g in GRIDS:
                choices = [e for e in surface if e['cell'][0]==g and e['best']]
                if choices:
                    entry = max(choices,key=lambda e:e['best']['rho'])
                    name = '_'.join(map(str,entry['cell'])).replace('.','p')
                    traces.append(self.out/f'trace_{name}.csv')
        for path in traces:
            a = np.loadtxt(path, delimiter=',', skiprows=1)
            axes[0].plot(a[:,0], a[:,1], label=path.stem[6:])
            axes[1].plot(a[:,0], a[:,2])
        axes[0].axhline(59.5, color='red', linestyle='--', label='Study nadir threshold')
        axes[0].set_ylabel('Frequency (Hz)'); axes[0].legend(fontsize=6)
        axes[1].set_ylabel('BESS response (MW)'); axes[1].set_xlabel('Time (s)')
        axes[0].set_title('Aggregate demonstration dynamics (up to one critical cell per grid for large runs)')
        fig.tight_layout(); fig.savefig(self.out/'frequency_response.png', dpi=160); plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='results')
    parser.add_argument('--spacing', action='store_true')
    parser.add_argument('--full-matrix', action='store_true', help='50 demonstration cells; not the full research protocol')
    parser.add_argument('--ratio-points', type=int, default=9)
    parser.add_argument('--backend', choices=['python','simulink'], default='python')
    args = parser.parse_args()
    if args.ratio_points < 2: parser.error('--ratio-points must be >= 2')
    workflow = Workflow(args)
    try: workflow.run()
    except Exception:
        write_json(workflow.out/'evaluations.json', workflow.records)
        workflow.event('WORKFLOW', 'failed')
        raise


if __name__ == '__main__': main()
