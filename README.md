# Do equity anomalies survive reality in India?

**Question.** Do well-known momentum, low-volatility and short-term-reversal effects in Indian equities still pay once the backtest stops cheating? That means no survivorship bias, no look-ahead, dated Indian taxes and trading costs, realistic liquidity, and honest accounting for how many strategies were tried.

**Why it matters.** Most shared backtests quietly use data that did not exist at the time: today's index members, companies that later went bust silently removed, prices adjusted with later information. This project measures how much of a strategy's apparent return is real, how much is data artefact, and how much disappears after costs and tax.

**Start here:** [one-page summary](PORTFOLIO.md) · [results](reports/RESULTS_real.md) · [project site](docs/site/index.html) · [pre-registration](docs/PREREGISTRATION.md) · [lab notebook](docs/LAB_NOTEBOOK.md) · [IRIS submission kit](iris/FRAMING.md)

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

E2-E4 refuse to run on real data until `docs/PREREGISTRATION.md` is frozen with the git tag `prereg-v1`, and again if that file changes afterwards. `--data synthetic` dry-runs them on a known market.

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
