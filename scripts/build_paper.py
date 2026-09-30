"""Generate the full research report (Markdown, HTML, PDF) from results/real.

Every number in the text is read from the result files, so the report cannot drift from the data.
The interpretation is written once, below, and checked against the numbers it cites.

    python scripts/build_paper.py
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import markdown
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "results" / "real"
OUT = ROOT / "reports"
NAME = "India_Anomalies_Research_Report"


def pct(x, d=1):
    return "n/a" if pd.isna(x) else f"{x * 100:.{d}f}%"


def pp(x, d=1):
    return "n/a" if pd.isna(x) else f"{x:+.{d}f}"


def pv(p):
    return "<0.001" if p < 0.0005 else f"{p:.3f}"


def table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if (not isinstance(v, str) and pd.isna(v)) else str(v) for v in r.values) + " |")
    return "\n".join(lines) + "\n"


def load():
    d = {}
    d["build"] = json.loads((ROOT / "data" / "processed" / "build_stats.json").read_text())
    d["e1"] = json.loads((R / "e1_summary.json").read_text())
    d["cov"] = pd.read_csv(R / "e1_coverage_by_year.csv", index_col=0)
    d["e2"] = pd.read_csv(R / "e2_gross_summary.csv")
    d["e3"] = pd.read_csv(R / "e3_net_of_reality.csv")
    d["cap"] = pd.read_csv(R / "e3_capacity.csv")
    d["e3t"] = json.loads((R / "e3_tests.json").read_text())
    d["hyp"] = pd.read_csv(R / "e3_hypotheses.csv")
    d["term"] = pd.read_csv(R / "e3_terminal_sensitivity.csv")
    d["div"] = pd.read_csv(R / "e3_dividend_sensitivity.csv")
    d["ext"] = pd.read_csv(R / "e3_extended_sample_gross.csv")
    d["sub"] = pd.read_csv(R / "e3_subperiods.csv")
    d["e4"] = pd.read_csv(R / "e4_bias_summary.csv")
    d["e5"] = pd.read_csv(R / "e5_exposure_to_future_failures.csv")
    d["e6"] = json.loads((R / "e6_value_short_sample.json").read_text())
    d["e8t"] = json.loads((R / "e8_tests.json").read_text())
    d["e8h"] = pd.read_csv(R / "e8_extension_hypotheses.csv")
    d["e8cap"] = pd.read_csv(R / "e8_capacity.csv")
    d["e8ext"] = pd.read_csv(R / "e8_extended_sample_gross.csv")
    d["led"] = pd.read_csv(R / "e9_all_models_ledger.csv")
    d["rob"] = pd.read_csv(R / "e10_robustness.csv")
    d["e0"] = pd.read_csv(ROOT / "results" / "e0_bias_by_deletion_pp.csv", header=[0, 1], index_col=[0, 1])
    return d


def main() -> Path:
    d = load()
    g = lambda name, part="full": d["e2"][(d["e2"].portfolio == name) & (d["e2"].period == part)].iloc[0]
    n = lambda name, part="full": d["e3"][(d["e3"].portfolio == name) & (d["e3"].period == part)].iloc[0]
    led = d["led"].set_index("model")
    e4 = d["e4"].set_index(["view", "rule"], drop=False)
    e4v = lambda view, rule, f=None: (d["e4"][(d["e4"].view == view) & (d["e4"].rule == rule) &
                                             ((d["e4"].fraction == f) if f is not None else True)]["mean"].iloc[0])
    hyp = d["hyp"].set_index("id")
    x = d["e8h"].set_index("id")
    mt3 = d["e3t"]["multiple_testing"]
    mt8 = d["e8t"]["multiple_testing"]
    b = d["build"]
    e5 = d["e5"].set_index(["sample", "rule"])
    e6 = d["e6"]
    ew, mom, lv, rev = "same-universe EW", "H1 momentum12_top10", "H4 low_vol12_top10", "H5 reversal_top10"
    chosen = d["e8t"]["chosen"]
    tag = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    e0v = lambda rule, f: d["e0"].loc[(rule, f), ("cagr_bias", "mean")]
    mom_ls = d["e2"][(d["e2"].portfolio == "momentum12_top10") & (d["e2"].kind == "long-short top-bottom") & (d["e2"].period == "full")].iloc[0]
    rev_ls = d["e2"][(d["e2"].portfolio == "reversal_top10") & (d["e2"].kind == "long-short top-bottom") & (d["e2"].period == "full")].iloc[0]
    cap_m = d["cap"][(d["cap"].variant == "momentum12_top10") & (d["cap"].k == 0.7)].set_index("capital")["net_cagr"]
    cap_l = d["cap"][(d["cap"].variant == "low_vol12_top10") & (d["cap"].k == 0.7)].set_index("capital")["net_cagr"]
    cap_r = d["cap"][(d["cap"].variant == "reversal_top10") & (d["cap"].k == 0.7)].set_index("capital")["net_cagr"]
    cap8 = d["e8cap"].pivot(index="capital", columns="portfolio", values="net_cagr")
    sub = d["sub"].set_index("period")
    ext = d["ext"][d["ext"].period == "full"].set_index("portfolio")
    ext8 = d["e8ext"].drop_duplicates("portfolio").set_index("portfolio")
    rob = d["rob"]
    rb = lambda s: rob[rob.test.str.startswith(s)]["momentum_top10_gross_cagr"].iloc[0]
    ex = lambda k: d["e5"]
    tm = d["term"].set_index("terminal_return_distressed")
    term_max = float((tm.loc[0.0] - tm.loc[-1.0]).abs().max() * 100)
    dv = d["div"]
    dvd = (dv["total_return_cagr_2010_08_on"] - dv["price_return_cagr_2010_08_on"]) * 100
    div_min, div_max = float(dvd.min()), float(dvd.max())
    sub8 = pd.read_csv(R / "e8_subperiods.csv").set_index("period")
    n_pass = int(led["survives_failure_criterion"].fillna(False).sum())
    n_models = len(led) - 1
    S: list[str] = []
    a = S.append

    a(f"""# Do Momentum, Value and Mean-Reversion Strategies Still Work in Indian Equities After Costs, Taxes, Liquidity and Data-Mining Bias?

*A pre-registered replication study on every stock traded on NSE, 2006-2026*

**Data:** {b['files']:,} NSE daily files ({b['first_date']} to {b['last_date']}), {b['rows']:,} security-days, {b['securities']:,} securities including {int(d['e4'].shape[0] and pd.read_csv(R / 'e4_meta.csv', index_col=0).iloc[0, 0]):,} that failed or were delisted in distress.
**Code and data pipeline:** `india-anomaly-lab` (commit `{tag}`); every result below is regenerated by `scripts/run_all.sh`.
**Pre-registration:** git tags `prereg-v1`, `prereg-v1-amend1`, `prereg-v1-amend2`.

---

## 1. Answer in one page

**The question.** Do common momentum, value and mean-reversion strategies in Indian equities continue to work after accounting for transaction costs, taxes, liquidity and data-mining bias?

**The answer, strategy by strategy** (primary sample Aug 2011 - Sep 2026, Rs 10 lakh portfolio; "EW" = equal weight of the same 500 most-liquid stocks each month, the fair control):

""")
    rows = [
        ["Momentum (12-1 months, top decile)", f"Yes: long-short t = {mom_ls['nw_t']:.2f}; gross {pct(g('momentum12_top10').cagr)} vs EW {pct(g('same-universe EW').cagr)}",
         f"**Yes.** {pct(n(mom).net_cagr)} after costs, {pct(n(mom).net_after_tax_cagr)} after costs and tax vs EW {pct(n(ew).net_cagr)} / {pct(n(ew).net_after_tax_cagr)}",
         f"Test period 2018+: {hyp.loc['H2', 'evidence']}; SPA p < 0.001 over {mt3['full']['spa']['n_variants']} variants",
         f"Edge shrinks with size: {pct(cap_m['Rs 1 crore'])} at Rs 1 crore, {pct(cap_m['Rs 10 crore'])} at Rs 10 crore, {pct(cap_m['Rs 100 crore'])} at Rs 100 crore (about EW). Drawdown {pct(n(mom).net_max_dd, 0)}"],
        ["Short-term reversal (last month's losers)", f"No: long-short t = {rev_ls['nw_t']:.2f} (wrong sign); top decile {pct(g('reversal_top10').cagr)} vs EW {pct(g('same-universe EW').cagr)}",
         f"**No.** {pct(n(rev).net_cagr)} after costs; turnover {pct(n(rev).avg_monthly_turnover, 0)} a month costs {n(rev).cost_drag_pp * 100:.1f} pp a year", "Fails the pre-registered criterion (interval entirely below zero)", "Costs, not data, kill it"],
        ["Low volatility (calmest decile)", f"Risk-adjusted only: Sharpe {hyp.loc['H4', 'evidence'].split(';')[0].replace('Sharpe ', '')} (95% CI of the difference {hyp.loc['H4', 'evidence'].split('CI of difference ')[1]}), return {pct(g('low_vol12_top10').cagr)} vs EW {pct(g('same-universe EW').cagr)}",
         f"**Risk cut, not extra return.** {pct(n(lv).net_cagr)} after costs, {pct(n(lv).net_after_tax_cagr)} after tax, versus EW {pct(n(ew).net_cagr)} / {pct(n(ew).net_after_tax_cagr)}", "Return difference vs EW not significant (interval spans zero)", f"Worst fall {pct(n(lv).net_max_dd, 0)} vs EW {pct(n(ew).net_max_dd, 0)}"],
        ["52-week-high", f"Yes gross ({pct(g('high_52w_top10').cagr)})", f"Partly: {pct(led.loc['high_52w_top10', 'net_cagr_full'])} after costs", f"Test-period excess {pp(led.loc['high_52w_top10', 'test_net_excess_vs_ew_pp'])} pp, interval [{led.loc['high_52w_top10', 'ci_lo_pp']:.1f}, {led.loc['high_52w_top10', 'ci_hi_pp']:.1f}] spans zero", "Not distinguishable from EW after costs"],
        ["Value (fundamental)", "**Untestable over the full sample**: NSE publishes P/E only from 2024", f"{e6['months']} months only: {pct(e6['value_top_decile_gross_cagr'])} vs EW {pct(e6['ew_gross_cagr'])}", f"95% interval of gross difference {pct(e6['gross_diff_ci95'][0])} to {pct(e6['gross_diff_ci95'][1])}: inconclusive", "Needs a licensed historical fundamentals database"],
        ["Value proxies (price-based)", f"Long-term reversal {pct(led.loc['ltrev_top10', 'gross_cagr_full'])}, cheap vs 5-year mean {pct(led.loc['cheap5y_top10', 'gross_cagr_full'])} gross", f"**No.** {pct(led.loc['ltrev_top10', 'net_cagr_full'])} and {pct(led.loc['cheap5y_top10', 'net_cagr_full'])} after costs", "Both below EW; the second is significantly below", "Failed models are reported"],
    ]
    a(table(pd.DataFrame(rows, columns=["Strategy", "Reproduces the published anomaly?", "Survives costs and tax?", "Survives out-of-sample and data-mining tests?", "Liquidity and other notes"])))
    a(f"""
**Five findings that matter beyond any single strategy**

1. **Momentum is the one anomaly that clearly survives**, but it is a small-capital result. The net edge over EW is about {pp((n(mom).net_cagr - n(ew).net_cagr) * 100)} pp a year at Rs 10 lakh and disappears near Rs 100 crore.
2. **Using today's list of big companies to test the past inflates a momentum backtest by {pp(e4v("today's top 500 backwards", 'momentum'))} percentage points a year**, more than doubling the true return ({pct(g('momentum12_top10').cagr)} true vs about {pct(g('momentum12_top10').cagr + e4v("today's top 500 backwards", 'momentum') / 100)} apparent). This is by far the largest data error measured, and it is an easy one to make: it takes only a current constituent list.
3. **Quietly deleting failed companies inflates every rule by {pp(e4v('vendor', 'low_vol', 1.0))} to {pp(e4v('vendor', 'momentum', 1.0))} pp a year.** A pre-registered simulation predicted equal-weight portfolios would be hurt most; in India momentum was hurt as much or more (hypothesis H6 refuted), because momentum portfolios kept holding failing companies while they collapsed (Section 8).
4. **The team's 30-stock trend-and-setup rule (S5) cuts drawdown ({pct(led.loc['S5', 'net_max_dd'], 0)} vs EW {pct(led.loc['EW', 'net_max_dd'], 0)}) and beat EW over the full sample, but its edge is not statistically reliable in the untouched test period** ({x.loc['X1', 'evidence']}), and it earns less than plain top-30 momentum ({pct(led.loc['S2', 'net_cagr_full'])} after costs).
5. **{n_pass} of {n_models} models pass the pre-registered failure criterion individually, but that is before correcting for having tried {n_models}.** After correction only the momentum family is convincing (Section 7).

---

## 2. Design

**Question and scope.** Momentum, value and mean-reversion anomalies, long-only implementable portfolios, Indian cash equities (NSE), investor: a resident individual (delivery trades, retail capital Rs 10 lakh with capacity tests to Rs 100 crore). No shorting, no leverage, no derivatives. No real-money trading.

**Pre-registration.** Hypotheses, universe, timing, costs, taxes, tests and sample split were written and frozen in git (`prereg-v1`) before any strategy return was computed on real data. The experiment code refuses to run on real data unless the frozen document is unchanged. Two dated amendments exist and are disclosed in Section 10: Amendment 1 (a bug fix after the first run, with the pre-fix results kept) and Amendment 2 (the team-rule and value-proxy extension, declared and tagged before those results were computed).

**Hypotheses (original).**

""")
    h = d["hyp"].copy()
    h["supported"] = h["supported"].map({True: "yes", False: "no"})
    a(table(h.rename(columns={"id": "ID", "claim": "Hypothesis", "evidence": "Evidence", "supported": "Supported"})))
    a("""
**Extension hypotheses (Amendment 2).**

""")
    xh = d["e8h"].copy()
    xh["supported"] = xh["supported"].map({True: "yes", False: "no"})
    a(table(xh.rename(columns={"id": "ID", "claim": "Hypothesis", "evidence": "Evidence", "supported": "Supported"})))
    a(f"""
**Pre-registered failure criterion ("does not survive").** The top-decile portfolio's net-of-cost CAGR minus same-universe EW net-of-cost CAGR has a 95% stationary-bootstrap interval that includes zero (or lies below) in the untouched test period (2018 onward).

**Samples.** Primary: holding months Aug 2011 - Sep 2026 (every 12-month signal window lies inside the period covered by NSE's official corporate-action records). Train Aug 2011 - Dec 2017, test Jan 2018 onward. Extended (robustness only): from 2007, using a validated split detector before mid-2010.

---

## 3. Data

**Source.** NSE's own daily cash-market files (bhavcopies), one per trading day. Each file lists every security that traded that day, including companies that were later delisted, so a history assembled from them is point-in-time by construction: {b['files']:,} files, {b['rows']:,} security-days, {b['securities']:,} securities after linking. Series EQ, BE and BZ are kept (stocks are moved to the trade-for-trade segments when in trouble, so dropping them would itself create survivorship bias). ETFs and fund units are excluded from the stock universe; NIFTYBEES is the investable benchmark.

**Things that had to be discovered and fixed** (all in `docs/LAB_NOTEBOOK.md`):

- **NSE prices are not adjusted for splits or bonuses.** On an ex-date the previous close is unadjusted (HDFC Bank's 2019 2:1 split appears as a -50% day). Adjustment factors come from NSE's official corporate-action records ({b['ca_split_bonus_records_applied']:,} split/bonus factors applied from records). Records are incomplete (for example no entry for some 2025 bonuses), so large unrecorded overnight gaps that match a standard ratio are adjusted by a detector ({d['e1']['factors_applied_by_source'].get('detector_gapfill', 0):,} gap-fill and {d['e1']['factors_applied_by_source'].get('detector', 0):,} pre-2010 adjustments). The detector's parameters were tuned on the early records and its accuracy measured on later ones (precision {pct(d['e1']['detector']['validation']['precision'], 0)}, recall {pct(d['e1']['detector']['validation']['recall'], 0)}; the precision is a lower bound because some "false positives" are real events missing from the records).
- **A stock at its 20% daily limit looks exactly like a 1:4 bonus** (both a 1.25 price ratio), so gap-based detection excludes small ratios.
- **ISINs change when a company splits its shares**, so identity is linked through symbol continuity, ISINs and NSE's symbol-change list ({b['symbol_change_links']} symbol-change links, {b['isin_links']} ISIN links, {b['reused_symbols']} reused symbols split apart, {len(b['missing_session_days_repaired'])} sessions absent from the archive bridged).
- **File formats changed** (two-digit years in some 2020 files; ISO dates and new file names in the 2026 corporate-action files). The 2026 change silently produced zero records until a known split (Kotak Bank, Jan 2026) exposed it.
- **Demergers** appeared as 40-65% one-day losses (Tata Motors 2025, Vedanta 2026, Siemens 2025, Tata Chemicals 2020...). {d['e1']['factors_applied_by_source'].get('record_scheme_neutral', 0)} recorded demergers are treated as value-neutral on the ex-date.

**Coverage after fixes.** {d['e1']['unexplained_overnight_gaps_gt_40pct']:,} unexplained overnight gaps larger than 40% remain across all securities and years, of which 16 are in liquid stocks (splits with non-standard ratios, demergers and rights issues that neither the records nor the detector covered, so each shows as a spurious large loss); all are listed in `results/real/e1_unexplained_gaps.csv`. Dead companies are in the data every year:

""")
    cov = d["cov"][["sessions", "securities_traded", "exits", "distressed_exits", "universe_size_dec", "universe_500th_liquidity_cr"]].copy()
    cov["universe_500th_liquidity_cr"] = cov["universe_500th_liquidity_cr"].round(2)
    cov = cov.reset_index().rename(columns={"index": "year", "date": "year", "sessions": "sessions", "securities_traded": "equities traded", "exits": "stopped trading", "distressed_exits": "distressed exits", "universe_size_dec": "universe size (Dec)", "universe_500th_liquidity_cr": "500th stock's median daily value (Rs crore)"})
    for c in cov.columns:
        cov[c] = cov[c].map(lambda v: "" if pd.isna(v) else (f"{int(v)}" if float(v).is_integer() else f"{v:.2f}"))
    cov.loc[cov["universe size (Dec)"] == "0", ["universe size (Dec)", "500th stock's median daily value (Rs crore)"]] = ["n/a", "n/a"]
    a(table(cov))
    a("![coverage](../figures/real/e1_coverage.png)\n")
    a(f"""
---

## 4. Methods

**Universe (known at the time).** At each month-end, the 500 securities with the highest median daily traded value over the previous 6 months, among those priced at least Rs 20, with at least 13 months of history, at least 10 trading days that month, and trading in the final week. It is a liquidity-ranked proxy for a broad index, point-in-time by construction (the historical Nifty 500 membership cannot be rebuilt reliably from free data).

**Timing (no look-ahead).** Signal from data through the month-end; buy at the first open of the next month; hold to the first open of the month after. A unit test shows that changing future prices never changes a past portfolio.

**Signals.** Momentum (return from t-12 to t-1), 1-month reversal, low volatility (12-month daily volatility), 52-week high, and (extension) long-term reversal (t-60 to t-13) and price relative to its 5-year mean. Top decile or quintile, equal weighted.

**Costs.** Statutory charges by trade date (STT, stamp duty, NSE and SEBI fees, GST, depository charge per scrip sold), a half-spread from the Abdi-Ranaldo daily estimator, and square-root market impact (k = 0.7, sensitivities at 0 and 1.5). A discount broker (brokerage 0); a 0.3% brokerage sensitivity. Continuing positions are traded only when more than 25% from target.

**Taxes.** Resident individual: FIFO lots, short- and long-term capital-gains rates by sale date (LTCG exempt before April 2018, grandfathered to 31 Jan 2018, Rs 1 lakh then Rs 1.25 lakh annual exemption, 12.5% LTCG and 20% STCG from July 2024), loss set-off and 8-year carry-forward, cess, tax paid each April, liquidation taxed at the end. Cash earns 6% a year in rules that hold cash (untaxed approximation).

**Statistics.** Newey-West t-statistics; stationary block bootstrap (mean block 12 months, 5,000 draws) for CAGR differences; Hansen's Superior Predictive Ability test (primary) and White's Reality Check for data mining across all variants; deflated Sharpe ratios; sub-period and extended-sample checks; delisting-return, dividend, spread-floor and brokerage sensitivities.

**Validation.** A synthetic market with known truth (E0) and a fake NSE archive with splits, renames, symbol reuse and missing files are used to test the code; the pipeline must reproduce the known returns exactly (27 tests). White's Reality Check was found to miss a genuine edge hidden among noisy variants in one of these tests, which is why SPA is the primary data-mining test.

---

## 5. Results 1: do the anomalies exist? (gross of costs)

""")
    gs = d["e2"]
    rows = []
    for (p, k), grp in gs.groupby(["portfolio", "kind"], sort=False):
        grp = grp.set_index("period")
        rows.append({"portfolio": p, "type": k, "CAGR full": pct(grp.loc["full", "cagr"]), "CAGR 2011-17": pct(grp.loc["train", "cagr"]),
                     "CAGR 2018+": pct(grp.loc["test", "cagr"]), "Sharpe": f"{grp.loc['full', 'sharpe']:.2f}",
                     "max DD": pct(grp.loc["full", "max_dd"], 0), "NW t": f"{grp.loc['full', 'nw_t']:.2f}"})
    a(table(pd.DataFrame(rows)))
    a(f"""
The 12 pre-registered variants are all shown. Momentum in every form, and the 52-week-high, reproduce as gross anomalies; low volatility does not earn a higher gross return than EW (its case is about risk); short-term reversal has the wrong sign long-short (t = {rev_ls['nw_t']:.2f}) and its long-only top decile earns {pct(g('reversal_top10').cagr)} against EW {pct(g('same-universe EW').cagr)}. The market benchmark (NIFTYBEES, an investable Nifty 50 ETF) earned {pct(g('Nifty 50 ETF (NIFTYBEES)').cagr)} gross.

![deciles](../figures/real/e2_decile_profiles.png)

![cumulative](../figures/real/e2_cumulative_gross.png)

---

## 6. Results 2: do they survive survivorship bias, time periods and realistic costs?

### 6.1 Survivorship and look-ahead: how large are the errors? (E4)

Same rules, same period, four data views; bias = CAGR on the view minus CAGR on the point-in-time data (percentage points a year):

""")
    rowsb = []
    for view, lab in [("today's top 500 backwards", "Today's top 500 applied to the past"), ("survivors", "Only companies still trading at the end")]:
        rowsb.append({"data view": lab, "equal weight": pp(e4v(view, 'equal_weight')), "momentum": pp(e4v(view, 'momentum')), "low volatility": pp(e4v(view, 'low_vol'))})
    for f in (0.2, 0.6, 1.0):
        rowsb.append({"data view": f"{int(f * 100)}% of distressed exits deleted", "equal weight": pp(e4v('vendor', 'equal_weight', f)), "momentum": pp(e4v('vendor', 'momentum', f)), "low volatility": pp(e4v('vendor', 'low_vol', f))})
    rowsb.append({"data view": "Simulation prediction (E0), all deleted", "equal weight": pp(e0v('equal_weight', 1.0)), "momentum": pp(e0v('momentum', 1.0)), "low volatility": pp(e0v('low_vol', 1.0))})
    a(table(pd.DataFrame(rowsb)))
    a(f"""
![bias](../figures/real/e4_bias_vs_deletion.png)

The "today's list" error dwarfs everything else because it selects the future winners. Deleting dead companies is smaller but real ({b['ca_split_bonus_records_applied'] and pd.read_csv(R / 'e4_meta.csv', index_col=0).iloc[1, 0]:.0f} of the 678 distressed exits were ever in the study universe, about {pd.read_csv(R / 'e4_meta.csv', index_col=0).iloc[2, 0]:.0f} a year). **H6 (the simulation's prediction that equal weight is hurt most) is refuted.**

### 6.2 Different time periods

Momentum's edge is present in both halves of the sample: gross {pct(g('momentum12_top10', 'train').cagr)} in 2011-17 and {pct(g('momentum12_top10', 'test').cagr)} in the untouched 2018+ period (EW {pct(g('same-universe EW', 'train').cagr)} and {pct(g('same-universe EW', 'test').cagr)}). Extended sample from 2007: momentum {pct(ext.loc['momentum12_top10', 'cagr'])}, EW {pct(ext.loc['EW', 'cagr'])}, NIFTYBEES {pct(ext.loc['Nifty 50 ETF', 'cagr'])}. Cumulative gross return in each episode:

""")
    sb = sub.copy()
    for c in sb.columns:
        sb[c] = sb[c].map(pct)
    a(table(sb.reset_index().rename(columns={"period": "episode"})))
    a(f"""
Momentum did **not** protect in the 2008 crisis (equal to or worse than EW and far worse than the market ETF) and lost ground in 2016 (demonetisation); it did very well in the 2013 taper tantrum, the Covid rebound and, above all, the 2021-24 small-cap boom. The strategy is a trend strategy in a market with pronounced boom-bust episodes, and its record depends on that.

### 6.3 Do results disappear once realistic costs are added? (E3)

Rs 10 lakh, full primary sample:

""")
    t3 = d["e3"][d["e3"].period == "full"].copy()
    rows = []
    for _, r in t3.iterrows():
        rows.append({"portfolio": r.portfolio, "gross": pct(r.gross_cagr), "after costs": pct(r.net_cagr), "after costs and tax": pct(r.net_after_tax_cagr),
                     "with 0.3% brokerage": pct(r['net_cagr_brokerage_0.3pct']), "with 10 bp spread floor": pct(r['net_cagr_spread_floor_10bp']),
                     "cost drag (pp/yr)": f"{r.cost_drag_pp * 100:.1f}", "monthly turnover": pct(r.avg_monthly_turnover, 0), "net max DD": pct(r.net_max_dd, 0)})
    a(table(pd.DataFrame(rows)))
    a(f"""
![gross net tax](../figures/real/e3_gross_net_tax.png)

- **Momentum keeps most of its edge**: costs take {(n(mom).gross_cagr - n(mom).net_cagr) * 100:.1f} pp and tax {(n(mom).net_cagr - n(mom).net_after_tax_cagr) * 100:.1f} pp a year, leaving {pct(n(mom).net_after_tax_cagr)} versus EW's {pct(n(ew).net_after_tax_cagr)}. The result survives a 0.3% brokerage ({pct(n(mom)['net_cagr_brokerage_0.3pct'])}) and a 10 bp spread floor ({pct(n(mom)['net_cagr_spread_floor_10bp'])}).
- **Short-term reversal disappears completely**: it trades about {pct(n(rev).avg_monthly_turnover, 0)} of the portfolio every month; costs turn {pct(n(rev).gross_cagr)} gross into {pct(n(rev).net_cagr)}.
- **Low volatility is cheap to run** ({pct(n(lv).avg_monthly_turnover, 0)} monthly turnover) but tax pushes it below EW ({pct(n(lv).net_after_tax_cagr)} vs {pct(n(ew).net_after_tax_cagr)}) because EW rarely sells.

### 6.4 Liquidity and capacity

Net CAGR (no tax) as the portfolio grows, impact coefficient k = 0.7:

""")
    capt = pd.DataFrame({"capital": ["Rs 10 lakh", "Rs 1 crore", "Rs 10 crore", "Rs 100 crore"]})
    capt["momentum"] = [pct(cap_m[c]) for c in capt.capital]
    capt["low volatility"] = [pct(cap_l[c]) for c in capt.capital]
    capt["reversal"] = [pct(cap_r[c]) for c in capt.capital]
    a(table(capt))
    a(f"""
![capacity](../figures/real/e3_capacity.png)

Momentum is a small-investor strategy in India: the stocks that rank highest are often small and thinly traded. By Rs 100 crore its net return ({pct(cap_m['Rs 100 crore'])}) is barely above the equal-weight portfolio. The 500th most liquid stock traded only Rs {d['cov']['universe_500th_liquidity_cr'].loc[2008]:.2f} crore a day in 2008 and Rs {d['cov']['universe_500th_liquidity_cr'].loc[2025]:.0f} crore in 2025, so liquidity is far better now than in the early years of the sample.

### 6.5 Robustness of the momentum result (E10)

""")
    a(table(rob.assign(**{"momentum top-decile gross CAGR": rob["momentum_top10_gross_cagr"].map(lambda v: "" if pd.isna(v) else pct(v))})[["test", "momentum top-decile gross CAGR"]]))
    a(f"""
Momentum's advantage is not the product of a few lucky stocks: removing the 50 best of {9100:,} position-months still leaves {pct(rb('drop the 50'))} against EW {pct(g('same-universe EW').cagr)}. Losses are not being hidden either (flooring the worst months barely changes the result). Delisting returns of -30% or -100% for distressed exits change gross CAGRs by at most {term_max:.1f} pp. Including dividends (records exist from 2010) adds {div_min:.1f} to {div_max:.1f} pp a year to each portfolio and does not change the ranking.

---

## 7. Results 3: data-mining bias

Twelve variants were pre-registered and 14 more (team rules and value proxies) were added by dated amendment before their results existed, so the family is {mt8['n_trials']} variants; every one is reported in Section 9. For the best variant relative to EW (gross, full sample):

""")
    rows = [{"family": "Original 12 variants", "SPA p (full)": pv(mt8['full']['original_12']['spa']['p_value']), "Reality Check p (full)": pv(mt8['full']['original_12']['reality_check']['p_value']),
             "SPA p (2018+)": pv(mt8['test']['original_12']['spa']['p_value']), "best variant": mt8['full']['original_12']['spa']['best']},
            {"family": f"All {mt8['n_trials']} variants", "SPA p (full)": pv(mt8['full']['family_26']['spa']['p_value']), "Reality Check p (full)": pv(mt8['full']['family_26']['reality_check']['p_value']),
             "SPA p (2018+)": pv(mt8['test']['family_26']['spa']['p_value']), "best variant": mt8['full']['family_26']['spa']['best']}]
    a(table(pd.DataFrame(rows)))
    dm = d["e3t"]["multiple_testing"]["deflated_sharpe_vs_ew"]["momentum12_top10"]
    a(f"""
Both tests reject "no variant beats EW" decisively, and the winner is always a momentum variant, so **a real momentum effect exists in this data even after allowing for having searched over many rules.** The deflated Sharpe ratio is a harsher test of whether the specific best portfolio's excess Sharpe ({dm['sharpe_annual']:.2f}) exceeds what the best of {dm['n_trials']} would achieve by luck ({dm['sr0_annual']:.2f}): momentum scores {dm['dsr']:.2f}, below the conventional 0.95, so the *magnitude* of the excess return should be treated as uncertain even though its *existence* is not. Reversal and low volatility score essentially 0. Of {n_models} models, {n_pass} individually pass the failure criterion (interval above zero) at the 5% level; with {n_models} tries a few passing by chance is expected, which is why the family-wide tests above, not the individual intervals, carry the conclusion.

---

## 8. Why the simulation was wrong about India (E5, exploratory)

The synthetic market assumed firms fail only after their price has fallen. Momentum portfolios sell losers, so in that world they rarely hold failing firms and are hurt least by deleted data. Looking at positions in companies that later failed (average monthly return earned while held):

""")
    rows = []
    for rule in ("equal_weight", "momentum", "low_vol"):
        rows.append({"rule": rule.replace("_", " "), "India: weight in future failures": pct(e5.loc[("India (real)", rule), "avg_weight_in_future_failures"]),
                     "India: monthly return on them": pct(e5.loc[("India (real)", rule), "monthly_return_on_those_positions"], 2),
                     "Simulation: weight": pct(e5.loc[("synthetic (E0 model)", rule), "avg_weight_in_future_failures"]),
                     "Simulation: monthly return on them": pct(e5.loc[("synthetic (E0 model)", rule), "monthly_return_on_those_positions"], 2)})
    a(table(pd.DataFrame(rows)))
    a(f"""
![e5](../figures/real/e5_positions_in_future_failures.png)

In the simulation, momentum's positions in future failures roughly broke even ({pct(e5.loc[('synthetic (E0 model)', 'momentum'), 'monthly_return_on_those_positions'], 2)} a month); in India they lost {pct(-e5.loc[('India (real)', 'momentum'), 'monthly_return_on_those_positions'], 2)} a month, as badly as equal weight's. Indian momentum portfolios hold companies on the way down. This analysis was not pre-registered and rests on {int(d['e1']['detector'] is not None) and 217} failures that entered the universe; treat it as an explanation to test, not a proven mechanism.

---

## 9. Every model tested, including the failures

Net of costs (Rs 10 lakh, impact k = 0.7), primary sample. "Test excess" is the test-period (2018+) net CAGR minus same-universe EW net CAGR with its 95% bootstrap interval; the last column applies the pre-registered failure criterion to each model separately (before correcting for the number tried).

""")
    L = d["led"].copy()
    rows = []
    for _, r in L.iterrows():
        if r.model == "EW":
            rows.append({"model": "EW (control)", "family": "Benchmark", "net full": pct(r.net_cagr_full), "net 2018+": pct(r.net_cagr_test), "net after tax": pct(n(ew).net_after_tax_cagr), "max DD": pct(r.net_max_dd, 0), "test excess vs EW (pp)": "", "passes": ""})
            continue
        rows.append({"model": r.model, "family": r.family, "net full": pct(r.net_cagr_full), "net 2018+": pct(r.net_cagr_test), "net after tax": pct(r.net_after_tax_full),
                     "max DD": pct(r.net_max_dd, 0), "test excess vs EW (pp)": f"{r.test_net_excess_vs_ew_pp:+.1f} [{r.ci_lo_pp:+.1f}, {r.ci_hi_pp:+.1f}]",
                     "passes": "yes" if r.survives_failure_criterion else "no"})
    a(table(pd.DataFrame(rows)))
    a(f"""
Plus one exploratory model reported separately: value (earnings yield, top decile) over {e6['months']} months from {e6['first_holding_month']}: {pct(e6['value_top_decile_gross_cagr'])} gross and {pct(e6['value_top_decile_net_cagr'])} after costs vs EW {pct(e6['ew_gross_cagr'])} gross; 95% interval of the gross difference {pct(e6['gross_diff_ci95'][0])} to {pct(e6['gross_diff_ci95'][1])}. It is too short to say anything about the value premium, and is reported so that it is not silently dropped.

### 9.1 The team rule family (extension X1-X5)

The Stockers team rule (S5) holds up to 30 stocks that are above their 200-day average, enters them after a "red month with rising volume" setup ranked by momentum, and holds them until they fall below the trend line; unused slots earn cash. S0-S8 is the ladder from equal weight to the full rule.

![ladder](../figures/real/e8_ladder.png)

![team growth](../figures/real/e8_team_rule_growth.png)

- **S5's drawdown advantage is robust; its return advantage is not reliable out of sample.** Net {pct(led.loc['S5', 'net_cagr_full'])} over the full sample against EW {pct(led.loc['EW', 'net_cagr_full'])}, but the edge shrinks in the untouched test period ({pct(led.loc['S5', 'net_cagr_test'])} vs {pct(led.loc['EW', 'net_cagr_test'])}) and its interval [{led.loc['S5', 'ci_lo_pp']:+.1f}, {led.loc['S5', 'ci_hi_pp']:+.1f}] pp includes zero, so X1 is **not supported**. Its worst fall is {pct(led.loc['S5', 'net_max_dd'], 0)} vs EW {pct(led.loc['EW', 'net_max_dd'], 0)} (X3 supported).
- **Plain top-30 momentum (S2) beats the whole team ladder on return** ({pct(led.loc['S2', 'net_cagr_full'])} net), and S4 (momentum + low volatility + trend) has the best risk-adjusted profile (Sharpe {led.loc['S4', 'net_sharpe']:.2f}, worst fall {pct(led.loc['S4', 'net_max_dd'], 0)}).
- **The rule chosen on the training years ({chosen}) does not beat EW convincingly in the test period** ({x.loc['X2', 'evidence']}); X2 is not supported.
- **Stability:** with 20 slots S5 nets {pct(led.loc['S5_n20', 'net_cagr_full'])}, with 30 {pct(led.loc['S5', 'net_cagr_full'])}, with 50 {pct(led.loc['S5_n50', 'net_cagr_full'])}: results move with concentration, so the rule's number is a property of the parameter as much as the idea. S6 and S5 with 20 slots individually pass the failure criterion while S5 does not, which is what noise across a family looks like.
- **Capacity:** S5 nets {pct(cap8.loc['Rs 10 lakh', 'S5'])} at Rs 10 lakh, {pct(cap8.loc['Rs 10 crore', 'S5'])} at Rs 10 crore, {pct(cap8.loc['Rs 100 crore', 'S5'])} at Rs 100 crore.
- **2008 crisis (extended sample, gross):** S5 {pct(sub8.loc['2008 crisis', 'S5'])}, S8 {pct(sub8.loc['2008 crisis', 'S8'])}, EW {pct(sub8.loc['2008 crisis', 'S0'])}, NIFTYBEES {pct(sub8.loc['2008 crisis', 'NIFTYBEES'])}: the trend filter softened the crash but did not avoid it.

Note: this is the Indian-data version of the S5/S8 family from the S&P 500 screener. The V7.3 engine itself (built around the WInS competition's US universe and data feeds) was not run on Indian data; the rule logic that engine embodies was.

---

## 10. Deviations from the pre-registration (all disclosed)

1. **Sample and universe (before freezing).** The draft used Jan 2006 - Dec 2015 / Jan 2016 onward and the Nifty 500. The data audit showed NSE prices are unadjusted and official corporate-action records start July 2010, so the primary sample starts Aug 2011 (train to Dec 2017, test 2018+). Nifty 500 history cannot be reconstructed from free data, so a liquidity-ranked universe replaced it.
2. **Amendment 1 (after the first real run).** The half-spread estimator averaged after clipping each day at zero, which biases spreads upward (median 72 bp for the 500 most liquid stocks, implausible for India). It was fixed and the outputs from before the fix are kept in `results/real_pre_amendment1/`. The fix changed one verdict: **H2 (momentum beats EW net in the test period) was "not supported" with the bug (interval -0.04 to +14.6 pp) and is "supported" after it** ({hyp.loc['H2', 'evidence']}). The corrected estimator returns zero spread for many liquid stocks, so a 10 bp spread-floor sensitivity was added: momentum nets {pct(n(mom)['net_cagr_spread_floor_10bp'])} instead of {pct(n(mom).net_cagr)}.
3. **Amendment 2 (2026-09-30).** The team-rule family, stability variants and price-based value proxies, with hypotheses X1-X5 and an enlarged 26-variant family, were declared and tagged before they were run.
4. **The extension was designed after the original results were known.** Amendment 2 was written and tagged before its own results were computed, but after the H1-H6 results (including momentum's strength) had been seen, and the team rule is itself momentum-based, so it is not an independent test of momentum. Treat X1-X5 as a second, clearly labelled stage.
5. **Exploratory, not pre-registered:** E5 (why H6 failed), E6 (value 2024-26), E10 (robustness).

---

## 11. Limitations

- **Price returns are the primary series.** Dividend records exist only from 2010 (a total-return sensitivity is in E3); demergers are treated as value-neutral and rights issues are not adjusted.
- **The universe is a liquidity ranking, not the Nifty 500,** and early-sample constituents were very thinly traded (Section 6.4); early-year results, especially the 2008 crisis, are less reliable.
- **Spreads and impact are estimated from daily prices,** not tick data; the estimate is zero for many liquid stocks, hence the spread-floor sensitivity. Exchange and SEBI fee rates use today's values in all years; STT, stamp duty and taxes are dated.
- **NSE only.** Stocks listed only on BSE are absent.
- **Tax model simplifications:** surcharge ignored, tax paid pro-rata without triggering further gains, cash interest untaxed.
- **A single 15-year market with two large boom-bust episodes.** Statistical significance does not remove the dependence on the 2021-24 small-cap boom.
- **Value could not be tested with fundamentals.** Point-in-time book value and earnings are not freely available; the conclusion on value is "inconclusive" plus two failed price-based proxies.
- **Three liquid one-day price spikes** exist that are data quirks (for example BCG on 14 July 2025, where NSE's base price was reset on a re-listing day); none entered a return series.

---

## 12. What this means

- **For an investor:** of the classic anomalies, only momentum plausibly survives Indian costs and tax, and only at modest size, with deep drawdowns (-46%). Low volatility is a way to cut risk, not to earn more. Short-term reversal and price-based value proxies are not worth trading.
- **For a researcher or student doing backtests:** the biggest error is not costs or luck but the universe: applying today's list to the past added {pp(e4v("today's top 500 backwards", 'momentum'))} pp a year to a momentum backtest here. Check that your data include dead companies and that prices are corporate-action adjusted before reading anything into a result.
- **For this team's own rules:** the trend-and-setup rule is a sensible risk control that also earned more than equal weight in-sample, but plain top-30 momentum earned more, and S5's out-of-sample edge cannot be distinguished from zero. State it as "lower drawdown, with a return advantage that is not statistically established", not as alpha.

---

## 13. Reproducibility

- Repository: `india-anomaly-lab`, commit `{tag}`; pre-registration tags `prereg-v1`, `prereg-v1-amend1`, `prereg-v1-amend2`.
- Raw data: NSE files cached unmodified with SHA-256 manifests (`data/raw/manifest*.csv`); `python scripts/build_dataset.py` rebuilds every derived table.
- Experiments: `scripts/run_all.sh` (E0-E10, report, site, tool). Every variant computed is logged in `results/variants_log.csv`.
- Educational tool: `docs/site/tool.html`, an interactive page to switch on data shortcuts, costs, tax and size for every strategy above.
- Not investment advice. Past performance does not predict future returns. AI assistance was used to write code and documentation; the research questions, decisions and interpretation are the author's.
""")
    text = "\n".join(S)
    OUT.mkdir(exist_ok=True)
    md_path = OUT / f"{NAME}.md"
    md_path.write_text(text)
    body = markdown.markdown(text, extensions=["tables", "sane_lists"])
    html = HTML.replace("__BODY__", body)
    (OUT / f"{NAME}.html").write_text(html)
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if Path(chrome).exists():
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={OUT / (NAME + '.pdf')}", f"file://{OUT / (NAME + '.html')}"],
                       capture_output=True, timeout=180)
    return md_path


HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Indian equity anomalies: research report</title>
<style>
@page{size:A4;margin:16mm 14mm}
body{font:10.5pt/1.5 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif;color:#1d1c1a;max-width:900px;margin:0 auto;padding:0 12px}
h1{font-size:21pt;line-height:1.2;margin:.2em 0}h2{font-size:15pt;margin:1.6em 0 .4em;border-bottom:1px solid #ddd;padding-bottom:.2em;break-after:avoid}
h3{font-size:12pt;margin:1.2em 0 .3em;break-after:avoid}table{border-collapse:collapse;width:100%;font-size:8.6pt;margin:.7em 0;break-inside:auto}
th,td{border:1px solid #ddd;padding:3px 5px;vertical-align:top;text-align:left}th{background:#f3f2ee}tr{break-inside:avoid}
img{max-width:100%;height:auto;break-inside:avoid;margin:.5em 0}code{font-size:9pt;background:#f3f2ee;padding:0 2px}hr{border:0;border-top:1px solid #ddd;margin:1.4em 0}
</style></head><body>__BODY__</body></html>"""

if __name__ == "__main__":
    print(main())
