"""E8 - The Stockers team rule family (S0-S8) and price-based value proxies, pre-registered as
Amendment 2 (tag prereg-v1-amend2). Extension hypotheses X1-X5, 26-variant multiple-testing family.

    python experiments/e8_team_rules.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import _common as C
from indialab import engine, integrity, research as rs, stats, study, teamrules as T

CASH = T.CASH_YIELD


def cagr(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12 / len(r)) - 1) if len(r) else np.nan


def ci_text(ci: dict) -> str:
    return f"{ci['diff'] * 100:+.2f} pp/yr, 95% CI [{ci['lo'] * 100:+.2f}, {ci['hi'] * 100:+.2f}]"


def main():
    monthly, secs, bench, out = C.load("real")
    st = C.make_study(monthly, secs, bench, "real")
    feat = T.load_features(C.ROOT / "data" / "processed", st.p)
    st.elig = st.elig            # primary sample universe
    weights = {}
    for spec in T.SPECS:
        weights[spec.key] = T.weights(spec, st.p, st.elig, feat)
        print(spec.key, "weights done", flush=True)
    weights["S5_n20"] = T.weights(T.SPECS[5], st.p, st.elig, feat, n_hold=20)
    weights["S5_n50"] = T.weights(T.SPECS[5], st.p, st.elig, feat, n_hold=50)

    # ---- value proxies (fully invested, equal weight within the group) -------------------------
    value = {"ltrev_top10": (T.lt_reversal(st.p), 10), "ltrev_top20": (T.lt_reversal(st.p), 5),
             "cheap5y_top10": (T.cheap_vs_5y_mean(st.p), 10), "cheap5y_top20": (T.cheap_vs_5y_mean(st.p), 5)}
    for k, (score, n) in value.items():
        q = rs.quantile_portfolios(score, st.elig, n_q=n)
        weights[k] = rs.target_weights(q.eq(n))

    ew_w = rs.target_weights(st.elig)
    cfg = engine.RunConfig(cash_yield=CASH)
    res, rows, gross = {}, [], {}
    ew_gross = st.ew()
    for key, w in weights.items():
        st._log("gross", key)
        g = T.gross_with_cash(st, w, CASH)
        gross[key] = g
        net, _ = st.run(w, engine.RunConfig(tax=False, cash_yield=CASH), key)
        tx, summ = st.run(w, cfg, key)
        net0, _ = st.run(w, engine.RunConfig(tax=False, cash_yield=0.0), key)
        res[key] = {"gross": g.loc[net.index], "net": net["net"], "net_tax": tx["net"]}
        cashw = (1 - w.loc[st.months].sum(axis=1)).clip(lower=0)
        for part in ("full", "train", "test"):
            sl = study.split(net["net"])[part].index
            rows.append({"portfolio": key, "period": part, "gross_cagr": cagr(g.loc[sl]),
                         "net_cagr": cagr(net["net"].loc[sl]), "net_after_tax_cagr": cagr(tx["net"].loc[sl]),
                         "net_cagr_cash0": cagr(net0["net"].loc[sl]),
                         "net_sharpe": integrity.perf(net["net"].loc[sl]).get("sharpe"),
                         "net_max_dd": integrity.perf(net["net"].loc[sl]).get("max_dd"),
                         "avg_monthly_turnover": float(net["turnover"].loc[sl].mean()),
                         "avg_names": float(net["names"].loc[sl].mean()),
                         "avg_cash_weight": float(cashw.loc[sl.map(lambda m: m - 1)].mean())})
        print(key, "run done", flush=True)
    tbl = pd.DataFrame(rows)
    tbl.to_csv(out / "e8_team_and_value_net_of_reality.csv", index=False)

    # ---- extension hypotheses -------------------------------------------------------------------
    ew_net, _ = st.run(ew_w, engine.RunConfig(tax=False, cash_yield=CASH), "S0")
    ew_net = ew_net["net"]
    test_idx = study.split(ew_net)["test"].index
    ver = {}

    def beat(key):
        return stats.bootstrap_cagr_diff(res[key]["net"].loc[test_idx], ew_net.loc[test_idx])

    train_idx = study.split(ew_net)["train"].index
    cand = [f"S{i}" for i in range(1, 9)]
    train_sharpe = {k: integrity.perf(res[k]["net"].loc[train_idx]).get("sharpe", np.nan) for k in cand}
    chosen = max(train_sharpe, key=lambda k: train_sharpe[k])
    ddw = lambda k: integrity.perf(res[k]["net"])["max_dd"]
    ver["X1"] = {"claim": "S5 beats same-universe EW net of costs (test period)", "ci": beat("S5")}
    ver["X2"] = {"claim": f"Rule chosen on training data ({chosen}) beats EW net (test period)", "ci": beat(chosen),
                 "chosen": chosen, "train_sharpe": train_sharpe}
    ver["X3"] = {"claim": "S5 has a smaller net max drawdown than EW (full sample)",
                 "s5": ddw("S5"), "ew": ddw("S0")}
    ver["X4"] = {"claim": "Long-term reversal (decile) beats EW net (test period)", "ci": beat("ltrev_top10")}
    ver["X5"] = {"claim": "Cheap vs 5y mean (decile) beats EW net (test period)", "ci": beat("cheap5y_top10")}
    hyp = []
    for k in ("X1", "X2", "X4", "X5"):
        v = ver[k]
        hyp.append({"id": k, "claim": v["claim"], "evidence": ci_text(v["ci"]), "supported": bool(v["ci"]["lo"] > 0)})
    hyp.insert(2, {"id": "X3", "claim": ver["X3"]["claim"],
                   "evidence": f"S5 {ver['X3']['s5'] * 100:.1f}% vs EW {ver['X3']['ew'] * 100:.1f}%",
                   "supported": bool(abs(ver["X3"]["s5"]) < abs(ver["X3"]["ew"]))})
    pd.DataFrame(hyp).to_csv(out / "e8_extension_hypotheses.csv", index=False)

    # ---- 26-variant multiple-testing family (gross long-only vs same-universe EW) --------------
    orig = pd.read_csv(out / "e2_long_only_gross_returns.csv", index_col=0)
    orig.index = pd.PeriodIndex(orig.index, freq="M")
    ext = pd.DataFrame({k: gross[k] for k in cand + ["S5_n20", "S5_n50"] + list(value)})
    fam = orig.join(ext, how="inner")
    mt = {}
    for part in ("full", "test"):
        idx = study.split(ew_gross)[part].index.intersection(fam.index)
        mt[part] = {"family_26": {"spa": stats.spa(fam.loc[idx], ew_gross.loc[idx]),
                                  "reality_check": stats.reality_check(fam.loc[idx], ew_gross.loc[idx])},
                    "original_12": {"spa": stats.spa(orig.loc[idx], ew_gross.loc[idx]),
                                    "reality_check": stats.reality_check(orig.loc[idx], ew_gross.loc[idx])},
                    "extension_only_14": {"spa": stats.spa(ext.loc[idx], ew_gross.loc[idx])}}
    exs = fam.sub(ew_gross.reindex(fam.index), axis=0)
    srv = float((exs.mean() / exs.std()).var())
    mt["deflated_sharpe_vs_ew_net"] = {}
    for k in ("S5", chosen, "ltrev_top10", "cheap5y_top10"):
        x = res[k]["net"] - ew_net.reindex(res[k]["net"].index)
        mt["deflated_sharpe_vs_ew_net"][k] = stats.deflated_sharpe(x, 26, srv)
    mt["n_trials"] = 26

    # ---- capacity, extended sample, sub-periods ------------------------------------------------
    cap_rows = []
    for k in ("S5", chosen):
        for cap_name, capv in study.CAPITALS.items():
            r, _ = st.run(weights[k], engine.RunConfig(capital=capv, tax=False, cash_yield=CASH), k)
            cap_rows.append({"portfolio": k, "capital": cap_name, "net_cagr": cagr(r["net"])})
    pd.DataFrame(cap_rows).to_csv(out / "e8_capacity.csv", index=False)

    ext_st = C.make_study(monthly, secs, bench, "real", log=False, extended=True)
    ext_feat = T.load_features(C.ROOT / "data" / "processed", ext_st.p)
    e_rows, e_series = [], {"S0": T.gross_with_cash(ext_st, T.weights(T.SPECS[0], ext_st.p, ext_st.elig, ext_feat), CASH)}
    for spec in T.SPECS[1:]:
        e_series[spec.key] = T.gross_with_cash(ext_st, T.weights(spec, ext_st.p, ext_st.elig, ext_feat), CASH)
    bm = ext_st.benchmark()
    if bm is not None:
        e_series["NIFTYBEES"] = bm
    for k, r in e_series.items():
        e_rows.append({"portfolio": k, "first_month": str(r.index.min()), **study.perf_row(r)})
    pd.DataFrame(e_rows).to_csv(out / "e8_extended_sample_gross.csv", index=False)
    sub = []
    for label, (s0, s1) in study.SUBPERIODS.items():
        sl = slice(pd.Period(s0, "M"), pd.Period(s1, "M"))
        sub.append({"period": label, **{k: float((1 + r.loc[sl]).prod() - 1) if len(r.loc[sl]) else np.nan
                                          for k, r in e_series.items() if k in ("S0", "S1", "S3", "S5", "S8", "NIFTYBEES")}})
    pd.DataFrame(sub).to_csv(out / "e8_subperiods.csv", index=False)

    (out / "e8_tests.json").write_text(json.dumps({"verdicts": ver, "multiple_testing": mt, "chosen": chosen},
                                                  indent=2, default=float))
    _figures(res, ew_net, chosen, tbl, bench_series=st.benchmark())
    pd.set_option("display.width", 220)
    print(tbl[tbl.period != "train"].round(3).to_string(index=False))
    print(pd.DataFrame(hyp).to_string(index=False))
    print(json.dumps(mt, indent=1, default=float)[:1800])


def _figures(res, ew_net, chosen, tbl, bench_series):
    fig, ax = C.plt.subplots(figsize=(9.5, 4.3))
    for k, lab, col, lw in [("S5", "S5 team rule (net)", "#eb6834", 2.0), (chosen, f"{chosen} chosen on training (net)", "#9b59b6", 1.4)]:
        r = res[k]["net"]
        ax.plot(r.index.to_timestamp(), (1 + r).cumprod(), color=col, lw=lw, label=lab)
    ax.plot(ew_net.index.to_timestamp(), (1 + ew_net).cumprod(), color="#52514e", lw=1.6, label="same-universe EW (net)")
    if bench_series is not None:
        b = bench_series.reindex(ew_net.index).fillna(0)
        ax.plot(b.index.to_timestamp(), (1 + b).cumprod(), color="#8a8983", lw=1.1, label="Nifty 50 ETF (gross)")
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(C.plt.FuncFormatter(lambda y, _: f"Rs {y:g}"))
    ax.yaxis.set_minor_formatter(C.plt.NullFormatter())
    test0 = (study.TRAIN_END + 1).start_time
    ax.axvline(test0, color="#b8b7b0", lw=1)
    ax.set_title("Team rule after costs (Rs 10 lakh): growth of Rs 1, log scale", loc="left")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(C.fig_path("real", "e8_team_rule_growth"), dpi=160)
    keys = [f"S{i}" for i in range(0, 9)]
    fig, ax = C.plt.subplots(figsize=(9.5, 4))
    x = np.arange(len(keys))
    for i, (part, col) in enumerate([("train", "#b8b7b0"), ("test", "#eb6834")]):
        g = tbl[(tbl.period == part) & tbl.portfolio.isin(keys)].set_index("portfolio").loc[keys]
        ax.bar(x + (i - 0.5) * 0.38, g["net_cagr"] * 100, 0.38, color=col, label=f"{part} ({'2011-17' if part == 'train' else '2018+'})")
    ax.set_xticks(x, keys)
    ax.set_ylabel("net CAGR (%)")
    ax.set_title("Team rule ladder after costs: S0 equal weight -> S8", loc="left")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(C.fig_path("real", "e8_ladder"), dpi=160)


if __name__ == "__main__":
    main()
