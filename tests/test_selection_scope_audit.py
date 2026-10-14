"""Synthetic decision-scope tests; no material fit or model-quality claim."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

TOOLS=Path(__file__).resolve().parents[1]/'wannier_mcp/workflow/tools'
sys.path.insert(0,str(TOOLS))
import selection_scope_audit as scope
import workflow_runtime as runtime
import review_workflow
import manifold_selection
from orbital_evidence import digest


class SelectionScopeAudit(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cache=self.root/'charge.npz'
        self.e=np.tile([-10.,-4.,-2.,-.1,.4,2.,5.],(3,1))
        self.raw=np.zeros((1,3,7,1,9));self.raw[0,:,0,0,0]=.8
        self.raw[0,:,1:4,0,1]=.8
        self.raw[0,:,4,0,1]=.4
        self.raw[0,:,5:,0,1]=.2
        meta={'name':'invented-scope-test','ef':0.,'ef_source':'invented independent reference',
              'species':['Al'],'counts':[1],'spin_mode':'scalar','sampling':'band_path',
              'columns':['s','py','pz','px','dxy','dyz','dz2','dxz','x2-y2'],
              'procar_sha256':'a'*64}
        self.meta=meta;self.save()
        self.selection={'cache_sha256':hashlib.sha256(self.cache.read_bytes()).hexdigest(),
                        'proposal_id':'invented','basis':['Al:p'],'num_wann':3,
                        'target_band_ranges_1based':[[2,5]],
                        'band_selection':{'exclude_bands_1based':[]},
                        'windows_relative_eV':{'dis_eV':[-4.5,3.],'frozen_eV':[-.5,.8]},
                        'frozen_edge_candidate':{'reference':{'suggested_upper_eV':.5}}}

    def save(self):
        self.meta['source_files']={'kpoints':{'sha256':'b'*64}}
        geometry={'kcoords_type':'fractional','source_kpoints_sha256':'b'*64,
                  'reciprocal_lattice_2pi_per_angstrom':np.eye(3).tolist(),
                  'segments_1based':[[1,3]],'ticks':[{'index_1based':1,'label':'A'},
                                                {'index_1based':3,'label':'B'}]}
        np.savez(self.cache,metadata=json.dumps(self.meta),energies=self.e[None],raw=self.raw,
                 occupations=np.where(self.e[None]<0,2.,0.),
                 kcoords=np.array([[[0.,0.,0.],[.25,0.,0.],[.5,0.,0.]]]),path_geometry=json.dumps(geometry))

    def fresh_hash(self):
        self.selection['cache_sha256']=hashlib.sha256(self.cache.read_bytes()).hexdigest()

    def test_unprotected_target_is_reported_without_retuning(self):
        before=copy.deepcopy(self.selection)
        r=scope.audit(self.cache,self.selection)
        self.assertEqual(r['lower_protection']['unflagged_target_below_frozen'],6)
        self.assertEqual(r['lower_protection']['target_below_EF_inside_frozen'],3)
        self.assertIn('valence_frozen_lower',[q['slot'] for q in r['required_review']])
        self.assertEqual(self.selection,before)
        self.assertEqual(r['automatically_changed_variables'],[])
        self.assertFalse(r['scientific_approval'])

    def test_scalar_interval_includes_non_target_rows(self):
        self.selection['target_band_ranges_1based']=[[2,4]]
        r=scope.audit(self.cache,self.selection)
        self.assertEqual(r['scalar_membership']['outside_declared_target_states_inside_frozen'],3)
        self.assertEqual(r['scalar_membership']['negative_states_inside_frozen'],0)
        self.assertIn('scalar_window_membership',[q['slot'] for q in r['required_review']])

    def test_counterexample_uses_strongest_omitted_not_aggregate(self):
        self.raw[0,:,3,0,1]=.2
        self.raw[0,:,3,0,0]=.15
        self.raw[0,:,3,0,4]=.15
        self.save();self.fresh_hash()
        r=scope.audit(self.cache,self.selection)
        self.assertEqual(r['scalar_membership']['negative_states_inside_frozen'],0)
        self.raw[0,:,3,0,4]=.25
        self.save();self.fresh_hash()
        r=scope.audit(self.cache,self.selection)
        self.assertEqual(r['scalar_membership']['negative_states_inside_frozen'],3)
        self.assertEqual(r['automatically_changed_variables'],[])

    def test_count_conflict_is_not_basis_deficiency(self):
        self.selection['windows_relative_eV']['frozen_eV']=[-4.2,.8]
        r=scope.audit(self.cache,self.selection)
        self.assertEqual(r['numerical_checks']['max_nfrozen'],4)
        self.assertEqual(r['numerical_checks']['data_adequacy']['status'],'needs_frozen_review')
        self.assertIn('frozen_count_conflict',[q['slot'] for q in r['required_review']])
        self.assertEqual(self.selection['basis'],['Al:p'])
        self.assertEqual(r['automatically_changed_variables'],[])

    def test_no_global_gap_does_not_require_exclusion(self):
        self.e[1]=[-2.,-1.8,-1.,0.,.5,2.5,5.]
        self.save();self.fresh_hash()
        r=scope.audit(self.cache,self.selection)
        self.assertGreater(r['lower_protection']['target_below_frozen_inside_outer'],0)
        self.assertEqual(self.selection['band_selection']['exclude_bands_1based'],[])

    def test_excluded_target_and_stale_inputs_are_rejected(self):
        stale=copy.deepcopy(self.selection);stale['cache_sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'cache differ'):scope.audit(self.cache,stale)
        with self.assertRaisesRegex(ValueError,'review differ'):scope.audit(self.cache,self.selection,{'x':1})
        self.selection['band_selection']['exclude_bands_1based']=[2]
        with self.assertRaisesRegex(ValueError,'overlap'):scope.audit(self.cache,self.selection)

    def test_candidate_origin_and_valid_review_binding(self):
        review={'window_override':{'frozen_eV':[-.5,.8]}}
        self.selection['review_sha256']=digest(review)
        self.selection['reviewed_window_override']=review['window_override']
        r=scope.audit(self.cache,self.selection,review)
        self.assertEqual(r['upper_origin']['candidate_source'],'explicit_reviewed_scalar_override')
        self.assertTrue(r['upper_origin']['above_band_edge_probe'])
        self.assertFalse(r['scientific_approval'])

    def test_cli_compact_result_retains_review_facts(self):
        selection=self.root/'selection.json';selection.write_text(json.dumps(self.selection))
        out=self.root/'scope.json'
        with patch.dict(os.environ,{'WAN_WORKSPACE_ROOT':str(self.root)}):
            envelope,parsed=runtime.execute('selection_scope_audit.py',
                ['--cache',str(self.cache),'--selection',str(selection),'--out',str(out)],self.root)
        self.assertTrue(envelope['ok'],envelope)
        self.assertEqual(parsed['lower_protection']['unflagged_target_below_frozen'],6)
        self.assertIn('lower_protection',envelope['result'])
        self.assertTrue(out.is_file())

    def test_propose_orchestration_runs_audit_without_scientific_changes(self):
        inspection=manifold_selection.inspect(self.cache)
        ipath=self.root/'inspection.json';ipath.write_text(json.dumps(inspection))
        review={'format':manifold_selection.FORMAT,'inspection_id':inspection['inspection_id'],
                'basis_revision':'invented',
                'task_scope_review':{'format':'task-scope-review-v1','objective_kind':'primary_task_manifold',
                  'objective':'Invented independently declared p-derived task.',
                  'requested_scope_evidence':None,'task_anchor_band_ranges_1based':[[2,4]],
                  'deep_blocks':[{'band_range_1based':[1,1],'orbital_groups':['Al:s'],
                                  'role':'retained_background','reason':'Invented concentrated lower background.'}]},
                'additional_basis_evidence':{},
                'orbital_decisions':[{'group':g,'action':a,'task_role':r,
                  'reason':'Invented raw task-state review.','risk_if_included':'Candidate scope.',
                  'risk_if_omitted':'Task representation.'} for g,a,r in
                  [('Al:p','include','task_component'),('Al:s','omit_first_trial','deep_only'),
                   ('Al:d','omit_first_trial','no_task_role')]],
                'target_band_ranges_1based':[[2,5]],'reference_band_ranges_1based':[[2,7]],
                'frozen_band_ranges_1based':[[4,5]],'reference_upper_status':'reviewed_decay',
                'target_reason':'Invented task and low conduction.',
                'reference_reason':'Invented tail-inspection domain.',
                'frozen_reason':'Inspect unprotected lower states in the audit.',
                'window_override':{'dis_eV':[-4.5,3.],'frozen_eV':[-.5,.8],
                                   'reason':'Invented explicit draft candidate for audit.'}}
        rp=self.root/'review.json';rp.write_text(json.dumps(review));before=rp.read_bytes()
        with patch.dict(os.environ,{'WAN_WORKSPACE_ROOT':str(self.root),'MPLBACKEND':'Agg',
                                   'MPLCONFIGDIR':str(self.root/'mpl')}):
            result=review_workflow.propose(str(self.cache),str(ipath),str(rp),str(self.root/'draft'),str(self.root))
        self.assertTrue(result['ok'],result)
        self.assertTrue(result['scope_audit']['ok'],result)
        self.assertTrue((self.root/'draft/scope_audit.json').is_file())
        self.assertEqual(rp.read_bytes(),before)
        decision=json.loads((self.root/'draft/selection.json').read_text())
        self.assertEqual(decision['windows_relative_eV']['frozen_eV'],[-.5,.8])


if __name__=='__main__':unittest.main()
