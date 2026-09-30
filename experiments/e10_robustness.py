"""E10 - Robustness of the headline (momentum) result to extreme observations and data errors.

    python experiments/e10_robustness.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
from indialab import research as rs, study


def cagr(x):
    return float((1 + x).prod() ** (12 / len(x)) - 1)


def main():
    monthly, secs, bench, out = C.load("real")
    st = C.make_study(monthly, secs, bench, "real", log=False)
    r = st.hold.loc[st.months]
    rows = []
    ew_w = rs.target_weights(st.elig).loc[st.months]
    rows.append({"test": "same-universe EW (reference)", "momentum_top10_gross_cagr": np.nan,
                 "ew_gross_cagr": cagr((ew_w * r.fillna(0)).sum(axis=1))})
    w = rs.target_weights(st.members(study.PRIMARY["H1"])).loc[st.months]
    contrib = w * r.fillna(0)
    rows.append({"test": "baseline", "momentum_top10_gross_cagr": cagr(contrib.sum(axis=1))})
    for cap in (2.0, 1.0, 0.5):
        rows.append({"test": f"cap each monthly return at +{cap:.0%}",
                     "momentum_top10_gross_cagr": cagr((w * r.clip(upper=cap).fillna(0)).sum(axis=1))})
    flat = contrib.stack()
    for n in (10, 25, 50, 100):
        k = flat.copy()
        k.loc[flat.sort_values(ascending=False).head(n).index] = 0.0
        rows.append({"test": f"drop the {n} best position-months (of {int((w > 0).sum().sum())})",
                     "momentum_top10_gross_cagr": cagr(k.unstack().sum(axis=1))})
    for lo in (-0.5, -0.9):
        rows.append({"test": f"floor each monthly return at {lo:.0%} (worst losses capped)",
                     "momentum_top10_gross_cagr": cagr((w * r.clip(lower=lo).fillna(0)).sum(axis=1))})
    d = pd.read_parquet(C.ROOT / "data" / "processed" / "daily.parquet", columns=["date", "sec", "symbol", "ret", "value"])
    spikes = d[(d["ret"] > 1.0) & (d["value"] > 1e7)]
    held_ever = set(w.columns[(w > 0).any()])
    rows.append({"test": f"daily returns > +100% in liquid stocks: {len(spikes)} ({', '.join(spikes['symbol'].astype(str))}); "
                         f"held by the momentum portfolio: {int(spikes['sec'].isin(held_ever).sum())}",
                 "momentum_top10_gross_cagr": np.nan})
    df = pd.DataFrame(rows)
    df.to_csv(out / "e10_robustness.csv", index=False)
    print(df.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
