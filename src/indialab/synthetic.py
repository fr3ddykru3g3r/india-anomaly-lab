"""Synthetic equity market with known ground truth, for measuring backtest bias.

Every firm's full history - including bankruptcies and takeovers - is generated,
so the TRUE performance of any strategy is known. We then build the views a
researcher usually gets:

    truth     every firm, every month it existed, terminal returns included
    vendor    histories of dead firms deleted with some probability (the way free
              data sources silently drop delisted names)
    survivors only firms alive at the end, back-tested over their whole life
              (applying today's constituent list to the past)

and measure bias = backtest on a view - backtest on the truth.

Model (monthly):
    r_it = mu_i + beta_i * m_t + e_it,  m_t Student-t market shock, e_it firm shock
    bankruptcy hazard rises with the firm's drawdown: h = h0 * exp(-a * dd), dd <= 0
    takeover hazard constant; bankrupt firms book a terminal loss, targets a premium
    every exit is replaced by an IPO next month (firm count stays constant)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MarketParams:
    n_firms: int = 800
    months: int = 240
    # Calibrated (4 seeds): ~1.8%/yr bankruptcies, ~3.1%/yr takeovers, true
    # equal-weight CAGR ~8% - the same order as broad-market history.
    mkt_mu: float = 0.0095
    mkt_vol: float = 0.045
    idio_vol_median: float = 0.07
    drift_sd: float = 0.004
    h0_bankrupt: float = 0.0001
    dd_sensitivity: float = 6.0
    bankrupt_return: float = -0.90
    h_takeover: float = 0.0025
    takeover_premium: float = 0.30
    t_df: float = 5.0


def simulate(p: MarketParams = MarketParams(), seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (returns T x firms with NaN outside each firm's life, firm table)."""
    rng = np.random.default_rng(seed)
    T = p.months
    z = rng.standard_t(p.t_df, T) / np.sqrt(p.t_df / (p.t_df - 2))
    mkt = p.mkt_mu + p.mkt_vol * z
    cols, firms = [], []
    alive = []

    def new_firm(start):
        f = {"id": len(firms), "start": start, "end": None, "exit": "alive",
             "mu": rng.normal(0, p.drift_sd), "beta": rng.uniform(0.6, 1.4),
             "ivol": p.idio_vol_median * np.exp(rng.normal(0, 0.35)), "wealth": 1.0, "peak": 1.0}
        firms.append(f)
        cols.append(np.full(T, np.nan))
        return f

    for _ in range(p.n_firms):
        alive.append(new_firm(0))
    for t in range(T):
        survivors = []
        for f in alive:
            r = f["mu"] + f["beta"] * mkt[t] + f["ivol"] * rng.standard_normal()
            dd = f["wealth"] / f["peak"] - 1
            if rng.random() < p.h0_bankrupt * np.exp(-p.dd_sensitivity * dd):
                r, f["exit"] = p.bankrupt_return, "bankrupt"
            elif rng.random() < p.h_takeover:
                r, f["exit"] = r + p.takeover_premium, "takeover"
            r = max(r, -0.99)
            cols[f["id"]][t] = r
            f["wealth"] *= 1 + r
            f["peak"] = max(f["peak"], f["wealth"])
            if f["exit"] == "alive":
                survivors.append(f)
            else:
                f["end"] = t
        for _ in range(len(alive) - len(survivors)):
            if t + 1 < T:
                survivors.append(new_firm(t + 1))
        alive = survivors
    R = pd.DataFrame(np.column_stack(cols), index=pd.RangeIndex(T, name="month"))
    info = pd.DataFrame([{k: f[k] for k in ("id", "start", "end", "exit")} for f in firms]).set_index("id")
    return R, info


def view(R: pd.DataFrame, info: pd.DataFrame, kind: str, delete_bankrupt: float = 0.0,
         delete_takeover: float = 0.0, seed: int = 0) -> pd.DataFrame:
    if kind == "truth":
        return R
    if kind == "survivors":
        return R.loc[:, info.index[info["exit"] == "alive"]]
    if kind == "vendor":
        rng = np.random.default_rng(seed + 10_000)
        u = rng.random(len(info))
        drop = ((info["exit"] == "bankrupt") & (u < delete_bankrupt)) | \
               ((info["exit"] == "takeover") & (u < delete_takeover))
        return R.loc[:, info.index[~drop.values]]
    raise ValueError(kind)


def strategy_returns(R: pd.DataFrame, rule: str, frac: float = 0.1) -> pd.Series:
    """Monthly returns of a rule applied to whatever firms exist in the view.

    Signal uses data through month t; the portfolio earns month t+1. Firms that
    exit in t+1 earn their terminal return (if their history exists in the view).
    """
    logw = np.log1p(R).cumsum()
    mom = (np.log1p(R).shift(1).rolling(11, min_periods=11).sum())        # months t-11..t-1
    vol = R.rolling(12, min_periods=12).std()
    trend = logw > logw.rolling(10, min_periods=10).mean()
    out = []
    for t in range(12, len(R) - 1):
        avail = R.iloc[t].notna() & R.iloc[t + 1].notna()
        if rule == "equal_weight":
            pick = avail
        elif rule == "momentum":
            s = mom.iloc[t][avail].dropna()
            pick = s.index[s >= s.quantile(1 - frac)]
        elif rule == "low_vol":
            s = vol.iloc[t][avail].dropna()
            pick = s.index[s <= s.quantile(frac)]
        elif rule == "trend":
            pick = trend.iloc[t][avail]
            pick = pick.index[pick]
        else:
            raise ValueError(rule)
        cols = pick.index[pick] if isinstance(pick, pd.Series) else pick
        out.append(R.iloc[t + 1][cols].mean() if len(cols) else 0.0)
    return pd.Series(out, index=R.index[13:len(R)])


def cagr(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12 / len(r)) - 1)


def max_dd(r: pd.Series) -> float:
    w = (1 + r).cumprod()
    return float((w / w.cummax() - 1).min())


def to_monthly_panel(R: pd.DataFrame, info: pd.DataFrame, start: str = "2006-01", seed: int = 0,
                     vendor_drop: pd.Index | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Express a synthetic market in the same long monthly schema the NSE pipeline produces,
    so the real research code can be validated where the truth is known."""
    rng = np.random.default_rng(seed + 99)
    months = pd.period_range(start, periods=len(R), freq="M")
    R = R.copy()
    R.index = months
    if vendor_drop is not None:
        R = R.drop(columns=vendor_drop)
    px = np.exp(np.log1p(R).cumsum()).where(R.notna())
    liq = pd.Series(np.exp(rng.normal(np.log(5e7), 1.2, R.shape[1])), index=R.columns)
    vol_d = R.rolling(12, min_periods=3).std().bfill() / np.sqrt(21)
    long = pd.DataFrame({
        "ret_cc": R.stack(), "ret_hold": R.stack(), "px": px.stack(),
    })
    long["close"] = 100 * long["px"]
    long["px_max"] = long["px"]
    long["value_med"] = liq.reindex(long.index.get_level_values(1)).to_numpy() * np.exp(rng.normal(0, 0.2, len(long)))
    long["n_days"] = 21
    long["sum_r"] = long["ret_cc"]
    long["sum_r2"] = 21 * vol_d.stack().reindex(long.index).to_numpy() ** 2 + long["ret_cc"] ** 2 / 21
    long["ar_s2"] = (2 * 0.002) ** 2
    long.index = long.index.set_names(["month", "sec"])
    monthly = long.reset_index()
    exit_month = info["end"].reindex(monthly["sec"]).to_numpy()
    monthly["active_end"] = ~(months.get_indexer(monthly["month"]) == exit_month)
    monthly["sec"] = "F" + monthly["sec"].astype(str)
    alive_end = info["exit"] == "alive"
    secs = pd.DataFrame({
        "exited": (~alive_end).to_numpy(), "distressed_exit": (info["exit"] == "bankrupt").to_numpy(),
        "last_date": [months[int(e)].end_time.normalize() if pd.notna(e) else months[-1].end_time.normalize()
                      for e in info["end"]],
        "is_fund": False, "last_symbol": "F" + info.index.astype(str),
    }, index="F" + info.index.astype(str))
    return monthly, secs.loc[secs.index.isin(monthly["sec"].unique())]
