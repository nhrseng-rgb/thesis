"""Small tests for optimization semantics and research evidence handling."""
import json
import unittest
from pathlib import Path
import numpy as np
from optimizers import optimize,dominates,fronts
from experiment_38 import core_cells,seed_for,Experiment,compositions
from study_backend import constraints


class OptimizationTests(unittest.TestCase):
    def test_deb_feasibility_before_objectives(self):
        feasible=dict(eligible=True,cv=0,objectives=[2,2])
        infeasible=dict(eligible=False,cv=.01,objectives=[0,0])
        self.assertTrue(dominates(feasible,infeasible))
        self.assertFalse(dominates(infeasible,feasible))

    def test_pareto_tradeoff(self):
        rows=[dict(eligible=True,cv=0,objectives=f) for f in ([0,1],[1,0],[2,2])]
        self.assertEqual(fronts(rows),[[0,1],[2]])

    def test_exact_budget_bounds_and_reproducibility(self):
        def evaluate(x):
            violation=max(0.,.15-float(x.sum()))
            return dict(eligible=violation==0,cv=violation,objectives=[float(x[0]),float(1-x[0])])
        for algorithm in ('RNSGA-II','MOPSO','AMOSA'):
            a=optimize(algorithm,evaluate,3,35,123,population=8)
            b=optimize(algorithm,evaluate,3,35,123,population=8)
            self.assertEqual(len(a),35)
            self.assertEqual(a,b)
            self.assertTrue(all(0<=v<=1 for row in a for v in row['x']))


class ProtocolTests(unittest.TestCase):
    def test_matrix_and_seeds(self):
        cells=core_cells()
        self.assertEqual(len(cells),50)
        self.assertEqual([sum(c['grid']==g for c in cells) for g in 'ABC'],[10,20,20])
        self.assertEqual(seed_for(cells[0],0),seed_for(dict(re=.2,state='islanded',grid='A',d=.2),0))
        self.assertNotEqual(seed_for(cells[0],0),seed_for(cells[0],1))

    def test_missing_metrics_do_not_pass(self):
        config=json.loads(Path('configs/walkthrough.json').read_text())
        residual=constraints({'nadir_hz':59.6},config['thresholds'])
        self.assertLess(residual['g1'],0)
        self.assertIsNone(residual['g7'])
        self.assertIsNone(residual['g9'])

    def test_pdf_sensitivity_values_and_coi_angle(self):
        config=json.loads(Path('configs/walkthrough.json').read_text())
        self.assertEqual(config['sensitivity']['virtual_h_s'],[2,8,10])
        self.assertEqual(config['sensitivity']['current_limit_pu'],1.2)
        self.assertEqual(config['spacing_site_counts'],[1,2,3,5])
        residual=constraints({'angle_coi_deviation_deg':181},config['thresholds'])
        self.assertGreater(residual['g8'],0)

    def test_positive_share_grid(self):
        values=list(compositions(10,5))
        self.assertEqual(len(values),126)
        self.assertTrue(all(sum(v)==10 and min(v)>0 for v in values))

    def test_repair_keeps_site_class_and_simplex(self):
        experiment=Experiment.__new__(Experiment)
        experiment.config={'rho_max':2}
        experiment.candidates={'A':list(range(10))}
        experiment.classes={}
        experiment.distances={'A':{(a,b):abs(a-b) for a in range(10) for b in range(10)}}
        design=experiment.decode(np.zeros(7),'A',3,'High')
        self.assertEqual(len(set(design['sites'])),3)
        self.assertIn(tuple(design['sites']),experiment.site_sets('A',3,'High'))
        self.assertAlmostEqual(sum(design['shares']),1)
        self.assertTrue(all(v>0 for v in design['shares']))


if __name__=='__main__': unittest.main()
