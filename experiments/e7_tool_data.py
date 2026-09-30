"""E7 - Precompute scenario returns for the educational backtesting tool (docs/site/tool.html).

Every series is a monthly return path already computed by the study code; the page only
compounds them. Scenarios: strategy x data view (honest / survivors only / today's list
applied backwards) x frictions (gross / costs / costs + tax) x capital.

    python experiments/e7_tool_data.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import _common as C
from indialab import engine, research as rs, study, teamrules as T

TEAM = {"team_s2": T.SPECS[2], "team_s4": T.SPECS[4], "team_s5": T.SPECS[5], "team_s7": T.SPECS[7]}
RULES = {"ew": None, "momentum": study.Variant("momentum", 12, 0.1), "low_vol": study.Variant("low_vol", 12, 0.1),
         "reversal": study.Variant("reversal", 0, 0.1), "high_52w": study.Variant("high_52w", 0, 0.1)}
RULES.update({k: None for k in TEAM})
CAPS = {"10L": 1e6, "1Cr": 1e7, "10Cr": 1e8, "100Cr": 1e9}


FEAT = {}


def weights(st, v, key=None):
    if key in TEAM:
        if id(st) not in FEAT:
            FEAT[id(st)] = T.load_features(C.ROOT / "data" / "processed", st.p)
        return T.weights(TEAM[key], st.p, st.elig, FEAT[id(st)])
    return rs.target_weights(st.elig) if v is None else rs.target_weights(st.members(v))


def gross(st, w, key):
    return T.gross_with_cash(st, w) if key in TEAM else st.gross(w)


def main():
    monthly, secs, bench, out = C.load("real")
    honest = C.make_study(monthly, secs, bench, "real", log=False)
    months = [str(m + 1) for m in honest.months]
    idx = pd.PeriodIndex(months, freq="M")
    data = {"months": months, "series": {}}

    def put(key, r):
        data["series"][key] = [None if pd.isna(x) else round(float(x), 6) for x in r.reindex(idx).values]

    put("bench|gross", honest.benchmark())
    for name, v in RULES.items():
        w = weights(honest, v, name)
        cy = T.CASH_YIELD if name in TEAM else 0.0
        put(f"{name}|honest|gross", gross(honest, w, name))
        for cap_name, cap in CAPS.items():
            res, _ = honest.run(w, engine.RunConfig(capital=cap, tax=False, cash_yield=cy), name)
            put(f"{name}|honest|costs|{cap_name}", res["net"])
        res, _ = honest.run(w, engine.RunConfig(cash_yield=cy), name)
        put(f"{name}|honest|tax|10L", res["net"])
        print(name, "honest done", flush=True)

    alive = secs.index[~secs["exited"].astype(bool)]
    surv = C.make_study(monthly[monthly["sec"].isin(alive)], secs.loc[secs.index.isin(alive)], bench, "real", log=False)
    last = honest.elig.index[honest.elig.any(axis=1).values][-1]
    today = honest.elig.columns[honest.elig.loc[last].values]
    tod = C.make_study(monthly[monthly["sec"].isin(today)], secs.loc[secs.index.isin(today)], bench, "real", log=False)
    tod.elig = rs.universe(tod.p, rs.UniverseRule(size=100_000))
    for view, st in [("survivors", surv), ("today", tod)]:
        for name, v in RULES.items():
            put(f"{name}|{view}|gross", gross(st, weights(st, v, name), name))
    (C.ROOT / "docs" / "site" / "tool_data.json").write_text(json.dumps(data, separators=(",", ":")))
    print(len(data["series"]), "series")


if __name__ == "__main__":
    main()
