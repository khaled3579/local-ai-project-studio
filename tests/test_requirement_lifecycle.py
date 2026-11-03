import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from project_core import Workspace
from project_requirements import Requirements
from project_requirement_lifecycle import classify
from project_acceptance import Acceptance
from project_recovery import Recovery


class RequirementLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws=Workspace(Path(self.temp.name));self.req=Requirements(self.ws)

    def add(self,text,identity='original'):
        self.req.record(text,'session',identity)
        return self.req.catalog()

    def reviewed(self,catalog,segments=None):
        items=[]
        for row in catalog['source_requirements']:
            if row['id'] in catalog['pending_obligation_reviews']:
                items.append({'requirement_id':row['id'],'reviewed_atomic':True,
                              'segments':segments or [{'start':0,'end':len(row['text']),'evidence_kind':'unit'}]})
        if items:self.req.review(expected_revision=catalog['revision'],items=items)
        return self.req.catalog()

    def test_five_message_classes_and_polite_action(self):
        for text,kind in [('أكمل','resume'),('ما حالة المشروع؟','question'),('أضف سلة','add'),
                          ('عدّل المتطلب','amend'),('ألغ المتطلب','cancel'),('هل يمكنك إضافة سلة؟','add'),
                          ('Can you support CSV?','add'),('هل يمكنك دعم CSV؟','add')]:
            self.assertEqual(classify(text),kind,text)

    def test_resume_question_and_context_rollover_preserve_identity(self):
        first=self.reviewed(self.add('Add a counter'))
        for index,text in enumerate(('أكمل','ما حالة المشروع؟','استئناف السلسلة')):
            self.req.record(text,'rotated-session',str(index))
        current=Requirements(Workspace(Path(self.temp.name))).catalog()
        self.assertEqual(first['revision'],current['revision'])
        self.assertEqual(first['requirements'],current['requirements'])
        self.assertEqual(len(current['message_classifications']),4)

    def test_compound_obligation_spans_and_reload_are_stable(self):
        catalog=self.add('Add cart and save cart')
        result=self.reviewed(catalog,[{'start':0,'end':8,'evidence_kind':'unit'},
                                     {'start':8,'end':22,'evidence_kind':'browser'}])
        self.assertEqual(len(result['requirements']),2)
        self.assertEqual([r['text'] for r in result['requirements']],['Add cart',' and save cart'])
        again=Requirements(Workspace(Path(self.temp.name))).catalog()
        self.assertEqual(result['revision'],again['revision'])
        old_ids=[r['id'] for r in again['requirements']]
        added=self.add('Add search','addition')
        self.assertEqual(old_ids,[r['id'] for r in added['requirements'][:2]])

    def test_review_cannot_omit_overlap_or_downgrade_behavior(self):
        original=self.add('Add counter')
        for segments in [[{'start':0,'end':3,'evidence_kind':'unit'}],
                         [{'start':0,'end':11,'evidence_kind':'unit'},{'start':1,'end':2,'evidence_kind':'unit'}],
                         [{'start':0,'end':11,'evidence_kind':'host'}]]:
            with self.assertRaises(ValueError):self.reviewed(original,segments)
        self.assertFalse((self.ws.meta/'requirement-reviews.json').exists())

    def test_review_compare_and_swap_and_history(self):
        original=self.add('Add counter')
        first=self.reviewed(original)
        with self.assertRaises(ValueError):self.reviewed(original)
        row=first['source_requirements'][0]
        self.req.review(expected_revision=first['revision'],items=[{'requirement_id':row['id'],'reviewed_atomic':True,
            'segments':[{'start':0,'end':len(row['text']),'evidence_kind':'browser'}]}])
        ledger=json.loads((self.ws.meta/'requirement-reviews.json').read_text())
        self.assertEqual(len(ledger['events']),1)
        self.assertEqual(ledger['events'][0]['previous']['segments'][0]['evidence_kind'],'unit')

    def test_explicit_amendment_preserves_logical_identity_and_retires_old_proof_id(self):
        first=self.reviewed(self.add('Add a counter'))
        old=first['requirements'][0]
        changed=self.add('Amend '+old['id']+' to add two counters','amendment')
        self.assertEqual(len(changed['requirements']),1)
        new=changed['requirements'][0]
        self.assertEqual(new['logical_id'],old['logical_id'])
        self.assertNotEqual(new['id'],old['id'])
        self.assertEqual(new['predecessors'],[old['id']])
        self.assertEqual(changed['retired_requirements'][0]['id'],old['id'])
        self.assertEqual(changed['relations'][0]['kind'],'amend')
        self.assertIsNotNone(changed['relations'][0]['time'])

    def test_explicit_cancellation_and_ambiguous_cancellation(self):
        first=self.reviewed(self.add('Add a counter'))
        unresolved=self.add('ألغ ميزة غير محددة','unclear')
        self.assertEqual(len(unresolved['requirements']),1)
        self.assertTrue(any(x.startswith('unclear:') and ('explicit predecessor' in x or 'unresolved scope' in x) for x in unresolved['review_issues']))
        # An ambiguous historical request remains visible even after another exact cancellation.
        cancelled=self.add('Cancel requirement '+first['requirements'][0]['id'],'cancel')
        self.assertEqual(cancelled['requirements'],[])
        self.assertTrue(cancelled['review_issues'])

    def test_unknown_target_does_not_remove_previous_requirement(self):
        self.reviewed(self.add('Add a counter'))
        unknown=self.add('Cancel requirement R_'+'a'*24+'_1','cancel')
        self.assertEqual(len(unknown['requirements']),1)
        self.assertTrue(any('unknown' in x for x in unknown['review_issues']))

    def test_cancellation_with_unresolved_exception_keeps_requirement(self):
        first=self.reviewed(self.add('Add a counter'))
        result=self.add('Cancel requirement '+first['requirements'][0]['id']+' except mobile','cancel')
        self.assertEqual(len(result['requirements']),1)
        self.assertTrue(any('unresolved scope' in issue for issue in result['review_issues']))

    def test_missing_or_unreviewed_mandatory_obligation_blocks_delivery(self):
        catalog=self.add('Add counter')
        blocked=Acceptance(self.ws,'أكمل',catalog=catalog).evaluate(None)
        self.assertFalse(blocked['passed']);self.assertIn('review',blocked['error'])
        reviewed=self.reviewed(catalog)
        blocked=Acceptance(self.ws,'أكمل',catalog=reviewed).evaluate(None)
        self.assertFalse(blocked['passed']);self.assertTrue(blocked['missing_plan'])

    def test_checkpoint_preserves_catalog_and_review_cursor(self):
        catalog=self.reviewed(self.add('Add counter'))
        state={'run_id':'r','catalog_revision':catalog['revision'],'requirement_ids':[r['id'] for r in catalog['requirements']]}
        Recovery(self.ws).save(state)
        self.assertEqual(Recovery(self.ws).load('r'),state)
        self.assertEqual(Requirements(self.ws).catalog()['revision'],state['catalog_revision'])

    def test_incompatible_evidence_kind_blocks_even_a_passing_named_test(self):
        catalog=self.reviewed(self.add('Render counter'),[{'start':0,'end':14,'evidence_kind':'browser'}])
        acceptance=Acceptance(self.ws,'continue',catalog=catalog)
        key=catalog['requirements'][0]['id']
        acceptance.invoke(items=[{'requirement_ids':[key],'criterion':'Render counter', 'tests':['python:test_fixture.Case.test_counter']}])
        report={'checks':[{'passed':True,'validation_run_id':'run','executed':1,
                           'successful_tests':['python:test_fixture.Case.test_counter']}]}
        with patch.object(acceptance,'fresh',return_value=True):
            result=acceptance.evaluate(report)
        self.assertFalse(result['passed'])
        self.assertIn('review:evidence-kind:'+key+':browser',result['items'][0]['missing_tests'])

    def test_brief_revision_preserves_source_lineage(self):
        self.req.edit('Add counter',self.req.revision(self.req.load()))
        first=self.reviewed(self.req.catalog())
        self.req.edit('Add search',self.req.revision(self.req.load()))
        changed=self.req.catalog()
        brief=next(r for r in changed['sources'] if r['id']=='brief')
        self.assertTrue(brief['previous_revision']);self.assertTrue(brief['recorded_at'])
        self.assertNotEqual(first['requirements'][0]['id'],changed['requirements'][0]['id'])

    def test_structural_heading_context_keeps_source_without_fake_test_obligation(self):
        catalog=self.add('## Overview\nAdd counter')
        heading=catalog['source_requirements'][0]
        self.req.review(expected_revision=catalog['revision'],items=[{'requirement_id':heading['id'],'reviewed_atomic':True,
            'segments':[{'start':0,'end':len(heading['text']),'evidence_kind':'context',
                         'context_reason':'Structural heading only; the next block holds the actual counter obligation.'}]}])
        updated=self.req.catalog()
        self.assertEqual(len(updated['requirements']),1)
        self.assertEqual(updated['requirements'][0]['text'],'Add counter')
        self.assertEqual(updated['reviewed_structural_context'][0]['text'],'## Overview')
        block=updated['requirements'][0]
        with self.assertRaises(ValueError):
            self.req.review(expected_revision=updated['revision'],items=[{'requirement_id':block['id'],'reviewed_atomic':True,
                'segments':[{'start':0,'end':len(block['text']),'evidence_kind':'context','context_reason':'Pretending this directive is just context is forbidden.'}]}])

    def attached(self):
        identity='a'*32;folder=self.ws.meta/'attachments'/identity;folder.mkdir(parents=True)
        raw=b'# Specification\nAdd a counter';sha=hashlib.sha256(raw).hexdigest()
        (folder/'original').write_bytes(raw);(folder/'text.txt').write_bytes(raw)
        (folder/'metadata.json').write_text(json.dumps({'sha256':sha}))
        self.req.record('Implement the attached specification','session','original',
            attachments=[{'id':identity,'kind':'text','name':'requirements.md','sha256':sha}])
        return folder,self.req.catalog()

    def test_missing_attachment_keeps_pinned_obligations_and_blocks_acceptance(self):
        folder,first=self.attached();(folder/'original').unlink()
        current=self.req.catalog()
        self.assertEqual([r['id'] for r in first['requirements']],[r['id'] for r in current['requirements']])
        self.assertTrue(any('attachment:' in issue for issue in current['review_issues']))
        self.assertFalse(Acceptance(self.ws,'continue',catalog=current).evaluate(None)['passed'])

    def test_changed_extracted_specification_does_not_replace_pinned_text(self):
        folder,first=self.attached();(folder/'text.txt').write_text('Drop the counter')
        current=self.req.catalog()
        self.assertEqual([r['text'] for r in first['requirements']],[r['text'] for r in current['requirements']])
        self.assertTrue(any('Pinned specification text changed' in issue for issue in current['review_issues']))

    def test_amendment_does_not_inherit_old_acceptance_links(self):
        first=self.reviewed(self.add('Add counter'))
        old=first['requirements'][0]
        acceptance=Acceptance(self.ws,'original',catalog=first)
        acceptance.invoke(items=[{'requirement_ids':[old['id']],'criterion':'Add counter',
                                  'tests':['python:test_fixture.Counter.test_counter']}])
        self.assertIsNotNone(acceptance.load())
        changed=self.reviewed(self.add('Amend '+old['id']+' to render counter','amendment'))
        current=Acceptance(self.ws,'continue',catalog=changed)
        self.assertIsNone(current.load())
        self.assertFalse(current.evaluate(None)['passed'])

    def test_changed_source_is_flagged_and_old_history_kept(self):
        first=self.reviewed(self.add('Add counter'))
        value=self.req.load();value['requests'][0]['text']='Add search';self.req.save(value)
        updated=self.req.catalog()
        self.assertTrue(any('original request changed' in x for x in updated['review_issues']))
        self.assertNotEqual(first['requirements'][0]['id'],updated['requirements'][0]['id'])
        self.assertTrue(list((self.ws.meta/'requirements-catalog-history').glob('*.json')))


class WholeSourceReview(unittest.TestCase):
    setUp = RequirementLifecycle.setUp
    add = RequirementLifecycle.add
    def test_whole_source_preserves_exact_unicode_and_revision(self):
        catalog=self.add('Add search \u0628\u0627\u0644\u0639\u0631\u0628\u064a\u0629 \U0001f50e')
        row=catalog['source_requirements'][0]
        self.assertEqual(row['source_length'],len(row['text']))
        self.req.review(expected_revision=catalog['revision'],items=[{
            'requirement_id':row['id'],'reviewed_atomic':True,
            'whole_source':True,'evidence_kind':'unit'}])
        current=self.req.catalog()
        self.assertFalse(current['pending_obligation_reviews'])
        self.assertEqual(current['requirements'][0]['text'],row['text'])
        self.assertEqual(current['revision'],Requirements(self.ws).catalog()['revision'])

    def test_whole_source_cannot_bypass_review_or_evidence_or_mix_modes(self):
        for extra in ({'reviewed_atomic':False},{'evidence_kind':'host'},
                      {'segments':[{'start':0,'end':3,'evidence_kind':'unit'}]},
                      {'evidence_kind':'context','context_reason':'not a structural heading at all'}):
            catalog=self.add('Add search')
            item={'requirement_id':catalog['source_requirements'][0]['id'],
                  'reviewed_atomic':True,'whole_source':True,'evidence_kind':'unit',**extra}
            with self.assertRaises(ValueError):self.req.review(expected_revision=catalog['revision'],items=[item])
            self.assertTrue(self.req.catalog()['pending_obligation_reviews'])

    def test_omitted_span_reports_exact_gap_and_metadata_repair(self):
        catalog=self.add('Add search');row=catalog['source_requirements'][0]
        with self.assertRaises(ValueError) as error:
            self.req.review(expected_revision=catalog['revision'],items=[{
                'requirement_id':row['id'],'reviewed_atomic':True,
                'segments':[{'start':0,'end':4,'evidence_kind':'unit'}]}])
        diagnostic=str(error.exception)
        for value in ('source_length','search','review_project_requirements','whole_source',row['id']):
            self.assertIn(value,diagnostic)
