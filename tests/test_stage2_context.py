import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from project_core import Workspace
from project_context import project_messages, tool_result_view, inspection_guidance
from project_source_index import _javascript_index
from project_file_context import require_complete_read


class ScriptSourceViews(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)

    def source(self, text, name='app.js'):
        (self.root/name).write_text(text, encoding='utf-8', newline='\n')
        return lambda **args: self.workspace.invoke('read_file', {'path': name, **args})

    def test_qualified_class_method_exact_body(self):
        read = self.source('export class Cart {\n constructor(){this.items=[];}\n total(){\n  return this.items.length;\n }\n reset(){this.items=[];}\n}\n')
        value = read(symbol='Cart.total')
        self.assertEqual(value['start_line'], 3)
        self.assertEqual(value['end_line'], 5)
        self.assertNotIn('reset', value['content'])
        self.assertEqual(value['sha256'], self.workspace.read('app.js')['sha256'])

    def test_exported_arrow_includes_const_and_export(self):
        read = self.source('export const twice = (value) => {\n return value * 2;\n};\nexport const unrelated=7;\n')
        value = read(symbol='twice')
        self.assertTrue(value['content'].startswith('export const twice'))
        self.assertNotIn('unrelated', value['content'])

    def test_jsdoc_contract_is_retained_with_symbol(self):
        read = self.source('export function unrelated(){return 7;}\n/** Return a sum, preserving decimal values. */\nexport function add(a,b){return a+b;}\n')
        value = read(symbol='add')
        self.assertIn('preserving decimal values', value['content'])
        self.assertNotIn('unrelated', value['content'])

    def test_object_methods_are_qualified(self):
        read = self.source('export const api = {\n save(value){return value;},\n load: (id) => id\n};\n')
        self.assertIn('save(value)', read(symbol='api.save')['content'])
        self.assertIn('load:', read(symbol='api.load')['content'])

    def test_typescript_overload_selects_implementation(self):
        read = self.source('export function parse(x: string): string;\nexport function parse(x: number): string;\nexport function parse(x: string | number): string {\n return String(x);\n}\n', 'app.ts')
        value = read(symbol='parse')
        self.assertEqual(value['start_line'], 3)
        self.assertIn('return String', value['content'])

    def test_minified_symbol_pages_with_emoji_offsets(self):
        read = self.source('const emoji="😀";export function first(){return 1;}export function second(){return "مرحبا";}')
        value = read(symbol='second', max_chars=12)
        parts = [value['content']]
        while value['next_read']:
            value = self.workspace.invoke('read_file', value['next_read'])
            parts.append(value['content'])
        self.assertEqual(''.join(parts),'export function second(){return "مرحبا";}')

    def test_tsx_component_is_not_executed(self):
        read = self.source('export const Card = ({title}: {title: string}) => {\n return <h1>{title}</h1>;\n};\n', 'Card.tsx')
        self.assertIn('<h1>', read(symbol='Card')['content'])

    def test_parser_never_executes_target_source(self):
        marker = self.root/'should-not-exist.txt'
        read = self.source(f'require("fs").writeFileSync({json.dumps(str(marker))},"unsafe");\nexport function safe(){{return 1;}}\n')
        self.assertIn('safe', read(symbol='safe')['content'])
        self.assertFalse(marker.exists())

    def test_invalid_script_has_explicit_range_fallback(self):
        read = self.source('export function broken( {\n')
        with self.assertRaises(ValueError):read(symbol='broken')
        self.assertIn('unavailable source index', read(outline=True)['content'])
        self.assertIn('broken', read(start_line=1, end_line=1)['content'])

    def test_ambiguous_methods_require_qualification(self):
        read = self.source('class A {\n value(){return 1;}\n}\nclass B {\n value(){return 2;}\n}\n')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):read(symbol='value')
        self.assertIn('return 2', read(symbol='B.value')['content'])

    def test_index_cache_invalidated_by_source_bytes(self):
        _javascript_index.cache_clear()
        read = self.source('export function cached(){return 1;}\n')
        original = subprocess.run
        with patch('project_source_index.subprocess.run', wraps=original) as run:
            read(symbol='cached'); read(outline=True)
            self.assertEqual(run.call_count, 1)
            (self.root/'app.js').write_text('export function cached(){return 2;}\n', encoding='utf-8')
            self.assertIn('return 2', read(symbol='cached')['content'])
            self.assertEqual(run.call_count, 2)

    def test_search_definitions_finds_class_method(self):
        self.source('export class Cart {\n total(){return 35;}\n}\n')
        value = self.workspace.invoke('search_project', {'query':'total', 'mode':'definitions', 'path':'app.js'})
        self.assertEqual(value['result']['matches'][0]['symbol'], 'Cart.total')

    def test_read_method_then_patch_preserves_other_methods(self):
        read = self.source('class A {\n total(){return 1;}\n untouched(){return 8;}\n}\n')
        receipt = read(symbol='A.total')
        self.workspace.patch('app.js', 'total(){return 1;}', 'total(){return 2;}', receipt['sha256'])
        self.assertIn('untouched(){return 8;}', self.workspace.read('app.js')['content'])


class MutationProjection(unittest.TestCase):
    def test_known_write_hash_cannot_replace_a_file_without_reading_it(self):
        with self.assertRaises(ValueError):
            require_complete_read({}, 'created.js', 'hash-from-write')

    def test_legacy_full_read_receipt_still_resumes(self):
        require_complete_read({}, 'old.js', 'old-read-sha', legacy_read=True)
        with self.assertRaises(ValueError):
            require_complete_read({'old.js': {'sha256':'old-read-sha','complete':False}}, 'old.js', 'old-read-sha', legacy_read=True)

    def history(self, count=5):
        messages=[{'role':'system','content':'instructions'},{'role':'user','content':'original request'}]
        for i in range(count):
            messages.extend([{'role':'assistant','tool_calls':[{'id':str(i),'type':'function','function':{'name':'write_file','arguments':json.dumps({'path':f'{i}.js','content':'code '+str(i)+'\n'*2000})}}]},
                             {'role':'tool','tool_call_id':str(i),'content':json.dumps({'saved':f'{i}.js','sha256':str(i)})}])
        return messages

    def test_only_older_successful_mutations_reduced(self):
        messages=self.history();original=copy.deepcopy(messages)
        projected=project_messages(messages)
        self.assertIn('Older committed source omitted', projected[2]['tool_calls'][0]['function']['arguments'])
        self.assertEqual(projected[-4:],messages[-4:])
        self.assertEqual(messages,original)

    def test_uncommitted_or_failed_payloads_are_preserved(self):
        messages=self.history()
        messages[3]['content']='{"error":"write failed"}'
        messages[5]['content']='{"not_executed":true}'
        projected=project_messages(messages)
        self.assertEqual(projected[2],messages[2]);self.assertEqual(projected[4],messages[4])

    def test_call_ids_and_native_json_history_remain_valid(self):
        from project_generation import json_messages
        messages=self.history();projected=project_messages(messages)
        for left,right in zip(messages,projected):
            self.assertEqual(left.get('tool_call_id'),right.get('tool_call_id'))
        for message in projected:
            for call in message.get('tool_calls',[]):self.assertIsInstance(json.loads(call['function']['arguments']),dict)
        self.assertIn('Previous actions',json_messages(projected,[])[2]['content'])

    def test_read_budget_keeps_two_fresh_views_intact(self):
        messages=[]
        for i in range(4):
            messages.extend([{'role':'assistant','tool_calls':[{'id':str(i),'function':{'name':'read_file'}}]},
                             {'role':'tool','tool_call_id':str(i),'content':json.dumps({'path':f'{i}.js','sha256':'sha','content':'x'*20000})}])
        projected=project_messages(messages)
        self.assertNotIn('content',json.loads(projected[1]['content']))
        self.assertEqual(projected[-4:],messages[-4:])

    def test_validation_failures_preserved_and_pass_output_receipted(self):
        failure={'name':'node_tests','passed':False,'stdout':'real failure '+('trace\n'*500),'tests':3}
        passed={'name':'syntax','passed':True,'stdout':'passed\n'*1000,'tests':44,'skipped':2}
        value={'success':False,'checks':[failure,passed],'fingerprint':'abc'}
        original=copy.deepcopy(value);view=tool_result_view('validate_project',value)
        self.assertEqual(view['checks'][0],failure)
        self.assertEqual(view['checks'][1]['tests'],44)
        self.assertEqual(view['checks'][1]['skipped'],2)
        self.assertIn('runner_output_omitted',view['checks'][1])
        self.assertEqual(value,original)


class InspectionGuidance(unittest.TestCase):
    def history(self, count=8):
        messages=[]
        for index in range(count):
            messages.extend([{'role':'assistant','tool_calls':[{'id':str(index),'function':{'name':'read_file'}}]},
                             {'role':'tool','tool_call_id':str(index),'content':'{"path":"app.js","sha256":"sha","content":"source"}'}])
        return messages

    def tools(self):
        return [{'function':{'name':'patch_file','parameters':{'type':'object','properties':{},'required':[]}}}]

    def test_long_successful_inspection_gets_advice_without_mutating_history(self):
        messages=self.history();original=copy.deepcopy(messages)
        self.assertIn('8 read/search',inspection_guidance(messages,self.tools()))
        self.assertEqual(messages,original)

    def test_short_inspection_and_read_only_chat_are_unchanged(self):
        self.assertIsNone(inspection_guidance(self.history(7),self.tools()))
        self.assertIsNone(inspection_guidance(self.history(),[{'function':{'name':'read_file'}}]))

    def test_diagnostic_or_mutation_resets_streak(self):
        for name in ['run_command','validate_project','patch_file','browser_project']:
            messages=self.history()+[{'role':'assistant','tool_calls':[{'id':'end','function':{'name':name}}]},
                                     {'role':'tool','tool_call_id':'end','content':'{}'}]
            self.assertIsNone(inspection_guidance(messages,self.tools()))

    def test_failed_read_is_left_for_error_recovery(self):
        messages=self.history();messages[-1]['content']='{"error":"missing source"}'
        self.assertIsNone(inspection_guidance(messages,self.tools()))

    def test_native_and_json_requests_receive_same_advisory(self):
        from project_generation import request_body
        messages=[{'role':'system','content':'test'},*self.history()]
        for protocol in ['native','json']:
            body=request_body(messages,self.tools(),100,protocol=protocol)
            self.assertIn('Inspection checkpoint',body['messages'][-1]['content'])


if __name__=='__main__':unittest.main()
