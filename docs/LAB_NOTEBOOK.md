# Lab notebook

Dated, factual record of what was done and what was found. Add your own entries (what you checked by hand, what surprised you, what you decided and why); judges read this as evidence of your process.

## 2026-09-26 - build log (AI-assisted)

- Started the NSE downloads: daily bhavcopies from Jan 2006 (resumable, 1 request/s, SHA-256 manifest) and weekly PR archives from Jul 2010 for corporate actions.
- Fake NSE archive with known truth (splits, bonus, rename before ISINs, symbol reuse, missing file, IPO, dividend): the pipeline reproduces every true daily return (tests/test_data_pipeline.py).
- **Finding 1:** the first real-data build (2006-09) showed -78% "returns" on split days (ABB, 28 Jun 2007). NSE's PREVCLOSE is *not* adjusted on ex-dates. Confirmed in 2019 (HDFC Bank 2:1 split: prevclose 2187.75, open 1099.90). The pipeline originally assumed the opposite; the fake archive was changed to mimic the real behaviour, and adjustments now come from NSE's Bc corporate-action records.
- **Finding 2:** ISINs change on face-value splits (20MICRONS INE144J01019 -> INE144J01027), so identity is linked through symbol continuity, ISINs and NSE's symbol-change list.
- **Finding 3:** a first gap detector for pre-2010 splits reached only ~63% precision / ~73% recall on liquid stocks against 2010-11 records. Cause: 20% lower circuits produce a 1.25 price ratio, the same as a 1:4 bonus; real splits often coincide with 3-5% overnight moves. Decision: primary sample starts Aug 2011 (all signal windows covered by records); 2007-11 becomes a robustness check with a detector tuned on 2010-14 records and validated on later records.
- **Finding 4:** White's Reality Check missed a genuine edge hidden among 29 noisy variants in a unit test; Hansen's studentized SPA test found it. SPA is the primary multiple-testing test.
- Pre-registration updated for these data facts (sample dates, universe definition). Not yet tagged.

## 2026-09-26 (later) - full download and data audit (AI-assisted)

- Download complete: 5,123 NSE trading days (293 weekdays without a session), 848 weekly PR archives, no errors. 8.35 million security-days; 3,949 securities after linking (631 symbol-change links, 505 ISIN links, 14 reused symbols split apart); 17 sessions missing from NSE's archive bridged.
- **Finding 5:** some 2020 legacy files write years with two digits ("13-Jul-20"); the parser now accepts both.
- **Finding 6:** NSE changed its corporate-action file format in 2026 (ISO dates, lower-case file names); the parser read zero rows from those files until fixed. KOTAKBANK's 5:1 split (14 Jan 2026) exposed it.
- **Finding 7:** NSE's record files are incomplete: no entry for PIDILITIND's 2025 bonus or SHRIRAMFIN's 2025 split; Infosys's 2015 bonus is recorded as "BONUS" with no ratio; HAL's 2023 split is recorded one day late. Fixes: ratio from the observed gap when a record confirms an event; nearest-session matching; detector gap-fill for large unrecorded gaps. The detector's held-out "precision" (74%) is therefore a lower bound: some "false positives" are real events missing from the records.
- **Finding 8:** demergers (Tata Motors 2025, Vedanta 2026, Siemens 2025, Tata Chemicals 2020...) appear as 40-65% one-day losses. 130 recorded demergers are treated as value-neutral on the ex-date.
- After fixes: 16 unexplained overnight gaps > 40% in liquid stocks (mostly rights issues and pre-2010), listed in results/real/e1_unexplained_gaps.csv.
- Pre-registration updated with these data rules, then frozen (tag prereg-v1). No strategy returns were computed on real data before the tag.
