"""Universe, signals and portfolio formation on the monthly point-in-time panel.

Timing convention (no look-ahead): everything known at the close of month t
(signal month) selects the portfolio that is bought at the first open of t+1
and held until the first open of t+2. Its return is `ret_hold` of month t+1.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class UniverseRule:
    size: int = 500                 # top-N by liquidity: a point-in-time proxy for a broad index
    min_price: float = 20.0         # rupees, unadjusted close at signal date
    liquidity_months: int = 6       # window for median daily traded value
    min_history_months: int = 13    # enough history for a 12-1 momentum signal
    min_days_last_month: int = 10   # traded on at least this many sessions in the signal month


def wide(monthly: pd.DataFrame, col: str) -> pd.DataFrame:
    return monthly.pivot(index="month", columns="sec", values=col).sort_index()


@dataclass
class Panel:
    """Wide month x security matrices built once from the long monthly table."""
    ret_cc: pd.DataFrame
    ret_hold: pd.DataFrame
    close: pd.DataFrame
    px: pd.DataFrame
    px_max: pd.DataFrame
    value_med: pd.DataFrame
    n_days: pd.DataFrame
    sum_r: pd.DataFrame
    sum_r2: pd.DataFrame
    ar_s2: pd.DataFrame
    active_end: pd.DataFrame
    is_fund: pd.Series
    exits: pd.DataFrame

    @classmethod
    def from_monthly(cls, monthly: pd.DataFrame, secs: pd.DataFrame, total_return: bool = False) -> "Panel":
        """total_return=True holds positions on dividend-inclusive returns (records exist from
        Jul 2010 only, so this is a sensitivity, not the primary specification)."""
        if total_return and "ret_hold_tr" in monthly:
            monthly = monthly.assign(ret_hold=monthly["ret_hold_tr"])
        months = pd.period_range(monthly["month"].min(), monthly["month"].max(), freq="M")
        w = {c: wide(monthly, c).reindex(months) for c in
             ("ret_cc", "ret_hold", "close", "px", "px_max", "value_med", "n_days", "sum_r", "sum_r2", "ar_s2",
              "active_end")}
        w["active_end"] = w["active_end"].astype("boolean").fillna(False).astype(bool)
        return cls(**w, is_fund=secs["is_fund"].reindex(w["close"].columns).fillna(False).astype(bool),
                   exits=secs.reindex(w["close"].columns))

    # ---- derived quantities -------------------------------------------------
    def liquidity(self, months: int) -> pd.DataFrame:
        return self.value_med.rolling(months, min_periods=max(2, months // 2)).median()

    def daily_vol(self, months: int = 12) -> pd.DataFrame:
        n = self.n_days.fillna(0).rolling(months, min_periods=months // 2).sum()
        s = self.sum_r.fillna(0).rolling(months, min_periods=months // 2).sum()
        s2 = self.sum_r2.fillna(0).rolling(months, min_periods=months // 2).sum()
        var = (s2 - s ** 2 / n) / (n - 1)
        return np.sqrt(var.where(n > 40))

    def half_spread(self, months: int = 3) -> pd.DataFrame:
        return np.sqrt(self.ar_s2.rolling(months, min_periods=1).mean()) / 2

    def history_months(self) -> pd.DataFrame:
        return self.close.notna().cumsum()


def universe(p: Panel, rule: UniverseRule = UniverseRule()) -> pd.DataFrame:
    """Boolean month x security: eligible at the close of each signal month."""
    liq = p.liquidity(rule.liquidity_months)
    ok = (p.close >= rule.min_price) & (p.n_days >= rule.min_days_last_month) & p.active_end
    ok &= p.history_months() >= rule.min_history_months
    ok &= ~p.is_fund.reindex(p.close.columns).values
    liq = liq.where(ok)
    rank = liq.rank(axis=1, ascending=False, method="first")
    return rank <= rule.size


# ---- signals: all use information up to the close of month t -------------------
def momentum(p: Panel, lookback: int = 12, skip: int = 1) -> pd.DataFrame:
    return p.px.shift(skip) / p.px.shift(lookback) - 1


def reversal(p: Panel) -> pd.DataFrame:
    return -p.ret_cc                                     # buy last month's losers


def low_vol(p: Panel, months: int = 12) -> pd.DataFrame:
    return -p.daily_vol(months)                          # higher score = lower volatility


def high_52w(p: Panel) -> pd.DataFrame:
    return p.px / p.px_max.rolling(12, min_periods=12).max()


SIGNALS = {"momentum": momentum, "reversal": reversal, "low_vol": low_vol, "high_52w": high_52w}


def quantile_portfolios(score: pd.DataFrame, elig: pd.DataFrame, n_q: int = 10) -> pd.DataFrame:
    """Quantile label (1 = lowest score, n_q = highest) per month x security, NaN if not held."""
    s = score.where(elig)
    pct = s.rank(axis=1, pct=True, method="first")
    return np.ceil(pct * n_q).clip(1, n_q)


def target_weights(members: pd.DataFrame) -> pd.DataFrame:
    """Equal weights within a boolean membership matrix (rows sum to 1 where any member)."""
    m = members.fillna(False).astype(float)
    return m.div(m.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)


def holding_returns(p: Panel, terminal: float = 0.0, distressed_only: bool = True) -> pd.DataFrame:
    """ret_hold aligned to the SIGNAL month (row t = return earned from t+1 open to t+2 open).

    A security whose trading stops during the holding month delivers its return up to its last
    close, compounded with `terminal` (e.g. -0.3 or -1.0 in sensitivity runs) if it exited for
    good (and, by default, only if the exit looks distressed).
    """
    r = p.ret_hold.shift(-1)
    if terminal:
        ex = p.exits
        mask = ex["exited"] & (ex["distressed_exit"] if distressed_only else True)
        last_m = pd.PeriodIndex(pd.to_datetime(ex.loc[mask, "last_date"]), freq="M")
        for sec, m in zip(ex.index[mask], last_m):
            t = m - 1                                     # signal month whose holding contains the exit
            if t in r.index and pd.notna(r.at[t, sec]):
                r.at[t, sec] = (1 + r.at[t, sec]) * (1 + terminal) - 1
    return r
