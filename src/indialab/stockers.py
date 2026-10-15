"""Offline bridge to the uploaded Stockers engine; original source stays intact.

Stockers generates per-security signals and positions. The India simulator adds
the monthly point-in-time universe, rupee costs, FIFO tax and capacity diagnostics.
This is an explicitly adapted replay, not the US YEARLY_TOP20/WGY portfolio.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from . import research as rs

ENGINE = Path(__file__).resolve().parents[2] / "vendor/stockers/engine_v7_2.py"
ENGINE_SHA256 = "b037f944b3bff5db60d01552582c80ce65a4234b1e7f1bb21775418ab5f00c9a"


@lru_cache(maxsize=1)
def load_engine():
    if hashlib.sha256(ENGINE.read_bytes()).hexdigest() != ENGINE_SHA256:
        raise ValueError("Vendored Stockers source does not match the supplied upload")
    name = "_indialab_original_stockers"
    spec = importlib.util.spec_from_file_location(name, ENGINE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses require the module during execution
    # The uploaded module suppresses warnings globally; do not inherit that policy.
    with warnings.catch_warnings():
        spec.loader.exec_module(module)
    module.ALLOW_DOWNLOAD = False
    module.STRICT_DATA_MODE = True
    return module


def ohlcv(daily: pd.DataFrame) -> pd.DataFrame:
    """Use the NSE panel's causal price index; never fetch or backfill prices."""
    required = {"date", "open", "high", "low", "close", "px", "volume"}
    if required - set(daily):
        raise ValueError(f"Missing NSE columns: {sorted(required - set(daily))}")
    d = daily.sort_values("date").set_index("date")
    if d.index.has_duplicates:
        raise ValueError("Duplicate security/session rows")
    # An archive row with no trades is not an executable OHLC observation.
    # Retain its economic marks in the India panel, but do not invent a native
    # Stockers open/high/low or count it toward the strategy's session warmup.
    d = d.loc[d.volume.ne(0)]
    if d.empty:
        raise ValueError("No traded daily bars for this security")
    if not np.isfinite(d[list(required - {"date"})].to_numpy(float)).all():
        raise ValueError("OHLCV contains missing or nonfinite values")
    if (d[["open", "high", "low", "close", "px"]] <= 0).any().any() or (d.volume < 0).any():
        raise ValueError("Positive prices and nonnegative volume are required")
    scale = d.px / d.close
    return pd.DataFrame({"Open": d.open * scale, "High": d.high * scale,
                         "Low": d.low * scale, "Close": d.px, "Volume": d.volume})


def symbol_replay(daily: pd.DataFrame, *, ema: int = 200, min_gain: float = 30,
                  warmup: int = 100, causal_warmup: bool = True):
    """Preserve original signal lag, pending-buy logic, exits and open/open returns.

    One disclosed correction: execution at a month's first open cannot depend on
    the observations accumulated by that month's end. Warmup uses prior counts.
    Native mode exists for regression comparisons only.
    """
    module = load_engine()
    strategy = module.UltimateSimpleSymbol(
        "NSE", ohlcv(daily), module.StrategyParams(ema_period=ema,
                                                  min_sell_gain_pct=min_gain, warmup_days=warmup))
    strategy._to_monthly()
    strategy.build_signals()
    if causal_warmup and not strategy.signals.empty:
        strategy.signals["Tradable"] = strategy.signals["_cumobs"].shift(1).ge(warmup)
    strategy.compute_position_and_returns()
    return strategy


def weights(daily: pd.DataFrame, panel: rs.Panel, elig: pd.DataFrame, *, ema: int = 200,
            min_gain: float = 30, warmup: int = 100, causal_warmup: bool = True) -> pd.DataFrame:
    """Equal-weight native active positions that are eligible at the prior close.

    The native position labels the execution month; India weights label the signal
    month. Subtract one month, with no additional signal shift. No leverage, target
    volatility, US factors, US index lists, or client cash flows are imported.
    """
    active = pd.DataFrame(False, index=elig.index, columns=elig.columns)
    for sec, frame in daily.groupby("sec", sort=False):
        if sec not in active or not elig[sec].any():
            continue
        strategy = symbol_replay(frame, ema=ema, min_gain=min_gain, warmup=warmup,
                                 causal_warmup=causal_warmup)
        position = strategy.position
        if position is None or position.empty:
            continue
        position = position.copy()
        position.index = position.index.to_period("M") - 1
        active[sec] = position.reindex(active.index).fillna(0).gt(0)
    return rs.target_weights(active & elig)
