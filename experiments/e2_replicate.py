"""E2 - Gross replication of the pre-registered anomalies (before any frictions).

    python experiments/e2_replicate.py --data real        # needs tag prereg-v1
    python experiments/e2_replicate.py --data synthetic   # dry run on a known market
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
from indialab import integrity, study


def main():
    a = C.args(__doc__)
    monthly, secs, bench, out = C.load(a.data, a.seed)
    st = C.make_study(monthly, secs, bench, a.data)
    ew = st.ew()
    b = st.benchmark()
    rows, series = [], {"same-universe EW": ew}
    if b is not None:
        series["Nifty 50 ETF (NIFTYBEES)"] = b
    for name, r in series.items():
        for part, x in study.split(r).items():
            rows.append({"portfolio": name, "kind": "benchmark", "period": part, **study.perf_row(x)})
    long_only = {}
    for v in study.GRID:
        lo = st.long_only_gross(v)
        ls = st.long_short_gross(v)
        long_only[v.name] = lo
        for part, x in study.split(lo).items():
            rows.append({"portfolio": v.name, "kind": "long-only top", "period": part, **study.perf_row(x)})
        for part, x in study.split(ls).items():
            rows.append({"portfolio": v.name, "kind": "long-short top-bottom", "period": part, **study.perf_row(x)})
    table = pd.DataFrame(rows)
    table.to_csv(out / "e2_gross_summary.csv", index=False)
    pd.DataFrame(long_only).to_csv(out / "e2_long_only_gross_returns.csv")
    ew.rename("ew").to_csv(out / "e2_ew_gross_returns.csv")
    if b is not None:
        b.rename("bench").to_csv(out / "e2_benchmark_returns.csv")

    # universe-size check (coverage of the point-in-time universe over time)
    n = st.elig.sum(axis=1).loc[st.months]
    n.rename("universe_size").to_csv(out / "e2_universe_size.csv")

    # decile profiles for the primary signals
    fig, axes = plt_subplots(1, 3)
    for ax, (h, v) in zip(axes, study.PRIMARY.items()):
        dec = st.decile_returns(study.Variant(v.signal, v.param, 0.1))
        dec.to_csv(out / f"e2_deciles_{v.signal}{v.param or ''}.csv")
        ann = (1 + dec).prod() ** (12 / len(dec)) - 1
        ax.bar(ann.index, ann.values * 100, color=C.COLORS[v.signal])
        ax.axhline((1 + ew.loc[dec.index]).prod() ** (12 / len(dec)) * 100 - 100, color=C.COLORS["ew"], lw=1,
                   ls="--", label="same-universe EW")
        ax.set_title(f"{h}: {v.signal.replace('_', ' ')} deciles", loc="left")
        ax.set_xlabel("decile (10 = highest score)")
        ax.set_xticks(range(1, 11))
    axes[0].set_ylabel("gross CAGR (%)")
    axes[0].legend(frameon=False)
    fig.tight_layout()
    fig.savefig(C.fig_path(a.data, "e2_decile_profiles"), dpi=160)

    fig, ax = plt_subplots(1, 1, figsize=(9, 4.2))
    ax = ax[0]
    for name, r in series.items():
        ax.plot(r.index.to_timestamp(), (1 + r).cumprod(), color=C.COLORS["ew" if "EW" in name else "bench"],
                lw=1.6, label=name)
    for v in study.PRIMARY.values():
        r = long_only[v.name]
        ax.plot(r.index.to_timestamp(), (1 + r).cumprod(), color=C.COLORS[v.signal], lw=1.2, label=v.name)
    ax.set_yscale("log")
    ax.axvline(pd.Timestamp("2016-01-01"), color="#b8b7b0", lw=1)
    ax.text(pd.Timestamp("2016-03-01"), ax.get_ylim()[0] * 1.1, "test period ->", fontsize=8, color="#8a8983")
    ax.set_title("Gross growth of Rs 1 (log scale), before costs and taxes", loc="left")
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(C.fig_path(a.data, "e2_cumulative_gross"), dpi=160)

    pd.set_option("display.width", 200)
    show = table[table.period.isin(["full", "test"])].pivot_table(index=["portfolio", "kind"], columns="period",
                                                                     values=["cagr", "sharpe", "nw_t"])
    print(show.round(3).to_string())


def plt_subplots(r, c, figsize=(12, 3.6)):
    fig, axes = C.plt.subplots(r, c, figsize=figsize)
    return fig, np.atleast_1d(axes)


if __name__ == "__main__":
    main()
