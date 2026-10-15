#!/usr/bin/env python3
"""Recompute supplied monthly results; this does not verify original daily parsing."""
from pathlib import Path
import hashlib
import sys
import argparse
from dataclasses import replace
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from indialab import engine, research as rs, study, stats
from run_research_audit import dump, metrics, periods


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missing-policy', choices=['raise', 'last_mark', 'total_loss'], default='raise')
    args = parser.parse_args()
    source = ROOT / 'artifacts/supplied'
    suffix = '' if args.missing_policy == 'raise' else f'-assume-{args.missing_policy}'
    out = ROOT / f'artifacts/research/supplied-monthly-reconciliation{suffix}'
    out.mkdir(parents=True, exist_ok=False)
    monthly = pd.read_parquet(source / 'monthly.parquet')
    monthly.month = pd.PeriodIndex(monthly.month, freq='M')
    secs = pd.read_parquet(source / 'securities.parquet')
    p = rs.Panel.from_monthly(monthly, secs)
    st = study.Study(p)
    legacy_months = st.elig.index[st.elig.any(axis=1).values][:-1]
    legacy_months = legacy_months[legacy_months >= study.PRIMARY_FIRST_HOLD - 1]
    original = pd.read_csv(source / 'e2_long_only_gross_returns.csv', index_col=0)
    original.index = pd.PeriodIndex(original.index, freq='M')
    checks = []
    for v in study.GRID:
        w = rs.target_weights(st.members(v))
        r = (w.loc[legacy_months] * st.hold.loc[legacy_months].fillna(0)).sum(axis=1)
        r.index = r.index + 1
        idx = r.index.intersection(original.index)
        err = (r.loc[idx] - original[v.name].loc[idx]).abs()
        checks.append({'model': v.name, 'months': len(idx), 'max_abs_error': float(err.max()),
                       'match_at_1e_10': bool((err < 1e-10).all())})
    dump(out / 'gross_reconciliation.json', checks)
    print('Legacy gross-series matches:',sum(c['match_at_1e_10'] for c in checks),'/',len(checks),flush=True)
    # Lag the supplied aggregate spread proxy conservatively. Exact corrected
    # daily spread pairs require the newly downloaded daily files.
    p.ar_s2 = p.ar_s2.shift(1)
    st = study.Study(p, label='supplied_monthly_lagged_spread', log_path=out / 'variants_log.csv')
    st.months = st.months[st.months + 1 >= pd.Period('2016-01', 'M')]
    weights = {'EW': rs.target_weights(st.elig)}
    weights.update({v.name: rs.target_weights(st.members(v)) for v in study.GRID})
    layers = {'gross': engine.RunConfig(costs=False, tax=False, band=0),
              'costs': engine.RunConfig(tax=False), 'costs_tax': engine.RunConfig(),
              'stress': engine.RunConfig(k=1.5, brokerage=.003, spread_floor=.001),
              **{f'capital_{cap:g}': engine.RunConfig(capital=cap, tax=False) for cap in (1e7,1e8,1e9)}}
    layers = {key:replace(cfg, missing_return_policy=args.missing_policy) for key,cfg in layers.items()}
    rows, failures, inference = [], [], []
    for layer, cfg in layers.items():
        series = {}
        for name, w in weights.items():
            try:
                result, summary = st.run(w, cfg, name)
                result.to_csv(out / f'{layer}-{name}.csv')
                series[name] = result.net
                for period, idx in periods(result.index).items():
                    x = result.loc[idx]
                    rows.append({'layer':layer,'model':name,'period':period,**metrics(x.net),
                                 'capacity_breach_months':int(x.capacity_breach.sum()),
                                 'max_participation':float(x.max_participation.max()) if len(x) else None})
            except Exception as exc:
                failures.append({'layer':layer,'model':name,'error':f'{type(exc).__name__}: {exc}'})
        if layer in ('gross','costs','costs_tax') and len(series)==len(weights):
            variants = pd.DataFrame({k:v for k,v in series.items() if k!='EW'})
            for period in ('full','through_2017','2018_on'):
                idx = periods(variants.index)[period]
                if len(idx)<24:
                    inference.append({'layer':layer,'period':period,'status':'unrun','months':len(idx)})
                    continue
                grid,ew = variants.loc[idx],series['EW'].loc[idx]
                inference.append({'layer':layer,'period':period,'months':len(idx),
                                  'spa':stats.spa(grid,ew,n_boot=1999),
                                  'white':stats.reality_check(grid,ew,n_boot=1999),
                                  'comparisons':{k:stats.bootstrap_cagr_diff(grid[k],ew,n_boot=1999) for k in grid}})
        print(layer,'complete; failure records',len(failures),flush=True)
    pd.DataFrame(rows).to_csv(out / 'summary.csv',index=False)
    dump(out / 'failures.json',failures)
    dump(out / 'statistics.json',inference)
    dump(out / 'evidence.json',{'scope':'Independent computations from supplied processed monthly data; original daily parsing unverified',
                              'source_hashes':{n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in
                                               ('monthly.parquet','securities.parquet','e2_long_only_gross_returns.csv')},
                              'legacy_reconciliation':checks,'failure_count':len(failures),
                              'complete_hold_start':str(st.months.min()+1),'complete_hold_end':str(st.months.max()+1),
                              'spread_policy':'Conservatively lagged supplied monthly aggregate',
                              'missing_return_policy':args.missing_policy,
                              'daily_stockers':'Not executed in this monthly-only reconciliation'})
    return 1 if failures or not all(c['match_at_1e_10'] for c in checks) else 0


if __name__=='__main__':
    raise SystemExit(main())
