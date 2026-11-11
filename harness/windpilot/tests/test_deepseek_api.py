import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from urbanwind_harness.api import APIError, DeepSeekClient


class DeepSeekMigrationTests(unittest.TestCase):
    def test_credentials_cannot_be_forwarded_to_another_origin_or_poe_relay(self):
        for settings in [{'base_url':'https://api.poe.com/v1'},
                         {'base_url':'http://api.deepseek.com'},
                         {'base_url':'https://api.deepseek.com.example.invalid'},
                         {'relay_url':'http://127.0.0.1:18766/v1'}]:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                DeepSeekClient(Path('unused.key'), **settings)

    def test_native_tool_and_reasoning_history_preserved_without_output_or_sampling_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            key=Path(tmp)/'fixture.key';key.write_text('synthetic-test-token')
            captured=[]
            message={'role':'assistant','content':'','reasoning_content':'fixture reasoning',
                'tool_calls':[{'id':'fixture-call','type':'function',
                    'function':{'name':'inspect','arguments':'{}'}}]}
            data={'model':'deepseek-flash','choices':[{'message':message,'finish_reason':'tool_calls'}],
                'usage':{'prompt_tokens':1,'completion_tokens':1}}
            def response(request,**kwargs):
                captured.append((request.full_url,json.loads(request.data),dict(request.header_items())))
                return io.StringIO(json.dumps(data))
            with patch('urllib.request.urlopen',side_effect=response):
                client=DeepSeekClient(key)
                first=client.complete([{'role':'user','content':'fixture'}],tools=[])
                history=[first['message'],{'role':'tool','tool_call_id':'fixture-call','content':'{}'}]
                client.complete(history,tools=[])
            self.assertEqual(first['message'],message)
            self.assertEqual(captured[1][1]['messages'],history)
            for url,body,headers in captured:
                self.assertEqual(url,'https://api.deepseek.com/chat/completions')
                self.assertEqual(body['model'],'deepseek-flash')
                self.assertFalse(body['parallel_tool_calls'])
                self.assertEqual(headers['Authorization'],'Bearer synthetic-test-token')
                self.assertTrue({'max_tokens','max_completion_tokens','temperature','top_p','seed','thinking','reasoning_effort'}.isdisjoint(body))

    def test_quota_error_is_not_retried_or_converted_to_a_zero_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            key=Path(tmp)/'fixture.key';key.write_text('synthetic-test-token')
            error=urllib.error.HTTPError('https://api.deepseek.com/chat/completions',402,
                'fixture insufficient balance',{},io.BytesIO(b'{"error":"insufficient_balance"}'))
            with patch('urllib.request.urlopen',side_effect=error) as call,patch('time.sleep') as sleep:
                with self.assertRaises(APIError) as raised:
                    DeepSeekClient(key).complete([])
            self.assertEqual(raised.exception.status,402)
            self.assertEqual(call.call_count,1)
            sleep.assert_not_called()


if __name__=='__main__':unittest.main()
