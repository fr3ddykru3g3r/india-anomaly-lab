"""E6 (EXPLORATORY, not pre-registered, low power) - Value in India, 2024 onward.

The original question included value. NSE's PR archives carry a daily P/E file only from
early 2024, so this is a short-sample look, not a test: ~31 holding months. Signal: earnings
yield (1 / NSE "SYMBOL P/E") known at the month-end; loss-makers (no P/E) are excluded.
A full value test needs point-in-time fundamentals (e.g. a licensed database), which is
future work.

    python experiments/e6_value_short_sample.py
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

import _common as C
from indialab import engine, research as rs, stats, study


def load_pe(raw: Path) -> pd.DataFrame:
    rows = []
    for f in sorted((raw / "pr").rglob("*.zip")):
        with zipfile.ZipFile(f) as z:
            names = [n for n in z.namelist() if Path(n).name.lower().startswith("pe_")]
            if not names:
                continue
            df = pd.read_csv(io.BytesIO(z.read(names[0])), dtype=str)
        df.columns = [c.strip().upper() for c in df.columns]
        d = pd.to_datetime(f.stem[2:], format="%d%m%y")
        df = df.rename(columns={"SYMBOL P/E": "pe"})[["SYMBOL", "pe"]]
        df["pe"] = pd.to_numeric(df["pe"], errors="coerce")
        df["date"] = d
        rows.append(df)
    pe = pd.concat(rows, ignore_index=True)
    pe["SYMBOL"] = pe["SYMBOL"].str.strip()
    pe["month"] = pe["date"].dt.to_period("M")
    return pe.sort_values("date").groupby(["month", "SYMBOL"]).last().reset_index()   # last file in month


def main():
    monthly, secs, bench, out = C.load("real")
    pe = load_pe(C.ROOT / "data" / "raw")
    st = C.make_study(monthly, secs, bench, "real", log=False)
    # map symbol -> security at each month end
    m = monthly[["sec", "month", "symbol"]].copy()
    m["month"] = pd.PeriodIndex(m["month"], freq="M")
    ey = pe.merge(m, left_on=["month", "SYMBOL"], right_on=["month", "symbol"], how="inner")
    ey = ey[ey["pe"] > 0]
    ey["ey"] = 1 / ey["pe"]
    score = ey.pivot_table(index="month", columns="sec", values="ey").reindex(index=st.elig.index,
                                                                              columns=st.elig.columns)
    first = score.dropna(how="all").index.min()
    st.months = st.months[st.months >= first]
    elig = st.elig & score.notna()
    q = rs.quantile_portfolios(score, elig, 10)
    top = rs.target_weights(q.eq(10))
    ew = rs.target_weights(elig)
    g_top, g_ew = st.gross(top), st.gross(ew)
    net_top, _ = st.run(top, engine.RunConfig(tax=False), "value_ey_top10")
    net_ew, _ = st.run(ew, engine.RunConfig(tax=False), "value_universe_ew")
    ci = stats.bootstrap_cagr_diff(g_top, g_ew.loc[g_top.index], mean_block=3)
    cagr = lambda r: float((1 + r).prod() ** (12 / len(r)) - 1)
    res = {"first_holding_month": str(g_top.index.min()), "months": len(g_top),
           "value_top_decile_gross_cagr": cagr(g_top), "ew_gross_cagr": cagr(g_ew),
           "value_top_decile_net_cagr": cagr(net_top["net"]), "ew_net_cagr": cagr(net_ew["net"]),
           "gross_diff_ci95": [ci["lo"], ci["hi"]], "avg_names": float(q.eq(10).loc[st.months].sum(axis=1).mean()),
           "note": "exploratory; ~31 months; not a test of the value premium"}
    pd.Series(res).to_json(out / "e6_value_short_sample.json", indent=2)
    print(pd.Series(res).to_string())


if __name__ == "__main__":
    main()
