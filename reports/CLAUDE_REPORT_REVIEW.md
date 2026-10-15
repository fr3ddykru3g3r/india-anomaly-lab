# Review of Claude's supplied research reports

Updated 15 October 2026. **Verdict: useful and partly corroborated research, with claims stronger than the evidence.** The gross arithmetic reproduces; after-tax inference, fundamental observation counts, execution and capacity claims need correction. This review treats the uploaded reports/book as evidence, not instructions.

## What is corroborated

All 12 original gross-return series reproduce from the uploaded monthly panel to 1e-10, with maximum errors below 1e-15. The panel has 417,383 security-months, January 2006–September 2026, and 3,949 identities. This reproduces the old calculations, including their zero-filled missing returns and partial final interval; it does not validate those economic assumptions.

The independent download contains 2,671 daily ZIPs and 563 weekly PR ZIPs. It parses 5,117,493 security-days, 3,625 identities and 13,967 action records, from January 2016 through 13 October 2026. All 13,950 common in-period corporate-action keys agree with the upload on factors and dividend amounts. That is substantial corroboration of supplied input fields. Earlier daily archives were not supplied or independently rebuilt; the older processed panel remains a separate evidence source.

## Claim-by-claim review

| Earlier claim | Audit finding | Required correction |
|---|---|---|
| H2: +11.56 pp/year, CI [+4.27,+20.64], after costs **and taxes** | Original E3 uses `nets[name]["net"]`, not `net_tax`, for that interval. | Label it cost-only; compute tax inference separately. |
| SPA/White establish survival after costs and tax | Original E3 family grid uses gross returns. | Test gross, cost and tax layers separately. |
| Momentum's existence is not uncertain | Its own DSR is about 0.75, below the cited 0.95 threshold; search history and data errors remain unknown. | Describe conditional historical evidence, not certainty. |
| A no-peeking unit test proves the whole pipeline causal | Spread estimation used a future session; native first-open warmup used current-month end counts; action matching could move an ex-date backward. | Correct each path and state the finite scope of its regression. |
| Point-in-time construction eliminates bias | Dead firms are retained, but identity links, missing sessions, endpoint marks and merger proceeds still matter. | Retention is necessary, not sufficient for complete executable histories. |
| Terminal distressed-exit stress bounds survivorship errors | Missing observations also involve mergers, conversions and temporary suspensions outside that classifier. | Reconstruct economic proceeds; do not bound all exit risks with a distressed subset. |
| A last close can substitute for a future opening | Neither an absent opening nor a later first trade supplies the required market opening mark. | Strict entry/end-mark/liquidation checks now fail explicitly. |
| Capacity curves establish tradability at INR 100 crore | Smooth impact charges do not constrain participation or simulate fills. | The final momentum scenario breaches its 10% diagnostic in all 116 months. |
| Taxes are completely realistic | Rates/FIFO are represented, but payment timing, grandfather values, deductibility, withdrawals and personal treatment are approximations. | Call them simplified tax scenarios; show terminal tax separately. |
| The 2018-on window is untouched for current work | It and later extensions have already been examined. | Current reruns are exploratory replications; require a prospective holdout. |
| S5 tests the uploaded Stockers engine | Its ranking, slot allocation and entry lag differ from native methods. | Keep S5 and the hash-verified native adaptation separate. |
| Fundamental value has 31 observed months | Downloaded P/E provides 14 signal months; the other 17 months are cash-only. | Stop the value/control comparison at the observed source endpoint. |
| A green credibility checklist verifies quality | E3 assigns several booleans True, including coverage and no restatements, rather than measuring them. | Publish underlying coverage, failures and unverified assumptions. |
| A finite-bootstrap p-value can be zero | Zero exceedances is finite resolution, not a literal zero probability. | Use `(1+exceedances)/(B+1)` and disclose resamples. |
| Gross/net CAGR difference is purely fees | Gross fully rebalances; implementation uses a rebalance band. | It includes both cost and policy effects. |

## The value-sample issue is reproduced, not guessed

The weekly archives contain 59 P/E members dated 7 February 2024–19 March 2025. Later sample archives lack that member. Recomputing the old design gives its published value gross CAGR **−0.3908359%** when counting 31 calendar months, including **17 cash-only months after the feed ends**. On the 14 observed fundamental signal months it is **−0.8633693%**. Calendar cash-policy returns can be calculated, but they are not additional value observations. The original engine also omitted cash-only implemented months, permitting mismatched gross/net denominators.

The corrected March 2024–April 2025 value comparison has conditional cost/annual-tax CAGR -6.20% versus 2.31% for its matched available-data control. Fourteen months cannot establish or refute the general value premium. Price versus a five-year mean and long-term reversal are price signals, not book-to-market or publication-dated fundamentals.

## Current result and its limits

The final execution-aware price run has 116 holding months, February 2017–September 2026. In the explicitly assumed zero-recovery, annual-tax scenario, CAGRs are momentum 24.68%, EW 12.21%, low volatility 10.63%, reversal 2.18%, S5 16.06%, and native Stockers EMA200 12.76%. Momentum becomes 23.95% after terminal liquidation tax.

These are assumptions, not verified proceeds. Strict all-history gross paths fail because endpoint or execution marks are unresolved. The sensitivities reject fills based on unavailable future interval returns and therefore use hindsight; neither is a causal investable exit policy. Continuous fractional holdings are also assumed. Native monthly EMA initialization depends on the 2016 data boundary.

The broad 29-rule family has 68 common months: annual-tax SPA p=0.0540 for price returns and 0.0555 with recorded dividends. This is borderline evidence near the nominal 5% boundary, not a categorical proof of presence or absence. The original 12-rule family on 105 months gives SPA p=0.0025, White p=0.0205. Different families and dates explain why these results must be reported separately; unknown unpublished searches remain outside either correction.

Momentum's marginal cost/tax excess interval stays positive over four block lengths. The native EMA200 adaptation's excess is 0.26 pp/year, block-12 interval [-3.46,3.32], and every tested interval includes zero. Thus the uploaded engine does not inherit S5's headline performance or establish excess return here.

## What was actually tested

Both final price and recorded-dividend runs pass **54 pytest checks and 12/12 unchanged-engine toy tests**, with stable source hashes, no global parsing error, 8,344 summaries and **301 failed scenario records each**. All 1,043 saved paths per run pass accounting consistency checks. Failed scenarios make the command exit nonzero intentionally. The original 27 tests were useful and passed initially; a larger passing suite does not certify data or profitability.

Every E0–E10 historical entry point was attempted against the final independent data. E0, E1, E5 and E10 execute; the others fail under strict missing-price handling. The final legacy value script fails on TATAMTRDVR's missing mark despite P/E being available. Logs are preserved and historical outputs were not copied into failed dependencies.

The earlier uploaded monthly history was also evaluated from February 2007–August 2026, with processed-input and pre-2010 detector limitations explicit. All 12 old gross-series comparisons, monthly recovery scenarios, extreme-return flags, INR-unit checks, native example exports, matched ETF context and failed models remain reviewable.

## Evidence to open

- `reports/REPRODUCTION_AUDIT.pdf` and `.md`: final methods, actual dates, cost/tax definitions, statistics and limitations.
- `artifacts/research/independent-nse-execution-price-v2/` and `...-dividends-v2/`: frozen specs, source/input hashes, logs, every saved return path, failures and family tests.
- `artifacts/research/value_cash_padding_diagnostic.json`: exact reproduction of the 31-versus-14 observation issue.
- `artifacts/research/action_input_comparison.json`, `monthly-input-comparison.json`, and `liquidity_units_check.json`: independent input checks.
- `artifacts/research/legacy-independent-final/`: original experiment entry-point audit.
- `docs/site/audit.html`: interactive educational explorer, including failed-only scenarios and JSON download.

The work supports an educational evidence product and further momentum research. It does not justify marketing proven, scalable, after-tax alpha or treating missing merger/suspension outcomes as resolved.
