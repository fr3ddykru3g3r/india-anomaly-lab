# Do equity anomalies survive reality in India?

**Question.** Do well-known momentum, low-volatility and short-term-reversal effects in Indian equities still pay once the backtest stops cheating? That means no survivorship bias, no look-ahead, dated Indian taxes and trading costs, realistic liquidity, and honest accounting for how many strategies were tried.

**Why it matters.** Most shared backtests quietly use data that did not exist at the time: today's index members, companies that later went bust silently removed, prices adjusted with later information. This project measures how much of a strategy's apparent return is real, how much is data artefact, and how much disappears after costs and tax.

**Live:** [project site](https://fr3ddykru3g3r.github.io/india-anomaly-lab/site/) · [interactive backtest reality check](https://fr3ddykru3g3r.github.io/india-anomaly-lab/site/tool.html)

**Start here:** [one-page summary](PORTFOLIO.md) · [results](reports/RESULTS_real.md) · [project site](docs/site/index.html) · [interactive tool](docs/site/tool.html) · [pre-registration](docs/PREREGISTRATION.md) · [lab notebook](docs/LAB_NOTEBOOK.md) · [IRIS submission kit](iris/FRAMING.md)

## What is in here

| Layer | What it does | Where |
|---|---|---|
| Data | Downloads every NSE daily file since 2006 (including later-delisted stocks), links securities across symbol and ISIN changes, adjusts splits and bonuses from NSE's own corporate-action records | `src/indialab/data/` |
| Research | Point-in-time universe (500 most liquid stocks each month), signals, portfolios bought at the next day's open | `src/indialab/research.py`, `study.py` |
| Frictions | Dated STT, stamp duty, exchange/SEBI fees, GST, DP charges; spread (Abdi-Ranaldo) and square-root impact; FIFO capital-gains tax with STCG/LTCG regimes, 2018 grandfathering, set-off and carry-forward | `costs.py`, `engine.py`, `tax.py` |
| Integrity | Pre-registration enforced in code; every variant logged; Hansen SPA, White Reality Check, deflated Sharpe; same-universe controls; credibility checklist | `stats.py`, `integrity.py`, `docs/PREREGISTRATION.md` |
| Validation | Synthetic markets with known truth (E0) and a fake NSE archive with splits, renames, symbol reuse and missing files; the pipeline must reproduce the truth exactly | `synthetic.py`, `data/fake.py`, `tests/` |
| Output | Generated results report and a static site with a trade-cost calculator and bias explorer | `scripts/build_report.py`, `scripts/build_site.py` |

## Experiments

| ID | Question | Script |
|---|---|---|
| E0 | On a synthetic market with known truth, how much does missing dead-firm data inflate common rules? | `experiments/e0_synthetic_survivorship.py` |
| E1 | Is the NSE dataset complete and correctly adjusted? (no returns computed) | `experiments/e1_data_coverage.py` |
| E2 | Do the anomalies exist gross of frictions? | `experiments/e2_replicate.py` |
| E3 | Do they survive costs, tax, capacity limits and multiple testing? | `experiments/e3_reality.py` |
| E4 | How much do survivor-only and vendor-style data inflate Indian backtests, and does it match E0? | `experiments/e4_bias.py` |
| E5 | *(exploratory)* Why was H6 refuted? | `experiments/e5_why_h6_failed.py` |
| E6 | *(exploratory)* Value on the short 2024-26 sample | `experiments/e6_value_short_sample.py` |
| E7 | Scenario data for the educational tool | `experiments/e7_tool_data.py` |

E2-E4 refuse to run on real data until `docs/PREREGISTRATION.md` is frozen with the git tag `prereg-v1`, and again if that file changes afterwards. `--data synthetic` dry-runs them on a known market.

## Project goals and where each is met

| Goal | Status | Evidence |
|---|---|---|
| Reproduce published anomalies | Done: momentum, reversal, low volatility, 52-week high; value only as a short 2024-26 exploratory test (NSE publishes P/E only from 2024) | E2, E6 |
| Account for survivorship and look-ahead bias | Done: point-in-time universe, dead companies kept, next-open execution, look-ahead unit test; bias measured directly | E1, E4, tests |
| Test across time periods | Done: train 2011-17 / untouched test 2018+, extended 2007+ sample, five market episodes | E2, E3 |
| Do results survive realistic costs? | Done: dated statutory costs, spread, impact, capacity to Rs 100 crore, capital-gains tax | E3 |
| Publish failed models too | Done: all 12 pre-registered variants, refuted H6, failed H5a, inconclusive value, pre-amendment results kept | `docs/site/tool.html`, `reports/RESULTS_real.md` |
| Educational backtesting tool | Done: interactive page (strategy x data shortcut x costs x tax x size) + trade-cost calculator | `docs/site/tool.html`, `docs/site/index.html` |
| No real-money trading, no "AI alpha" claims | Done: measurement study; no ML | this README |

## Headline results (primary sample Aug 2011 - Sep 2026)

- Applying today's top-500 list to the past inflates a momentum backtest by **+21.7 percentage points a year** (48.4% vs 26.7%).
- The simulation's prediction that equal-weight portfolios suffer most from deleted failures (H6) was **refuted**: in India, momentum portfolios held failing companies while they collapsed (E5, exploratory).
- Momentum beats the same-universe equal-weight portfolio after Indian costs and tax at Rs 10 lakh (21.9% vs 11.4% a year); the edge falls to 12.8% at Rs 100 crore. Reversal is destroyed by costs; low volatility reduces risk, not return.

## Findings about the data (before any strategy was tested)

- **NSE prices are not split-adjusted.** On an ex-date, the bhavcopy's previous close is the unadjusted price (HDFC Bank's 2019 2:1 split looks like a -50% day). Factors come from NSE's corporate-action records, and are applied only when the price actually gapped by the recorded ratio.
- **ISINs are not permanent.** A face-value split issues a new ISIN, so identity is linked, not assumed.
- **A 20% lower circuit looks exactly like a 1:4 bonus** (both give a 1.25 price ratio). Gap-based split detection must exclude small ratios. Its accuracy is measured against NSE's records instead of assumed.
- **Records start in July 2010**, so the primary sample starts in Aug 2011. 2007-2011 is a disclosed robustness check.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests
scripts/run_all.sh        # downloads (resumable), builds the dataset, runs E0-E4, builds report + site
```

## Non-goals

No real-money trading, no investment advice, no "AI-powered alpha". This is a measurement study. A negative result ("this anomaly does not survive costs") is a result, and every tested variant is reported.

*AI assistance was used to write code and documentation; research questions, decisions, verification and write-up are the author's own. Disclose this in any competition submission.*
