"""Dated Indian delivery-trade cost model.

    cost = statutory levies (STT, stamp, exchange, SEBI, GST on fees)
         + fixed DP charge per scrip sold
         + half-spread + square-root market impact

Statutory levies depend on the trade date (see config/india_costs.json); the
current regime is used for dates on or after its effective date and the dated
STT history before it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

CONFIG = Path(__file__).resolve().parents[2] / "config" / "india_costs.json"


@lru_cache(maxsize=1)
def schedule() -> dict:
    return json.loads(CONFIG.read_text())


def _stt_rate(date: pd.Timestamp) -> float:
    cfg = schedule()
    if date >= pd.Timestamp(cfg["current_regime"]["effective_from"]):
        return cfg["current_regime"]["stt_delivery_sell"]
    rate = 0.0
    for row in cfg["stt_history"]:
        if date >= pd.Timestamp(row["from"]):
            rate = row["delivery_each_side"]
    return rate


def _stamp_rate(date: pd.Timestamp) -> float:
    return schedule()["current_regime"]["stamp_duty_buy"] if date >= pd.Timestamp("2020-07-01") else 0.0001


@dataclass(frozen=True)
class TradeCost:
    statutory: float      # INR
    fixed: float          # INR
    impact: float         # INR

    @property
    def total(self) -> float:
        return self.statutory + self.fixed + self.impact


def trade_cost(value_inr: float, side: str, date, half_spread: float = 0.0,
               sigma_daily: float = 0.0, adv_inr: float = np.inf, k: float | None = None) -> TradeCost:
    """Cost in rupees of one delivery trade of `value_inr` on `date`.

    side: "buy" or "sell". half_spread and sigma_daily are fractions.
    """
    if side not in ("buy", "sell"):
        raise ValueError(side)
    date = pd.Timestamp(date)
    cur = schedule()["current_regime"]
    stt = _stt_rate(date) * value_inr
    stamp = _stamp_rate(date) * value_inr if side == "buy" else 0.0
    fees = (cur["exchange_txn_nse"] + cur["sebi_turnover_fee"] + cur["brokerage_delivery"]) * value_inr
    gst = cur["gst_rate_on_fees"] * fees
    fixed = cur["dp_charge_per_scrip_sold_inr"] if side == "sell" else 0.0
    k = schedule()["market_impact"]["k"] if k is None else k
    participation = value_inr / adv_inr if np.isfinite(adv_inr) and adv_inr > 0 else 0.0
    impact = value_inr * (half_spread + k * sigma_daily * np.sqrt(participation))
    return TradeCost(statutory=stt + stamp + fees + gst, fixed=fixed, impact=impact)


def round_trip_bps(value_inr: float, date, **kw) -> float:
    b = trade_cost(value_inr, "buy", date, **kw).total
    s = trade_cost(value_inr, "sell", date, **kw).total
    return (b + s) / value_inr * 1e4


def abdi_ranaldo_half_spread(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Abdi & Ranaldo (2017) close-high-low spread estimator, returned as a half-spread.

    s^2 = 4 * (c_t - eta_t) * (c_t - eta_{t+1}), eta = (log H + log L)/2, c = log C.
    Negative two-day estimates are set to zero before averaging (as in the paper).
    """
    eta = (np.log(high) + np.log(low)) / 2
    c = np.log(close)
    s2 = 4 * (c - eta) * (c - eta.shift(-1))
    s2 = s2.clip(lower=0)
    return np.sqrt(s2) / 2


def statutory_rates(date) -> dict:
    """Per-rupee statutory rates for a delivery trade on `date` (buy and sell side)."""
    date = pd.Timestamp(date)
    cur = schedule()["current_regime"]
    fees = cur["exchange_txn_nse"] + cur["sebi_turnover_fee"]
    gst = cur["gst_rate_on_fees"] * fees
    stt = _stt_rate(date)
    return {"buy": stt + _stamp_rate(date) + fees + gst, "sell": stt + fees + gst,
            "dp_per_sell": cur["dp_charge_per_scrip_sold_inr"]}


def trade_costs_vec(buy_inr: np.ndarray, sell_inr: np.ndarray, date, half_spread: np.ndarray,
                    sigma_daily: np.ndarray, adv_inr: np.ndarray, k: float, brokerage: float = 0.0) -> np.ndarray:
    """Vectorised `trade_cost` for one rebalance date: rupee cost per security.

    Same model as trade_cost, plus an optional brokerage rate (0 for a discount broker;
    about 0.3-0.5% was typical for full-service brokers in the 2000s). GST on brokerage included.
    """
    rates = statutory_rates(date)
    gst = schedule()["current_regime"]["gst_rate_on_fees"]
    buy, sell = np.nan_to_num(buy_inr), np.nan_to_num(sell_inr)
    traded = buy + sell
    hs = np.nan_to_num(half_spread)
    sig = np.nan_to_num(sigma_daily)
    adv = np.where(np.isfinite(adv_inr) & (adv_inr > 0), adv_inr, np.inf)
    impact = traded * (hs + k * sig * np.sqrt(traded / adv))
    stat = buy * rates["buy"] + sell * rates["sell"] + traded * brokerage * (1 + gst)
    dp = np.where(sell > 0, rates["dp_per_sell"], 0.0)
    return stat + dp + impact
