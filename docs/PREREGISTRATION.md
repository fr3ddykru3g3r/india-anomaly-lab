# Pre-registration v1

**Status:** frozen at git tag `prereg-v1`. Real-data anomaly returns are computed only after this tag (the experiment scripts refuse to run on real data otherwise, and refuse again if this file changes after the tag). Any later change goes in the *Amendments* section with a date and reason, followed by a new tag.

**Data seen before freezing:** the NSE files were downloaded and audited (coverage, identities, corporate actions; `experiments/e1_data_coverage.py`). The audit computes **no** strategy or anomaly returns. The code was dry-run only on a synthetic market with known truth.

## Question

Do well-known price-based anomalies in Indian equities survive point-in-time data, dated Indian costs and taxes, realistic liquidity, and correction for the number of variants tried?

## Hypotheses

| ID | Hypothesis | Test | Supported if |
|---|---|---|---|
| H1 | 12-1 momentum (top minus bottom decile) earns a positive gross return | Newey-West t on monthly long-short returns, full period | t > 1.96 |
| H2 | The momentum top decile beats the same-universe equal-weight (EW) portfolio after costs and taxes | Test-period (2018+) net-of-cost CAGR difference vs EW net of cost; 95% stationary-bootstrap interval | lower bound > 0 |
| H3 | The best variant in the grid beats EW after correcting for every variant tried | Hansen SPA test (primary) and White Reality Check, gross long-only variants vs EW, full primary sample | SPA p < 0.05 |
| H4 | The low-volatility top decile (lowest 12-month daily volatility) has a higher Sharpe ratio than EW | 95% bootstrap interval of the gross Sharpe difference, full period | lower bound > 0 |
| H5a | 1-month reversal (last month's losers minus winners) is profitable gross | Newey-West t, full period | t > 1.96 |
| H5b | The reversal top decile does **not** beat EW after costs | Same test as H2 | interval includes or lies below 0 |
| H6 (method) | Deleting dead companies inflates equal-weight backtests more than momentum and low-volatility backtests, as the synthetic study (E0) predicts | Bias at 100% deletion of distressed exits, E4 | EW bias > momentum bias and EW bias > low-vol bias |

**Pre-registered failure criterion ("does not survive"):** the long-only top-decile portfolio's net-of-cost CAGR minus same-universe EW net-of-cost CAGR has a 95% bootstrap interval that includes zero in the test period.

## Data

| Item | Choice |
|---|---|
| Prices | NSE daily cash-market bhavcopies, every trading day Jan 2006 onward, series EQ, BE and BZ (BE/BZ kept: stocks move there when in trouble) |
| Identity | Securities linked through symbol continuity, ISINs (face-value splits issue new ISINs) and NSE's symbol-change list; a symbol that reappears after 250+ sessions with a different or unknown ISIN is a different company |
| Splits and bonuses | NSE's corporate-action records (Bc files in PR archives) from Jul 2010; NSE's PREVCLOSE is *not* adjusted on ex-dates, so factors are applied explicitly, and only when the price actually gapped by the recorded ratio. Before Jul 2010 (extended sample only), a gap detector whose parameters are chosen on 2010-14 records and whose precision/recall are reported on later records |
| Record gaps (found in the audit) | NSE's weekly record files omit some later events and some records state no ratio. Records without a ratio take the nearest standard ratio to the observed gap; records match the nearest session within 5 days; unrecorded overnight gaps >= 1.45x matching a standard ratio are adjusted by the detector in all years; recorded demergers with a gap > 10% are treated as value-neutral on the ex-date. Rights issues are not adjusted |
| Returns | Primary: split/bonus-adjusted **price** returns (dividend records exist only from 2010, so price returns keep the whole period comparable). Sensitivity: dividend-inclusive returns from Aug 2010 |
| Missing archive days | If most securities' NSE base price disagrees with our last close, the previous session is missing and returns are chained from our last close |
| Funds | ETFs and fund units excluded from the stock universe (ISIN prefix INF; name list before ISINs). NIFTYBEES is the investable benchmark |

## Universe (point-in-time by construction)

At each month-end, the 500 securities with the highest median daily traded value over the previous 6 months, among those with: close >= Rs 20; at least 13 months of history; at least 10 trading days in the month; traded in the month's final week.

*Change from the draft:* the draft named the Nifty 500. Its historical membership is published only as scattered index-change notices and cannot be rebuilt reliably, so a liquidity-ranked universe that is point-in-time by construction replaces it. The draft's separate Rs 1 crore liquidity floor is dropped in favour of the ranking.

## Portfolios and timing

- Signal at the close of month t; buy at the first open of month t+1; hold to the first open of month t+2.
- Signals: momentum (return from t-12 to t-1), 1-month reversal (minus last month's return), low volatility (minus trailing daily volatility), 52-week high (price / 12-month high).
- Portfolios: equal weight within the top decile (primary) or quintile; long-short is top minus bottom (paper only; not implementable for a retail investor in India).
- Delisting: a security that stops trading exits at its last traded price; sensitivity with -30% and -100% for distressed exits (last series BE/BZ, price < Rs 10, or down > 50% over its final 12 months). A stock that cannot be bought because it stopped trading is held as cash.

## Frictions

| Item | Choice |
|---|---|
| Statutory costs | `config/india_costs.json`, regime at trade date: STT, stamp duty, NSE and SEBI fees, GST, DP charge per scrip sold |
| Brokerage | 0 (discount broker); sensitivity 0.3% (typical full-service rate in the 2000s) |
| Spread and impact | Half-spread from the Abdi-Ranaldo estimator (3-month mean) + k x daily volatility x sqrt(order / median daily traded value); k in {0, 0.7, 1.5}, 0.7 primary |
| Capital | Rs 10 lakh primary; capacity at Rs 1 crore, 10 crore and 100 crore |
| Rebalancing | Monthly; continuing positions traded only if more than 25% away from target weight |
| Taxes | Resident individual; FIFO lots; STCG/LTCG by sale date; LTCG exempt before Apr 2018, grandfathered to 31 Jan 2018 values; annual exemption; set-off and 8-year loss carry-forward; cess; tax paid each April from the portfolio; final liquidation taxed |

## Periods

- **Primary sample:** holding months Aug 2011 onward, so that every 12-month signal window lies inside the period covered by NSE's corporate-action records.
- **Train:** Aug 2011 - Dec 2017. **Test:** Jan 2018 onward (untouched until the tag).
- **Extended sample (robustness only):** from the first month with a full universe (2007), with detector-based adjustments before Jul 2010. Hypotheses are judged on the primary sample.
- Sub-periods reported descriptively from the extended sample: 2008 crisis, 2013 taper tantrum, 2016 demonetisation, 2020 Covid, 2021-24 retail boom.

*Change from the draft:* the draft used Jan 2006 - Dec 2015 / Jan 2016 onward. The data audit showed that NSE prices are not split-adjusted and official corporate-action records start in Jul 2010, so the primary sample starts where the records do.

## Variant grid (all counted)

Momentum lookback {12, 6} months; low-volatility window {12, 6} months; reversal; 52-week high; each as top decile and top quintile: **12 long-only variants**. Every portfolio computed is appended to `results/variants_log.csv`. The number of trials used in the deflated Sharpe ratio is the larger of 12 and the number of distinct variants logged.

## Statistics

Newey-West t-statistics; stationary block bootstrap (mean block 12 months, 5,000 draws); Hansen SPA and White Reality Check vs same-universe EW; deflated Sharpe ratio (Bailey & Lopez de Prado 2014) of each primary's excess over EW.

## Amendments

None yet.
