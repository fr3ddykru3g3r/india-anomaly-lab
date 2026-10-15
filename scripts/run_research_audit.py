#!/usr/bin/env python3
"""Reproduce tests and the uploaded strategy without overwriting previous findings.

Default: a known-truth fake NSE archive, not evidence of an Indian return premium.
Real: --mode real --data-root /path/to/india-anomaly-lab/data
The output directory must be new. Failures remain in the ledger and report.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import platform
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'experiments'))
import numpy as np
import pandas as pd
from indialab import engine, integrity, research as rs, stats, stockers, study, teamrules as team
from indialab.data import build, fake
from indialab.data.fundamentals import earnings_yield


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def source_hashes():
    return {str(p.relative_to(ROOT)): sha(p)
            for directory in ('src', 'tests', 'scripts', 'experiments', 'config', 'vendor')
            for p in (ROOT / directory).rglob('*')
            if p.is_file() and '__pycache__' not in p.parts}


def dump(path, value):
    def clean(x):
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, (np.bool_,)):
            return bool(x)
        if isinstance(x, (np.integer,)):
            return int(x)
        if isinstance(x, (float, np.floating)):
            return float(x) if np.isfinite(x) else None
        return x
    Path(path).write_text(json.dumps(clean(value), indent=2, default=str, allow_nan=False) + '\n')


def command(args, out, name, env):
    started = time.monotonic()
    with (out / f'{name}.log').open('w') as log:
        result = subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
    return {'command': args, 'exit_code': result.returncode,
            'seconds': round(time.monotonic() - started, 2), 'log': f'{name}.log'}


def inventory(data_root):
    return [{'path': str(p.relative_to(data_root)), 'bytes': p.stat().st_size, 'sha256': sha(p)}
            for p in sorted(data_root.rglob('*')) if p.is_file() and p.name != '.gitkeep']


def manifest_mismatches(data_root, files):
    """Compare stored NSE download hashes when supplied; absence is disclosed."""
    expected = {}
    for name in ('manifest.csv', 'manifest_pr.csv'):
        p = data_root / 'raw' / name
        if p.exists():
            for row in pd.read_csv(p).to_dict('records'):
                if row.get('status') == 200 and isinstance(row.get('sha256'), str):
                    expected[str(row['url']).rsplit('/', 1)[-1]] = row['sha256']
    return [f['path'] for f in files if Path(f['path']).name in expected
            and f['sha256'] != expected[Path(f['path']).name]], len(expected)


def family(st, daily, processed):
    weights = {'EW': rs.target_weights(st.elig)}
    unavailable = []
    for v in study.GRID:
        weights[v.name] = rs.target_weights(st.members(v))
    features = team.load_features(processed, st.p)
    for spec in team.SPECS[1:]:
        weights[spec.key] = team.weights(spec, st.p, st.elig, features)
    for n in (20, 50):
        weights[f'S5_n{n}'] = team.weights(team.SPECS[5], st.p, st.elig, features, n_hold=n)
    for name, score in (('ltrev', team.lt_reversal(st.p)), ('cheap5y', team.cheap_vs_5y_mean(st.p))):
        for q in (5, 10):
            key = f'{name}_top{100 // q}'
            if not score.where(st.elig).notna().any().any():
                unavailable.append({'model': key, 'status': 'unrun', 'reason': 'Insufficient trailing price history'})
            else:
                # Keep initial cash explicitly; matched-start comparisons are needed for inference.
                weights[key] = rs.target_weights(rs.quantile_portfolios(score, st.elig, n_q=q).eq(q))
                weights[key].attrs['first_signal_month'] = score.where(st.elig).dropna(how='all').index.min()
    for ema in (100, 200, 300):
        try:
            weights[f'Stockers_EMA{ema}'] = stockers.weights(daily, st.p, st.elig, ema=ema)
        except Exception as exc:
            unavailable.append({'model': f'Stockers_EMA{ema}', 'status': 'failed',
                                'reason': f'Native input/position replay: {type(exc).__name__}: {exc}'})
    mapping = pd.read_parquet(processed / 'monthly.parquet', columns=['sec','symbol','month'])
    ey, value_info = earnings_yield(processed.parent / 'raw', mapping)
    if ey.empty:
        unavailable.append({'model':'value_ey_top10','status':'unrun','reason':value_info['reason']})
    else:
        ey = ey.reindex(index=st.elig.index, columns=st.elig.columns)
        elig_value = st.elig & ey.notna()
        first = ey.where(st.elig).dropna(how='all').index.min()
        if pd.isna(first):
            unavailable.append({'model':'value_ey_top10','status':'unrun','reason':'No eligible earnings-yield observations'})
        else:
            weights['value_ey_top10'] = rs.target_weights(rs.quantile_portfolios(ey, elig_value, n_q=10).eq(10))
            weights['EW_value_available'] = rs.target_weights(elig_value)
            for key in ('value_ey_top10','EW_value_available'):
                weights[key].attrs['first_signal_month'] = first
                weights[key].attrs['last_signal_month'] = ey.where(st.elig).dropna(how='all').index.max()
    return weights, unavailable, value_info


def periods(index):
    return {'full': index, 'through_2017': index[index <= pd.Period('2017-12', 'M')],
            '2018_on': index[index >= pd.Period('2018-01', 'M')],
            **{label: index[(index >= pd.Period(a, 'M')) & (index <= pd.Period(b, 'M'))]
               for label, (a, b) in study.SUBPERIODS.items()}}


def evaluation_months(weights, months):
    """Do not extend a fundamental sample with cash after its source disappears."""
    first = weights.attrs.get('first_signal_month', months[0])
    last = weights.attrs.get('last_signal_month', months[-1])
    return months[(months >= first) & (months <= last)]


def opening_availability(daily, signal_months, securities):
    """An observed executable bar on the market's first archived session.

    This is execution evidence, never a signal or hindsight universe filter.
    A later first trade in the month cannot substitute for that opening.
    """
    month=daily.date.dt.to_period('M')
    first=daily.groupby(month).date.min()
    ok=daily.date.eq(month.map(first)) & daily.open.gt(0) & daily.volume.gt(0)
    observed=pd.DataFrame({'sec':daily.loc[ok,'sec'],'month':month.loc[ok],'available':True})
    wide=observed.pivot_table(index='month',columns='sec',values='available',aggfunc='any')
    wide.index=wide.index-1  # execution month -> signal month
    return wide.reindex(index=signal_months,columns=securities).fillna(False).astype(bool)


def metrics(r):
    r = r.dropna()
    if not len(r):
        return {'months': 0, 'status': 'unrun'}
    wealth = (1 + r).cumprod()
    return {'months': len(r), 'first': str(r.index.min()), 'last': str(r.index.max()),
            'cagr': float(wealth.iloc[-1] ** (12 / len(r)) - 1),
            'max_dd': float((wealth / wealth.cummax().clip(lower=1) - 1).min()),
            'sharpe_zero_rf': float(r.mean() / r.std() * np.sqrt(12)) if r.std() > 0 else None,
            'status': 'completed' if len(r) >= 12 else 'short_sample'}


def replay(args, data_root, out):
    monthly, securities = build.load(data_root / 'processed')
    daily = pd.read_parquet(data_root / 'processed/daily.parquet')
    p = rs.Panel.from_monthly(monthly, securities, total_return=args.return_basis == 'dividends')
    st = study.Study(p, label=args.mode, log_path=out / 'variants_log.csv')
    opening=opening_availability(daily, st.elig.index, st.elig.columns)
    # Both endpoint marks must correspond to market-first-session opens.
    # A delisted last close or a later observed open is not the missing mark.
    st.hold=st.hold.where(opening & opening.shift(-1).fillna(False))
    dump(out / 'execution_definition.json', {'entry':'first archived market session of t+1',
         'end_mark':'first archived market session of t+2',
         'availability':'positive opening price and traded volume',
         'note':'Archive-derived calendar, not independent calendar authentication; no queue or partial fills'})
    if not len(st.months):
        raise ValueError('No eligible study months after the primary-sample start')
    plans, unavailable, value_info = family(st, daily, data_root / 'processed')
    dump(out / 'value_input_metadata.json', value_info)
    for record in unavailable:
        st._log('unrun', record['model'], record)
    planned = sorted(set(plans) - {'EW','EW_value_available'} | {r['model'] for r in unavailable})
    dump(out / 'frozen_run_spec.json', {'mode': args.mode, 'seed': args.seed, 'n_boot': args.n_boot,
                                       'return_basis': args.return_basis,
                                       'family': planned, 'family_size': len(planned),
                                       'note': 'Exploratory replication; historical data already examined. Not a new preregistration.'})
    layers = {'gross': engine.RunConfig(costs=False, tax=False, band=0),
              'costs': engine.RunConfig(tax=False),
              'costs_tax': engine.RunConfig(),
              'stress': engine.RunConfig(k=1.5, brokerage=.003, spread_floor=.001),
              **{f'capital_{capital:g}': engine.RunConfig(capital=capital, tax=False)
                 for capital in (1e7, 1e8, 1e9)}}
    if args.mode == 'real':
        base_layers = layers.copy()
        for policy in ('last_mark', 'total_loss'):
            layers.update({f'assume_{policy}:{name}': replace(cfg, missing_return_policy=policy)
                           for name,cfg in base_layers.items()})
    summaries, failures, returns, tests = [], list(unavailable), [], []
    # Future exits are deliberately used only for this biased diagnostic.
    survivor_mask = ~securities.exited.reindex(st.elig.columns).fillna(False)
    for view in ('all_histories', 'delete_exited_biased'):
        if view == 'all_histories':
            current = plans
        else:
            old = st.elig
            st.elig = old & survivor_mask
            current, _, _ = family(st, daily, data_root / 'processed')
            st.elig = old
        for layer, cfg in layers.items():
            series = {}
            for name, w in current.items():
                try:
                    available = w.attrs.get('first_signal_month', st.months[0])
                    run_months = evaluation_months(w, st.months)
                    st._log('net', f'{view}:{layer}:{name}', {**cfg.__dict__, 'first_signal_month':str(available)})
                    result, terminal = engine.simulate(w.loc[run_months], st.hold.loc[run_months],
                        st._hs.loc[run_months], st._sigma.loc[run_months], st._adv.loc[run_months], cfg,
                        execution_available=opening.loc[run_months])
                    result.to_csv(out / 'returns' / f'{view}-{layer}-{name}.csv')
                    series[name] = result.net
                    for period, idx in periods(result.index).items():
                        x = result.loc[idx]
                        row = {'view': view, 'layer': layer, 'model': name, 'period': period,
                               **metrics(x.net), 'capital': cfg.capital,
                               'capacity_breach_months': int(x.capacity_breach.sum()),
                               'max_participation': float(x.max_participation.max()) if len(x) else None,
                               'cost_inr': float(x.cost_inr.sum()), 'tax_inr': float(x.tax_inr.sum()),
                               'turnover': float(x.turnover.mean()) if len(x) else None}
                        row['missing_names_months'] = int(x.missing_names.sum())
                        row['missing_open_names_months'] = int(x.missing_open_names.sum())
                        row['max_missing_open_exposure'] = float(x.missing_open_exposure_start.max()) if len(x) else None
                        row['max_missing_exposure'] = float(x.missing_exposure_start.max()) if len(x) else None
                        row['missing_return_policy'] = cfg.missing_return_policy
                        if period == 'full':
                            row['terminal_tax_inr'] = terminal.get('liquidation_tax')
                            row['wealth_after_terminal_tax'] = terminal.get('final_wealth_after_tax')
                            if terminal.get('final_wealth_after_tax') is not None and len(result):
                                row['terminal_after_tax_cagr'] = (terminal['final_wealth_after_tax']/cfg.capital)**(12/len(result))-1
                        summaries.append(row)
                except Exception as exc:
                    failures.append({'model': name, 'layer': layer, 'view': view, 'status': 'failed',
                                     'reason': f'{type(exc).__name__}: {exc}'})
            if view != 'all_histories' or layer.split(':')[-1] not in ('gross', 'costs', 'costs_tax'):
                continue
            if 'EW' not in series:
                tests.append({'layer':layer,'status':'blocked','reason':'The same-universe EW benchmark failed'})
                continue
            if 'value_ey_top10' in series and 'EW_value_available' in series:
                r, control_value = series['value_ey_top10'], series['EW_value_available']
                overlap = r.index.intersection(control_value.index)
                if len(overlap) >= 24:
                    tests.append({'layer':layer, 'period':'value_available_sample', 'status':'completed_exploratory',
                                  'months':len(overlap), 'benchmark':'EW_value_available',
                                  'cagr_diff_ci':stats.bootstrap_cagr_diff(r.loc[overlap], control_value.loc[overlap],
                                       n_boot=args.n_boot, seed=args.seed),
                                  'note':'Separate limited-history value comparison; not a full-history value or global-family claim'})
                else:
                    tests.append({'layer':layer,'period':'value_available_sample','status':'unrun',
                                  'months':len(overlap),'reason':'Fewer than 24 months of observed fundamentals; no padded post-availability cash history'})
            # Value has its own available-data control and history. A failed
            # price-family member must still block that family's inference.
            price_names = set(current) - {'value_ey_top10','EW_value_available'}
            failed_price = [r for r in unavailable if r['status'] == 'failed'
                            and r['model'] not in ('value_ey_top10','EW_value_available')]
            if any(name not in series for name in price_names) or failed_price:
                tests.append({'layer': layer, 'status': 'blocked', 'reason': 'One or more price-family models failed'})
                continue
            grid = pd.DataFrame({name: r for name, r in series.items()
                                 if name not in ('EW','EW_value_available','value_ey_top10')})
            for period in ('full', 'through_2017', '2018_on'):
                idx = periods(grid.index)[period]
                common_start = max([st.months[0]] + [w.attrs.get('first_signal_month', st.months[0])
                      for name,w in current.items() if name not in ('value_ey_top10','EW_value_available')]) + 1
                idx = idx[idx >= common_start]
                if len(idx) < 24:
                    tests.append({'layer': layer, 'period': period, 'status': 'unrun', 'months': len(idx),
                                  'reason': 'Fewer than 24 months; low-power window'})
                    continue
                control = series['EW'].loc[idx]
                subset = grid.loc[idx]
                excess = subset.sub(control, axis=0)
                varying = excess.std() > 1e-12
                # Identical/no-trade strategies cannot be studentized: expose them.
                excluded = list(subset.columns[~varying])
                subset = subset.loc[:, varying]
                item = {'layer': layer, 'period': period, 'months': len(idx), 'excluded_zero_variance': excluded,
                        'n_boot': args.n_boot, 'seed': args.seed, 'mean_block': 12,
                        'family_common_first_month': str(common_start),
                        'status': 'completed_exploratory', 'historical_search_count': 'unknown'}
                if subset.empty:
                    item.update(status='unrun', reason='No nondegenerate excess-return series')
                else:
                    item['white'] = stats.reality_check(subset, control, n_boot=args.n_boot, seed=args.seed)
                    item['spa'] = stats.spa(subset, control, n_boot=args.n_boot, seed=args.seed)
                    sr = excess.loc[:, varying].mean() / excess.loc[:, varying].std()
                    item['comparisons'] = {name: {
                        'cagr_diff_ci': stats.bootstrap_cagr_diff(subset[name], control, n_boot=args.n_boot, seed=args.seed),
                        'deflated_sharpe': stats.deflated_sharpe(excess[name], max(len(planned), len(subset.columns)), float(sr.var()))}
                        for name in subset}
                tests.append(item)
        print(f'{view}: completed replay', flush=True)
    pd.DataFrame(summaries).to_csv(out / 'summary.csv', index=False)
    dump(out / 'failures.json', failures)
    dump(out / 'statistics.json', tests)
    return summaries, failures, tests


def educational_page(path, payload):
    data = json.dumps(payload, default=str).replace('</', '<\\/')
    template = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest Evidence Lab</title><style>body{max-width:1100px;margin:40px auto;padding:0 20px;font:16px/1.55 system-ui;color:#1d2935;background:#fafbf9}h1{font-size:36px}label{display:inline-block;margin:10px 20px 10px 0}select{display:block;padding:9px;font:inherit}table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid #ccc;padding:9px}th{background:#e9efec}.notice{padding:18px;background:#fff3d6;border-left:5px solid #ad6400}.scroll{overflow:auto}button{padding:8px;font:inherit}a{color:#164f96}</style>
<h1>Backtest Evidence Lab</h1><p class="notice" id="mode"></p>
<p>Explore how the same rules change with implementation costs, taxes, capital and a deliberately biased universe. Every evaluated rule is shown, including losses. Empty windows and failures are published in the evidence files.</p>
<label>Implementation<select id="layer"></select></label><label>Data treatment<select id="view"><option value="all_histories">All available histories</option><option value="delete_exited_biased">Delete future exits — deliberately biased</option></select></label><label>Time window<select id="period"></select></label>
<p id="note" role="status"></p><div class="scroll"><table><thead><tr><th>Rule</th><th>Dates</th><th>Months</th><th>Status</th><th>CAGR</th><th>Max drawdown</th><th>Annualized Sharpe (RF=0)</th><th>Capacity breaches</th></tr></thead><tbody id="rows"></tbody></table></div>
<h2>Failed and unavailable models</h2><p>These records belong to the selected implementation and data treatment. A missing result must not be read as a zero return.</p><ul id="failures"></ul>
<p><button id="download">Download all results and failures (JSON)</button></p>
<h2>How to read this</h2><p>Gross uses full monthly rebalancing; implemented portfolios use a 25% relative rebalance band, so their difference includes implementation-policy effects. Tax results use a simplified resident-individual FIFO model. A 10% single-day ADV breach marks a capacity concern; the model does not simulate partial fills or trading circuits. Costs at larger capital do not prove executable capacity.</p>
<p>Deleting stocks that disappear uses future knowledge. This is an educational bias experiment, never an investable strategy. A positive return or a Sharpe ratio is not proof of alpha. The historical 2018 split has already been inspected; it is not a fresh holdout.</p>
<p>“Assume last mark” cashes out unavailable observations at the carried mark; “assume total loss” writes off existing holdings. Both reject new target fills when their future holding return is missing. These use hindsight as audit sensitivities and do not establish a causal, executable exit policy. Strict implementations publish missing-price failures.</p>
<p>Value means point-in-time fundamentals. Long-term reversal and price relative to a five-year mean are price signals, not substitutes for book-to-market or earnings yield. Full-history fundamental value remains untested here.</p>
<p id="counts"></p><p>No trades are sent. No broker credentials are used. This is a research and education tool.</p>
<script>const D=__PAYLOAD__;const el=id=>document.getElementById(id);const pct=x=>x==null?'—':(100*x).toFixed(2)+'%';
el('mode').textContent=D.mode==='synthetic'?'SYNTHETIC VALIDATION ONLY: fake NSE archives with known corporate actions. These results say nothing about Indian investment profitability.':'Historical NSE replay: exploratory research. Review input hashes, missing-data failures and assumptions before relying on results.';
for(const k of ['layer','period'])for(const v of [...new Set([...(k==='layer'?['gross','costs','costs_tax']:[]),...D.rows.map(r=>r[k]),...(k==='layer'?D.failures.map(r=>r.layer).filter(Boolean):[])])]){const o=document.createElement('option');o.value=v;o.textContent=v;el(k).append(o)}
function draw(){const rows=D.rows.filter(r=>r.layer===el('layer').value&&r.view===el('view').value&&r.period===el('period').value);el('rows').replaceChildren();for(const r of rows){const tr=document.createElement('tr');for(const v of [r.model,r.first&&r.last?r.first+' to '+r.last:'—',r.months,r.status,pct(r.cagr),pct(r.max_dd),r.sharpe_zero_rf==null?'—':r.sharpe_zero_rf.toFixed(2),r.capacity_breach_months]){const td=document.createElement('td');td.textContent=v;tr.append(td)}el('rows').append(tr)}if(!rows.length){const tr=document.createElement('tr');const td=document.createElement('td');td.colSpan=8;td.textContent='No evaluated return path in this selection. Read the failures below.';tr.append(td);el('rows').append(tr)}const fs=D.failures.filter(r=>(!r.layer||r.layer===el('layer').value)&&(!r.view||r.view===el('view').value));el('failures').replaceChildren();for(const r of fs){const li=document.createElement('li');li.textContent=r.model+' — '+r.status+': '+r.reason;el('failures').append(li)}if(!fs.length){const li=document.createElement('li');li.textContent='No failures recorded for this selection.';el('failures').append(li)}el('note').textContent=el('view').value==='all_histories'?'All supplied histories retained. Samples differ when a rule requires longer history; compare actual dates.':'WARNING: future exits removed. Performance changes are a bias diagnostic.'}for(const k of ['layer','view','period'])el(k).addEventListener('change',draw);el('download').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(D,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='backtest-evidence.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};el('counts').textContent=D.failures.length+' failed or unavailable records published; check statistics.json for family tests and low-power windows. Tax CAGR includes annual payments; terminal liquidation tax is reported separately in the evidence.';draw();</script></html>'''
    path.write_text(template.replace('__PAYLOAD__', data))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode', choices=['synthetic', 'real'], default='synthetic')
    ap.add_argument('--data-root', type=Path, default=ROOT / 'data')
    ap.add_argument('--out', type=Path)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--n-boot', type=int, default=1999)
    ap.add_argument('--return-basis', choices=['price','dividends'], default='price',
                    help='Recorded dividend-inclusive holding returns are a separate sensitivity, not a full corporate-action tape')
    ap.add_argument('--synthetic-sessions', type=int, default=3500)
    ap.add_argument('--preflight-only', action='store_true', help='Inventory real inputs without running tests or returns')
    ap.add_argument('--reuse-processed', action='store_true', help='Explicitly reuse cached panels; build lineage must be independently verified')
    args = ap.parse_args()
    out = (args.out or ROOT / 'artifacts/research' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')).resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'returns').mkdir()
    frozen_source = source_hashes()
    dump(out / 'source_at_start.json', frozen_source)
    if args.preflight_only:
        raw_count = len(list((args.data_root / 'raw/bhavcopy').rglob('*.zip')))
        required = ('daily.parquet', 'monthly.parquet', 'securities.parquet')
        present = {name: (args.data_root / 'processed' / name).is_file() for name in required}
        ready = raw_count > 0 or (args.reuse_processed and all(present.values()))
        dump(out / 'preflight.json', {'data_root': str(args.data_root.resolve()), 'raw_archives': raw_count,
                                     'processed_files_present': present, 'inputs_available': ready,
                                     'note': 'Inventory only. No tests, returns or parsing were executed.'})
        print(f'Real-input preflight: {"available" if ready else "blocked: NSE inputs absent or cached-panel reuse not authorized"}; {out}')
        return 0 if ready else 1
    env = os.environ.copy()
    env.setdefault('MPLCONFIGDIR', '/workspace/.cache/matplotlib' if Path('/workspace').exists() else str(out / '.mpl'))
    env.setdefault('XDG_CACHE_HOME', str(out / '.cache'))
    commands = {}
    commands['pytest'] = command([sys.executable, '-m', 'pytest', '-q', 'tests', f'--junitxml={out / "tests.xml"}'], out, 'pytest', env)
    commands['stockers_self_tests'] = command([sys.executable, str(stockers.ENGINE), '--self_test_only',
                '--no_download', '--no_auto_factors', '--project_root', str(out / 'stockers_native')], out, 'stockers_self_tests', env)
    counts = {}
    if (out / 'tests.xml').exists():
        nodes = ET.parse(out / 'tests.xml').findall('.//testsuite')
        counts = {k: sum(int(n.attrib.get(k, 0)) for n in nodes) for k in ('tests', 'failures', 'errors', 'skipped')}
    data_root = args.data_root.resolve()
    rows, failures, statistical = [], [], []
    data_error = None
    source_files = []
    try:
        if args.mode == 'synthetic':
            data_root = out / 'fixture'
            fake.build(data_root / 'raw', start='2010-01-01', n_sessions=args.synthetic_sessions, seed=args.seed)
            build.build(data_root / 'raw', data_root / 'processed', log=lambda msg: print(msg, flush=True))
        else:
            import _common
            _common.prereg_guard()  # existing document and upstream tags, never bypassed
            if not args.reuse_processed:
                if not list((data_root / 'raw/bhavcopy').rglob('*.zip')):
                    raise FileNotFoundError(f'No downloaded NSE archive under {data_root}; cached panels require explicit --reuse-processed and lineage verification')
                build.build(data_root / 'raw', data_root / 'processed')
            if not all((data_root / 'processed' / name).is_file() for name in ('daily.parquet', 'monthly.parquet', 'securities.parquet')):
                raise FileNotFoundError(f'Required processed NSE panels missing under {data_root}')
        source_files = inventory(data_root)
        mismatches, manifest_records = manifest_mismatches(data_root, source_files)
        if mismatches:
            raise ValueError(f'Download-manifest hash mismatches: {mismatches[:10]}')
        dump(out / 'input_inventory.json', {'files': source_files, 'manifest_records': manifest_records,
                                          'note': 'Hashes freeze supplied inputs; they do not independently authenticate NSE.'})
        rows, failures, statistical = replay(args, data_root, out)
    except Exception as exc:
        data_error = f'{type(exc).__name__}: {exc}'
        (out / 'replay_error.log').write_text(traceback.format_exc())
        print(data_error, file=sys.stderr)
    ending_source = source_hashes()
    changed_source = sorted(k for k in set(frozen_source) | set(ending_source)
                            if frozen_source.get(k) != ending_source.get(k))
    git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    status = subprocess.run(['git', 'status', '--short'], cwd=ROOT, capture_output=True, text=True)
    evidence = {'mode': args.mode, 'return_basis':args.return_basis, 'generated_utc': datetime.now(timezone.utc).isoformat(),
                'python': sys.version, 'platform': platform.platform(), 'git_head': git.stdout.strip(),
                'working_tree_status': status.stdout, 'source_hashes': frozen_source,
                'source_hashes_at_end': ending_source, 'source_changed_during_run': changed_source,
                'uploaded_engine_sha256': sha(stockers.ENGINE), 'commands': commands, 'pytest': counts,
                'data_error': data_error, 'input_file_count': len(source_files),
                'summary_rows': len(rows), 'failure_records': len(failures), 'seed': args.seed, 'n_boot': args.n_boot}
    dump(out / 'evidence.json', evidence)
    dump(out / 'environment_versions.json', {p: __import__(p).__version__ for p in ('numpy', 'pandas', 'scipy', 'matplotlib', 'pyarrow', 'pytest')})
    dump(out / 'tool_data.json', {'mode': args.mode, 'rows': rows, 'failures': failures})
    educational_page(out / 'audit.html', {'mode': args.mode, 'rows': rows, 'failures': failures})
    report = [f'# Reproduction run: {args.mode}', '',
              '**Synthetic returns are software validation, not evidence of Indian alpha.**' if args.mode == 'synthetic' else '**Exploratory historical replication; previously seen data are not a fresh holdout.**', '',
              f'Generated UTC: {evidence["generated_utc"]}', f'Uploaded engine SHA-256: `{evidence["uploaded_engine_sha256"]}`', '',
              f'Pytest: {counts}. Underlying exit status: {commands["pytest"]["exit_code"]}.',
              f'Original Stockers self-test exit status: {commands["stockers_self_tests"]["exit_code"]}. See its CSV for the 12 individual checks.',
              f'Inputs: {len(source_files)} files inventoried. Summary rows: {len(rows)}. Failed/unavailable model records: {len(failures)}.', '',
              f'Replay blocker: {data_error or "none"}.', '',
              'Evidence: evidence.json, input_inventory.json, frozen_run_spec.json, variants_log.csv, summary.csv, statistics.json, failures.json, returns/*.csv, tests.xml, and command logs.', '',
              'Read docs/REPLICATION_PROTOCOL.md and reports/REPRODUCTION_AUDIT.md for definitions, confirmed defects, historical-output status, tax assumptions and remaining scientific limitations.']
    (out / 'REPORT.md').write_text('\n'.join(report) + '\n')
    print(f'Evidence: {out}', flush=True)
    if data_error or changed_source or any(c['exit_code'] != 0 for c in commands.values()) or any(f.get('status') == 'failed' for f in failures):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
