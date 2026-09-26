# Project plan and scope review

## Verdict on the original idea

The core idea is strong: reproducing known anomalies under honest conditions is a real research question, it plays to an India-based team's advantage (local data, market structure, tax rules), and "publish the failures" is exactly what separates research from a trading pitch. Changes:

### Rename or re-scope "microstructure"

Market microstructure means order books, bid-ask spreads, price impact and trade-level data. The original question is about **anomaly survival after frictions**. Either:

- rename it ("Do equity anomalies survive reality in India?"), or
- earn the word by making frictions a real contribution: estimate spreads from daily data (Abdi-Ranaldo), model impact, test capacity (how much capital before the edge disappears), and measure how events such as the T+1 settlement move (2023) or STT changes altered costs.

This scaffold does both: the title is honest and the cost model is a first-class module.

### Add

1. **Pre-registration** (`PREREGISTRATION.md`), frozen with a dated git tag before real data is examined. This is the single strongest credibility signal.
2. **Synthetic ground truth** (done: E0). It validates the method before it is pointed at messy real data.
3. **Selection vs timing decomposition** for every strategy: rule versus same-universe equal weight.
4. **ISIN-based identity** and an explicit delisting-return policy.
5. **Capacity curves**: net return at Rs 10 lakh, 1 crore and 10 crore.
6. **Dated tax regimes**: LTCG exempt before Apr 2018 and taxed after; STCG 10/15/20%. Tax changes are natural experiments.
7. **Subperiods**: 2008 crisis, the 2013 taper tantrum, 2016 demonetisation, 2020, and 2021-24 retail boom.

### Subtract or defer

- **Full web app.** Start with a CLI, notebooks and a static results site. An "educational tool" can be the documented library plus a Streamlit or GitHub Pages demo later.
- **Value from free fundamentals.** Point-in-time Indian fundamentals are hard to get free. Do price-based anomalies first; add book-to-market only with a publication-lag rule and a documented source.
- **"Credibility interval" as a formal statistic.** Develop it only after E0-E2 show which assumptions matter.
- **Anything ML/"AI".** It adds overfitting risk and credibility cost with no research payoff here.

## Phases

| Phase | Deliverable | Status |
|---|---|---|
| 0 Tooling + synthetic | cost model, integrity toolkit, E0 | done |
| 1 Point-in-time data | NSE daily files + corporate-action records + identity linking; E1 audit | built and tested on a fake archive; real download and audit in progress |
| 2 Pre-register | `docs/PREREGISTRATION.md` frozen with tag `prereg-v1` (enforced by the experiment scripts) | written; tag after the E1 audit |
| 3 Replicate | momentum, reversal, low-vol, 52-week-high; gross vs same-universe EW and NIFTYBEES (E2) | code done, dry-run on synthetic |
| 4 Reality layers | costs, taxes, capacity, sub-periods, multiple testing, verdicts (E3) | code done, dry-run on synthetic |
| 5 Bias study | survivors / today's list / vendor deletion vs point-in-time, compared with E0 (E4) | code done, dry-run on synthetic |
| 6 Write-up | generated results report + static site; the discussion is written by the author | builders done |

Deferred: value (needs point-in-time fundamentals), demerger/rights adjustments, Nifty 500 membership reconstruction, ML.

## IRIS / science-fair angle

The strongest research question is methodological and generalises beyond India:

> *How much of a backtest's apparent performance is an artefact of missing and look-ahead data, does the answer depend on the strategy, and can it be predicted from a simulation with known ground truth?*

E0 already shows the rule-dependence, and it independently reproduces the order of magnitude measured on real S&P 500 data (+0.6 to +1.4 pp at 20-30% missing). The Indian study becomes the real-world test case. Check the fair's current rules on AI assistance and prior-work disclosure, and keep a dated lab notebook.
