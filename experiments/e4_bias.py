"""E4 - How much do the common data shortcuts inflate Indian backtests? (tests H6)

Views of the same market, same rules, gross returns:
    point-in-time        every security as it traded, dead ones included (the baseline)
    survivors            only securities still trading at the end of the data
    today's top 500      the final month's universe applied to the whole history
    vendor-x%            x% of distressed exits deleted (as free data sources do), 5 random draws

Bias = CAGR(view) - CAGR(point-in-time). Compared with the synthetic prediction from E0.

    python experiments/e4_bias.py --data real | synthetic
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
from indialab import research as rs, study

RULES = {"equal_weight": None, "momentum": study.Variant("momentum", 12, 0.1),
         "low_vol": study.Variant("low_vol", 12, 0.1)}
FRACTIONS = np.round(np.arange(0, 1.01, 0.2), 1)
DRAWS = 5


def cagr(r):
    return float((1 + r).prod() ** (12 / len(r)) - 1)


def rule_returns(st: study.Study, members_filter: pd.DataFrame | None = None) -> dict:
    out = {}
    if members_filter is not None:
        st.elig = st.elig & members_filter.reindex(index=st.elig.index, columns=st.elig.columns).fillna(False)
    for name, v in RULES.items():
        out[name] = st.gross(rs.target_weights(st.elig)) if v is None else st.gross(rs.target_weights(st.members(v)))
    return out


def main():
    a = C.args(__doc__)
    monthly, secs, bench, out = C.load(a.data, a.seed)
    base = C.make_study(monthly, secs, bench, a.data, log=False)
    truth = rule_returns(base)
    idx = truth["equal_weight"].index
    rows = []

    alive = secs.index[~secs["exited"].astype(bool)]
    surv = C.make_study(monthly[monthly["sec"].isin(alive)], secs.loc[secs.index.isin(alive)], bench, a.data, log=False)
    for name, r in rule_returns(surv).items():
        rows.append({"view": "survivors", "fraction": np.nan, "draw": 0, "rule": name,
                     "bias_pp": (cagr(r.loc[idx]) - cagr(truth[name])) * 100})

    last = base.elig.index[base.elig.any(axis=1).values][-1]
    today = base.elig.columns[base.elig.loc[last].values]
    loose = rs.UniverseRule(size=100_000)
    tstudy = C.make_study(monthly[monthly["sec"].isin(today)], secs.loc[secs.index.isin(today)], bench, a.data, log=False)
    tstudy.elig = rs.universe(tstudy.p, loose)
    for name, r in rule_returns(tstudy).items():
        rows.append({"view": "today's top 500 backwards", "fraction": np.nan, "draw": 0, "rule": name,
                     "bias_pp": (cagr(r.loc[idx.intersection(r.index)]) - cagr(truth[name].loc[idx.intersection(r.index)])) * 100})

    dead = secs.index[secs["exited"].astype(bool) & secs["distressed_exit"].astype(bool)]
    for f in FRACTIONS:
        for d in range(DRAWS if 0 < f < 1 else 1):
            rng = np.random.default_rng(1000 * d + int(f * 100))
            drop = pd.Index(rng.choice(dead, size=int(round(f * len(dead))), replace=False)) if len(dead) else pd.Index([])
            keep = ~monthly["sec"].isin(drop)
            v = C.make_study(monthly[keep], secs.drop(index=drop), bench, a.data, log=False)
            for name, r in rule_returns(v).items():
                rows.append({"view": "vendor", "fraction": f, "draw": d, "rule": name,
                             "bias_pp": (cagr(r.loc[idx]) - cagr(truth[name])) * 100})
        print(f"fraction {f:.1f} done", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(out / "e4_bias_raw.csv", index=False)
    summ = df.groupby(["view", "fraction", "rule"], dropna=False)["bias_pp"].agg(["mean", "std"]).reset_index()
    summ.to_csv(out / "e4_bias_summary.csv", index=False)
    years = len(idx) / 12
    in_universe_dead = base.elig.loc[:, base.elig.columns.isin(dead)].any().sum()
    meta = {"distressed_exits_total": int(len(dead)), "distressed_exits_ever_in_universe": int(in_universe_dead),
            "per_year_in_universe": float(in_universe_dead / years), "years": years}
    pd.Series(meta).to_csv(out / "e4_meta.csv")

    e0 = C.ROOT / "results" / "e0_bias_by_deletion_pp.csv"
    fig, ax = C.plt.subplots(figsize=(8.5, 4))
    colors = {"equal_weight": C.COLORS["reversal"], "momentum": C.COLORS["momentum"], "low_vol": C.COLORS["low_vol"]}
    vend = df[df.view == "vendor"].groupby(["rule", "fraction"])["bias_pp"].mean().unstack(0)
    for rule in RULES:
        ax.plot(vend.index * 100, vend[rule], marker="o", color=colors[rule], label=f"{rule.replace('_', ' ')} ({a.data})")
    if e0.exists():
        e = pd.read_csv(e0, header=[0, 1], index_col=[0, 1])
        for rule in RULES:
            s = e.xs(rule, level=0)[("cagr_bias", "mean")]
            ax.plot(s.index * 100, s.values, ls="--", lw=1, color=colors[rule], alpha=0.7)
        ax.plot([], [], ls="--", color="#8a8983", label="E0 synthetic prediction")
    ax.axhline(0, color="#52514e", lw=0.8)
    ax.set_xlabel("share of distressed exits deleted from the data (%)")
    ax.set_ylabel("backtest CAGR minus point-in-time CAGR (pp/yr)")
    ax.set_title("Deleting dead companies inflates Indian backtests - and by rule", loc="left")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(C.fig_path(a.data, "e4_bias_vs_deletion"), dpi=160)

    # H6 verdict (pre-registered): bias at 100% deletion, EW > momentum and EW > low-vol
    full = summ[(summ.view == "vendor") & (summ.fraction == 1.0)].set_index("rule")["mean"]
    h6 = {"id": "H6", "claim": "Deleting dead companies inflates EW more than momentum and low-volatility (E0 prediction)",
          "evidence": f"bias at 100% deletion: EW {full['equal_weight']:+.2f}, momentum {full['momentum']:+.2f}, "
                      f"low-vol {full['low_vol']:+.2f} pp/yr",
          "supported": bool(full["equal_weight"] > full["momentum"] and full["equal_weight"] > full["low_vol"])}
    hp = out / "e3_hypotheses.csv"
    if hp.exists():
        h = pd.read_csv(hp)
        h = pd.concat([h[h["id"] != "H6"], pd.DataFrame([h6])], ignore_index=True)
        h.to_csv(hp, index=False)
    pd.set_option("display.width", 200)
    print(summ.round(2).to_string(index=False))
    print(h6)
    print(meta)


if __name__ == "__main__":
    main()
