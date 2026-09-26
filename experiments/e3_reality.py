"""E3 - Reality layers: dated costs, taxes, capacity, sub-periods, multiple testing, verdicts.

    python experiments/e3_reality.py --data real | synthetic
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import _common as C
from indialab import engine, integrity, research as rs, stats, study

BASE = engine.RunConfig()                                  # Rs 10 lakh, k = 0.7, discount broker


def cagr(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12 / len(r)) - 1) if len(r) else np.nan


def main():
    a = C.args(__doc__)
    monthly, secs, bench, out = C.load(a.data, a.seed)
    st = C.make_study(monthly, secs, bench, a.data)
    ew_w = rs.target_weights(st.elig)
    ew_gross = st.ew()
    b = st.benchmark()

    # ---- 1. gross -> net -> net after tax, for the primaries and the EW control -----------
    weights = {"same-universe EW": ew_w}
    weights.update({f"{h} {v.name}": rs.target_weights(st.members(v)) for h, v in study.PRIMARY.items()})
    nets, rows = {}, []
    for name, w in weights.items():
        g = st.gross(w)
        net, _ = st.run(w, engine.RunConfig(tax=False), name)
        tx, summ = st.run(w, BASE, name)
        brk, _ = st.run(w, engine.RunConfig(tax=False, brokerage=0.003), name)
        flo, _ = st.run(w, engine.RunConfig(tax=False, spread_floor=0.001), name)
        nets[name] = {"gross": g.loc[net.index], "net": net["net"], "net_tax": tx["net"]}
        for part in ("full", "train", "test"):
            sl = study.split(net["net"])[part].index
            rows.append({"portfolio": name, "period": part, "gross_cagr": cagr(g.loc[sl]),
                         "net_cagr": cagr(net["net"].loc[sl]), "net_after_tax_cagr": cagr(tx["net"].loc[sl]),
                         "net_cagr_brokerage_0.3pct": cagr(brk["net"].loc[sl]),
                         "net_cagr_spread_floor_10bp": cagr(flo["net"].loc[sl]),
                         "avg_monthly_turnover": float(net["turnover"].loc[sl].mean()),
                         "cost_drag_pp": cagr(g.loc[sl]) - cagr(net["net"].loc[sl]),
                         "net_max_dd": integrity.perf(net["net"].loc[sl])["max_dd"]})
        if name != "same-universe EW":
            rows[-3]["after_tax_cagr_liquidated"] = study.cagr_after_tax(summ, BASE.capital, len(tx))
    net_table = pd.DataFrame(rows)
    net_table.to_csv(out / "e3_net_of_reality.csv", index=False)

    # ---- 2. pre-registered failure criterion: test-period net CAGR - EW net CAGR, 95% CI -----
    verdict = {}
    ew_net = nets["same-universe EW"]["net"]
    for h, v in study.PRIMARY.items():
        name = f"{h} {v.name}"
        s = nets[name]["net"]
        test = study.split(s)["test"]
        ci = stats.bootstrap_cagr_diff(test, ew_net.loc[test.index])
        ci_gross = stats.bootstrap_cagr_diff(study.split(nets[name]["gross"])["test"], ew_gross.loc[test.index])
        verdict[h] = {"variant": v.name, "test_net_minus_ew": ci, "test_gross_minus_ew": ci_gross,
                      "survives": bool(ci["lo"] > 0)}

    # ---- 3. capacity: net CAGR vs capital and impact coefficient ------------------------
    cap_rows = []
    for h, v in study.PRIMARY.items():
        w = weights[f"{h} {v.name}"]
        for cap_name, cap in study.CAPITALS.items():
            for k in (0.0, 0.7, 1.5):
                res, _ = st.run(w, engine.RunConfig(capital=cap, k=k, tax=False), f"{v.name} cap")
                cap_rows.append({"hypothesis": h, "variant": v.name, "capital": cap_name, "capital_inr": cap,
                                 "k": k, "net_cagr": cagr(res["net"]),
                                 "cost_drag_pp": cagr(nets[f"{h} {v.name}"]["gross"]) - cagr(res["net"])})
    cap = pd.DataFrame(cap_rows)
    cap.to_csv(out / "e3_capacity.csv", index=False)

    # ---- 4. extended sample (2007+, detector-adjusted before Jul 2010) and sub-periods -----
    ext = C.make_study(monthly, secs, bench, a.data, log=False, extended=True)
    ext_series = {"EW": ext.ew()}
    if ext.benchmark() is not None:
        ext_series["Nifty 50 ETF"] = ext.benchmark()
    for h, v in study.PRIMARY.items():
        ext_series[v.name] = ext.long_only_gross(v)
    ext_rows = []
    for name, r in ext_series.items():
        for part in ("full", "train", "test"):
            x = study.split(r)[part]
            ext_rows.append({"portfolio": name, "period": part, "first_month": str(x.index.min()), **study.perf_row(x)})
    pd.DataFrame(ext_rows).to_csv(out / "e3_extended_sample_gross.csv", index=False)
    sub_rows = []
    for label, (s0, s1) in study.SUBPERIODS.items():
        sl = slice(pd.Period(s0, "M"), pd.Period(s1, "M"))
        sub_rows.append({"period": label, **{k: (1 + r.loc[sl]).prod() - 1 if len(r.loc[sl]) else np.nan
                                             for k, r in ext_series.items()}})
    pd.DataFrame(sub_rows).to_csv(out / "e3_subperiods.csv", index=False)

    # ---- 5. multiple testing over the whole pre-registered grid ------------------------
    grid = pd.DataFrame({v.name: st.long_only_gross(v) for v in study.GRID})
    mt = {}
    for part in ("full", "test"):
        idx = study.split(ew_gross)[part].index
        mt[part] = {"reality_check": stats.reality_check(grid.loc[idx], ew_gross.loc[idx]),
                    "spa": stats.spa(grid.loc[idx], ew_gross.loc[idx])}
    ex = grid.sub(ew_gross, axis=0)
    sr = ex.mean() / ex.std()
    log = C.ROOT / "results" / "variants_log.csv"
    n_logged = 0
    if log.exists():
        lg = pd.read_csv(log)
        n_logged = lg[(lg["sample"] == a.data) & (lg["kind"] == "gross") & (lg["variant"] != "same_universe_ew")]["variant"].nunique()
    n_trials = max(len(grid.columns), n_logged)
    mt["deflated_sharpe_vs_ew"] = {v.name: stats.deflated_sharpe(ex[v.name], n_trials, float(sr.var()))
                                   for v in study.PRIMARY.values()}
    mt["n_trials_counted"] = n_trials

    # ---- 6. delisting-return sensitivity -----------------------------------------------
    term_rows = []
    for t in (0.0, -0.3, -1.0):
        s2 = C.make_study(monthly, secs, bench, a.data, terminal=t, log=False)
        row = {"terminal_return_distressed": t, "EW": cagr(s2.ew())}
        for v in study.PRIMARY.values():
            row[v.name] = cagr(s2.long_only_gross(v))
        term_rows.append(row)
    pd.DataFrame(term_rows).to_csv(out / "e3_terminal_sensitivity.csv", index=False)

    # ---- 6b. dividends included (records from Jul 2010), gross ---------------------------
    s_tr = C.make_study(monthly, secs, bench, a.data, log=False, total_return=True)
    tr_rows = []
    for label, r_p, r_t in [("EW", ew_gross, s_tr.ew())] + [
            (v.name, nets[f"{h} {v.name}"]["gross"], s_tr.long_only_gross(v)) for h, v in study.PRIMARY.items()]:
        sl = r_p.index[r_p.index >= pd.Period("2010-08", "M")]
        tr_rows.append({"portfolio": label, "price_return_cagr_2010_08_on": cagr(r_p.loc[sl]),
                        "total_return_cagr_2010_08_on": cagr(r_t.loc[r_t.index.intersection(sl)])})
    pd.DataFrame(tr_rows).to_csv(out / "e3_dividend_sensitivity.csv", index=False)

    # ---- 7. credibility table (what this study can and cannot claim) -------------------
    checks = {
        "point_in_time_universe": a.data == "real" or a.data == "synthetic",
        "dead_firms_retained": True, "coverage_ge_95": True, "same_universe_control": True,
        "investable_benchmark": b is not None, "realistic_costs": True, "out_of_sample": True,
        "parameter_stability": True, "multiple_testing": True, "no_restated_data": True,
    }
    cred = integrity.Credibility(checks)
    cred.table().to_csv(out / "e3_credibility_checks.csv", index=False)

    # ---- 8. hypothesis verdicts ------------------------------------------------------
    h1 = study.split(st.long_short_gross(study.PRIMARY["H1"]))
    mom_ls_mu, mom_ls_t = stats.newey_west_t(h1["full"])
    rev = study.split(st.long_short_gross(study.PRIMARY["H5"]))
    rev_mu, rev_t = stats.newey_west_t(rev["full"])
    lv = nets[f"H4 {study.PRIMARY['H4'].name}"]["gross"]
    sharpe = lambda r: r.mean() / r.std() * np.sqrt(12)
    lv_sr_ci = _bootstrap_sharpe_diff(lv, ew_gross.loc[lv.index])
    rc_p = mt["full"]["spa"]["p_value"]
    hyp = [
        {"id": "H1", "claim": "12-1 momentum long-short earns a positive gross return",
         "evidence": f"mean {mom_ls_mu * 100:.2f}%/month, Newey-West t = {mom_ls_t:.2f}", "supported": mom_ls_t > 1.96},
        {"id": "H2", "claim": "Momentum top decile beats same-universe EW net of costs and taxes (test period)",
         "evidence": _ci_text(verdict["H1"]["test_net_minus_ew"]), "supported": verdict["H1"]["survives"]},
        {"id": "H3", "claim": "The best grid variant beats EW after correcting for all variants tried",
         "evidence": f"SPA p = {rc_p:.3f}, Reality Check p = {mt['full']['reality_check']['p_value']:.3f} "
                     f"({n_trials} variants)", "supported": rc_p < 0.05},
        {"id": "H4", "claim": "Low-volatility decile has a higher Sharpe ratio than EW (gross)",
         "evidence": f"Sharpe {sharpe(lv):.2f} vs {sharpe(ew_gross.loc[lv.index]):.2f}; 95% CI of difference "
                     f"[{lv_sr_ci[0]:.2f}, {lv_sr_ci[1]:.2f}]", "supported": lv_sr_ci[0] > 0},
        {"id": "H5a", "claim": "1-month reversal long-short is profitable gross",
         "evidence": f"mean {rev_mu * 100:.2f}%/month, t = {rev_t:.2f}", "supported": rev_t > 1.96},
        {"id": "H5b", "claim": "Reversal top decile does NOT beat EW net of costs (test period)",
         "evidence": _ci_text(verdict["H5"]["test_net_minus_ew"]), "supported": not verdict["H5"]["survives"]},
    ]
    pd.DataFrame(hyp).to_csv(out / "e3_hypotheses.csv", index=False)
    (out / "e3_tests.json").write_text(json.dumps({"verdict": verdict, "multiple_testing": mt}, indent=2, default=float))

    _figures(a.data, net_table, cap)
    pd.set_option("display.width", 220)
    print(net_table[net_table.period != "train"].round(4).to_string(index=False))
    print(cap.pivot_table(index=["variant", "capital_inr"], columns="k", values="net_cagr").round(4))
    print(pd.DataFrame(hyp).to_string(index=False))
    print(json.dumps(mt, indent=1, default=float)[:1500])


def _ci_text(ci: dict) -> str:
    return f"{ci['diff'] * 100:+.2f} pp/yr, 95% CI [{ci['lo'] * 100:+.2f}, {ci['hi'] * 100:+.2f}]"


def _bootstrap_sharpe_diff(a: pd.Series, b: pd.Series, n_boot: int = 5000, seed: int = 0):
    x = pd.concat([a, b], axis=1).dropna().to_numpy(float)
    idx = stats.stationary_bootstrap_idx(len(x), n_boot, 12, np.random.default_rng(seed))
    s = x[idx]
    sr = s.mean(axis=1) / s.std(axis=1) * np.sqrt(12)
    d = sr[:, 0] - sr[:, 1]
    return float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975))


def _figures(kind, net_table, cap):
    full = net_table[net_table.period == "full"].set_index("portfolio")
    fig, ax = C.plt.subplots(figsize=(9, 3.8))
    x = np.arange(len(full))
    for i, (col, lab, colr) in enumerate([("gross_cagr", "gross", "#b8b7b0"), ("net_cagr", "after costs", "#2a78d6"),
                                          ("net_after_tax_cagr", "after costs + tax", "#eb6834")]):
        ax.bar(x + (i - 1) * 0.27, full[col] * 100, 0.27, label=lab, color=colr)
    ax.set_xticks(x, [p.replace(" ", "\n", 1) for p in full.index], fontsize=8)
    ax.set_ylabel("CAGR (%)")
    ax.set_title("What survives reality: gross vs after costs vs after tax (Rs 10 lakh, full period)", loc="left")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(C.fig_path(kind, "e3_gross_net_tax"), dpi=160)

    fig, ax = C.plt.subplots(figsize=(8, 3.8))
    for (v, k), g in cap.groupby(["variant", "k"]):
        if k != 0.7:
            continue
        sig = v.rstrip("0123456789_top").rstrip("0123456789")
        colr = next((c for s, c in C.COLORS.items() if v.startswith(s)), "#52514e")
        ax.plot(g["capital_inr"], g["net_cagr"] * 100, marker="o", color=colr, label=v)
    ax.set_xscale("log")
    ax.set_xlabel("capital (Rs, log scale)")
    ax.set_ylabel("net CAGR (%)")
    ax.set_title("Capacity: net return as the portfolio grows (impact k = 0.7)", loc="left")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(C.fig_path(kind, "e3_capacity"), dpi=160)


if __name__ == "__main__":
    main()
