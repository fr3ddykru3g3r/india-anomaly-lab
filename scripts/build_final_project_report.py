"""Publish a synthesis from frozen executed runs; never launches or tunes backtests."""
from pathlib import Path
import hashlib, json, os
import pandas as pd
import markdown

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
RUN = Path('artifacts/research/independent-nse-execution-price-v2')
OUT = Path('artifacts/research/final-project-report')
OUT.mkdir(parents=True, exist_ok=True)
x = pd.read_csv(RUN / 'summary.csv')
layer = 'assume_total_loss:costs_tax'
d = x[(x.view == 'all_histories') & (x.layer == layer) & (x.period == 'full')].copy()
fair = d[d.months == 116].sort_values('cagr', ascending=False)
assert fair.iloc[0].model == 'S2'
assert len(d) == 32 and not d.model.duplicated().any()
checks = json.loads((RUN / 'output_path_checks.json').read_text())
assert len(checks) == 1043 and all(c['passed'] for c in checks)
fair.to_csv(OUT / 'matched_116_month_ranking.csv', index=False)
d.sort_values(['months', 'cagr'], ascending=False).to_csv(OUT / 'all_models_with_dates.csv', index=False)
e = json.loads((RUN / 'evidence.json').read_text())
assert e['pytest'] == dict(tests=54, failures=0, errors=0, skipped=0)
assert not e['source_changed_during_run']

def pct(v):
    return '—' if pd.isna(v) else f'{100*v:.2f}%'
def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---']*len(headers)) + ' |'] + ['| ' + ' | '.join(map(str,row)) + ' |' for row in rows])
def row(model):
    return d.set_index('model').loc[model]
labels = {'S2':'S2: momentum, top 30','S3':'S3: momentum + trend, top 30', 'momentum12_top10':'12–1 momentum, top decile','low_vol12_top10':'12-month low volatility, top decile', 'S5':'S5: earlier team approximation', 'Stockers_EMA300':'Native Stockers, monthly EMA300','Stockers_EMA200':'Native Stockers, monthly EMA200', 'EW':'Same-universe equal weight','reversal_top10':'One-month reversal, top decile'}
selected = ['S2','S3','momentum12_top10','S5','Stockers_EMA300','Stockers_EMA200','EW','low_vol12_top10','reversal_top10']
headline = table(['Strategy','Annual-tax CAGR','After terminal tax CAGR','Maximum drawdown'], [[labels[m],pct(row(m).cagr),pct(row(m).terminal_after_tax_cagr),pct(row(m).max_dd)] for m in selected])
rank_table = table(['Rank','Model ID','CAGR','Max drawdown','Sharpe, zero cash rate'], [[i+1,r.model,pct(r.cagr),pct(r.max_dd),f'{r.sharpe_zero_rf:.2f}'] for i, r in enumerate(fair.itertuples())])
short = d[d.months != 116]
short_table = table(['Model','First holding month','Last holding month','Months','CAGR'], [[r.model,r.first,r.last,r.months,pct(r.cagr)] for r in short.itertuples()])
base = x[(x.view=='all_histories') & (x.period=='full')].set_index(['model','layer'])
layer_table = table(['Model','Gross','Costs','Annual tax','Higher-cost stress'], [[m]+[pct(base.loc[(m,'assume_total_loss:'+l),'cagr']) for l in ['gross','costs','costs_tax','stress']] for m in ['S2','momentum12_top10','EW','low_vol12_top10','reversal_top10']])
periods = x[(x.view=='all_histories') & (x.layer==layer) & x.model.isin(['S2','momentum12_top10','EW','low_vol12_top10','reversal_top10']) & x.period.isin(['2018_on','2020 Covid','2021-24 retail boom'])]
periods.to_csv(OUT/'period_comparison.csv',index=False)
period_table = table(['Window','Model','First','Last','Months','CAGR','Max drawdown'], [[r.period,r.model,r.first,r.last,r.months,pct(r.cagr),pct(r.max_dd)] for r in periods.itertuples()])
# Every plotted wealth endpoint is checked against the published summary.
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
fig, ax = plt.subplots(figsize=(10,5))
for m in ['S2','momentum12_top10','S5','EW','low_vol12_top10','reversal_top10']:
    r = pd.read_csv(RUN / f'returns/all_histories-{layer}-{m}.csv')
    assert len(r)==116
    calculated = (r.wealth.iloc[-1]/1e6)**(12/len(r))-1
    assert abs(calculated-row(m).cagr)<1e-10
    dates=pd.to_datetime(r.month+'-01')
    ax.plot([pd.Timestamp('2017-01-01'),*dates], [1,*list(r.wealth/1e6)],label=labels[m])
ax.set_yscale('log'); ax.yaxis.set_major_formatter(FuncFormatter(lambda v,pos:f'{v:g}×'))
ax.set(title='Conditional growth after modeled costs and annual tax',ylabel='Wealth / initial INR 10 lakh')
ax.grid(alpha=.2);ax.legend(fontsize=8);fig.tight_layout()
fig.savefig('figures/audit/final_strategy_comparison.png',dpi=180);plt.close(fig)
text = f'''# India Anomaly Lab — final project report

15 October 2026 · Research and educational backtesting · Independent NSE replay

## Which strategy was best?

**S2, the simple 12–1 momentum portfolio holding up to 30 stocks, had the highest simulated after-cost/annual-tax CAGR among strategies with the same 116-month history: 27.85% per year.** After modeled terminal liquidation tax it was 26.98%. Its maximum drawdown was **48.68%**. This is the observed winner of an already examined model family, not a prospectively validated selection.

**Momentum was the strongest research candidate; no strategy has yet been established as an executable winner.** All strict all-history gross paths stop on missing invested prices or execution marks. The positive rankings below come from explicitly assumed recoveries and missing-entry handling. Some of that handling consults future interval availability, so even the zero-recovery sensitivity is not a causal execution backtest or a lower bound on achievable returns.

The simpler momentum decile returned 24.68% with a 46.96% drawdown. Adding the S3 trend filter reduced S2's return to 26.76% and did not reduce its worst drawdown. The S5 setup approximation returned 16.06%. More elaborate filters did not beat simple S2 on this matched full history. Among the three native Stockers adaptations, EMA300 had the highest full-history CAGR, 13.20%; EMA200 returned 12.76%. These use the uploaded engine's monthly EMA spans, not the daily EMA200 in S3/S5.

**For lower observed losses, 12-month low volatility had the smallest maximum drawdown among these matched-history strategies, 18.42%, with 10.63% CAGR.** That is a different objective from maximizing return. Short-term reversal was weak after frictions and lost money under higher-cost stress. Fundamental value has only 14 observed holding months and cannot support a full-history verdict.

## Results readers can compare

All rows below use February 2017–September 2026, 116 completed holding months, initial INR 10 lakh, price returns, all available histories, and `assume_total_loss:costs_tax`. Annual-tax CAGR includes modeled periodic tax payments; the separate terminal column deducts additional estimated liquidation tax. Terminal trading fees are omitted. Drawdowns use the annual-tax path before terminal liquidation. These are historical annualized rates, not yearly promises.

{headline}

![Same-period strategy comparison](../figures/audit/final_strategy_comparison.png)

The zero-recovery assumption writes off existing unresolved exposures. Unavailable target fills are rejected, including checks using future holding-return availability. Last-mark sensitivities are retained separately. Neither assumption reconstructs actual merger proceeds, conversion shares or suspension outcomes. Fractional shares, unrestricted fills and approximate tax/cost inputs further limit executability. No real-money trading was performed.

## Do costs erase the anomalies?

{layer_table}

Costs substantially reduce reversal: 10.63% gross becomes 2.70% after modeled trading costs and 2.18% after annual tax; the higher-cost stress is −7.26%. Momentum remains positive under the tested formulas: S2 is 23.53% in higher-cost stress, and the momentum decile is 21.07%. Persistence under an assumed formula does not establish realizable profitability. Gross uses full rebalancing; implemented layers use a 25% relative rebalance band, so the gaps include policy changes as well as charges. The higher-cost stress retains annual tax and sets impact coefficient k=1.5, brokerage=0.003 and minimum half-spread=0.001; it is a separate simulation, not an arithmetic subtraction from this table.

At INR 100 crore, momentum decile's cost-only CAGR is 18.52%, but it exceeds the 10% daily traded-value diagnostic in all 116 months; the largest order is approximately 7.07 times the proxy. S2's corresponding CAGR is 17.76%, so the return ranking also changes with capital. The engine reports breaches rather than enforcing partial fills. No capacity claim follows from these curves.

## Different periods and statistical uncertainty

{period_table}

The episode table is clipped to the exact dates shown; its 11-month Covid window is a short descriptive sample, and its annualized CAGR is not a full-year observation or an inference result.

The 2018-on window contains 105 months: S2 returns 22.69%, momentum decile 20.28% and EW 9.58%, under the same annual-tax sensitivity. This window has already been examined and is not an untouched holdout. The independently downloaded archive cannot test the 2008 crisis or 2013 taper episode. Separate runs on uploaded processed history cover February 2007–August 2026, 235 months: momentum decile 16.38%, EW 9.26% and reversal −9.49%. Those older years lack independently rebuilt daily opening evidence and are reported separately.

The original 12-rule family on January 2018–September 2026 has conditional annual-tax SPA p=0.0025 and White p=0.0205. The broader 29-price-rule family must use its shorter common history, February 2021–September 2026, 68 months: SPA p=0.0540 and White p=0.0095. With recorded dividends its SPA p is 0.0555. These different tests/windows answer different questions. Near-threshold values with 1,999 bootstrap resamples are not definitive; the original historical search count remains unknown.

For the momentum decile versus matched EW over the original-grid window, the annualized CAGR difference is 10.70 percentage points, with stationary-bootstrap block-12 interval [3.33, 20.31]. All tested block lengths 3/6/12/24 keep that interval positive. Native Stockers EMA200's excess is only 0.26 points, interval [−3.46, 3.32]; all four intervals include zero. A marginal interval for one rule is not a family-adjusted probability that the selected S2 winner will work. Factor-neutral alpha, exact Indian-paper replication and a genuinely unseen prospective test remain unestablished.

## How the project was made

1. Preserved the uploaded programs, reports and result files unchanged under `artifacts/supplied/`, with hashes. Documents were treated as evidence rather than instructions. Kept the original 7,108-line Stockers upload byte-identical and invoked its own signal/position methods through an offline India adapter.
2. Downloaded NSE archives from 1 January 2016 through 13 October 2026. Checked all 3,939 calendar dates to retain exceptional weekend sessions. Preserved 2,671 daily ZIPs and 563 weekly PR ZIPs with URL/status/time/byte-count/SHA-256 manifests and CRC/schema/date validation. Unavailable 404 archives are not independently authenticated holidays.
3. Parsed old and UDiFF formats into dated prices, OHLC, volume and INR turnover. Retained EQ/BE/BZ, resolved duplicate symbol/session rows by traded value, and linked dated renames/ISINs with symbol-reuse epochs. Built 5,117,493 security-days, 3,625 linked identities and 13,967 action records. Froze input inventories before running models.
4. Applied recorded splits/bonuses and frozen action-detector settings. Compared 13,950 common supplied/downloaded action keys: factors and dividends agree within 1e-8. Verified implied traded-value units across all 5,117,493 traded rows. Retained unresolved extreme-return and overnight-gap investigations rather than deleting them silently.
5. Constructed monthly features and a historical liquidity universe: up to 500 non-fund names, price at least INR 20, at least 10 traded sessions in the signal month, at least 13 observed historical months, ranked by the trailing six-month median daily traded-value proxy. This is a historical liquidity universe, not historical Nifty 500 membership. The broad download itself is not a claim of perfect all-equity coverage.
6. Computed signals at month-end t and required observed market-first-session openings at t+1 and t+2. Excluded incomplete final intervals. 2016 is primarily feature warmup; the common main holding sample starts February 2017. S2 ranks adjusted price(t−1)/price(t−12)−1, targets 1/30 per selected name and leaves unused slots in zero-yield cash. Momentum `top10` means the top decile, not ten stocks; `top20` means top quintile.
7. Ran 30 planned variants plus two controls through 21 implementation layers and two universe views. The intentionally biased view deletes eventual exits only to demonstrate survivorship effects. Strict mode fails on unresolved invested marks; explicit last-mark and zero-recovery sensitivities remain separately labeled. Applied trading-cost, annual-tax, stress and capital scenarios and saved every completed monthly path and failure.
8. Reconciled the old outputs, executed all E0–E10 entry points in isolated directories, ran software/native-engine checks, inspected output accounting, performed separate matched-window statistical tests, and built a browser-tested educational explorer. No broker integration or live orders were used.

The uploaded native engine retains its original additional direct-entry month lag, monthly aggregation and EMA spans. Its India adapter uses completed-session warmup and historical eligibility, and applies rupee costs/tax. The earlier S5 approximation has different ranking, slot and timing rules; it is not a native-engine reproduction. Nine exported examples reconcile 272 active observed native monthly returns to the India underlying calculation within 1e-8, with maximum discrepancy approximately 1.44e-15. This checks specific calculations, not every line or missing-price liquidation.

## What was wrong or overstated in Claude's reports?

All 12 original gross-return series reproduce from the supplied monthly panel to within 1e-10. Their arithmetic was not invented. But the original cost-only momentum confidence interval was labeled after-tax, and original family significance tests used gross returns. A hardcoded credibility flag was not a measured test. The reported deflated Sharpe did not support the strongest certainty claim.

The old value calculation counted 31 calendar months including 17 cash-only months after the fundamentals feed ended. Only 14 observed signal months exist, producing March 2024–April 2025 holdings. Corrected conditional after-cost/annual-tax value CAGR is −6.20%, versus +2.31% for its available-data EW control; inference is unrun below 24 months. Five-year price cheapness and long-term reversal are price proxies, not fundamental value.

The audit also corrected future spread-pair timing, backward ex-date matching, native warmup timing, cash-month accounting, missing-invested-return handling, initial-capital drawdowns, finite-bootstrap p-values and April tax withdrawal after assumed losses. The broad “no look-ahead” claim exceeded what the old tests demonstrated. Smooth impact curves did not establish liquidity capacity. The full claim review, including favorable corroboration and exact limitations, is appended below.

## Proof of work and how to inspect it

These are executed artifacts, not proposed tests. Each of the price and recorded-dividend final runs has **54/54 software tests passing, 12/12 original native toy checks passing, 1,043 completed monthly paths, 8,344 summary rows and 301 failed model/scenario records**. No global parsing error occurred and numerical source hashes were unchanged during each run. All 1,043 saved paths per run pass finite-return, nonnegative-charge and wealth-recursion checks. The runner intentionally exits 1 when model failures remain. Those failures do not mean the software tests failed, and passing software tests do not prove profitable trading.

32 portfolios × 21 layers × 2 views = 1,344 attempted combinations = 1,043 completed + 301 failed. The 8,344 summaries are eight period views of completed paths, not 8,344 independent hypotheses. Every original E0–E10 entry point was attempted: E0/E1/E5/E10 exit 0; E2/E3/E4/E6/E7/E8/E9 exit 1 under strict missing-price handling. No old outputs were copied in to complete failed runs.

| Evidence | Inspect this artifact relative to repository root |
| --- | --- |
| Exact final ranking, dates and cost/tax scenarios | `artifacts/research/independent-nse-execution-price-v2/summary.csv` |
| New matched ranking and period extraction | `artifacts/research/final-project-report/` |
| Actual failed security/month reasons | `artifacts/research/independent-nse-execution-price-v2/failures.json` |
| Test result and logs | same run: `tests.xml`, `pytest.log`, `stockers_self_tests.log` |
| Actual commands, environment and run-time source hashes | same run: `evidence.json`, `source_at_start.json` |
| Frozen input names, sizes and hashes | same run: `input_inventory.json` |
| Every monthly wealth/cost/tax path | same run: `returns/` |
| Accounting checks and statistical evidence | same run: `output_path_checks.json`, `statistics.json`, `original_grid_statistics.json`, `block_length_sensitivity.csv` |
| Raw retrieval provenance | `data/raw/manifest.csv`, `manifest_pr.csv`, `download_attempts.csv` |
| Actual parsed panels and lineage | `artifacts/research/independent-nse-inputs/processed/`, `lineage.json` |
| Interactive-tool browser checks | `artifacts/research/browser-check/browser_results.json`, desktop/mobile screenshots |
| Legacy experiment execution logs | `artifacts/research/legacy-independent-final/` |
| Unchanged user uploads | `artifacts/supplied/upload_manifest.json` and original files |

Original Stockers SHA-256: `b037f944b3bff5db60d01552582c80ce65a4234b1e7f1bb21775418ab5f00c9a`. Base Git revision: `71658c943d7b0e975d9e7d24ba88bc90614d826c`; audited changes are a local source overlay, not a new remote release. Python 3.12.14 and `requirements-research.lock.txt` record the environment. Hashes prove byte identity, not independent publisher authenticity. This report generator saves a focused evidence hash manifest and independently recalculates plotted CAGRs from the saved monthly wealth paths. It does not rerun or tune strategies.

To reproduce from the extracted input archive, from the project checkout and installed locked environment:

```bash
.venv/bin/python scripts/run_research_audit.py --mode real --reuse-processed \\
  --data-root /path/to/nse-inputs-2016-2026 --out artifacts/research/NEW_RUN
```

Use a new output directory. Preserve both success paths and `failures.json`, including the deliberate nonzero overall exit. See `docs/REPLICATION_PROTOCOL.md` for details. The educational explorer is `docs/site/audit.html`; its downloaded ledger exposes all 8,344 rows and 301 failures. Browser verification found no page errors or outer mobile overflow and checked filters, bias warnings, strict failures and JSON export.

## What this project establishes, and what comes next

The project is a reproducible exploratory evidence base and educational prototype. It corroborates old gross arithmetic, demonstrates substantial friction costs for reversal, identifies momentum as the strongest conditional candidate, and exposes material execution and inference gaps. It does not establish clean value evidence, a native Stockers advantage or commercially deployable alpha. It has no Stanford affiliation or external academic validation.

Before an investable conclusion, reconstruct merger/share-conversion and suspension outcomes; verify unexplained action gaps and calendar coverage; obtain publication-dated fundamental history; enforce integer shares, participation limits and real execution rules; calibrate historical fees and review tax treatment; and freeze a prospective evaluation period. The existing educational tool could support a research product, but startup demand, licensing and investment-service obligations have not been validated. Avoid marketing the historical winner as “AI-powered alpha.”

## Complete matched-history model ledger

These are all portfolios with the same 116-month full history, ranked by the annual-tax sensitivity used above. Sharpe assumes a zero risk-free rate. The return winner also leads this particular Sharpe column, but neither is an adjusted test of the selected winner.

{rank_table}

The remaining models have shorter input histories and are not ranked against the 116-month sample:

{short_table}

## Technical audit appendix

The following is the full reproduction audit, retained with its detailed source, bias, tax, benchmark and dividend discussion.

'''
audit=Path('reports/REPRODUCTION_AUDIT.md').read_text()
review=Path('reports/CLAUDE_REPORT_REVIEW.md').read_text()
# Shift appendix headings down one level without altering source reports.
text += '\n'.join('#'+line if line.startswith('#') else line for line in audit.splitlines())
text += '\n\n## Claim-by-claim review appendix\n\n'
text += '\n'.join('#'+line if line.startswith('#') else line for line in review.splitlines())
Path('reports/FINAL_PROJECT_REPORT.md').write_text(text+'\n')
body=markdown.markdown(text,extensions=['tables','fenced_code'])
css='''body{font:15px/1.55 system-ui,sans-serif;color:#18242c;max-width:1050px;margin:40px auto;padding:0 24px}h1,h2,h3{line-height:1.2;color:#153d53}h2{margin-top:36px}table{border-collapse:collapse;width:100%;font-size:12px;margin:18px 0}td,th{padding:7px;border:1px solid #ccd6de;text-align:left}th{background:#edf3f7}img{max-width:100%}code{overflow-wrap:anywhere;font-size:.86em}pre{white-space:pre-wrap;background:#f1f4f6;padding:14px}a{color:#165a84}@media print{body{font-size:10px;margin:0;padding:0}h2,h3{break-after:avoid}tr,img{break-inside:avoid}table{font-size:8px}pre{font-size:9px}h2{margin-top:24px}}'''
Path('reports/FINAL_PROJECT_REPORT.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>India Anomaly Lab — Final project report</title><style>'+css+'</style><body>'+body+'</body></html>')
files=[RUN / name for name in ['summary.csv','evidence.json','tests.xml','failures.json','input_inventory.json','statistics.json','original_grid_statistics.json','output_path_checks.json']]
files += [Path('reports/REPRODUCTION_AUDIT.md'),Path('reports/CLAUDE_REPORT_REVIEW.md'),Path(__file__).relative_to(ROOT)]
manifest={'generated_from':'existing frozen executed outputs; no numerical rerun','selection':{'view':'all_histories','layer':layer,'period':'full','matched_months':116},'winner':'S2','assertions':['32 portfolios represented','54 software tests passed','1043 saved paths passed accounting checks','plotted CAGRs match saved summary within 1e-10'], 'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(OUT/'proof_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'winner':fair.iloc[0].model,'matched_portfolios':len(fair),'other_sample_portfolios':len(short),'report_bytes':len(text.encode()),'proof':str(OUT/'proof_manifest.json')}))
