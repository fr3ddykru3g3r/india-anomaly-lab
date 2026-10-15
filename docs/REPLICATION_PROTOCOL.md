# Replication protocol and evidence boundaries

This protocol describes the new reproduction runner, not an alteration of the
original pre-registration. It is an exploratory replication written after the
repository's historical results were available. Do not create or change an
upstream pre-registration tag to make a test pass.

## Question and permissible conclusion

Do published momentum, reversal and low-risk effects remain after documented
implementation frictions in Indian cash equities? Fundamental value is a
separate question requiring dated financial-statement availability. A positive
backtest is a historical measurement, not an investable recommendation.

A reproducible result requires the exact original inputs, a frozen code/specification
hash, successful parsing and coverage audits, executable timing assumptions,
reported failures, a complete trial family, matched benchmark and time coverage,
and independently reviewed statutory/tax assumptions. Uploaded processed panels
have been reconciled, and independent NSE downloads from 2016 onward are being
rebuilt and tested. Each snapshot records its actual date coverage and input
lineage. Synthetic validation remains separate from real investment evidence.

## Run the project

Python 3.12 was used for this reproduction; the project requires Python 3.11+.
`requirements-research.lock.txt` records the exact installed Python package versions.
Binary wheels and operating-system dependencies can differ by platform.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-research.lock.txt
.venv/bin/python scripts/run_research_audit.py --mode synthetic
```

For the user's existing laptop checkout, copy this delivery into the project while
preserving its `data/` directory and local work, then run:

```bash
cd /Users/Kyrosah/Downloads/india-anomaly-lab
bash scripts/run_local_reproduction.sh "$PWD"
```

No repository is downloaded, reset or cleaned by that helper. Real mode rebuilds
panels from existing `data/raw/bhavcopy/**/*.zip`, PR archives, and reference files.
Explicit `--reuse-processed` can use cached `data/processed` panels after verifying lineage.
Keep the existing
Git checkout and its original `prereg-v1*` tags: the guard checks the original
document. A source-only archive does not substitute for this Git provenance.
Real mode never silently substitutes synthetic data. A nonzero exit plus a report
is the expected outcome when inputs or required tests are unavailable.

Use `--out NEW_DIRECTORY` to choose a fresh evidence directory and `--data-root`
to locate an existing `data` directory without moving it. The default output is
`artifacts/research/<UTC timestamp>/`. Existing evidence directories are never
overwritten. Raw and processed data can be large; allow enough memory for pandas
to load the daily panel (the historical report claims 8.35 million rows).

The runner executes the full pytest suite and the uploaded engine's self-test
command, preserves their actual exit codes, and writes JUnit XML, text logs,
source hashes, package versions, input hashes, a model ledger, monthly return
files, summary tables, bootstrap results, and an educational HTML tool. A failed
model remains in `failures.json`; unavailable windows are explicit. A script
exiting successfully does not validate any omitted experiment.

`scripts/freeze_inputs.py --out NEW_INPUT_DIRECTORY` freezes raw archive bytes
and rebuilds into a separate input tree; `--supplied-actions PATH` explicitly
uses a supplied action parquet and records that lineage. The independent build
omits that option and reads downloaded PR archives. The audit runner's optional
`--return-basis dividends` evaluates recorded dividend-inclusive returns as a
separate sensitivity; default is price returns. Source hashes are captured before
and after execution, and a changing source tree produces a nonzero exit.

## Input parsing and identity

The existing pipeline reads legacy and UDiFF NSE CSV members from ZIP archives,
normalizes dates and numeric fields, retains EQ/BE/BZ, and keeps the largest-value
row for a duplicated symbol/session. Missing ISINs are allowed in older files.
Security identity is linked across symbol changes and split-related ISIN changes;
a long gap plus a new identity is treated as symbol reuse. Funds are excluded.

Corporate-action records supply split/bonus factors. PREVCLOSE is not assumed
adjusted. Detector fallbacks, missing-session bridging, and demerger neutralization
are disclosed approximations. A demerger is not economically established as
value-neutral just because a price gap is removed. Rights issues, arbitrary
corporate reorganizations and unavailable exit proceeds require separate audit.
The known-truth fake archive tests splits, bonuses, dividends, a rename, a reused
symbol, an IPO, a failure, a missing file and both NSE formats. Passing those tests
does not prove the correctness/completeness of a real archive.

The runner computes SHA-256 of every supplied input and checks against stored
download manifests when present. A hash freezes bytes; it does not authenticate
their source. Processed files are not automatically trusted as a fresh rebuild.
If reusing processed files, verify their build lineage against the raw archive.

## Rules and execution

The original universe is the top 500 by trailing six-month median daily traded
value, subject to price/history/trading-day/activity filters known at month end.
It is a research universe, not a reconstruction of historical Nifty membership.
The exact universe can be inspected through source and frozen inputs.

The planned family is 30 variants plus the EW control: the original
12 momentum/reversal/low-volatility/52-week-high variants, S1–S8, S5 with 20 and
50 slots, four long-horizon price signals, uploaded Stockers EMA100/200/300,
and earnings yield when PR P/E snapshots exist (with its own available-data EW control).
Rules without enough history are marked unrun. Price-to-five-year-mean and
long-term reversal are **not fundamental value**. No full-history book-to-market,
publication-lagged earnings-yield, factor-neutral alpha or short-sale claim is made.

The Stockers bridge uses the original `UltimateSimpleSymbol` class on causally
adjusted NSE daily OHLCV, then calls its monthly aggregation, signal generation
and position/open-to-open return engine. The original upload remains byte-identical.
Its direct divergence signal examines the previous completed month and its
position routine shifts that signal again: preserve this extra lag, rather than
quietly replacing it with the pre-existing S5 rule. A disclosed correction gates
execution warmup by prior completed sessions, rather than the execution month's
end-of-month observation count. Zero-volume daily bars are omitted from native
execution and warmup; they cannot establish an executable open. India adds monthly eligibility, equal weighting,
rupee frictions and taxes. US yearly lists, leverage, US factor regressions and
WGY client obligations are not imported as Indian evidence.

India weights at signal close t earn first-open t+1 to first-open t+2 returns.
Adjusted price levels use observed historical factors rather than future
back-adjustments. Spread estimates use only completed two-session pairs.
Prices for nonfilled/untradable orders must be audited; missing held-stock returns
raise an error, in paper-gross paths as well as implemented portfolios. Fully
exited positions do not need the following holding interval's return. Complete
holding intervals require the next month's opening observations; the final
partial interval is excluded. The monthly tax/cost date is the first calendar day of the month,
an approximation to the actual first trading session. No auction fill, queue,
circuit-limit, suspension, borrow, market-order or partial-fill engine is claimed.

## Frictions and capacity

The tool presents gross, cost-only, after-cost/after-tax, a higher-cost stress,
and INR 1/10/100 crore cost-only capital scenarios (INR 10 lakh baseline).
Gross fully rebalances; implementation layers apply the original 25% relative
band, so their difference also includes rebalance-policy effects.

The cost formula combines STT, stamp duty, exchange and SEBI fees, GST, DP fees,
half-spread and square-root impact. It is not a completely verified historical
schedule: pre-2020 stamp duty is approximated, historical non-STT exchange/DP/GST
rates use modern constants, and several config rows are explicitly `VERIFY`.
Costs should be bracketed and reviewed against dated primary circulars.

Order size divided by trailing daily traded value is recorded for every rebalance.
Above 10% is flagged as a capacity breach, **not** assumed executable. The runner
does not enforce participation limits or invent a partial-fill return. ADV, impact
coefficients and spread proxies are estimates; model numbers at INR 100 crore
are sensitivities, not demonstrated scalable capacity.

The resident-individual FIFO tax engine includes dated rates, annual exemptions,
grandfathering, loss set-off/carry-forward and cess, but retains approximations:
long-term status uses 365 days rather than the precise legal anniversary rule;
grandfathering uses a monthly price mark rather than security-level statutory FMV;
mixed-rate-year loss allocation is weighted; tax withdrawals shrink holdings/basis
without realizing further gains; transaction-cost deductibility is simplified;
surcharge and personal circumstances are omitted; final liquidation does not pay
a second set of trading fees. This is a tax sensitivity, not an exact liability.
Do not describe its after-tax result as production-grade tax accounting.

## Bias, periods and statistics

All supplied security histories are retained in the primary replay. The separate
`delete_exited_biased` run deliberately removes securities using **future** exit
labels: it measures one survivorship shortcut, not all survivorship bias. Future
exit labels never screen the primary investable universe. Delisting proceeds,
terminal-price recoverability, missing files, identity links, corporate actions
and unavailable histories still require real-data validation. Last-mark and
zero-recovery scenarios explicitly publish assumed exits. They reject new
target fills with missing future holding returns, so they use hindsight as audit
sensitivities rather than define a causal execution policy. Neither sensitivity
substitutes for verified merger consideration or delisting proceeds.

Report full, through-2017, 2018-on and the repository's named historical episodes.
The 2018-on period has already appeared in earlier reports; it is a historical
split, not an untouched future holdout. Fundamental value's short 2024-on P/E
sample, when available, is analyzed against its own matched eligible
control, with publication timing and negative/missing earnings disclosed.

Use jointly resampled monthly stationary-bootstrap CAGR differences with expected
block length 12, fixed seed and 1,999 resamples. Windows below 24 months do not
receive inferential tests. White Reality Check and Hansen SPA assess the common
available family separately for gross, costs and tax layers. Zero-variance models
cannot be studentized and are listed explicitly. Finite-bootstrap p-values use
`(1 + exceedances)/(B + 1)`, never zero. Deflated Sharpe uses the evaluated layer's
excess-return dispersion and at least the planned 30 trials, but the total
historical Stockers search count is **unknown**. Current-family correction does
not erase earlier unpublished searches. Confidence intervals are marginal;
bootstrap sign proportions are not null-centered hypothesis p-values.

No automatic claim of survival is produced. Require economically meaningful,
capacity-feasible, matched-period outperformance after realistic costs; uncertainty
after family correction; robustness to block lengths, liquidity floors, genuine
delisting outcomes, verified tax schedules and total-return choices; and a new,
locked future evaluation. The current harness does not implement every robustness
experiment in this list. Paper long–short results in the older study are academic
spreads; Indian retail cash-equity shorting constraints preclude assuming them
implementable.

## Product boundary

The initial product is an evidence and education workbench: dataset provenance,
strategy definitions, fill assumptions, a full failed-model registry, cost/tax/
liquidity sensitivities, uncertainty and downloadable reports. Do not promise
returns, connect a broker, place trades or use “AI-powered alpha” claims.
Before any commercial release, resolve data/software rights, independent validation,
security, jurisdiction-specific advice/research rules and factual user disclosures.
Commercial viability has not been tested by this research.

## Fundamental observation endpoints

The downloaded weekly PR archive currently supplies P/E snapshots only from
February 2024 through March 2025. The value portfolio and its matched control
stop at the last observed fundamental signal month; no later cash-only months
are counted as value observations. This yields 14 holding months, March 2024–
April 2025, below the 24-month inference threshold. Dividend-inclusive holdings
are a sensitivity of recorded payouts and omit dividend-income tax. Corporate
actions are attached only on or after their recorded ex-date; source errors are
not repaired by moving actions into earlier sessions.
