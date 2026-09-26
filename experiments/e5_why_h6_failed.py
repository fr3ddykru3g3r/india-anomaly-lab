"""E5 (EXPLORATORY, not pre-registered) - Why was H6 refuted?

H6 assumed, like the synthetic market, that momentum portfolios escape failing firms. Tests:
(1) how much weight each rule held in firms that later failed, and what those positions
earned while held; (2) momentum ranks of failures before exit (few failures are still in
the universe near exit, so this one is low-powered). Both compared with the synthetic market.

    python experiments/e5_why_h6_failed.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
from indialab import research as rs, study, synthetic as S

LAGS = [1, 3, 6, 12]


def ranks_before_exit(p: rs.Panel, elig: pd.DataFrame, dead: pd.Index, last_month: pd.Series) -> pd.DataFrame:
    mom = rs.momentum(p, 12, 1).where(elig)
    pct = mom.rank(axis=1, pct=True)
    rows = []
    for sec in dead.intersection(pct.columns):
        m = last_month[sec]
        for lag in LAGS:
            t = m - lag
            if t in pct.index and pd.notna(pct.at[t, sec]):
                rows.append({"sec": sec, "lag": lag, "pct": pct.at[t, sec]})
    return pd.DataFrame(rows)


def holdings_in_future_failures(p: rs.Panel, elig: pd.DataFrame, dead: pd.Index, label: str) -> pd.DataFrame:
    """For each rule: share of portfolio weight in firms that later fail, the monthly return
    earned on those positions, and their contribution to the portfolio return (pp/yr)."""
    st = study.Study(p, first_hold=study.PRIMARY_FIRST_HOLD if label.startswith("India") else None)
    st.elig = elig
    r = st.hold.loc[st.months].fillna(0.0)
    rules = {"equal_weight": rs.target_weights(elig),
             "momentum": rs.target_weights(st.members(study.Variant("momentum", 12, 0.1))),
             "low_vol": rs.target_weights(st.members(study.Variant("low_vol", 12, 0.1)))}
    rows = []
    for name, w in rules.items():
        w = w.loc[st.months]
        d = w.columns.isin(dead)
        wd = w.loc[:, d].sum(axis=1)
        contrib = (w.loc[:, d] * r.loc[:, d]).sum(axis=1)
        ret_dead = contrib.sum() / wd.sum() if wd.sum() > 0 else np.nan
        rest = (w.loc[:, ~d] * r.loc[:, ~d]).sum(axis=1)
        ret_rest = rest.sum() / w.loc[:, ~d].sum(axis=1).sum()
        rows.append({"sample": label, "rule": name, "avg_weight_in_future_failures": float(wd.mean()),
                     "monthly_return_on_those_positions": float(ret_dead),
                     "monthly_return_on_other_positions": float(ret_rest),
                     "drag_pp_per_year": float((contrib - wd * ret_rest).mean() * 12 * 100)})
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame, label: str) -> pd.DataFrame:
    g = df.groupby("lag")["pct"]
    return pd.DataFrame({"sample": label, "n": g.size(), "median_percentile": g.median(),
                         "share_in_top_decile": g.apply(lambda x: (x > 0.9).mean()),
                         "share_in_top_half": g.apply(lambda x: (x > 0.5).mean()),
                         "share_in_bottom_decile": g.apply(lambda x: (x <= 0.1).mean())}).reset_index()


def main():
    monthly, secs, bench, out = C.load("real")
    p = rs.Panel.from_monthly(monthly, secs)
    elig = rs.universe(p)
    dead = secs.index[secs["exited"].astype(bool) & secs["distressed_exit"].astype(bool)]
    last = pd.PeriodIndex(pd.to_datetime(secs.loc[dead, "last_date"]), freq="M")
    real = summarise(ranks_before_exit(p, elig, dead, pd.Series(last, index=dead)), "India (real)")

    R, info = S.simulate(S.MarketParams(n_firms=1200, months=249), seed=0)
    ms, ss = S.to_monthly_panel(R, info)
    ps = rs.Panel.from_monthly(ms, ss)
    el = rs.universe(ps)
    sd = ss.index[ss["distressed_exit"].astype(bool)]
    sl = pd.PeriodIndex(pd.to_datetime(ss.loc[sd, "last_date"]), freq="M")
    syn = summarise(ranks_before_exit(ps, el, sd, pd.Series(sl, index=sd)), "synthetic (E0 model)")

    exposure = pd.concat([holdings_in_future_failures(p, elig, dead, "India (real)"),
                          holdings_in_future_failures(ps, el, sd, "synthetic (E0 model)")], ignore_index=True)
    exposure.to_csv(out / "e5_exposure_to_future_failures.csv", index=False)
    print(exposure.round(4).to_string(index=False))
    res = pd.concat([real, syn], ignore_index=True)
    res.to_csv(out / "e5_momentum_before_exit.csv", index=False)

    fig, ax = C.plt.subplots(figsize=(8.5, 3.8))
    rules = ["equal_weight", "momentum", "low_vol"]
    x = np.arange(len(rules))
    for i, (lab, col) in enumerate([("India (real)", "#eb6834"), ("synthetic (E0 model)", "#8a8983")]):
        g = exposure[exposure["sample"] == lab].set_index("rule").loc[rules]
        ax.bar(x + (i - 0.5) * 0.38, g["monthly_return_on_those_positions"] * 100, 0.38, color=col, label=lab)
    ax.axhline(0, color="#52514e", lw=0.8)
    ax.set_xticks(x, ["equal weight", "momentum", "low volatility"])
    ax.set_ylabel("avg monthly return while held (%)")
    ax.set_title("Exploratory: return earned on positions in companies that later failed", loc="left")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(C.fig_path("real", "e5_positions_in_future_failures"), dpi=160)
    pd.set_option("display.width", 200)
    print(res.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
