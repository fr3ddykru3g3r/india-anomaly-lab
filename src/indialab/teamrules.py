"""The Stockers team rule family (S0-S8), ported to the Indian panel.

Origin: `screener/rules.py` and `screener/backtest.py` of the Stockers workspace (S&P 500 version).
The rule logic below is the same; only the data changes (NSE point-in-time universe, adjusted prices).

Rule S5, evaluated at each completed month end t:
  ELIGIBLE  in the point-in-time universe, month-end price above its 200-day EMA
  HOLD      a held stock stays held while eligible
  EXIT      a held stock that closes the month below EMA200 (or leaves the universe)
  SETUP     red month (close < first open) with positive volume delta, while eligible
  ENTER     setups fill free slots (30 names), strongest 12-1 momentum first; bought next open
  cash      unused slots earn a cash yield

S6 skips entries more than 35% above EMA200; S7 ranks by momentum / 63-day volatility;
S8 = S6 + S7. S1-S4 are the ladder below S5 (trend only, momentum, momentum + trend,
momentum + low volatility + trend). S0 is equal weight of the universe.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import research as rs

N_HOLD = 30
CHASE_CAP = 1.35
CASH_YIELD = 0.06     # approximate average Indian 91-day T-bill yield 2011-26; sensitivity at 0


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    rule: str                  # all | trend | mom | mom_trend | momlv_trend | setup_trend
    cap: float | None = None
    rank: str = "mom"          # mom | mom_vol


SPECS = [
    Spec("S0", "Equal weight, whole universe", "all"),
    Spec("S1", "Trend: every stock above EMA200", "trend"),
    Spec("S2", "Momentum 12-1, top 30", "mom"),
    Spec("S3", "Momentum top 30 + EMA200 trend filter", "mom_trend"),
    Spec("S4", "Momentum + low volatility top 30 + trend filter", "momlv_trend"),
    Spec("S5", "Team setup (red month, +volume delta) above EMA200; hold while above", "setup_trend"),
    Spec("S6", "S5 + no chasing (skip entries > 35% above EMA200)", "setup_trend", cap=CHASE_CAP),
    Spec("S7", "S5 ranked by momentum / volatility", "setup_trend", rank="mom_vol"),
    Spec("S8", "S5 + no chasing + risk-adjusted ranking", "setup_trend", cap=CHASE_CAP, rank="mom_vol"),
]


def is_setup(month_ret_oc: float, voldelta: float) -> bool:
    return bool(month_ret_oc < 0 and voldelta > 0)


def s5_step(held, eligible, mom: pd.Series, month_ret_oc: pd.Series, voldelta: pd.Series, capacity: int,
            blocked: set | None = None):
    """Same logic as screener/rules.py::s5_step. Returns (new holdings)."""
    elig = set(eligible)
    kept = [x for x in held if x in elig]
    setups = [x for x in eligible if x not in kept and is_setup(month_ret_oc.get(x, 0.0), voldelta.get(x, 0.0))]
    room = capacity - len(kept)
    open_ = [x for x in setups if not blocked or x not in blocked]
    entries = list(mom.reindex(open_).dropna().nlargest(room).index) if room > 0 and open_ else []
    return kept + entries


def load_features(processed: Path, panel: rs.Panel) -> dict[str, pd.DataFrame]:
    """Month-end features from the daily panel, aligned to the study panel (month x security)."""
    d = pd.read_parquet(processed / "daily.parquet",
                        columns=["sec", "date", "open", "close", "volume", "ret", "px"])
    d = d.sort_values(["sec", "date"]).reset_index(drop=True)
    d["month"] = d["date"].dt.to_period("M")
    g = d.groupby("sec", sort=False)
    d["ema200"] = g["px"].transform(lambda s: s.ewm(span=200, adjust=False).mean())
    d["vol63"] = g["ret"].transform(lambda s: s.rolling(63, min_periods=40).std()) * np.sqrt(252)
    d["voldelta"] = np.sign(d["close"] - d["open"]) * d["volume"].fillna(0)
    d["pxo"] = d["px"] * (d["open"].where(d["open"] > 0, d["close"]) / d["close"])
    gm = d.groupby(["sec", "month"], sort=False)
    m = pd.DataFrame({"ema200": gm["ema200"].last(), "vol63": gm["vol63"].last(), "voldelta": gm["voldelta"].sum(),
                      "px_last": gm["px"].last(), "pxo_first": gm["pxo"].first()})
    m["month_ret_oc"] = m["px_last"] / m["pxo_first"] - 1
    out = {}
    for c in ("ema200", "vol63", "voldelta", "px_last", "month_ret_oc"):
        out[c] = m[c].unstack("sec").reindex(index=panel.close.index, columns=panel.close.columns)
    return out


def weights(spec: Spec, panel: rs.Panel, elig: pd.DataFrame, feat: dict, n_hold: int = N_HOLD) -> pd.DataFrame:
    """Target weights by signal month. Held/entered names get 1/n_hold; the remainder is cash
    (S0 and S1 are fully invested, equal weight)."""
    px, ema = feat["px_last"], feat["ema200"]
    trend = (px > ema) & ema.notna()
    mom = rs.momentum(panel, 12, 1)
    score = mom / feat["vol63"] if spec.rank == "mom_vol" else mom
    stretch = px / ema
    W = pd.DataFrame(0.0, index=elig.index, columns=elig.columns)
    held: list = []
    for t in elig.index:
        e = elig.loc[t]
        el = list(e.index[e.values])
        if not el:
            held = []
            continue
        if spec.rule == "all":
            pick = el
        elif spec.rule == "trend":
            pick = [x for x in el if trend.at[t, x]]
        else:
            m = mom.loc[t, el].dropna()
            if spec.rule in ("mom_trend", "momlv_trend", "setup_trend"):
                m = m[[bool(trend.at[t, x]) for x in m.index]]
            if spec.rule in ("mom", "mom_trend"):
                pick = list(m.nlargest(n_hold).index)
            elif spec.rule == "momlv_trend":
                v = feat["vol63"].loc[t, m.index]
                sc = m.rank(pct=True) + (-v).rank(pct=True)
                pick = list(sc.nlargest(n_hold).index)
            else:
                blocked = (set(stretch.loc[t, m.index][stretch.loc[t, m.index] > spec.cap].index)
                           if spec.cap else None)
                pick = s5_step(held, list(m.index), score.loc[t, m.index], feat["month_ret_oc"].loc[t],
                               feat["voldelta"].loc[t], n_hold, blocked=blocked)
        held = pick
        if pick:
            fully = spec.rule in ("all", "trend")
            W.loc[t, pick] = 1.0 / (len(pick) if fully else n_hold)
    return W


def gross_with_cash(st, w: pd.DataFrame, cash_yield: float = CASH_YIELD) -> pd.Series:
    """Paper return of the weights including the cash leg."""
    g = st.gross(w)
    cash_w = (1.0 - w.loc[st.months].sum(axis=1)).clip(lower=0)
    cash_w.index = cash_w.index + 1
    return g + cash_w * ((1 + cash_yield) ** (1 / 12) - 1)


# ---- published "value-like" price-based anomalies (no fundamentals needed) ----------------
def lt_reversal(panel: rs.Panel) -> pd.DataFrame:
    """De Bondt-Thaler / Fama-French long-term reversal: buy the worst performers of months t-60..t-13."""
    return -(panel.px.shift(12) / panel.px.shift(60) - 1)


def cheap_vs_5y_mean(panel: rs.Panel) -> pd.DataFrame:
    """Price relative to its own 5-year average: low = 'cheap' (a price-only value proxy)."""
    return -(panel.px / panel.px.rolling(60, min_periods=48).mean())
