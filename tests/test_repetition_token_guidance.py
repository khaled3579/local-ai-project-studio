import json,tempfile,time,unittest
from pathlib import Path
from project_core import Workspace
from project_validation_guidance import passing_context
from project_metrics import stream_measurement,record_generation

class GuidanceAndTokenCoverage(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.ws=Workspace(Path(temp.name));self.ws.write('app.py','x=1\n')
    def test_executed_original_ids_and_exact_original_review_source_are_visible(self):
        report={'success':True,'source_unchanged_during_validation':True,'fingerprint':self.ws.fingerprint(force=True),
            'checks':[{'passed':True,'executed':1,'validation_run_id':'real','successful_tests':['python:test_app.T.test_x']},
                      {'passed':True,'successful_tests':['python:fake.T.test_fake']}]}
        catalog={'revision':'rev','pending_obligation_reviews':['R1'],
                 'source_requirements':[{'id':'R1','text':'Preserve the existing reference comments.'}]}
        result=passing_context(self.ws,report,catalog)
        self.assertIn('python:test_app.T.test_x',result);self.assertNotIn('python:fake',result)
        self.assertIn('host:source_comments_preserved',result);self.assertIn('catalog_revision',result)
        self.assertEqual(report['checks'][0]['validation_run_id'],'real')
        report['in_progress']=True;self.assertEqual(passing_context(self.ws,report,catalog),'')
        report.pop('in_progress');self.ws.write('app.py','x=2\n',self.ws.read('app.py')['sha256'])
        self.assertEqual(passing_context(self.ws,report,catalog),'')
    def test_large_catalog_is_bounded_without_silently_reviewing_truncated_text(self):
        catalog={'revision':'rev','pending_obligation_reviews':[f'R{i}' for i in range(30)],
                 'source_requirements':[{'id':f'R{i}','text':'Long source '+('word '*400)} for i in range(30)]}
        report={'success':True,'source_unchanged_during_validation':True,'fingerprint':self.ws.fingerprint(force=True),'checks':[]}
        result=passing_context(self.ws,report,catalog)
        self.assertLess(len(result),10500);self.assertIn('"pending_source_reviews": 30',result)
        self.assertIn('"text_truncated": true',result);self.assertIn('read its full original text',result)
    def test_unknown_tokens_are_not_filled_with_estimates_or_timing_counters(self):
        row=stream_measurement({'prompt_tokens':12.5},{'prompt_n':100,'predicted_n':50},time.monotonic(),None,'incomplete')
        self.assertIsNone(row['prompt_tokens']);self.assertIsNone(row['completion_tokens'])
        self.assertFalse(row['token_accounting_complete']);self.assertEqual(row['token_usage_source'],'unavailable')
        zero=stream_measurement({'prompt_tokens':0,'completion_tokens':0},{},time.monotonic(),None,'complete')
        self.assertTrue(zero['token_accounting_complete'])
    def test_partial_usage_retains_lower_bound_and_missing_coverage_after_json_resume(self):
        job={'id':'j'}
        first=stream_measurement({'prompt_tokens':20},{},time.monotonic(),None,'incomplete')
        record_generation(job,self.ws,first,'one',step=1,protocol='native',estimated_input=999)
        job=json.loads(json.dumps(job))
        full=stream_measurement({'prompt_tokens':30,'completion_tokens':5},{},time.monotonic(),None,'complete')
        record_generation(job,self.ws,full,'two',step=2,protocol='native',estimated_input=999)
        usage=job['token_usage'];self.assertEqual(usage['requests'],2);self.assertEqual(usage['measured_requests'],1)
        self.assertEqual(usage['prompt_tokens'],50);self.assertEqual(usage['completion_tokens'],5)
        self.assertEqual(usage['missing_usage_requests'],1);self.assertFalse(usage['usage_complete'])
        self.assertTrue(usage['observed_tokens_are_lower_bound'])

    def test_usage_frame_without_terminal_marker_stays_a_lower_bound(self):
        from project_stream import consume
        from test_inference_metrics import Response
        rows=[]
        with self.assertRaises(ValueError):
            consume(Response([{'usage':{'prompt_tokens':20,'completion_tokens':4},'choices':[]}]),
                    lambda:False,lambda *a:None,measure=rows.append)
        self.assertEqual(rows[0]['prompt_tokens'],20)
        self.assertFalse(rows[0]['token_accounting_complete'])
        job={'id':'partial'}
        record_generation(job,self.ws,rows[0],'partial',step=1,protocol='native',estimated_input=999)
        self.assertEqual(job['token_usage']['measured_requests'],0)
        self.assertTrue(job['token_usage']['observed_tokens_are_lower_bound'])
