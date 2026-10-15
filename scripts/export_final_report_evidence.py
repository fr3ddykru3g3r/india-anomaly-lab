"""Extract a small report snapshot from frozen runs, without executing strategies."""
from pathlib import Path
import hashlib
import json

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'artifacts/research/independent-nse-execution-price-v2'
OUT = ROOT / 'reports/data/final_report'
LAYER = 'assume_total_loss:costs_tax'
MODELS = ['S2', 'S3', 'momentum12_top10', 'S5', 'Stockers_EMA100',
          'Stockers_EMA200', 'Stockers_EMA300', 'EW', 'low_vol12_top10', 'reversal_top10']


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sources = []

    def read(name):
        path = RUN / name
        sources.append(path)
        return path

    summary = pd.read_csv(read('summary.csv'))
    # Keep every all-history portfolio, period and scenario for traceable context.
    summary[summary['view'] == 'all_histories'].to_csv(OUT / 'summary.csv', index=False)
    paths = []
    for model in MODELS:
        frame = pd.read_csv(read(f'returns/all_histories-{LAYER}-{model}.csv'))
        frame.insert(0, 'model', model)
        paths.append(frame)
    pd.concat(paths, ignore_index=True).to_csv(OUT / 'monthly_paths.csv', index=False)
    intervals = pd.read_csv(read('block_length_sensitivity.csv'))
    intervals[intervals.layer == LAYER].to_csv(OUT / 'bootstrap_intervals.csv', index=False)
    original = json.loads(read('original_grid_statistics.json').read_text())
    broad = json.loads(read('statistics.json').read_text())
    family_tests = {
        'original_12': next(r for r in original if r['layer'] == LAYER and r['period'] == '2018_on'),
        'broader_29': next(r for r in broad if r['layer'] == LAYER and r.get('period') == 'full'),
    }
    # Individual-rule comparisons live in the published bootstrap table.
    family_tests['broader_29'].pop('comparisons', None)
    (OUT / 'family_tests.json').write_text(json.dumps(family_tests, indent=2) + '\n')
    checks = json.loads(read('output_path_checks.json').read_text())
    failures = json.loads(read('failures.json').read_text())
    evidence = json.loads(read('evidence.json').read_text())
    native_log = read('stockers_self_tests.log').read_text()
    assert '12/12' in native_log
    assert len(checks) == 1043 and all(r['passed'] for r in checks)
    assert len(failures) == 301
    assert evidence['pytest'] == dict(tests=54, failures=0, errors=0, skipped=0)
    assert not evidence['source_changed_during_run']
    manifest = {
        'scope': 'Small derived report snapshot; full raw inputs and execution logs are separate.',
        'source_run': str(RUN.relative_to(ROOT)),
        'generated_utc_of_source_run': evidence['generated_utc'],
        'selection': {'view': 'all_histories', 'headline_layer': LAYER,
                      'headline_first': '2017-02', 'headline_last': '2026-09',
                      'initial_capital_inr': 1000000},
        'pytest': evidence['pytest'], 'native_self_tests_passed': 12,
        'completed_paths': len(checks), 'failed_scenarios': len(failures),
        'attempted_scenarios': len(checks) + len(failures),
        'summary_rows_all_views': len(summary),
        'all_saved_paths_pass_accounting_checks': True,
        'numerical_source_changed_during_run': False,
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sources},
        'snapshot_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(OUT.iterdir()) if p.name != 'provenance.json'},
    }
    (OUT / 'provenance.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'snapshot': str(OUT.relative_to(ROOT)), 'paths': len(paths),
                      'summary_rows': int((summary['view'] == 'all_histories').sum())}))


if __name__ == '__main__':
    main()
