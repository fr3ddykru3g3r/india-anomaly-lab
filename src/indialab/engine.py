"""Month-by-month portfolio simulation in rupees: drift, banded rebalancing, dated costs, taxes.

    gross   paper return of the target weights (no costs, full rebalance)
    net     after trading costs (statutory + spread + impact at the stated capital)
    net_tax after costs and annual capital-gains tax (paid each April)
    after-tax terminal wealth also liquidates everything at the end and pays that tax.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import costs as C
from .tax import GRANDFATHER_DATE, TaxBook, fy_of


@dataclass(frozen=True)
class RunConfig:
    capital: float = 1_000_000.0      # Rs 10 lakh
    k: float = 0.7                    # square-root impact coefficient
    brokerage: float = 0.0            # discount broker
    band: float = 0.25                # relative rebalance band for continuing positions
    tax: bool = True
    costs: bool = True


def trade_date(signal_month: pd.Period) -> pd.Timestamp:
    return (signal_month + 1).start_time


def simulate(weights: pd.DataFrame, hold: pd.DataFrame, half_spread: pd.DataFrame, sigma: pd.DataFrame,
             adv: pd.DataFrame, cfg: RunConfig = RunConfig()) -> tuple[pd.DataFrame, dict]:
    months = weights.index
    cols = weights.columns
    W0 = weights.to_numpy(float)
    R = hold.reindex(index=months, columns=cols).to_numpy(float)
    HS = half_spread.reindex(index=months, columns=cols).to_numpy(float)
    SG = sigma.reindex(index=months, columns=cols).to_numpy(float)
    AD = adv.reindex(index=months, columns=cols).to_numpy(float)
    h = np.zeros(len(cols))
    cash = cfg.capital
    book = TaxBook() if cfg.tax else None
    gf_marked = False
    rows = []
    for i, m in enumerate(months):
        w = W0[i]
        if w.sum() <= 0 and h.sum() <= 0:
            continue
        date = trade_date(m)
        wealth_start = h.sum() + cash
        tax_paid = 0.0
        if book is not None and date.month == 4:
            tax_paid = book.settle(fy_of(date) - 1)
            if tax_paid > 0:
                keep = max(wealth_start - tax_paid, 0.0) / wealth_start
                h *= keep
                cash *= keep
                book.scale_all(keep)        # pro-rata withdrawal: value and basis shrink together
        if book is not None and not gf_marked and date > GRANDFATHER_DATE:
            book.mark_grandfather()
            gf_marked = True
        wealth = h.sum() + cash
        target = w * wealth
        cont = (h > 0) & (target > 0)
        fixed = cont & (np.abs(target - h) <= cfg.band * target)
        if fixed.any():
            target[fixed] = h[fixed]
            free = (target > 0) & ~fixed
            room = wealth - target[fixed].sum()
            if free.any() and target[free].sum() > 0:
                target[free] *= room / target[free].sum()
        buy = np.clip(target - h, 0, None)
        sell = np.clip(h - target, 0, None)
        cost = 0.0
        if cfg.costs:
            c = C.trade_costs_vec(buy, sell, date, HS[i], SG[i], AD[i], cfg.k, cfg.brokerage)
            cost = float(np.nansum(c))
        if book is not None:
            for j in np.flatnonzero(sell > 1e-6):
                if target[j] <= 0:
                    book.sell_all(cols[j], date)
                else:
                    book.sell(cols[j], date, sell[j])
            for j in np.flatnonzero(buy > 1e-6):
                book.buy(cols[j], date, buy[j])
        invested = target.sum()
        cash = wealth - invested
        scale = (wealth - cost) / wealth if wealth > 0 else 0.0
        target *= scale
        cash *= scale
        if book is not None and scale != 1.0:
            book.scale_mv(scale)            # costs raise the effective basis (treated as deductible)
        r = np.nan_to_num(R[i])
        h = target * (1 + r)
        if book is not None:
            for j in np.flatnonzero(target > 0):
                book.grow(cols[j], 1 + r[j])
        wealth_end = h.sum() + cash
        gross = float(np.nansum(w * r)) if w.sum() > 0 else 0.0
        rows.append({"month": m + 1, "gross": gross, "net": wealth_end / wealth_start - 1,
                     "cost_inr": cost, "tax_inr": tax_paid, "turnover": float((buy + sell).sum() / 2 / wealth),
                     "names": int((w > 0).sum()), "wealth": wealth_end})
    out = pd.DataFrame(rows).set_index("month")
    summary = {"final_wealth_pre_liquidation": float(out["wealth"].iloc[-1]) if len(out) else cfg.capital}
    if book is not None and len(out):
        last_date = (out.index[-1] + 1).start_time
        for sec in list(book.lots):
            book.sell_all(sec, last_date)
        fy = fy_of(last_date)
        liq_tax = sum(book.settle(y) for y in sorted(set(book.gains) | {fy}))
        summary["liquidation_tax"] = float(liq_tax)
        summary["final_wealth_after_tax"] = summary["final_wealth_pre_liquidation"] - liq_tax
        summary["tax_paid_by_fy"] = {int(k): float(v) for k, v in book.paid.items()}
    return out, summary
