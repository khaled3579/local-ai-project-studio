import json
from pathlib import Path
import tempfile
import unittest

from project_acceptance import Acceptance, host_evidence, required_host_evidence
from project_core import Workspace
from project_quality import delivery_evidence
from project_validation import VERSION


class AcceptanceTests(unittest.TestCase):
    def test_stale_browser_save_returns_targeted_next_step_before_final_attempt(self):
        self.ws._save_validation_report(self.report())
        plan=Acceptance(self.ws,'Verify counter.',evidence={'browser:old':{'passed':False}})
        saved=plan.invoke([{'requirement_ids':['R1'],'criterion':'Counter increments in browser',
                            'tests':['python:test_app.Tests.test_add','browser:old']}])
        self.assertTrue(saved['saved'])
        self.assertIn('stale receipt is NOT a failed UI assertion',saved['next_step'])
        self.assertIn('prerequisite fills and clicks',saved['next_step'])
        self.assertNotIn('python:test_app.Tests.test_add',saved['next_step'])

    def test_root_and_dunder_tests_are_protected_without_a_tests_directory(self):
        paths = ['cart.test.js','cart.spec.ts','logic.test.cjs','logic.spec.mjs','test_app.py','__tests__/nested.js']
        for name in paths:
            path = self.ws.root/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('original test')
        original = self.plan.capture_tests()
        self.assertTrue(all(name in original for name in paths))
        (self.ws.root/'cart.test.js').write_text('changed test')
        proof = host_evidence(self.ws,original,{})
        self.assertFalse(proof['host:original_tests_unchanged']['passed'])
        self.assertEqual(proof['host:original_tests_unchanged']['changed'],['cart.test.js'])

    def test_unrelated_sentences_cannot_create_preservation_constraint(self):
        self.assertEqual(required_host_evidence('Preserve caller inputs. Add tests.'), [])
        self.assertEqual(required_host_evidence('Add tests that verify inputs unchanged.'), [])
        self.assertEqual(required_host_evidence('Do not edit tests or README.txt; keep exact bytes.'),
                         ['host:original_tests_unchanged','host:original_readmes_unchanged'])
    def test_preservation_constraint_requires_matching_host_links(self):
        self.ws._save_validation_report(self.report())
        plan = Acceptance(self.ws, 'Do not edit existing test files or README.txt; their exact bytes must stay unchanged.',
                          evidence={'host:original_tests_unchanged':{'passed':True}, 'host:original_readmes_unchanged':{'passed':True}})
        rows = [{'requirement_ids':[r['id'] for r in plan.requirements], 'criterion':'Original files retain identical bytes',
                 'tests':['python:test_app.Tests.test_add']}]
        response = plan.invoke(rows)
        self.assertTrue(plan.evaluate(self.report())['passed'])
        self.assertEqual(response['missing_evidence'], [])
        self.assertIn('host:original_tests_unchanged',plan.load()['items'][0]['tests'])
        self.assertIn('host:original_readmes_unchanged',plan.load()['items'][0]['tests'])
        rows[0]['tests'] += ['host:original_tests_unchanged','host:original_readmes_unchanged']
        plan.invoke(rows)
        self.assertTrue(plan.evaluate(self.report())['passed'])

    def test_failed_host_constraint_cannot_be_hidden_by_passing_unit_test(self):
        plan = Acceptance(self.ws, 'Preserve existing tests.', evidence={'host:original_tests_unchanged':{'passed':False}})
        plan.invoke([{'requirement_ids':['R1'],'criterion':'Existing tests unchanged',
                      'tests':['python:test_app.Tests.test_add','host:original_tests_unchanged']}])
        self.assertFalse(plan.evaluate(self.report())['passed'])

    def test_repair_message_contains_only_unmet_checks_not_passed_receipts(self):
        plan=Acceptance(self.ws,'Add values. Answer in Arabic.', evidence={'host:arabic_response':{'passed':False}})
        plan.invoke([{'requirement_ids':['R1'],'criterion':'Adds signed values','tests':['python:test_app.Tests.test_add']},
                     {'requirement_ids':['R2'],'criterion':'Arabic response provided','tests':['host:arabic_response']}])
        message=plan.repair_message(self.report())
        self.assertIn('host:arabic_response',message)
        self.assertNotIn('python:test_app.Tests.test_add',message)
        self.assertNotIn('Adds signed values',message)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(Path(self.temp.name))
        (self.ws.root / 'app.py').write_text('answer = 42\n')
        self.plan = Acceptance(self.ws, 'Add values. Preserve inputs.', revision='r1')
        self.items = [dict(quote='Add values.', criterion='Signed values sum correctly', tests=['python:test_app.Tests.test_add']),
                      dict(quote='Preserve inputs.', criterion='Caller input is unchanged', tests=['python:test_app.Tests.test_immutable'])]

    def report(self):
        return dict(success=True, fingerprint=self.ws.fingerprint(force=True), validator_version=VERSION,
                    source_unchanged_during_validation=True, checks=[dict(name='python_unit_tests', passed=True,
                    validation_run_id='execution', executed=2, successful_tests=[t for r in self.items for t in r['tests']])])

    def evidence(self, report=None):
        return delivery_evidence(self.ws, self.plan.source, report or self.report(), acceptance=self.plan)

    def test_no_plan_blocks_passing_suite(self):
        self.assertFalse(self.evidence()['validated'])

    def id_items(self):
        return [dict(requirement_ids=['R1'], criterion=self.items[0]['criterion'], tests=self.items[0]['tests']),
                dict(requirement_ids=['R2'], criterion=self.items[1]['criterion'], tests=self.items[1]['tests'])]

    def test_requirement_ids_derive_exact_source_without_copying_quotes(self):
        self.ws._save_validation_report(self.report())
        result = self.plan.invoke(self.id_items())
        self.assertEqual(result['missing_evidence'], [])
        saved = self.plan.load()
        self.assertEqual(saved['version'], 2)
        self.assertEqual(saved['items'][0]['quote'], 'Add values.')
        self.assertTrue(self.evidence()['validated'])

    def test_requirement_ids_cannot_omit_a_constraint(self):
        with self.assertRaisesRegex(ValueError, 'Unmapped requirement IDs: R2'):
            self.plan.invoke(self.id_items()[:1])

    def test_invented_requirement_id_is_rejected(self):
        items = self.id_items(); items[0]['requirement_ids'] = ['R404']
        with self.assertRaisesRegex(ValueError, 'unknown requirement IDs'):
            self.plan.invoke(items)

    def test_read_plan_supplies_exact_fresh_test_ids(self):
        self.ws._save_validation_report(self.report())
        value = self.plan.invoke()
        self.assertTrue(value['validation_current'])
        self.assertEqual(value['requirements'], [{'id':'R1','text':'Add values.','required_evidence':[]}, {'id':'R2','text':'Preserve inputs.','required_evidence':[]}])
        self.assertIn('python:test_app.Tests.test_add', value['successful_tests'])
        self.assertNotIn('python:tests.test_app.Tests.test_add', value['successful_tests'])

    def test_read_plan_never_advertises_stale_test_ids(self):
        self.ws._save_validation_report(self.report())
        (self.ws.root/'app.py').write_text('answer = 43\n')
        value = self.plan.invoke()
        self.assertFalse(value['validation_current'])
        self.assertEqual(value['successful_tests'], [])

    def test_named_passes_allow_delivery_and_survive_reload(self):
        self.plan.invoke(self.items)
        restored = Acceptance(self.ws, self.plan.source, revision='r1')
        self.assertEqual(restored.load()['items'], self.items)
        self.assertTrue(self.evidence()['validated'])

    def test_omitted_constraint_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unmapped'):
            self.plan.invoke(self.items[:1])

    def test_invented_source_and_wildcards_rejected(self):
        for field, value in [('quote', 'made up'), ('tests', ['python:*']), ('criterion', '')]:
            items = [dict(r) for r in self.items]
            items[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.plan.invoke(items)

    def test_missing_skipped_or_failed_test_blocks(self):
        self.plan.invoke(self.items)
        for change in [dict(successful_tests=[]), dict(passed=False), dict(executed=0), dict(validation_run_id='')]:
            report = self.report()
            report['checks'][0].update(change)
            self.assertFalse(self.evidence(report)['validated'])

    def test_source_change_invalidates_test_proof(self):
        self.plan.invoke(self.items)
        report = self.report()
        (self.ws.root / 'app.py').write_text('answer = 43\n')
        self.assertFalse(self.evidence(report)['validated'])

    def test_request_or_user_requirements_revision_invalidates_plan(self):
        self.plan.invoke(self.items)
        for prompt, revision in [(self.plan.source, 'r2'), ('Different request', 'r1')]:
            self.assertIsNone(Acceptance(self.ws, prompt, revision=revision).load())

    def test_current_brief_must_also_be_mapped(self):
        plan = Acceptance(self.ws, self.plan.source, brief='Arabic labels')
        with self.assertRaisesRegex(ValueError, 'unmapped'):
            plan.invoke(self.items)

    def test_corrupt_plan_is_missing_proof(self):
        self.plan.path.write_text('{broken')
        self.assertFalse(self.evidence()['validated'])

    def test_host_constraints_do_not_replace_mandatory_real_tests(self):
        plan = Acceptance(self.ws, 'Do not export', evidence={'host:no_export': {'passed': True}})
        plan.invoke([dict(quote='Do not export', criterion='No archive exported', tests=['host:no_export'])])
        self.assertFalse(plan.evaluate({'checks': []})['passed'])

    def test_host_snapshot_survives_retries_and_detects_changed_tests_and_readme(self):
        tests = self.ws.root / 'tests'
        tests.mkdir()
        (tests / 'test_app.py').write_text('original test')
        (self.ws.root / 'README.txt').write_text('original README')
        baseline = self.plan.capture_tests()
        (tests / 'test_app.py').write_text('changed test')
        (self.ws.root / 'README.txt').write_text('changed README')
        self.assertEqual(self.plan.capture_tests(), baseline)
        evidence = host_evidence(self.ws, baseline, {})
        self.assertFalse(evidence['host:original_tests_unchanged']['passed'])
        self.assertFalse(evidence['host:original_readmes_unchanged']['passed'])

    def test_browser_proof_invalidates_after_source_edit(self):
        browser = {'browser:example': dict(passed=True, fingerprint=self.ws.fingerprint(force=True),
                                           assertion={'passed': True})}
        self.assertTrue(host_evidence(self.ws, {}, {}, browser=browser)['browser:example']['passed'])
        (self.ws.root / 'app.py').write_text('answer = 43\n')
        self.assertFalse(host_evidence(self.ws, {}, {}, browser=browser)['browser:example']['passed'])

    def test_host_launch_export_and_response_checks_are_observations(self):
        result = host_evidence(self.ws, {}, {'run_entry': 'app.py', 'export': {'id': 'export'}}, 'تم تنفيذ الطلب')
        self.assertFalse(result['host:no_persistent_launch']['passed'])
        self.assertFalse(result['host:no_export']['passed'])
        self.assertTrue(result['host:arabic_response']['passed'])
        self.assertFalse(result['host:application_running']['passed'])
        self.assertFalse(result['host:export_current']['passed'])
        result = host_evidence(self.ws, {}, {'export': {'source_fingerprint': self.ws.fingerprint(force=True)}})
        self.assertTrue(result['host:export_current']['passed'])

    def test_duplicate_node_names_are_not_unambiguous_proof(self):
        (self.ws.root / 'app.py').unlink()
        (self.ws.root / 'index.html').write_text('<!doctype html><html><title>Test</title><body>Test</body></html>')
        tests = self.ws.root / 'tests'
        tests.mkdir()
        (tests / 'app.test.cjs').write_text("const test = require('node:test'); test('same',()=>{}); test('same',()=>{}); test('mixed',()=>{}); test.skip('mixed',()=>{});")
        (tests / 'empty.test.cjs').write_text('// An implicit passing file is not a named test.\n')
        report = self.ws.validate()
        check = next(r for r in report['checks'] if 'successful_tests' in r)
        self.assertEqual(check['successful_tests'], [])

    def test_unicode_and_repeated_excerpts(self):
        plan = Acceptance(self.ws, 'احفظ 😊\nاحفظ 😊')
        plan.invoke([dict(quote='احفظ 😊', criterion='Unicode survives save/reopen', tests=['node:unicode save'])])

    def test_python_runner_supplies_real_ids_excludes_skip_and_expected_failure(self):
        tests = self.ws.root / 'tests'
        tests.mkdir()
        (tests / 'test_app.py').write_text('import unittest\nclass Tests(unittest.TestCase):\n'
            ' def test_add(self): self.assertEqual(2+2,4)\n'
            ' @unittest.skip("unsupported")\n def test_skip(self): pass\n'
            ' @unittest.expectedFailure\n def test_expected(self): self.fail()\n'
            'class CPass(unittest.TestCase):\n def id(self):return \"duplicate\"\n def test_ok(self):pass\n'
            'class DSkip(unittest.TestCase):\n def id(self):return \"duplicate\"\n @unittest.skip(\"skip\")\n def test_skip(self):pass\n')
        report = self.ws.validate()
        check = next(r for r in report['checks'] if r['name'] == 'python_unit_tests')
        self.assertEqual(check['successful_tests'], ['python:test_app.Tests.test_add'])

    def test_node_runner_supplies_real_ids_excludes_skip(self):
        (self.ws.root / 'app.py').unlink()
        (self.ws.root / 'index.html').write_text('<!doctype html><html lang="en"><title>Test</title><body>Test</body></html>')
        tests = self.ws.root / 'tests'
        tests.mkdir()
        (tests / 'app.test.cjs').write_text("const test = require('node:test'); const assert = require('node:assert/strict');\n"
            "test('addition works',()=>assert.equal(2+2,4));\ntest.skip('not executed',()=>{});\n")
        report = self.ws.validate()
        check = next(r for r in report['checks'] if r.get('successful_tests'))
        self.assertEqual(check['successful_tests'], ['node:addition works'])


if __name__ == '__main__':
    unittest.main()
