import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from urbanwind_harness.agent_judge import build_payload
from urbanwind_harness.open_runner import reviewer_evidence
from urbanwind_harness.public_verification import log_observation
from urbanwind_harness.review_evidence import deterministic_feedback, referenced_json_paths, registered_execution_ids


def document(path, value):
    text = json.dumps(value)
    return {'path': path, 'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}


class EvidenceReviewRegressions(unittest.TestCase):
    def test_large_manifest_keeps_late_run_exit_times_and_evidence(self):
        runs = [{'case_id': str(i), 'commands': ['x'*6000], 'software': [{'name': 'openfoam'}],
                 'started_at': '2026-10-12T01:00:00+00:00', 'ended_at': '2026-10-12T01:01:00+00:00',
                 'exit_code': 7 if i == 19 else 0, 'evidence': [{'path': 'raw-'+str(i), 'sha256': 'a'*64}]}
                for i in range(20)]
        payload = build_payload({'public_documents': [], 'selected_public_artifacts': [document('run_manifest.json', {'runs': runs})]}, {'errors': []})
        self.assertIsNotNone(payload)
        view = payload['evidence']['selected_public_artifacts'][0]['content']['runs']
        self.assertEqual(len(view), 20)
        self.assertEqual(view[-1]['exit_code'], 7)
        self.assertEqual(view[-1]['started_at'], runs[-1]['started_at'])
        self.assertEqual(view[-1]['evidence'], runs[-1]['evidence'])
        self.assertIn('omitted_characters', view[-1]['commands'][0])

    def test_all_raw_observations_survive_list_compaction(self):
        rows = [{'path': str(i), 'coordinates': [[0, 1, 2]]*52, 'public_inlet_coordinate_match': i != 21,
                 'sha256': 'a'*64} for i in range(22)]
        payload = build_payload({'public_documents': [], 'selected_public_artifacts': []},
                                {'errors': ['bad_coordinates'], 'public_numerical_checks': {'raw_observations': rows}})
        view = payload['checks']['public_numerical_checks']['raw_observations']
        self.assertEqual(len(view), 22)
        self.assertFalse(view[-1]['public_inlet_coordinate_match'])
        self.assertEqual(view[-1]['coordinate_count'], 52)
        self.assertEqual(payload['checks']['errors'], ['bad_coordinates'])

    def test_log_observations_keep_startup_peak_and_do_not_infer_exit_success(self):
        text = 'cells: 365488\nMesh OK.\nnProcs : 16\nTime = 0.1\ncumulative = -4.4e-4\nTime = 180\ncumulative = -4.1e-4\nEnd\nFinalising parallel run\n'
        view = log_observation(text)
        self.assertAlmostEqual(view['max_abs_cumulative_continuity'], 4.4e-4)
        self.assertEqual(view['final_time'], 180)
        self.assertEqual(view['cell_count'], 365488)
        self.assertTrue(view['end_seen'])
        self.assertNotIn('solver_exit_code', view)
        self.assertIsNone(log_observation('partial log')['final_time'])
        self.assertFalse(log_observation('partial log')['end_seen'])

    def test_numeric_error_review_returns_complete_diagnosis(self):
        checks = {'errors': ['case/log:raw_continuity_missing_or_above_public_gate'],
                  'public_numerical_checks': {'acceptance': {'findings': []}, 'raw_observations': [{'limit': 1e-4, 'actual': 4.4e-4}]}}
        result = deterministic_feedback(checks)
        self.assertEqual(result['public_checks'], checks)
        self.assertEqual(result['agent_judge']['blocking_errors'], checks['errors'])
        self.assertEqual(result['agent_judge']['verdict'], 'repair_required')

    def test_one_hop_references_reject_escape_and_private_names(self):
        names = ['cases/diagnosis.json', '../outside.json', '/outside.json', 'x/reference.json',
                 'grading/result.json', 'C:/outside.json', 'x\\reference.json']
        refs = [{'path': name, 'sha256': 'a'*64} for name in names]
        self.assertEqual(referenced_json_paths([document('evidence_manifest.json', refs)]), ['cases/diagnosis.json'])

    def test_registered_diagnosis_delivered_with_actual_hash_and_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'workspace';canonical=root/'canonical';run=root/'run'
            for path in (workspace,canonical,run):path.mkdir()
            (canonical/'task_spec.json').write_text(json.dumps({'required_artifacts': ['evidence_manifest.json']}))
            (run/'config.json').write_text(json.dumps({'cpu_set': [3, 7]}))
            (workspace/'diagnosis.json').write_text(json.dumps({'decision': 'reject', 'solver_run_count': 0}))
            (workspace/'evidence_manifest.json').write_text(json.dumps({'diagnosis': {'path': 'diagnosis.json', 'sha256': 'f'*64}}))
            evidence=reviewer_evidence(workspace,canonical,run)
            diagnostic=next(x for x in evidence['selected_public_artifacts'] if x['path']=='diagnosis.json')
            self.assertNotEqual(diagnostic['sha256'], 'f'*64)
            self.assertEqual(json.loads(diagnostic['text'])['solver_run_count'], 0)
            self.assertEqual(evidence['runtime_allocation']['cpu_set'], [3,7])

    def test_original_receipt_survives_later_edit_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'workspace';canonical=root/'canonical';run=root/'run'
            for path in (workspace,canonical,run):path.mkdir()
            (canonical/'task_spec.json').write_text(json.dumps({'required_artifacts':['run_manifest.json']}))
            (workspace/'run_manifest.json').write_text(json.dumps({'runs':[{'execution_receipts':['harness_trace/003/execution.json']}]}))
            for i in range(1,20):
                p=run/'commands'/str(i).zfill(3);p.mkdir(parents=True)
                (p/'execution.json').write_text(json.dumps({'command':'simpleFoam' if i==3 else 'edit_document',
                     'exit_code':7 if i==3 else 0,'stdout':'Time = 400\nEnd\n','started_at_unix':i,'ended_at_unix':i+1}))
            evidence=reviewer_evidence(workspace,canonical,run)
            payload=build_payload(evidence,{'errors':[]})
            records=payload['evidence']['registered_execution_evidence']
            self.assertEqual(len(records),1)
            self.assertEqual(records[0]['path'],'harness_trace/003/execution.json')
            self.assertEqual(records[0]['exit_code'],7)
            self.assertEqual(records[0]['command_excerpt'],'simpleFoam')
            self.assertIn(records[0]['path'],payload['evidence_hashes'])

    def test_receipt_names_cannot_select_arbitrary_host_paths(self):
        values=['harness_trace/003/execution.json','../../grading/result.json','harness_trace/../execution.json','/etc/passwd']
        self.assertEqual(registered_execution_ids({'runs':[{'execution_receipts':values}]}),['003'])

    def test_large_manifest_notes_are_explicitly_budgeted(self):
        huge={'runs':[{'case_id':str(i),'notes':'x'*10000} for i in range(20)]}
        payload=build_payload({'public_documents':[], 'selected_public_artifacts':[document('run_manifest.json',huge)]}, {'errors':[]})
        self.assertIsNotNone(payload)
        self.assertLessEqual(len(json.dumps(payload,ensure_ascii=False).encode()),96*1024)
        self.assertEqual(len(payload['evidence']['selected_public_artifacts'][0]['content']['runs']),20)
        self.assertIn('notes',payload['evidence']['selected_public_artifacts'][0]['content']['runs'][0]['omitted_fields'])

    def test_over_budget_prose_yields_to_receipts_and_diagnostics(self):
        prose=[document('report-'+str(i)+'.json', {'notes':'x'*6000}) for i in range(38)]
        receipt=document('run_manifest.json', {'runs':[{'case_id':'x','exit_code':9,'started_at':'t1','ended_at':'t2'}]})
        diagnosis=document('cases/diagnosis.json', {'decision':'reject','solver_run_count':0})
        payload=build_payload({'public_documents':[], 'selected_public_artifacts':prose+[receipt,diagnosis]}, {'errors':[]})
        self.assertIsNotNone(payload)
        artifacts=payload['evidence']['selected_public_artifacts']
        self.assertTrue(any(x.get('budget_omitted_excerpt_bytes') or x.get('budget_omitted_prose_bytes') for x in artifacts))
        self.assertEqual(artifacts[-2]['content']['runs'][0]['exit_code'],9)
        self.assertEqual(artifacts[-1]['content']['solver_run_count'],0)


if __name__ == '__main__': unittest.main()
