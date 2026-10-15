#!/usr/bin/env python3
"""Execute every historical experiment in a new evidence tree, keeping failures.

The input tree must be frozen before starting. Historical scripts use a fixed
root; the worker redirects only their output/input root and retains the original
Git preregistration check. No prior results are copied in to fill dependencies.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
MODULES = ['e0_synthetic_survivorship', 'e1_data_coverage', 'e2_replicate',
           'e3_reality', 'e4_bias', 'e5_why_h6_failed', 'e6_value_short_sample',
           'e7_tool_data', 'e8_team_rules', 'e9_all_models_ledger', 'e10_robustness']


def worker(name, out):
    sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'experiments')]
    import _common as common
    original_guard = common.prereg_guard

    def checked_guard():
        redirected = common.ROOT
        common.ROOT = ROOT
        try:
            original_guard()
        finally:
            common.ROOT = redirected

    common.ROOT = out
    common.prereg_guard = checked_guard
    checked_guard()
    module = importlib.import_module(name)
    if hasattr(module, 'ROOT'):
        module.ROOT = out
    sys.argv = [name, '--data', 'real']
    try:
        if name == 'e0_synthetic_survivorship':
            module.main(12)
        else:
            module.main()
    except Exception:
        traceback.print_exc()
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--worker', choices=MODULES)
    args = parser.parse_args()
    out = args.out.resolve()
    if args.worker:
        return worker(args.worker, out)
    if args.data_root is None:
        parser.error('--data-root is required')
    out.mkdir(parents=True, exist_ok=False)
    (out / 'data').symlink_to(args.data_root.resolve(), target_is_directory=True)
    for directory in ('results/real', 'figures/real', 'docs/site', 'reports', 'logs'):
        (out / directory).mkdir(parents=True, exist_ok=True)
    ledger = []
    for name in MODULES:
        command = [sys.executable, str(Path(__file__).resolve()), '--out', str(out), '--worker', name]
        started = time.monotonic()
        env = os.environ.copy()
        env.setdefault('MPLCONFIGDIR', str(out / '.mpl'))
        with (out / 'logs' / f'{name}.log').open('w') as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        ledger.append({'experiment':name, 'exit_code':result.returncode,
                       'status':'executed' if result.returncode == 0 else 'failed',
                       'seconds':round(time.monotonic()-started,2), 'command':command,
                       'log':f'logs/{name}.log'})
        (out / 'experiment_ledger.json').write_text(json.dumps(ledger, indent=2)+'\n')
        print(name, result.returncode, flush=True)
    (out / 'README.md').write_text('# Historical experiment audit\n\n'
        'Every E0–E10 entry point was attempted in isolation from the tracked historical outputs. '
        'A zero command exit means the script executed, not that the scientific claims passed. '
        'Failed scripts and missing downstream dependencies are retained. E0 is synthetic; '
        'the others use this run\'s frozen input tree. See the experiment ledger and logs.\n')
    return int(any(row['exit_code'] != 0 for row in ledger))


if __name__ == '__main__':
    raise SystemExit(main())
