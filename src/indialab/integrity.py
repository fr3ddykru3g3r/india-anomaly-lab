"""Backtest-integrity tools: metrics, selection-vs-timing decomposition, credibility grade.

The grade is an internal triage label derived from explicit pass/fail tests. It is
not a performance score and should never be presented as one; publish the tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def perf(r: pd.Series, periods: int = 12) -> dict:
    r = r.dropna()
    if len(r) < periods:
        return {"n": len(r)}
    w = (1 + r).cumprod()
    vol = r.std() * np.sqrt(periods)
    cagr = w.iloc[-1] ** (periods / len(r)) - 1
    dd = (w / w.cummax() - 1).min()
    return {"n": len(r), "cagr": cagr, "vol": vol, "sharpe": r.mean() * periods / vol if vol else np.nan,
            "max_dd": dd, "calmar": cagr / abs(dd) if dd < 0 else np.nan}


def selection_vs_timing(strategy: pd.Series, same_universe_ew: pd.Series,
                        investable_benchmark: pd.Series | None = None) -> pd.DataFrame:
    """Split a result into what the UNIVERSE earned and what the RULE added.

    return contribution = strategy - same-universe equal weight   (the rule)
    universe premium    = same-universe EW - investable benchmark (the list)
    risk contribution   = |maxDD(universe)| - |maxDD(strategy)|
    """
    idx = strategy.dropna().index.intersection(same_universe_ew.dropna().index)
    if investable_benchmark is not None:
        idx = idx.intersection(investable_benchmark.dropna().index)
    rows = {"strategy": perf(strategy.loc[idx]), "same_universe_ew": perf(same_universe_ew.loc[idx])}
    if investable_benchmark is not None:
        rows["investable_benchmark"] = perf(investable_benchmark.loc[idx])
    t = pd.DataFrame(rows).T
    s, u = t.loc["strategy"], t.loc["same_universe_ew"]
    t.attrs["return_contribution"] = s["cagr"] - u["cagr"]
    t.attrs["sharpe_contribution"] = s["sharpe"] - u["sharpe"]
    t.attrs["risk_contribution"] = abs(u["max_dd"]) - abs(s["max_dd"])
    if investable_benchmark is not None:
        t.attrs["universe_premium"] = u["cagr"] - t.loc["investable_benchmark"]["cagr"]
    return t


CHECKS = [
    ("point_in_time_universe", "Universe membership known at each date (no current lists applied backwards)"),
    ("dead_firms_retained", "Delisted / bankrupt / merged firms keep their histories and terminal returns"),
    ("coverage_ge_95", "Price coverage of intended holdings >= 95% in every period"),
    ("same_universe_control", "Strategy compared with equal weight of the same universe"),
    ("investable_benchmark", "Compared with something an investor could actually have bought"),
    ("realistic_costs", "Dated statutory costs + spread/impact at a stated capital"),
    ("out_of_sample", "Rules and parameters fixed before an untouched test period"),
    ("parameter_stability", "Neighbouring parameters give similar results"),
    ("multiple_testing", "Number of variants tried is reported and corrected for (WRC/SPA or deflated Sharpe)"),
    ("no_restated_data", "No later-revised or later-published data used at a date it was unknown"),
]


@dataclass
class Credibility:
    results: dict = field(default_factory=dict)

    def grade(self) -> str:
        """A: evidence. B: useful with caveats. C: exploratory. D: do not cite.

        Integrity failures (universe, dead firms, look-ahead) cap the grade no
        matter how many statistical tests pass - significance cannot rescue a
        biased universe.
        """
        r = {k: bool(self.results.get(k, False)) for k, _ in CHECKS}
        fatal = not (r["point_in_time_universe"] and r["no_restated_data"])
        if fatal:
            return "D"
        core = ["dead_firms_retained", "same_universe_control", "realistic_costs", "out_of_sample"]
        if not all(r[k] for k in core):
            return "C"
        return "A" if all(r.values()) else "B"

    def table(self) -> pd.DataFrame:
        return pd.DataFrame([{"check": k, "description": d, "pass": bool(self.results.get(k, False))}
                             for k, d in CHECKS])
