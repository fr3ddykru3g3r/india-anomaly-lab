# Project summary (one page)

**India Anomaly Lab: how much of a stock backtest is real?**
Independent research project, 2026. Python; about 2,500 lines plus tests.

**The question.** Stock strategies that look profitable on paper often rely on data that did not exist at the time, and they ignore costs and taxes. I measured how much of the apparent return in Indian equities is real, how much is a data artefact, and whether a simulation with known answers can predict the artefact.

**What I built**
- A point-in-time dataset of **every stock traded on India's NSE since 2006**, including companies that later collapsed or delisted, assembled from the exchange's own daily files.
- A validated corporate-action pipeline. I discovered that NSE's published prices are *not* adjusted for stock splits, and that a stock hitting its 20% daily limit looks exactly like a 1:4 bonus issue. I fixed both, and measured the fix's accuracy against exchange records.
- A backtesting engine in rupees with India's dated transaction taxes, trading costs, market impact, and capital-gains tax law (including the 2018 and 2024 changes).
- Synthetic markets with known truth, used to test every part of the code before trusting it on real data.

**How I kept it honest**
- Hypotheses frozen in a pre-registration before any real result. The code refuses to run on real data otherwise.
- Every strategy variant tried is logged and corrected for (Hansen SPA test, deflated Sharpe ratio).
- Negative results are reported alongside positive ones.

**Findings.** See `iris/KEY_NUMBERS.md` and the project site (`docs/site/`).

**Skills.** Statistics (bootstrap, multiple-testing correction, time-series inference), data engineering, simulation, financial market microstructure, Indian tax and regulation, scientific writing.

*An AI coding assistant helped write the software under my direction; the question, decisions, verification and analysis are mine.*
