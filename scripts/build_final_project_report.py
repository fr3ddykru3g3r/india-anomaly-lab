"""Build the curated report from a published snapshot; never rerun strategies."""
from pathlib import Path
from string import Template
import hashlib
import json
import os
import re

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR', '/tmp/indialab-report-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter
import markdown
import numpy as np
import pandas as pd

DATA = ROOT / 'reports/data/final_report'
FIGURES = ROOT / 'figures/audit'
LAYER = 'assume_total_loss:costs_tax'
INITIAL = 1_000_000
LABELS = {
    'S2': 'S2: momentum, top 30', 'S3': 'S3: momentum + trend',
    'momentum12_top10': 'Momentum, strongest 10%', 'S5': 'S5: earlier team approximation',
    'Stockers_EMA100': 'Native Stockers, monthly EMA100',
    'Stockers_EMA200': 'Native Stockers, monthly EMA200',
    'Stockers_EMA300': 'Native Stockers, monthly EMA300',
    'EW': 'Same-universe equal weight', 'low_vol12_top10': 'Low volatility, lowest-risk 10%',
    'reversal_top10': 'One-month reversal',
}
SHORT = {'S2': 'S2 momentum', 'S3': 'S3 momentum + trend',
         'momentum12_top10': 'Momentum decile', 'S5': 'Earlier S5', 'EW': 'Equal weight',
         'low_vol12_top10': 'Low volatility', 'reversal_top10': 'Reversal',
         'Stockers_EMA200': 'Native EMA200'}
COLORS = {'S2': '#126485', 'S3': '#478da8', 'momentum12_top10': '#c66622',
          'S5': '#6d6196', 'EW': '#555c63', 'low_vol12_top10': '#26795b',
          'reversal_top10': '#a74043', 'Stockers_EMA200': '#92713e'}


def pct(value):
    return '—' if pd.isna(value) else f'{100 * value:.2f}%'


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows])


def main():
    provenance = json.loads((DATA / 'provenance.json').read_text())
    for name, expected in provenance['snapshot_sha256'].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == expected, name
    summaries = pd.read_csv(DATA / 'summary.csv')
    paths = pd.read_csv(DATA / 'monthly_paths.csv')
    bootstrap = pd.read_csv(DATA / 'bootstrap_intervals.csv')
    families = json.loads((DATA / 'family_tests.json').read_text())
    for family in families.values():
        family.setdefault('first', family.get('family_common_first_month'))
        family.setdefault('last', str(pd.Period(family['first'], freq='M') + family['months'] - 1))
    full = summaries[(summaries.layer == LAYER) & (summaries.period == 'full')].set_index('model')
    matched = full[full.months == 116].sort_values('cagr', ascending=False)
    assert len(full) == 32 and full.index.is_unique and matched.index[0] == 'S2'
    assert len(matched) == 26
    assert provenance['completed_paths'] + provenance['failed_scenarios'] == 1344

    # Verify chart paths against their original summaries and accounting identity.
    series = {}
    for model, frame in paths.groupby('model', sort=False):
        months = pd.PeriodIndex(frame.month, freq='M')
        assert months.equals(pd.period_range('2017-02', '2026-09', freq='M'))
        wealth = np.r_[INITIAL, frame.wealth.to_numpy(float)]
        calculated_cagr = (wealth[-1] / INITIAL) ** (12 / len(frame)) - 1
        drawdown = wealth / np.maximum.accumulate(wealth) - 1
        assert abs(calculated_cagr - full.loc[model, 'cagr']) < 1e-10, model
        assert abs(drawdown.min() - full.loc[model, 'max_dd']) < 1e-10, model
        assert np.allclose(wealth[1:] / wealth[:-1] - 1, frame.net, atol=1e-10), model
        dates = [months[0].start_time - pd.Timedelta(days=1), *months.to_timestamp(how='end')]
        series[model] = {'dates': dates, 'wealth': wealth, 'drawdown': drawdown}

    FIGURES.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.edgecolor': '#bcc6cc', 'axes.labelcolor': '#253746',
                         'text.color': '#253746', 'grid.alpha': .18,
                         'svg.hashsalt': 'india-anomaly-final-report'})
    chart_names = []

    def save(fig, name):
        fig.tight_layout()
        fig.savefig(FIGURES / f'{name}.png', dpi=200, bbox_inches='tight')
        svg = FIGURES / f'{name}.svg'
        fig.savefig(svg, bbox_inches='tight', metadata={'Date': None})
        svg.write_text('\n'.join(line.rstrip() for line in svg.read_text().splitlines()) + '\n')
        plt.close(fig)
        chart_names.append(name)

    fig, ax = plt.subplots(figsize=(10.5, 5.3))
    for model in ['S2', 'momentum12_top10', 'S5', 'EW', 'low_vol12_top10', 'reversal_top10']:
        s = series[model]
        ax.plot(s['dates'], s['wealth'] / INITIAL, label=SHORT[model], color=COLORS[model], lw=2)
    ax.set_yscale('log')
    ax.set_yticks([.25, .5, 1, 2, 5, 10])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value:g}×'))
    ax.yaxis.set_minor_formatter(FuncFormatter(lambda *_: ''))
    ax.set(ylabel='Wealth / initial INR 10 lakh', title='Momentum led conditional growth')
    ax.grid(True, which='major')
    ax.legend(ncol=3, loc='upper left', frameon=False, fontsize=9)
    save(fig, 'final_strategy_comparison')

    fig, ax = plt.subplots(figsize=(10.5, 4.6))
    for model in ['S2', 'S5', 'EW', 'low_vol12_top10']:
        s = series[model]
        ax.plot(s['dates'], s['drawdown'], label=f'{SHORT[model]}: {pct(full.loc[model, "max_dd"])}',
                color=COLORS[model], lw=1.9)
    ax.set(ylim=(-.62, .035), ylabel='Loss from previous peak',
           title='Low volatility experienced much smaller drawdowns')
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.grid(True, axis='y')
    ax.legend(ncol=2, loc='lower left', fontsize=9, frameon=False)
    save(fig, 'final_drawdowns')

    cost_models = ['S2', 'momentum12_top10', 'EW', 'low_vol12_top10', 'reversal_top10']
    cost_layers = ['gross', 'costs', 'costs_tax', 'stress']
    layer_labels = ['Gross', 'After costs', 'After annual tax', 'Higher-cost stress + tax']
    cost = summaries[summaries.period == 'full'].set_index(['model', 'layer'])
    fig, ax = plt.subplots(figsize=(10.5, 5.2))
    positions = np.arange(len(cost_models))
    for i, (suffix, color) in enumerate(zip(cost_layers, ['#a6b4c1', '#5487a1', '#126485', '#c66622'])):
        vals = [cost.loc[(model, 'assume_total_loss:' + suffix), 'cagr'] for model in cost_models]
        bars = ax.bar(positions + (i - 1.5) * .19, vals, width=.18, label=layer_labels[i], color=color)
        ax.bar_label(bars, labels=[f'{100 * v:.1f}' for v in vals], fontsize=7.5, padding=3)
    ax.set_xticks(positions, [SHORT[model] for model in cost_models])
    ax.set(ylim=(-.12, .43), ylabel='Annualized growth', title='Reversal was particularly vulnerable to implementation costs')
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.axhline(0, color='#65727b', lw=.8)
    ax.grid(True, axis='y')
    ax.set_axisbelow(True)
    ax.legend(ncol=2, loc='upper right', frameon=False, fontsize=9)
    save(fig, 'final_cost_layers')

    ci = bootstrap[(bootstrap.period == '2018_on') & (bootstrap.mean_block == 12)].set_index('model')
    uncertainty_models = ['momentum12_top10', 'S5', 'Stockers_EMA200', 'low_vol12_top10', 'reversal_top10']
    fig, ax = plt.subplots(figsize=(10.5, 4.5))
    for y, model in enumerate(uncertainty_models):
        r = ci.loc[model]
        ax.errorbar(100 * r['diff'], y, xerr=[[100 * (r['diff'] - r.lo)], [100 * (r.hi - r['diff'])]],
                    fmt='o', capsize=5, color=COLORS[model], markersize=7, lw=2)
        ax.text(100 * r.hi + .7, y, f'{100 * r["diff"]:+.2f} pp', va='center', fontsize=10)
    ax.axvline(0, color='#6c747a', ls='--', lw=1)
    ax.set_yticks(range(len(uncertainty_models)), [SHORT[m] for m in uncertainty_models])
    ax.invert_yaxis()
    ax.set(xlim=(-16, 27), xlabel='Annualized CAGR difference versus equal weight (percentage points)',
           title='Native EMA200 showed little evidence of excess return')
    ax.grid(True, axis='x')
    save(fig, 'final_excess_uncertainty')

    selected = ['S2', 'S3', 'momentum12_top10', 'S5', 'Stockers_EMA300',
                'Stockers_EMA200', 'EW', 'low_vol12_top10', 'reversal_top10']
    values = {}
    for prefix, model in [('s2', 'S2'), ('s3', 'S3'), ('s5', 'S5'), ('momentum', 'momentum12_top10'),
                          ('native300', 'Stockers_EMA300'), ('native200', 'Stockers_EMA200'),
                          ('ew', 'EW'), ('lowvol', 'low_vol12_top10')]:
        r = full.loc[model]
        values.update({f'{prefix}_cagr': pct(r.cagr), f'{prefix}_drawdown': pct(r.max_dd),
                       f'{prefix}_terminal_cagr': pct(r.terminal_after_tax_cagr),
                       f'{prefix}_final_lakh': f'{series[model]["wealth"][-1] / 1e5:.2f}',
                       f'{prefix}_peak_remaining': f'{10 * (1 + r.max_dd):.2f}'})
    for prefix, model in [('momentum', 'momentum12_top10'), ('native', 'Stockers_EMA200')]:
        r = ci.loc[model]
        values.update({f'{prefix}_excess': f'{100 * r["diff"]:.2f}',
                       f'{prefix}_lo': f'{100 * r.lo:.2f}', f'{prefix}_hi': f'{100 * r.hi:.2f}'})
    for prefix, model in [('reversal', 'reversal_top10'), ('s2', 'S2'), ('momentum', 'momentum12_top10')]:
        for suffix, label in zip(cost_layers, ['gross', 'costs', 'tax', 'stress']):
            values[f'{prefix}_{label}'] = pct(cost.loc[(model, 'assume_total_loss:' + suffix), 'cagr'])
    values['comparison_table'] = table(
        ['Strategy', 'Final wealth (INR lakh)', 'CAGR', 'Max drawdown', 'Sharpe*'],
        [[LABELS[m], f'{series[m]["wealth"][-1] / 1e5:.2f}', pct(full.loc[m, 'cagr']),
          pct(full.loc[m, 'max_dd']), f'{full.loc[m, "sharpe_zero_rf"]:.2f}'] for m in selected])
    values['cost_table'] = table(['Strategy', 'Gross', 'After costs', 'After annual tax', 'Higher-cost stress'],
        [[SHORT[m]] + [pct(cost.loc[(m, 'assume_total_loss:' + suffix), 'cagr']) for suffix in cost_layers]
         for m in cost_models])
    later = summaries[(summaries.period == '2018_on') & (summaries.layer == LAYER)].set_index('model')
    values['period_table'] = table(['Strategy', 'CAGR', 'Max drawdown'],
        [[SHORT[m], pct(later.loc[m, 'cagr']), pct(later.loc[m, 'max_dd'])] for m in cost_models])
    values['family_table'] = table(['Family', 'Dates', 'Months', 'SPA p-value', 'White p-value'],
        [[label, f'{r["first"]}–{r["last"]}', r['months'], f'{r["spa"]["p_value"]:.4f}',
          f'{r["white"]["p_value"]:.4f}']
         for label, r in [('Original 12 rules', families['original_12']), ('Broader 29 price rules', families['broader_29'])]])
    values['checks_table'] = table(['Check or outcome', 'Frozen-run result'], [
        ['Software regression tests', '54 passed; 0 failures, errors or skips'],
        ['Unchanged native-engine toy checks', '12 of 12 passed'],
        ['Attempted portfolio/scenario/view combinations', '1,344'],
        ['Completed monthly portfolio paths', '1,043'], ['Failed scenario records retained', '301'],
        ['Saved paths passing accounting checks', '1,043 of 1,043'],
        ['Summary rows across eight period views', '8,344'],
        ['Numerical source changed during the run', 'No']])
    narrative = Template((ROOT / 'docs/FINAL_REPORT_NARRATIVE.md').read_text()).substitute(values)
    ledger = table(['Rank', 'Model ID', 'CAGR', 'After terminal tax', 'Max drawdown', 'Sharpe*'],
        [[i + 1, model, pct(r.cagr), pct(r.terminal_after_tax_cagr), pct(r.max_dd), f'{r.sharpe_zero_rf:.2f}']
         for i, (model, r) in enumerate(matched.iterrows())])
    shorter = full[full.months != 116]
    short_table = table(['Model', 'First holding month', 'Last holding month', 'Months', 'CAGR'],
        [[m, r['first'], r['last'], int(r.months), pct(r.cagr)] for m, r in shorter.iterrows()])
    text = narrative + '\n\n## Appendix A. Complete model ledger\n\n'
    text += 'These 26 portfolios share the same 116-month history and annual-tax sensitivity. '
    text += 'Sharpe assumes a zero risk-free rate. Terminal trading fees are omitted.\n\n' + ledger
    text += '\n\nThe remaining six portfolios use shorter histories and are not ranked against the full sample.\n\n' + short_table
    for title, filename in [('Appendix B. Full reproduction audit', 'REPRODUCTION_AUDIT.md'),
                            ('Appendix C. Claim-by-claim review', 'CLAUDE_REPORT_REVIEW.md')]:
        text += '\n\n## ' + title + '\n\n'
        source = (ROOT / 'reports' / filename).read_text()
        text += '\n'.join('#' + line if line.startswith('#') else line for line in source.splitlines())
    (ROOT / 'reports/FINAL_PROJECT_REPORT.md').write_text(text + '\n')
    body = markdown.markdown(text, extensions=['tables', 'fenced_code', 'toc'])
    body = re.sub(r'<p>(<img [^>]+>)</p>\s*<p><em>(.*?)</em></p>',
                  r'<figure>\1<figcaption>\2</figcaption></figure>', body, flags=re.S)
    body = re.sub(r'(<table>.*?</table>)', r'<div class="table-scroll">\1</div>', body, flags=re.S)
    css = (ROOT / 'docs/final_report.css').read_text()
    html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>India Anomaly Lab — Final project report</title><style>' + css +
            '</style></head><body><main>' + body + '</main></body></html>')
    (ROOT / 'reports/FINAL_PROJECT_REPORT.html').write_text(html)
    proof = {'source': 'Published frozen evidence snapshot; no numerical rerun',
             'figures': chart_names, 'monthly_paths_verified': len(series),
             'checks': ['CAGR matches monthly wealth within 1e-10',
                        'Maximum drawdown matches monthly wealth within 1e-10',
                        'Wealth recursion matches saved net returns', 'Snapshot SHA-256 matches provenance'],
             'source_sha256': provenance['source_sha256']}
    (DATA / 'report_build_check.json').write_text(json.dumps(proof, indent=2) + '\n')
    print(json.dumps({'figures': len(chart_names), 'verified_monthly_paths': len(series),
                      'matched_strategies': len(matched), 'report_bytes': len(text.encode())}))


if __name__ == '__main__':
    main()
