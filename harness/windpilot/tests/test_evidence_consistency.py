import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from urbanwind_harness.evidence_consistency import difference, probe_series, verify_evidence, window_statistics
from urbanwind_harness.open_runner import _public_file


class EvidenceConsistencyRegressions(unittest.TestCase):
    def fixture(self, directory, raw_velocity=None, sample_type='patchProbes'):
        root=Path(directory);canonical=root/'canonical';workspace=root/'workspace'
        canonical.mkdir();workspace.mkdir();(canonical/'local_assets').mkdir()
        def write(base,name,value):
            p=base/name;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(json.dumps(value),encoding='utf-8')
        write(canonical,'reporting_contract.json',{'expected_solver_case_ids':['demo-case']})
        write(canonical,'submission_format.json',{'documents':{'profile_results.json':{'objects':{
            'inlet_U_m_s':{'container_type':'list'},'inlet_U_m_s/*':{'container_type':'list'},
            'inlet_U_m_s/*/*':{'container_type':'list'}}}}})
        contract={'case_id':'demo-case','required_manifest':'samples.json','profile_target_csv':'local_assets/targets.csv',
            'profile_points_m':[[0,0,1],[0,0,2]],'profile_iterations':[20,40],'profile_iteration_count':2,
            'raw_observables':['inlet patchProbes U'],
            'frozen_gates':{'max_inlet_U_rmse_m_s':.01},
            'result_contract':{'path':'profile_results.json','inlet_U_shape':[2,2,3]}}
        write(canonical,'local_assets/profile_contract.json',contract)
        (canonical/'local_assets/targets.csv').write_text('z_m,layer_thickness_m,target_Ux_m_s\n1,1,1\n2,3,2\n',encoding='utf-8')
        result={'case_id':'demo-case','iterations':[20,40],
            'inlet_U_m_s':[[[1.,0.,0.],[2.,0.,0.]],[[1.,0.,0.],[2.,0.,0.]]],
            'metrics':{'inlet_profile_rmse_ms':0.},'all_run_gates_pass':True}
        write(workspace,'profile_results.json',result)
        raw=workspace/'case/postProcessing/inletProfile/0/U';raw.parent.mkdir(parents=True)
        raw.write_text(raw_velocity or '# Probe 0 (0 0 1)\n# Probe 1 (0 0 2)\n20 (1 0 0) (2 0 0)\n40 (1 0 0) (2 0 0)\n')
        control=workspace/'case/system/controlDict';control.parent.mkdir(parents=True)
        control.write_text('application simpleFoam; endTime 40; functions { inletProfile { type '+sample_type+'; patches (inlet); fields (U); writeInterval 20; } }')
        refs={key:{'path':str(p.relative_to(workspace)).replace('\\','/'),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            for key,p in [('probe_U',raw),('controlDict',control)]}
        write(workspace,'samples.json',{'case_id':'demo-case','evidence':refs})
        return canonical,workspace,result,write

    def test_valid_patch_sampling_shape_raw_and_weighted_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w,_,_=self.fixture(tmp)
            before={str(p):p.read_bytes() for p in w.rglob('*') if p.is_file()}
            result=verify_evidence(w,c,_public_file)
            self.assertEqual(result['errors'],[])
            self.assertEqual(result['raw_report_comparisons'],1)
            self.assertEqual(result['workspace_writes'],0)
            self.assertEqual(before,{str(p):p.read_bytes() for p in w.rglob('*') if p.is_file()})

    def test_report_loses_vector_axis_like_observed_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w,r,write=self.fixture(tmp)
            r['inlet_U_m_s']=[[v[0] for v in row] for row in r['inlet_U_m_s']]
            write(w,'profile_results.json',r)
            codes={x['code'] for x in verify_evidence(w,c,_public_file)['findings']}
            self.assertTrue({'public_container_type','declared_array_shape','report_raw_mismatch'} <= codes)

    def test_flattened_native_vectors_rejected_before_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w,_,_=self.fixture(tmp,raw_velocity='# Probe 0 (0 0 1)\n# Probe 1 (0 0 2)\n20 1 0 0 2 0 0\n40 1 0 0 2 0 0\n')
            result=verify_evidence(w,c,_public_file)
            self.assertTrue(any('vector_groups_required' in x.get('actual','') for x in result['findings']))

    def test_boundary_settings_substituted_for_actual_internal_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw='# Probe 0 (0 0 1)\n# Probe 1 (0 0 2)\n20 (1.1 0 0) (2.2 0 0)\n40 (1.1 0 0) (2.2 0 0)\n'
            c,w,r,_=self.fixture(tmp,raw_velocity=raw,sample_type='probes')
            result=verify_evidence(w,c,_public_file)
            codes={x['code'] for x in result['findings']}
            self.assertTrue({'report_raw_mismatch','raw_inlet_public_gate','inlet_internal_field_sampling','reported_public_metric_mismatch'} <= codes)
            metric=next(x for x in result['observations'] if x.get('metric')=='inlet_U_rmse_m_s')
            self.assertAlmostEqual(metric['value'],(0.0325)**.5)
            self.assertTrue(json.loads((w/'profile_results.json').read_text())['all_run_gates_pass'])

    def test_stale_hash_cannot_certify_raw_report_consistency(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w,_,_=self.fixture(tmp)
            p=w/'samples.json';x=json.loads(p.read_text());x['evidence']['probe_U']['sha256']='0'*64;p.write_text(json.dumps(x))
            self.assertTrue(any(x['code']=='raw_evidence_hash' for x in verify_evidence(w,c,_public_file)['findings']))

    def test_exact_public_coordinate_identity_records_float_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w,_,_=self.fixture(tmp,raw_velocity='# Probe 0 (0 0 1.0000000000000002)\n# Probe 1 (0 0 2)\n20 (1 0 0) (2 0 0)\n40 (1 0 0) (2 0 0)\n')
            finding=next(x for x in verify_evidence(w,c,_public_file)['findings'] if x['code']=='public_probe_coordinate_identity')
            self.assertLess(finding['max_abs_delta_m'],1e-14)

    def test_preflight_reads_actual_patch_selection_without_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w,_,_=self.fixture(tmp,sample_type='probes')
            result=verify_evidence(w,c,_public_file,case_paths=['case'],include_reports=False)
            self.assertTrue(any(x.get('code')=='inlet_internal_field_sampling' for x in result['unknown_or_advisories']))
            self.assertTrue(any(x.get('patches')=='( inlet )' for x in result['observations']))

    def test_path_escape_is_a_read_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w,_,_=self.fixture(tmp);p=w/'samples.json';x=json.loads(p.read_text())
            x['evidence']['probe_U']['path']='../private-secret';p.write_text(json.dumps(x))
            result=verify_evidence(w,c,_public_file)
            self.assertTrue(any(x['code']=='raw_probe_parse' and 'not_a_workspace_relative_path' in x['actual'] for x in result['findings']))

    def test_strict_native_row_validation(self):
        for raw in ['20 (1 2 3)\n20 (1 2 3)', '20 (1 2 3)\n40 (1 2)', '20 (nan 0 0)', '# Probe 1 (0 0 1)\n20 (1 2 3)']:
            with self.subTest(raw=raw),self.assertRaises(ValueError):probe_series(raw,True,1)

    def test_native_patch_header_with_projection_annotation_is_parsed(self):
        text='# Probe 0 (0 0 1) at patch inlet with a distance of 0 m to the original point (0 0 1)\n20 (1 2 3)\n'
        self.assertEqual(probe_series(text,True,1)['coordinates'],[[0,0,1]])

    def test_units_dictionary_is_not_a_second_result_and_findings_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w,r,write=self.fixture(tmp)
            r['units']={'inlet_U_m_s':'m/s','inlet_k_m2_s2':'m2/s2'}
            write(w,'profile_results.json',r)
            self.assertEqual(verify_evidence(w,c,_public_file)['errors'],[])
            r['inlet_U_m_s']=[[1,2],[1,2]];write(w,'profile_results.json',r)
            result=verify_evidence(w,c,_public_file)
            self.assertLessEqual(len(result['findings']),4)
            self.assertTrue(any(x.get('occurrences',0)==4 for x in result['findings']))

    def test_difference_catches_shape_nonfinite_and_wrong_value(self):
        self.assertIsNone(difference([1.,2.],[1.,2.]))
        self.assertEqual(difference([[1.]],[[1.,2.]])['reason'],'shape')
        self.assertEqual(difference([float('nan')],[1.])['reason'],'nonnumeric_or_nonfinite')
        self.assertEqual(difference([1.,2.],[1.,3.])['first_difference']['pointer'],'/1')

    def test_population_resolved_tke_and_window_endpoints(self):
        series=probe_series('0 (0 0 0)\n1 (2 0 0)\n2 (4 0 0)\n',True,1)
        a=window_statistics(series,[0,2]);b=window_statistics(series,[0,2],True)
        self.assertEqual(a['mean_Ux_m_s'],[2])
        self.assertAlmostEqual(a['resolved_tke_m2_s2'][0],4/3)
        self.assertEqual(b['sample_count'],2)
        self.assertEqual(b['mean_Ux_m_s'],[3])
        self.assertEqual(b['resolved_tke_m2_s2'],[.5])

    def test_generic_les_statistics_recomputed_from_case_bound_raw_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w,r,write=self.fixture(tmp)
            write(c,'reporting_contract.json',{'expected_solver_case_ids':['demo-case'],
                'simulation_result_contract':{'case_results':{'exact_case_ids':['demo-case'],'probe_array_length':2,'statistical_window_s':[20,40]}}})
            write(c,'submission_format.json',{'documents':{'simulation_metrics.json':{'objects':{}}}})
            write(w,'evidence_manifest.json',{'entries':[{'role':'raw_probe_timeseries','case_id':'demo-case',
                'path':'case/postProcessing/inletProfile/0/U','sha256':hashlib.sha256((w/'case/postProcessing/inletProfile/0/U').read_bytes()).hexdigest()}]})
            result={'case_results':[{'case_id':'demo-case','window_s':[20,40],'sample_count':2,
                'mean_Ux_m_s':[1,2],'resolved_tke_m2_s2':[0,0]}]}
            write(w,'simulation_metrics.json',result)
            good=verify_evidence(w,c,_public_file)
            self.assertEqual(good['errors'],[])
            result['case_results'][0]['mean_Ux_m_s'][1]=9
            write(w,'simulation_metrics.json',result)
            bad=verify_evidence(w,c,_public_file)
            self.assertTrue(any(x['code']=='reported_raw_statistics_mismatch' for x in bad['findings']))


if __name__=='__main__':unittest.main()
