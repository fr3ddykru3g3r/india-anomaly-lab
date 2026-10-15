#!/usr/bin/env python3
"""Build a traceable report and scientific figures from an existing audit run.

Does not execute strategies, change prior evidence or invent missing results.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import markdown
from run_research_audit import educational_page

MODELS = {'EW':'Same-universe EW','momentum12_top10':'Momentum 12–1',
          'low_vol12_top10':'Low volatility','reversal_top10':'One-month reversal',
          'S5':'Earlier S5 approximation','Stockers_EMA200':'Native Stockers EMA200 adaptation'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', type=Path, required=True)
    ap.add_argument('--data-root', type=Path, required=True)
    ap.add_argument('--snapshot-label', required=True)
    args = ap.parse_args()
    run = args.run.resolve()
    evidence = json.loads((run / 'evidence.json').read_text())
    summary = pd.read_csv(run / 'summary.csv')
    failures = json.loads((run / 'failures.json').read_text())
    build = json.loads((args.data_root / 'processed/build_stats.json').read_text())
    figures = ROOT / 'figures/audit'
    figures.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'axes.spines.top':False,'axes.spines.right':False,
                         'axes.grid':True,'grid.alpha':.2,'font.size':10})
    layer = 'assume_total_loss:costs_tax'
    selected = summary[(summary.view=='all_histories') & (summary.period=='full') &
                       (summary.layer==layer) & summary.model.isin(MODELS)]
    rows = []
    for key,label in MODELS.items():
        x=selected[selected.model==key]
        if x.empty:
            rows.append(f'| {label} | unavailable | — | — | — |')
        else:
            x=x.iloc[0]
            rows.append(f'| {label} | {x["first"]}–{x["last"]} ({int(x.months)} months) | {100*x.cagr:.2f}% | {100*x.max_dd:.2f}% | {int(x.capacity_breach_months)} |')
    fig, ax=plt.subplots(figsize=(10,4.8))
    for key,label in MODELS.items():
        path=run/'returns'/f'all_histories-{layer}-{key}.csv'
        if path.exists():
            r=pd.read_csv(path,index_col=0).net
            r.index=pd.PeriodIndex(r.index,freq='M').to_timestamp()
            initial=pd.Series([1.],index=[r.index[0]-pd.offsets.MonthBegin(1)])
            ax.plot(pd.concat([initial,(1+r).cumprod()]),label=label)
    ax.set_yscale('log')
    from matplotlib.ticker import FuncFormatter
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x,p:f'INR {x:g}'))
    ax.yaxis.set_minor_formatter(FuncFormatter(lambda x,p:''))
    low,high=ax.get_ylim()
    ax.set_yticks([x for x in (.1,.2,.3,.5,1,2,3,5,10,20,30,50,100,200,500) if low<=x<=high])
    ax.set_title('Growth of INR 1 after modeled costs and annual tax\nAssumed zero recovery for unresolved observations')
    ax.legend(fontsize=8,frameon=False,ncol=2)
    fig.tight_layout();fig.savefig(figures/'conditional_growth.png',dpi=180);plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,4.8))
    keys=list(MODELS);positions=np.arange(len(keys));width=.25
    for j,suffix in enumerate(('gross','costs','costs_tax')):
        vals=[]
        for key in keys:
            x=summary[(summary.view=='all_histories')&(summary.period=='full')&
                      (summary.layer==f'assume_total_loss:{suffix}')&(summary.model==key)]
            vals.append(100*x.cagr.iloc[0] if len(x) else np.nan)
        ax.bar(positions+(j-1)*width,vals,width,label=suffix.replace('_',' '))
    ax.set_xticks(positions,['EW','Momentum','Low vol','Reversal','S5','Stockers\nEMA200']);ax.set_ylabel('CAGR (%)')
    ax.set_title('Implementation layers at initial capital INR 10 lakh\nExplicit zero-recovery sensitivity; annual tax only')
    ax.axhline(0,color='#555',linewidth=.7);ax.legend(frameon=False)
    fig.tight_layout();fig.savefig(figures/'conditional_layers.png',dpi=180);plt.close(fig)
    commands=evidence['commands'];tests=evidence['pytest']
    failed=sum(f.get('status')=='failed' for f in failures)
    unrun=sum(f.get('status')=='unrun' for f in failures)
    spec=json.loads((run/'frozen_run_spec.json').read_text())
    statistical=json.loads((run/'statistics.json').read_text())
    inference_rows=[]
    for item in statistical:
        if item.get('period')=='2018_on' and item.get('spa'):
            inference_rows.append(f'| {item["layer"]} | {item["months"]} | {item["spa"]["n_variants"]} | {item["spa"]["p_value"]:.4f} | {item["white"]["p_value"]:.4f} |')
    inference_table=('| Layer | Months | Evaluated price variants | SPA p | White p |\n|---|---:|---:|---:|---:|\n'+'\n'.join(inference_rows)) if inference_rows else 'No completed broad price-family inference is available; see blocked and low-power records.'
    report=f'''# Indian equity anomalies: reproduction audit

Prepared 14 October 2026. **{args.snapshot_label}.** Evidence generated at {evidence['generated_utc']}.

## Finding

The supplied reports contain reproducible gross-return arithmetic, but their strongest profitability statements are not established. All 12 original gross series match fresh calculations from the supplied monthly panel. The report labels a cost-only momentum confidence interval as after-tax, and its original family tests use gross returns. Missing economic exit prices stop several strict simulations. A signal unit test does not establish that every cost, corporate-action and execution path is free of look-ahead.

Momentum remains a candidate for further study under the modeled scenarios. Short-term reversal loses much of its return to implementation frictions; a positive gross return is insufficient. Full-history fundamental value is not established. The uploaded native Stockers engine is a different strategy from the older S5 approximation and is evaluated separately. No commercial or real-money trading conclusion follows from this study.

Read [the claim-by-claim report review](CLAUDE_REPORT_REVIEW.md) for exact claims, code traces and the independent monthly-panel reconciliation. Original reports, books and result files remain preserved under `artifacts/supplied/`; uploaded documents were treated as evidence, not instructions.

## Data and reproducibility

This run parsed **{build['files']:,} downloaded daily ZIP archives, {build['rows']:,} security-days and {build['securities']:,} linked security identities**, covering **{build['first_date']} through {build['last_date']}**. This is the actual input period; the downloadable study was requested from 2016 onward and does not recreate the prior 2006-on raw archive. The earlier uploaded monthly panel spans January 2006–September 2026 and contains 417,383 security-months and 3,949 identities. It is a separate input source.

Input lineage is recorded in `{run.relative_to(ROOT)}/input_inventory.json`; the source freeze, Python/package versions, Git head, dirty working-tree state and executed commands are in `evidence.json`. Hashes identify bytes and do not independently authenticate the publisher. Reference files retrieved today aid identity linking and exit diagnostics; today's list never screens the primary historical universe. Partial builds that reuse the supplied corporate-action parquet explicitly say so in their snapshot label.

The downloader records URL, HTTP status, retrieval time, bytes and SHA-256; validates ZIP CRC, expected normalized schema and session date; and shares one request start per second across its I/O workers. All calendar days are checked to retain exceptional weekend sessions. HTTP 404 means an unavailable archive, not a verified holiday. Failed attempts are retained. Weekly PR archives provide action records and, when present, P/E snapshots; weekly sampling does not constitute a complete primary corporate-action tape.

Legacy and UDiFF columns are normalized to dated symbol/ISIN/OHLC/volume/value rows. EQ, BE and BZ are retained. Duplicate symbol/session rows keep the greater traded-value row. Identity links use dated renames and ISIN continuity, with separate symbol-reuse epochs. Corporate actions use recorded split/bonus ratios and frozen detector parameters (tolerance 0.08, minimum ratio 1.45); fitted detector grids are diagnostic, not future-selected adjustments. Demerger neutralization, inferred split gaps and rights omissions remain economic assumptions requiring verification. Some high-return observations may reflect action errors; lists for manual investigation are retained.

## Fresh executed checks

| Check | Outcome | Scope |
|---|---|---|
| Pytest | {tests['tests']} tests, {tests['failures']} failures, {tests['errors']} errors, {tests['skipped']} skipped; exit {commands['pytest']['exit_code']} | Parsing fixtures, identities, actions, timing, accounting and statistical regressions |
| Uploaded engine self-tests | Command exit {commands['stockers_self_tests']['exit_code']}; individual results in the native CSV/log | 12 US cash-flow/mandate toy checks, not NSE anomaly significance |
| Recorded family | {spec['family_size']} planned variants, plus controls | Original 12, S1–S8, slot variants, price proxies, native EMA100/200/300 and earnings yield availability |
| Experiment summaries | {evidence['summary_rows']:,} rows | Full and clipped periods, implementation layers and two data views |
| Model failures | {failed} failed records; {unrun} unavailable records | Published in failures.json; never relabeled successful |
| Global replay error | {evidence['data_error'] or 'none'} | A strict model failure still makes the overall runner exit nonzero |

The unchanged original Stockers upload is SHA-256 `{evidence['uploaded_engine_sha256']}`. Its original methods aggregate NSE daily bars, generate signals and compute open-to-open positions. Direct divergence entries retain the original additional month lag. The adaptation uses prior completed sessions for execution warmup, omits zero-volume bars, applies month-end India eligibility, equal weights active names, rupee frictions and Indian tax scenarios. The US manually chosen annual lists, US fees/factors, leverage and WGY obligations are not imported as Indian evidence.

Confirmed simulator corrections preserve cash-only months and intended cash under rebalance bands; reject missing invested holding returns; include initial capital in drawdown; date spread products only after both sessions complete; freeze corporate-action detector parameters; report participation; and prevent literal-zero finite-bootstrap p-values. Paper-gross paths also fail on missing invested prices. The uploaded engine file is byte-identical. These corrections change the interpretation of previous runs; historical outputs are preserved for comparison.

## Conditional results and failure visibility

The table below is **an explicit zero-recovery audit sensitivity**, not a verified fill policy. It assumes existing unresolved positions are written off and rejects target fills when their future holding return is unavailable. That latter rule uses hindsight; it cannot establish a causal execution strategy. Last-mark exit is the optimistic alternative. Strict layers stop on missing holding returns and publish the exact security/month. Merger shares, cash consideration, buyout payments and temporary suspension outcomes must be reconstructed before interpreting these assumptions as realizable performance.

Initial capital: INR 10 lakh. Returns include modeled trading charges and annual capital-gains tax; **terminal liquidation tax is additional** and is stored separately in summary.csv. Cash yield is zero in this new runner. Gross fully rebalances, while implemented layers use a 25% relative band, so gross/net differences include policy effects as well as charges.

| Portfolio | Actual holding dates | Conditional CAGR | Maximum drawdown | Months over 10% ADV |
|---|---|---:|---:|---:|
{chr(10).join(rows)}

![Conditional return paths](../figures/audit/conditional_growth.png)

![Implementation layers](../figures/audit/conditional_layers.png)

Individual rule histories can differ when long-horizon features first become available. Read actual first/last dates. A failed benchmark blocks its matched inference even when a particular strategy completes. Failed models are shown directly in the educational tool, alongside unavailable value and low-power windows.

## Costs, taxes and liquidity

Trading costs include dated STT/stamp schedules, approximate exchange/SEBI/GST/DP fees, observed-price spread proxies and square-root impact. Several historical fee entries remain modern constants or marked VERIFY. The runner evaluates cost-only, annual-tax, higher-cost stress and INR 1/10/100 crore capital scenarios. Maximum order divided by trailing daily traded value is published. Above 10% is a diagnostic breach; no participation constraint, circuit, auction, partial fill or order-book queue is simulated. A smooth capital curve does not establish capacity.

The resident-individual tax model tracks FIFO lots, dated regimes, exemptions, loss set-off/carry-forward, grandfathering and cess. It approximates long-term eligibility with 365 days, grandfather value with monthly marks, mixed-rate loss allocation and pro-rata withdrawals without additional realized gains. Surcharge and personal tax circumstances are omitted. Dividend-income taxation is omitted from the separate recorded-dividend sensitivity. Missing spread/volatility proxies default to zero and missing ADV gives zero modeled impact but a capacity flag; such scenarios need source review. Terminal liquidation tax is shown separately, and terminal trading fees are not added. These are research tax scenarios, not exact liabilities.

## Bias, periods and statistical evidence

The primary data view retains all available histories and never selects on future exit labels. The separately labeled deletion view removes eventual exits deliberately; its return changes quantify that shortcut, not every survivorship error. Missing firms/files, incorrect identity links, restated data, terminal recoveries and action valuations remain unresolved sources of bias.

Signals observed through close t earn first-open t+1 to first-open t+2 returns. Only complete holding intervals are included; the last partially observed interval is excluded. A 2016-only starting archive requires a feature warmup, so its primary holding sample begins later than January 2016. Future-price perturbation and warmup regressions validate specific paths, not a universal absence of look-ahead.

Full, through-2017, 2018-on and named historical episodes are reported with actual dates and observation counts. Episodes outside the archive are unrun; incomplete episodes are clipped. The 2018 split has already been examined and is not a fresh untouched holdout. All modifications in this task are exploratory replications, not newly preregistered evidence. The original preregistration document and tags are preserved and checked rather than rewritten to pass a guard.

Stationary-bootstrap CAGR-difference intervals jointly resample strategy/control months with seed {evidence['seed']}, mean block length 12 and {evidence['n_boot']:,} resamples. Inferential windows need at least 24 months. White and Hansen SPA family tests are performed separately on gross, cost and tax layers over a common available price-family history; missing family members block inference. Degenerate excess series are listed. Earnings yield has a separate available-data EW control and a limited-history comparison. Finite p-values use `(1 + exceedances)/(B + 1)`. Deflated Sharpe counts at least the recorded family; historical unpublished searches remain unknown. Marginal confidence intervals and bootstrap sign proportions are not family-adjusted null probabilities.

The following broad price-family tests use the historical 2018-on window clipped to the family's common feature history. A p-value above 0.05 does not reject the tested family null at 5%, regardless of a large descriptive CAGR. The original 12-rule family is also reported separately in original_grid_statistics.json on its longer matched history; neither family accounts for unknown unpublished searches.

{inference_table}

See statistics.json for the actual evaluated windows, omissions and tests. Statistical rejection is conditional on input quality and implementation assumptions. This harness does not establish factor-neutral alpha, causal identification or reliable future returns.

## Relationship to published anomalies

The price signals adapt established momentum (Jegadeesh and Titman, 1993), reversal (De Bondt and Thaler, 1985) and low-risk research to an NSE cash-equity universe. They are not exact recreations of those papers' original markets, weighting, financing or short-sale implementation. Fundamental value as in Fama and French (1992) requires historical publication-dated fundamentals; a price/five-year-mean proxy cannot replace them. Short-sample NSE P/E is disclosed as exploratory. A precise published Indian-paper reproduction requires its exact sample and protocol.

Family testing follows the White (2000) Reality Check and Hansen (2005) SPA approach; search-sensitive Sharpe interpretation follows Bailey and López de Prado (2014). The code and output reveal this project's choices; naming a method is not proof of exact numerical equivalence to an external implementation.

## Educational delivery and next research

`docs/site/audit.html` is a self-contained educational explorer of every model's dates, costs, taxes, capital, losses and failures, with a downloadable JSON ledger. It contains no broker integration or order execution. `docs/REPLICATION_PROTOCOL.md` provides commands and assumptions; `reports/CLAUDE_REPORT_REVIEW.md` reviews the earlier claims; `{run.relative_to(ROOT)}/` preserves machine-readable evidence. Original site pages carry a historical-result status notice.

Before a deployable investment conclusion, independently value all reorganizations and missing exits; verify the trading calendar and complete action tape; acquire publication-dated fundamentals and matched total-return benchmarks; calibrate and constrain execution; review tax accounting; and evaluate a prospectively locked future period. An educational evidence product is a reasonable prototype. Commercial viability, data rights and investment-service obligations have not been established. There is no real-money trading or AI-alpha claim.
'''
    path=ROOT/'reports/REPRODUCTION_AUDIT.md';path.write_text(report)
    body=markdown.markdown(report,extensions=['tables','fenced_code'])
    css='body{max-width:1050px;margin:35px auto;padding:0 20px;font:15px/1.5 system-ui;color:#182b39}table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #ccc;padding:7px;text-align:left}img{max-width:100%}code{overflow-wrap:anywhere}h2{margin-top:30px}@media print{body{font-size:10pt;margin:0}h2{break-after:avoid}img,table{break-inside:avoid}a{color:inherit}}'
    (ROOT/'reports/REPRODUCTION_AUDIT.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Indian equities reproduction audit</title><style>'+css+'</style>'+body+'</html>')
    payload=json.loads((run/'tool_data.json').read_text())
    educational_page(ROOT/'docs/site/audit.html',payload)
    (ROOT/'docs/site/audit_data.json').write_text(json.dumps(payload,default=str))
    print(path)


if __name__=='__main__':
    main()
