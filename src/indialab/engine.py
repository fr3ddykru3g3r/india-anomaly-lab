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
    spread_floor: float = 0.0         # minimum half-spread (sensitivity, amendment 1)
    cash_yield: float = 0.0           # annual yield on uninvested cash (amendment 2; untaxed approximation)
    participation_limit: float = 0.10  # diagnostic only; breached trades are not assumed executable
    missing_return_policy: str = "raise"  # optional, explicitly labeled last_mark / total_loss sensitivities


def trade_date(signal_month: pd.Period) -> pd.Timestamp:
    return (signal_month + 1).start_time


def simulate(weights: pd.DataFrame, hold: pd.DataFrame, half_spread: pd.DataFrame, sigma: pd.DataFrame,
             adv: pd.DataFrame, cfg: RunConfig = RunConfig(), *,
             execution_available: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    months = weights.index
    cols = weights.columns
    W0 = weights.to_numpy(float)
    if not np.isfinite(W0).all() or (W0 < 0).any() or (W0.sum(axis=1) > 1 + 1e-9).any():
        raise ValueError("Weights must be finite, long-only, and sum to at most one")
    if cfg.capital <= 0 or not 0 < cfg.participation_limit <= 1:
        raise ValueError("Positive capital and a participation limit in (0, 1] are required")
    if cfg.missing_return_policy not in ("raise", "last_mark", "total_loss"):
        raise ValueError("Unknown missing-return policy")
    R = hold.reindex(index=months, columns=cols).to_numpy(float)
    HS = half_spread.reindex(index=months, columns=cols).to_numpy(float)
    SG = sigma.reindex(index=months, columns=cols).to_numpy(float)
    AD = adv.reindex(index=months, columns=cols).to_numpy(float)
    EX = (execution_available.reindex(index=months, columns=cols).fillna(False).to_numpy(bool)
          if execution_available is not None else np.ones(W0.shape, dtype=bool))
    h = np.zeros(len(cols))
    cash = cfg.capital
    book = TaxBook() if cfg.tax else None
    gf_marked = False
    rows = []
    for i, m in enumerate(months):
        w = W0[i].copy()
        date = trade_date(m)
        wealth_start = h.sum() + cash
        missing_open = ~EX[i] & ((w > 0) | (h > 1e-6))
        unfilled_open_weight = float(w[missing_open].sum())
        missing_open_exposure = float(h[missing_open].sum() / wealth_start) if wealth_start > 0 else 0.0
        if missing_open.any():
            if cfg.missing_return_policy == "raise":
                names = ", ".join(str(x) for x in cols[missing_open][:5])
                raise ValueError(f"Missing executable opening price in {m}: {names}; cannot assume entry or liquidation")
            w[missing_open] = 0.0
            if cfg.missing_return_policy == "total_loss":
                h[missing_open] = 0.0
                if book is not None:
                    for sec in cols[missing_open]:
                        book.assumed_writeoff(sec, date)
        # R[i] describes the coming holding interval. A position fully sold at
        # its opening mark does not need that future interval's return.
        missing = ~np.isfinite(R[i]) & (w > 0)
        missing_exposure = float(h[missing].sum() / wealth_start) if wealth_start > 0 else 0.0
        unfilled_weight = float(w[missing].sum())
        if missing.any():
            if cfg.missing_return_policy == "raise":
                names = ", ".join(str(x) for x in cols[missing][:5])
                raise ValueError(f"Missing holding return for an invested security in {m}: {names}; audit the data")
            # Missing opening/holding observations never produce a new fill.
            # Existing positions either cash out at the last mark (optimistic
            # assumption), or are written off (severe assumption), explicitly.
            w[missing] = 0.0
            if cfg.missing_return_policy == "total_loss":
                h[missing] = 0.0
                if book is not None:
                    for sec in cols[missing]:
                        book.assumed_writeoff(sec, date)
        tax_paid = 0.0
        if book is not None and date.month == 4:
            tax_paid = book.settle(fy_of(date) - 1)
            if tax_paid > 0:
                settlement_wealth = h.sum() + cash
                keep = max(settlement_wealth - tax_paid, 0.0) / settlement_wealth if settlement_wealth > 0 else 0.0
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
                # Preserve the intended cash allocation instead of investing all room.
                desired = max(wealth * w.sum() - target[fixed].sum(), 0.0)
                target[free] *= min(room, desired) / target[free].sum()
        buy = np.clip(target - h, 0, None)
        sell = np.clip(h - target, 0, None)
        traded = buy + sell
        valid_adv = np.isfinite(AD[i]) & (AD[i] > 0)
        participation = np.divide(traded, AD[i], out=np.full(len(cols), np.inf), where=valid_adv)
        participation[traded <= 1e-6] = 0.0
        max_participation = float(participation.max()) if len(cols) else 0.0
        cost = 0.0
        if cfg.costs:
            hs = np.fmax(np.nan_to_num(HS[i]), cfg.spread_floor)
            c = C.trade_costs_vec(buy, sell, date, hs, SG[i], AD[i], cfg.k, cfg.brokerage)
            cost = float(np.nansum(c))
        if not np.isfinite(cost) or cost >= wealth:
            raise ValueError(f"Trading costs exhaust portfolio wealth in {m}")
        if ((target > 1e-6) & ~np.isfinite(R[i])).any():
            raise ValueError(f"Missing holding return for an invested security in {m}; audit the data")
        if ((target > 1e-6) & (R[i] < -1)).any():
            raise ValueError(f"Holding return below -100% in {m}")
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
        cash *= (1 + cfg.cash_yield) ** (1 / 12)
        if book is not None:
            for j in np.flatnonzero(target > 0):
                book.grow(cols[j], 1 + r[j])
        wealth_end = h.sum() + cash
        gross = float(np.nansum(w * r)) if w.sum() > 0 else 0.0
        rows.append({"month": m + 1, "gross": gross, "net": wealth_end / wealth_start - 1,
                     "cost_inr": cost, "tax_inr": tax_paid, "turnover": float((buy + sell).sum() / 2 / wealth),
                     "names": int((w > 0).sum()), "wealth": wealth_end,
                     "max_participation": max_participation,
                     "capacity_breach": max_participation > cfg.participation_limit,
                     "missing_names": int(missing.sum()), "missing_exposure_start": missing_exposure,
                     "unfilled_target_weight": unfilled_weight,
                     "missing_open_names": int(missing_open.sum()),
                     "missing_open_exposure_start": missing_open_exposure,
                     "unfilled_open_target_weight": unfilled_open_weight})
    out = pd.DataFrame(rows, columns=["month", "gross", "net", "cost_inr", "tax_inr", "turnover",
                                      "names", "wealth", "max_participation", "capacity_breach", "missing_names",
                                      "missing_open_names", "missing_open_exposure_start", "unfilled_open_target_weight",
                                      "missing_exposure_start", "unfilled_target_weight"]).set_index("month")
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
