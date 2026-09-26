"""The pre-registered study: variants, gross replication, reality layers, bias study.

Every portfolio this module computes is appended to results/variants_log.csv, so the
number of things tried is on the record (it feeds the Reality Check and deflated Sharpe).
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import engine, integrity, research as rs, stats

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Variant:
    signal: str          # momentum | reversal | low_vol | high_52w
    param: int           # lookback (momentum) / window (low_vol); 0 otherwise
    top: float           # fraction held: 0.1 decile, 0.2 quintile

    @property
    def name(self) -> str:
        p = f"{self.param}" if self.param else ""
        return f"{self.signal}{p}_top{int(self.top * 100)}"


# Pre-registered grid (docs/PREREGISTRATION.md). Primary specifications are marked.
GRID = [Variant(s, p, t) for s, p in [("momentum", 12), ("momentum", 6), ("reversal", 0),
                                      ("low_vol", 12), ("low_vol", 6), ("high_52w", 0)]
        for t in (0.1, 0.2)]
PRIMARY = {"H1": Variant("momentum", 12, 0.1), "H4": Variant("low_vol", 12, 0.1), "H5": Variant("reversal", 0, 0.1)}
# Primary sample: every signal window lies inside the period covered by NSE's corporate-action
# records (Jul 2010 onward). The extended sample (from 2007) relies on the gap detector before
# Jul 2010 and is a robustness check only.
PRIMARY_FIRST_HOLD = pd.Period("2011-08", "M")
TRAIN_END = pd.Period("2017-12", "M")
SUBPERIODS = {
    "2008 crisis": ("2008-01", "2009-03"), "2013 taper tantrum": ("2013-05", "2013-12"),
    "2016 demonetisation": ("2016-11", "2017-03"), "2020 Covid": ("2020-02", "2020-12"),
    "2021-24 retail boom": ("2021-01", "2024-09"),
}
CAPITALS = {"Rs 10 lakh": 1e6, "Rs 1 crore": 1e7, "Rs 10 crore": 1e8, "Rs 100 crore": 1e9}


def score(p: rs.Panel, v: Variant) -> pd.DataFrame:
    if v.signal == "momentum":
        return rs.momentum(p, v.param, 1)
    if v.signal == "low_vol":
        return rs.low_vol(p, v.param)
    return rs.SIGNALS[v.signal](p)


class Study:
    def __init__(self, panel: rs.Panel, rule: rs.UniverseRule = rs.UniverseRule(), label: str = "real",
                 log_path: Path | None = None, terminal: float = 0.0, benchmark_sec: str | None = None,
                 first_hold: pd.Period | None = PRIMARY_FIRST_HOLD):
        self.p, self.rule, self.label = panel, rule, label
        self.elig = rs.universe(panel, rule)
        first = self.elig.any(axis=1)
        months = self.elig.index[first.values][:-1]               # last month has no holding return
        if first_hold is not None:
            months = months[months >= first_hold - 1]
        self.months = months
        self.hold = rs.holding_returns(panel, terminal=terminal)
        self.log_path = log_path
        self.benchmark_sec = benchmark_sec
        self._sigma = panel.daily_vol(3)
        self._hs = panel.half_spread(3)
        self._adv = panel.liquidity(3)

    # ---- bookkeeping -----------------------------------------------------
    def _log(self, kind: str, name: str, extra: dict | None = None) -> None:
        if self.log_path is None:
            return
        row = {"time": datetime.now().isoformat(timespec="seconds"), "sample": self.label, "kind": kind,
               "variant": name, "universe": json.dumps(asdict(self.rule)), "extra": json.dumps(extra or {})}
        row["hash"] = hashlib.sha1(json.dumps(row, sort_keys=True).encode()).hexdigest()[:10]
        new = not self.log_path.exists()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)

    # ---- portfolios --------------------------------------------------------
    def members(self, v: Variant) -> pd.DataFrame:
        q = rs.quantile_portfolios(score(self.p, v), self.elig, n_q=round(1 / v.top))
        return q.eq(round(1 / v.top))

    def gross(self, weights: pd.DataFrame) -> pd.Series:
        w = weights.loc[self.months]
        r = self.hold.loc[self.months].reindex(columns=w.columns)
        out = (w * r.fillna(0.0)).sum(axis=1)
        out.index = out.index + 1                                 # label by holding month
        return out

    def ew(self) -> pd.Series:
        self._log("gross", "same_universe_ew")
        return self.gross(rs.target_weights(self.elig))

    def long_only_gross(self, v: Variant) -> pd.Series:
        self._log("gross", v.name)
        return self.gross(rs.target_weights(self.members(v)))

    def long_short_gross(self, v: Variant) -> pd.Series:
        n = round(1 / v.top)
        q = rs.quantile_portfolios(score(self.p, v), self.elig, n_q=n)
        self._log("gross_long_short", v.name)
        return self.gross(rs.target_weights(q.eq(n))) - self.gross(rs.target_weights(q.eq(1)))

    def decile_returns(self, v: Variant) -> pd.DataFrame:
        q = rs.quantile_portfolios(score(self.p, v), self.elig, n_q=10)
        return pd.DataFrame({d: self.gross(rs.target_weights(q.eq(d))) for d in range(1, 11)})

    def benchmark(self) -> pd.Series | None:
        if self.benchmark_sec is None or self.benchmark_sec not in self.p.ret_hold.columns:
            return None
        r = self.hold[self.benchmark_sec].loc[self.months]
        r.index = r.index + 1
        return r

    def run(self, weights: pd.DataFrame, cfg: engine.RunConfig, name: str) -> tuple[pd.DataFrame, dict]:
        self._log("net", name, asdict(cfg))
        w = weights.loc[self.months]
        return engine.simulate(w, self.hold.loc[self.months], self._hs.loc[self.months],
                               self._sigma.loc[self.months], self._adv.loc[self.months], cfg)


def split(r: pd.Series) -> dict[str, pd.Series]:
    return {"full": r, "train": r[r.index <= TRAIN_END], "test": r[r.index > TRAIN_END]}


def perf_row(r: pd.Series) -> dict:
    p = integrity.perf(r)
    mu, t = stats.newey_west_t(r)
    p.update({"mean_pm": mu, "nw_t": t})
    return p


def cagr_after_tax(summary: dict, capital: float, n_months: int) -> float:
    return (summary["final_wealth_after_tax"] / capital) ** (12 / n_months) - 1
