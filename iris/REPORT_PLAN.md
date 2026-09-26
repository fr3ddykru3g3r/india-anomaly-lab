# IRIS report plan (built on the real results, 26 Sep 2026)

This is the plan: storyline, order, the numbers to cite and where they come from. Write the prose yourself. Every number below is in `reports/RESULTS_real.md` or `iris/KEY_NUMBERS.md`.

## The storyline in four sentences

1. Backtests of Indian stock strategies can be badly inflated by the data they use, and I measured by how much on every NSE stock since 2006, including the ~680 that died.
2. The largest error is using today's company list: it inflates a momentum backtest by **+21.7 percentage points a year**, more than doubling its apparent return, while silently deleting dead companies adds +2.3 to +2.5 pp.
3. My simulation predicted equal-weight portfolios would be hurt most. Indian data **refuted** that: momentum is hurt as much or more, which shows the simulation's assumption (firms fail only after falling) does not hold in India.
4. On honest data, momentum still beats the market after Indian costs and tax at small scale (21.9% vs 11.4% a year), but the edge shrinks sharply with portfolio size. Short-term reversal is destroyed by costs, and low volatility cuts risk, not return.

## Title

*How Much of a Backtest Is Real? Survivorship, Cost and Data-Mining Bias in 20 Years of Indian Equities, and a Simulation It Refuted*

## Figure plan (6 figures, in this order)

| # | Figure | File | One-line message |
|---|---|---|---|
| 1 | Synthetic bias by rule (E0) | `figures/e0_bias_vs_missing_data.png` | In a market with known truth, missing dead firms inflate backtests, by rule |
| 2 | Data coverage (E1) | `figures/real/e1_coverage.png` | Every NSE equity that traded, dead ones included |
| 3 | Bias on real data vs prediction (E4) | `figures/real/e4_bias_vs_deletion.png` | Real Indian bias; momentum line sits on/above EW, unlike the prediction |
| 4 | Gross growth (E2) | `figures/real/e2_cumulative_gross.png` | Momentum's gross lead; test period marked |
| 5 | Gross -> costs -> tax (E3) | `figures/real/e3_gross_net_tax.png` | What survives reality |
| 6 | Capacity (E3) | `figures/real/e3_capacity.png` | Momentum's edge erodes with size |

## Numbers to cite (primary sample Aug 2011 - Sep 2026, Rs 10 lakh)

**Data (E1):** 5,123 daily files; 8.35 million security-days; 3,949 securities; 678 distressed exits; 1,059 recorded split/bonus adjustments + detector (held-out precision 82%, recall 89%); 130 demergers neutralised; 16 unexplained large gaps in liquid stocks.

**Bias (E4), CAGR inflation in pp/yr:**

| Shortcut | Equal weight | Momentum | Low vol |
|---|---|---|---|
| Today's top 500 applied backwards | +11.2 | **+21.7** | +4.6 |
| Only survivors | +2.6 | +3.9 | +1.3 |
| All distressed exits deleted | +2.3 | +2.5 | +1.1 |
| E0 prediction (all deleted) | +3.9 | +2.5 | +1.8 |

**Anomalies (E2/E3):**

| Portfolio | Gross | After costs | After tax | Max drawdown (net) |
|---|---|---|---|---|
| Same-universe EW | 12.5% | 11.7% | 11.4% | -54% |
| Momentum top decile | 26.7% | 23.9% | 21.9% | -46% |
| Low-vol top decile | 12.9% | 12.0% | 11.1% | -19% |
| Reversal top decile | 5.5% | -3.1% | -3.1% | -78% |

NIFTYBEES (investable Nifty 50 ETF): 10.7% gross.

**Hypotheses:** H1 yes (t = 4.72); H2 yes (test-period net excess +11.6 pp, 95% CI +4.3 to +20.6); H3 yes (SPA p < 0.001, 12 variants); H4 yes (Sharpe 1.00 vs 0.64, CI of difference 0.12 to 0.60); H5a no; H5b yes; **H6 no**.

**Capacity (momentum, net, k = 0.7):** 23.9% at Rs 10 lakh -> 22.9% (1 crore) -> 19.7% (10 crore) -> 12.8% (100 crore).

## Honesty points that make the project stronger (put them in; judges look for them)

1. **H6 was refuted.** Present it as the main scientific finding, not a failure. The follow-up (E5, exploratory, label it as such) shows why: in the simulation, momentum's positions in future failures earned +0.2%/month (bought while rising, sold before the collapse); in India they lost -2.6%/month, as badly as equal weight's (-2.4%). Indian momentum portfolios ride failing companies down. Add E5's figure (`figures/real/e5_positions_in_future_failures.png`) as figure 3b.
2. **Amendment 1.** After the first real run I found a bug in the spread estimator (it inflated costs). I fixed it, logged a dated amendment, and kept the old outputs (`results/real_pre_amendment1/`). Under the buggy costs, H2 was *not* supported (CI -0.04 to +14.6). Report both. A 10 bp spread-floor sensitivity changes momentum's net CAGR only from 23.9% to 23.4%.
3. **Deflated Sharpe for momentum is 0.75**, below the conventional 0.95, even though SPA rejects luck. Say which test answers which question.
4. **Concentration:** momentum's cumulative return in the 2021-24 retail boom was +446% vs +206% for EW. The training period (2011-17) was also strong (28% gross), so it is not only the boom, but the drawdown is -46%.
5. **Value** (part of the original question) could only be checked on 31 months (NSE P/E files start in 2024): -0.4%/yr vs +2.1% for EW, interval -9.1 to +2.9 points, i.e. inconclusive (E6). Say so; do not drop it.
6. **Limitations:** price returns (dividends add ~1.3-1.6 pp for all portfolios from 2010); rights issues unadjusted; daily-data spread estimates; liquidity universe, not the Nifty 500.

## Section-by-section plan (IRIS limits)

| Section | Words | Cover |
|---|---|---|
| Abstract | <= 250 | storyline sentences 1-4 + method in one sentence + one takeaway |
| Introduction & objective | 100-150 | backtest problem; why India; question; H1-H6 in one line |
| Innovation | 50-100 | bias is rule-dependent and a simulation's prediction was tested and refuted on real data; pre-registration enforced in code; validated corporate-action pipeline for free NSE data |
| Methodology | 150-250 | data -> universe/timing -> frictions -> statistics -> validation (fake archive, E0) |
| Results & conclusions | 100-150 | the bias table's momentum row; H6 refuted; momentum survives at small scale only; reversal dies; low vol = risk not return |
| Acknowledgement & references | 50-100 | AI-use statement (see FRAMING.md); 4-6 references |

## Research paper (full)

Follow `iris/PAPER_OUTLINE.md`; tables come straight from `reports/RESULTS_real.md`. Put the pre-registration and Amendment 1 in an appendix.

## Video (90 s)

Use `iris/VIDEO_STORYBOARD.md`, with figure 3 (bias vs deletion) as the centrepiece and the line "today's list inflates momentum by 22 points a year".

## Before submitting

- [ ] Read `docs/PREREGISTRATION.md` and Amendment 1; be able to explain both.
- [ ] Rehearse `iris/JUDGE_PREP.md` Q15-Q17 using H6.
- [ ] Anonymity check (no school, city or state; git identity already non-school).
- [ ] Choose the category (Mathematics or Behavioural & Social Sciences) after reading IRIS's category descriptions.
