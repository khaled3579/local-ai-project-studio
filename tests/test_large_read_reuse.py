import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from project_core import Workspace
from project_file_context import observe_read
from project_progress import cached_read


class LargeReadReuse(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(Path(self.temp.name))
        (self.ws.root / 'large.txt').write_text('abcdefghij\n' * 3000, encoding='utf-8')
        self.state = {}

    def read(self, args, epoch=0):
        invoke = Mock(side_effect=lambda: self.ws.read_for_model(**args))
        result = cached_read(self.state, self.ws, 'read_file', args, invoke, epoch)
        return result, invoke.call_count

    def test_standard_large_page_and_explicit_defaults_share_saved_view(self):
        first, _ = self.read({'path': 'large.txt'})
        self.state = json.loads(json.dumps(self.state))
        second, calls = self.read({'path': './large.txt', 'offset': 0, 'max_chars': 12000, 'outline': False})
        self.assertEqual(calls, 0)
        self.assertTrue(second['reused_read'])
        self.assertEqual(first['content'], second['content'])
        coverage = {}
        observe_read(coverage, second)
        self.assertFalse(coverage['large.txt']['complete'])

    def test_source_and_context_epoch_invalidate_saved_pages(self):
        args = {'path': 'large.txt'}
        self.read(args)
        self.assertEqual(self.read(args, 1)[1], 1)
        (self.ws.root / 'large.txt').write_text('changed\n', encoding='utf-8', newline='\n')
        result, calls = self.read(args, 1)
        self.assertEqual(calls, 1)
        self.assertEqual(result['content'], 'changed\n')

    def test_ranges_remain_distinct_and_invalid_types_cannot_hit_valid_cache(self):
        self.read({'path': 'large.txt'})
        self.assertEqual(self.read({'path': 'large.txt', 'offset': 12000})[1], 1)
        for args in ({'path': 'large.txt', 'offset': False},
                     {'path': 'large.txt', 'outline': 0},
                     {'path': 'large.txt', 'max_chars': '12000'}):
            with self.assertRaises(ValueError):
                self.read(args)

    def test_saved_cache_is_bounded(self):
        for offset in range(0, 30000, 1000):
            self.read({'path': 'large.txt', 'offset': offset})
        self.assertLessEqual(len(self.state['read_cache']), 24)
        self.assertLessEqual(len(json.dumps(self.state['read_cache'], ensure_ascii=False)), 262144)
