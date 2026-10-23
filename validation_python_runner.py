"""Trusted unittest entry point: write execution counts, never infer them from logs."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import unittest
from typing import cast


class Result(unittest.TextTestResult):
    passed = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successful_tests = []
        self.skipped_parents = set()
        self.registered_test_ids = {}

    def startTest(self, test):
        super().startTest(test)
        identity = test.id()
        if identity in self.registered_test_ids or len(self.registered_test_ids) < 4096:
            self.registered_test_ids[identity] = self.registered_test_ids.get(identity, 0) + 1

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed += 1
        if test.id() not in self.skipped_parents and len(self.successful_tests) < 4096:
            self.successful_tests.append("python:" + test.id())

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.skipped_parents.add(getattr(test, "test_case", test).id())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    start = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(root / 'tests') if (root / 'tests').is_dir() else str(root), pattern='test*.py')
    result = cast(Result, unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite))
    skipped = sum(not isinstance(test, getattr(unittest.case, '_SubTest')) for test, _ in result.skipped)
    report = {'runner': 'unittest', 'run_id': args.run_id, 'tests': result.testsRun,
              'executed': result.testsRun - skipped, 'passed': result.passed,
              'successful_tests': [name for name in result.successful_tests if result.registered_test_ids.get(name.removeprefix('python:')) == 1],
              'skipped': skipped, 'skipped_subtests': len(result.skipped) - skipped,
              'failed': len(result.failures), 'errors': len(result.errors),
              'expected_failures': len(result.expectedFailures),
              'unexpected_successes': len(result.unexpectedSuccesses),
              'success': result.wasSuccessful(), 'seconds': round(time.monotonic() - start, 3)}
    destination = Path(args.report)
    temporary = destination.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(report, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(destination)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
