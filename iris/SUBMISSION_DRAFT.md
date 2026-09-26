# Submission skeleton (fill in your own words; word limits in brackets)

Each section lists the points to cover and the facts to cite, not finished prose. Numbers marked `{...}` come from `iris/KEY_NUMBERS.md` after the real run.

## Abstract [<= 250 words]
Points, in order:
1. Problem: published and shared backtests use today's company lists and ignore costs, so they overstate returns.
2. Question (one sentence, from FRAMING).
3. Method: synthetic markets with known truth; every NSE stock 2006-2026 incl. dead companies; frozen pre-registration; dated Indian costs and taxes; SPA multiple-testing test.
4. Key results: bias size by strategy `{e4 bias EW/mom/lowvol}`; which anomalies survive net of reality `{H2, H5b}`; SPA p `{H3}`.
5. Conclusion: one sentence on what an investor or researcher should do differently.

## Introduction & objective [100-150]
- Why backtests mislead (survivorship, look-ahead, costs, many strategies tried).
- Why India: STT, dated tax changes, circuit limits, free exchange data that includes dead firms.
- Objective = H1-H6 in one sentence.

## Innovation [50-100]
- Strategy-dependent bias, measured and predicted (not a single "survivorship adjustment").
- Simulation prediction tested on real data.
- Pre-registration enforced in code; validated corporate-action pipeline for free NSE data.

## Methodology [150-250]
- Data (NSE bhavcopies, corporate-action records, identity linking).
- Universe (500 most liquid each month, known at the time), timing (next-day open).
- Frictions (cost and tax model), capital sizes.
- Statistics (Newey-West, bootstrap, SPA, deflated Sharpe).
- Validation (fake archive reproduces truth; synthetic E0).

## Results & conclusions [100-150]
- 3 numbers max, each with its uncertainty interval.
- One negative result stated plainly.

## Acknowledgement & references [50-100]
- AI-assistance disclosure (see FRAMING).
- 4-6 key references from REFERENCES.md.
