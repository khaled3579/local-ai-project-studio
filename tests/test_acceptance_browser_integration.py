"""Scripted protocol regression with real Node execution and real Chromium.

Model choices/usage are synthetic here; this is not a token or model-quality benchmark.
"""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

import project_studio
from project_toolhub import hub
from test_inference_metrics import Response


class BrowserAcceptanceIntegration(unittest.TestCase):
    def run_protocol(self, protocol):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            root = folder/'project'; (root/'tests').mkdir(parents=True)
            (root/'index.html').write_text('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Counter</title></head>'
                '<body><button id="increment" type="button">Increment</button><span id="count">0</span>'
                '<script src="app.js"></script></body></html>', encoding='utf-8')
            source = "const output=document.getElementById('count');document.getElementById('increment').onclick=()=>{output.textContent=String(Number(output.textContent)+1);};"
            (root/'app.js').write_text(source, encoding='utf-8')
            (root/'tests/counter.test.cjs').write_text(
                "const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');"
                "test('counter increments',()=>{const nodes={count:{textContent:'0'},increment:{}};"
                "vm.runInNewContext(fs.readFileSync('app.js','utf8'),{document:{getElementById:id=>nodes[id]}});"
                "nodes.increment.onclick();assert.equal(nodes.count.textContent,'1');});", encoding='utf-8')
            studio = project_studio.Studio(settings_path=folder/'settings.json')
            studio.select(str(root))
            (studio.workspace.meta/'repair-settings.json').write_text('{"autonomous_repair":false}')
            prompt = 'Verify the counter with Node tests and an actual browser click.'
            studio.job = {'id':'browser-integration', 'status':'running', 'mode':'project', 'original_prompt':prompt,
                          'prompt':prompt, 'started':1, 'verified':False}
            studio.repair_run = {'id':'browser-run', 'status':'running', 'attempt_index':1}
            actions = [('validate_project', {}), ('browser_project', {'action':'open','entry':'index.html'}),
                       ('browser_project', {'action':'click','selector':'#increment'}),
                       ('browser_project', {'action':'assert','selector':'#count','expected':'1'}),
                       ('acceptance_plan', {'items':[{'requirement_ids':['R1'], 'criterion':'Counter increments once with real browser confirmation',
                                                    'tests':['node:counter increments','@browser']}]}), (None,{})]
            sent = []

            def post(url, **kwargs):
                if url.endswith('/apply-template'):
                    result=Mock();result.json.return_value={'prompt':'template'};return result
                if url.endswith('/tokenize'):
                    result=Mock();result.json.return_value={'tokens':[1]};return result
                body=kwargs['json'];sent.append(body)
                name, arguments=actions[len(sent)-1]
                arguments=json.loads(json.dumps(arguments))
                if name=='acceptance_plan':
                    text='\n'.join(str(m.get('content','')) for m in body['messages'])
                    match=re.search(r'"acceptance_evidence_id"\s*:\s*"(browser:[a-f0-9]+)"',text)
                    self.assertIsNotNone(match, text[-4000:])
                    arguments['items'][0]['tests'][1]=match[1]
                    self.assertIn('acceptance_guidance', text)
                if protocol=='json':
                    delta={'content':json.dumps({'tool':name,'arguments':arguments} if name else {'tool':'final','message':'تم التحقق من `index.html`.'})}
                    finish='stop'
                elif name:
                    delta={'tool_calls':[{'index':0,'id':f'c{len(sent)}','function':{'name':name,'arguments':json.dumps(arguments)}}]};finish='tool_calls'
                else:
                    delta={'content':'تم التحقق من `index.html`.'};finish='stop'
                response=Response([{'choices':[{'delta':delta,'finish_reason':finish}]},
                                   {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':20}},'[DONE]'])
                response.status_code=200;return response

            manager=Mock();manager.json.return_value={'actual_context':32768,'active':{'model':'gemma4-unit' if protocol=='json' else 'qwen-unit'}}
            try:
                with patch.object(project_studio.requests,'post',side_effect=post), patch.object(project_studio.requests,'get',return_value=manager):
                    studio.agent_attempt(prompt)
                self.assertEqual(studio.job['status'],'complete',studio.job)
                self.assertTrue(studio.job['verified'])
                evidence=json.loads((studio.workspace.meta/'delivery-evidence.json').read_text(encoding='utf-8'))
                row=evidence['requirements_coverage']['items'][0]
                self.assertTrue(evidence['validated'])
                self.assertTrue(row['passed'])
                self.assertEqual(row['evidence']['node:counter increments']['name'],'web_node_tests')
                browser=next(value for key,value in row['evidence'].items() if key.startswith('browser:'))
                self.assertTrue(browser['assertion']['passed'])
                self.assertEqual(len(sent),6)
            finally:
                hub(studio.workspace).browser.close()

    def test_native_real_node_and_browser_evidence_deliver(self):
        self.run_protocol('native')

    def test_json_real_node_and_browser_evidence_deliver(self):
        self.run_protocol('json')
