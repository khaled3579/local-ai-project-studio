import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from project_core import Workspace
from project_context import project_messages
from project_file_context import observe_read, require_complete_read


class SourceViews(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root)
        self.text = '# العربية\r\nimport math\r\n\r\nclass Calc:\r\n    @staticmethod\r\n    def twice(x):\r\n        return x * 2\r\n\r\ndef separate():\r\n    return 7\r\n'
        self.raw = b'\xef\xbb\xbf' + self.text.encode('utf-8')
        (self.root / 'calc.py').write_bytes(self.raw)

    def test_editor_full_read_unchanged(self):
        value = self.ws.read('calc.py')
        self.assertEqual(set(value), {'path', 'content', 'sha256'})
        self.assertEqual(value['content'], self.text)
        self.assertEqual(value['sha256'], hashlib.sha256(self.raw).hexdigest())

    def test_symbol_decorator_and_qualified_name(self):
        value = self.ws.invoke('read_file', {'path': 'calc.py', 'symbol': 'Calc.twice'})
        self.assertEqual(value['start_line'], 5)
        self.assertEqual(value['end_line'], 7)
        self.assertIn('@staticmethod\r\n', value['content'])
        self.assertNotIn('def separate', value['content'])
        self.assertEqual(value['sha256'], self.ws.read('calc.py')['sha256'])

    def test_patch_from_partial_receipt_preserves_rest(self):
        view = self.ws.invoke('read_file', {'path': 'calc.py', 'symbol': 'twice'})
        self.ws.patch('calc.py', 'return x * 2', 'return x * 3', view['sha256'])
        text = self.ws.read('calc.py')['content']
        self.assertIn('def separate():\n    return 7', text)
        self.assertIn('return x * 3', text)

    def test_stale_partial_receipt_rejected(self):
        value = self.ws.invoke('read_file', {'path': 'calc.py', 'symbol': 'twice'})
        (self.root / 'calc.py').write_text(self.text + '# manual edit', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.ws.patch('calc.py', 'return x * 2', 'return 0', value['sha256'])
        self.assertIn('# manual edit', (self.root / 'calc.py').read_text(encoding='utf-8'))

    def test_multiline_lf_patch_into_crlf_source(self):
        observed = self.ws.read('calc.py')
        self.ws.patch('calc.py', '    def twice(x):\n        return x * 2',
                      '    def twice(x):\n        return x * 4', observed['sha256'])
        self.assertIn('return x * 4', self.ws.read('calc.py')['content'])
        self.assertIn('def separate():\n    return 7', self.ws.read('calc.py')['content'])

    def test_lossless_pagination_with_unicode_and_long_line(self):
        original = '# ' + 'مرحبا' * 9000 + '\nanswer = 42\n'
        (self.root / 'long.py').write_text(original, encoding='utf-8')
        args, parts = {'path': 'long.py', 'max_chars': 137}, []
        while args:
            value = self.ws.invoke('read_file', args)
            self.assertLessEqual(len(value['content']), 137)
            parts.append(value['content'])
            args = value['next_read']
        self.assertEqual(''.join(parts), (self.root / 'long.py').read_bytes().decode('utf-8'))

    def test_outline_pagination(self):
        args, parts = {'path': 'calc.py', 'outline': True, 'max_chars': 15}, []
        while args:
            value = self.ws.invoke('read_file', args)
            parts.append(value['content'])
            args = value['next_read']
        definitions = json.loads(''.join(parts))['definitions']
        self.assertEqual([d['symbol'] for d in definitions], ['Calc', 'Calc.twice', 'separate'])

    def test_invalid_ranges_and_types(self):
        for args in ({'start_line': 0}, {'start_line': True}, {'start_line': 100},
                     {'start_line': 7, 'end_line': 3}, {'max_chars': 0}, {'max_chars': 32001},
                     {'offset': -1}, {'offset': 999999}, {'outline': 'yes'},
                     {'symbol': 'twice', 'start_line': 2}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.ws.invoke('read_file', {'path': 'calc.py', **args})

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            self.ws.invoke('read_file', {'path': '../outside.py', 'symbol': 'f'})

    def test_empty_file_and_eof(self):
        (self.root / 'empty.py').write_text('', encoding='utf-8')
        value = self.ws.invoke('read_file', {'path': 'empty.py'})
        self.assertEqual(value['content'], '')
        self.assertFalse(value['more'])

    def test_default_model_read_is_bounded(self):
        (self.root / 'large.py').write_text('# comment\n' * 3000, encoding='utf-8')
        value = self.ws.invoke('read_file', {'path': 'large.py'})
        self.assertEqual(len(value['content']), 12000)
        self.assertTrue(value['more'])
        self.assertEqual(self.ws.read('large.py')['content'], (self.root / 'large.py').read_bytes().decode('utf-8'))

    def test_ambiguous_symbol_rejected(self):
        (self.root / 'calc.py').write_text('class A:\n def f(self):pass\nclass B:\n def f(self):pass\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.ws.invoke('read_file', {'path': 'calc.py', 'symbol': 'f'})
        self.assertIn('def f', self.ws.invoke('read_file', {'path': 'calc.py', 'symbol': 'B.f'})['content'])

    def test_invalid_python_has_explicit_fallback(self):
        (self.root / 'bad.py').write_text('def f(:', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid Python'):
            self.ws.invoke('read_file', {'path': 'bad.py', 'symbol': 'f'})
        self.assertIn('Invalid Python', self.ws.invoke('read_file', {'path': 'bad.py', 'outline': True})['content'])

    def test_js_outline_and_symbol_use_syntax_parser(self):
        (self.root / 'app.js').write_text('export function f(x) { return x; }', encoding='utf-8')
        value = self.ws.invoke('read_file', {'path': 'app.js', 'outline': True})
        self.assertIn('TypeScript compiler AST', value['content'])
        self.assertIn('export function f', self.ws.invoke('read_file', {'path': 'app.js', 'symbol': 'f'})['content'])

    def test_partial_reads_cannot_authorize_whole_file_replacement(self):
        coverage = {}
        view = self.ws.invoke('read_file', {'path': 'calc.py', 'symbol': 'twice'})
        observe_read(coverage, view)
        with self.assertRaisesRegex(ValueError, 'excerpt'):
            require_complete_read(coverage, 'calc.py', view['sha256'])

    def test_paginated_reads_authorize_replacement_only_after_complete(self):
        coverage, args = {}, {'path': 'calc.py', 'max_chars': 15}
        while args:
            view = self.ws.invoke('read_file', args)
            observe_read(coverage, view)
            args = view['next_read']
            if args:
                self.assertFalse(coverage['calc.py']['complete'])
        require_complete_read(coverage, 'calc.py', view['sha256'])
        self.assertTrue(coverage['calc.py']['complete'])

    def test_new_hash_invalidates_previous_full_coverage(self):
        coverage = {}
        observe_read(coverage, self.ws.read('calc.py'))
        (self.root / 'calc.py').write_text(self.text + '# change', encoding='utf-8')
        view = self.ws.invoke('read_file', {'path': 'calc.py', 'symbol': 'twice'})
        observe_read(coverage, view)
        self.assertFalse(coverage['calc.py']['complete'])

    def test_disjoint_ranges_do_not_hide_a_gap(self):
        coverage = {}
        for start, end in ((1, 2), (5, 10)):
            view = self.ws.invoke('read_file', {'path': 'calc.py', 'start_line': start, 'end_line': end})
            observe_read(coverage, view)
        self.assertFalse(coverage['calc.py']['complete'])

    def test_outline_does_not_authorize_overwrite(self):
        coverage = {}
        view = self.ws.invoke('read_file', {'path': 'calc.py', 'outline': True})
        observe_read(coverage, view)
        self.assertFalse(coverage['calc.py']['complete'])


class Projection(unittest.TestCase):
    def test_old_reads_removed_only_from_projection(self):
        messages = [{'role': 'system', 'content': 'preserve original request'}]
        for i in range(7):
            messages.extend([{'role': 'assistant', 'tool_calls': [{'id': str(i), 'function': {'name': 'read_file'}}]},
                             {'role': 'tool', 'tool_call_id': str(i), 'content': json.dumps({'path': f'{i}.py', 'sha256': 'abc', 'content': 'source' * 1000})}])
        original = json.dumps(messages)
        projected = project_messages(messages)
        self.assertEqual(json.dumps(messages), original)
        self.assertEqual(len(projected), len(messages))
        self.assertEqual(projected[-8:], messages[-8:])
        self.assertNotIn('source', json.loads(projected[2]['content']).get('content', ''))
        self.assertEqual([m.get('tool_call_id') for m in projected], [m.get('tool_call_id') for m in messages])

    def test_error_validation_and_malformed_results_preserved(self):
        messages = [{'role': 'tool', 'content': value} for value in ['{bad', '{"error":"keep me"}', '{"success":false,"checks":[]}']]
        self.assertEqual(project_messages(messages), messages)


if __name__ == '__main__':
    unittest.main()
