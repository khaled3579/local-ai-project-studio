"""Run a local service independently, retaining startup/native crash diagnostics."""
import faulthandler
from pathlib import Path
import runpy
import sys
import traceback


def main():
    root = Path(__file__).resolve().parent
    target = Path(sys.argv[1]).resolve()
    log_path = Path(sys.argv[2]).resolve()
    if target not in (root / 'daily_ai.py', root / 'project_studio.py'):
        raise ValueError('Unsupported service')
    if log_path.parent != root / 'daily/logs':
        raise ValueError('Bootstrap log must stay in the service log directory')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        faulthandler.enable(file=log, all_threads=True)
        sys.argv = [str(target), *sys.argv[3:]]
        try:
            runpy.run_path(str(target), run_name='__main__')
        except BaseException:
            traceback.print_exc(file=log)
            raise
        finally:
            faulthandler.disable()


if __name__ == '__main__':
    main()
