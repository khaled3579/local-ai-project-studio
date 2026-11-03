import json,tempfile,unittest
from pathlib import Path
from project_core import Workspace
from project_requirements import Requirements

class ReviewRecoveryGuidance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws=Workspace(Path(self.temp.name));self.req=Requirements(self.ws)

    def test_bad_row_is_identified_without_saving_other_rows_or_dropping_behavior(self):
        self.req.record('Add total.\nPreserve existing calculation semantics.','s','m')
        cat=self.req.catalog();blocks=cat['source_requirements']
        with self.assertRaises(ValueError) as caught:
            self.req.review(expected_revision=cat['revision'],items=[
                {'requirement_id':b['id'],'reviewed_atomic':True,'whole_source':True,
                 'evidence_kind':'behavior' if n==0 else 'context'} for n,b in enumerate(blocks)])
        repair=caught.exception.review_repair
        self.assertEqual(repair['requirement_id'],blocks[1]['id'])
        self.assertEqual(repair['source_text'],blocks[1]['text'])
        self.assertEqual(repair['source_length'],len(blocks[1]['text']))
        self.assertEqual(repair['supported_host_evidence'],[])
        self.assertFalse(repair['batch_saved'])
        self.assertEqual(self.req.catalog()['pending_obligation_reviews'],cat['pending_obligation_reviews'])
        self.assertFalse((self.ws.meta/'requirement-reviews.json').exists())

    def test_exact_text_segments_preserve_unicode_and_stable_ids_across_reload(self):
        self.req.record('أضف سلة واحفظ السلة','s','m');cat=self.req.catalog();block=cat['source_requirements'][0]
        result=self.req.review(expected_revision=cat['revision'],items=[{'requirement_id':block['id'],
            'reviewed_atomic':True,'segments':[{'text':'أضف سلة','evidence_kind':'unit'},
                                             {'text':'واحفظ السلة','evidence_kind':'browser'}]}])
        self.assertEqual([r['text'] for r in result['requirements']],['أضف سلة','واحفظ السلة'])
        self.assertEqual([r['id'] for r in self.req.catalog()['requirements']],
                         [r['id'] for r in Requirements(Workspace(self.ws.root)).catalog()['requirements']])

    def test_ambiguous_missing_mixed_and_omitted_fragments_remain_rejected(self):
        self.req.record('abc abc','s','m');cat=self.req.catalog();key=cat['source_requirements'][0]['id']
        for segments in ([{'text':'abc','evidence_kind':'unit'}],
                         [{'text':'missing','evidence_kind':'unit'}],
                         [{'text':'abc','occurrence':1,'start':0,'end':3,'evidence_kind':'unit'}],
                         [{'text':'abc','occurrence':1,'evidence_kind':'unit'}]):
            with self.assertRaises(ValueError):
                self.req.review(expected_revision=cat['revision'],items=[{'requirement_id':key,'reviewed_atomic':True,'segments':segments}])
        result=self.req.review(expected_revision=cat['revision'],items=[{'requirement_id':key,'reviewed_atomic':True,
            'segments':[{'text':'abc','occurrence':1,'evidence_kind':'unit'},
                        {'text':'abc','occurrence':2,'evidence_kind':'unit'}]}])
        self.assertEqual(len(result['requirements']),2)
