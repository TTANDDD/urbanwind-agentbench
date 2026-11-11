import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urbanwind_harness.delivery_closure import public_view, prepare_delivery, sampling_preflight, safe_path


class DeliveryClosureTests(unittest.TestCase):
    def setup_case(self, root, extra=None):
        c, w = root/'canonical', root/'workspace'
        c.mkdir(); w.mkdir()
        (c/'reporting_contract.json').write_text(json.dumps({'task_id':'public-demo',
            'simulation_result_contract':{'path':'answer.json', **(extra or {})}}))
        (c/'task_spec.json').write_text(json.dumps({'task_id':'public-demo',
            'required_artifacts':['answer.json']}))
        return c,w

    def test_missing_identity_count_and_false_gate_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); c,w=self.setup_case(p)
            obj={'cases':[{'sample_count':3001.0,'mean_Ux_m_s':[1.1,2.2],
                           'all_run_gates_pass':False}]}
            original=json.dumps(obj).encode(); (w/'answer.json').write_bytes(original)
            result=prepare_delivery(w,c,p/'receipt')
            actual=json.loads((w/'answer.json').read_bytes())
            self.assertEqual(actual['task_id'],'public-demo')
            self.assertIs(type(actual['cases'][0]['sample_count']),int)
            self.assertFalse(actual['cases'][0]['all_run_gates_pass'])
            self.assertEqual(actual['cases'][0]['mean_Ux_m_s'],[1.1,2.2])
            self.assertEqual(Path(result['writes'][0]['backup']).read_bytes(),original)
            second=prepare_delivery(w,c,p/'second')
            self.assertEqual(second['writes'],[])
            self.assertEqual(second['changes'],[])

    def test_missing_file_not_synthesized(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,w=self.setup_case(p)
            result=prepare_delivery(w,c,p/'receipt')
            self.assertTrue(result['errors'])
            self.assertFalse((w/'answer.json').exists())

    def test_conflicting_id_fractional_bool_and_nonfinite_count_not_repaired(self):
        for count in [2.25,True,-1]:
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);c,w=self.setup_case(p)
                (w/'answer.json').write_text(json.dumps({'task_id':'wrong','sample_count':count,'gate':False}))
                result=prepare_delivery(w,c,p/'receipt')
                actual=json.loads((w/'answer.json').read_bytes())
                self.assertEqual(actual['task_id'],'wrong')
                self.assertEqual(actual['sample_count'],count)
                self.assertTrue(result['errors'])

    def test_nonfinite_document_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,w=self.setup_case(p)
            raw=b'{"value": NaN}'
            (w/'answer.json').write_bytes(raw)
            result=prepare_delivery(w,c,p/'receipt')
            self.assertTrue(result['errors'])
            self.assertEqual((w/'answer.json').read_bytes(),raw)

    def test_only_matching_provenance_is_updated(self):
        for stale in [False,True]:
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);c,w=self.setup_case(p)
                (c/'submission_format.json').write_text(json.dumps({'documents':{'manifest.json':{}}}))
                raw=b'{"sample_count": 8.0}'
                (w/'answer.json').write_bytes(raw)
                old=hashlib.sha256(raw).hexdigest()
                stated='a'*64 if stale else old
                (w/'manifest.json').write_text(json.dumps({'ref':{'path':'answer.json','sha256':stated}}))
                result=prepare_delivery(w,c,p/'receipt')
                updated=json.loads((w/'manifest.json').read_bytes())['ref']['sha256']
                if stale:
                    self.assertEqual(updated,stated)
                    self.assertTrue(any('stale_hash' in e for e in result['errors']))
                else:
                    self.assertEqual(updated,hashlib.sha256((w/'answer.json').read_bytes()).hexdigest())

    def test_public_paths_do_not_escape_or_modify_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,w=self.setup_case(p)
            (c/'submission_format.json').write_text(json.dumps({'documents':{'../answer.json':{}}}))
            with self.assertRaises(ValueError):public_view(c)
            for name in ['/etc/passwd','../private.json','C:/private.json']:
                with self.assertRaises(ValueError):safe_path(w,name)

    def test_cyclic_references_reject_write_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,w=self.setup_case(p)
            (c/'submission_format.json').write_text(json.dumps({'documents':{'b.json':{}}}))
            for name,other in [('answer.json','b.json'),('b.json','answer.json')]:
                (w/name).write_text(json.dumps({'ref':{'path':other,'sha256':'a'*64}}))
            before={f.name:f.read_bytes() for f in w.iterdir()}
            result=prepare_delivery(w,c,p/'receipt')
            self.assertEqual(result['writes'],[])
            self.assertTrue(any('cyclic' in e for e in result['errors']))
            self.assertEqual(before,{f.name:f.read_bytes() for f in w.iterdir()})

    def test_sampling_preflight_reads_real_dictionary_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,w=self.setup_case(p)
            system=w/'case/system';system.mkdir(parents=True)
            text='application pimpleFoam; startTime 0; endTime 180; deltaT 0.015; functions { response { type probes; writeControl runTime; writeInterval 0.2; } }'
            (system/'controlDict').write_text(text)
            result=sampling_preflight(w,c,['case'])
            self.assertEqual(result['errors'],[])
            self.assertEqual(result['cases'][0]['sampling'][0]['estimated_intervals'],900)
            self.assertEqual((system/'controlDict').read_text(),text)
            self.assertEqual(result['solver_calls'],0)


if __name__=='__main__':unittest.main()
