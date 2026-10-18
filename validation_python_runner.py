"""Trusted unittest entry point: write execution counts, never infer them from logs."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import unittest


class Result(unittest.TextTestResult):
    passed = 0

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed += 1


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
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    skipped = sum(not isinstance(test, unittest.case._SubTest) for test, _ in result.skipped)
    report = {'runner': 'unittest', 'run_id': args.run_id, 'tests': result.testsRun,
              'executed': result.testsRun - skipped, 'passed': result.passed,
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
