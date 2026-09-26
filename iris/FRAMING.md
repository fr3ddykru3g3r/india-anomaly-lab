# IRIS National Fair 2026-27: framing and submission plan

## Deadline first

The 2026-27 registration and submission window closes in **early October 2026 (about 3 Oct)**, with no extensions. Confirm the exact date on <https://register.irisnationalfair.org/> today. Everything below is scheduled to fit that window.

## The framing: a measurement question, not a trading strategy

Judges at an ISEF-affiliated fair reward a clear, testable question, controlled comparisons, known ground truth, and honest negative results. "I built a stock screener that makes money" loses on all four counts. This project asks:

> **How much of a stock-strategy backtest's apparent return is an artefact of the data and the frictions it ignores? Does that artefact depend on the strategy, and can a simulation with known truth predict it?**

| Element | What we have |
|---|---|
| Testable hypotheses | H1-H6, frozen in `docs/PREREGISTRATION.md` (git tag `prereg-v1`) before any real result |
| Independent variables | data view (point-in-time / survivors / % of dead firms deleted); strategy; frictions (none / costs / tax); capital size |
| Dependent variable | CAGR bias vs point-in-time truth; net-of-reality excess over equal weight |
| Controls | same-universe equal weight; investable ETF (NIFTYBEES); synthetic market with known truth |
| Ground truth | E0 synthetic markets; fake NSE archive whose true returns are reproduced exactly (tests) |
| Real data | every NSE equity 2006-2026, including companies that later disappeared |
| Novel contribution | (1) the bias is **strategy-dependent** (E0: equal weight inflated ~2x more than momentum/low-vol); (2) the synthetic prediction is tested against real Indian data (E4, H6); (3) a documented, validated corporate-action pipeline for free NSE data (e.g. NSE prices are not split-adjusted; circuit limits mimic 1:4 bonuses) |

**Category (recommendation):** *Mathematics*, if it has a statistics subcategory, because the core contribution is a statistical measurement method (bias estimation, multiple-testing correction, simulation validation). The alternative is *Behavioural & Social Sciences*, if the fair lists economics/finance there. Check the category descriptions on the IRIS site before choosing. Do **not** use Computer Science: the code is a tool, not the finding.

**Title options:**
1. *How Much of a Backtest Is Real? Measuring Survivorship, Cost and Data-Mining Bias in Indian Equity Anomalies*
2. *Ghost Returns: Strategy-Dependent Survivorship Bias in Indian Stock Backtests, Predicted by Simulation and Tested on 20 Years of NSE Data*

## What IRIS asks for, and where each piece comes from

| Component | Limit | Source in this repo | Who writes it |
|---|---|---|---|
| Abstract | 250 words | `iris/SUBMISSION_DRAFT.md` skeleton + `reports/RESULTS_real.md` numbers | **you** |
| Introduction & objective | 100-150 | FRAMING (this file), README | you |
| Innovation | 50-100 | "Novel contribution" row above | you |
| Methodology | 150-250 | `docs/PREREGISTRATION.md`, `docs/DATA_PLAN.md` | you |
| Results & conclusions | 100-150 | `reports/RESULTS_real.md`, `iris/KEY_NUMBERS.md` (generated) | you |
| Acknowledgement & references | 50-100 | `iris/REFERENCES.md` | you |
| Video | 90 s | `iris/VIDEO_STORYBOARD.md` | you (record yourself) |
| Research paper | - | `iris/PAPER_OUTLINE.md` + figures in `figures/real/` | you |
| Research data book | - | `docs/LAB_NOTEBOOK.md`, `results/variants_log.csv`, git history | you continue it |

The words must be yours. The fair checks for AI-written text, and the project history (git log, notebook) must show your own process.

## AI-use disclosure (required in spirit, likely in rules)

State plainly in the paper's acknowledgements, for example: *"An AI coding assistant was used to write parts of the software and documentation under my direction. The research question, hypotheses, verification of results and all written analysis are my own."* Be ready to explain every module in the judging interview. `iris/JUDGE_PREP.md` lists the questions to practise.

## Compliance checklist

- [ ] No school name, city or state anywhere: paper, abstract, video, **and the repo you link** (check git author email and the site footer). The local git identity for this repo is set to a non-school noreply address for this reason.
- [ ] Data less than 12 months old: every real result is computed in Sep-Oct 2026 (timestamps in `results/variants_log.csv`).
- [ ] Individual or team of two; list only actual contributors.
- [ ] No human or animal subjects, no hazardous materials: purely computational.
- [ ] References for every method used (`iris/REFERENCES.md`).

## Schedule to the deadline

| Day | Task |
|---|---|
| 1 | Download finishes -> dataset build -> E1 audit -> **you read PREREGISTRATION.md** -> commit + tag `prereg-v1` |
| 1 | E2-E4 on real data, report and site regenerated, `iris/KEY_NUMBERS.md` generated |
| 2 | Choose category and title; register |
| 2-3 | Write the six text sections yourself from KEY_NUMBERS; check word counts |
| 4 | Record the 90-s video from the storyboard |
| 5 | Paper draft from PAPER_OUTLINE; practise JUDGE_PREP answers |
| 6 | Anonymity check; submit with a day to spare |

## If the real run is not ready in time

Submit on what already exists: E0 (synthetic, complete), the validated data pipeline and its findings, and E1 (data audit). Frame the Indian replication as "stage 2, in progress", which the rules allow ("projects may extend previously started work").
