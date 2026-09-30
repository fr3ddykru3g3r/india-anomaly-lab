"""E9 - One ledger with EVERY model tested (original 12 + team family + value proxies), all after
costs and tax, with the pre-registered failure criterion applied to each. Descriptive: adds no new
variants (all are already in the 26-variant family).

    python experiments/e9_all_models_ledger.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
from indialab import engine, integrity, research as rs, stats, study, teamrules as T

CASH = T.CASH_YIELD


def cagr(r):
    return float((1 + r).prod() ** (12 / len(r)) - 1) if len(r) else np.nan


def main():
    monthly, secs, bench, out = C.load("real")
    st = C.make_study(monthly, secs, bench, "real", log=False)
    feat = T.load_features(C.ROOT / "data" / "processed", st.p)
    ew_w = rs.target_weights(st.elig)
    ew_net = st.run(ew_w, engine.RunConfig(tax=False, cash_yield=CASH), "S0")[0]["net"]
    test = study.split(ew_net)["test"].index
    models = {}
    for v in study.GRID:
        models[v.name] = ("Original grid", v.signal, rs.target_weights(st.members(v)), 0.0)
    for spec in T.SPECS[1:]:
        models[spec.key] = ("Team rule", spec.label, T.weights(spec, st.p, st.elig, feat), CASH)
    for k, n in (("S5_n20", 20), ("S5_n50", 50)):
        models[k] = ("Team rule (stability)", f"S5 with {n} slots", T.weights(T.SPECS[5], st.p, st.elig, feat, n_hold=n), CASH)
    for k, (score, n, lab) in {"ltrev_top10": (T.lt_reversal(st.p), 10, "Long-term reversal, decile"),
                               "ltrev_top20": (T.lt_reversal(st.p), 5, "Long-term reversal, quintile"),
                               "cheap5y_top10": (T.cheap_vs_5y_mean(st.p), 10, "Cheap vs 5-year mean, decile"),
                               "cheap5y_top20": (T.cheap_vs_5y_mean(st.p), 5, "Cheap vs 5-year mean, quintile")}.items():
        q = rs.quantile_portfolios(score, st.elig, n_q=n)
        models[k] = ("Value proxy (price-based)", lab, rs.target_weights(q.eq(n)), 0.0)
    rows = []
    for name, (family, label, w, cy) in models.items():
        g = T.gross_with_cash(st, w, cy) if cy else st.gross(w)
        net = st.run(w, engine.RunConfig(tax=False, cash_yield=cy), name)[0]
        tx, _ = st.run(w, engine.RunConfig(cash_yield=cy), name)
        n_, t_ = net["net"], tx["net"]
        ci = stats.bootstrap_cagr_diff(n_.loc[test], ew_net.loc[test])
        rows.append({"model": name, "family": family, "description": label,
                     "gross_cagr_full": cagr(g.loc[n_.index]), "gross_cagr_test": cagr(g.loc[test]),
                     "net_cagr_full": cagr(n_), "net_cagr_test": cagr(n_.loc[test]),
                     "net_after_tax_full": cagr(t_), "net_max_dd": integrity.perf(n_)["max_dd"],
                     "net_sharpe": integrity.perf(n_)["sharpe"], "turnover": float(net["turnover"].mean()),
                     "test_net_excess_vs_ew_pp": ci["diff"] * 100, "ci_lo_pp": ci["lo"] * 100, "ci_hi_pp": ci["hi"] * 100,
                     "survives_failure_criterion": bool(ci["lo"] > 0)})
        print(name, flush=True)
    led = pd.DataFrame(rows)
    ew = {"model": "EW", "family": "Benchmark", "description": "Same-universe equal weight",
          "net_cagr_full": cagr(ew_net), "net_cagr_test": cagr(ew_net.loc[test]),
          "net_max_dd": integrity.perf(ew_net)["max_dd"], "net_sharpe": integrity.perf(ew_net)["sharpe"]}
    led = pd.concat([pd.DataFrame([ew]), led], ignore_index=True)
    led.to_csv(out / "e9_all_models_ledger.csv", index=False)
    pd.set_option("display.width", 250)
    print(led.round(3).drop(columns=["description"]).to_string(index=False))


if __name__ == "__main__":
    main()
