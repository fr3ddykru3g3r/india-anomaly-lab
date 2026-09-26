"""Indian capital-gains tax on listed equity for a resident individual, by financial year.

Model (stated simplifications in brackets):
- FIFO lots per security; a lot held >= 365 days is long-term when sold.
- Rates follow the regime in force on the SALE date (config capital_gains_tax_resident_individual).
- LTCG grandfathering: for lots bought before 1 Feb 2018 the cost is max(cost, min(value on
  31 Jan 2018, sale value)) [value taken at the Jan-2018 month end].
- Set-off each FY: short-term losses offset short- then long-term gains; long-term losses only
  long-term gains; unused losses carry forward 8 years.
- The annual LTCG exemption (Rs 1 lakh from FY2018-19, Rs 1.25 lakh from FY2024-25) applies once.
- Cess on tax; surcharge ignored [small investor]. Tax is paid from the portfolio at the
  first rebalance after 31 March [pro-rata, without triggering further gains].
"""
from __future__ import annotations

from collections import defaultdict, deque

import numpy as np
import pandas as pd

from .costs import schedule

GRANDFATHER_DATE = pd.Timestamp("2018-01-31")


def fy_of(date: pd.Timestamp) -> int:
    """Financial year labelled by its starting calendar year (FY2018-19 -> 2018)."""
    return date.year if date.month >= 4 else date.year - 1


def regime(date: pd.Timestamp) -> dict:
    out = None
    for row in schedule()["capital_gains_tax_resident_individual"]:
        if date >= pd.Timestamp(row["from"]):
            out = row
    return out or {"stcg": 0.0, "ltcg": 0.0, "ltcg_exemption_inr": None, "holding_days_for_long_term": 365}


def cess(fy: int) -> float:
    rate = 0.0
    for row in schedule().get("cess_on_tax", []):
        if fy >= row["from_fy"]:
            rate = row["rate"]
    return rate


class TaxBook:
    def __init__(self):
        self.lots: dict[str, deque] = defaultdict(deque)   # sec -> [acq_date, cost, mv, fmv2018]
        self.gains = defaultdict(lambda: {"st": [], "lt": []})  # fy -> lists of (gain, rate)
        self.carry_st: list[tuple[int, float]] = []          # (fy incurred, loss > 0)
        self.carry_lt: list[tuple[int, float]] = []
        self.paid: dict[int, float] = {}

    # --- position bookkeeping --------------------------------------------
    def buy(self, sec: str, date: pd.Timestamp, amount: float) -> None:
        if amount > 0:
            self.lots[sec].append([date, amount, amount, None])

    def grow(self, sec: str, factor: float) -> None:
        for lot in self.lots.get(sec, ()):
            lot[2] *= factor

    def mark_grandfather(self) -> None:
        for lots in self.lots.values():
            for lot in lots:
                lot[3] = lot[2]

    def scale_mv(self, factor: float) -> None:
        """Market value shrinks (costs, tax paid out) but the tax cost basis does not."""
        for lots in self.lots.values():
            for lot in lots:
                lot[2] *= factor
                if lot[3] is not None:
                    lot[3] *= factor

    def scale_all(self, factor: float) -> None:
        for lots in self.lots.values():
            for lot in lots:
                lot[1] *= factor
                lot[2] *= factor
                if lot[3] is not None:
                    lot[3] *= factor

    def sell(self, sec: str, date: pd.Timestamp, amount: float) -> None:
        """Sell `amount` rupees of market value FIFO and book the gains."""
        lots = self.lots.get(sec)
        if not lots or amount <= 0:
            return
        reg, fy = regime(date), fy_of(date)
        remaining = amount
        while remaining > 1e-9 and lots:
            acq, cost, mv, fmv = lots[0]
            take = min(remaining, mv)
            frac = take / mv if mv > 0 else 1.0
            basis = cost * frac
            long_term = (date - acq).days >= reg["holding_days_for_long_term"]
            if long_term and fmv is not None and acq <= GRANDFATHER_DATE:
                basis = max(basis, min(fmv * frac, take))
            gain = take - basis
            self.gains[fy]["lt" if long_term else "st"].append((gain, reg["ltcg" if long_term else "stcg"]))
            if frac >= 1 - 1e-12:
                lots.popleft()
            else:
                lots[0][1] -= cost * frac
                lots[0][2] -= take
                if fmv is not None:
                    lots[0][3] -= fmv * frac
            remaining -= take
        if not lots:
            self.lots.pop(sec, None)

    def sell_all(self, sec: str, date: pd.Timestamp) -> None:
        mv = sum(l[2] for l in self.lots.get(sec, ()))
        self.sell(sec, date, mv)

    def market_value(self, sec: str) -> float:
        return float(sum(l[2] for l in self.lots.get(sec, ())))

    # --- annual settlement ------------------------------------------------
    def settle(self, fy: int) -> float:
        g = self.gains.pop(fy, {"st": [], "lt": []})
        st = sum(x for x, _ in g["st"])
        lt = sum(x for x, _ in g["lt"])
        pos_st = [(x, r) for x, r in g["st"] if x > 0]
        pos_lt = [(x, r) for x, r in g["lt"] if x > 0]
        rate_st = sum(x * r for x, r in pos_st) / sum(x for x, _ in pos_st) if pos_st else regime(pd.Timestamp(f"{fy + 1}-03-31"))["stcg"]
        rate_lt = sum(x * r for x, r in pos_lt) / sum(x for x, _ in pos_lt) if pos_lt else regime(pd.Timestamp(f"{fy + 1}-03-31"))["ltcg"]
        horizon = schedule().get("loss_carry_forward_years", 8)
        self.carry_st = [(y, l) for y, l in self.carry_st if fy - y <= horizon]
        self.carry_lt = [(y, l) for y, l in self.carry_lt if fy - y <= horizon]
        # current-year set-off
        if st < 0:
            use = min(-st, max(lt, 0.0))
            lt -= use
            st += use
        # brought-forward losses: short-term against st then lt; long-term against lt only
        st, self.carry_st = _apply_carry(st, self.carry_st)
        if st <= 0:
            lt, self.carry_st = _apply_carry(lt, self.carry_st)
        lt, self.carry_lt = _apply_carry(lt, self.carry_lt)
        if st < 0:
            self.carry_st.append((fy, -st))
            st = 0.0
        if lt < 0:
            self.carry_lt.append((fy, -lt))
            lt = 0.0
        reg = regime(pd.Timestamp(f"{fy + 1}-03-31"))
        exempt = reg.get("ltcg_exemption_inr") or 0.0
        if rate_lt == 0.0:
            exempt = 0.0
        tax = (st * rate_st + max(lt - exempt, 0.0) * rate_lt) * (1 + cess(fy))
        self.paid[fy] = tax
        return tax


def _apply_carry(gain: float, carry: list) -> tuple[float, list]:
    if gain <= 0 or not carry:
        return gain, carry
    out = []
    for y, loss in carry:
        use = min(loss, max(gain, 0.0))
        gain -= use
        if loss - use > 1e-9:
            out.append((y, loss - use))
    return gain, out
