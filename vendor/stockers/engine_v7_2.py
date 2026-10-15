# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import io
import re
import time
import shutil
import warnings
import zipfile
import urllib.request
import math
import hashlib
import platform
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional, Set
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import argparse
import json
from datetime import datetime

warnings.filterwarnings("ignore")
try:
    import yfinance as yf

    _YF_OK = True
except Exception:
    _YF_OK = False

try:
    import statsmodels.api as sm

    _SM_OK = True
except Exception:
    _SM_OK = False

try:
    from scipy import stats as scipy_stats
    _SCIPY_OK = True
except Exception:
    scipy_stats = None
    _SCIPY_OK = False

# =====================================================================
# Config (defaults; most are CLI-overridable)
# =====================================================================
ALLOW_DOWNLOAD = True
DOWNLOAD_SOURCE = "yahoo"

# Anchor all project paths to the Stockers project rather than the shell's
# current working directory. This makes the script safe to launch from `/`,
# Downloads, or anywhere else. Override on another machine with
# STOCKERS_PROJECT_ROOT=/path/to/stocker.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_KNOWN_PROJECT_ROOT = "/Users/Kyrosah/Downloads/Stockers/stocker"
_DEFAULT_PROJECT_ROOT = _KNOWN_PROJECT_ROOT if os.path.isdir(_KNOWN_PROJECT_ROOT) else _SCRIPT_DIR
PROJECT_ROOT = os.path.abspath(os.path.expanduser(os.environ.get(
    "STOCKERS_PROJECT_ROOT", _DEFAULT_PROJECT_ROOT
)))

DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# Local Tiingo archive used for delisted / Yahoo-missing names.
# Override without editing the script by setting STOCKERS_TIINGO_MISSING_DIR.
TIINGO_MISSING_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "STOCKERS_TIINGO_MISSING_DIR",
    os.path.join(PROJECT_ROOT, "data_tiingo_missing"),
)))

CSVA_DIR = os.path.join(PROJECT_ROOT, "final")
SIGNALS_DIR = os.path.join(CSVA_DIR, "signals")
OOS_DIR = os.path.join(CSVA_DIR, "oos")
DIAG_DIR = os.path.join(CSVA_DIR, "diag")
MC_DIR = os.path.join(CSVA_DIR, "monte_carlo")
WGY_DIR = os.path.join(CSVA_DIR, "wgy_2027_case")
FACTOR_DIR = os.path.join(PROJECT_ROOT, "factors", "kenneth_french")

# Official Kenneth French Data Library downloads. These are downloaded once,
# cached locally, hashed, and then reused. Stock-price downloads remain disabled
# in --canonical_run; factor bootstrapping is separately controlled and auditable.
KEN_FRENCH_FF5_DAILY_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
)
KEN_FRENCH_MOM_DAILY_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Momentum_Factor_daily_CSV.zip"
)
KEN_FRENCH_FF5_FILENAME = "F-F_Research_Data_5_Factors_2x3_daily.csv"
KEN_FRENCH_MOM_FILENAME = "F-F_Momentum_Factor_daily.csv"

_SOURCE_SPAN_CACHE: Dict[str, Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]] = {}

# Shared in-memory cache. Parameter grids and walk-forward tests repeatedly request
# the same daily files; parsing them once materially reduces runtime without
# changing any strategy logic. Values are always returned as copies so callers
# cannot mutate the cached canonical frame.
_DAILY_DATA_CACHE: Dict[Tuple[str, str, float], pd.DataFrame] = {}
STRICT_DATA_MODE = False
DATA_QUALITY_EVENTS: List[Dict[str, Any]] = []


def _configure_project_paths(project_root: Optional[str] = None,
                             tiingo_missing_dir: Optional[str] = None,
                             factor_dir: Optional[str] = None) -> None:
    """Re-anchor all project paths after CLI parsing for portable runs."""
    global PROJECT_ROOT, DATA_DIR, TIINGO_MISSING_DIR, CSVA_DIR, SIGNALS_DIR, OOS_DIR, DIAG_DIR, MC_DIR, WGY_DIR, FACTOR_DIR
    root = project_root or PROJECT_ROOT
    PROJECT_ROOT = os.path.abspath(os.path.expanduser(root))
    DATA_DIR = os.path.join(PROJECT_ROOT, "data")
    TIINGO_MISSING_DIR = os.path.abspath(os.path.expanduser(
        tiingo_missing_dir or os.environ.get("STOCKERS_TIINGO_MISSING_DIR", os.path.join(PROJECT_ROOT, "data_tiingo_missing"))
    ))
    CSVA_DIR = os.path.join(PROJECT_ROOT, "final")
    SIGNALS_DIR = os.path.join(CSVA_DIR, "signals")
    OOS_DIR = os.path.join(CSVA_DIR, "oos")
    DIAG_DIR = os.path.join(CSVA_DIR, "diag")
    MC_DIR = os.path.join(CSVA_DIR, "monte_carlo")
    WGY_DIR = os.path.join(CSVA_DIR, "wgy_2027_case")
    FACTOR_DIR = os.path.abspath(os.path.expanduser(
        factor_dir or os.environ.get("STOCKERS_FACTOR_DIR", os.path.join(PROJECT_ROOT, "factors", "kenneth_french"))
    ))


def _sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _fmt_pvalue(p: float) -> str:
    if p is None or not np.isfinite(p):
        return "--"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"

def _download_binary(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    """Download bytes from an official source with a small retry budget."""
    last_exc: Optional[Exception] = None
    headers = {
        "User-Agent": "Stockers-WGY-Research/1.0 (+educational research)",
        "Accept": "*/*",
    }
    for attempt in range(1, int(retries) + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=int(timeout)) as resp:
                payload = resp.read()
            if not payload:
                raise IOError(f"empty response from {url}")
            return payload
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.0 * attempt)
    raise RuntimeError(f"Could not download official Kenneth French factor file from {url}: {last_exc}")


def _extract_first_csv_from_zip_bytes(payload: bytes, target_path: str) -> str:
    """Extract the first CSV member from an in-memory ZIP to a stable local path."""
    with zipfile.ZipFile(io.BytesIO(payload), "r") as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv") and not n.endswith("/")]
        if not names:
            raise ValueError("Kenneth French ZIP did not contain a CSV file.")
        # Prefer the largest CSV member in case an archive ever contains metadata extras.
        name = max(names, key=lambda n: zf.getinfo(n).file_size)
        data = zf.read(name)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    tmp = target_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, target_path)
    return target_path


def _factor_file_coverage(path: str, kind: str) -> Dict[str, Any]:
    """Validate a cached official factor file and return coverage metadata."""
    if kind == "ff5":
        df = load_ff5(path, freq="daily")
    elif kind == "mom":
        df = _parse_momentum_daily_csv(path)
    else:
        raise ValueError(f"Unknown factor kind: {kind}")
    if df.empty:
        raise ValueError(f"{kind} factor file parsed to an empty data set: {path}")
    return {
        "rows": int(len(df)),
        "first_date": str(pd.Timestamp(df.index.min()).date()),
        "last_date": str(pd.Timestamp(df.index.max()).date()),
        "sha256": _sha256_file(path),
    }


def ensure_kenneth_french_factors(*, factor_dir: Optional[str] = None,
                                   refresh: bool = False,
                                   required_end: Optional[str] = None) -> Tuple[str, str]:
    """Ensure official U.S. FF5-daily + Momentum-daily files are locally cached.

    This is intentionally *not* a live stream. The first run downloads the two
    official Dartmouth ZIP archives, extracts stable CSVs under PROJECT_ROOT,
    records SHA-256 hashes/source URLs, and every later run reuses those exact
    cached files unless --refresh_factors is requested.
    """
    root = os.path.abspath(os.path.expanduser(factor_dir or FACTOR_DIR))
    os.makedirs(root, exist_ok=True)
    ff5_path = os.path.join(root, KEN_FRENCH_FF5_FILENAME)
    mom_path = os.path.join(root, KEN_FRENCH_MOM_FILENAME)

    specs = [
        ("ff5", KEN_FRENCH_FF5_DAILY_URL, ff5_path),
        ("mom", KEN_FRENCH_MOM_DAILY_URL, mom_path),
    ]
    metadata: Dict[str, Any] = {
        "provider": "Kenneth R. French Data Library, Dartmouth/Tuck",
        "retrieved_or_checked_utc": datetime.utcnow().isoformat() + "Z",
        "factor_dir": root,
        "files": {},
    }
    for kind, url, path in specs:
        need_download = bool(refresh or not os.path.isfile(path))
        if not need_download:
            try:
                cov = _factor_file_coverage(path, kind)
                if required_end:
                    # Allow a month of publication lag, but reject obviously stale/corrupt caches.
                    req = pd.to_datetime(required_end)
                    last = pd.to_datetime(cov["last_date"])
                    if last < req - pd.DateOffset(months=2):
                        need_download = True
            except Exception:
                need_download = True
        if need_download:
            cli.info(f"Downloading official Kenneth French {kind.upper()} factors once → {path}")
            payload = _download_binary(url)
            _extract_first_csv_from_zip_bytes(payload, path)
        cov = _factor_file_coverage(path, kind)
        cov.update({"url": url, "path": os.path.abspath(path), "downloaded_this_run": need_download})
        metadata["files"][kind] = cov

    os.makedirs(DIAG_DIR, exist_ok=True)
    manifest_path = os.path.join(DIAG_DIR, "kenneth_french_factor_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)
    cli.saved(manifest_path)
    cli.box("KENNETH FRENCH FACTORS", [
        ("FF5 source", "official Dartmouth/Tuck cache"),
        ("FF5 coverage", f"{metadata['files']['ff5']['first_date']} → {metadata['files']['ff5']['last_date']}"),
        ("Momentum coverage", f"{metadata['files']['mom']['first_date']} → {metadata['files']['mom']['last_date']}"),
        ("Cache directory", root),
        ("FF5 SHA-256", metadata['files']['ff5']['sha256'][:16] + "…"),
        ("MOM SHA-256", metadata['files']['mom']['sha256'][:16] + "…"),
    ])
    return ff5_path, mom_path

POSSIBLE_DATE_COLS = ["date", "datetime", "time", "timestamp"]
DUMB_DATE_COLS = ["date", "datetime", "Date", "Datetime", "timestamp", "Timestamp", "Unnamed: 0", "Unnamed:0"]

COL_ALIASES = {
    "open": ["open", "o", "opn"],
    "high": ["high", "h", "hi"],
    "low": ["low", "l", "lo"],
    "close": ["close", "c"],
    "volume": ["volume", "v", "vol"],
    "adjclose": ["adjclose", "adj close", "adj_close", "adjusted close", "closeadj"],
}

# IMPORTANT: leave volume UNADJUSTED so liquidity math isn't garbage.
ADJUST_BY_ADJCLOSE = True
ADJUST_VOLUME = False


# ---------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------

def load_ff5(path, freq="daily"):
    """
    Load Fama-French 5-factor CSV (daily or monthly) — automatically skips header junk.
    Converts percent values to decimals and fixes blank first column.
    freq: 'daily' (as-is) or 'monthly' (compound daily → month-end)
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    # find the actual header row
    for i, line in enumerate(text.splitlines()):
        if "Mkt-RF" in line and "SMB" in line and "HML" in line:
            header_line = i
            break
    else:
        raise ValueError(f"Could not find Fama-French header row in {path}")

    df = pd.read_csv(io.StringIO(text), skiprows=header_line)
    if df.columns[0] == "" or str(df.columns[0]).startswith("Unnamed"):
        df.rename(columns={df.columns[0]: "DATE"}, inplace=True)

    df = df[df["DATE"].astype(str).str.fullmatch(r"\d{8}")]
    df["DATE"] = pd.to_datetime(df["DATE"], format="%Y%m%d")
    df.set_index("DATE", inplace=True)
    cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]
    df = df[cols].apply(pd.to_numeric, errors="coerce") / 100.0
    df = df.dropna(how="any")

    if freq == "monthly":
        df = df.groupby(pd.Grouper(freq="ME")).apply(lambda g: (1 + g).prod() - 1)

    return df


def _normalize_col_name(name: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(name).strip().lower())


def _infer_date_col(df: pd.DataFrame, *, extra_candidates: Optional[List[str]] = None) -> Optional[str]:
    """Heuristic date-column detection for messy vendor CSVs."""
    if df is None or df.empty:
        return None

    cols = list(df.columns)
    if not cols:
        return None

    normalized = {_normalize_col_name(col): col for col in cols}

    search: List[str] = []
    if extra_candidates:
        search.extend(extra_candidates)
    search.extend(POSSIBLE_DATE_COLS)
    search.extend(DUMB_DATE_COLS)

    for cand in search:
        key = _normalize_col_name(cand)
        if key in normalized:
            return normalized[key]

    # Fall back to dtype inspection: pick the first column that parses as datetime
    for col in cols:
        series = df[col]
        if series.empty:
            continue
        sample = series.dropna().astype(str).head(10)
        if sample.empty:
            continue
        parsed = pd.to_datetime(sample, errors="coerce", utc=False)
        if parsed.notna().mean() >= 0.6:
            return col

    return None


# =====================================================================
# GLOBAL CONSTANTS & HYPERPARAMETERS
# =====================================================================
# --- Default Strategy Parameters ---
DEFAULT_EMA_PERIOD = 200
DEFAULT_MIN_SELL_GAIN_PCT = 30.0
DEFAULT_TARGET_VOL = 0.35

# --- Data-Snooping & WFA Test Parameters ---
# Defines the parameter grid for sensitivity testing
EMA_GRID_RANGE = [150, 175, 200, 225, 250]
GAIN_GRID_RANGE = [20.0, 25.0, 30.0, 35.0, 40.0]

# Walk-Forward Analysis (WFA) settings
WFA_TRAIN_YEARS = 6
WFA_TEST_YEARS = 2
WFA_STEP_YEARS = 1

# Monte Carlo / Bootstrap settings
MC_SIMS = 10000
MC_BLOCK_SIZE = 6  # For Stationary Block Bootstrap
REALITY_CHECK_BOOTS = 2000
SPA_BOOTS = 2000

# Fama-French Regression settings
FF5_HAC_LAGS = 6

# Volatility Scaling settings
VOL_LOOKBACK_MONTHS = 12
VOL_CLAMP_LOW = 0.1
VOL_CLAMP_HIGH = 1.0
VOL_WINSOR_PCT = 0.1
VOL_MIN_SCALE = 0.30
VOL_UP_CAP = 0.20
VOL_DOWN_CAP = 0.20
DD_THROTTLE_CUTS = ((-0.10, 0.8), (-0.15, 0.6), (-0.20, 0.5), (-0.30, 0.3))

# Ticker cleanups / aliases
TICKER_ALIAS = {
    "YHOO": "AABA",
    # Facebook changed its ticker from FB to META in 2022. Current Yahoo "FB"
    # can refer to a different company, so META is the safe continuity alias.
    "FB": "META",
}

# =====================================================================
# STRATEGY UNIVERSE DEFINITION
# =====================================================================

"""
This dictionary defines the trading universe for the "Hype" factor.
It is the core of the strategy's thesis.

CRITICAL METHODOLOGY NOTES (Read This First):

1.  **LIMITED LOOK-AHEAD BIAS (1-Year Lag):**
    This map is **explicitly lagged by one year.** The list associated with
    a given year (e.g., `1999`) defines the "Top 20" stocks *at the end of*
    that year. The backtest logic (see `build_portfolio_returns_from_csv`)
    uses this list for trading *during the following year* (e.g., 2000).
    This correctly simulates the real-world process of an annual review
    and rebalance.

2.  **SURVIVORSHIP BIAS IS THE *POINT*:**
    This list was **manually curated to include "ghosts"**—companies that
    were "Top 20" hype stocks but subsequently crashed, were acquired, or
    were delisted (e.g., JDSU, YHOO, JAVA, EMC, BRCM).

    The ENTIRE THESIS of this strategy is not just to pick winners,
    but to **survive the inevitable crash** of a hype-driven universe.
    The "Dumb Universe" test (`-72.5%` drawdown) proves the lethality of
    this universe. This list is the "graveyard" that proves the
    strategy's 200-EMA signal is a robust survival tool, not just
    a lucky filter on a biased list.

3.  **TICKER ALIASING (Line 153):**
    The `TICKER_ALIAS` map (e.g., "YHOO": "AABA") is used by the data
    loader to correctly handle ticker changes for these "ghosts,"
    ensuring data continuity and accurate delisting simulation.
"""
YEARLY_TOP20 = {
    1999: [
        "MSFT", "CSCO", "INTC", "DELL", "ORCL", "QCOM", "AMZN", "JDSU", "YHOO", "EBAY",
        "ADBE", "TXN", "JAVA", "MCHP", "BRCM", "AAPL", "EMC", "XLNX", "SYMC",
    ],
    2000: [
        "MSFT", "CSCO", "INTC", "ORCL", "DELL", "QCOM", "AMZN", "JDSU", "YHOO", "EBAY",
        "ADBE", "JAVA", "TXN", "EMC", "BRCM", "XLNX", "AAPL", "SYMC", "MCHP",
    ],
    2001: [
        "MSFT", "INTC", "DELL", "CSCO", "ORCL", "AAPL", "ADBE", "EBAY", "YHOO", "AMZN",
        "QCOM", "TXN", "EMC", "JAVA", "XLNX", "MCHP", "BRCM", "SYMC", "NVDA", "CHKP",
    ],
    2002: [
        "MSFT", "INTC", "DELL", "CSCO", "AAPL", "EBAY", "ADBE", "AMZN", "ORCL", "QCOM",
        "NVDA", "YHOO", "TXN", "EMC", "JDSU", "SYMC", "BRCM", "XLNX", "MCHP", "PAYX",
    ],
    2003: [
        "MSFT", "INTC", "CSCO", "AAPL", "DELL", "EBAY", "ORCL", "AMZN", "ADBE", "QCOM",
        "NVDA", "YHOO", "TXN", "SYMC", "EMC", "XLNX", "BRCM", "MCHP", "MAT", "SPLS",
    ],
    2004: [
        "MSFT", "AAPL", "INTC", "CSCO", "DELL", "EBAY", "QCOM", "AMZN", "ORCL", "ADBE",
        "NVDA", "YHOO", "TXN", "SYMC", "EMC", "XLNX", "MCHP", "BRCM", "SBUX", "BIIB",
    ],
    2005: [
        "MSFT", "AAPL", "INTC", "CSCO", "DELL", "EBAY", "QCOM", "AMZN", "ORCL", "ADBE",
        "NVDA", "YHOO", "TXN", "SBUX", "SYMC", "MCHP", "BRCM", "BIIB", "CELG", "GILD",
    ],
    2006: [
        "MSFT", "AAPL", "INTC", "CSCO", "DELL", "AMZN", "EBAY", "QCOM", "ADBE", "NVDA",
        "ORCL", "YHOO", "TXN", "SBUX", "BIIB", "CELG", "GILD", "MCHP", "BRCM", "PAYX",
    ],
    2007: [
        "AAPL", "MSFT", "GOOG", "CSCO", "INTC", "QCOM", "AMZN", "ORCL", "NVDA", "ADBE",
        "EBAY", "SBUX", "YHOO", "TXN", "BIIB", "CELG", "GILD", "MCHP", "BRCM", "NFLX",
    ],
    2008: [
        "AAPL", "MSFT", "GOOG", "CSCO", "INTC", "AMZN", "QCOM", "ORCL", "ADBE", "NVDA",
        "EBAY", "SBUX", "BIIB", "CELG", "GILD", "YHOO", "TXN", "BRCM", "MCHP", "NFLX",
    ],
    2009: [
        "AAPL", "MSFT", "GOOG", "AMZN", "CSCO", "QCOM", "ADBE", "ORCL", "INTC", "NVDA",
        "EBAY", "SBUX", "BIIB", "CELG", "GILD", "NFLX", "BRCM", "MCHP", "TXN", "WYNN",
    ],
    2010: [
        "AAPL", "GOOG", "MSFT", "AMZN", "ORCL", "QCOM", "ADBE", "INTC", "NVDA", "NFLX",
        "EBAY", "SBUX", "CELG", "GILD", "BIIB", "TSLA", "COST", "PAYX", "CSCO", "TXN",
    ],
    2011: [
        "AAPL", "GOOG", "MSFT", "AMZN", "INTC", "ADBE", "NFLX", "QCOM", "NVDA", "EBAY",
        "SBUX", "CELG", "GILD", "BIIB", "TSLA", "COST", "CSCO", "TXN", "PAYX", "VRTX",
    ],
    2012: [
        "AAPL", "GOOG", "AMZN", "MSFT", "INTC", "QCOM", "ADBE", "NFLX", "NVDA", "EBAY",
        "SBUX", "CELG", "GILD", "BIIB", "TSLA", "COST", "VRTX", "REGN", "CSCO", "TXN",
    ],
    2013: [
        "AAPL", "GOOG", "AMZN", "MSFT", "FB", "NFLX", "QCOM", "ADBE", "NVDA", "TSLA",
        "EBAY", "SBUX", "CELG", "GILD", "BIIB", "VRTX", "REGN", "CSCO", "TXN", "COST",
    ],
    2014: [
        "AAPL", "GOOG", "AMZN", "FB", "MSFT", "NFLX", "TSLA", "ADBE", "NVDA", "QCOM",
        "CELG", "GILD", "BIIB", "VRTX", "REGN", "CSCO", "TXN", "SBUX", "AVGO", "COST",
    ],
    2015: [
        "AAPL", "AMZN", "GOOG", "FB", "MSFT", "NFLX", "TSLA", "NVDA", "ADBE", "AVGO",
        "CELG", "GILD", "BIIB", "VRTX", "REGN", "CSCO", "TXN", "PYPL", "SBUX", "COST",
    ],
    2016: [
        "AAPL", "AMZN", "GOOG", "FB", "MSFT", "NFLX", "NVDA", "TSLA", "ADBE", "AVGO",
        "BIIB", "GILD", "VRTX", "REGN", "CSCO", "TXN", "PYPL", "COST", "INTC", "QCOM",
    ],
    2017: [
        "AAPL", "AMZN", "GOOG", "FB", "MSFT", "NVDA", "TSLA", "ADBE", "AVGO", "NFLX",
        "PYPL", "CSCO", "TXN", "INTU", "COST", "REGN", "VRTX", "GILD", "CSX", "SBUX",
    ],
    2018: [
        "AAPL", "AMZN", "MSFT", "GOOG", "FB", "NVDA", "ADBE", "AVGO", "NFLX", "TSLA",
        "PYPL", "INTU", "CSCO", "TXN", "COST", "REGN", "VRTX", "GILD", "SBUX", "AMGN",
    ],
    2019: [
        "AAPL", "MSFT", "AMZN", "GOOG", "FB", "NVDA", "ADBE", "NFLX", "TSLA", "AVGO",
        "INTU", "PYPL", "CSCO", "TXN", "COST", "REGN", "VRTX", "GILD", "SBUX", "AMD",
    ],
    2020: [
        "AAPL", "MSFT", "AMZN", "GOOG", "FB", "NVDA", "TSLA", "ADBE", "NFLX", "PYPL",
        "AVGO", "INTU", "CSCO", "TXN", "COST", "AMD", "REGN", "VRTX", "SBUX", "AMGN",
    ],
    2021: [
        "AAPL", "MSFT", "AMZN", "GOOG", "FB", "NVDA", "TSLA", "ADBE", "PYPL", "AVGO",
        "INTU", "NFLX", "CSCO", "TXN", "SNOW", "CRWD", "AMD", "COST", "SBUX", "AMGN",
    ],
    2022: [
        "AAPL", "MSFT", "GOOG", "AMZN", "NVDA", "TSLA", "META", "AVGO", "ADBE", "INTU",
        "AMD", "CSCO", "TXN", "PYPL", "COST", "SNOW", "CRWD", "DDOG", "REGN", "VRTX",
    ],
    2023: [
        "AAPL", "MSFT", "AMZN", "GOOG", "META", "NVDA", "TSLA", "AVGO", "ADBE", "AMD",
        "INTU", "CRWD", "DDOG", "SNOW", "COST", "PLTR", "VRTX", "REGN", "CSCO", "TXN",
    ],
    2024: [
        "MSFT", "NVDA", "AAPL", "AMZN", "GOOG", "META", "AVGO", "TSLA", "ADBE", "AMD",
        "INTU", "SNOW", "CRWD", "PLTR", "DDOG", "VRTX", "REGN", "COST", "CSCO", "TXN",
    ],
    2025: [
        "NVDA", "MSFT", "AAPL", "AMZN", "GOOG", "META", "AVGO", "TSLA", "ADBE", "AMD",
        "INTU", "PLTR", "CRWD", "DDOG", "SNOW", "VRTX", "REGN", "COST", "CSCO", "TXN",
    ],
}


# =====================================================================
# CLI
# =====================================================================
class _CLI:
    def __init__(self, verbose_saves: bool = False):
        self.width = shutil.get_terminal_size((100, 20)).columns
        self.color = self._ansi_ok()
        self.verbose_saves = verbose_saves

        def c(s): return s if self.color else ""

        self.RESET = c("\033[0m")
        self.DIM = c("\033[2m")
        self.BOLD = c("\033[1m")
        self.HDR = c("\033[38;5;45m")
        self.INFOC = c("\033[38;5;110m")
        self.SAVEC = c("\033[38;5;78m")
        self.WARNC = c("\033[38;5;214m")
        self.ERRC = c("\033[38;5;203m")
        self.GRAY = c("\033[38;5;245m")
        self.OK = c("\033[38;5;82m")
        self.BLUE = c("\033[38;5;69m")
        self._saved_paths = []
        self._last_progress_len = 0

    def progress(self, i: int, n: int, label: str = ""):
        """Simple in-place progress bar."""
        i = max(0, min(i, n))
        width = max(20, min(50, self.width - 40))
        frac = 0.0 if n == 0 else i / float(n)
        filled = int(round(width * frac))
        bar = "[" + "█" * filled + " " * (width - filled) + f"] {i}/{n}"
        if label:
            bar = f"{bar} {label}"
        sys.stdout.write("\r" + " " * (self._last_progress_len + 2))
        sys.stdout.write("\r" + bar)
        sys.stdout.flush()
        self._last_progress_len = len(bar)
        if i == n:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._last_progress_len = 0

    def _ansi_ok(self) -> bool:
        if sys.platform.startswith("win"):
            return bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") or sys.stdout.isatty())
        return sys.stdout.isatty()

    def line(self, ch: str = "─", color: Optional[str] = None):
        c = color or self.GRAY
        print(c + ch * self.width + self.RESET)

    def banner(self, title: str, subtitle: Optional[str] = None):
        self.line("═", self.HDR)
        print(f"{self.HDR}{self.BOLD}{title}{self.RESET}")
        if subtitle:
            print(f"{self.DIM}{subtitle}{self.RESET}")
        self.line("─", self.GRAY)

    def section(self, title: str):
        print()
        print(f"{self.BLUE}{self.BOLD}› {title}{self.RESET}")
        self.line("─", self.GRAY)

    def kv(self, key: str, value: str, kpad: int = 24, color: Optional[str] = None):
        k = (key + " ").ljust(kpad, ".")
        c = color or self.INFOC
        print(f"{self.DIM}{k}{self.RESET} {c}{value}{self.RESET}")

    def info(self, msg: str):
        print(f"{self.INFOC}INFO{self.RESET}  {msg}")

    def saved(self, path: str):
        self._saved_paths.append(path)
        if self.verbose_saves:
            print(f"{self.SAVEC}SAVED{self.RESET} {path}")

    def warn(self, msg: str):
        print(f"{self.WARNC}WARN {self.RESET} {msg}")

    def err(self, msg: str):
        print(f"{self.ERRC}ERROR{self.RESET} {msg}")

    def box(self, title: str, lines: List[Tuple[str, str]], kpad: int = 20):
        w = min(self.width, 100)
        top = "╭" + "─" * (w - 2) + "╮"
        bot = "╰" + "─" * (w - 2) + "╯"
        mid = "├" + "─" * (w - 2) + "┤"
        print(f"{self.GRAY}{top}{self.RESET}")
        t = f" {title} "
        print(f"{self.GRAY}│{self.RESET}{self.BOLD}{t:<{w - 2}}{self.RESET}{self.GRAY}│{self.RESET}")
        print(f"{self.GRAY}{mid}{self.RESET}")
        for k, v in lines:
            key = (k + " ").ljust(kpad, ".")
            out = f" {self.DIM}{key}{self.RESET} {self.BOLD}{v}{self.RESET} "
            print(f"{self.GRAY}│{self.RESET}{out:<{w - 2}}{self.GRAY}│{self.RESET}")
        print(f"{self.GRAY}{bot}{self.RESET}")

    def table_percent(self, df: pd.DataFrame, title: str, percent_cols=("CAGR", "MaxDD")):
        self.section(title)
        if df.empty:
            self.warn("Table is empty.")
            return
        disp = df.copy()
        for c in disp.columns:
            if c in percent_cols:
                disp[c] = disp[c] * 100.0
        cols = list(disp.columns)
        colw = [max(8, max(len(str(x)) for x in disp[c].astype(str).tolist() + [c])) for c in cols]
        header = "Year".ljust(12) + "  " + "  ".join(c.ljust(w) for c, w in zip(cols, colw))
        print(self.BOLD + header + self.RESET)

        def _fmt(c, v, w):
            if pd.isna(v): return "--".rjust(w)
            if c in percent_cols:
                return f"{v:.2f}".rjust(w)
            if c.lower() == "months":
                return f"{int(v):d}".rjust(w)
            if c.lower() == "sharpe":
                return f"{v:.2f}".rjust(w)
            return f"{v:.2f}".rjust(w)

        for idx, row in disp.iterrows():
            key = str(idx)
            vals = [_fmt(c, row[c], w) for c, w in zip(cols, colw)]
            print(key.ljust(12) + "  " + "  ".join(vals))

    # NEW: plain table wrapper so existing calls work
    def table(self, obj: pd.DataFrame | pd.Series, title: str, max_rows: int = 8):
        _print_table(obj, title, max_rows=max_rows)


cli = _CLI(verbose_saves=False)


# =====================================================================
# Pretty table for Series/DataFrame
# =====================================================================
def _print_table(obj, title: str, max_rows: int = 8):
    cli.section(title)
    if obj is None:
        cli.warn("No data.")
        return
    if isinstance(obj, pd.Series):
        show = obj.to_frame(name=str(obj.name) if obj.name is not None else "value")
    elif isinstance(obj, pd.DataFrame):
        show = obj.copy()
    else:
        cli.warn(f"Unsupported type for table: {type(obj)}")
        return
    if show.empty:
        cli.warn("No rows.")
        return
    if not isinstance(show.index, pd.RangeIndex):
        show = show.copy()
        show.insert(0, "_index", show.index.astype(str))
        show.index = range(len(show))
    if len(show) > max_rows:
        head = show.head(max_rows // 2)
        tail = show.tail(max_rows - len(head))
        show = pd.concat([head, tail], axis=0)
    # Pandas 3 / mixed-dtype safe rendering: never call len() on raw
    # column labels or cell values.  Convert every display token explicitly.
    raw_cols = list(show.columns)
    cols = [str(c) for c in raw_cols]
    colw = []
    for c, label in zip(raw_cols, cols):
        # ravel() also makes this robust if a vendor frame contains unusual
        # object values; all width calculations operate on strings only.
        vals = [str(v) for v in np.asarray(show[c], dtype=object).ravel().tolist()]
        widths = [8, len(label)] + [len(v) for v in vals]
        colw.append(max(widths))
    header = "  ".join(label.ljust(w) for label, w in zip(cols, colw))
    print(cli.BOLD + header + cli.RESET)
    for _, row in show.iterrows():
        line = "  ".join(str(row[c]).ljust(w) for c, w in zip(raw_cols, colw))
        print(line)


# =====================================================================
# IO helpers / Data loading
# =====================================================================
def _find_first_present(names: List[str], cols_lower: List[str]) -> Optional[str]:
    for n in names:
        if n in cols_lower:
            return n
    return None


def _standardize(symbol: str, df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        try:
            if symbol in df.columns.get_level_values(-1):
                df = df.xs(symbol, axis=1, level=-1)
            else:
                df.columns = ["_".join([str(x) for x in tup if str(x) != ""]) for x in df.columns]
        except Exception:
            df.columns = ["_".join([str(x) for x in tup if str(x) != ""]) for x in df.columns]
    out = df.reset_index()
    if "Date" not in out.columns:
        out.rename(columns={out.columns[0]: "Date"}, inplace=True)
    final_map = {}
    for c in out.columns:
        cl = str(c).lower()
        if cl == "date":
            final_map[c] = "Date"
        elif cl == "open":
            final_map[c] = "Open"
        elif cl == "high":
            final_map[c] = "High"
        elif cl == "low":
            final_map[c] = "Low"
        elif cl == "close":
            final_map[c] = "Close"
        elif cl in ("adj close", "adj_close", "adjclose"):
            final_map[c] = "AdjClose"
        elif cl == "volume":
            final_map[c] = "Volume"
    out = out.rename(columns=final_map)
    needed = ["Date", "Open", "High", "Low", "Close", "Volume"]
    keep = [c for c in ["Date", "Open", "High", "Low", "Close", "AdjClose", "Volume"] if c in out.columns]
    if not set(needed).issubset(set(out.columns)):
        return None
    out = out[keep].copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce", utc=False)
    out = out.dropna(subset=["Date"]).drop_duplicates(subset=["Date"]).sort_values("Date")
    return out


def _download_daily(symbol: str, data_dir: str, start: Optional[str] = None, end: Optional[str] = None) -> Optional[
    str]:
    if not ALLOW_DOWNLOAD or DOWNLOAD_SOURCE.lower() != "yahoo" or not _YF_OK:
        return None
    try:
        s = pd.to_datetime(start).strftime("%Y-%m-%d") if start else "1990-01-01"
        e = pd.to_datetime(end).strftime("%Y-%m-%d") if end else pd.Timestamp.today().strftime("%Y-%m-%d")
        df = yf.download(symbol, start=s, end=e, progress=False, auto_adjust=False, threads=False, group_by="column")
        out = _standardize(symbol, df)
        if out is None or out.empty:
            tkr = yf.Ticker(symbol)
            df2 = tkr.history(start=s, end=e, interval="1d", auto_adjust=False)
            out = _standardize(symbol, df2)
        if out is None or out.empty:
            cli.warn(f"{symbol}: Yahoo returned no usable rows.")
            return None
        os.makedirs(data_dir, exist_ok=True)
        out_path = os.path.join(data_dir, f"{symbol.upper()}.csv")
        out.to_csv(out_path, index=False)
        cli.saved(out_path)
        return out_path
    except Exception as ex:
        cli.warn(f"{symbol}: download failed — {ex}")
        return None


def _map_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = _strip_vendor_metadata_rows(df)
    cols_lower = [c.lower().strip() for c in df.columns]
    colmap = {}
    dcol = _find_first_present(POSSIBLE_DATE_COLS, cols_lower)
    if dcol is None:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={df.columns[0]: "Date"})
            dcol = "date"
            cols_lower = [c.lower().strip() for c in df.columns]
        else:
            dcol = df.columns[0].lower()
    for std, aliases in COL_ALIASES.items():
        if std == "adjclose":
            continue
        c = _find_first_present(aliases, cols_lower)
        if c is None:
            raise ValueError(f"Missing required column for '{std}' (aliases: {aliases})")
        colmap[df.columns[cols_lower.index(c)]] = std.capitalize()
    adjc = _find_first_present(COL_ALIASES.get("adjclose", []), cols_lower)
    if adjc is not None:
        colmap[df.columns[cols_lower.index(adjc)]] = "AdjClose"
    if dcol in cols_lower:
        colmap[df.columns[cols_lower.index(dcol)]] = "Date"
    out = df.rename(columns=colmap)
    needed = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if not set(needed).issubset(out.columns):
        raise ValueError("After mapping, required OHLCV columns missing.")
    keep = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if "AdjClose" in out.columns:
        keep.insert(5, "AdjClose")
    return out[keep]


def _strip_vendor_metadata_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Remove leading non-data rows that some CSV vendors include."""
    if df is None or df.empty:
        return df

    cleaned = df.dropna(how="all").copy()
    dcol = _infer_date_col(cleaned)
    if dcol is None:
        return cleaned

    parsed = pd.to_datetime(cleaned[dcol], errors="coerce", utc=True)
    first_valid = parsed.first_valid_index()
    if first_valid is None:
        return cleaned

    cleaned = cleaned.loc[first_valid:]
    cleaned[dcol] = parsed.loc[first_valid:].dt.tz_localize(None)
    return cleaned


def _apply_adjclose_backadjust(df: pd.DataFrame) -> pd.DataFrame:
    if "AdjClose" not in df.columns:
        return df
    fac = (df["AdjClose"] / df["Close"]).replace([np.inf, -np.inf], np.nan)
    fac = fac.ffill().bfill().replace(0.0, np.nan).fillna(1.0)
    for c in ["Open", "High", "Low", "Close"]:
        df[c] = df[c] * fac
    if ADJUST_VOLUME:
        safe = fac.replace(0.0, np.nan).fillna(1.0)
        df["Volume"] = df["Volume"] / safe
    return df


def _csv_candidates(symbol: str, root: Optional[str]) -> List[str]:
    """Return likely CSV paths for a symbol inside a local data root."""
    if not root:
        return []
    symbol = str(symbol).strip()
    upper = symbol.upper()
    lower = symbol.lower()
    names = list(dict.fromkeys([f"{symbol}.csv", f"{upper}.csv", f"{lower}.csv"]))
    out: List[str] = []
    for name in names:
        out.extend([
            os.path.join(root, name),
            os.path.join(root, upper, name),
            os.path.join(root, lower, name),
            os.path.join(root, "daily", name),
            os.path.join(root, "csv", name),
        ])
    return list(dict.fromkeys(out))


def _find_local_csv(symbol: str, *roots: Optional[str]) -> Optional[str]:
    """Find a symbol CSV, including a case-insensitive fallback for Tiingo dumps."""
    for root in roots:
        if not root:
            continue
        for candidate in _csv_candidates(symbol, root):
            if os.path.isfile(candidate):
                return candidate

        # Tiingo/local archives sometimes preserve ticker filename casing differently.
        if os.path.isdir(root):
            target = f"{str(symbol).strip()}.csv".lower()
            try:
                for name in os.listdir(root):
                    if name.lower() == target:
                        candidate = os.path.join(root, name)
                        if os.path.isfile(candidate):
                            return candidate
            except OSError:
                pass
    return None


def _quick_file_date_span(path: str) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    """Cheap vendor-agnostic date-span scan used only to choose among local sources."""
    ap = os.path.abspath(path)
    if ap in _SOURCE_SPAN_CACHE:
        return _SOURCE_SPAN_CACHE[ap]
    lo: Optional[pd.Timestamp] = None
    hi: Optional[pd.Timestamp] = None
    # Handles YYYY-MM-DD, YYYY/MM/DD, and compact YYYYMMDD inside Tiingo/Yahoo CSVs.
    pat = re.compile(r"(?<!\\d)((?:19|20)\\d{2})[-/]?(\\d{2})[-/]?(\\d{2})(?!\\d)")
    try:
        with open(ap, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                for y, m, d in pat.findall(line):
                    try:
                        ts = pd.Timestamp(year=int(y), month=int(m), day=int(d))
                    except Exception:
                        continue
                    lo = ts if lo is None or ts < lo else lo
                    hi = ts if hi is None or ts > hi else hi
    except OSError:
        pass
    _SOURCE_SPAN_CACHE[ap] = (lo, hi)
    return lo, hi


def _local_source_score(path: str, *, required_years: Optional[List[int]] = None,
                        start_hint: Optional[str] = None, end_hint: Optional[str] = None) -> Tuple[float, float, int]:
    lo, hi = _quick_file_date_span(path)
    if lo is None or hi is None:
        return (-1.0, -1.0, int(os.path.getsize(path)) if os.path.exists(path) else 0)
    if required_years:
        covered = sum(int(lo.year <= int(y) <= hi.year) for y in required_years)
        primary = covered / max(len(required_years), 1)
    elif start_hint or end_hint:
        st = pd.to_datetime(start_hint) if start_hint else lo
        en = pd.to_datetime(end_hint) if end_hint else hi
        overlap_start = max(lo, st)
        overlap_end = min(hi, en)
        primary = max(0.0, float((overlap_end - overlap_start).days + 1))
    else:
        primary = max(0.0, float((hi - lo).days + 1))
    span_days = max(0.0, float((hi - lo).days + 1))
    size = int(os.path.getsize(path)) if os.path.exists(path) else 0
    return (primary, span_days, size)


def _resolve_daily_source(symbol: str, data_dir: str,
                          start_hint: Optional[str] = None,
                          end_hint: Optional[str] = None,
                          required_years: Optional[List[int]] = None) -> Tuple[Optional[str], str]:
    """Choose the best local history rather than blindly preferring a short primary file.

    Candidate set includes primary/Tiingo files for the requested ticker and for a
    safe ticker-continuity alias (e.g. FB→META). This prevents a tiny current CSV
    from shadowing a much more complete delisted-history Tiingo file.
    """
    sym = str(symbol).upper()
    aliases = [sym]
    alias = TICKER_ALIAS.get(sym)
    if alias and alias.upper() not in aliases:
        aliases.append(alias.upper())
    candidates: List[Tuple[str, str]] = []
    for i, ticker in enumerate(aliases):
        p = _find_local_csv(ticker, data_dir)
        if p:
            label = "primary_local" if ticker == sym else f"alias_primary:{sym}->{ticker}"
            candidates.append((p, label))
        t = _find_local_csv(ticker, TIINGO_MISSING_DIR)
        if t:
            label = "tiingo_fallback" if ticker == sym else f"alias_tiingo:{sym}->{ticker}"
            candidates.append((t, label))
    if not candidates:
        return None, "missing_local"
    # Deduplicate same physical path while preserving first label.
    dedup: Dict[str, str] = {}
    for p, label in candidates:
        dedup.setdefault(os.path.abspath(p), label)
    scored = []
    for p, label in dedup.items():
        score = _local_source_score(
            p, required_years=required_years, start_hint=start_hint, end_hint=end_hint
        )
        # Prefer primary on an exact score tie; otherwise choose broader coverage.
        tie_pref = 1 if label.startswith("primary_local") else 0
        scored.append((score, tie_pref, p, label))
    scored.sort(key=lambda x: (x[0][0], x[0][1], x[0][2], x[1]), reverse=True)
    _, _, path, label = scored[0]
    return path, label


def _load_daily_csv(symbol: str, data_dir: str, start_hint: Optional[str] = None,
                    end_hint: Optional[str] = None,
                    required_years: Optional[List[int]] = None) -> pd.DataFrame:
    # Data-source precedence is intentional:
    #   1) normal project CSVs
    #   2) local Tiingo archive for delisted / Yahoo-missing tickers
    #   3) Yahoo download as a last resort
    path, source_kind = _resolve_daily_source(
        symbol, data_dir, start_hint=start_hint, end_hint=end_hint, required_years=required_years
    )
    # Routine local-source selection is intentionally silent. Source provenance is
    # preserved by the canonical data-integrity audit/manifest, while warnings and
    # genuine load failures remain visible. This avoids repeated alias/Tiingo log spam
    # inside parameter grids and walk-forward loops (e.g. FB->META, YHOO->AABA).

    if path is None:
        fetched = _download_daily(symbol, data_dir, start_hint, end_hint)
        if fetched is not None and os.path.isfile(fetched):
            path = fetched
            source_kind = "yahoo_download"
    if path is None:
        searched = [data_dir]
        if TIINGO_MISSING_DIR:
            searched.append(TIINGO_MISSING_DIR)
        raise FileNotFoundError(
            f"{symbol}: CSV not found in local sources ({', '.join(searched)}) and Yahoo download failed/disabled."
        )

    abspath = os.path.abspath(path)
    try:
        mtime = float(os.path.getmtime(abspath))
    except OSError:
        mtime = -1.0
    cache_key = (str(symbol).upper(), abspath, mtime)
    cached = _DAILY_DATA_CACHE.get(cache_key)
    if cached is not None:
        return cached.copy()

    df = pd.read_csv(path, engine="python")
    if df.empty:
        fetched = _download_daily(symbol, data_dir, start_hint, end_hint) if ALLOW_DOWNLOAD else None
        if fetched and os.path.isfile(fetched):
            path = fetched
            source_kind = "yahoo_download"
            df = pd.read_csv(fetched, engine="python")
        if df.empty:
            raise ValueError(f"{symbol}: empty CSV at {path}")
    df = _map_columns(df)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_localize(None)
    df = df.dropna(subset=["Date"]).drop_duplicates(subset=["Date"]).sort_values("Date").set_index("Date")
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if ADJUST_BY_ADJCLOSE and "AdjClose" in df.columns:
        df = _apply_adjclose_backadjust(df)

    # Cache only the normalized immutable source frame.
    try:
        final_path = os.path.abspath(path)
        final_mtime = float(os.path.getmtime(final_path))
        final_key = (str(symbol).upper(), final_path, final_mtime)
        _DAILY_DATA_CACHE[final_key] = df.copy()
    except OSError:
        pass
    return df.copy()


def _required_trade_years(yearly_map: Dict[int, List[str]], symbol: str,
                          start: Optional[str], end: Optional[str]) -> List[int]:
    """Return trade years that genuinely overlap the configured sample window.

    The backtest window is interpreted as [start, end): an end such as 2026-01-01
    means the sample contains data through 2025-12-31, not the 2026 trading year.
    The previous audit compared only calendar-year integers and therefore demanded
    a full 2026 history even when the run stopped at the first instant of 2026.
    """
    sym = str(symbol).upper()
    start_ts = pd.to_datetime(start) if start else None
    end_ts = pd.to_datetime(end) if end else None
    years = []
    for y, syms in yearly_map.items():
        trade_year = int(y) + 1
        year_start = pd.Timestamp(year=trade_year, month=1, day=1)
        year_end_exclusive = pd.Timestamp(year=trade_year + 1, month=1, day=1)
        overlaps_start = start_ts is None or year_end_exclusive > start_ts
        overlaps_end = end_ts is None or year_start < end_ts
        if overlaps_start and overlaps_end and sym in {str(x).upper() for x in syms}:
            years.append(trade_year)
    return sorted(set(years))


def run_data_integrity_audit(yearly_map: Dict[int, List[str]], data_dir: str,
                             start: Optional[str], end: Optional[str], *,
                             strict: bool = False, min_coverage: float = 0.95) -> pd.DataFrame:
    """Audit local point-in-time inputs and freeze hashes for reproducibility.

    The audit intentionally does not call Yahoo. In strict/canonical mode, missing
    local sources or insufficient symbol-year coverage stop the run instead of
    being silently skipped. This does not claim survivorship bias is eliminated;
    it exposes the remaining gaps.
    """
    all_syms = sorted({str(s).upper() for v in yearly_map.values() for s in v})
    rows: List[Dict[str, Any]] = []
    required_pairs = 0
    covered_pairs = 0
    for sym in all_syms:
        req_years = _required_trade_years(yearly_map, sym, start, end)
        required_pairs += len(req_years)
        path, source = _resolve_daily_source(sym, data_dir, start_hint=start, end_hint=end, required_years=req_years)
        first = last = pd.NaT
        nrows = 0
        available_years: Set[int] = set()
        sha = ""
        status = "OK"
        error = ""
        if path is None:
            status = "MISSING"
            error = "No local CSV in primary or Tiingo fallback"
        else:
            try:
                df = _load_daily_csv(sym, data_dir, start_hint=start, end_hint=end, required_years=req_years)
                nrows = int(len(df))
                if nrows:
                    first, last = df.index.min(), df.index.max()
                    available_years = set(int(y) for y in pd.Index(df.index.year).unique())
                sha = _sha256_file(path)
            except Exception as exc:
                status = "ERROR"
                error = str(exc)
        covered = [y for y in req_years if y in available_years]
        covered_pairs += len(covered)
        coverage = (len(covered) / len(req_years)) if req_years else 1.0
        if status == "OK" and coverage < 1.0:
            status = "PARTIAL"
        rows.append({
            "Symbol": sym, "Source": source, "Path": os.path.abspath(path) if path else "",
            "SHA256": sha, "Rows": nrows,
            "FirstDate": first, "LastDate": last,
            "RequiredTradeYears": ",".join(map(str, req_years)),
            "CoveredTradeYears": ",".join(map(str, covered)),
            "CoveragePct": 100.0 * coverage, "Status": status, "Error": error,
        })
    audit = pd.DataFrame(rows)
    os.makedirs(DIAG_DIR, exist_ok=True)
    audit_path = os.path.join(DIAG_DIR, "data_integrity_audit.csv")
    audit.to_csv(audit_path, index=False)
    cli.saved(audit_path)

    pair_coverage = covered_pairs / required_pairs if required_pairs else 1.0
    manifest = {
        "generated_utc": datetime.utcnow().isoformat() + "Z",
        "project_root": PROJECT_ROOT,
        "primary_data_dir": data_dir,
        "tiingo_fallback_dir": TIINGO_MISSING_DIR,
        "allow_download": bool(ALLOW_DOWNLOAD),
        "script_sha256": _sha256_file(os.path.abspath(__file__)),
        "required_symbol_year_pairs": int(required_pairs),
        "covered_symbol_year_pairs": int(covered_pairs),
        "coverage_fraction": float(pair_coverage),
        "files": audit[["Symbol", "Source", "Path", "SHA256", "Status"]].to_dict("records"),
    }
    manifest_path = os.path.join(DIAG_DIR, "frozen_input_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    cli.saved(manifest_path)

    bad = audit[audit["Status"].isin(["MISSING", "ERROR"])]
    partial = audit[audit["Status"] == "PARTIAL"]
    cli.box("DATA INTEGRITY AUDIT", [
        ("Symbols audited", str(len(audit))),
        ("Symbol-year coverage", f"{pair_coverage * 100:.2f}%"),
        ("Missing/error symbols", str(len(bad))),
        ("Partial-coverage symbols", str(len(partial))),
        ("Yahoo allowed", "YES" if ALLOW_DOWNLOAD else "NO (frozen)"),
        ("Script SHA-256", manifest["script_sha256"][:16] + "…"),
    ])
    if strict and (pair_coverage < float(min_coverage) or not bad.empty):
        examples = ", ".join(bad["Symbol"].head(8).tolist())
        raise RuntimeError(
            f"Canonical data audit failed: coverage={pair_coverage:.2%}, "
            f"minimum={min_coverage:.2%}, missing/error={len(bad)}"
            + (f" ({examples})" if examples else "")
            + ". Fix local data or lower --data_min_coverage deliberately. "
            + ("Partial examples: " + "; ".join(
                f"{r.Symbol} missing {sorted(set(map(int, str(r.RequiredTradeYears).split(','))) - set(map(int, str(r.CoveredTradeYears).split(','))) if str(r.CoveredTradeYears) else set())}"
                for _, r in partial.head(6).iterrows()
            ) if not partial.empty else "")
        )
    return audit


def ensure_dirs():
    """Create output folders under PROJECT_ROOT, never relative to the shell CWD."""
    try:
        for path in (CSVA_DIR, SIGNALS_DIR, OOS_DIR, DIAG_DIR, MC_DIR, WGY_DIR, FACTOR_DIR):
            os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"Could not create Stockers output directories under {PROJECT_ROOT!r}. "
            f"Set STOCKERS_PROJECT_ROOT to a writable project folder. Original error: {exc}"
        ) from exc


def write_yearly_top20_csv(yearly_map: Dict[int, List[str]],
                           out_path: str = os.path.join(CSVA_DIR, "yearly_top20.csv")) -> None:
    rows = []
    for y, lst in sorted(yearly_map.items()):
        for rank, t in enumerate(lst, start=1):
            rows.append({"Year": y, "Ticker": t.upper(), "Rank": rank})
    pd.DataFrame(rows).to_csv(out_path, index=False)
    cli.saved(out_path)


# =====================================================================
# Strategy
# =====================================================================
@dataclass
class StrategyParams:
    ema_period: int = 200
    min_sell_gain_pct: float = 30.0
    warmup_days: int = 100
    trading_start: Optional[pd.Timestamp] = None


class UltimateSimpleSymbol:
    def __init__(self, symbol: str, daily_df: pd.DataFrame, params: StrategyParams, *,
                 eligible_years: Optional[Set[int]] = None):
        self.symbol = symbol.upper()
        self.d = daily_df
        self.p = params
        self.eligible_years: Set[int] = set(eligible_years or [])
        self.monthly: Optional[pd.DataFrame] = None
        self.signals: Optional[pd.DataFrame] = None
        self.monthly_returns: Optional[pd.Series] = None
        self.position: Optional[pd.Series] = None

    @staticmethod
    def _vol_delta(d: pd.DataFrame) -> pd.Series:
        up = (d["Close"] > d["Open"]).astype(int)
        dn = (d["Close"] < d["Open"]).astype(int)
        return (up - dn) * d["Volume"]

    def _to_monthly(self) -> None:
        d = self.d.copy()
        d["VolDelta"] = self._vol_delta(d)
        d["EMA"] = d["Close"].ewm(span=self.p.ema_period, adjust=False).mean()
        d["_obs"] = 1
        d["_cumobs"] = d["_obs"].cumsum()
        agg = {
            "Open": "first", "High": "max", "Low": "min", "Close": "last",
            "Volume": "sum", "VolDelta": "sum", "EMA": "last", "_cumobs": "last"
        }
        # Unified to month-end frequency "ME"
        m = d.resample("ME").agg(agg).dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        m["CandleColor"] = np.where(m["Close"] > m["Open"], "green", "red")
        m["DeltaColor"] = np.where(m["VolDelta"] > 0, "green", "red")
        m["AboveEMA"] = m["Close"] > m["EMA"]
        m["CandleGainPct"] = (m["Close"] - m["Open"]) / m["Open"] * 100.0

        if self.p.trading_start is not None:
            gate = (m.index >= pd.Timestamp(self.p.trading_start).to_period("M").to_timestamp("M"))
        else:
            gate = pd.Series(True, index=m.index)

        m["Tradable"] = gate & (m["_cumobs"] >= self.p.warmup_days)
        yrs = m.index.year.astype(int)
        if self.eligible_years:
            m["Eligible"] = yrs.isin(list(self.eligible_years))
        else:
            m["Eligible"] = True
        self.monthly = m

    def build_signals(self) -> None:
        """Build trading signals with BUG FIX for pending_buy edge case."""
        if self.monthly is None or self.monthly.empty:
            self.signals = pd.DataFrame()
            return
        m = self.monthly.copy()
        n = len(m)
        idx = m.index
        years = idx.year.astype(int).values
        if self.eligible_years:
            elig_exec = np.array([(years[i] in self.eligible_years) for i in range(n)], dtype=bool)
        else:
            elig_exec = np.array([(i + 1) < n for i in range(n)], dtype=bool)

        sig = pd.Series(index=idx, data="HOLD", dtype=object)
        st = pd.Series(index=idx, data="", dtype=object)
        pending_buy = False

        for i in range(n):
            if i == 0 or not m["Tradable"].iloc[i]:
                continue
            prev = m.iloc[i - 1]
            candle = prev["CandleColor"]
            delta = prev["DeltaColor"]
            above = prev["AboveEMA"]
            gain = prev["CandleGainPct"]
            current_above = m["AboveEMA"].iloc[i]

            # Pending buy confirmation
            if pending_buy and current_above:
                if elig_exec[i]:
                    sig.iloc[i] = "BUY"
                    st.iloc[i] = "EMA Cross Buy"
                    pending_buy = False
                continue

            # Divergence buy signal
            if (candle == "red") and (delta == "green"):
                if above:
                    if elig_exec[i]:
                        sig.iloc[i] = "BUY"
                        st.iloc[i] = "Divergence Buy"
                        pending_buy = False  # BUG FIX: Reset pending_buy on direct buy
                else:
                    pending_buy = True
                continue

            # BUG FIX: Only check sell if we didn't just generate a BUY
            if sig.iloc[i] != "BUY":
                # Divergence sell signal
                if (prev["CandleColor"] == "green") and (prev["DeltaColor"] == "red") and (
                        gain >= self.p.min_sell_gain_pct):
                    sig.iloc[i] = "SELL"
                    st.iloc[i] = "Divergence Sell"
                    pending_buy = False  # BUG FIX: Reset pending_buy on sell

        out = m.copy()
        out["Signal"] = sig
        out["SignalType"] = st
        self.signals = out

    def compute_position_and_returns(self) -> Tuple[pd.Series, pd.Series]:
        if self.signals is None or self.signals.empty:
            empty = pd.Series(dtype=float, name=self.symbol)
            return empty, empty
        m = self.signals.copy()
        idx = m.index
        entry_at_i = (m["Signal"] == "BUY").shift(1, fill_value=False)
        exit_at_i = ((m["Signal"] == "SELL").shift(1, fill_value=False) | (~m["AboveEMA"]).shift(1, fill_value=False))
        position = pd.Series(0, index=idx, dtype=int)
        in_pos = 0
        for i in range(len(idx)):
            if exit_at_i.iloc[i] and in_pos == 1:
                in_pos = 0
            if entry_at_i.iloc[i] and m["Tradable"].iloc[i]:
                in_pos = 1
            position.iloc[i] = in_pos
        next_open = m["Open"].shift(-1)
        o2o = (next_open / m["Open"] - 1.0).fillna(0.0)
        mret = (position * o2o).astype(float)
        mret.name = self.symbol
        position.name = self.symbol
        self.position = position
        self.monthly_returns = mret
        return position, mret

    def export_detailed_trade_log(self) -> pd.DataFrame:
        """
        Clean trade log semantics:
        - For OPEN trades, do NOT set an exit_date. Use columns: exit_date=NaT, exit_price=NaN, exit_reason='OPEN', as_of=last_index
        - One row per completed/ongoing trade. No duplicates.
        """
        if self.position is None or self.signals is None:
            return pd.DataFrame()
        m = self.signals.copy()
        position = self.position
        idx = m.index
        trades = []
        trade_id = 1
        entry_date = None
        entry_price = None
        entry_signal = None
        for i in range(len(idx)):
            curr_pos = position.iloc[i]
            prev_pos = position.iloc[i - 1] if i > 0 else 0
            if curr_pos == 1 and prev_pos == 0:
                entry_date = idx[i]
                entry_price = m["Open"].iloc[i]
                if i > 0 and m["Signal"].iloc[i - 1] == "BUY":
                    entry_signal = m["SignalType"].iloc[i - 1]
                else:
                    entry_signal = "Unknown"
            elif curr_pos == 0 and prev_pos == 1:
                exit_date = idx[i]
                exit_price = m["Open"].iloc[i]
                if i > 0:
                    if m["Signal"].iloc[i - 1] == "SELL":
                        exit_reason = "Divergence Sell"
                    elif not m["AboveEMA"].iloc[i - 1]:
                        exit_reason = "EMA Hard Exit"
                    else:
                        exit_reason = "Unknown"
                else:
                    exit_reason = "Unknown"
                pnl_pct = (exit_price / entry_price - 1.0) * 100.0 if entry_price and entry_price > 0 else 0.0
                hold_months = (exit_date.year - entry_date.year) * 12 + (
                        exit_date.month - entry_date.month) if entry_date else 0
                trades.append({
                    "trade_id": trade_id, "ticker": self.symbol,
                    "entry_date": entry_date, "exit_date": exit_date,
                    "entry_price": entry_price, "exit_price": exit_price,
                    "holding_months": hold_months, "pnl_pct": pnl_pct,
                    "entry_signal": entry_signal, "exit_reason": exit_reason,
                    "entry_ema": m["EMA"].iloc[i - 1] if i > 0 else np.nan,
                    "exit_ema": m["EMA"].iloc[i],
                    "as_of": pd.NaT,
                    "open_flag": 0,
                })
                trade_id += 1
                entry_date = None
                entry_price = None
                entry_signal = None
        # Open trade roll-forward
        if position.iloc[-1] == 1 and entry_date is not None:
            last_ts = idx[-1]
            trades.append({
                "trade_id": trade_id, "ticker": self.symbol,
                "entry_date": entry_date, "exit_date": pd.NaT,
                "entry_price": entry_price, "exit_price": np.nan,
                "holding_months": (last_ts.year - entry_date.year) * 12 + (last_ts.month - entry_date.month),
                "pnl_pct": (m["Close"].iloc[-1] / entry_price - 1.0) * 100.0 if entry_price > 0 else 0.0,
                "entry_signal": entry_signal, "exit_reason": "OPEN",
                "entry_ema": m["EMA"].iloc[-1], "exit_ema": np.nan,
                "as_of": last_ts,
                "open_flag": 1,
            })
        df = pd.DataFrame(trades)
        if not df.empty:
            df = df.sort_values(["entry_date", "ticker", "trade_id"]).drop_duplicates(
                subset=["ticker", "entry_date", "exit_date"], keep="last"
            ).reset_index(drop=True)
        return df

    def save_outputs(self) -> None:
        if self.signals is not None and not self.signals.empty:
            sig_path = os.path.join(SIGNALS_DIR, f"{self.symbol}_signals.csv")
            self.signals.to_csv(sig_path)
            cli.saved(sig_path)
        if self.monthly_returns is not None and not self.monthly_returns.empty:
            ret_path = os.path.join(SIGNALS_DIR, f"{self.symbol}_monthly_returns.csv")
            self.monthly_returns.to_csv(ret_path, header=True)
            cli.saved(ret_path)
        if self.position is not None and not self.position.empty:
            pos_path = os.path.join(SIGNALS_DIR, f"{self.symbol}_positions.csv")
            self.position.to_csv(pos_path, header=True)
            cli.saved(pos_path)
        trade_log = self.export_detailed_trade_log()
        if not trade_log.empty:
            trade_path = os.path.join(SIGNALS_DIR, f"{self.symbol}_trades.csv")
            trade_log.to_csv(trade_path, index=False)
            cli.saved(trade_path)


# =====================================================================
# Portfolio & Risk
# =====================================================================
def build_portfolio_returns_from_csv(yearly_map: Dict[int, List[str]], data_dir: str, start: Optional[str] = None,
                                     end: Optional[str] = None,
                                     ema_period: int = 200, min_sell_gain_pct: float = 30.0, min_active_names: int = 0,
                                     preload_years: int = 1, *, verbose: bool = True):
    trading_start = pd.to_datetime(start) if start else None
    preload_start = (pd.Timestamp(start) - pd.DateOffset(years=preload_years)).strftime("%Y-%m-%d") if start else None
    warmup_days = max(40, ema_period // 2)
    params = StrategyParams(ema_period=ema_period, min_sell_gain_pct=min_sell_gain_pct,
                            warmup_days=warmup_days, trading_start=trading_start)
    all_syms = sorted({s.upper() for v in yearly_map.values() for s in v})
    if verbose: cli.info(f"Universe size (unique symbols): {len(all_syms)}")
    symbol_years = {}
    for y, syms in yearly_map.items():
        trade_year = y + 1  # <-- CRITICAL: Enforces 1-year lag.
        for s in syms:
            symbol_years.setdefault(s.upper(), set()).add(int(trade_year))
    all_positions: Dict[str, pd.Series] = {}
    all_returns: Dict[str, pd.Series] = {}
    for k, s in enumerate(all_syms, start=1):
        if verbose: cli.progress(k, len(all_syms), label="Symbols")
        try:
            sym_try = s
            try:
                daily = _load_daily_csv(
                    sym_try, data_dir, start_hint=preload_start, end_hint=end,
                    required_years=sorted(symbol_years.get(s, set()))
                )
            except Exception:
                alias = TICKER_ALIAS.get(s, s)
                if alias != s:
                    if verbose: cli.warn(f"{s}: trying alias {alias}")
                    sym_try = alias
                    daily = _load_daily_csv(
                        sym_try, data_dir, start_hint=preload_start, end_hint=end,
                        required_years=sorted(symbol_years.get(s, set()))
                    )
                else:
                    raise
            if end:
                daily = daily[daily.index <= pd.to_datetime(end)]
            if daily is None or daily.empty:
                if verbose: cli.warn(f"{s}: no rows in requested window")
                continue
            engine = UltimateSimpleSymbol(s, daily, params, eligible_years=symbol_years.get(s, set()))
            engine._to_monthly()
            if engine.monthly is None or engine.monthly.empty or not isinstance(engine.monthly.index, pd.DatetimeIndex):
                if verbose: cli.warn(f"{s}: monthly index invalid/empty; skipping")
                continue
            engine.build_signals()
            pos, ret = engine.compute_position_and_returns()
            if verbose: engine.save_outputs()  # Only save outputs on main runs
            if start:
                mask = ret.index >= pd.to_datetime(start).to_period("M").to_timestamp("M")
                ret = ret[mask]
                pos = pos[mask]
            if not ret.empty:
                all_positions[s] = pos
                all_returns[s] = ret
            else:
                if verbose: cli.warn(f"{s}: produced empty return stream after filters")
        except Exception as e:
            DATA_QUALITY_EVENTS.append({"Symbol": s, "Stage": "portfolio_build", "Error": str(e)})
            if STRICT_DATA_MODE:
                raise RuntimeError(f"Strict data mode: {s} failed during portfolio build — {e}") from e
            if verbose: cli.warn(f"{s}: skipped — {e}")
    if not all_returns:
        raise RuntimeError("No symbols produced return streams.")
    ret_df = pd.DataFrame(all_returns).sort_index()
    pos_df = pd.DataFrame(all_positions).sort_index()
    if ret_df.empty: raise RuntimeError("Aligned return frame is empty.")
    active_sets_by_month: List[set] = []
    port_ret: List[float] = []
    for dt in ret_df.index:
        in_position = [ticker for ticker in pos_df.columns if
                       pd.notna(pos_df.loc[dt, ticker]) and pos_df.loc[dt, ticker] == 1]
        active_sets_by_month.append(set(in_position))
        if len(in_position) < min_active_names:
            port_ret.append(0.0)
        else:
            row = ret_df.loc[dt, in_position].dropna()
            port_ret.append(0.0 if row.empty else row.mean())
    port_series = pd.Series(port_ret, index=ret_df.index, name="PortfolioRet")

    if verbose:  # Only save outputs on main runs
        pos_path = os.path.join(CSVA_DIR, "portfolio_position_matrix.csv")
        pd.DataFrame(all_positions).to_csv(pos_path)
        cli.saved(pos_path)
        pos_counts = pd.DataFrame(all_positions).sum(axis=1)
        pos_counts.name = "num_positions"
        pos_counts_path = os.path.join(CSVA_DIR, "monthly_position_counts.csv")
        pos_counts.to_csv(pos_counts_path, header=True)
        cli.saved(pos_counts_path)

    return port_series, ret_df, active_sets_by_month


def consolidate_all_trades(data_dir: str = SIGNALS_DIR) -> pd.DataFrame:
    all_trades = []
    for file in os.listdir(data_dir):
        if file.endswith("_trades.csv"):
            path = os.path.join(data_dir, file)
            df = pd.read_csv(path)
            if not df.empty:
                all_trades.append(df)
    if not all_trades:
        cli.warn("No trade logs found to consolidate")
        return pd.DataFrame()
    master = pd.concat(all_trades, ignore_index=True).sort_values(["entry_date", "ticker"]).reset_index(drop=True)
    master["entry_date"] = pd.to_datetime(master["entry_date"], errors="coerce")
    master["exit_date"] = pd.to_datetime(master["exit_date"], errors="coerce")
    # drop duplicates defensively
    master = master.drop_duplicates(subset=["ticker", "entry_date", "exit_date"], keep="last").reset_index(drop=True)
    master["trade_id"] = range(1, len(master) + 1)
    master["win"] = (master["pnl_pct"] > 0).astype(int)
    return master


# =====================================================================
# Scaling & Costs
# =====================================================================
def _apply_speed_caps(prev_scale: float, raw_scale: float, up_speed_cap: float, down_speed_cap: float) -> float:
    if np.isnan(prev_scale):
        return raw_scale
    delta = raw_scale - prev_scale
    if delta > 0:
        delta = min(delta, up_speed_cap)
    else:
        delta = max(delta, -down_speed_cap)
    return prev_scale + delta


def apply_vol_target_and_dd_throttle(
        port_rets: pd.Series,
        target_vol_yr: float = 0.30,
        lookback_m: int = 12,
        clamp: Tuple[float, float] = (0.5, 1.0),
        dd_cuts: Tuple[Tuple[float, float], ...] = ((-0.22, 0.6), (-0.32, 0.3)),
        winsor: Optional[float] = 0.20,
        *,
        de_risk_only: bool = True,
        min_scale: float = 0.30,
        up_speed_cap: float = 0.20,
        down_speed_cap: float = 0.35,
        use_vol_target: bool = True,
        use_dd_throttle: bool = True,
        verbose: bool = True,
) -> pd.Series:
    r = port_rets.copy().sort_index()
    if winsor is not None:
        # absolute clip, not percentile winsorization
        r = r.clip(lower=-winsor, upper=winsor)
    if use_vol_target:
        target_m = target_vol_yr / np.sqrt(12)
        realized = r.rolling(lookback_m, min_periods=max(3, lookback_m // 2)).std()
        base_raw = (target_m / realized).shift(1)
        base_clamped = base_raw.clip(lower=clamp[0], upper=clamp[1])
        base_speed = pd.Series(index=r.index, dtype=float)
        prev = np.nan
        for t in r.index:
            s = base_clamped.loc[t]
            s = 1.0 if np.isnan(s) else s
            s = max(s, min_scale)
            s = _apply_speed_caps(prev, s, up_speed_cap, down_speed_cap) if not np.isnan(prev) else s
            base_speed.loc[t] = s
            prev = s
    else:
        base_speed = pd.Series(1.0, index=r.index, dtype=float)
        base_raw = base_clamped = base_speed.copy()
    if use_dd_throttle:
        eq = (1.0 + r.fillna(0)).cumprod()
        dd = (eq / eq.cummax() - 1.0)
        throttle_vals = pd.Series(1.0, index=r.index)
        tmp = throttle_vals.copy()
        for cut, mult in dd_cuts:
            tmp = np.where(dd.shift(1) <= cut, tmp * mult, tmp)
        throttle_vals = pd.Series(tmp, index=r.index)
    else:
        throttle_vals = pd.Series(1.0, index=r.index)
    final_scale = (base_speed * throttle_vals).clip(lower=min_scale)
    if de_risk_only:
        final_scale = final_scale.clip(upper=1.0)
    scaled_rets = final_scale * r
    if verbose:
        pd.DataFrame({
            "base_raw_shifted": base_raw,
            "base_clamped": base_clamped,
            "base_speed_capped": base_speed,
            "dd_throttle_shifted": throttle_vals,
            "final_scale": final_scale,
        }).to_csv(os.path.join(CSVA_DIR, "portfolio_scalers.csv"))
        cli.saved(os.path.join(CSVA_DIR, "portfolio_scalers.csv"))
    return scaled_rets


def _simple_turnover_cost_series(port_index: pd.Index, active_sets: List[set], per_side_bps: float,
                                 rebalance_monthly: bool, *, fixed_fee_dollars: float = 25.0,
                                 initial_capital: float = 300_000.0) -> pd.Series:
    """Fallback transaction-cost estimate when an execution-level trade log is unavailable.

    WInS stock/ETF transactions are flat-dollar fees. `per_side_bps` is retained only
    as an OPTIONAL extra proportional execution-cost sensitivity; it is no longer the
    platform commission itself.
    """
    proportional = max(float(per_side_bps), 0.0) / 1e4
    capital = max(float(initial_capital), 1.0)
    out = []
    for i in range(len(active_sets)):
        if i == 0:
            # Initial entries are unknown without a trade log; charge one transaction
            # per active name as a conservative WInS implementation approximation.
            n_initial = len(active_sets[i]) if i < len(active_sets) else 0
            out.append(min(n_initial * float(fixed_fee_dollars) / capital, 0.01))
            continue
        prev, cur = active_sets[i - 1], active_sets[i]
        entered = len(cur - prev)
        exited = len(prev - cur)
        n_transactions = entered + exited
        denom = max(len(prev | cur), 1)
        changed = n_transactions / denom
        fixed_cost = n_transactions * float(fixed_fee_dollars) / capital
        prop_cost = changed * proportional
        if rebalance_monthly:
            # Approximate one extra rebalance transaction for 10% sleeve turnover.
            fixed_cost += float(fixed_fee_dollars) / capital
            prop_cost += 0.10 * proportional
        out.append(min(fixed_cost + prop_cost, 0.01))
    return pd.Series(out, index=port_index, dtype=float).fillna(0.0)


def _execution_bar_metrics(daily: pd.DataFrame, exec_ts: pd.Timestamp, adv_window: int) -> Optional[Dict[str, float]]:
    if daily is None or daily.empty or exec_ts is None or pd.isna(exec_ts):
        return None

    exec_ts = pd.Timestamp(exec_ts)
    month = exec_ts.to_period("M")
    month_slice = daily[daily.index.to_period("M") == month]
    if month_slice.empty:
        month_slice = daily[daily.index.to_period("M") == (month + 1)]
    if month_slice.empty:
        return None

    bar = month_slice.iloc[0]
    loc = daily.index.get_loc(bar.name)
    if isinstance(loc, slice):
        pos = loc.start
    elif isinstance(loc, np.ndarray):
        pos = int(loc[0]) if loc.size else 0
    else:
        pos = int(loc)

    prev_close = float(bar["Open"])
    if pos > 0:
        prev_close = float(daily.iloc[pos - 1]["Close"])

    high = float(bar["High"])
    low = float(bar["Low"])
    open_px = float(bar["Open"])
    if open_px == 0:
        close_px = float(bar["Close"])
        open_px = close_px if close_px != 0 else 1.0
    true_range = max(high - low, abs(high - prev_close), abs(low - prev_close))
    true_range_pct = max(true_range / open_px, 0.0)

    dollar_vol_series = (daily["Close"] * daily["Volume"]).replace([np.inf, -np.inf], np.nan)
    if pos > 0:
        adv_slice = dollar_vol_series.iloc[max(0, pos - adv_window):pos]
    else:
        adv_slice = pd.Series(dtype=float)
    if adv_slice.empty:
        adv_dollar = float(dollar_vol_series.iloc[:pos + 1].mean()) if pos >= 0 else float(dollar_vol_series.mean())
    else:
        adv_dollar = float(adv_slice.mean())
    adv_dollar = float(np.nan_to_num(adv_dollar, nan=0.0, posinf=0.0, neginf=0.0))

    day_dollar = float(bar["Close"] * bar["Volume"])
    day_dollar = float(np.nan_to_num(day_dollar, nan=0.0, posinf=0.0, neginf=0.0))

    return {
        "true_range_pct": true_range_pct,
        "adv_dollar": max(adv_dollar, 0.0),
        "day_dollar": max(day_dollar, 0.0),
    }


def estimate_turnover_cost(port_index: pd.Index, active_sets: List[set], per_side_bps: float = 0.0,
                           rebalance_monthly: bool = False, *, master_trades: Optional[pd.DataFrame] = None,
                           data_dir: str = DATA_DIR, slippage_mult: float = 0.5, adv_window: int = 20,
                           min_slip_bps: float = 1.0, initial_capital: float = 300_000.0,
                           fixed_stock_fee: float = 25.0) -> Tuple[
    pd.Series, pd.DataFrame]:
    """
    Estimates a robust, dynamic cost series for the portfolio.

    This model is NOT a simple, fixed-cost assumption. It incorporates:
    1.  **WInS Flat Stock Commission:** `fixed_stock_fee` dollars per cleared transaction.
    2.  **Optional Extra Proportional Cost:** `per_side_bps` (default 0; sensitivity only).
    3.  **Dynamic Slippage:** Modeled as a function of:
        -   `slippage_mult`: A multiplier on market volatility.
        -   `true_range_pct`: A proxy for daily volatility.
        -   `participation`: The trade's notional size as a % of ADV,
                            simulating market impact.

    ADV is calculated using a lookback window to avoid look-ahead bias.
    A fallback is used if master_trades is not provided.
    """
    active_counts = pd.Series([max(len(s), 1) for s in active_sets], index=port_index, dtype=float)
    base_series = pd.Series(0.0, index=port_index, dtype=float)

    if master_trades is None or master_trades.empty:
        simple = _simple_turnover_cost_series(
            port_index, active_sets, per_side_bps, rebalance_monthly,
            fixed_fee_dollars=fixed_stock_fee, initial_capital=initial_capital
        )
        return simple, pd.DataFrame()

    trades = master_trades.copy()
    trades["entry_date"] = pd.to_datetime(trades["entry_date"], errors="coerce")
    if "exit_date" in trades.columns:
        trades["exit_date"] = pd.to_datetime(trades["exit_date"], errors="coerce")
    else:
        trades["exit_date"] = pd.NaT

    per_side_pct = per_side_bps / 1e4
    min_slip_pct = (min_slip_bps or 0.0) / 1e4
    rows: List[Dict[str, Any]] = []
    daily_cache: Dict[str, pd.DataFrame] = {}

    def get_daily(sym: str) -> pd.DataFrame:
        key = sym.upper()
        if key in daily_cache:
            return daily_cache[key]
        try:
            daily_cache[key] = _load_daily_csv(key, data_dir)
        except Exception:
            alias = TICKER_ALIAS.get(key, key)
            try:
                daily_cache[key] = _load_daily_csv(alias, data_dir)
            except Exception:
                daily_cache[key] = pd.DataFrame()
        return daily_cache[key]

    def weight_for_month(ts: pd.Timestamp) -> float:
        if ts not in active_counts.index:
            return 0.0
        denom = float(active_counts.loc[ts])
        return 0.0 if denom <= 0 else 1.0 / denom

    def weight_before_month(ts: pd.Timestamp) -> float:
        if ts not in active_counts.index:
            return 0.0
        loc = active_counts.index.get_loc(ts)
        if isinstance(loc, slice):
            loc = loc.start
        elif isinstance(loc, np.ndarray):
            loc = int(loc[0]) if loc.size else 0
        if loc > 0:
            denom = float(active_counts.iloc[loc - 1])
            return 0.0 if denom <= 0 else 1.0 / denom
        return weight_for_month(ts)

    for trade in trades.itertuples():
        ticker = str(getattr(trade, "ticker", "")).upper()
        trade_id = getattr(trade, "trade_id", None)
        daily = get_daily(ticker)

        for side, exec_date in (("ENTRY", getattr(trade, "entry_date", None)),
                                ("EXIT", getattr(trade, "exit_date", None))):
            if exec_date is None or pd.isna(exec_date):
                continue
            exec_ts = pd.Timestamp(exec_date)
            exec_month = exec_ts.to_period("M").to_timestamp("M")
            weight = weight_for_month(exec_month) if side == "ENTRY" else weight_before_month(exec_month)
            if weight <= 0:
                continue

            metrics = _execution_bar_metrics(daily, exec_ts, adv_window) if not daily.empty else None
            range_pct = metrics["true_range_pct"] if metrics else 0.0
            adv_dollar = metrics["adv_dollar"] if metrics and metrics["adv_dollar"] > 0 else None
            day_dollar = metrics["day_dollar"] if metrics and metrics["day_dollar"] > 0 else None
            dollar_liquidity = adv_dollar if adv_dollar and adv_dollar > 0 else day_dollar if day_dollar and day_dollar > 0 else None

            # Conservative fallback liquidity so %ADV/participation isn't 0
            if not dollar_liquidity or dollar_liquidity <= 0:
                dollar_liquidity = 5_000_000_000.0  # 5B default for mega caps

            trade_notional = initial_capital * weight
            participation = min(1.0, trade_notional / dollar_liquidity)

            slippage_pct = max(min_slip_pct, slippage_mult * range_pct * max(participation, 1e-6))

            # WInS charges a FLAT fee, not a bps commission. The portfolio return
            # impact of that fee is fee / portfolio capital. Slippage remains
            # proportional to the individual position weight.
            fixed_commission_portfolio_pct = float(fixed_stock_fee) / max(float(initial_capital), 1.0)
            extra_prop_portfolio_pct = per_side_pct * weight
            slippage_portfolio_pct = slippage_pct * weight
            total_pct = fixed_commission_portfolio_pct + extra_prop_portfolio_pct + slippage_portfolio_pct

            base_series.loc[exec_month] += total_pct
            rows.append({
                "trade_id": trade_id,
                "ticker": ticker,
                "side": side,
                "exec_month": exec_month,
                "weight": weight,
                "fixed_commission_dollars": float(fixed_stock_fee),
                "commission_pct": fixed_commission_portfolio_pct,
                "extra_proportional_cost_pct": extra_prop_portfolio_pct,
                "slippage_pct": slippage_pct,
                "slippage_portfolio_pct": slippage_portfolio_pct,
                "cost_pct": total_pct,
                "participation": participation,
                "range_pct": range_pct,
                "notional": trade_notional,
                "liquidity_dollar": dollar_liquidity,
            })

    if not rows:
        simple = _simple_turnover_cost_series(
            port_index, active_sets, per_side_bps, rebalance_monthly,
            fixed_fee_dollars=fixed_stock_fee, initial_capital=initial_capital
        )
        return simple, pd.DataFrame()

    if rebalance_monthly and per_side_pct > 0:
        reb_add = 0.10 * per_side_pct
        base_series = base_series.add(reb_add, fill_value=0.0)

    base_series = base_series.reindex(port_index, fill_value=0.0)
    trade_costs = pd.DataFrame(rows)
    return base_series, trade_costs


def summarize_turnover_and_trade_costs(active_sets: List[set], idx: pd.Index, master_trades: pd.DataFrame,
                                       per_side_bps: float = 0.0, *,
                                       trade_costs: Optional[pd.DataFrame] = None,
                                       cost_series: Optional[pd.Series] = None) -> Dict[str, float]:
    churn = []
    for i in range(1, len(active_sets)):
        prev, cur = active_sets[i - 1], active_sets[i]
        denom = max(len(prev | cur), 1)  # union
        changed = len((prev - cur) | (cur - prev)) / denom
        churn.append(changed)
    avg_monthly_churn = float(np.nanmean(churn)) if churn else 0.0
    turnover_per_year = avg_monthly_churn * 12.0 * 100.0
    result = dict(
        avg_monthly_churn=avg_monthly_churn,
        turnover_per_year=turnover_per_year,
        trades_per_month=0.0,
        implied_cost_drag_bps_per_year=0.0,
    )

    if master_trades is None or master_trades.empty:
        return result

    master_trades["entry_date"] = pd.to_datetime(master_trades["entry_date"])
    active_sizes = [len(s) for s in active_sets]
    avg_active = float(np.nanmean(active_sizes)) if active_sizes else 1.0
    trades_per_month = len(master_trades) / max(len(idx), 1)
    result["trades_per_month"] = trades_per_month

    if cost_series is not None and not cost_series.empty:
        result["implied_cost_drag_bps_per_year"] = float(cost_series.mean() * 12.0 * 1e4)
    else:
        round_trip_cost_pct = 2.0 * per_side_bps / 1e4
        monthly_cost_drag = (trades_per_month / max(avg_active, 1.0)) * round_trip_cost_pct
        result["implied_cost_drag_bps_per_year"] = monthly_cost_drag * 12.0 * 1e4

    if trade_costs is not None and not trade_costs.empty:
        valid = trade_costs.dropna(subset=["trade_id"]) if "trade_id" in trade_costs.columns else trade_costs
        grouped = valid.groupby("trade_id")["cost_pct"].sum() if "trade_id" in valid.columns else pd.Series(dtype=float)
        if not grouped.empty:
            result["median_round_trip_bps"] = float(grouped.median() * 1e4)
            result["mean_round_trip_bps"] = float(grouped.mean() * 1e4)
        if "participation" in valid.columns and valid["participation"].notna().any():
            result["avg_participation"] = float(valid["participation"].dropna().mean())

    return result


# =====================================================================
# Summary & Benchmarks
# =====================================================================
def summarize_and_plot(port_rets: pd.Series, initial_capital: float = 10_000.0,
                       out_csv: str = os.path.join(CSVA_DIR, "portfolio_monthly_returns.csv"),
                       out_png: str = os.path.join(CSVA_DIR, "portfolio_equity.png")) -> Dict[str, float]:
    if port_rets.empty: raise ValueError("Empty portfolio return series.")
    port_rets.to_csv(out_csv, header=True)
    cli.saved(out_csv)

    # Correct equity/return math (no off-by-one)
    equity = (1.0 + port_rets).cumprod() * initial_capital
    months = len(port_rets)
    years = months / 12.0
    total_return = (1.0 + port_rets).prod() - 1.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    if len(port_rets) > 1:
        mu = port_rets.mean()
        sd = port_rets.std()
        sharpe = (mu / sd) * np.sqrt(12) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    rollmax = equity.cummax()
    dd = equity / rollmax - 1.0
    max_dd = dd.min()

    stats = {
        "FinalCapital": float(equity.iloc[-1]),
        "TotalReturnPct": total_return * 100.0,
        "CAGR": cagr,
        "Sharpe": sharpe,
        "MaxDrawdownPct": max_dd * 100.0,
        "Months": int(months),
    }

    plt.figure(figsize=(12, 6))
    x = equity.index.to_timestamp() if hasattr(equity.index, "to_timestamp") else equity.index
    plt.plot(x, equity.values, linewidth=2)
    plt.title("Portfolio Equity (Open→Open, split-adjusted)", pad=10)
    plt.xlabel("Date")
    plt.ylabel("Equity ($)")
    plt.grid(True, alpha=0.3, linestyle="--")
    plt.tight_layout()
    plt.savefig(out_png, dpi=200, bbox_inches="tight")
    cli.saved(out_png)
    cli.box("PORTFOLIO SUMMARY", [
        ("Final Capital", f"${stats['FinalCapital']:,.2f}"),
        ("Total Return", f"{stats['TotalReturnPct']:.2f}%"),
        ("CAGR", f"{stats['CAGR'] * 100:.2f}%"),
        ("Sharpe (ann.)", f"{stats['Sharpe']:.2f}"),
        ("Max Drawdown", f"{stats['MaxDrawdownPct']:.2f}%"),
        ("Months", str(stats["Months"]))
    ])
    return stats


def _monthly_benchmark_o2o(symbol: str, data_dir: str, start: Optional[str], end: Optional[str]) -> Optional[pd.Series]:
    try:
        d = _load_daily_csv(symbol, data_dir, start_hint=start, end_hint=end)
    except Exception as e:
        cli.warn(f"{symbol}: benchmark load failed — {e}")
        return None
    if start: d = d[d.index >= pd.to_datetime(start)]
    if end:   d = d[d.index <= pd.to_datetime(end)]
    if d.empty:
        cli.warn(f"{symbol}: no data in window for benchmark.")
        return None
    m_open = d["Open"].resample("ME").first().dropna()
    m_next_open = m_open.shift(-1)
    m_ret = (m_next_open / m_open - 1.0).fillna(0.0)
    m_ret.name = symbol.upper()
    return m_ret


def _yearly_returns_from_monthlies(r: pd.Series) -> pd.Series:
    if r is None or r.empty: return pd.Series(dtype=float)
    y = ((1.0 + r).groupby(r.index.year).prod() - 1.0)
    y.index = y.index.astype(int)
    return y


def build_yearly_comparison_table_o2o(port_monthly: pd.Series, data_dir: str, start: Optional[str], end: Optional[str],
                                      symbols: List[str] = ["SPY", "QQQ"]) -> pd.DataFrame:
    """Build year-by-year comparison table with Portfolio, SPY, and QQQ"""
    cli.section("Building Year-by-Year Comparison with Benchmarks")

    # Get portfolio yearly returns
    port_y = _yearly_returns_from_monthlies(port_monthly)
    comp = pd.DataFrame({"Portfolio": port_y})

    # Get benchmark yearly returns
    for s in symbols:
        cli.info(f"Loading benchmark: {s}")
        b = _monthly_benchmark_o2o(s, data_dir, start, end)
        if b is None:
            cli.warn(f"Could not load {s}")
            continue
        by = _yearly_returns_from_monthlies(b)
        comp[s.upper()] = by

    # Calculate outperformance
    if "SPY" in comp.columns:
        comp["vs_SPY"] = comp["Portfolio"] - comp["SPY"]
    if "QQQ" in comp.columns:
        comp["vs_QQQ"] = comp["Portfolio"] - comp["QQQ"]

    comp = comp.sort_index()

    # Save to CSV
    out_csv = os.path.join(CSVA_DIR, "yearly_vs_bench_o2o.csv")
    comp.to_csv(out_csv, float_format="%.6f")
    cli.saved(out_csv)

    # Display table with percentages
    cli.table_percent(comp, "Calendar-Year Returns (%): Portfolio vs SPY vs QQQ",
                      percent_cols=("Portfolio", "SPY", "QQQ", "vs_SPY", "vs_QQQ"))

    # Summary statistics
    if len(comp) > 0:
        cli.section("Summary Statistics (Year-by-Year)")

        summary_lines = []
        summary_lines.append(("Years Analyzed", str(len(comp))))
        summary_lines.append(("Portfolio Avg", f"{comp['Portfolio'].mean() * 100:.2f}%"))

        if "SPY" in comp.columns:
            summary_lines.append(("SPY Avg", f"{comp['SPY'].mean() * 100:.2f}%"))
            summary_lines.append(("Win Rate vs SPY", f"{(comp['Portfolio'] > comp['SPY']).mean() * 100:.1f}%"))
            summary_lines.append(("Avg Outperformance vs SPY", f"{comp['vs_SPY'].mean() * 100:.2f}%"))

        if "QQQ" in comp.columns:
            summary_lines.append(("QQQ Avg", f"{comp['QQQ'].mean() * 100:.2f}%"))
            summary_lines.append(("Win Rate vs QQQ", f"{(comp['Portfolio'] > comp['QQQ']).mean() * 100:.1f}%"))
            summary_lines.append(("Avg Outperformance vs QQQ", f"{comp['vs_QQQ'].mean() * 100:.2f}%"))

        cli.box("Year-by-Year Comparison Summary", summary_lines)

    # Create visualization
    plot_yearly_comparison(comp)

    return comp


def plot_yearly_comparison(comp: pd.DataFrame):
    """Create visualization of year-by-year returns"""
    if comp.empty:
        return

    fig, axes = plt.subplots(2, 1, figsize=(14, 10))

    years = comp.index
    x = np.arange(len(years))
    width = 0.25

    # Plot 1: Absolute returns
    ax1 = axes[0]
    ax1.bar(x - width, comp["Portfolio"] * 100, width, label="Portfolio", alpha=0.8)
    if "SPY" in comp.columns:
        ax1.bar(x, comp["SPY"] * 100, width, label="SPY", alpha=0.8)
    if "QQQ" in comp.columns:
        ax1.bar(x + width, comp["QQQ"] * 100, width, label="QQQ", alpha=0.8)

    ax1.set_xlabel("Year")
    ax1.set_ylabel("Annual Return (%)")
    ax1.set_title("Year-by-Year Returns: Portfolio vs Benchmarks")
    ax1.set_xticks(x)
    ax1.set_xticklabels(years, rotation=45)
    ax1.legend()
    ax1.grid(True, alpha=0.3, axis="y")
    ax1.axhline(0, color="black", linewidth=0.5)

    # Plot 2: Cumulative returns
    ax2 = axes[1]
    cum_port = (1 + comp["Portfolio"]).cumprod()
    ax2.plot(years, cum_port, "o-", linewidth=2, label="Portfolio", markersize=4)

    if "SPY" in comp.columns:
        cum_spy = (1 + comp["SPY"]).cumprod()
        ax2.plot(years, cum_spy, "s-", linewidth=2, label="SPY", markersize=4)

    if "QQQ" in comp.columns:
        cum_qqq = (1 + comp["QQQ"]).cumprod()
        ax2.plot(years, cum_qqq, "^-", linewidth=2, label="QQQ", markersize=4)

    ax2.set_xlabel("Year")
    ax2.set_ylabel("Cumulative Growth ($1 → $X)")
    ax2.set_title("Cumulative Growth: Portfolio vs Benchmarks")
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.set_yscale("log")

    plt.tight_layout()
    out_png = os.path.join(CSVA_DIR, "yearly_comparison.png")
    plt.savefig(out_png, dpi=200, bbox_inches="tight")
    cli.saved(out_png)
    plt.close()


def _ensure_timestamp_series(series: Optional[pd.Series]) -> Optional[pd.Series]:
    if series is None or series.empty:
        return None
    s = series.copy()
    if isinstance(s.index, pd.PeriodIndex):
        s.index = s.index.to_timestamp()
    return s


def plot_yearly_line_charts(port_monthly: pd.Series,
                            equal_weight_monthly: Optional[pd.Series] = None,
                            spy_monthly: Optional[pd.Series] = None,
                            qqq_monthly: Optional[pd.Series] = None,
                            out_dir: Optional[str] = None) -> None:
    """Create per-year line charts comparing portfolio, equal-weight, and benchmarks."""
    port_monthly = _ensure_timestamp_series(port_monthly)
    eq_monthly = _ensure_timestamp_series(equal_weight_monthly)
    spy_monthly = _ensure_timestamp_series(spy_monthly)
    qqq_monthly = _ensure_timestamp_series(qqq_monthly)

    valid = {k: v for k, v in {
        "Portfolio": port_monthly,
        "Equal-Weight": eq_monthly,
        "SPY": spy_monthly,
        "QQQ": qqq_monthly,
    }.items() if v is not None and not v.empty}

    if not valid:
        return

    all_years: Set[int] = set()
    for series in valid.values():
        all_years.update(series.index.year.tolist())

    if not all_years:
        return

    out_dir = out_dir or os.path.join(CSVA_DIR, "yearly_line_charts")
    os.makedirs(out_dir, exist_ok=True)

    for year in sorted(all_years):
        fig, ax = plt.subplots(figsize=(10, 5))
        plotted = False
        for label, series in valid.items():
            year_mask = series.index.year == year
            if not year_mask.any():
                continue
            year_slice = series.loc[year_mask]
            if year_slice.empty:
                continue
            growth = (1.0 + year_slice).cumprod()
            ax.plot(year_slice.index, growth, linewidth=2, label=label)
            plotted = True

        if not plotted:
            plt.close(fig)
            continue

        ax.set_title(f"Monthly Growth — {year}")
        ax.set_xlabel("Date")
        ax.set_ylabel("Growth of $1")
        ax.grid(True, alpha=0.3, linestyle="--")
        ax.legend()
        ax.set_ylim(bottom=0)
        plt.tight_layout()

        out_png = os.path.join(out_dir, f"year_{year}.png")
        plt.savefig(out_png, dpi=200, bbox_inches="tight")
        cli.saved(out_png)
        plt.close(fig)


# =====================================================================
# STRATEGY GRID + DATA-SNOOPING TESTS
# =====================================================================
def _stationary_bootstrap_indices(n: int, block_size: int) -> List[int]:
    block = max(1, int(block_size))
    idx: List[int] = []
    while len(idx) < n:
        start = np.random.randint(0, n)
        length = np.random.geometric(1.0 / block)
        for j in range(length):
            idx.append((start + j) % n)
            if len(idx) >= n:
                break
    return idx[:n]


def _column_sharpe(df: pd.DataFrame) -> pd.Series:
    mu = df.mean(axis=0)
    sd = df.std(axis=0, ddof=1)
    ann = np.sqrt(12)
    sharpe = np.where(sd > 0, (mu / sd) * ann, 0.0)
    return pd.Series(sharpe, index=df.columns)


def _extract_grid_levels(columns: pd.Index) -> Tuple[pd.Index, List[float]]:
    if isinstance(columns, pd.MultiIndex) and columns.nlevels >= 2:
        return columns.get_level_values(0), list(columns.get_level_values(1))
    return columns, [np.nan] * len(columns)


def build_strategy_return_grid(
        yearly_map,
        data_dir,
        start,
        end,
        *,
        ema_range: Optional[List[int]] = None,
        gain_range: Optional[List[float]] = None,
        preload_years: int = 1,
        per_side_bps: float = 0.0,
        target_vol: float = 0.30,
        lookback_m: int = 12,
        clamp: Tuple[float, float] = (0.2, 1.0),
        dd_cuts: Tuple[Tuple[float, float], ...] = ((-0.05, 0.9), (-0.10, 0.7)),
        winsor: float = 0.10,
        scaling_kwargs: Optional[Dict[str, Any]] = None,
        min_active_names: int = 0,
        verbose: bool = True,
) -> pd.DataFrame:
    ema_vals = ema_range or [150, 175, 200, 225, 250]
    gain_vals = gain_range or [20.0, 25.0, 30.0, 35.0, 40.0]
    scaling_kwargs = scaling_kwargs or {}

    series_list: List[pd.Series] = []
    col_keys: List[Tuple[int, float]] = []

    for ema in ema_vals:
        for gain in gain_vals:
            if verbose: cli.info(f"  Building grid: EMA={ema}, Gain={gain:.1f}")
            try:
                port_raw, _, active_sets = build_portfolio_returns_from_csv(
                    yearly_map,
                    data_dir,
                    start=start,
                    end=end,
                    ema_period=ema,
                    min_sell_gain_pct=gain,
                    min_active_names=min_active_names,
                    preload_years=preload_years,
                    verbose=False,  # <-- Suppress inner progress bar
                )
                costs, _ = estimate_turnover_cost(port_raw.index, active_sets, per_side_bps=per_side_bps)
                port_after_cost = port_raw - costs
                port_scaled = apply_vol_target_and_dd_throttle(
                    port_after_cost,
                    target_vol_yr=target_vol,
                    lookback_m=lookback_m,
                    clamp=clamp,
                    dd_cuts=dd_cuts,
                    winsor=winsor,
                    verbose=False,  # <-- Suppress saving scaler CSVs
                    **scaling_kwargs,
                )
                label = f"EMA{ema}_GAIN{gain}"
                series_list.append(port_scaled.rename(label))
                col_keys.append((ema, gain))
            except Exception as e:
                if STRICT_DATA_MODE:
                    raise RuntimeError(f"Strict data mode: parameter grid failed for EMA={ema}, Gain={gain}: {e}") from e
                cli.warn(f"Failed to build strategy grid for EMA={ema}, Gain={gain}: {e}")

    if not series_list:
        return pd.DataFrame()

    grid = pd.concat(series_list, axis=1, join="inner")
    grid.columns = pd.MultiIndex.from_tuples(col_keys, names=["EMA_Period", "Min_Gain_Pct"])
    grid = grid.sort_index()
    return grid


def whites_reality_check(
        strategy_returns: pd.DataFrame,
        *,
        n_boot: int = 2000,
        block_size: int = 6,
) -> Dict[str, Any]:
    cli.section("White's Reality Check")
    if strategy_returns is None or strategy_returns.empty:
        cli.warn("Strategy grid empty; skipping White's Reality Check.")
        return {}

    aligned = strategy_returns.dropna(how="any")
    if aligned.empty:
        cli.warn("Strategy grid missing overlapping history; skipping Reality Check.")
        return {}

    sharpe = _column_sharpe(aligned)
    best_col = sharpe.idxmax()
    best_sharpe = sharpe.loc[best_col]
    centered = aligned - aligned.mean()

    boot_max = []
    cli.info(f"Stationary bootstrap ({n_boot} resamples) for Reality Check...")
    for b in range(n_boot):
        if b % max(1, n_boot // 10) == 0:
            cli.progress(b, n_boot, "Reality Check")
        idx = _stationary_bootstrap_indices(len(centered), block_size)
        sample = centered.iloc[idx]
        boot_sharpe = _column_sharpe(sample)
        boot_max.append(boot_sharpe.max())
    cli.progress(n_boot, n_boot, "Reality Check")

    boot_arr = np.array(boot_max)
    p_value = (np.sum(boot_arr >= best_sharpe) + 1) / (n_boot + 1)

    ema_vals, gain_vals = _extract_grid_levels(aligned.columns)
    summary = pd.DataFrame({
        "EMA_Period": ema_vals,
        "Min_Gain_Pct": gain_vals,
        "Sharpe": sharpe.values,
        "RealityCheck_p": [p_value if col == best_col else np.nan for col in sharpe.index],
    }).sort_values("Sharpe", ascending=False)

    out_path = os.path.join(DIAG_DIR, "whites_reality_check.csv")
    summary.to_csv(out_path, index=False)
    cli.saved(out_path)
    _print_table(summary.head(10), "Top Sharpe Combos (Reality Check)")
    cli.box("White's Reality Check", [
        ("Best EMA", str(best_col[0]) if isinstance(best_col, tuple) else str(best_col)),
        ("Best Gain%", f"{best_col[1]:.1f}" if isinstance(best_col, tuple) else "-"),
        ("Best Sharpe", f"{best_sharpe:.2f}"),
        ("p-value", _fmt_pvalue(float(p_value))),
        ("Bootstraps", str(n_boot)),
    ])

    return {
        "best_strategy": best_col,
        "best_sharpe": best_sharpe,
        "p_value": p_value,
    }


def hansen_spa(
        strategy_returns: pd.DataFrame,
        *,
        n_boot: int = 2000,
        block_size: int = 6,
) -> pd.DataFrame:
    cli.section("Hansen's SPA Test")
    if strategy_returns is None or strategy_returns.empty:
        cli.warn("Strategy grid empty; skipping SPA test.")
        return pd.DataFrame()

    aligned = strategy_returns.dropna(how="any")
    if aligned.empty:
        cli.warn("Strategy grid missing overlapping history; skipping SPA test.")
        return pd.DataFrame()

    T = len(aligned)
    sqrt_T = np.sqrt(T)
    sigma = aligned.std(axis=0, ddof=1).replace(0, np.nan)
    mu = aligned.mean(axis=0)
    sharpe = _column_sharpe(aligned)
    obs_t = ((mu / sigma) * sqrt_T).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    centered = aligned - mu

    cli.info(f"Bootstrapping {n_boot} samples for SPA...")
    boot_stats = np.zeros((n_boot, aligned.shape[1]))
    for b in range(n_boot):
        if b % max(1, n_boot // 10) == 0:
            cli.progress(b, n_boot, "SPA")
        idx = _stationary_bootstrap_indices(T, block_size)
        sample = centered.iloc[idx]
        boot_mu = sample.mean(axis=0)
        boot_t = ((boot_mu / sigma) * sqrt_T).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        boot_stats[b, :] = boot_t.values
    cli.progress(n_boot, n_boot, "SPA")

    order = obs_t.sort_values(ascending=False).index
    positive_order = [col for col in order if obs_t.loc[col] >= 0]
    col_pos = {col: i for i, col in enumerate(aligned.columns)}
    pvals = pd.Series(1.0, index=aligned.columns, dtype=float)

    for col in positive_order:
        remaining = positive_order[positive_order.index(col):]
        remain_idx = [col_pos[c] for c in remaining]
        obs = obs_t.loc[remaining].max()
        boot = boot_stats[:, remain_idx].max(axis=1)
        p = (np.sum(boot >= obs) + 1) / (n_boot + 1)
        pvals.loc[col] = p

    ema_vals, gain_vals = _extract_grid_levels(aligned.columns)
    spa_df = pd.DataFrame({
        "EMA_Period": ema_vals,
        "Min_Gain_Pct": gain_vals,
        "Sharpe": sharpe.values,
        "SPA_pvalue": pvals.values,
    }).sort_values("Sharpe", ascending=False)

    out_path = os.path.join(DIAG_DIR, "hansen_spa.csv")
    spa_df.to_csv(out_path, index=False)
    cli.saved(out_path)
    _print_table(spa_df.head(10), "Hansen SPA (Top 10)")

    if not spa_df.empty:
        best = spa_df.iloc[0]
        cli.box("Hansen SPA", [
            ("Best EMA", f"{best['EMA_Period']:.0f}"),
            ("Best Gain%", f"{best['Min_Gain_Pct']:.1f}"),
            ("Sharpe", f"{best['Sharpe']:.2f}"),
            ("SPA p-value", _fmt_pvalue(float(best['SPA_pvalue']))),
            ("Bootstraps", str(n_boot)),
        ])

    return spa_df


# =====================================================================
# MONTE CARLO SIMULATIONS
# =====================================================================
def monte_carlo_bootstrap(returns: pd.Series, n_sims: int = 10000, block_size: int = 6,
                          seed: Optional[int] = None) -> pd.DataFrame:
    """
    Stationary block bootstrap Monte Carlo simulation
    Returns distribution of performance metrics
    """
    cli.section("Monte Carlo Bootstrap Analysis")
    if seed is not None:
        np.random.seed(seed)

    r = returns.dropna().values
    n = len(r)

    results = []
    cli.info(f"Running {n_sims} bootstrap simulations...")

    for sim in range(n_sims):
        if sim % 1000 == 0:
            cli.progress(sim, n_sims, "Bootstrap sims")

        # Stationary block bootstrap
        boot_returns = []
        idx = 0
        while idx < n:
            start = np.random.randint(0, n)
            block_len = np.random.geometric(1.0 / block_size)
            block_len = min(block_len, n - idx)
            for j in range(block_len):
                boot_returns.append(r[(start + j) % n])
            idx += block_len

        boot_returns = np.array(boot_returns[:n])

        # Calculate metrics
        eq = (1 + boot_returns).cumprod()
        total_years = n / 12.0
        cagr = (eq[-1] ** (1 / total_years) - 1) if total_years > 0 else 0
        sharpe = (boot_returns.mean() / boot_returns.std()) * np.sqrt(12) if boot_returns.std() > 0 else 0
        dd = eq / np.maximum.accumulate(eq) - 1
        max_dd = dd.min()

        results.append({
            "CAGR": cagr,
            "Sharpe": sharpe,
            "MaxDD": max_dd,
            "FinalEquity": eq[-1]
        })

    cli.progress(n_sims, n_sims, "Bootstrap sims")

    df = pd.DataFrame(results)

    # Save results
    df.to_csv(os.path.join(MC_DIR, "mc_bootstrap_results.csv"), index=False)
    cli.saved(os.path.join(MC_DIR, "mc_bootstrap_results.csv"))

    # Plot distributions
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    metrics = ["CAGR", "Sharpe", "MaxDD", "FinalEquity"]
    for idx, metric in enumerate(metrics):
        ax = axes[idx // 2, idx % 2]
        ax.hist(df[metric], bins=50, alpha=0.7, edgecolor="black")
        ax.axvline(df[metric].median(), color="r", linestyle="--", linewidth=2,
                   label=f"Median: {df[metric].median():.3f}")
        ax.axvline(df[metric].quantile(0.05), color="orange", linestyle="--", linewidth=1,
                   label=f"5th %ile: {df[metric].quantile(0.05):.3f}")
        ax.axvline(df[metric].quantile(0.95), color="green", linestyle="--", linewidth=1,
                   label=f"95th %ile: {df[metric].quantile(0.95):.3f}")
        ax.set_title(f"{metric} Distribution")
        ax.set_xlabel(metric)
        ax.set_ylabel("Frequency")
        ax.legend()
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(MC_DIR, "mc_distributions.png"), dpi=200)
    cli.saved(os.path.join(MC_DIR, "mc_distributions.png"))
    plt.close()

    # Summary statistics
    summary = pd.DataFrame({
        "Metric": metrics,
        "Mean": [df[m].mean() for m in metrics],
        "Median": [df[m].median() for m in metrics],
        "Std": [df[m].std() for m in metrics],
        "5th_percentile": [df[m].quantile(0.05) for m in metrics],
        "95th_percentile": [df[m].quantile(0.95) for m in metrics]
    })

    summary.to_csv(os.path.join(MC_DIR, "mc_summary.csv"), index=False)
    cli.saved(os.path.join(MC_DIR, "mc_summary.csv"))

    _print_table(summary, "Monte Carlo Summary Statistics")

    return df


def monte_carlo_parameter_sensitivity(
        yearly_map,
        data_dir,
        start,
        end,
        preload_years=1,
        n_trials=50,
        seed: Optional[int] = None,
        *,
        ema_range: Optional[List[int]] = None,
        gain_range: Optional[List[float]] = None,
        per_side_bps: float = 0.0,
        target_vol: float = 0.30,
        lookback_m: int = 12,
        clamp: Tuple[float, float] = (0.2, 1.0),
        dd_cuts: Tuple[Tuple[float, float], ...] = ((-0.05, 0.9), (-0.10, 0.7)),
        winsor: float = 0.10,
        scaling_kwargs: Optional[Dict[str, Any]] = None,
        min_active_names: int = 0,
):
    """
    Test strategy sensitivity to parameter variations
    """
    cli.section("Monte Carlo Parameter Sensitivity Analysis")
    if seed is not None:
        np.random.seed(seed)

    ema_vals = ema_range or [150, 175, 200, 225, 250]
    gain_vals = gain_range or [20.0, 25.0, 30.0, 35.0, 40.0]

    strategy_grid = build_strategy_return_grid(
        yearly_map,
        data_dir,
        start,
        end,
        ema_range=ema_vals,
        gain_range=gain_vals,
        preload_years=preload_years,
        per_side_bps=per_side_bps,
        target_vol=target_vol,
        lookback_m=lookback_m,
        clamp=clamp,
        dd_cuts=dd_cuts,
        winsor=winsor,
        scaling_kwargs=scaling_kwargs,
        min_active_names=min_active_names,
        verbose=True,  # This is a main run, show progress
    )

    if strategy_grid.empty:
        cli.warn("Strategy grid failed; skipping parameter sensitivity analysis.")
        return pd.DataFrame(), strategy_grid

    results = []
    for col in strategy_grid.columns:
        series = strategy_grid[col]
        equity = (1.0 + series).cumprod()
        total_years = len(series) / 12.0
        cagr = (equity.iloc[-1] ** (1 / total_years) - 1) if total_years > 0 else 0
        sd = series.std()
        sharpe = (series.mean() / sd) * np.sqrt(12) if sd > 0 else 0
        dd = equity / equity.cummax() - 1
        max_dd = dd.min()
        results.append({
            "EMA_Period": col[0],
            "Min_Gain_Pct": col[1],
            "CAGR": cagr,
            "Sharpe": sharpe,
            "MaxDD": max_dd
        })

    df = pd.DataFrame(results)
    df.to_csv(os.path.join(MC_DIR, "parameter_sensitivity.csv"), index=False)
    cli.saved(os.path.join(MC_DIR, "parameter_sensitivity.csv"))

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for idx, metric in enumerate(["CAGR", "Sharpe", "MaxDD"]):
        pivot = df.pivot(index="EMA_Period", columns="Min_Gain_Pct", values=metric)
        im = axes[idx].imshow(pivot.values, cmap="RdYlGn" if metric != "MaxDD" else "RdYlGn_r", aspect="auto")
        axes[idx].set_xticks(range(len(pivot.columns)))
        axes[idx].set_yticks(range(len(pivot.index)))
        axes[idx].set_xticklabels(pivot.columns)
        axes[idx].set_yticklabels(pivot.index)
        axes[idx].set_xlabel("Min Gain %")
        axes[idx].set_ylabel("EMA Period")
        axes[idx].set_title(f"{metric} Sensitivity")
        for i in range(len(pivot.index)):
            for j in range(len(pivot.columns)):
                axes[idx].text(j, i, f"{pivot.values[i, j]:.2f}",
                               ha="center", va="center", color="black", fontsize=8)
        plt.colorbar(im, ax=axes[idx])

    plt.tight_layout()
    plt.savefig(os.path.join(MC_DIR, "parameter_sensitivity_heatmap.png"), dpi=200)
    cli.saved(os.path.join(MC_DIR, "parameter_sensitivity_heatmap.png"))
    plt.close()

    _print_table(df.head(10), "Top 10 Parameter Combinations by CAGR")

    return df, strategy_grid


# =====================================================================
# "CHAMELEON" TEST: ADAPTIVE/OPTIMIZING WALK-FORWARD
# =====================================================================
def walk_forward_optimization(
        yearly_map, data_dir, start_date, end_date,
        train_years=6, test_years=2, step_years=1, preload_years=1,
        # --- NEW PARAMS TO PASS DOWN ---
        ema_range: Optional[List[int]] = None,
        gain_range: Optional[List[float]] = None,
        per_side_bps: float = 0.0,
        target_vol: float = 0.30,
        lookback_m: int = 12,
        clamp: Tuple[float, float] = (0.2, 1.0),
        dd_cuts: Tuple[Tuple[float, float], ...] = ((-0.05, 0.9), (-0.10, 0.7)),
        winsor: float = 0.10,
        scaling_kwargs: Optional[Dict[str, Any]] = None,
        min_active_names: int = 0
) -> Tuple[pd.DataFrame, pd.Series]:
    """
    "Chameleon" Test: Realistic walk-forward out-of-sample analysis
    where parameters are *re-optimized* in each training window.

    THIS IS THE FIXED VERSION THAT AVOIDS DATA SNOOPING by building
    one continuous series (IS+OOS) per window *before* scaling.

    Returns:
        - pd.DataFrame: Summary table of results per window
        - pd.Series: The *stitched, final OOS return series*
    """
    cli.section("Walk-Forward 'Chameleon' Test (Optimizing Params)")

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    ema_vals = ema_range or [150, 175, 200, 225, 250]
    gain_vals = gain_range or [20.0, 25.0, 30.0, 35.0, 40.0]
    scaling_kwargs = scaling_kwargs or {}

    results = []
    failures = []
    equity_curves = {}

    # ### NEW: Collect the final OOS return legs ###
    all_oos_returns: List[pd.Series] = []

    current_start = start
    window_id = 0

    while current_start + pd.DateOffset(years=train_years + test_years) <= end:
        window_id += 1
        train_end = current_start + pd.DateOffset(years=train_years)
        test_start = train_end
        test_end = test_start + pd.DateOffset(years=test_years)
        continuous_end = test_end

        train_start_str = current_start.strftime("%Y-%m-%d")
        train_end_str = train_end.strftime("%Y-%m-%d")
        continuous_end_str = continuous_end.strftime("%Y-%m-%d")
        test_start_str = test_start.strftime("%Y-%m-%d")
        test_end_str = test_end.strftime("%Y-%m-%d")

        cli.info(
            f"Window {window_id}: Train={train_start_str} to {train_end_str}, Test={test_start_str} to {test_end_str}")

        try:
            # === 1. FIND BEST PARAMS ON TRAINING DATA ===
            cli.info(f"  [WFA-Chameleon {window_id}] Running parameter grid on training data...")
            train_grid = build_strategy_return_grid(
                yearly_map, data_dir,
                start=train_start_str,
                end=train_end_str,
                ema_range=ema_vals,
                gain_range=gain_vals,
                preload_years=preload_years,
                per_side_bps=per_side_bps,
                target_vol=target_vol,
                lookback_m=lookback_m,
                clamp=clamp,
                dd_cuts=dd_cuts,
                winsor=winsor,
                scaling_kwargs=scaling_kwargs,
                min_active_names=min_active_names,
                verbose=False
            )

            if train_grid.empty:
                raise ValueError("Parameter grid on training data returned empty.")

            train_sharpe = _column_sharpe(train_grid)
            best_params_key = train_sharpe.idxmax()
            best_ema = int(best_params_key[0])
            best_gain = float(best_params_key[1])
            best_is_sharpe = train_sharpe.max()

            cli.info(
                f"  [WFA-Chameleon {window_id}] Best params: EMA={best_ema}, Gain={best_gain:.1f} (Sharpe={best_is_sharpe:.2f})")

            # === 2. BUILD *CONTINUOUS* (IS + OOS) SERIES WITH BEST PARAMS ===
            cli.info(f"  [WFA-Chameleon {window_id}] Building continuous (IS+OOS) returns...")

            port_continuous_raw, _, active_continuous = build_portfolio_returns_from_csv(
                yearly_map, data_dir,
                start=train_start_str,
                end=continuous_end_str,
                ema_period=best_ema,
                min_sell_gain_pct=best_gain,
                min_active_names=min_active_names,
                preload_years=preload_years,
                verbose=False
            )

            if port_continuous_raw.empty:
                raise ValueError("Continuous (IS+OOS) portfolio build returned empty.")

            costs_continuous, _ = estimate_turnover_cost(
                port_continuous_raw.index,
                active_continuous,
                per_side_bps=per_side_bps
            )

            scaled_continuous = apply_vol_target_and_dd_throttle(
                port_continuous_raw - costs_continuous,
                target_vol_yr=target_vol,
                lookback_m=lookback_m,
                clamp=clamp,
                dd_cuts=dd_cuts,
                winsor=winsor,
                verbose=False,
                **scaling_kwargs
            )

            # === 3. SLICE THE *SCALED* SERIES into IS and OOS ===
            cli.info(f"  [WFA-Chameleon {window_id}] Slicing continuous scaled series...")

            if isinstance(scaled_continuous.index, pd.PeriodIndex):
                scaled_continuous.index = scaled_continuous.index.to_timestamp()

            train_mask = (scaled_continuous.index >= train_start_str) & (scaled_continuous.index < train_end_str)
            test_mask = (scaled_continuous.index >= test_start_str) & (scaled_continuous.index < test_end_str)

            train_scaled = scaled_continuous.loc[train_mask]
            test_scaled = scaled_continuous.loc[test_mask]

            all_oos_returns.append(test_scaled)  # <-- Collect the OOS leg

            if train_scaled.empty:
                raise ValueError("IS slice of scaled series is empty.")
            if test_scaled.empty:
                cli.warn(f"  [WFA-Chameleon {window_id}] OOS slice is empty. Reporting 0s.")

            # === 4. CALCULATE METRICS FOR BOTH SLICES ===
            eq_train = (1 + train_scaled).cumprod()
            years_train = len(train_scaled) / 12.0
            cagr_train = (eq_train.iloc[-1] ** (1 / years_train) - 1) if (years_train > 0 and not eq_train.empty) else 0
            sharpe_train = (train_scaled.mean() / train_scaled.std()) * np.sqrt(12) if (
                    train_scaled.std() > 0 and not train_scaled.empty) else 0
            dd_train = (eq_train / eq_train.cummax() - 1).min() if not eq_train.empty else 0

            eq_test = (1 + test_scaled).cumprod()
            years_test = len(test_scaled) / 12.0
            cagr_test = (eq_test.iloc[-1] ** (1 / years_test) - 1) if (years_test > 0 and not eq_test.empty) else 0
            sharpe_test = (test_scaled.mean() / test_scaled.std()) * np.sqrt(12) if (
                    test_scaled.std() > 0 and not test_scaled.empty) else 0
            dd_test = (eq_test / eq_test.cummax() - 1).min() if not eq_test.empty else 0

            results.append({
                "Window": window_id,
                "Train_Start": current_start.date(),
                "Train_End": train_end.date(),
                "Test_Start": test_start.date(),
                "Test_End": test_end.date(),
                "IS_CAGR": cagr_train,
                "OOS_CAGR": cagr_test,
                "IS_Sharpe": sharpe_train,
                "OOS_Sharpe": sharpe_test,
                "IS_MaxDD": dd_train,
                "OOS_MaxDD": dd_test,
                "CAGR_Degradation": cagr_test - cagr_train,
                "Sharpe_Degradation": sharpe_test - sharpe_train,
                "Best_EMA": best_ema,
                "Best_Gain": best_gain,
            })

            equity_curves[f"IS_W{window_id}"] = eq_train
            equity_curves[f"OOS_W{window_id}"] = eq_test

        except Exception as e:
            msg = str(e)
            failures.append({
                "Window": window_id,
                "Train_Start": current_start.date(),
                "Error": msg
            })
            cli.warn(f"Window {window_id} failed: {msg}")

        current_start += pd.DateOffset(years=step_years)

    # === 5. SAVE RESULTS ===
    df_results = pd.DataFrame(results)
    df_results.to_csv(os.path.join(OOS_DIR, "walk_forward_OPTIMIZATION_results.csv"), index=False)
    cli.saved(os.path.join(OOS_DIR, "walk_forward_OPTIMIZATION_results.csv"))

    if failures:
        df_fail = pd.DataFrame(failures)
        df_fail.to_csv(os.path.join(OOS_DIR, "wfa_failures.csv"), index=False)
        cli.saved(os.path.join(OOS_DIR, "wfa_failures.csv"))

    if not df_results.empty:
        fig, axes = plt.subplots(3, 2, figsize=(14, 15))

        # ... (all the plotting logic) ...
        axes[0, 0].plot(df_results["Window"], df_results["IS_CAGR"] * 100, "o-", label="In-Sample", linewidth=2)
        axes[0, 0].plot(df_results["Window"], df_results["OOS_CAGR"] * 100, "s-", label="Out-of-Sample", linewidth=2)
        axes[0, 0].set_title("CAGR: In-Sample vs Out-of-Sample")
        axes[0, 0].set_xlabel("Window")
        axes[0, 0].set_ylabel("CAGR (%)")
        axes[0, 0].legend()
        axes[0, 0].grid(True, alpha=0.3)
        axes[0, 1].plot(df_results["Window"], df_results["IS_Sharpe"], "o-", label="In-Sample", linewidth=2)
        axes[0, 1].plot(df_results["Window"], df_results["OOS_Sharpe"], "s-", label="Out-of-Sample", linewidth=2)
        axes[0, 1].set_title("Sharpe Ratio: In-Sample vs Out-of-Sample")
        axes[0, 1].set_xlabel("Window")
        axes[0, 1].set_ylabel("Sharpe Ratio")
        axes[0, 1].legend()
        axes[0, 1].grid(True, alpha=0.3)
        axes[1, 0].bar(df_results["Window"], df_results["CAGR_Degradation"] * 100, alpha=0.7)
        axes[1, 0].axhline(0, color="r", linestyle="--", linewidth=1)
        axes[1, 0].set_title("CAGR Degradation (OOS - IS)")
        axes[1, 0].set_xlabel("Window")
        axes[1, 0].set_ylabel("Degradation (%)")
        axes[1, 0].grid(True, alpha=0.3)
        axes[1, 1].bar(df_results["Window"], df_results["Sharpe_Degradation"], alpha=0.7)
        axes[1, 1].axhline(0, color="r", linestyle="--", linewidth=1)
        axes[1, 1].set_title("Sharpe Degradation (OOS - IS)")
        axes[1, 1].set_xlabel("Window")
        axes[1, 1].set_ylabel("Degradation")
        axes[1, 1].grid(True, alpha=0.3)
        axes[2, 0].plot(df_results["Window"], df_results["Best_EMA"], "o-", label="Best EMA", linewidth=2)
        axes[2, 0].set_title("Optimal EMA per Window")
        axes[2, 0].set_xlabel("Window")
        axes[2, 0].set_ylabel("EMA Period")
        axes[2, 0].legend()
        axes[2, 0].grid(True, alpha=0.3)
        axes[2, 1].plot(df_results["Window"], df_results["Best_Gain"], "s-", label="Best Gain %", linewidth=2,
                        color="orange")
        axes[2, 1].set_title("Optimal Gain % per Window")
        axes[2, 1].set_xlabel("Window")
        axes[2, 1].set_ylabel("Gain %")
        axes[2, 1].legend()
        axes[2, 1].grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(os.path.join(OOS_DIR, "walk_forward_OPTIMIZATION_analysis.png"), dpi=200)
        cli.saved(os.path.join(OOS_DIR, "walk_forward_OPTIMIZATION_analysis.png"))
        plt.close()

        _print_table(df_results, "Walk-Forward 'Chameleon' (Optimizing) Results")
    else:
        cli.warn("No 'Chameleon' results to plot (all windows failed).")

    if not df_results.empty:
        cli.box("Walk-Forward 'Chameleon' Summary", [
            ("Avg IS CAGR", f"{df_results['IS_CAGR'].mean() * 100:.2f}%"),
            ("Avg OOS CAGR", f"{df_results['OOS_CAGR'].mean() * 100:.2f}%"),
            ("Avg IS Sharpe", f"{df_results['IS_Sharpe'].mean():.2f}"),
            ("Avg OOS Sharpe", f"{df_results['OOS_Sharpe'].mean():.2f}"),
            ("Avg CAGR Degradation", f"{df_results['CAGR_Degradation'].mean() * 100:.2f}%"),
            ("Avg Sharpe Degradation", f"{df_results['Sharpe_Degradation'].mean():.2f}"),
            ("% Windows OOS > 0", f"{(df_results['OOS_CAGR'] > 0).mean() * 100:.1f}%")
        ])

    # === 6. RETURN THE STITCHED OOS SERIES ===
    if not all_oos_returns:
        cli.warn("Chameleon Test produced no OOS return segments.")
        return df_results, pd.Series(dtype=float)

    final_oos_returns = pd.concat(all_oos_returns).sort_index()
    final_oos_returns = final_oos_returns.loc[~final_oos_returns.index.duplicated(keep="first")]

    return df_results, final_oos_returns


# =====================================================================
# "LION" TEST: STATIC PARAMETER WALK-FORWARD VALIDATION
# =====================================================================
def walk_forward_validation(
        # --- Static params for the "Lion" ---
        static_ema: int,
        static_gain: float,
        # --- All the other WFA args ---
        yearly_map, data_dir, start_date, end_date,
        train_years=6, test_years=2, step_years=1, preload_years=1,
        per_side_bps: float = 0.0,
        target_vol: float = 0.30,
        lookback_m: int = 12,
        clamp: Tuple[float, float] = (0.2, 1.0),
        dd_cuts: Tuple[Tuple[float, float], ...] = ((-0.05, 0.9), (-0.10, 0.7)),
        winsor: float = 0.10,
        scaling_kwargs: Optional[Dict[str, Any]] = None,
        min_active_names: int = 0
) -> Tuple[pd.DataFrame, pd.Series]:
    """
    "Lion" Test: Run a walk-forward analysis using ONE STATIC, UNCHANGING
    set of parameters (static_ema, static_gain) for all windows.

    This tests the robustness of a *single* model, not an adaptive methodology.

    Returns:
        - pd.DataFrame: Summary table of results per window
        - pd.Series: The *stitched, final OOS return series*
    """
    cli.section("Walk-Forward 'Lion' Test (Static Params)")
    cli.info(f"Using STATIC params: EMA={static_ema}, Gain={static_gain:.1f}")

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    scaling_kwargs = scaling_kwargs or {}

    results = []
    failures = []

    # ### This is the stitched, final OOS return series ###
    all_oos_returns: List[pd.Series] = []

    current_start = start
    window_id = 0

    while current_start + pd.DateOffset(years=train_years + test_years) <= end:
        window_id += 1
        train_end = current_start + pd.DateOffset(years=train_years)
        test_start = train_end
        test_end = test_start + pd.DateOffset(years=test_years)
        continuous_end = test_end

        train_start_str = current_start.strftime("%Y-%m-%d")
        train_end_str = train_end.strftime("%Y-%m-%d")
        continuous_end_str = continuous_end.strftime("%Y-%m-%d")
        test_start_str = test_start.strftime("%Y-%m-%d")
        test_end_str = test_end.strftime("%Y-%m-%d")

        cli.info(
            f"Window {window_id}: Train={train_start_str} to {train_end_str}, Test={test_start_str} to {test_end_str}")

        try:
            # === 1. BUILD *CONTINUOUS* (IS + OOS) SERIES WITH STATIC PARAMS ===
            cli.info(f"  [WFA-Static {window_id}] Building continuous returns...")

            port_continuous_raw, _, active_continuous = build_portfolio_returns_from_csv(
                yearly_map, data_dir,
                start=train_start_str,
                end=continuous_end_str,
                ema_period=static_ema,  # <-- LION
                min_sell_gain_pct=static_gain,  # <-- LION
                min_active_names=min_active_names,
                preload_years=preload_years,
                verbose=False
            )

            if port_continuous_raw.empty:
                raise ValueError("Continuous (IS+OOS) portfolio build returned empty.")

            costs_continuous, _ = estimate_turnover_cost(
                port_continuous_raw.index,
                active_continuous,
                per_side_bps=per_side_bps
            )

            scaled_continuous = apply_vol_target_and_dd_throttle(
                port_continuous_raw - costs_continuous,
                target_vol_yr=target_vol,
                lookback_m=lookback_m,
                clamp=clamp,
                dd_cuts=dd_cuts,
                winsor=winsor,
                verbose=False,
                **scaling_kwargs
            )

            # === 2. SLICE THE *SCALED* SERIES into IS and OOS ===
            if isinstance(scaled_continuous.index, pd.PeriodIndex):
                scaled_continuous.index = scaled_continuous.index.to_timestamp()

            train_mask = (scaled_continuous.index >= train_start_str) & (scaled_continuous.index < train_end_str)
            test_mask = (scaled_continuous.index >= test_start_str) & (scaled_continuous.index < test_end_str)

            train_scaled = scaled_continuous.loc[train_mask]
            test_scaled = scaled_continuous.loc[test_mask]

            if train_scaled.empty:
                raise ValueError("IS slice of scaled series is empty.")

            all_oos_returns.append(test_scaled)  # <-- Save the OOS leg

            # === 3. CALCULATE METRICS FOR BOTH SLICES ===
            eq_train = (1 + train_scaled).cumprod()
            years_train = len(train_scaled) / 12.0
            cagr_train = (eq_train.iloc[-1] ** (1 / years_train) - 1) if (years_train > 0 and not eq_train.empty) else 0
            sharpe_train = (train_scaled.mean() / train_scaled.std()) * np.sqrt(12) if (
                    train_scaled.std() > 0 and not train_scaled.empty) else 0

            eq_test = (1 + test_scaled).cumprod()
            years_test = len(test_scaled) / 12.0
            cagr_test = (eq_test.iloc[-1] ** (1 / years_test) - 1) if (years_test > 0 and not eq_test.empty) else 0
            sharpe_test = (test_scaled.mean() / test_scaled.std()) * np.sqrt(12) if (
                    test_scaled.std() > 0 and not test_scaled.empty) else 0

            results.append({
                "Window": window_id,
                "Train_Start": current_start.date(),
                "Test_End": test_end.date(),
                "IS_CAGR": cagr_train,
                "OOS_CAGR": cagr_test,
                "IS_Sharpe": sharpe_train,
                "OOS_Sharpe": sharpe_test,
                "Static_EMA": static_ema,
                "Static_Gain": static_gain,
            })

        except Exception as e:
            msg = str(e)
            failures.append({"Window": window_id, "Error": msg})
            cli.warn(f"Window {window_id} failed: {msg}")

        current_start += pd.DateOffset(years=step_years)

    # === 4. SAVE RESULTS ===
    df_results = pd.DataFrame(results)
    df_results.to_csv(os.path.join(OOS_DIR, "walk_forward_VALIDATION_static.csv"), index=False)
    cli.saved(os.path.join(OOS_DIR, "walk_forward_VALIDATION_static.csv"))

    _print_table(df_results, "Walk-Forward 'Lion' Test (Static Params) Results")

    if not df_results.empty:
        cli.box("Walk-Forward 'Lion' Summary", [
            ("Avg IS CAGR", f"{df_results['IS_CAGR'].mean() * 100:.2f}%"),
            ("Avg OOS CAGR", f"{df_results['OOS_CAGR'].mean() * 100:.2f}%"),
            ("Avg IS Sharpe", f"{df_results['IS_Sharpe'].mean():.2f}"),
            ("Avg OOS Sharpe", f"{df_results['OOS_Sharpe'].mean():.2f}"),
            ("% Windows OOS > 0", f"{(df_results['OOS_CAGR'] > 0).mean() * 100:.1f}%")
        ])

    # === 5. RETURN THE STITCHED, FINAL EQUITY CURVE ===
    if not all_oos_returns:
        cli.warn("Lion Test produced no OOS return segments.")
        return df_results, pd.Series(dtype=float)

    final_oos_returns = pd.concat(all_oos_returns).sort_index()
    # Drop duplicate index entries, which can happen at window seams
    final_oos_returns = final_oos_returns.loc[~final_oos_returns.index.duplicated(keep="first")]
    return df_results, final_oos_returns


# =====================================================================
# NEW: LION vs CHAMELEON PLOT
# =====================================================================
def plot_wfa_comparison(
        lion_series: pd.Series,
        chameleon_series: pd.Series,
        initial_capital: float = 10_000.0,
        out_png: str = os.path.join(OOS_DIR, "wfa_showdown_lion_vs_chameleon.png")
):
    """
    Plots the final stitched OOS equity curves for the
    Lion (static) and Chameleon (adaptive) models.
    """
    cli.section("WFA Showdown: Lion (Static) vs. Chameleon (Adaptive)")

    fig, ax = plt.subplots(figsize=(14, 7))

    if lion_series is not None and not lion_series.empty:
        lion_eq = (1.0 + lion_series.fillna(0)).cumprod() * initial_capital
        x_lion = lion_eq.index.to_timestamp() if hasattr(lion_eq.index, "to_timestamp") else lion_eq.index
        ax.plot(x_lion, lion_eq.values, linewidth=2.0, label="Lion (Static)", color="blue")
    else:
        cli.warn("Lion OOS series is empty, skipping plot.")

    if chameleon_series is not None and not chameleon_series.empty:
        chameleon_eq = (1.0 + chameleon_series.fillna(0)).cumprod() * initial_capital
        x_cham = chameleon_eq.index.to_timestamp() if hasattr(chameleon_eq.index,
                                                              "to_timestamp") else chameleon_eq.index
        ax.plot(x_cham, chameleon_eq.values, linewidth=2.5, label="Chameleon (Adaptive)", color="red", linestyle="--")
    else:
        cli.warn("Chameleon OOS series is empty, skipping plot.")

    ax.set_title("WFA Showdown: Lion (Static) vs. Chameleon (Adaptive) OOS Equity", pad=10)
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity ($)")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(True, alpha=0.4, linestyle="--")

    plt.tight_layout()
    plt.savefig(out_png, dpi=200, bbox_inches="tight")
    cli.saved(out_png)
    plt.close()


# =====================================================================
# Dumb universe (CSV-only equal-weight) helpers
# =====================================================================
def load_daily_csv_dumb(symbol, data_dir=DATA_DIR):
    fp, _source_kind = _resolve_daily_source(symbol, data_dir)
    if fp is None:
        raise FileNotFoundError(
            f"{symbol}: CSV not found in {data_dir} or Tiingo fallback {TIINGO_MISSING_DIR}"
        )

    # Read raw (no header) so we can detect the special 3-row header
    raw = pd.read_csv(fp, header=None, engine="python")

    # Detect your 3-row layout exactly:
    # Row0 starts with 'Price', Row1 starts with 'Ticker', Row2 starts with 'Date'
    def _is_three_row_header(df: pd.DataFrame) -> bool:
        if df.shape[0] < 4 or df.shape[1] < 2:
            return False
        c0 = str(df.iat[0, 0]).strip().lower()
        c1 = str(df.iat[1, 0]).strip().lower()
        c2 = str(df.iat[2, 0]).strip().lower()
        return (c0 == "price") and (c1 == "ticker") and (c2 == "date")

    if _is_three_row_header(raw):
        # Build final column names:
        base_cols = ["Date"]
        suffix = [str(x).strip() for x in raw.iloc[0, 1:].tolist()]
        cols = base_cols + suffix

        # Data begins at row 3
        df = raw.iloc[3:, :len(cols)].copy()
        df.columns = cols

        # Coerce date
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=False)
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()

        # Normalize numeric
        for c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        # Map to standard names:
        colmap = {}
        for c in df.columns:
            cl = c.strip().lower()
            if cl == "open":
                colmap[c] = "Open"
            elif cl == "high":
                colmap[c] = "High"
            elif cl == "low":
                colmap[c] = "Low"
            elif cl == "close":
                colmap[c] = "Close"
            elif cl == "volume":
                colmap[c] = "Volume"
            elif cl == "price":
                colmap[c] = "Adj Close"  # treat as total-return close
        df = df.rename(columns=colmap)

        if "Adj Close" not in df.columns:
            if "Close" in df.columns:
                df["Adj Close"] = df["Close"]
            else:
                raise ValueError(f"{symbol}: missing Close/Adj Close after mapping")

        try:
            df.index = df.index.tz_localize(None)
        except Exception:
            pass

        return df

    # Fallback to the general robust loader if not in the special format
    df = pd.read_csv(fp, engine="python")
    dcol = _infer_date_col(df, extra_candidates=list(df.columns[:1]))
    if dcol is None:
        try:
            tmp = pd.read_csv(fp, engine="python", index_col=0, parse_dates=True)
            if isinstance(tmp.index, pd.DatetimeIndex) and len(tmp.index.dropna()) > 0:
                df = tmp.reset_index().rename(columns={tmp.columns[0]: "Date"})
                dcol = "Date"
        except Exception:
            pass
    if dcol is None:
        raise ValueError(f"{symbol}: no date column in CSV")

    df[dcol] = pd.to_datetime(df[dcol], errors="coerce", utc=False)
    df = df.dropna(subset=[dcol]).rename(columns={dcol: "Date"}).set_index("Date").sort_index()
    try:
        df.index = df.index.tz_localize(None)
    except Exception:
        pass
    # pandas 3 removed errors="ignore" from to_numeric.  This loader only
    # needs numeric market-data columns, so coercion is the correct behavior:
    # non-numeric vendor metadata becomes NaN instead of aborting the ticker.
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    if "Adj Close" not in df.columns:
        candidates = [c for c in df.columns if re.sub(r"[\s_]+", "", str(c)).lower() in {"adjclose", "adjustedclose"}]
        if candidates:
            df = df.rename(columns={candidates[0]: "Adj Close"})
        elif "Close" in df.columns:
            df["Adj Close"] = df["Close"]
        else:
            likely = [c for c in df.columns if str(c).lower().startswith("close")]
            if likely:
                df["Adj Close"] = df[likely[0]]
            else:
                raise ValueError(f"{symbol}: missing Close/Adj Close in CSV")

    # Guarantee the one series used by the dumb-universe calculation is
    # numeric and finite enough to resample.
    df["Adj Close"] = pd.to_numeric(df["Adj Close"], errors="coerce")
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=["Adj Close"])
    return df


def monthly_returns_from_daily(df):
    px = df["Adj Close"].dropna()
    m_last = px.resample("ME").last()
    ret = m_last.pct_change(fill_method=None).dropna()
    return ret


def build_dumb_universe_monthly(yearly_map, start, end):
    start_dt, end_dt = pd.to_datetime(start), pd.to_datetime(end)

    # Load all symbols present in the mapping (CSV-only)
    all_syms = sorted({s.upper() for v in yearly_map.values() for s in v})
    sym_mrets = {}
    for s in all_syms:
        try:
            df = load_daily_csv_dumb(s)
            available_start = df.index.min() if not df.empty else pd.NaT
            available_end = df.index.max() if not df.empty else pd.NaT
            df = df[(df.index >= start_dt) & (df.index <= end_dt)]
            if df.empty:
                if pd.notna(available_start) and pd.notna(available_end):
                    cli.warn(
                        f"{s}: no rows in requested window {start_dt.date()} → {end_dt.date()} "
                        f"(local file covers {available_start.date()} → {available_end.date()}) — dumb universe"
                    )
                else:
                    cli.warn(f"{s}: local file contains no usable price rows — dumb universe")
                continue
            sym_mrets[s] = monthly_returns_from_daily(df)
        except Exception as e:
            DATA_QUALITY_EVENTS.append({"Symbol": s, "Stage": "dumb_universe", "Error": str(e)})
            if STRICT_DATA_MODE:
                raise RuntimeError(f"Strict data mode: {s} failed in dumb-universe build — {e}") from e
            cli.warn(f"{s}: skipped in dumb universe — {e}")

    if not sym_mrets:
        raise RuntimeError("No valid tickers for dumb comparison.")

    # Annual rebalance: for each year y+1, hold the list from year y
    years = sorted(y for y in yearly_map.keys() if (y + 1) <= 2100)
    legs = []
    for y in years:
        y1 = y + 1  # <-- CRITICAL: Enforces 1-year lag for the "Dumb" test.
        syms = [s for s in yearly_map[y] if s in sym_mrets]
        if not syms:
            continue
        y_start = pd.Timestamp(f"{y1}-01-01")
        y_end = pd.Timestamp(f"{y1}-12-31")

        slices = []
        for s in syms:
            r = sym_mrets[s]
            r = r[(r.index >= y_start) & (r.index <= y_end)]
            if not r.empty:
                slices.append(r)
        if not slices:
            continue
        yr = pd.concat(slices, axis=1).mean(axis=1)
        legs.append(yr)

    if not legs:
        return pd.Series(dtype=float)

    dumb_m = pd.concat(legs).sort_index()
    dumb_m = dumb_m[(dumb_m.index >= start_dt) & (dumb_m.index <= end_dt)]
    return dumb_m


def load_bench_monthlies(ticker):
    try:
        df = load_daily_csv_dumb(ticker)
        return monthly_returns_from_daily(df)
    except Exception as e:
        cli.warn(f"{ticker}: benchmark load failed — {e}")
        return pd.Series(dtype=float)


# =====================================================================
# Fama–French 5 Factors (daily CSV → monthly factors) + regression
# =====================================================================
def _parse_ff5_daily_csv(ff5_path: str | Path) -> pd.DataFrame:
    """
    Load F-F 5 daily: expects columns like ['date','Mkt-RF','SMB','HML','RMW','CMA','RF'] in PERCENT units.
    Robust to 'Date' / 'DATE' / 'Unnamed: 0' and yyyymmdd integer dates.
    Returns daily DECIMAL returns with a proper DatetimeIndex.
    """
    p = Path(ff5_path)
    if not p.exists():
        raise FileNotFoundError(f"FF5 file not found: {ff5_path}")

    df = pd.read_csv(p)
    # Find/normalize date column
    cand = [c for c in df.columns if str(c).strip().lower() in {"date", "datetime", "unnamed: 0"}]
    if not cand:
        # fallback to first column
        cand = [df.columns[0]]
    dcol = cand[0]
    # Handle yyyymmdd as int or str
    d = df[dcol]
    try:
        # if already YYYY-MM-DD strings, this still works
        dt = pd.to_datetime(d.astype(str), format="%Y%m%d", errors="coerce")
        if dt.isna().all():
            dt = pd.to_datetime(d, errors="coerce")
    except Exception:
        dt = pd.to_datetime(d, errors="coerce")

    df = df.rename(columns=lambda x: str(x).strip())
    df["Date"] = dt
    df = df.dropna(subset=["Date"]).drop_duplicates(subset=["Date"]).sort_values("Date").set_index("Date")

    # Keep standard factor names if present (case-insensitive map)
    colmap = {}
    for want in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]:
        hit = [c for c in df.columns if str(c).replace(" ", "").lower() == want.replace("-", "").lower()]
        if hit:
            colmap[hit[0]] = want
    if not colmap:
        raise ValueError("Could not find Fama–French columns (Mkt-RF, SMB, HML, RMW, CMA, RF).")

    df = df[list(colmap.keys())].rename(columns=colmap)

    # Convert PERCENT → DECIMAL
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce") / 100.0
    df = df.dropna(how="any")

    return df


def _compound_to_monthly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compound daily simple returns to monthly simple returns: (1+r_d)^N - 1
    Applied to each column independently (incl. RF).
    Output index is month-end timestamps (freq M).
    """
    if daily_df.empty:
        return daily_df

    # group by month and compound
    def _comp(x: pd.Series) -> float:
        return float((1.0 + x.dropna()).prod() - 1.0)

    m = daily_df.groupby(pd.Grouper(freq="ME")).agg(_comp)
    m.index = m.index.to_period("M").to_timestamp("M")
    return m


def _align_portfolio_and_factors(port_monthly: pd.Series, ff5_monthly: pd.DataFrame) -> Tuple[pd.Series, pd.DataFrame]:
    """
    Align by month-end. Returns (portfolio monthly returns, factor monthly returns) with same index.
    """
    if not isinstance(port_monthly.index, pd.DatetimeIndex):
        raise ValueError("Portfolio returns must have DatetimeIndex (monthly).")
    p = port_monthly.copy()
    # force month-end stamps
    p.index = p.index.to_period("M").to_timestamp("M")
    f = ff5_monthly.copy()
    common = p.index.intersection(f.index)
    p2 = p.loc[common]
    f2 = f.loc[common]
    return p2, f2


def run_ff5_regression_monthly(port_monthly: pd.Series,
                               ff5_daily_csv: str | Path,
                               nw_lags: int = 6,
                               out_dir: str = CSVA_DIR,
                               label: str = "portfolio") -> pd.DataFrame:
    """
    Performs a Fama-French 5-factor regression using the standard
    academic methodology (monthly returns).

    Methodology:
    1. Loads daily FF5 factors (in percent units) and converts to decimals.
    2. Compounds daily factor returns to **month-end total returns**.
    3. Aligns portfolio monthly returns to the same month-end index.
    4. Calculates strategy **excess returns** (Portfolio - RF).
    5. Regresses excess returns against the 5 factors (Mkt-RF, SMB, HML, RMW, CMA).
    6. Uses **Newey-West (HAC) robust standard errors** for the final model.
    """
    if not _SM_OK:
        raise RuntimeError("statsmodels is not available; install it to run factor regressions.")

    # Load daily FF5 and show a small sample in percent units (as published)
    daily = load_ff5(ff5_daily_csv, freq="daily")
    cli.table((daily[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]] * 100).round(2),
              "Fama–French 5 Factors (Daily %, sample)", max_rows=10)

    # Compound to month-end
    ff5_m = _compound_to_monthly(daily)

    # Align portfolio and factors on month-end index
    pm, fm = _align_portfolio_and_factors(port_monthly, ff5_m)

    # Build regression frame: monthly excess return of the strategy
    y_excess = (pm - fm["RF"]).dropna()

    # Re-align factors to the same filtered index
    fm = fm.loc[y_excess.index]
    X_df = fm[["Mkt-RF", "SMB", "HML", "RMW", "CMA"]].copy()
    X_df = sm.add_constant(X_df)  # adds 'const' column, stays a DataFrame

    # Run the OLS regression.
    # CRITICAL: We use `cov_type="HAC"` to fit the model using
    # Newey-West (HAC) robust standard errors. This corrects for
    # potential autocorrelation and heteroskedasticity in the residuals,
    # making the resulting t-stats and p-values (especially for Alpha)
    # far more robust and credible.
    model = sm.OLS(y_excess, X_df, missing="drop")
    res = model.fit(cov_type="HAC", cov_kwds={"maxlags": int(nw_lags)})

    # Collect results
    idx = ["const", "Mkt-RF", "SMB", "HML", "RMW", "CMA"]
    coefs = pd.Series(res.params, index=idx, name="coef")
    tvals = pd.Series(res.tvalues, index=idx, name="t")
    pvals = pd.Series(res.pvalues, index=idx, name="p")

    # Annualize alpha from monthly
    alpha_m = float(coefs.loc["const"])
    alpha_ann = (1.0 + alpha_m) ** 12 - 1.0

    summ = pd.concat([coefs, tvals, pvals], axis=1)
    summ.loc["R2", "coef"] = res.rsquared
    summ.loc["AdjR2", "coef"] = res.rsquared_adj
    summ.loc["N", "coef"] = len(y_excess)

    # Save artifacts
    os.makedirs(out_dir, exist_ok=True)
    merged = pd.DataFrame({
        "Date": y_excess.index,
        f"{label}_ret": pm.loc[y_excess.index].values,
        f"{label}_excess": y_excess.values,
        "Mkt-RF": fm.loc[y_excess.index, "Mkt-RF"].values,
        "SMB": fm.loc[y_excess.index, "SMB"].values,
        "HML": fm.loc[y_excess.index, "HML"].values,
        "RMW": fm.loc[y_excess.index, "RMW"].values,
        "CMA": fm.loc[y_excess.index, "CMA"].values,
        "RF": fm.loc[y_excess.index, "RF"].values,
    })
    merged.to_csv(os.path.join(out_dir, "factors_merged.csv"), index=False)
    cli.saved(os.path.join(out_dir, "factors_merged.csv"))

    out_csv = os.path.join(out_dir, "ff5_regression_summary.csv")
    summ.to_csv(out_csv)
    cli.saved(out_csv)

    # Pretty print
    cli.section("Fama–French 5 Regression (Monthly, HAC)")
    cli.box("Risk-Adjusted Performance", [
        ("Alpha (monthly)", f"{alpha_m * 100:.3f}%"),
        ("Alpha (annualized)", f"{alpha_ann * 100:.2f}%"),
        ("R²", f"{res.rsquared:.3f}"),
        ("Adj R²", f"{res.rsquared_adj:.3f}"),
        ("Obs", f"{int(summ.loc['N', 'coef']):d}"),
        ("NW lags", str(int(nw_lags))),
    ])

    coef_tbl = pd.DataFrame({
        "Coef": coefs.round(6),
        "t": tvals.round(2),
        "p": pvals.round(4),
    })
    _print_table(coef_tbl, "Coefficients (monthly units)")

    return summ


def _parse_momentum_daily_csv(momentum_path: str | Path) -> pd.DataFrame:
    """Load Kenneth French-style daily momentum factor (percent -> decimal).

    Accepts common labels such as Mom, MOM, WML, and Mom   . Header/comment
    junk is skipped by detecting the first row containing a momentum label.
    """
    p = Path(momentum_path)
    if not p.exists():
        raise FileNotFoundError(f"Momentum factor file not found: {momentum_path}")
    text = p.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    header_line = None
    for i, line in enumerate(lines):
        low = re.sub(r"\s+", "", line.lower())
        if any(tok in low for tok in [",mom", ",wml", "mom,", "wml,"]):
            header_line = i
            break
    if header_line is None:
        # Fall back to ordinary CSV parsing; some downloaded files are already clean.
        df = pd.read_csv(p)
    else:
        df = pd.read_csv(io.StringIO(text), skiprows=header_line)
    if df.empty:
        raise ValueError("Momentum factor CSV is empty.")
    df = df.rename(columns=lambda x: str(x).strip())
    dcol = df.columns[0]
    d = df[dcol]
    dt = pd.to_datetime(d.astype(str).str.strip(), format="%Y%m%d", errors="coerce")
    if dt.isna().all():
        dt = pd.to_datetime(d, errors="coerce")
    mom_col = None
    for c in df.columns[1:]:
        norm = re.sub(r"[^a-z]", "", str(c).lower())
        if norm in {"mom", "momentum", "wml", "umd"}:
            mom_col = c
            break
    if mom_col is None:
        raise ValueError("Could not identify momentum column (Mom/MOM/WML/UMD).")
    out = pd.DataFrame({"Date": dt, "MOM": pd.to_numeric(df[mom_col], errors="coerce") / 100.0})
    out = out.dropna().drop_duplicates(subset=["Date"]).sort_values("Date").set_index("Date")
    return out


def run_ff6_momentum_regression_monthly(port_monthly: pd.Series,
                                         ff5_daily_csv: str | Path,
                                         momentum_daily_csv: str | Path,
                                         nw_lags: int = 6,
                                         out_dir: str = CSVA_DIR,
                                         label: str = "portfolio") -> pd.DataFrame:
    """Fama-French 5 + momentum regression with HAC/Newey-West errors.

    This is the preferred factor check for a momentum-based strategy because an
    FF5-only intercept can otherwise absorb omitted momentum exposure.
    """
    if not _SM_OK:
        raise RuntimeError("statsmodels is not available; install it to run factor regressions.")
    ff5_d = load_ff5(ff5_daily_csv, freq="daily")
    mom_d = _parse_momentum_daily_csv(momentum_daily_csv)
    daily = ff5_d.join(mom_d, how="inner").dropna()
    if daily.empty:
        raise ValueError("FF5 and momentum factor files have no overlapping daily observations.")
    monthly = _compound_to_monthly(daily)
    pm, fm = _align_portfolio_and_factors(port_monthly, monthly)
    y_excess = (pm - fm["RF"]).dropna()
    fm = fm.loc[y_excess.index]
    factor_cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM"]
    X = sm.add_constant(fm[factor_cols].copy())
    res = sm.OLS(y_excess, X, missing="drop").fit(
        cov_type="HAC", cov_kwds={"maxlags": int(nw_lags)}
    )
    idx = ["const"] + factor_cols
    coefs = pd.Series(res.params, index=idx, name="coef")
    tvals = pd.Series(res.tvalues, index=idx, name="t")
    pvals = pd.Series(res.pvalues, index=idx, name="p")
    summ = pd.concat([coefs, tvals, pvals], axis=1)
    summ.loc["R2", "coef"] = res.rsquared
    summ.loc["AdjR2", "coef"] = res.rsquared_adj
    summ.loc["N", "coef"] = len(y_excess)
    alpha_m = float(coefs.loc["const"])
    alpha_ann = (1.0 + alpha_m) ** 12 - 1.0

    os.makedirs(out_dir, exist_ok=True)
    merged = pd.DataFrame(index=y_excess.index)
    merged[f"{label}_ret"] = pm.loc[y_excess.index]
    merged[f"{label}_excess"] = y_excess
    for c in factor_cols + ["RF"]:
        merged[c] = fm.loc[y_excess.index, c]
    merged.to_csv(os.path.join(out_dir, "ff6_momentum_factors_merged.csv"))
    out_csv = os.path.join(out_dir, "ff6_momentum_regression_summary.csv")
    summ.to_csv(out_csv)
    cli.saved(out_csv)
    cli.section("Fama-French 5 + Momentum Regression (Monthly, HAC)")
    cli.box("Momentum-Controlled Risk-Adjusted Performance", [
        ("Alpha (monthly)", f"{alpha_m * 100:.3f}%"),
        ("Alpha (annualized)", f"{alpha_ann * 100:.2f}%"),
        ("Alpha p-value", _fmt_pvalue(float(pvals.loc["const"]))),
        ("MOM loading", f"{float(coefs.loc['MOM']):.3f}"),
        ("MOM p-value", _fmt_pvalue(float(pvals.loc["MOM"]))),
        ("R²", f"{res.rsquared:.3f}"),
        ("Obs", str(len(y_excess))),
    ])
    _print_table(pd.DataFrame({"Coef": coefs.round(6), "t": tvals.round(2), "p": pvals.round(4)}),
                 "FF5 + MOM Coefficients")
    return summ


def _psr_from_returns(returns: pd.Series, benchmark_sharpe_ann: float = 0.0) -> Dict[str, float]:
    """Probabilistic Sharpe Ratio using monthly observations.

    The formula adjusts the sampling distribution of Sharpe for skewness and
    kurtosis. Annualized Sharpe is reported for readability; the probability is
    calculated consistently in monthly units.
    """
    r = pd.Series(returns).dropna().astype(float)
    n = len(r)
    if n < 3 or r.std(ddof=1) <= 0:
        return {"N": n, "SharpeAnn": np.nan, "BenchmarkSharpeAnn": benchmark_sharpe_ann, "PSR": np.nan}
    sr_m = float(r.mean() / r.std(ddof=1))
    sr_star_m = float(benchmark_sharpe_ann) / math.sqrt(12.0)
    skew = float(r.skew())
    kurt = float(r.kurtosis()) + 3.0  # Pearson kurtosis
    denom2 = 1.0 - skew * sr_m + ((kurt - 1.0) / 4.0) * (sr_m ** 2)
    denom = math.sqrt(max(denom2, 1e-12))
    z = (sr_m - sr_star_m) * math.sqrt(max(n - 1, 1)) / denom
    if _SCIPY_OK:
        psr = float(scipy_stats.norm.cdf(z))
    else:
        psr = float(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))
    return {
        "N": float(n), "SharpeAnn": sr_m * math.sqrt(12.0),
        "BenchmarkSharpeAnn": float(benchmark_sharpe_ann), "Skew": skew,
        "Kurtosis": kurt, "Z": z, "PSR": psr,
    }


def run_probabilistic_deflated_sharpe(port_returns: pd.Series,
                                       strategy_grid: Optional[pd.DataFrame],
                                       out_dir: str = DIAG_DIR) -> pd.DataFrame:
    """Save Probabilistic Sharpe and an approximate Deflated Sharpe Ratio.

    DSR uses the dispersion of Sharpe ratios across the actually tested strategy
    grid to raise the benchmark Sharpe for multiple trials. It complements, not
    replaces, White's Reality Check and Hansen SPA.
    """
    base = _psr_from_returns(port_returns, benchmark_sharpe_ann=0.0)
    rows = [{"Metric": "PSR_vs_0", **base}]
    if strategy_grid is not None and not strategy_grid.empty:
        aligned = strategy_grid.dropna(how="any")
        if not aligned.empty and aligned.shape[1] >= 2:
            ann_sharpes = _column_sharpe(aligned).astype(float)
            sr_month = ann_sharpes / math.sqrt(12.0)
            sigma_sr = float(sr_month.std(ddof=1))
            n_trials = int(len(sr_month))
            gamma = 0.5772156649015329
            if _SCIPY_OK and sigma_sr > 0 and n_trials > 1:
                q1 = float(scipy_stats.norm.ppf(1.0 - 1.0 / n_trials))
                q2 = float(scipy_stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e)))
                expected_max_m = sigma_sr * ((1.0 - gamma) * q1 + gamma * q2)
            else:
                expected_max_m = sigma_sr * math.sqrt(max(2.0 * math.log(max(n_trials, 2)), 0.0))
            benchmark_ann = expected_max_m * math.sqrt(12.0)
            dsr = _psr_from_returns(port_returns, benchmark_sharpe_ann=benchmark_ann)
            dsr["Trials"] = n_trials
            dsr["GridSharpeStdMonthly"] = sigma_sr
            rows.append({"Metric": "DSR_multiple_trials", **dsr})
    out = pd.DataFrame(rows)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "probabilistic_deflated_sharpe.csv")
    out.to_csv(path, index=False)
    cli.saved(path)
    cli.section("Probabilistic / Deflated Sharpe")
    display = out.copy()
    if "PSR" in display.columns:
        display["PSR"] = display["PSR"].map(lambda x: f"{x * 100:.2f}%" if pd.notna(x) else "--")
    _print_table(display, "Sharpe Significance Diagnostics")
    return out


# =====================================================================
# Utility
# =====================================================================
def set_global_seed(seed: Optional[int]):
    if seed is not None:
        np.random.seed(seed)


def save_manifest(args_dict: dict, out_dir: str = CSVA_DIR):
    manifest = {
        "run_started": datetime.utcnow().isoformat() + "Z",
        "args": args_dict,
        "script_path": os.path.abspath(__file__),
        "script_sha256": _sha256_file(os.path.abspath(__file__)),
        "platform": platform.platform(),
        "versions": {
            "python": sys.version,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "matplotlib": matplotlib.__version__,
            "statsmodels": getattr(sm, "__version__", "unavailable") if _SM_OK else "unavailable",
            "scipy": getattr(__import__("scipy"), "__version__", "unavailable") if _SCIPY_OK else "unavailable",
        }
    }
    path = os.path.join(out_dir, "run_manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    cli.saved(path)




# =====================================================================
# WINNER-INSPIRED TEST ENGINES (ADDITIVE, NOT CLAIMED AS THE WINNER'S EXACT MODEL)
# =====================================================================
# The uploaded 2026 winner/finalist video showed several investable ideas:
#   * sector rotation,
#   * a large-cap value + momentum composite,
#   * a small-cap growth sleeve,
#   * a diversified ETF sleeve,
#   * dynamic equity exposure based on probability of success / return gap /
#     shortfall risk.
#
# Only ideas that can be backtested without silently introducing obvious
# point-in-time fundamental look-ahead are implemented here.  In particular,
# the exact historical E/P + momentum stock-ranking model is NOT recreated
# from today's fundamentals.  Instead we test:
#   1) a sector-rotation proxy using long-lived SPDR sector ETFs,
#   2) a 17% sector-overlay on the Lion OOS engine,
#   3) a 58/25/17 structural proxy using Lion / IWM / sector-rotation,
#   4) an optional IWD value ballast to test whether the strategy's growth
#      factor tilt benefits from a value sleeve.
#
# These are benchmarks / ablations, not assertions about the winner's exact
# proprietary implementation.
WINNER_SECTOR_ETFS = ("XLK", "XLF", "XLV", "XLI", "XLY", "XLP", "XLE", "XLB", "XLU")
WINNER_DEFENSIVE_SECTORS = ("XLV", "XLP", "XLU")


def _winner_cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(index=getattr(df, "index", None))
    mu = df.mean(axis=1)
    sd = df.std(axis=1, ddof=0).replace(0.0, np.nan)
    return df.sub(mu, axis=0).div(sd, axis=0)


def _winner_cap_and_normalize(weights: pd.Series, cap: float = 0.50) -> pd.Series:
    """Cap sector weights without changing their sign; assumes non-negative inputs."""
    w = weights.clip(lower=0.0).astype(float).copy()
    if w.sum() <= 0:
        return w
    w /= w.sum()
    cap = float(np.clip(cap, 0.05, 1.0))
    # A few deterministic passes are enough for the small sector universe.
    for _ in range(10):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w.loc[over] = cap
        under = ~over
        if not under.any() or w.loc[under].sum() <= 0:
            break
        w.loc[under] += excess * (w.loc[under] / w.loc[under].sum())
    if w.sum() > 0:
        w /= w.sum()
    return w


def build_winner_sector_rotation_proxy(
        data_dir: str,
        *,
        start: Optional[str],
        end: Optional[str],
        top_n: int = 3,
        riskoff_cash: float = 0.40,
        round_trip_cost_bps: float = 0.0,
        flat_etf_fee: float = 25.0,
        reference_capital: float = 300_000.0,
) -> Tuple[pd.Series, pd.DataFrame]:
    """
    Winner-inspired sector-rotation proxy using only point-in-time price history.

    Signal (known before each month's return):
      - prior 11-month momentum, excluding the current month;
      - downside-volatility penalty;
      - SPY 10-month trend / 12-month drawdown as a macro-risk proxy.

    In risk-on months, hold the top N sectors by momentum-minus-downside score.
    In risk-off months, restrict to defensive sectors and keep a configurable
    cash fraction.  Weights are inverse-downside-risk with a 50% sector cap.

    This intentionally does NOT reproduce the winner's macro regression using
    today's revised CPI / unemployment / yield-curve history; doing that without
    vintage data would add revision look-ahead.  If ALFRED/vintage macro data is
    supplied later, the exact macro feature set can be tested separately.
    """
    top_n = max(1, int(top_n))
    riskoff_cash = float(np.clip(riskoff_cash, 0.0, 1.0))

    loaded: Dict[str, pd.Series] = {}
    for ticker in WINNER_SECTOR_ETFS:
        s = _monthly_benchmark_o2o(ticker, data_dir, start, end)
        if s is not None and not s.dropna().empty:
            loaded[ticker] = s.astype(float)

    if len(loaded) < 5:
        cli.warn(
            "Winner sector-rotation proxy skipped: fewer than five sector ETF "
            "return series were available."
        )
        return pd.Series(dtype=float, name="WinnerSectorRotation"), pd.DataFrame()

    rets = pd.concat(loaded, axis=1).sort_index()
    rets = rets.replace([np.inf, -np.inf], np.nan)

    # 12-1 style momentum proxy: eleven completed monthly returns, then shift
    # one month so the current return is never used to choose the current weight.
    mom = (1.0 + rets).rolling(11, min_periods=8).apply(np.prod, raw=True) - 1.0
    mom = mom.shift(1)

    downside = (
        rets.clip(upper=0.0).pow(2)
        .rolling(12, min_periods=8).mean().pow(0.5)
        .shift(1)
    )
    mom_z = _winner_cross_sectional_zscore(mom)
    risk_z = _winner_cross_sectional_zscore(downside)
    score = mom_z - 0.50 * risk_z

    spy = _monthly_benchmark_o2o("SPY", data_dir, start, end)
    if spy is not None and not spy.dropna().empty:
        spy = spy.astype(float).sort_index()
        spy_index = (1.0 + spy.fillna(0.0)).cumprod()
        lagged_index = spy_index.shift(1)
        trend10 = lagged_index > lagged_index.rolling(10, min_periods=8).mean()
        dd12 = lagged_index / lagged_index.rolling(12, min_periods=8).max() - 1.0
        risk_on = (trend10 & (dd12 > -0.15)).reindex(rets.index).fillna(False)
    else:
        risk_on = pd.Series(True, index=rets.index)

    out = pd.Series(index=rets.index, dtype=float, name="WinnerSectorRotation")
    weight_rows: List[Dict[str, Any]] = []
    prev_w = pd.Series(0.0, index=list(rets.columns) + ["CASH"])

    for dt in rets.index:
        ret_row = rets.loc[dt]
        score_row = score.loc[dt]
        mom_row = mom.loc[dt]
        risk_row = downside.loc[dt]

        valid = ret_row.notna() & score_row.notna() & risk_row.notna()
        if not valid.any():
            continue

        if bool(risk_on.get(dt, False)):
            candidates = [c for c in rets.columns if valid.get(c, False)]
            cash_weight = 0.0
            regime = "risk_on"
        else:
            candidates = [c for c in WINNER_DEFENSIVE_SECTORS if c in rets.columns and valid.get(c, False)]
            if not candidates:
                candidates = [c for c in rets.columns if valid.get(c, False)]
            cash_weight = riskoff_cash
            regime = "risk_off"

        # Prefer positive-momentum sectors, but do not force the portfolio into
        # cash solely because every sector had a weak trailing year.
        positive = [c for c in candidates if np.isfinite(mom_row.get(c, np.nan)) and mom_row.get(c, np.nan) > 0]
        ranked_pool = positive if positive else candidates
        ranked = sorted(
            ranked_pool,
            key=lambda c: float(score_row.get(c, -np.inf)),
            reverse=True,
        )[:top_n]
        if not ranked:
            continue

        inv_risk = pd.Series(
            {c: 1.0 / max(float(risk_row.get(c, np.nan)), 1e-6) for c in ranked},
            dtype=float,
        )
        sector_w = _winner_cap_and_normalize(inv_risk, cap=0.50)
        sector_w *= (1.0 - cash_weight)

        cur_w = pd.Series(0.0, index=prev_w.index)
        for c, w in sector_w.items():
            cur_w[c] = float(w)
        cur_w["CASH"] = float(cash_weight)

        # 0.5 * sum(abs(delta weights)) is one-way turnover.  Applying a
        # round-trip bps estimate converts a full switch A->B into one round trip.
        turnover = 0.5 * float((cur_w - prev_w).abs().sum())
        proportional_cost = turnover * float(round_trip_cost_bps) / 10_000.0
        n_etf_transactions = sum(
            int(abs(float(cur_w.get(c, 0.0)) - float(prev_w.get(c, 0.0))) > 1e-12)
            for c in rets.columns
        )
        fixed_fee_cost = n_etf_transactions * float(flat_etf_fee) / max(float(reference_capital), 1.0)
        cost = proportional_cost + fixed_fee_cost

        gross = float(sum(cur_w[c] * float(ret_row[c]) for c in rets.columns if c in cur_w.index and np.isfinite(ret_row[c])))
        out.loc[dt] = gross - cost

        row: Dict[str, Any] = {
            "Date": dt,
            "Regime": regime,
            "Turnover": turnover,
            "GrossReturn": gross,
            "Cost": cost,
            "ETFTransactions": int(n_etf_transactions),
            "FixedETFFeesDollars": float(n_etf_transactions * float(flat_etf_fee)),
            "NetReturn": out.loc[dt],
            "CashWeight": cur_w["CASH"],
        }
        for c in rets.columns:
            row[f"W_{c}"] = float(cur_w.get(c, 0.0))
        weight_rows.append(row)
        prev_w = cur_w

    out = out.dropna()
    weights_df = pd.DataFrame(weight_rows)
    if not weights_df.empty and "Date" in weights_df.columns:
        weights_df = weights_df.set_index("Date").sort_index()
    return out, weights_df


def _winner_blend_series(parts: Dict[str, Tuple[pd.Series, float]], name: str) -> pd.Series:
    clean: Dict[str, pd.Series] = {}
    weights: Dict[str, float] = {}
    for label, (series, weight) in parts.items():
        if series is None or series.dropna().empty or float(weight) <= 0:
            continue
        clean[label] = series.dropna().astype(float)
        weights[label] = float(weight)
    if not clean:
        return pd.Series(dtype=float, name=name)

    aligned = pd.concat(clean, axis=1, join="inner").dropna(how="any")
    if aligned.empty:
        return pd.Series(dtype=float, name=name)
    wsum = sum(weights.values())
    if wsum <= 0:
        return pd.Series(dtype=float, name=name)
    w = pd.Series({k: v / wsum for k, v in weights.items()})
    result = aligned.mul(w, axis=1).sum(axis=1)
    result.name = name
    return result


def build_winner_inspired_growth_proxies(
        *,
        lion_series: Optional[pd.Series],
        data_dir: str,
        start: Optional[str],
        end: Optional[str],
        top_n: int = 3,
        riskoff_cash: float = 0.40,
        per_side_bps: float = 0.0,
        flat_etf_fee: float = 25.0,
        reference_capital: float = 300_000.0,
) -> Tuple[Dict[str, pd.Series], pd.DataFrame]:
    """
    Build additive winner-inspired benchmark engines.

    The 58/25/17 structural proxy mirrors the observed presentation weights only
    as an experiment:
      58% = Stockers Lion OOS engine (large-cap/growth role proxy)
      25% = IWM total return (small-cap market-access proxy)
      17% = winner-inspired sector-rotation ETF proxy

    It is explicitly NOT called the winner's exact strategy.
    """
    proxies: Dict[str, pd.Series] = {}
    sector, sector_weights = build_winner_sector_rotation_proxy(
        data_dir,
        start=start,
        end=end,
        top_n=top_n,
        riskoff_cash=riskoff_cash,
        round_trip_cost_bps=max(0.0, 2.0 * float(per_side_bps)),
        flat_etf_fee=float(flat_etf_fee),
        reference_capital=float(reference_capital),
    )
    if not sector.empty:
        proxies["Winner-inspired sector rotation proxy"] = sector

    iwm = _monthly_benchmark_o2o("IWM", data_dir, start, end)
    iwd = _monthly_benchmark_o2o("IWD", data_dir, start, end)

    if lion_series is not None and not lion_series.dropna().empty:
        lion = lion_series.dropna().astype(float)
        if not sector.empty:
            proxies["Lion + winner sector overlay 83/17"] = _winner_blend_series(
                {"Lion": (lion, 0.83), "Sector": (sector, 0.17)},
                "LionWinnerSector8317",
            )
        if iwm is not None and not iwm.dropna().empty and not sector.empty:
            proxies["Winner 58/25/17 structure proxy"] = _winner_blend_series(
                {"Lion": (lion, 0.58), "IWM": (iwm, 0.25), "Sector": (sector, 0.17)},
                "WinnerStructure582517",
            )
        if iwd is not None and not iwd.dropna().empty:
            proxies["Lion + value ballast 85/15"] = _winner_blend_series(
                {"Lion": (lion, 0.85), "IWD": (iwd, 0.15)},
                "LionValue8515",
            )

    # Remove empty proxies created by non-overlapping dates.
    proxies = {k: v.dropna() for k, v in proxies.items() if v is not None and not v.dropna().empty}
    return proxies, sector_weights


def _winner_plot_engine_equity(return_series: Dict[str, pd.Series], out_png: str,
                               initial_capital: float = 300_000.0) -> None:
    if not return_series:
        return
    plt.figure(figsize=(12, 7))
    plotted = 0
    for label, s in return_series.items():
        if s is None or s.dropna().empty:
            continue
        ss = s.dropna().astype(float)
        equity = float(initial_capital) * (1.0 + ss).cumprod()
        plt.plot(equity.index, equity.values, linewidth=1.8, label=label)
        plotted += 1
    if plotted == 0:
        plt.close()
        return
    plt.title("Winner-Inspired Growth Engine Proxies (Historical Total Return)")
    plt.xlabel("Date")
    plt.ylabel("Growth of starting capital ($)")
    plt.yscale("log")
    plt.grid(True, alpha=0.25, linestyle="--")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close()
    cli.saved(out_png)


# =====================================================================
# 2027 WHARTON GLOBAL YOUTH (LAURA GAO) - HISTORICAL MANDATE REPLAY
# =====================================================================
# This layer is deliberately ADDITIVE: it does not remove or alter any of the
# legacy backtest / Monte-Carlo / WFA / Reality-Check / SPA tests above or below.
#
# Historical replay convention mapped EXACTLY from the official case-study calendar:
#   t = 0 years : 2027 equivalent — invest $300,000 at the beginning of the path
#   t = 1 year  : 2028 equivalent — add $150,000 at the beginning of the year
#   t = 4 years : 2031 equivalent — co-sponsor communication checkpoint
#   t = 6 years : 2033 equivalent — establish the operating reserve, decide facility
#                 contribution, and make the first $50,000 operating payment
#   t = 6..15   : 2033-2042 equivalents — ten annual $50,000 operating payments
#
# Example: if a valid return path begins in Jan-1999, the second contribution is
# Jan-2000, sponsor checkpoint Jan-2003, first withdrawal Jan-2005, and the last
# withdrawal Jan-2014. If the strategy's first investable return is Jan-2000,
# the same mandate shifts one year later automatically.
#
# Competition note: the official case fixes the ten $50k payments in NOMINAL
# dollars and explicitly says personal/capital-gains taxes need not be modeled.
# We therefore keep those official cash flows nominal, but add OPTIONAL tax and
# inflation sensitivity so judges can see implementation robustness.
WGY_INITIAL_CONTRIBUTION = 300_000.0
WGY_SECOND_CONTRIBUTION = 150_000.0
WGY_SECOND_CONTRIBUTION_MONTH = 12
WGY_WITHDRAWAL_START_MONTH = 72
WGY_SPONSOR_LEAD_MONTHS = 24
WGY_OPERATING_PAYMENT = 50_000.0
WGY_OPERATING_PAYMENT_COUNT = 10
WGY_NOMINAL_OPERATING_RESERVE = WGY_OPERATING_PAYMENT * WGY_OPERATING_PAYMENT_COUNT


def _wgy_pv_ladder_reserve(annual_yield: float,
                           payment: float = WGY_OPERATING_PAYMENT,
                           payment_count: int = WGY_OPERATING_PAYMENT_COUNT) -> float:
    """PV at the first-payment date of fixed beginning-of-year payments.

    Payment 1 is due immediately, so it is undiscounted. Payments 2..N are
    discounted by 1..N-1 years. This is a scenario/implementation assumption,
    not an official case-study number. At 0% yield it equals the $500k ceiling.
    """
    y = float(annual_yield)
    if y <= -1.0:
        raise ValueError("Reserve yield must be greater than -100%.")
    return float(sum(float(payment) / ((1.0 + y) ** i) for i in range(int(payment_count))))


def _fmt_money(x: float) -> str:
    if x is None or not np.isfinite(x):
        return "--"
    return f"${x:,.0f}"


def _wgy_event_months(withdrawal_start_month: int = WGY_WITHDRAWAL_START_MONTH,
                      sponsor_lead_months: int = WGY_SPONSOR_LEAD_MONTHS,
                      payment_count: int = WGY_OPERATING_PAYMENT_COUNT) -> Dict[str, Any]:
    withdrawal_start_month = int(withdrawal_start_month)
    sponsor_lead_months = int(sponsor_lead_months)
    sponsor_month = max(0, withdrawal_start_month - sponsor_lead_months)
    payment_months = tuple(withdrawal_start_month + 12 * i for i in range(int(payment_count)))
    # The competition horizon ends with the final required payment. Include the
    # final payment month itself, but do not add an extra post-2042 year.
    horizon_months = payment_months[-1] + 1 if payment_months else withdrawal_start_month + 1
    return {
        "sponsor_month": sponsor_month,
        "withdrawal_start_month": withdrawal_start_month,
        "payment_months": payment_months,
        "horizon_months": horizon_months,
    }


def _wgy_validate_inputs(confidence: float, flex_pct: float, inflation: float,
                         reserve_yield: float, horizon_months: int,
                         second_contribution_month: int,
                         withdrawal_start_month: int,
                         tax_rate: float, tax_realization: float) -> None:
    if not (0.50 <= confidence < 1.0):
        raise ValueError("WGY confidence must be in [0.50, 1.00).")
    if not (0.0 <= flex_pct < 1.0):
        raise ValueError("WGY flexibility percentage must be in [0, 1).")
    if inflation <= -1.0:
        raise ValueError("WGY inflation assumption must be greater than -100%.")
    if reserve_yield <= -1.0:
        raise ValueError("WGY reserve yield must be greater than -100%.")
    if second_contribution_month <= 0:
        raise ValueError("Second contribution must occur after the initial contribution.")
    if withdrawal_start_month <= second_contribution_month:
        raise ValueError("First withdrawal must occur after the second contribution.")
    if not (0.0 <= tax_rate < 1.0):
        raise ValueError("WGY tax rate must be in [0, 1).")
    if not (0.0 <= tax_realization <= 1.0):
        raise ValueError("WGY tax realization fraction must be in [0, 1].")
    events = _wgy_event_months(withdrawal_start_month)
    if horizon_months <= max(events["payment_months"]):
        raise ValueError("WGY horizon is too short to include all ten operating payments.")


def _wgy_stationary_bootstrap_sample(values: np.ndarray, horizon: int,
                                      block_size: int, rng: np.random.Generator) -> np.ndarray:
    """Stationary-block bootstrap a path of an arbitrary requested horizon."""
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ValueError("Cannot bootstrap an empty return series.")
    block = max(1, int(block_size))
    p = 1.0 / block
    out: List[float] = []
    n = len(values)
    while len(out) < horizon:
        start = int(rng.integers(0, n))
        length = int(rng.geometric(p))
        for j in range(length):
            out.append(float(values[(start + j) % n]))
            if len(out) >= horizon:
                break
    return np.asarray(out[:horizon], dtype=float)


def _wgy_required_cagr(*,
                       initial_contribution: float,
                       second_contribution: float,
                       second_year: float,
                       target_year: float,
                       target_value: float) -> float:
    """Solve the nominal CAGR needed to reach a target before withdrawals."""
    target_value = max(float(target_value), 0.0)
    if target_value <= 0:
        return 0.0

    def future_value(r: float) -> float:
        a = initial_contribution * (1.0 + r) ** target_year
        b = second_contribution * (1.0 + r) ** max(target_year - second_year, 0.0)
        return a + b

    lo, hi = -0.95, 2.0
    if future_value(lo) >= target_value:
        return lo
    while future_value(hi) < target_value and hi < 100.0:
        hi *= 2.0
    for _ in range(160):
        mid = (lo + hi) / 2.0
        if future_value(mid) >= target_value:
            hi = mid
        else:
            lo = mid
    return float(hi)


def _wgy_tax_on_growth(growth_before: float, monthly_return: float,
                       tax_rate: float, realization_fraction: float) -> float:
    """
    Approximate taxable realized gains for implementation sensitivity.

    This is intentionally conservative and transparent rather than pretending to
    reconstruct tax lots. Only positive monthly gains are taxable; the
    realization fraction controls how much of those gains are assumed realized.
    """
    if tax_rate <= 0.0 or realization_fraction <= 0.0 or growth_before <= 0.0:
        return 0.0
    gross_gain = growth_before * max(float(monthly_return), 0.0)
    return max(gross_gain * realization_fraction * tax_rate, 0.0)


def _wgy_progressive_lock_target(month: int, sponsor_month: int,
                                 withdrawal_start_month: int,
                                 reserve_target: float) -> float:
    """Annual 1/3 -> 2/3 -> 100% lock schedule over the sponsor lead period."""
    if month < sponsor_month or month > withdrawal_start_month:
        return 0.0
    n_steps = max(1, (withdrawal_start_month - sponsor_month) // 12 + 1)
    step = min(n_steps, max(1, (month - sponsor_month) // 12 + 1))
    return float(reserve_target) * step / n_steps



def _wgy_goal_aware_state(
        *,
        month: int,
        returns: np.ndarray,
        total_assets: float,
        reserve_now: float,
        withdrawal_start_month: int,
        operating_reserve_target: float,
        flex_floor_dollars: float,
        flex_floor_pct: float,
) -> Dict[str, float]:
    """
    Winner-inspired dynamic allocation state.

    The uploaded winning-team presentation changed equity exposure using three
    quantities: probability of success, return gap to target, and shortfall risk.
    This function implements the same *decision architecture* using only
    information available up to the current month.

    It uses trailing log-return mean/volatility as a deliberately transparent
    approximation; it does not peek at future returns in the path.
    """
    total_assets = float(max(total_assets, 0.0))
    reserve_now = float(max(reserve_now, 0.0))
    months_left = max(int(withdrawal_start_month) - int(month), 1)
    flex_req = max(float(flex_floor_dollars), float(flex_floor_pct) * total_assets)
    target_total = float(operating_reserve_target) + flex_req

    if total_assets <= 0.0:
        return {
            "prob_success": 0.0,
            "required_cagr": np.inf,
            "expected_cagr": -1.0,
            "return_gap": -np.inf,
            "shortfall_risk": 1.0,
            "growth_exposure": 1.0,
            "target_lock": reserve_now,
        }

    hist = np.asarray(returns[:max(int(month), 0)], dtype=float)
    hist = hist[np.isfinite(hist)]
    hist = hist[hist > -0.999999]
    if hist.size > 36:
        hist = hist[-36:]

    # With little history, do not pretend we know the distribution: keep risk
    # exposure high and only preserve whatever reserve is already locked.
    if hist.size < 12:
        req_log = max(math.log(max(target_total / total_assets, 1e-12)), 0.0)
        req_cagr = math.exp(req_log * 12.0 / months_left) - 1.0
        return {
            "prob_success": 0.50,
            "required_cagr": req_cagr,
            "expected_cagr": np.nan,
            "return_gap": np.nan,
            "shortfall_risk": 0.50,
            "growth_exposure": 1.0,
            "target_lock": reserve_now,
        }

    log_r = np.log1p(np.clip(hist, -0.95, None))
    mu_m = float(np.mean(log_r))
    sigma_m = float(np.std(log_r, ddof=1)) if log_r.size > 1 else 0.0
    req_log_total = math.log(max(target_total / total_assets, 1e-12))
    req_log_total = max(req_log_total, 0.0)
    req_cagr = math.exp(req_log_total * 12.0 / months_left) - 1.0
    expected_cagr = math.exp(mu_m * 12.0) - 1.0
    return_gap = expected_cagr - req_cagr

    if sigma_m <= 1e-12:
        prob_success = 1.0 if mu_m * months_left >= req_log_total else 0.0
        p05_terminal = total_assets * math.exp(mu_m * months_left)
    else:
        z = (req_log_total - mu_m * months_left) / (sigma_m * math.sqrt(months_left))
        cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        prob_success = float(np.clip(1.0 - cdf, 0.0, 1.0))
        z05 = -1.6448536269514729
        p05_terminal = total_assets * math.exp(
            mu_m * months_left + z05 * sigma_m * math.sqrt(months_left)
        )

    shortfall_risk = max(target_total - p05_terminal, 0.0) / max(target_total, 1e-12)

    # Observed winner logic: behind schedule -> increase growth exposure;
    # ahead of schedule -> reduce risk and preserve gains.
    if prob_success >= 0.98 and return_gap >= 0.0 and shortfall_risk <= 0.05:
        growth_exposure = 0.35
    elif prob_success >= 0.95 and return_gap >= 0.0:
        growth_exposure = 0.50
    elif prob_success >= 0.85:
        growth_exposure = 0.75
    else:
        growth_exposure = 1.00

    desired_safe_assets = total_assets * (1.0 - growth_exposure)
    target_lock = min(float(operating_reserve_target), max(reserve_now, desired_safe_assets))
    return {
        "prob_success": prob_success,
        "required_cagr": req_cagr,
        "expected_cagr": expected_cagr,
        "return_gap": return_gap,
        "shortfall_risk": shortfall_risk,
        "growth_exposure": growth_exposure,
        "target_lock": target_lock,
    }


def simulate_wgy_case_path(
        monthly_returns: np.ndarray,
        *,
        initial_contribution: float = WGY_INITIAL_CONTRIBUTION,
        second_contribution: float = WGY_SECOND_CONTRIBUTION,
        second_contribution_month: int = WGY_SECOND_CONTRIBUTION_MONTH,
        withdrawal_start_month: int = WGY_WITHDRAWAL_START_MONTH,
        sponsor_lead_months: int = WGY_SPONSOR_LEAD_MONTHS,
        operating_reserve_target: float = WGY_NOMINAL_OPERATING_RESERVE,
        flex_floor_dollars: float = 25_000.0,
        flex_floor_pct: float = 0.20,
        reserve_yield_annual: float = 0.0,
        facility_cost: float = 0.0,
        inflation: float = 0.025,
        tax_rate: float = 0.0,
        tax_realization_fraction: float = 0.0,
        allocation_mode: str = "static",
        ratchet_buffer: float = 1.15,
) -> Dict[str, float]:
    """
    Apply the competition mandate DIRECTLY to one historical/bootstrap path.

    Cash-flow convention
    --------------------
    * t=0: +$300k before the first monthly return.
    * t=+1y by default: +$150k before that month's return.
    * t=+6y by default: establish reserve, choose facility contribution, then
      make the first of ten fixed $50k beginning-of-year operating payments.
    * Payments remain nominal $50k: inflation is used for real-dollar reporting,
      not to rewrite the official liability.

    allocation_mode
    ---------------
    static      : keep all investable capital in the growth engine until the
                  first withdrawal date, then ring-fence the reserve.
    progressive : from sponsor-communication date, lock 1/3, 2/3, then 100% of
                  the reserve target annually if assets permit.
    ratchet     : once the plan is sufficiently funded, lock safe assets early;
                  otherwise retain growth exposure.
    goal_aware   : winner-inspired dynamic allocation starting at the sponsor
                  checkpoint. Uses trailing-data probability of success, return
                  gap to the mandate target, and shortfall risk to determine how
                  much capital remains exposed to the growth engine.
    """
    events = _wgy_event_months(withdrawal_start_month, sponsor_lead_months)
    sponsor_month = events["sponsor_month"]
    payment_months = events["payment_months"]
    horizon = events["horizon_months"]

    r = np.asarray(monthly_returns, dtype=float)
    if len(r) < horizon:
        raise ValueError(f"WGY path requires at least {horizon} monthly returns; got {len(r)}.")
    r = r[:horizon]
    if not np.all(np.isfinite(r)):
        raise ValueError("WGY path contains non-finite monthly returns.")

    mode = str(allocation_mode).strip().lower()
    if mode not in {"static", "progressive", "ratchet", "goal_aware"}:
        raise ValueError("allocation_mode must be static, progressive, ratchet, or goal_aware.")

    reserve_mret = (1.0 + reserve_yield_annual) ** (1.0 / 12.0) - 1.0
    growth = 0.0
    reserve = 0.0
    total_external_contributions = 0.0
    total_operating_paid = 0.0
    reserve_topups_from_growth = 0.0
    total_tax_paid = 0.0
    reserve_only_success = True
    operating_commitment_success = True

    portfolio_sponsor = np.nan
    portfolio_pre_withdrawal = np.nan
    reserve_initial_at_withdrawal = np.nan
    facility_contribution = 0.0
    flexibility_retained = np.nan
    first_payment_shortfall = 0.0
    max_payment_shortfall = 0.0
    fully_funded_payment_count = 0
    terminal_assets_after_last_payment = np.nan
    terminal_growth_after_last_payment = np.nan
    terminal_reserve_after_last_payment = np.nan

    # Winner-inspired goal-aware diagnostics, captured at the sponsor checkpoint.
    goal_prob_at_sponsor = np.nan
    goal_required_cagr_at_sponsor = np.nan
    goal_return_gap_at_sponsor = np.nan
    goal_shortfall_at_sponsor = np.nan
    growth_exposure_at_sponsor = np.nan
    goal_lock_events = 0

    # Cash-flow-adjusted shortfall statistics.
    min_total_after_first_withdrawal = np.inf
    min_growth_after_first_withdrawal = np.inf

    for month in range(horizon):
        # Beginning-of-month external contributions.
        if month == 0:
            growth += initial_contribution
            total_external_contributions += initial_contribution
        if month == int(second_contribution_month):
            growth += second_contribution
            total_external_contributions += second_contribution

        total_before = growth + reserve

        if month == sponsor_month:
            portfolio_sponsor = total_before

        # Optional pre-funding / de-risking before the first required payment.
        if month < withdrawal_start_month and month >= sponsor_month and month % 12 == sponsor_month % 12:
            target_lock = reserve
            if mode == "progressive":
                target_lock = max(reserve, _wgy_progressive_lock_target(
                    month, sponsor_month, withdrawal_start_month, operating_reserve_target
                ))
            elif mode == "ratchet":
                flex_req_now = max(float(flex_floor_dollars), float(flex_floor_pct) * total_before)
                plan_floor = float(operating_reserve_target) + flex_req_now
                funded_ratio = total_before / plan_floor if plan_floor > 0 else np.inf
                if funded_ratio >= float(ratchet_buffer):
                    target_lock = float(operating_reserve_target)
                else:
                    # If not yet overfunded, use a gentler progressive minimum.
                    target_lock = max(reserve, 0.5 * _wgy_progressive_lock_target(
                        month, sponsor_month, withdrawal_start_month, operating_reserve_target
                    ))
            elif mode == "goal_aware":
                state = _wgy_goal_aware_state(
                    month=month,
                    returns=r,
                    total_assets=total_before,
                    reserve_now=reserve,
                    withdrawal_start_month=withdrawal_start_month,
                    operating_reserve_target=operating_reserve_target,
                    flex_floor_dollars=flex_floor_dollars,
                    flex_floor_pct=flex_floor_pct,
                )
                target_lock = max(reserve, float(state["target_lock"]))
                goal_lock_events += int(target_lock > reserve + 1e-9)
                if month == sponsor_month:
                    goal_prob_at_sponsor = float(state["prob_success"])
                    goal_required_cagr_at_sponsor = float(state["required_cagr"])
                    goal_return_gap_at_sponsor = float(state["return_gap"]) if np.isfinite(state["return_gap"]) else np.nan
                    goal_shortfall_at_sponsor = float(state["shortfall_risk"])
                    growth_exposure_at_sponsor = float(state["growth_exposure"])

            transfer = min(max(target_lock - reserve, 0.0), max(growth, 0.0))
            growth -= transfer
            reserve += transfer

        # First withdrawal date: finish reserve funding FIRST, then facility decision.
        if month == withdrawal_start_month:
            total_pre = growth + reserve
            portfolio_pre_withdrawal = total_pre

            transfer = min(max(float(operating_reserve_target) - reserve, 0.0), max(growth, 0.0))
            growth -= transfer
            reserve += transfer
            reserve_initial_at_withdrawal = reserve

            # Laura contributes as much as responsibly possible AFTER the
            # operating reserve is protected. Flexibility is defined on the
            # residual surplus, not on the entire portfolio.
            surplus_after_reserve = max(growth, 0.0)
            flexibility_required = min(
                surplus_after_reserve,
                max(float(flex_floor_dollars), float(flex_floor_pct) * surplus_after_reserve),
            )
            facility_contribution = max(0.0, surplus_after_reserve - flexibility_required)
            growth -= facility_contribution
            flexibility_retained = growth

            if reserve_initial_at_withdrawal + 1e-9 < operating_reserve_target:
                reserve_only_success = False

        # Required operating payments happen at the beginning of the applicable month.
        if month in payment_months:
            due = float(WGY_OPERATING_PAYMENT)
            from_reserve = min(max(reserve, 0.0), due)
            reserve -= from_reserve
            paid = from_reserve
            shortfall = due - paid
            if shortfall > 1e-9:
                reserve_only_success = False
                from_growth = min(max(growth, 0.0), shortfall)
                growth -= from_growth
                paid += from_growth
                reserve_topups_from_growth += from_growth
            remaining_shortfall = max(due - paid, 0.0)
            max_payment_shortfall = max(max_payment_shortfall, remaining_shortfall)
            if remaining_shortfall <= 1e-9:
                fully_funded_payment_count += 1
            if month == withdrawal_start_month:
                first_payment_shortfall = remaining_shortfall
            if paid + 1e-9 < due:
                operating_commitment_success = False
            total_operating_paid += paid
            if month == payment_months[-1]:
                terminal_growth_after_last_payment = growth
                terminal_reserve_after_last_payment = reserve
                terminal_assets_after_last_payment = growth + reserve

        # Returns after beginning-of-month cash flows.
        growth_before_return = growth
        gross_growth = growth_before_return * (1.0 + r[month])
        tax = _wgy_tax_on_growth(
            growth_before_return, r[month], float(tax_rate), float(tax_realization_fraction)
        )
        growth = max(gross_growth - tax, 0.0)
        total_tax_paid += tax

        # Reserve interest is cash income, so the tax sensitivity treats positive
        # interest as fully realized. With the conservative 0% reserve default,
        # this contributes no tax drag.
        if reserve > 0.0:
            reserve_interest = reserve * reserve_mret
            reserve_tax = max(reserve_interest, 0.0) * float(tax_rate)
            reserve += reserve_interest - reserve_tax
            total_tax_paid += reserve_tax

        if month >= withdrawal_start_month:
            min_total_after_first_withdrawal = min(min_total_after_first_withdrawal, growth + reserve)
            min_growth_after_first_withdrawal = min(min_growth_after_first_withdrawal, growth)

    final_assets = (float(terminal_assets_after_last_payment)
                    if np.isfinite(terminal_assets_after_last_payment) else growth + reserve)
    final_growth = (float(terminal_growth_after_last_payment)
                    if np.isfinite(terminal_growth_after_last_payment) else growth)
    final_reserve = (float(terminal_reserve_after_last_payment)
                     if np.isfinite(terminal_reserve_after_last_payment) else reserve)
    sponsor_need = max(float(facility_cost) - facility_contribution, 0.0) if facility_cost > 0 else np.nan

    sponsor_years = sponsor_month / 12.0
    withdrawal_years = withdrawal_start_month / 12.0
    horizon_years = payment_months[-1] / 12.0 if payment_months else withdrawal_years
    inf_sponsor = (1.0 + inflation) ** sponsor_years
    inf_withdraw = (1.0 + inflation) ** withdrawal_years
    inf_horizon = (1.0 + inflation) ** horizon_years

    total_required = WGY_OPERATING_PAYMENT * WGY_OPERATING_PAYMENT_COUNT
    return {
        "Portfolio_Sponsor": float(portfolio_sponsor),
        "Portfolio_FirstWithdrawal_PreReserve": float(portfolio_pre_withdrawal),
        "OperatingReserve_AtFirstWithdrawal": float(reserve_initial_at_withdrawal),
        "ReserveFullyFunded_FirstWithdrawal": float(reserve_initial_at_withdrawal + 1e-9 >= operating_reserve_target),
        "FacilityContribution": float(facility_contribution),
        "FlexibilityRetained": float(flexibility_retained),
        "OperatingPaid_Total": float(total_operating_paid),
        "ReserveTopupsFromGrowth": float(reserve_topups_from_growth),
        "ReserveOnlySuccess": float(reserve_only_success and total_operating_paid + 1e-9 >= total_required),
        "OperatingCommitmentSuccess": float(operating_commitment_success and total_operating_paid + 1e-9 >= total_required),
        "FirstPaymentShortfall": float(first_payment_shortfall),
        "MaxPaymentShortfall": float(max_payment_shortfall),
        "TotalOperatingShortfall": float(max(total_required - total_operating_paid, 0.0)),
        "PaymentsFullyFunded": float(fully_funded_payment_count),
        "ReserveEnding": float(final_reserve),
        "GrowthPortfolioEnding": float(final_growth),
        "FinalAssets": float(final_assets),
        "ExternalContributions_Total": float(total_external_contributions),
        "TaxPaid_Total": float(total_tax_paid),
        "CoSponsorNeed": float(sponsor_need) if np.isfinite(sponsor_need) else np.nan,
        "Portfolio_Sponsor_RealStartDollars": float(portfolio_sponsor / inf_sponsor) if inf_sponsor > 0 else np.nan,
        "Portfolio_FirstWithdrawal_RealStartDollars": float(portfolio_pre_withdrawal / inf_withdraw) if inf_withdraw > 0 else np.nan,
        "FacilityContribution_RealStartDollars": float(facility_contribution / inf_withdraw) if inf_withdraw > 0 else np.nan,
        "FinalAssets_RealStartDollars": float(final_assets / inf_horizon) if inf_horizon > 0 else np.nan,
        "MinAssets_AfterFirstWithdrawal": float(min_total_after_first_withdrawal) if np.isfinite(min_total_after_first_withdrawal) else np.nan,
        "MinGrowth_AfterFirstWithdrawal": float(min_growth_after_first_withdrawal) if np.isfinite(min_growth_after_first_withdrawal) else np.nan,
        "GoalAware_ProbSuccess_Sponsor": float(goal_prob_at_sponsor) if np.isfinite(goal_prob_at_sponsor) else np.nan,
        "GoalAware_RequiredCAGR_Sponsor": float(goal_required_cagr_at_sponsor) if np.isfinite(goal_required_cagr_at_sponsor) else np.nan,
        "GoalAware_ReturnGap_Sponsor": float(goal_return_gap_at_sponsor) if np.isfinite(goal_return_gap_at_sponsor) else np.nan,
        "GoalAware_ShortfallRisk_Sponsor": float(goal_shortfall_at_sponsor) if np.isfinite(goal_shortfall_at_sponsor) else np.nan,
        "GoalAware_GrowthExposure_Sponsor": float(growth_exposure_at_sponsor) if np.isfinite(growth_exposure_at_sponsor) else np.nan,
        "GoalAware_LockEvents": float(goal_lock_events),
        "AllocationMode": mode,
    }


def _wgy_historical_paths(
        monthly_returns: pd.Series,
        *,
        stride_months: int,
        second_contribution_month: int,
        withdrawal_start_month: int,
        sponsor_lead_months: int,
        operating_reserve_target: float,
        flex_floor_dollars: float,
        flex_floor_pct: float,
        reserve_yield_annual: float,
        facility_cost: float,
        inflation: float,
        tax_rate: float,
        tax_realization_fraction: float,
        allocation_mode: str,
        ratchet_buffer: float,
) -> pd.DataFrame:
    events = _wgy_event_months(withdrawal_start_month, sponsor_lead_months)
    horizon = events["horizon_months"]
    sponsor_month = events["sponsor_month"]
    payment_months = events["payment_months"]

    s = monthly_returns.dropna().sort_index()
    if len(s) < horizon:
        return pd.DataFrame()

    stride = max(1, int(stride_months))
    rows: List[Dict[str, Any]] = []
    for start_i in range(0, len(s) - horizon + 1, stride):
        window = s.iloc[start_i:start_i + horizon]
        result = simulate_wgy_case_path(
            window.values,
            second_contribution_month=second_contribution_month,
            withdrawal_start_month=withdrawal_start_month,
            sponsor_lead_months=sponsor_lead_months,
            operating_reserve_target=operating_reserve_target,
            flex_floor_dollars=flex_floor_dollars,
            flex_floor_pct=flex_floor_pct,
            reserve_yield_annual=reserve_yield_annual,
            facility_cost=facility_cost,
            inflation=inflation,
            tax_rate=tax_rate,
            tax_realization_fraction=tax_realization_fraction,
            allocation_mode=allocation_mode,
            ratchet_buffer=ratchet_buffer,
        )
        row: Dict[str, Any] = {
            "HistoricalStart": window.index[0],
            "SecondContributionDate": window.index[min(second_contribution_month, len(window) - 1)],
            "SponsorCheckpointDate": window.index[min(sponsor_month, len(window) - 1)],
            "FirstWithdrawalDate": window.index[min(withdrawal_start_month, len(window) - 1)],
            "LastWithdrawalDate": window.index[min(payment_months[-1], len(window) - 1)],
            "HistoricalEnd": window.index[-1],
        }
        row.update(result)
        rows.append(row)
    return pd.DataFrame(rows)


def _wgy_bootstrap_paths(
        monthly_returns: pd.Series,
        *,
        n_sims: int,
        block_size: int,
        seed: int,
        second_contribution_month: int,
        withdrawal_start_month: int,
        sponsor_lead_months: int,
        operating_reserve_target: float,
        flex_floor_dollars: float,
        flex_floor_pct: float,
        reserve_yield_annual: float,
        facility_cost: float,
        inflation: float,
        tax_rate: float,
        tax_realization_fraction: float,
        allocation_mode: str,
        ratchet_buffer: float,
) -> pd.DataFrame:
    values = monthly_returns.dropna().astype(float).values
    if values.size == 0:
        return pd.DataFrame()
    horizon = _wgy_event_months(withdrawal_start_month, sponsor_lead_months)["horizon_months"]
    rng = np.random.default_rng(seed)
    rows: List[Dict[str, Any]] = []
    cli.info(f"Running {n_sims} mandate bootstrap paths ({horizon} months each; mode={allocation_mode})...")
    for sim in range(int(n_sims)):
        if sim % max(1, int(n_sims) // 10) == 0:
            cli.progress(sim, int(n_sims), "WGY mandate paths")
        path = _wgy_stationary_bootstrap_sample(values, horizon, block_size, rng)
        result = simulate_wgy_case_path(
            path,
            second_contribution_month=second_contribution_month,
            withdrawal_start_month=withdrawal_start_month,
            sponsor_lead_months=sponsor_lead_months,
            operating_reserve_target=operating_reserve_target,
            flex_floor_dollars=flex_floor_dollars,
            flex_floor_pct=flex_floor_pct,
            reserve_yield_annual=reserve_yield_annual,
            facility_cost=facility_cost,
            inflation=inflation,
            tax_rate=tax_rate,
            tax_realization_fraction=tax_realization_fraction,
            allocation_mode=allocation_mode,
            ratchet_buffer=ratchet_buffer,
        )
        result["Simulation"] = sim + 1
        rows.append(result)
    cli.progress(int(n_sims), int(n_sims), "WGY mandate paths")
    return pd.DataFrame(rows)


def _wgy_compare_allocation_engines(
        monthly_returns: pd.Series,
        *,
        n_sims: int,
        block_size: int,
        seed: int,
        second_contribution_month: int,
        withdrawal_start_month: int,
        sponsor_lead_months: int,
        operating_reserve_target: float,
        flex_floor_dollars: float,
        flex_floor_pct: float,
        reserve_yield_annual: float,
        facility_cost: float,
        inflation: float,
        tax_rate: float,
        tax_realization_fraction: float,
        ratchet_buffer: float,
) -> pd.DataFrame:
    """Compare static, progressive, ratchet, and winner-inspired goal-aware policies on identical paths."""
    values = monthly_returns.dropna().astype(float).values
    if values.size == 0:
        return pd.DataFrame()
    horizon = _wgy_event_months(withdrawal_start_month, sponsor_lead_months)["horizon_months"]
    rng = np.random.default_rng(seed)
    modes = ("static", "progressive", "ratchet", "goal_aware")
    collectors: Dict[str, List[Dict[str, float]]] = {m: [] for m in modes}

    for sim in range(int(n_sims)):
        path = _wgy_stationary_bootstrap_sample(values, horizon, block_size, rng)
        for mode in modes:
            res = simulate_wgy_case_path(
                path,
                second_contribution_month=second_contribution_month,
                withdrawal_start_month=withdrawal_start_month,
                sponsor_lead_months=sponsor_lead_months,
                operating_reserve_target=operating_reserve_target,
                flex_floor_dollars=flex_floor_dollars,
                flex_floor_pct=flex_floor_pct,
                reserve_yield_annual=reserve_yield_annual,
                facility_cost=facility_cost,
                inflation=inflation,
                tax_rate=tax_rate,
                tax_realization_fraction=tax_realization_fraction,
                allocation_mode=mode,
                ratchet_buffer=ratchet_buffer,
            )
            collectors[mode].append(res)

    rows = []
    for mode in modes:
        df = pd.DataFrame(collectors[mode])
        if df.empty:
            continue
        rows.append({
            "Engine": mode,
            "OperatingSuccessPct": 100.0 * float(df["OperatingCommitmentSuccess"].mean()),
            "ReserveOnlySuccessPct": 100.0 * float(df["ReserveOnlySuccess"].mean()),
            "MedianFacility": float(df["FacilityContribution"].median()),
            "P05Facility": float(df["FacilityContribution"].quantile(0.05)),
            "CVaR05Facility": float(df.loc[df["FacilityContribution"] <= df["FacilityContribution"].quantile(0.05), "FacilityContribution"].mean()),
            "FailurePct": 100.0 * float(1.0 - df["OperatingCommitmentSuccess"].mean()),
            "ExpectedShortfallIfFailure": float(df.loc[df["TotalOperatingShortfall"] > 0, "TotalOperatingShortfall"].mean()) if (df["TotalOperatingShortfall"] > 0).any() else 0.0,
            "MedianFacilityReal": float(df["FacilityContribution_RealStartDollars"].median()),
            "MedianFinalAssets": float(df["FinalAssets"].median()),
            "P05FinalAssets": float(df["FinalAssets"].quantile(0.05)),
            "MedianTaxPaid": float(df["TaxPaid_Total"].median()),
            "MedianReserveTopup": float(df["ReserveTopupsFromGrowth"].median()),
            "MedianSponsorGrowthExposure": float(df["GoalAware_GrowthExposure_Sponsor"].median()) if df["GoalAware_GrowthExposure_Sponsor"].notna().any() else np.nan,
            "MedianSponsorSuccessProb": float(df["GoalAware_ProbSuccess_Sponsor"].median()) if df["GoalAware_ProbSuccess_Sponsor"].notna().any() else np.nan,
            "MedianSponsorReturnGap": float(df["GoalAware_ReturnGap_Sponsor"].median()) if df["GoalAware_ReturnGap_Sponsor"].notna().any() else np.nan,
        })
    return pd.DataFrame(rows)


def _wgy_format_engine_comparison(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["OperatingSuccessPct"] = out["OperatingSuccessPct"].map(lambda x: f"{x:.2f}%")
    out["ReserveOnlySuccessPct"] = out["ReserveOnlySuccessPct"].map(lambda x: f"{x:.2f}%")
    if "FailurePct" in out.columns:
        out["FailurePct"] = out["FailurePct"].map(lambda x: f"{x:.2f}%")
    for c in ["MedianFacility", "P05Facility", "CVaR05Facility", "ExpectedShortfallIfFailure", "MedianFacilityReal", "MedianFinalAssets", "P05FinalAssets", "MedianTaxPaid", "MedianReserveTopup"]:
        if c in out.columns:
            out[c] = out[c].map(_fmt_money)
    if "MedianSponsorGrowthExposure" in out.columns:
        out["MedianSponsorGrowthExposure"] = out["MedianSponsorGrowthExposure"].map(
            lambda x: f"{x * 100:.1f}%" if np.isfinite(x) else "--"
        )
    if "MedianSponsorSuccessProb" in out.columns:
        out["MedianSponsorSuccessProb"] = out["MedianSponsorSuccessProb"].map(
            lambda x: f"{x * 100:.1f}%" if np.isfinite(x) else "--"
        )
    if "MedianSponsorReturnGap" in out.columns:
        out["MedianSponsorReturnGap"] = out["MedianSponsorReturnGap"].map(
            lambda x: f"{x * 100:+.2f}%" if np.isfinite(x) else "--"
        )
    return out


def _wgy_quantile_table(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    metrics = [
        ("Sponsor-checkpoint portfolio", "Portfolio_Sponsor"),
        ("First-withdrawal portfolio (pre-reserve)", "Portfolio_FirstWithdrawal_PreReserve"),
        ("Facility contribution", "FacilityContribution"),
        ("Facility contribution (real start-$)", "FacilityContribution_RealStartDollars"),
        ("Final assets", "FinalAssets"),
        ("Final assets (real start-$)", "FinalAssets_RealStartDollars"),
        ("Tax paid", "TaxPaid_Total"),
    ]
    rows = []
    for label, col in metrics:
        if col not in df.columns:
            continue
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        if vals.empty:
            continue
        rows.append({
            "Metric": label,
            "P05": vals.quantile(0.05),
            "P25": vals.quantile(0.25),
            "Median": vals.quantile(0.50),
            "P75": vals.quantile(0.75),
            "P95": vals.quantile(0.95),
        })
    return pd.DataFrame(rows)


def _wgy_format_quantile_table(tbl: pd.DataFrame) -> pd.DataFrame:
    if tbl is None or tbl.empty:
        return pd.DataFrame()
    out = tbl.copy()
    for c in ["P05", "P25", "Median", "P75", "P95"]:
        if c in out.columns:
            out[c] = out[c].map(_fmt_money)
    return out


def _wgy_historical_timeline_preview(historical: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    if historical is None or historical.empty:
        return pd.DataFrame()
    cols = [
        "HistoricalStart", "SecondContributionDate", "SponsorCheckpointDate",
        "FirstWithdrawalDate", "LastWithdrawalDate", "HistoricalEnd",
        "OperatingCommitmentSuccess", "FacilityContribution", "FinalAssets",
    ]
    out = historical[[c for c in cols if c in historical.columns]].head(int(n)).copy()
    if "OperatingCommitmentSuccess" in out.columns:
        out["OperatingCommitmentSuccess"] = out["OperatingCommitmentSuccess"].map(lambda x: "YES" if x >= 0.5 else "NO")
    for c in ["FacilityContribution", "FinalAssets"]:
        if c in out.columns:
            out[c] = out[c].map(_fmt_money)
    return out


def _wgy_tax_sensitivity(
        monthly_returns: pd.Series,
        *,
        n_sims: int,
        block_size: int,
        seed: int,
        second_contribution_month: int,
        withdrawal_start_month: int,
        sponsor_lead_months: int,
        operating_reserve_target: float,
        flex_floor_dollars: float,
        flex_floor_pct: float,
        reserve_yield_annual: float,
        facility_cost: float,
        inflation: float,
        tax_rate: float,
        tax_realization_fraction: float,
        allocation_mode: str,
        ratchet_buffer: float,
) -> pd.DataFrame:
    """Run official no-tax case and optional implementation tax sensitivity side-by-side."""
    configs = [("Official case: taxes ignored", 0.0, 0.0)]
    if tax_rate > 0.0 and tax_realization_fraction > 0.0:
        configs.append(("Implementation tax sensitivity", tax_rate, tax_realization_fraction))
    rows = []
    for i, (label, tr, rf) in enumerate(configs):
        df = _wgy_bootstrap_paths(
            monthly_returns,
            n_sims=n_sims,
            block_size=block_size,
            seed=seed,  # same seed for apples-to-apples paths
            second_contribution_month=second_contribution_month,
            withdrawal_start_month=withdrawal_start_month,
            sponsor_lead_months=sponsor_lead_months,
            operating_reserve_target=operating_reserve_target,
            flex_floor_dollars=flex_floor_dollars,
            flex_floor_pct=flex_floor_pct,
            reserve_yield_annual=reserve_yield_annual,
            facility_cost=facility_cost,
            inflation=inflation,
            tax_rate=tr,
            tax_realization_fraction=rf,
            allocation_mode=allocation_mode,
            ratchet_buffer=ratchet_buffer,
        )
        if df.empty:
            continue
        rows.append({
            "Scenario": label,
            "TaxRate": tr,
            "RealizationFraction": rf,
            "OperatingSuccessPct": 100.0 * float(df["OperatingCommitmentSuccess"].mean()),
            "MedianFacility": float(df["FacilityContribution"].median()),
            "MedianFacilityReal": float(df["FacilityContribution_RealStartDollars"].median()),
            "MedianFinalAssets": float(df["FinalAssets"].median()),
            "MedianTaxPaid": float(df["TaxPaid_Total"].median()),
        })
    return pd.DataFrame(rows)


def _wgy_format_tax_sensitivity(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["TaxRate"] = out["TaxRate"].map(lambda x: f"{x:.1%}")
    out["RealizationFraction"] = out["RealizationFraction"].map(lambda x: f"{x:.0%}")
    out["OperatingSuccessPct"] = out["OperatingSuccessPct"].map(lambda x: f"{x:.2f}%")
    for c in ["MedianFacility", "MedianFacilityReal", "MedianFinalAssets", "MedianTaxPaid"]:
        out[c] = out[c].map(_fmt_money)
    return out


def _wgy_compare_growth_return_series(
        series_map: Dict[str, pd.Series],
        *,
        n_sims: int, block_size: int, seed: int,
        second_contribution_month: int, withdrawal_start_month: int, sponsor_lead_months: int,
        operating_reserve_target: float, flex_floor_dollars: float, flex_floor_pct: float,
        reserve_yield_annual: float, facility_cost: float, inflation: float,
        tax_rate: float, tax_realization_fraction: float, allocation_mode: str, ratchet_buffer: float,
) -> pd.DataFrame:
    """Compare different GROWTH return engines under the exact same client mandate."""
    rows = []
    for j, (label, ser) in enumerate(series_map.items()):
        if ser is None:
            continue
        s = pd.Series(ser).dropna().astype(float).sort_index()
        if s.empty:
            continue
        sims = _wgy_bootstrap_paths(
            s, n_sims=n_sims, block_size=block_size, seed=seed + j * 1009,
            second_contribution_month=second_contribution_month,
            withdrawal_start_month=withdrawal_start_month, sponsor_lead_months=sponsor_lead_months,
            operating_reserve_target=operating_reserve_target, flex_floor_dollars=flex_floor_dollars,
            flex_floor_pct=flex_floor_pct, reserve_yield_annual=reserve_yield_annual,
            facility_cost=facility_cost, inflation=inflation, tax_rate=tax_rate,
            tax_realization_fraction=tax_realization_fraction, allocation_mode=allocation_mode,
            ratchet_buffer=ratchet_buffer,
        )
        if sims.empty:
            continue
        n = len(s)
        eq = (1.0 + s).cumprod()
        years = n / 12.0
        cagr = float(eq.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 and eq.iloc[-1] > 0 else np.nan
        sd = float(s.std())
        sharpe = float((s.mean() / sd) * np.sqrt(12.0)) if sd > 0 else np.nan
        maxdd = float((eq / eq.cummax() - 1.0).min())
        rows.append({
            "GrowthEngine": label,
            "SourceMonths": n,
            "SourceCAGR": cagr,
            "SourceSharpe": sharpe,
            "SourceMaxDD": maxdd,
            "MandateSuccessPct": 100.0 * float(sims["OperatingCommitmentSuccess"].mean()),
            "ReserveOnlySuccessPct": 100.0 * float(sims["ReserveOnlySuccess"].mean()),
            "MedianFacility": float(sims["FacilityContribution"].median()),
            "P05Facility": float(sims["FacilityContribution"].quantile(0.05)),
            "CVaR05Facility": float(sims.loc[sims["FacilityContribution"] <= sims["FacilityContribution"].quantile(0.05), "FacilityContribution"].mean()),
            "FailurePct": 100.0 * float(1.0 - sims["OperatingCommitmentSuccess"].mean()),
            "ExpectedShortfallIfFailure": float(sims.loc[sims["TotalOperatingShortfall"] > 0, "TotalOperatingShortfall"].mean()) if (sims["TotalOperatingShortfall"] > 0).any() else 0.0,
            "MedianRealFacility": float(sims["FacilityContribution_RealStartDollars"].median()),
            "MedianFinalAssets": float(sims["FinalAssets"].median()),
            "P05FinalAssets": float(sims["FinalAssets"].quantile(0.05)),
        })
    return pd.DataFrame(rows)


def _wgy_format_growth_engine_comparison(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    for c in ["SourceCAGR", "SourceMaxDD"]:
        out[c] = out[c].map(lambda x: f"{x * 100:.2f}%" if np.isfinite(x) else "--")
    out["SourceSharpe"] = out["SourceSharpe"].map(lambda x: f"{x:.2f}" if np.isfinite(x) else "--")
    for c in ["MandateSuccessPct", "ReserveOnlySuccessPct", "FailurePct"]:
        if c in out.columns:
            out[c] = out[c].map(lambda x: f"{x:.2f}%")
    for c in ["MedianFacility", "P05Facility", "CVaR05Facility", "ExpectedShortfallIfFailure", "MedianRealFacility", "MedianFinalAssets", "P05FinalAssets"]:
        out[c] = out[c].map(_fmt_money)
    return out



def _lower_tail_mean(values: pd.Series, q: float = 0.05) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna()
    if vals.empty:
        return np.nan
    cutoff = float(vals.quantile(q))
    tail = vals[vals <= cutoff]
    return float(tail.mean()) if not tail.empty else cutoff


def _wgy_tail_risk_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Client-native downside statistics: failure and cash-flow shortfall, not just drawdown."""
    if df is None or df.empty:
        return pd.DataFrame()
    shortfall = pd.to_numeric(df.get("TotalOperatingShortfall", 0.0), errors="coerce").fillna(0.0)
    failed = shortfall > 1e-9
    facility = pd.to_numeric(df["FacilityContribution"], errors="coerce").dropna()
    final_assets = pd.to_numeric(df["FinalAssets"], errors="coerce").dropna()
    rows = [
        ("Probability any operating-payment failure", float(failed.mean())),
        ("Expected operating shortfall (all paths)", float(shortfall.mean())),
        ("Expected shortfall conditional on failure", float(shortfall[failed].mean()) if failed.any() else 0.0),
        ("Worst operating shortfall", float(shortfall.max()) if len(shortfall) else 0.0),
        ("Facility VaR 5%", float(facility.quantile(0.05)) if not facility.empty else np.nan),
        ("Facility CVaR / Expected Shortfall 5%", _lower_tail_mean(facility, 0.05)),
        ("Final-assets VaR 5%", float(final_assets.quantile(0.05)) if not final_assets.empty else np.nan),
        ("Final-assets CVaR 5%", _lower_tail_mean(final_assets, 0.05)),
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value"])


def _beta_jeffreys_summary(successes: float, trials: float,
                            credibility: float = 0.95,
                            threshold: float = 0.95) -> Dict[str, float]:
    successes = float(max(successes, 0.0))
    trials = float(max(trials, successes, 0.0))
    a = 0.5 + successes
    b = 0.5 + max(trials - successes, 0.0)
    mean = a / (a + b)
    alpha = (1.0 - credibility) / 2.0
    if _SCIPY_OK:
        lo = float(scipy_stats.beta.ppf(alpha, a, b))
        hi = float(scipy_stats.beta.ppf(1.0 - alpha, a, b))
        p_above = float(1.0 - scipy_stats.beta.cdf(threshold, a, b))
    else:
        rng = np.random.default_rng(12345)
        draws = rng.beta(a, b, size=200000)
        lo, hi = map(float, np.quantile(draws, [alpha, 1.0 - alpha]))
        p_above = float(np.mean(draws >= threshold))
    return {
        "Successes": successes, "Trials": trials, "PosteriorAlpha": a, "PosteriorBeta": b,
        "PosteriorMean": mean, "CredibleLow": lo, "CredibleHigh": hi,
        f"P_Reliability_ge_{threshold:.2f}": p_above,
    }


def _wgy_bayesian_reliability(historical: pd.DataFrame, *,
                              stride_months: int, horizon_months: int,
                              credibility: float = 0.95,
                              threshold: float = 0.95) -> pd.DataFrame:
    """Jeffreys-prior reliability intervals, including an overlap-adjusted sensitivity.

    Annual rolling historical mandate paths overlap heavily. Treating every start
    as independent would overstate evidence. The second row conservatively shrinks
    the effective sample size by stride / mandate-horizon.
    """
    if historical is None or historical.empty:
        return pd.DataFrame()
    x = pd.to_numeric(historical["OperatingCommitmentSuccess"], errors="coerce").dropna()
    if x.empty:
        return pd.DataFrame()
    n = float(len(x))
    k = float(x.sum())
    raw = _beta_jeffreys_summary(k, n, credibility, threshold)
    raw["Method"] = "Naive rolling-cohort posterior"
    overlap_fraction = min(1.0, max(float(stride_months), 1.0) / max(float(horizon_months), 1.0))
    n_eff = max(1.0, n * overlap_fraction)
    k_eff = float(x.mean()) * n_eff
    adj = _beta_jeffreys_summary(k_eff, n_eff, credibility, threshold)
    adj["Method"] = "Overlap-adjusted posterior sensitivity"
    out = pd.DataFrame([raw, adj])
    cols = ["Method", "Successes", "Trials", "PosteriorMean", "CredibleLow", "CredibleHigh",
            f"P_Reliability_ge_{threshold:.2f}", "PosteriorAlpha", "PosteriorBeta"]
    return out[[c for c in cols if c in out.columns]]


def _wgy_frontier_metrics(sims: pd.DataFrame, safe_weight: float) -> Dict[str, float]:
    facility = sims["FacilityContribution"]
    shortfall = sims["TotalOperatingShortfall"]
    return {
        "SafeWeight": float(safe_weight),
        "GrowthWeight": float(1.0 - safe_weight),
        "OperatingSuccessPct": 100.0 * float(sims["OperatingCommitmentSuccess"].mean()),
        "ReserveOnlySuccessPct": 100.0 * float(sims["ReserveOnlySuccess"].mean()),
        "MedianFacility": float(facility.median()),
        "P05Facility": float(facility.quantile(0.05)),
        "CVaR05Facility": _lower_tail_mean(facility, 0.05),
        "MedianFinalAssets": float(sims["FinalAssets"].median()),
        "P05FinalAssets": float(sims["FinalAssets"].quantile(0.05)),
        "FailurePct": 100.0 * float(1.0 - sims["OperatingCommitmentSuccess"].mean()),
        "ExpectedShortfallIfFailure": float(shortfall[shortfall > 0].mean()) if (shortfall > 0).any() else 0.0,
    }


def _wgy_mandate_efficient_frontier(
        monthly_returns: pd.Series, *, n_sims: int, eval_sims: int,
        block_size: int, seed: int, safe_yield_annual: float,
        min_safe_weight: float, max_safe_weight: float, safe_step: float,
        target_success: float,
        second_contribution_month: int, withdrawal_start_month: int,
        sponsor_lead_months: int, operating_reserve_target: float,
        flex_floor_dollars: float, flex_floor_pct: float,
        reserve_yield_annual: float, facility_cost: float, inflation: float,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Select a safe/growth risk budget on one seed and evaluate it on another.

    The safe/growth mix is monthly rebalanced before the explicit operating
    reserve is carved out. Selection uses the official no-tax case to avoid
    optimizing on a tax assumption the competition does not require.
    """
    s = pd.Series(monthly_returns).dropna().astype(float)
    if s.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    horizon = _wgy_event_months(withdrawal_start_month, sponsor_lead_months)["horizon_months"]
    safe_mret = (1.0 + float(safe_yield_annual)) ** (1.0 / 12.0) - 1.0
    weights = np.arange(float(min_safe_weight), float(max_safe_weight) + 0.5 * float(safe_step), float(safe_step))
    weights = np.clip(weights, 0.0, 1.0)
    rng = np.random.default_rng(seed)
    base_paths = [_wgy_stationary_bootstrap_sample(s.values, horizon, block_size, rng) for _ in range(int(n_sims))]
    rows = []
    for w in weights:
        results = []
        for path in base_paths:
            blend = (1.0 - w) * path + w * safe_mret
            results.append(simulate_wgy_case_path(
                blend, second_contribution_month=second_contribution_month,
                withdrawal_start_month=withdrawal_start_month,
                sponsor_lead_months=sponsor_lead_months,
                operating_reserve_target=operating_reserve_target,
                flex_floor_dollars=flex_floor_dollars, flex_floor_pct=flex_floor_pct,
                reserve_yield_annual=reserve_yield_annual, facility_cost=facility_cost,
                inflation=inflation, tax_rate=0.0, tax_realization_fraction=0.0,
                allocation_mode="static",
            ))
        rows.append(_wgy_frontier_metrics(pd.DataFrame(results), float(w)))
    selection = pd.DataFrame(rows).sort_values("SafeWeight").reset_index(drop=True)
    feasible = selection[selection["OperatingSuccessPct"] >= 100.0 * float(target_success)]
    if not feasible.empty:
        chosen = feasible.sort_values(["MedianFacility", "P05Facility"], ascending=False).iloc[0]
    else:
        chosen = selection.sort_values(["OperatingSuccessPct", "P05Facility", "MedianFacility"], ascending=False).iloc[0]
    selection["Selected"] = np.isclose(selection["SafeWeight"], float(chosen["SafeWeight"]))

    # Independent evaluation seed: the chosen allocation is not judged on the same paths used to choose it.
    rng2 = np.random.default_rng(seed + 99173)
    eval_results = []
    w = float(chosen["SafeWeight"])
    for _ in range(int(eval_sims)):
        path = _wgy_stationary_bootstrap_sample(s.values, horizon, block_size, rng2)
        blend = (1.0 - w) * path + w * safe_mret
        eval_results.append(simulate_wgy_case_path(
            blend, second_contribution_month=second_contribution_month,
            withdrawal_start_month=withdrawal_start_month,
            sponsor_lead_months=sponsor_lead_months,
            operating_reserve_target=operating_reserve_target,
            flex_floor_dollars=flex_floor_dollars, flex_floor_pct=flex_floor_pct,
            reserve_yield_annual=reserve_yield_annual, facility_cost=facility_cost,
            inflation=inflation, tax_rate=0.0, tax_realization_fraction=0.0,
            allocation_mode="static",
        ))
    eval_df = pd.DataFrame([_wgy_frontier_metrics(pd.DataFrame(eval_results), w)])
    eval_df["SelectionTargetSuccessPct"] = 100.0 * float(target_success)
    eval_df["SelectionSims"] = int(n_sims)
    eval_df["EvaluationSims"] = int(eval_sims)
    eval_df["SafeYieldAnnual"] = float(safe_yield_annual)
    selection_feasible = not feasible.empty
    eval_df["SelectionConstraintFeasible"] = bool(selection_feasible)
    eval_df["IndependentEvalMeetsTarget"] = bool(
        float(eval_df.iloc[0]["OperatingSuccessPct"]) >= 100.0 * float(target_success)
    )
    if not selection_feasible:
        cli.warn(
            f"Mandate frontier found NO allocation meeting the {target_success*100:.1f}% selection target; "
            "the exported sleeve mix is the best tested fallback, not a constraint-satisfying optimum."
        )

    # Translate the mathematically selected safe/growth split into the four client-facing
    # sleeves while preserving the 35:10 split inside safe assets and 40:15 split inside growth.
    safe = w
    growth = 1.0 - safe
    sleeve = pd.DataFrame([
        {"Sleeve": "Vault", "Weight": safe * (35.0 / 45.0), "Bucket": "Safe / liabilities"},
        {"Sleeve": "Signal", "Weight": safe * (10.0 / 45.0), "Bucket": "Safe / flexibility"},
        {"Sleeve": "Engine", "Weight": growth * (40.0 / 55.0), "Bucket": "Growth"},
        {"Sleeve": "Canvas", "Weight": growth * (15.0 / 55.0), "Bucket": "Growth / mission"},
    ])
    sleeve["WeightPct"] = 100.0 * sleeve["Weight"]
    sleeve["ConstraintFeasible"] = bool(selection_feasible)
    sleeve["TargetSuccessPct"] = 100.0 * float(target_success)
    sleeve["IndependentEvalSuccessPct"] = float(eval_df.iloc[0]["OperatingSuccessPct"])
    return selection, eval_df, sleeve


def _wgy_crisis_templates(monthly_returns: pd.Series) -> Dict[str, np.ndarray]:
    s = pd.Series(monthly_returns).dropna().astype(float).sort_index()
    specs = {
        "Dot-com strategy experience": ("2000-03-01", "2002-10-31"),
        "GFC strategy experience": ("2007-10-01", "2009-03-31"),
        "COVID shock strategy experience": ("2020-02-01", "2020-04-30"),
        "2022 rate-shock strategy experience": ("2022-01-01", "2022-12-31"),
    }
    out: Dict[str, np.ndarray] = {}
    for label, (a, b) in specs.items():
        x = s[(s.index >= pd.Timestamp(a)) & (s.index <= pd.Timestamp(b))].values
        if len(x):
            out[label] = np.asarray(x, dtype=float)
    synthetic_m = (0.70 ** (1.0 / 3.0)) - 1.0
    out["Synthetic -30% over 3 months"] = np.array([synthetic_m] * 3, dtype=float)
    return out


def _wgy_crisis_timing_stress(monthly_returns: pd.Series, *,
                              n_sims: int, block_size: int, seed: int,
                              leads_months: List[int],
                              second_contribution_month: int, withdrawal_start_month: int,
                              sponsor_lead_months: int, operating_reserve_target: float,
                              flex_floor_dollars: float, flex_floor_pct: float,
                              reserve_yield_annual: float, facility_cost: float, inflation: float,
                              allocation_mode: str, ratchet_buffer: float) -> pd.DataFrame:
    """Splice real strategy crisis sequences into randomized paths at bad client-timing points."""
    s = pd.Series(monthly_returns).dropna().astype(float).sort_index()
    if s.empty:
        return pd.DataFrame()
    horizon = _wgy_event_months(withdrawal_start_month, sponsor_lead_months)["horizon_months"]
    templates = _wgy_crisis_templates(s)
    rng = np.random.default_rng(seed)
    base_paths = [_wgy_stationary_bootstrap_sample(s.values, horizon, block_size, rng) for _ in range(int(n_sims))]
    rows = []
    for label, shock in templates.items():
        shock = np.asarray(shock, dtype=float)
        cum_shock = float(np.prod(1.0 + shock) - 1.0)
        for lead in leads_months:
            end = int(withdrawal_start_month) - int(lead)
            start = end - len(shock)
            if start < 0 or end > horizon:
                continue
            results = []
            for base in base_paths:
                path = base.copy()
                path[start:end] = shock
                results.append(simulate_wgy_case_path(
                    path, second_contribution_month=second_contribution_month,
                    withdrawal_start_month=withdrawal_start_month,
                    sponsor_lead_months=sponsor_lead_months,
                    operating_reserve_target=operating_reserve_target,
                    flex_floor_dollars=flex_floor_dollars, flex_floor_pct=flex_floor_pct,
                    reserve_yield_annual=reserve_yield_annual, facility_cost=facility_cost,
                    inflation=inflation, tax_rate=0.0, tax_realization_fraction=0.0,
                    allocation_mode=allocation_mode, ratchet_buffer=ratchet_buffer,
                ))
            df = pd.DataFrame(results)
            shortfall = df["TotalOperatingShortfall"]
            rows.append({
                "Scenario": label, "LeadMonthsBeforeFirstPayment": int(lead),
                "ShockMonths": int(len(shock)), "TemplateCumulativeReturn": cum_shock,
                "OperatingSuccessPct": 100.0 * float(df["OperatingCommitmentSuccess"].mean()),
                "ReserveOnlySuccessPct": 100.0 * float(df["ReserveOnlySuccess"].mean()),
                "MedianFacility": float(df["FacilityContribution"].median()),
                "P05Facility": float(df["FacilityContribution"].quantile(0.05)),
                "CVaR05Facility": _lower_tail_mean(df["FacilityContribution"], 0.05),
                "ExpectedShortfallIfFailure": float(shortfall[shortfall > 0].mean()) if (shortfall > 0).any() else 0.0,
                "WorstShortfall": float(shortfall.max()),
            })
    return pd.DataFrame(rows)


def _save_wgy_advanced_diagnostics(monthly_returns: pd.Series, historical: pd.DataFrame, mc: pd.DataFrame, *,
                                   hist_stride: int, withdrawal_start_month: int, sponsor_lead_months: int,
                                   frontier_sims: int, frontier_eval_sims: int, frontier_safe_yield: float,
                                   frontier_min_safe: float, frontier_max_safe: float, frontier_step: float,
                                   frontier_target_success: float, crisis_sims: int,
                                   second_contribution_month: int, operating_reserve_target: float,
                                   flex_floor_dollars: float, flex_floor_pct: float,
                                   reserve_yield_annual: float, facility_cost: float, inflation: float,
                                   allocation_mode: str, ratchet_buffer: float, block_size: int, seed: int,
                                   skip_frontier: bool = False, skip_crisis: bool = False) -> Dict[str, Any]:
    outputs: Dict[str, Any] = {}
    tail = _wgy_tail_risk_summary(mc)
    if not tail.empty:
        path = os.path.join(WGY_DIR, "wgy_tail_risk_shortfall.csv")
        tail.to_csv(path, index=False); cli.saved(path)
        display = tail.copy()
        display["Value"] = display.apply(
            lambda row: f"{row['Value'] * 100:.2f}%" if "Probability" in str(row["Metric"]) else _fmt_money(float(row["Value"])), axis=1)
        cli.table(display, "Client-Native Tail Risk / Cash-Flow Shortfall")
        outputs["tail_risk"] = tail

    horizon = _wgy_event_months(withdrawal_start_month, sponsor_lead_months)["horizon_months"]
    bayes = _wgy_bayesian_reliability(historical, stride_months=hist_stride, horizon_months=horizon)
    if not bayes.empty:
        path = os.path.join(WGY_DIR, "wgy_bayesian_reliability.csv")
        bayes.to_csv(path, index=False); cli.saved(path)
        disp = bayes.copy()
        for c in ["PosteriorMean", "CredibleLow", "CredibleHigh", "P_Reliability_ge_0.95"]:
            if c in disp.columns:
                disp[c] = disp[c].map(lambda x: f"{100*x:.1f}%")
        cli.table(disp, "Bayesian Funding-Reliability Sensitivity")
        outputs["bayesian_reliability"] = bayes

    if not skip_frontier:
        selection, evaluation, sleeve = _wgy_mandate_efficient_frontier(
            monthly_returns, n_sims=frontier_sims, eval_sims=frontier_eval_sims,
            block_size=block_size, seed=seed + 4401, safe_yield_annual=frontier_safe_yield,
            min_safe_weight=frontier_min_safe, max_safe_weight=frontier_max_safe,
            safe_step=frontier_step, target_success=frontier_target_success,
            second_contribution_month=second_contribution_month, withdrawal_start_month=withdrawal_start_month,
            sponsor_lead_months=sponsor_lead_months, operating_reserve_target=operating_reserve_target,
            flex_floor_dollars=flex_floor_dollars, flex_floor_pct=flex_floor_pct,
            reserve_yield_annual=reserve_yield_annual, facility_cost=facility_cost, inflation=inflation,
        )
        if not selection.empty:
            p1 = os.path.join(WGY_DIR, "wgy_mandate_efficient_frontier_selection.csv")
            p2 = os.path.join(WGY_DIR, "wgy_mandate_efficient_frontier_evaluation.csv")
            p3 = os.path.join(WGY_DIR, "wgy_EXPERIMENTAL_frontier_sleeve_translation.csv")
            selection.to_csv(p1, index=False); evaluation.to_csv(p2, index=False); sleeve.to_csv(p3, index=False)
            cli.saved(p1); cli.saved(p2); cli.saved(p3)
            cli.table(evaluation, "Mandate Efficient Frontier: Independent Evaluation")
            cli.table(sleeve[["Sleeve", "WeightPct", "Bucket"]].round({"WeightPct": 2}), "EXPERIMENTAL Frontier Translation (NOT Final Client Allocation)")
            outputs["frontier_selection"] = selection; outputs["frontier_evaluation"] = evaluation; outputs["sleeve_allocation"] = sleeve
            try:
                fig, ax = plt.subplots(figsize=(9, 6))
                ax.plot(selection["FailurePct"], selection["MedianFacility"], marker="o")
                chosen = selection[selection["Selected"]]
                if not chosen.empty:
                    ax.scatter(chosen["FailurePct"], chosen["MedianFacility"], s=100, marker="*")
                ax.set_xlabel("Probability of operating-payment failure (%)")
                ax.set_ylabel("Median facility contribution ($)")
                ax.set_title("Mandate Efficient Frontier: Growth vs Funding Certainty")
                ax.grid(True, alpha=0.25)
                plt.tight_layout(); png=os.path.join(WGY_DIR, "wgy_mandate_efficient_frontier.png")
                plt.savefig(png, dpi=200, bbox_inches="tight"); plt.close(fig); cli.saved(png)
            except Exception as exc:
                cli.warn(f"Frontier plot failed — {exc}")

    if not skip_crisis:
        crisis = _wgy_crisis_timing_stress(
            monthly_returns, n_sims=crisis_sims, block_size=block_size, seed=seed + 6601,
            leads_months=[24, 12, 6, 0], second_contribution_month=second_contribution_month,
            withdrawal_start_month=withdrawal_start_month, sponsor_lead_months=sponsor_lead_months,
            operating_reserve_target=operating_reserve_target, flex_floor_dollars=flex_floor_dollars,
            flex_floor_pct=flex_floor_pct, reserve_yield_annual=reserve_yield_annual,
            facility_cost=facility_cost, inflation=inflation, allocation_mode=allocation_mode,
            ratchet_buffer=ratchet_buffer,
        )
        if not crisis.empty:
            p = os.path.join(WGY_DIR, "wgy_crisis_timing_stress.csv")
            crisis.to_csv(p, index=False); cli.saved(p)
            cli.table(crisis.head(20), "Bad-Timing Crisis Stress Tests", max_rows=20)
            outputs["crisis_timing"] = crisis
    return outputs



# =====================================================================
# FINAL CLIENT ARCHITECTURE — TWO-CLOCK STUDIO
# =====================================================================
# This is the portfolio Laura is actually presented with. The quant backtest
# validates Opportunity Lab; it is NOT extrapolated as the whole portfolio.
FINAL_RUNWAY_FLOORS = {
    0: 150_000.0,   # beginning 2027
    12: 250_000.0,  # beginning 2028, after contribution
    24: 320_000.0,  # 2029
    36: 380_000.0,  # 2030
    48: 430_000.0,  # 2031 sponsor window
    60: 460_000.0,  # 2032
}
FINAL_RISK_SPLIT = {
    "CoreStudio": 0.55,
    "CreativeEcosystem": 0.25,
    "OpportunityLab": 0.20,
}
FINAL_CAPITAL_MARKET_ASSUMPTIONS = {
    "RunwayYield": 0.030,
    "CoreMean": 0.070, "CoreVol": 0.150, "CoreDrag": 0.0015,
    "CreativeMean": 0.075, "CreativeVol": 0.170, "CreativeDrag": 0.0020,
    "OpportunityMean": 0.090, "OpportunityVol": 0.220, "OpportunityDrag": 0.0060,
    "ExtraImplementationDrag": 0.0025,
    "CorrCoreCreative": 0.78,
    "CorrCoreOpportunity": 0.72,
    "CorrCreativeOpportunity": 0.70,
}


def _annual_to_monthly_return(r: float) -> float:
    return (1.0 + float(r)) ** (1.0 / 12.0) - 1.0


def _safe_cholesky(corr: np.ndarray) -> np.ndarray:
    """Numerically stable Cholesky for a valid near-PD correlation matrix."""
    c = np.asarray(corr, dtype=float)
    c = 0.5 * (c + c.T)
    vals, vecs = np.linalg.eigh(c)
    vals = np.maximum(vals, 1e-8)
    c = (vecs * vals) @ vecs.T
    d = np.sqrt(np.diag(c))
    c = c / np.outer(d, d)
    return np.linalg.cholesky(c)


def _final_risky_rebalance(core: float, creative: float, opportunity: float) -> Tuple[float, float, float]:
    """Rebalance the residual risky budget to the binding 55/25/20 policy."""
    risk = max(float(core) + float(creative) + float(opportunity), 0.0)
    return (
        risk * FINAL_RISK_SPLIT["CoreStudio"],
        risk * FINAL_RISK_SPLIT["CreativeEcosystem"],
        risk * FINAL_RISK_SPLIT["OpportunityLab"],
    )


def _withdraw_from_risky_for_runway(amount: float, core: float, creative: float, opportunity: float
                                   ) -> Tuple[float, float, float, float]:
    """Fund the Runway in the governance priority: Opportunity -> Creative -> Core."""
    need = max(float(amount), 0.0)
    take_o = min(opportunity, need); opportunity -= take_o; need -= take_o
    take_c = min(creative, need); creative -= take_c; need -= take_c
    take_core = min(core, need); core -= take_core; need -= take_core
    funded = float(amount) - need
    return funded, core, creative, opportunity


def _simulate_final_client_path(
        risky_monthly_returns: np.ndarray,
        *,
        safe_yield_annual: float,
        reserve_target_2033: float,
        reserve_yield_annual: float,
        extra_annual_drag: float,
        inflation: float,
        flex_floor_dollars: float,
        flex_surplus_pct: float,
        wins_etf_fee: float,
        wins_treasury_fee: float,
        charge_wins_proxy_fees: bool = False,
) -> Dict[str, float]:
    """Simulate the exact client allocation policy for one 2027→2042 path.

    Important separation:
      * The FINAL REPORT base projection uses long-run implementation drag, not
        StockTrak fees as though Laura trades on WInS for 15 years.
      * `charge_wins_proxy_fees=True` is an optional implementation sensitivity
        for a proxy WInS realization of the sleeve trades.

    Facility contribution is MAXIMIZED in 2033 after:
      1) protecting the operating reserve; and
      2) retaining max($25k, 20% of residual surplus) as flexible capital.
    """
    arr = np.asarray(risky_monthly_returns, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError("risky_monthly_returns must have shape (months, 3).")
    payment_months = tuple(72 + 12 * i for i in range(WGY_OPERATING_PAYMENT_COUNT))
    horizon = payment_months[-1] + 1
    if len(arr) < horizon:
        raise ValueError(f"Final client path requires at least {horizon} months; got {len(arr)}.")

    runway = core = creative = opportunity = flex = 0.0
    facility = 0.0
    total_paid = 0.0
    total_fees = 0.0
    reserve_at_2033 = np.nan
    flexibility_at_2033 = np.nan
    portfolio_2031 = np.nan
    portfolio_2033_pre = np.nan
    fully_funded = 0
    first_payment_shortfall = 0.0
    max_shortfall = 0.0

    runway_mret_pre = _annual_to_monthly_return(safe_yield_annual)
    reserve_mret_post = _annual_to_monthly_return(reserve_yield_annual)
    extra_drag_m = max(float(extra_annual_drag), 0.0) / 12.0

    def charge(amount: float, bucket: str = "risk") -> None:
        nonlocal runway, core, creative, opportunity, flex, total_fees
        fee = max(float(amount), 0.0)
        if fee <= 0:
            return
        total_fees += fee
        # Fees come from available cash-like assets first, then risky capital.
        if flex >= fee:
            flex -= fee
            return
        rem = fee - flex
        flex = 0.0
        if runway > 0:
            t = min(runway, rem); runway -= t; rem -= t
        if rem > 0:
            funded, core2, creative2, opportunity2 = _withdraw_from_risky_for_runway(
                rem, core, creative, opportunity
            )
            core, creative, opportunity = core2, creative2, opportunity2

    for month in range(horizon):
        # Official external cash flows occur at beginning of period.
        if month == 0:
            runway += 150_000.0
            core += 82_500.0
            creative += 37_500.0
            opportunity += 30_000.0
            if charge_wins_proxy_fees:
                # 1 Treasury + 2 ETF sleeve implementations + 1 stock sleeve transaction proxy.
                charge(wins_treasury_fee + 3.0 * wins_etf_fee)
        elif month == 12:
            # Add $150k, then reserve-first.
            new_cash = 150_000.0
            floor = FINAL_RUNWAY_FLOORS[12]
            topup = min(max(floor - runway, 0.0), new_cash)
            runway += topup
            new_cash -= topup
            # Residual contribution enters the risky budget at 55/25/20.
            core += new_cash * FINAL_RISK_SPLIT["CoreStudio"]
            creative += new_cash * FINAL_RISK_SPLIT["CreativeEcosystem"]
            opportunity += new_cash * FINAL_RISK_SPLIT["OpportunityLab"]
            if charge_wins_proxy_fees:
                charge(wins_treasury_fee + 3.0 * wins_etf_fee)

        # Annual reserve-floor ratchet before 2033. Never reverse it.
        if month in FINAL_RUNWAY_FLOORS:
            target = FINAL_RUNWAY_FLOORS[month]
            need = max(target - runway, 0.0)
            if need > 0:
                funded, core, creative, opportunity = _withdraw_from_risky_for_runway(
                    need, core, creative, opportunity
                )
                runway += funded
                if charge_wins_proxy_fees and funded > 0:
                    charge(wins_treasury_fee + 3.0 * wins_etf_fee)
            core, creative, opportunity = _final_risky_rebalance(core, creative, opportunity)

        if month == 48:
            portfolio_2031 = runway + core + creative + opportunity + flex

        # 2033: exact liability funding first; maximize facility contribution second.
        if month == 72:
            portfolio_2033_pre = runway + core + creative + opportunity + flex
            need = max(float(reserve_target_2033) - runway, 0.0)
            funded, core, creative, opportunity = _withdraw_from_risky_for_runway(
                need, core, creative, opportunity
            )
            runway += funded

            # 2033 is the one time the planning floors are reconciled to the
            # ACTUAL cash-flow-matched liability cost. Any reserve amount above
            # the exact target is no longer required for the operating promise
            # and becomes part of the residual capital available for facility/
            # flexibility. This is not a "re-risk" before 2033.
            reserve_excess = max(runway - float(reserve_target_2033), 0.0)
            runway -= reserve_excess
            flex += reserve_excess
            reserve_at_2033 = runway

            risk_surplus = max(core + creative + opportunity + flex, 0.0)
            flexibility = min(
                risk_surplus,
                max(float(flex_floor_dollars), float(flex_surplus_pct) * risk_surplus),
            )
            facility = max(risk_surplus - flexibility, 0.0)
            # The facility gets everything except the explicit flexibility buffer.
            core = creative = opportunity = 0.0
            flex = flexibility
            flexibility_at_2033 = flexibility
            if charge_wins_proxy_fees:
                charge(wins_treasury_fee)

        # Beginning-of-year operating payment, including first payment in 2033.
        if month in payment_months:
            due = float(WGY_OPERATING_PAYMENT)
            paid = min(runway, due)
            runway -= paid
            shortfall = due - paid
            if shortfall > 0 and flex > 0:
                x = min(flex, shortfall); flex -= x; paid += x; shortfall -= x
            total_paid += paid
            if shortfall <= 1e-9:
                fully_funded += 1
            max_shortfall = max(max_shortfall, shortfall)
            if month == 72:
                first_payment_shortfall = shortfall

        # Returns occur after beginning-of-month flows.
        if month < 72:
            rc, rcr, ro = arr[month]
            # Capital-market assumptions are already net of sleeve-specific drag.
            core = max(core * (1.0 + rc - extra_drag_m), 0.0)
            creative = max(creative * (1.0 + rcr - extra_drag_m), 0.0)
            opportunity = max(opportunity * (1.0 + ro - extra_drag_m), 0.0)
            runway = max(runway * (1.0 + runway_mret_pre), 0.0)
        else:
            runway = max(runway * (1.0 + reserve_mret_post), 0.0)
            flex = max(flex * (1.0 + runway_mret_pre), 0.0)

    nominal_total_required = WGY_OPERATING_PAYMENT * WGY_OPERATING_PAYMENT_COUNT
    inf_2031 = (1.0 + inflation) ** 4
    inf_2033 = (1.0 + inflation) ** 6
    inf_2042 = (1.0 + inflation) ** 15
    final_assets = runway + flex
    return {
        "Portfolio2031": float(portfolio_2031),
        "Portfolio2033PreReserve": float(portfolio_2033_pre),
        "Reserve2033": float(reserve_at_2033),
        "FacilityContribution": float(facility),
        "FlexibilityRetained2033": float(flexibility_at_2033),
        "FlexibilityEnding": float(max(flex, 0.0)),
        "OperatingPaid": float(total_paid),
        "OperatingSuccess": float(total_paid + 1e-9 >= nominal_total_required),
        "PaymentsFullyFunded": float(fully_funded),
        "FirstPaymentShortfall": float(first_payment_shortfall),
        "MaxPaymentShortfall": float(max_shortfall),
        "FinalAssets2042": float(final_assets),
        "WInSProxyFeesPaid": float(total_fees),
        "Portfolio2031_Real2027": float(portfolio_2031 / inf_2031),
        "FacilityContribution_Real2027": float(facility / inf_2033),
        "FinalAssets2042_Real2027": float(final_assets / inf_2042),
    }


def run_final_client_architecture(
        opportunity_backtest_returns: pd.Series,
        *,
        n_sims: int,
        seed: int,
        inflation: float,
        planning_safe_yield: float = 0.025,
        pre2033_runway_yield: float = 0.030,
        zero_yield_stress: bool = True,
        extra_annual_drag: float = 0.0025,
        flex_floor_dollars: float = 25_000.0,
        flex_surplus_pct: float = 0.20,
        wins_stock_fee: float = 25.0,
        wins_etf_fee: float = 25.0,
        wins_treasury_fee: float = 10.0,
) -> Dict[str, Any]:
    """Run the FINAL client-level Monte Carlo aligned to the report allocation.

    The opportunity backtest is validation evidence only. Client projections use
    deliberately conservative sleeve assumptions (7%, 7.5%, 9%) rather than the
    historical ~20% signal CAGR.
    """
    os.makedirs(WGY_DIR, exist_ok=True)
    a = FINAL_CAPITAL_MARKET_ASSUMPTIONS.copy()
    corr = np.array([
        [1.0, a["CorrCoreCreative"], a["CorrCoreOpportunity"]],
        [a["CorrCoreCreative"], 1.0, a["CorrCreativeOpportunity"]],
        [a["CorrCoreOpportunity"], a["CorrCreativeOpportunity"], 1.0],
    ])
    L = _safe_cholesky(corr)

    means_a = np.array([a["CoreMean"], a["CreativeMean"], a["OpportunityMean"]], dtype=float)
    vols_a = np.array([a["CoreVol"], a["CreativeVol"], a["OpportunityVol"]], dtype=float)
    drags_a = np.array([a["CoreDrag"], a["CreativeDrag"], a["OpportunityDrag"]], dtype=float)
    means_m = np.array([_annual_to_monthly_return(x) for x in means_a - drags_a])
    vols_m = vols_a / np.sqrt(12.0)

    payment_months = tuple(72 + 12 * i for i in range(WGY_OPERATING_PAYMENT_COUNT))
    horizon = payment_months[-1] + 1
    rng = np.random.default_rng(seed)

    # Student-t innovations create fatter tails than Gaussian projections while
    # keeping the stated covariance structure transparent.
    df = 6.0
    scale_t = math.sqrt((df - 2.0) / df)
    rows = []
    reserve_plan = _wgy_pv_ladder_reserve(planning_safe_yield)
    for _ in range(int(n_sims)):
        z = rng.standard_normal((horizon, 3)) @ L.T
        chi = rng.chisquare(df, size=(horizon, 1))
        t_shock = z / np.sqrt(chi / df) * scale_t
        rets = means_m + vols_m * t_shock
        rets = np.clip(rets, -0.80, 1.50)
        rows.append(_simulate_final_client_path(
            rets,
            safe_yield_annual=pre2033_runway_yield,
            reserve_target_2033=reserve_plan,
            reserve_yield_annual=planning_safe_yield,
            extra_annual_drag=extra_annual_drag,
            inflation=inflation,
            flex_floor_dollars=flex_floor_dollars,
            flex_surplus_pct=flex_surplus_pct,
            wins_etf_fee=wins_etf_fee,
            wins_treasury_fee=wins_treasury_fee,
            charge_wins_proxy_fees=False,
        ))
    base = pd.DataFrame(rows)

    stress = pd.DataFrame()
    if zero_yield_stress:
        stress_rows = []
        rng2 = np.random.default_rng(seed + 77731)
        for _ in range(int(n_sims)):
            z = rng2.standard_normal((horizon, 3)) @ L.T
            chi = rng2.chisquare(df, size=(horizon, 1))
            t_shock = z / np.sqrt(chi / df) * scale_t
            rets = np.clip(means_m + vols_m * t_shock, -0.80, 1.50)
            stress_rows.append(_simulate_final_client_path(
                rets,
                safe_yield_annual=pre2033_runway_yield,
                reserve_target_2033=WGY_NOMINAL_OPERATING_RESERVE,
                reserve_yield_annual=0.0,
                extra_annual_drag=extra_annual_drag,
                inflation=inflation,
                flex_floor_dollars=flex_floor_dollars,
                flex_surplus_pct=flex_surplus_pct,
                wins_etf_fee=wins_etf_fee,
                wins_treasury_fee=wins_treasury_fee,
                charge_wins_proxy_fees=False,
            ))
        stress = pd.DataFrame(stress_rows)

    def summarize(df_: pd.DataFrame, label: str, reserve_target: float) -> Dict[str, Any]:
        fac = df_["FacilityContribution"]
        return {
            "Scenario": label,
            "ReserveTarget2033": float(reserve_target),
            "OperatingSuccessPct": 100.0 * float(df_["OperatingSuccess"].mean()),
            "MedianPortfolio2031": float(df_["Portfolio2031"].median()),
            "P10Portfolio2033": float(df_["Portfolio2033PreReserve"].quantile(0.10)),
            "MedianPortfolio2033": float(df_["Portfolio2033PreReserve"].median()),
            "P90Portfolio2033": float(df_["Portfolio2033PreReserve"].quantile(0.90)),
            "P10Facility": float(fac.quantile(0.10)),
            "MedianFacility": float(fac.median()),
            "P90Facility": float(fac.quantile(0.90)),
            "ProbabilityPositiveFacilityPct": 100.0 * float((fac > 0).mean()),
            "MedianFacilityReal2027": float(df_["FacilityContribution_Real2027"].median()),
            "MedianFinalAssets2042": float(df_["FinalAssets2042"].median()),
        }

    summaries = [summarize(base, f"Planning reserve @ {planning_safe_yield:.1%}", reserve_plan)]
    if not stress.empty:
        summaries.append(summarize(stress, "Zero-yield $500k stress", WGY_NOMINAL_OPERATING_RESERVE))
    summary = pd.DataFrame(summaries)

    # Policy schedule is a governance output, not an optimized aesthetic weight.
    schedule_rows = []
    for month, floor in FINAL_RUNWAY_FLOORS.items():
        risk_budget_example = max((300_000.0 if month == 0 else 450_000.0) - floor, 0.0)
        schedule_rows.append({
            "YearOffset": int(month // 12),
            "CalendarYear": 2027 + int(month // 12),
            "MinimumRunwayFloor": float(floor),
            "RiskSplit_CorePct": 55.0,
            "RiskSplit_CreativePct": 25.0,
            "RiskSplit_OpportunityPct": 20.0,
        })
    schedule = pd.DataFrame(schedule_rows)

    opp = pd.Series(opportunity_backtest_returns).dropna().astype(float)
    if len(opp) >= 12:
        opp_years = len(opp) / 12.0
        opp_cagr = float((1.0 + opp).prod() ** (1.0 / opp_years) - 1.0)
        opp_vol = float(opp.std(ddof=1) * np.sqrt(12.0))
    else:
        opp_cagr = np.nan
        opp_vol = np.nan

    assumptions = pd.DataFrame([
        ["Runway pre-2033 yield", pre2033_runway_yield, "Planning assumption"],
        ["2033 planning reserve yield", planning_safe_yield, "Used only to size cash-flow-matched reserve"],
        ["Core expected return", a["CoreMean"], "Conservative client projection"],
        ["Core volatility", a["CoreVol"], "Scenario assumption"],
        ["Creative expected return", a["CreativeMean"], "Conservative client projection"],
        ["Creative volatility", a["CreativeVol"], "Scenario assumption"],
        ["Opportunity expected return", a["OpportunityMean"], "Client projection assumption; deliberately conservative"],
        ["Opportunity volatility", a["OpportunityVol"], "Client projection assumption"],
        ["Opportunity historical backtest CAGR", opp_cagr, "Validation evidence only; NOT extrapolated as client return"],
        ["Opportunity historical backtest volatility", opp_vol, "Validation evidence only"],
        ["Extra annual implementation drag", extra_annual_drag, "Long-horizon sensitivity"],
        ["WInS stock fee", wins_stock_fee, "Flat dollars per cleared stock transaction"],
        ["WInS ETF fee", wins_etf_fee, "Flat dollars per cleared ETF transaction"],
        ["WInS Treasury fee", wins_treasury_fee, "Flat dollars per cleared Treasury transaction"],
        ["Personal tax rate — official case", 0.0, "Case-compliant headline"],
        ["Inflation", inflation, "Real-dollar reporting; $50k liabilities remain nominal"],
        ["Flexibility floor dollars", flex_floor_dollars, "Retained after reserve"],
        ["Flexibility share of residual surplus", flex_surplus_pct, "Retained after reserve"],
    ], columns=["Assumption", "Value", "Role"])

    base.to_csv(os.path.join(WGY_DIR, "FINAL_client_portfolio_paths.csv"), index=False)
    if not stress.empty:
        stress.to_csv(os.path.join(WGY_DIR, "FINAL_client_portfolio_zero_yield_stress_paths.csv"), index=False)
    summary.to_csv(os.path.join(WGY_DIR, "FINAL_client_portfolio_summary.csv"), index=False)
    schedule.to_csv(os.path.join(WGY_DIR, "FINAL_runway_floor_schedule.csv"), index=False)
    assumptions.to_csv(os.path.join(WGY_DIR, "FINAL_assumptions_and_fees.csv"), index=False)

    # A simple presentation-ready facility chart.
    try:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.hist(base["FacilityContribution"], bins=50, alpha=0.75, edgecolor="black")
        for q, ls, lab in [(0.10, "--", "P10"), (0.50, "-", "Median"), (0.90, "--", "P90")]:
            val = float(base["FacilityContribution"].quantile(q))
            ax.axvline(val, linestyle=ls, linewidth=1.8, label=f"{lab}: {_fmt_money(val)}")
        ax.set_title("Final Client Architecture: 2033 Facility Contribution")
        ax.set_xlabel("Maximum responsible facility contribution ($)")
        ax.set_ylabel("Simulated paths")
        ax.legend()
        ax.grid(True, alpha=0.25)
        plt.tight_layout()
        plt.savefig(os.path.join(WGY_DIR, "FINAL_facility_contribution_distribution.png"), dpi=200, bbox_inches="tight")
        plt.close(fig)
    except Exception as exc:
        cli.warn(f"Final client facility chart failed — {exc}")

    cli.section("FINAL CLIENT PORTFOLIO — TWO-CLOCK STUDIO")
    cli.table(summary, "Client-Level Projection (Conservative Assumptions)")
    cli.box("BINDING CAPITAL POLICY", [
        ("2027 Runway", "$150,000 / 50% of initial $300k"),
        ("Risky residual split", "55% Core / 25% Creative / 20% Opportunity"),
        ("2031 Runway floor", "$430,000"),
        ("2032 Runway floor", "$460,000"),
        ("2033 planning reserve", _fmt_money(reserve_plan)),
        ("Zero-yield stress reserve", "$500,000"),
        ("Facility rule", "MAX residual after reserve + flexibility buffer"),
        ("Flexibility rule", f"max({_fmt_money(flex_floor_dollars)}, {flex_surplus_pct:.0%} of post-reserve surplus)"),
    ])
    return {"paths": base, "stress": stress, "summary": summary, "schedule": schedule, "assumptions": assumptions}

def run_wgy_2027_case_study(
        monthly_returns: pd.Series,
        *,
        n_sims: int = 10_000,
        aux_sims: int = 3000,
        block_size: int = 6,
        seed: int = 42,
        confidence: float = 0.90,
        historical_stride_months: int = 12,
        second_contribution_month: int = WGY_SECOND_CONTRIBUTION_MONTH,
        withdrawal_start_month: int = WGY_WITHDRAWAL_START_MONTH,
        sponsor_lead_months: int = WGY_SPONSOR_LEAD_MONTHS,
        operating_reserve_target: float = WGY_NOMINAL_OPERATING_RESERVE,
        flex_floor_dollars: float = 25_000.0,
        flex_floor_pct: float = 0.20,
        reserve_yield_annual: float = 0.0,
        facility_cost: float = 0.0,
        inflation: float = 0.025,
        tax_rate: float = 0.20,
        tax_realization_fraction: float = 0.25,
        allocation_mode: str = "ratchet",
        ratchet_buffer: float = 1.15,
        frontier_sims: int = 2000,
        frontier_eval_sims: int = 5000,
        frontier_safe_yield: float = 0.0,
        frontier_min_safe: float = 0.0,
        frontier_max_safe: float = 0.60,
        frontier_step: float = 0.05,
        frontier_target_success: float = 0.99,
        crisis_sims: int = 2000,
        skip_frontier: bool = False,
        skip_crisis: bool = False,
) -> Dict[str, Any]:
    """Run the direct historical mandate replay + bootstrap + allocation comparison."""
    events = _wgy_event_months(withdrawal_start_month, sponsor_lead_months)
    horizon = events["horizon_months"]
    _wgy_validate_inputs(
        confidence, flex_floor_pct, inflation, reserve_yield_annual, horizon,
        second_contribution_month, withdrawal_start_month, tax_rate, tax_realization_fraction,
    )
    if monthly_returns is None or monthly_returns.dropna().empty:
        cli.warn("WGY mandate replay skipped: strategy return series is empty.")
        return {}

    s = monthly_returns.dropna().sort_index()
    os.makedirs(WGY_DIR, exist_ok=True)

    # Deterministic reserve-sizing sensitivity. The official case fixes the ten
    # payments nominally; this table shows how a cash-flow-matched Treasury
    # ladder would change the amount required at the first-payment date under
    # alternative assumed yields. It is not a forecast of 2033 yields.
    reserve_sens = pd.DataFrame([
        {"AssumedYield": y,
         "PVReserve": _wgy_pv_ladder_reserve(y),
         "CapitalFreedVs500k": WGY_NOMINAL_OPERATING_RESERVE - _wgy_pv_ladder_reserve(y)}
        for y in (0.00, 0.02, 0.03, 0.04, 0.05)
    ])
    reserve_sens_path = os.path.join(WGY_DIR, "wgy_reserve_sizing_sensitivity.csv")
    reserve_sens.to_csv(reserve_sens_path, index=False)
    cli.saved(reserve_sens_path)

    cli.section("WGY 2027 Case: Direct Historical Mandate Replay")
    cli.warn(
        "Each historical path now receives the ACTUAL mandate cash flows from its own start date. "
        "Backtested returns remain historical evidence, not guaranteed future returns; selection/survivorship gaps can still make results optimistic."
    )
    cli.kv("Historical Return Start", str(s.index[0].date()) if hasattr(s.index[0], "date") else str(s.index[0]))
    cli.kv("Initial Contribution", "$300,000 @ t=0")
    cli.kv("Second Contribution", f"$150,000 @ t=+{second_contribution_month / 12:.0f} years")
    cli.kv("Sponsor Checkpoint", f"t=+{events['sponsor_month'] / 12:.0f} years")
    cli.kv("First $50k Withdrawal", f"t=+{withdrawal_start_month / 12:.0f} years")
    cli.kv("Operating Commitment", "10 × $50,000 annually (fixed nominal)")
    cli.kv("Replay Horizon", f"{horizon / 12:.0f} years")
    cli.kv("Allocation Mode", allocation_mode)
    cli.kv("Ring-Fenced Reserve Target", _fmt_money(operating_reserve_target))
    cli.kv("Reserve Yield", f"{reserve_yield_annual * 100:.2f}%/yr")
    cli.kv("Inflation", f"{inflation * 100:.2f}%/yr (real-value reporting only)")
    cli.kv("Tax Sensitivity", f"{tax_rate * 100:.1f}% × {tax_realization_fraction * 100:.0f}% assumed realized positive gains")
    cli.kv("Official Tax Base", "0% tax also run separately because the case says taxes need not be modeled")

    hurdle_target = float(operating_reserve_target) + float(flex_floor_dollars)
    hurdle_cagr = _wgy_required_cagr(
        initial_contribution=WGY_INITIAL_CONTRIBUTION,
        second_contribution=WGY_SECOND_CONTRIBUTION,
        second_year=second_contribution_month / 12.0,
        target_year=withdrawal_start_month / 12.0,
        target_value=hurdle_target,
    )
    real_hurdle = (1.0 + hurdle_cagr) / (1.0 + inflation) - 1.0
    cli.box("MANDATE HURDLE BEFORE FIRST WITHDRAWAL", [
        ("Nominal target", _fmt_money(hurdle_target)),
        ("Required nominal CAGR", f"{hurdle_cagr * 100:.2f}%"),
        ("Required real CAGR", f"{real_hurdle * 100:.2f}%"),
        ("Purpose", "fund reserve + minimum retained flexibility"),
    ])

    historical = _wgy_historical_paths(
        s,
        stride_months=historical_stride_months,
        second_contribution_month=second_contribution_month,
        withdrawal_start_month=withdrawal_start_month,
        sponsor_lead_months=sponsor_lead_months,
        operating_reserve_target=operating_reserve_target,
        flex_floor_dollars=flex_floor_dollars,
        flex_floor_pct=flex_floor_pct,
        reserve_yield_annual=reserve_yield_annual,
        facility_cost=facility_cost,
        inflation=inflation,
        tax_rate=0.0,
        tax_realization_fraction=0.0,
        allocation_mode=allocation_mode,
        ratchet_buffer=ratchet_buffer,
    )
    mc = _wgy_bootstrap_paths(
        s,
        n_sims=n_sims,
        block_size=block_size,
        seed=seed,
        second_contribution_month=second_contribution_month,
        withdrawal_start_month=withdrawal_start_month,
        sponsor_lead_months=sponsor_lead_months,
        operating_reserve_target=operating_reserve_target,
        flex_floor_dollars=flex_floor_dollars,
        flex_floor_pct=flex_floor_pct,
        reserve_yield_annual=reserve_yield_annual,
        facility_cost=facility_cost,
        inflation=inflation,
        tax_rate=0.0,
        tax_realization_fraction=0.0,
        allocation_mode=allocation_mode,
        ratchet_buffer=ratchet_buffer,
    )

    historical_path = os.path.join(WGY_DIR, "wgy_historical_mandate_replay.csv")
    mc_path = os.path.join(WGY_DIR, "wgy_bootstrap_mandate_paths.csv")
    historical.to_csv(historical_path, index=False)
    mc.to_csv(mc_path, index=False)
    cli.saved(historical_path)
    cli.saved(mc_path)

    if not historical.empty:
        cli.table(_wgy_historical_timeline_preview(historical), "Historical Mandate Replay: Timeline + Outcomes")
        hist_success = 100.0 * float(historical["OperatingCommitmentSuccess"].mean())
        hist_reserve = 100.0 * float(historical["ReserveOnlySuccess"].mean())
        cli.box("HISTORICAL MANDATE REPLAY", [
            ("Rolling paths", str(len(historical))),
            ("10/10 operating success", f"{hist_success:.1f}%"),
            ("Reserve-only success", f"{hist_reserve:.1f}%"),
            ("Median facility", _fmt_money(float(historical["FacilityContribution"].median()))),
            ("Worst-start facility", _fmt_money(float(historical["FacilityContribution"].min()))),
            ("Worst final assets", _fmt_money(float(historical["FinalAssets"].min()))),
        ])

    if mc.empty:
        cli.warn("WGY bootstrap produced no paths.")
        return {"historical": historical, "mc": mc}

    alpha = (1.0 - confidence) / 2.0
    facility = mc["FacilityContribution"].dropna()
    pledge_low = float(facility.quantile(alpha))
    pledge_high = float(facility.quantile(1.0 - alpha))
    pledge_median = float(facility.median())
    reserve_funding_prob = float(mc["ReserveFullyFunded_FirstWithdrawal"].mean())
    reserve_only_prob = float(mc["ReserveOnlySuccess"].mean())
    operating_prob = float(mc["OperatingCommitmentSuccess"].mean())

    cli.box("CO-SPONSOR / FACILITY RANGE AT FIRST WITHDRAWAL DATE", [
        (f"{confidence * 100:.0f}% Scenario Range", f"{_fmt_money(pledge_low)} – {_fmt_money(pledge_high)}"),
        ("Median nominal facility", _fmt_money(pledge_median)),
        ("Median real facility", _fmt_money(float(mc["FacilityContribution_RealStartDollars"].median()))),
        ("P(full reserve)", f"{reserve_funding_prob * 100:.2f}%"),
        ("P(reserve alone pays 10/10)", f"{reserve_only_prob * 100:.2f}%"),
        ("P(portfolio pays 10/10)", f"{operating_prob * 100:.2f}%"),
    ])

    quantiles = _wgy_quantile_table(mc)
    cli.table(_wgy_format_quantile_table(quantiles), "Mandate Bootstrap Outcome Distribution")

    # Compare three allocation policies using identical simulated paths.
    engine_comp = _wgy_compare_allocation_engines(
        s,
        n_sims=min(int(n_sims), int(aux_sims)),
        block_size=block_size,
        seed=seed + 701,
        second_contribution_month=second_contribution_month,
        withdrawal_start_month=withdrawal_start_month,
        sponsor_lead_months=sponsor_lead_months,
        operating_reserve_target=operating_reserve_target,
        flex_floor_dollars=flex_floor_dollars,
        flex_floor_pct=flex_floor_pct,
        reserve_yield_annual=reserve_yield_annual,
        facility_cost=facility_cost,
        inflation=inflation,
        tax_rate=0.0,
        tax_realization_fraction=0.0,
        ratchet_buffer=ratchet_buffer,
    )
    if not engine_comp.empty:
        engine_path = os.path.join(WGY_DIR, "wgy_allocation_engine_comparison.csv")
        engine_comp.to_csv(engine_path, index=False)
        cli.saved(engine_path)
        cli.table(_wgy_format_engine_comparison(engine_comp), "Allocation Engine Comparison")

    # Official no-tax base vs conservative implementation tax sensitivity.
    tax_sens = _wgy_tax_sensitivity(
        s,
        n_sims=min(int(n_sims), int(aux_sims)),
        block_size=block_size,
        seed=seed + 1701,
        second_contribution_month=second_contribution_month,
        withdrawal_start_month=withdrawal_start_month,
        sponsor_lead_months=sponsor_lead_months,
        operating_reserve_target=operating_reserve_target,
        flex_floor_dollars=flex_floor_dollars,
        flex_floor_pct=flex_floor_pct,
        reserve_yield_annual=reserve_yield_annual,
        facility_cost=facility_cost,
        inflation=inflation,
        tax_rate=tax_rate,
        tax_realization_fraction=tax_realization_fraction,
        allocation_mode=allocation_mode,
        ratchet_buffer=ratchet_buffer,
    )
    if not tax_sens.empty:
        tax_path = os.path.join(WGY_DIR, "wgy_tax_sensitivity.csv")
        tax_sens.to_csv(tax_path, index=False)
        cli.saved(tax_path)
        cli.table(_wgy_format_tax_sensitivity(tax_sens), "Official vs Tax-Aware Implementation Sensitivity")

    # FINAL client-level architecture. This is the portfolio policy used in the
    # case study; the legacy single-engine WGY diagnostics above remain as stress
    # evidence and are NOT presented as the allocation recommendation.
    final_client = run_final_client_architecture(
        s,
        n_sims=int(n_sims),
        seed=seed + 88001,
        inflation=inflation,
        planning_safe_yield=0.025,
        pre2033_runway_yield=0.030,
        zero_yield_stress=True,
        extra_annual_drag=0.0025,
        flex_floor_dollars=25_000.0,
        flex_surplus_pct=0.20,
        wins_stock_fee=25.0,
        wins_etf_fee=25.0,
        wins_treasury_fee=10.0,
    )

    advanced = _save_wgy_advanced_diagnostics(
        s, historical, mc, hist_stride=historical_stride_months,
        withdrawal_start_month=withdrawal_start_month, sponsor_lead_months=sponsor_lead_months,
        frontier_sims=frontier_sims, frontier_eval_sims=frontier_eval_sims,
        frontier_safe_yield=frontier_safe_yield, frontier_min_safe=frontier_min_safe,
        frontier_max_safe=frontier_max_safe, frontier_step=frontier_step,
        frontier_target_success=frontier_target_success, crisis_sims=crisis_sims,
        second_contribution_month=second_contribution_month,
        operating_reserve_target=operating_reserve_target, flex_floor_dollars=flex_floor_dollars,
        flex_floor_pct=flex_floor_pct, reserve_yield_annual=reserve_yield_annual,
        facility_cost=facility_cost, inflation=inflation, allocation_mode=allocation_mode,
        ratchet_buffer=ratchet_buffer, block_size=block_size, seed=seed,
        skip_frontier=skip_frontier, skip_crisis=skip_crisis,
    )

    sponsor_low = sponsor_high = sponsor_median = np.nan
    if facility_cost > 0 and "CoSponsorNeed" in mc.columns:
        sponsor = mc["CoSponsorNeed"].dropna()
        if not sponsor.empty:
            sponsor_low = float(sponsor.quantile(alpha))
            sponsor_high = float(sponsor.quantile(1.0 - alpha))
            sponsor_median = float(sponsor.median())
            cli.box("CO-SPONSOR FUNDING REQUIREMENT", [
                ("Facility cost assumption", _fmt_money(facility_cost)),
                (f"{confidence * 100:.0f}% sponsor-need range", f"{_fmt_money(sponsor_low)} – {_fmt_money(sponsor_high)}"),
                ("Median sponsor need", _fmt_money(sponsor_median)),
            ])

    summary_rows = [
        ("Historical return series begins", str(s.index[0])),
        ("Initial contribution", WGY_INITIAL_CONTRIBUTION),
        ("Second contribution month", second_contribution_month),
        ("First withdrawal month", withdrawal_start_month),
        ("Operating reserve target", operating_reserve_target),
        ("Required nominal CAGR to reserve+flex floor", hurdle_cagr),
        ("Required real CAGR", real_hurdle),
        ("Inflation assumption", inflation),
        ("Tax sensitivity rate", tax_rate),
        ("Tax sensitivity realization fraction", tax_realization_fraction),
        ("Allocation mode", allocation_mode),
        ("Facility contribution median", pledge_median),
        (f"Facility contribution lower bound ({confidence:.0%})", pledge_low),
        (f"Facility contribution upper bound ({confidence:.0%})", pledge_high),
        ("Probability full reserve funded", reserve_funding_prob),
        ("Probability reserve alone funds ten payments", reserve_only_prob),
        ("Probability portfolio funds ten payments", operating_prob),
        ("Median final assets", float(mc["FinalAssets"].median())),
        ("Median real final assets", float(mc["FinalAssets_RealStartDollars"].median())),
        ("Median tax paid", float(mc["TaxPaid_Total"].median())),
    ]
    if facility_cost > 0:
        summary_rows.extend([
            ("Facility cost assumption", facility_cost),
            ("Median co-sponsor need", sponsor_median),
            (f"Co-sponsor need lower bound ({confidence:.0%})", sponsor_low),
            (f"Co-sponsor need upper bound ({confidence:.0%})", sponsor_high),
        ])
    tail_adv = advanced.get("tail_risk") if isinstance(advanced, dict) else None
    if isinstance(tail_adv, pd.DataFrame) and not tail_adv.empty:
        for metric in ["Facility CVaR / Expected Shortfall 5%", "Expected shortfall conditional on failure"]:
            hit = tail_adv.loc[tail_adv["Metric"] == metric, "Value"]
            if not hit.empty:
                summary_rows.append((metric, float(hit.iloc[0])))
    bayes_adv = advanced.get("bayesian_reliability") if isinstance(advanced, dict) else None
    if isinstance(bayes_adv, pd.DataFrame) and not bayes_adv.empty:
        row = bayes_adv.iloc[-1]
        summary_rows.extend([
            ("Bayesian overlap-adjusted reliability mean", float(row["PosteriorMean"])),
            ("Bayesian overlap-adjusted 95% lower bound", float(row["CredibleLow"])),
            ("Bayesian overlap-adjusted 95% upper bound", float(row["CredibleHigh"])),
        ])
    frontier_adv = advanced.get("frontier_evaluation") if isinstance(advanced, dict) else None
    if isinstance(frontier_adv, pd.DataFrame) and not frontier_adv.empty:
        fr = frontier_adv.iloc[0]
        summary_rows.extend([
            ("Frontier selected safe weight", float(fr["SafeWeight"])),
            ("Frontier independent success probability", float(fr["OperatingSuccessPct"]) / 100.0),
            ("Frontier independent median facility", float(fr["MedianFacility"])),
            ("Frontier independent facility CVaR05", float(fr["CVaR05Facility"])),
        ])
    final_summary = final_client.get("summary") if isinstance(final_client, dict) else None
    if isinstance(final_summary, pd.DataFrame) and not final_summary.empty:
        fr = final_summary.iloc[0]
        summary_rows.extend([
            ("FINAL planning reserve target 2033", float(fr["ReserveTarget2033"])),
            ("FINAL operating success probability", float(fr["OperatingSuccessPct"]) / 100.0),
            ("FINAL median 2033 portfolio", float(fr["MedianPortfolio2033"])),
            ("FINAL P10 facility contribution", float(fr["P10Facility"])),
            ("FINAL median facility contribution", float(fr["MedianFacility"])),
            ("FINAL P90 facility contribution", float(fr["P90Facility"])),
            ("FINAL median facility real 2027 dollars", float(fr["MedianFacilityReal2027"])),
        ])
    summary_df = pd.DataFrame(summary_rows, columns=["Metric", "Value"])
    summary_path = os.path.join(WGY_DIR, "wgy_case_summary.csv")
    summary_df.to_csv(summary_path, index=False)
    cli.saved(summary_path)

    # Facility contribution distribution.
    try:
        fig, ax = plt.subplots(figsize=(11, 6))
        ax.hist(facility.values, bins=50, alpha=0.75, edgecolor="black")
        ax.axvline(pledge_low, linestyle="--", linewidth=1.5, label=f"Lower {confidence:.0%} bound")
        ax.axvline(pledge_median, linestyle="-", linewidth=2.0, label="Median")
        ax.axvline(pledge_high, linestyle="--", linewidth=1.5, label=f"Upper {confidence:.0%} bound")
        ax.set_title("WGY Mandate Replay: Facility Contribution Distribution")
        ax.set_xlabel("Facility contribution at first-withdrawal date ($)")
        ax.set_ylabel("Bootstrap paths")
        ax.legend()
        ax.grid(True, alpha=0.25)
        plt.tight_layout()
        png = os.path.join(WGY_DIR, "wgy_facility_contribution_distribution.png")
        plt.savefig(png, dpi=200, bbox_inches="tight")
        cli.saved(png)
        plt.close(fig)
    except Exception as exc:
        cli.warn(f"WGY facility distribution plot failed — {exc}")

    # Historical start-year outcome chart: this is the direct answer to
    # "what if the same mandate had begun at each historical start date?"
    if not historical.empty:
        try:
            plot_df = historical.copy()
            plot_df["StartYear"] = pd.to_datetime(plot_df["HistoricalStart"]).dt.year
            fig, ax = plt.subplots(figsize=(11, 6))
            ax.plot(plot_df["StartYear"], plot_df["FacilityContribution"], marker="o", label="Facility contribution")
            ax.axhline(0.0, linewidth=1.0)
            ax.set_title("Historical Mandate Replay by Start Year")
            ax.set_xlabel("Historical path start year")
            ax.set_ylabel("Facility contribution after reserve ($)")
            ax.grid(True, alpha=0.25)
            ax.legend()
            plt.tight_layout()
            png = os.path.join(WGY_DIR, "wgy_historical_start_year_facility.png")
            plt.savefig(png, dpi=200, bbox_inches="tight")
            cli.saved(png)
            plt.close(fig)
        except Exception as exc:
            cli.warn(f"WGY historical replay plot failed — {exc}")

    return {
        "historical": historical,
        "mc": mc,
        "quantiles": quantiles,
        "engine_comparison": engine_comp,
        "tax_sensitivity": tax_sens,
        "advanced_diagnostics": advanced,
        "pledge_low": pledge_low,
        "pledge_high": pledge_high,
        "pledge_median": pledge_median,
        "reserve_funding_prob": reserve_funding_prob,
        "reserve_only_prob": reserve_only_prob,
        "operating_prob": operating_prob,
        "hurdle_cagr": hurdle_cagr,
        "real_hurdle_cagr": real_hurdle,
        "facility_cost": facility_cost,
        "sponsor_low": sponsor_low,
        "sponsor_high": sponsor_high,
        "sponsor_median": sponsor_median,
        "allocation_mode": allocation_mode,
        "withdrawal_start_month": withdrawal_start_month,
    }


def run_engine_self_tests() -> pd.DataFrame:
    """Fast deterministic checks for cash-flow dates and core mandate mechanics."""
    rows = []
    def check(name: str, ok: bool, detail: str = ""):
        rows.append({"Test": name, "Pass": bool(ok), "Detail": detail})
        if not ok:
            raise AssertionError(f"Self-test failed: {name} — {detail}")

    events = _wgy_event_months(WGY_WITHDRAWAL_START_MONTH, WGY_SPONSOR_LEAD_MONTHS)
    check("Official second contribution offset", WGY_SECOND_CONTRIBUTION_MONTH == 12,
          f"got {WGY_SECOND_CONTRIBUTION_MONTH} months")
    check("Official sponsor checkpoint offset", events["sponsor_month"] == 48,
          f"got {events['sponsor_month']} months")
    check("Official first payment offset", WGY_WITHDRAWAL_START_MONTH == 72,
          f"got {WGY_WITHDRAWAL_START_MONTH} months")
    check("Ten annual payment months", len(events["payment_months"]) == 10 and events["payment_months"][-1] == 180,
          f"payment months={events['payment_months']}")
    check("Zero-yield reserve equals nominal commitment",
          abs(_wgy_pv_ladder_reserve(0.0) - 500000.0) < 1e-6,
          f"PV={_wgy_pv_ladder_reserve(0.0):.2f}")

    zero = np.zeros(events["horizon_months"], dtype=float)
    z = simulate_wgy_case_path(zero, allocation_mode="static", tax_rate=0.0,
                               second_contribution_month=12, withdrawal_start_month=72,
                               sponsor_lead_months=24, operating_reserve_target=500000.0,
                               flex_floor_dollars=100000.0, flex_floor_pct=0.10)
    check("Zero-return path cannot fully fund 500k reserve", z["ReserveFullyFunded_FirstWithdrawal"] < 0.5,
          f"reserve={z['OperatingReserve_AtFirstWithdrawal']:.2f}")
    check("Zero-return path records a cash-flow shortfall", z["TotalOperatingShortfall"] > 0,
          f"shortfall={z['TotalOperatingShortfall']:.2f}")

    strong = np.full(events["horizon_months"], 0.01, dtype=float)
    g = simulate_wgy_case_path(strong, allocation_mode="static", tax_rate=0.0,
                               second_contribution_month=12, withdrawal_start_month=72,
                               sponsor_lead_months=24, operating_reserve_target=500000.0,
                               flex_floor_dollars=100000.0, flex_floor_pct=0.10)
    check("Strong deterministic path funds all ten payments", g["OperatingCommitmentSuccess"] > 0.5,
          f"funded={g['PaymentsFullyFunded']:.0f}/10")
    check("Strong path counts all ten funded payments", int(round(g["PaymentsFullyFunded"])) == 10,
          f"funded={g['PaymentsFullyFunded']:.0f}")

    # Final-client architecture checks.
    check("2027 final allocation sums to $300k",
          abs(150000.0 + 82500.0 + 37500.0 + 30000.0 - 300000.0) < 1e-9,
          "Runway/Core/Creative/Opportunity opening allocation")
    simple_cost = _simple_turnover_cost_series(
        pd.date_range("2027-01-31", periods=1, freq="ME"),
        [{"AAA", "BBB"}], 0.0, False, fixed_fee_dollars=25.0, initial_capital=300000.0
    ).iloc[0]
    check("WInS flat stock fee is $25 per transaction",
          abs(simple_cost - 50.0 / 300000.0) < 1e-12,
          f"two initial transactions cost fraction={simple_cost:.8f}")

    strong_risky = np.full((181, 3), 0.01, dtype=float)
    f = _simulate_final_client_path(
        strong_risky, safe_yield_annual=0.03,
        reserve_target_2033=_wgy_pv_ladder_reserve(0.025),
        reserve_yield_annual=0.025, extra_annual_drag=0.0025,
        inflation=0.025, flex_floor_dollars=25000.0, flex_surplus_pct=0.20,
        wins_etf_fee=25.0, wins_treasury_fee=10.0, charge_wins_proxy_fees=False,
    )
    conservation = f["Reserve2033"] + f["FacilityContribution"] + f["FlexibilityRetained2033"]
    check("2033 facility maximizes residual after reserve + flexibility",
          abs(conservation - f["Portfolio2033PreReserve"]) < 1e-5 and f["FacilityContribution"] > 0,
          f"pre={f['Portfolio2033PreReserve']:.2f}, allocated={conservation:.2f}")

    out = pd.DataFrame(rows)
    os.makedirs(DIAG_DIR, exist_ok=True)
    path = os.path.join(DIAG_DIR, "engine_self_tests.csv")
    out.to_csv(path, index=False)
    cli.saved(path)
    cli.box("ENGINE SELF-TESTS", [
        ("Passed", f"{int(out['Pass'].sum())}/{len(out)}"),
        ("Timeline", "2027→2028→2031→2033→2042 equivalent"),
        ("Cash-flow mechanics", "verified on zero-return and strong toy paths"),
    ])
    return out


# =====================================================================
# MAIN
# =====================================================================
def main():
    global ALLOW_DOWNLOAD, STRICT_DATA_MODE
    parser = argparse.ArgumentParser(description="Enhanced Out-of-Sample Portfolio Testing — WGY 2027 Evidence Engine V7 FINAL")
    parser.add_argument("--project_root", type=str, default="",
                        help="Portable project root override; also supports STOCKERS_PROJECT_ROOT.")
    parser.add_argument("--tiingo_missing_dir", type=str, default="",
                        help="Portable Tiingo fallback override.")
    parser.add_argument("--factor_dir", type=str, default="",
                        help="Local Kenneth French factor cache directory (default: <project_root>/factors/kenneth_french).")
    parser.add_argument("--no_auto_factors", action="store_true",
                        help="Do not automatically download/cache official Kenneth French FF5 + momentum files when factor paths are omitted.")
    parser.add_argument("--refresh_factors", action="store_true",
                        help="Refresh the locally cached official Kenneth French factor files from Dartmouth/Tuck.")
    parser.add_argument("--canonical_run", action="store_true",
                        help="Freeze local data: disable Yahoo, enforce strict data audit, and fail on systematic gaps.")
    parser.add_argument("--no_download", action="store_true",
                        help="Disable Yahoo downloads without enabling strict audit failure.")
    parser.add_argument("--strict_data", action="store_true",
                        help="Raise instead of silently skipping symbol failures during portfolio construction.")
    parser.add_argument("--data_min_coverage", type=float, default=0.95,
                        help="Minimum local symbol-year coverage required in canonical mode.")
    parser.add_argument("--skip_self_tests", action="store_true",
                        help="Skip fast deterministic engine self-tests.")
    parser.add_argument("--self_test_only", action="store_true",
                        help="Run deterministic engine self-tests and exit before data/backtests.")
    parser.add_argument("--start", default="2000-01-01")
    parser.add_argument("--end", default="2026-01-01")
    parser.add_argument("--preload_years", type=int, default=2)
    parser.add_argument("--ema", type=int, default=DEFAULT_EMA_PERIOD)
    parser.add_argument("--min_sell_gain", type=float, default=DEFAULT_MIN_SELL_GAIN_PCT)
    parser.add_argument("--target_vol", type=float, default=DEFAULT_TARGET_VOL)
    parser.add_argument("--lookback_m", type=int, default=VOL_LOOKBACK_MONTHS)
    parser.add_argument("--clamp_low", type=float, default=VOL_CLAMP_LOW)
    parser.add_argument("--clamp_high", type=float, default=VOL_CLAMP_HIGH)
    parser.add_argument("--winsor", type=float, default=VOL_WINSOR_PCT)
    parser.add_argument("--per_side_bps", type=float, default=0.0,
                        help="Optional extra proportional execution-cost sensitivity. WInS commissions are flat-dollar fees.")
    parser.add_argument("--wins_stock_fee", type=float, default=25.0,
                        help="WInS flat fee per cleared stock transaction.")
    parser.add_argument("--wins_etf_fee", type=float, default=25.0,
                        help="WInS flat fee per cleared ETF transaction.")
    parser.add_argument("--wins_treasury_fee", type=float, default=10.0,
                        help="WInS flat fee per cleared Treasury transaction.")
    parser.add_argument("--slippage_mult", type=float, default=0.5,
                        help="Multiplier on true range used to approximate slippage per side.")
    parser.add_argument("--adv_window", type=int, default=20,
                        help="Number of daily observations for average dollar-volume (ADV) estimation.")
    parser.add_argument("--min_slip_bps", type=float, default=1.0,
                        help="Minimum slippage applied per side (in basis points).")
    parser.add_argument("--rebalance_monthly", action="store_true")
    parser.add_argument("--min_active", type=int, default=0)
    parser.add_argument("--min_scale", type=float, default=VOL_MIN_SCALE)
    parser.add_argument("--up_cap", type=float, default=VOL_UP_CAP)
    parser.add_argument("--down_cap", type=float, default=VOL_DOWN_CAP)
    parser.add_argument("--initial_capital", type=float, default=300_000.0)
    parser.add_argument("--mc_sims", type=int, default=MC_SIMS)
    parser.add_argument("--mc_block", type=int, default=MC_BLOCK_SIZE)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reality_boot", type=int, default=REALITY_CHECK_BOOTS,
                        help="Bootstrap resamples for White's Reality Check.")
    parser.add_argument("--spa_boot", type=int, default=SPA_BOOTS,
                        help="Bootstrap resamples for Hansen's SPA test.")

    # NEW: FF5 factor regression options
    parser.add_argument("--ff5_csv", type=str, default="",
                        help="Optional override path to FF5 daily CSV. If omitted, the official Kenneth French file is auto-cached.")
    parser.add_argument("--ff5_nw_lags", type=int, default=FF5_HAC_LAGS,
                        help="Newey–West (HAC) lags for monthly regression errors.")
    parser.add_argument("--mom_csv", type=str, default="",
                        help="Optional override path to Kenneth French daily momentum CSV. If omitted, the official file is auto-cached.")

    # 2027 WGY Laura Gao case-study projection parameters. These do NOT alter
    # any legacy robustness test; they add a separate cash-flow analysis.
    parser.add_argument("--wgy_sims", type=int, default=10000,
                        help="Bootstrap paths for the 2027 Laura Gao case-study cash-flow analysis.")
    parser.add_argument("--wgy_aux_sims", type=int, default=3000,
                        help="Paths for secondary WGY policy/tax comparisons; core mandate distribution still uses --wgy_sims.")
    parser.add_argument("--wgy_confidence", type=float, default=0.90,
                        help="Central confidence range reported to co-sponsors (default 90%%).")
    parser.add_argument("--wgy_hist_stride", type=int, default=12,
                        help="Months between historical rolling mandate replays (12 = annual starts).")
    parser.add_argument("--wgy_second_contribution_years", type=float, default=1.0,
                        help="Years after each historical path starts that the extra $150,000 is contributed (official case: 1 year, 2027→2028).")
    parser.add_argument("--wgy_withdraw_start_years", type=float, default=6.0,
                        help="Years after each historical path starts that the ten annual $50,000 withdrawals begin (official case: 6 years, 2027→2033).")
    parser.add_argument("--wgy_sponsor_lead_years", type=float, default=2.0,
                        help="Years before the first withdrawal at which co-sponsor communication is evaluated.")
    parser.add_argument("--wgy_reserve", type=float, default=WGY_NOMINAL_OPERATING_RESERVE,
                        help="Zero-yield nominal reserve ceiling. Used when --wgy_reserve_mode=nominal_ceiling.")
    parser.add_argument("--wgy_reserve_mode", choices=["nominal_ceiling", "pv_ladder"], default="pv_ladder",
                        help="Reserve sizing: full $500k nominal ceiling, or PV of the ten fixed payments at --wgy_reserve_yield.")
    parser.add_argument("--wgy_reserve_yield", type=float, default=0.025,
                        help="Planning safe yield used for the 2033 cash-flow-matched reserve. Zero-yield $500k is stress-tested separately.")
    parser.add_argument("--wgy_flex_floor", type=float, default=25000.0,
                        help="Minimum dollars retained outside the operating reserve after any 2033 facility contribution.")
    parser.add_argument("--wgy_flex_pct", type=float, default=0.20,
                        help="Minimum fraction of the post-reserve surplus retained for financial flexibility.")
    parser.add_argument("--wgy_facility_cost", type=float, default=0.0,
                        help="Optional assumed 2033 facility cost. If supplied, code also calculates co-sponsor funding need.")
    parser.add_argument("--wgy_inflation", type=float, default=0.025,
                        help="Annual inflation assumption for real-dollar reporting. Official $50k payments stay fixed nominal.")
    parser.add_argument("--wgy_tax_rate", type=float, default=0.20,
                        help="Optional implementation tax sensitivity on estimated realized positive gains. Official case base remains no-tax.")
    parser.add_argument("--wgy_tax_realization", type=float, default=0.25,
                        help="Fraction of positive monthly gains assumed realized for tax sensitivity (0-1).")
    parser.add_argument("--wgy_allocation_mode", choices=["static", "progressive", "ratchet", "goal_aware"], default="ratchet",
                        help="Mandate allocation policy: static, progressive, funded-ratio ratchet, or winner-inspired goal-aware.")
    parser.add_argument("--wgy_ratchet_buffer", type=float, default=1.15,
                        help="Funded-ratio threshold that allows the ratchet policy to lock the full reserve early.")
    parser.add_argument("--wgy_frontier_sims", type=int, default=2000,
                        help="Selection paths per safe/growth mix for the Mandate Efficient Frontier.")
    parser.add_argument("--wgy_frontier_eval_sims", type=int, default=5000,
                        help="Independent paths used to evaluate the allocation chosen by the frontier.")
    parser.add_argument("--wgy_frontier_safe_yield", type=float, default=0.0,
                        help="Annual safe-asset return used only in allocation-frontier sensitivity.")
    parser.add_argument("--wgy_frontier_min_safe", type=float, default=0.0,
                        help="Minimum safe-asset weight tested in the mandate frontier.")
    parser.add_argument("--wgy_frontier_max_safe", type=float, default=0.60,
                        help="Maximum safe-asset weight tested in the mandate frontier.")
    parser.add_argument("--wgy_frontier_step", type=float, default=0.05,
                        help="Safe-weight grid step for the mandate frontier.")
    parser.add_argument("--wgy_frontier_target_success", type=float, default=0.99,
                        help="Minimum probability of all ten payments for a frontier allocation to be feasible.")
    parser.add_argument("--wgy_crisis_sims", type=int, default=2000,
                        help="Bootstrap paths per crisis-timing stress scenario.")
    parser.add_argument("--skip_wgy_frontier", action="store_true",
                        help="Skip mandate-efficient-frontier selection/evaluation.")
    parser.add_argument("--skip_wgy_crisis", action="store_true",
                        help="Skip deliberately badly timed crisis stress tests.")
    parser.add_argument("--skip_wgy_case", action="store_true",
                        help="Skip the added Laura Gao historical mandate replay / cash-flow analysis.")

    # Winner-inspired experimental benchmarks. These are additive and never
    # replace the Stockers baseline / Lion / Chameleon tests.
    parser.add_argument("--skip_winner_proxies", action="store_true",
                        help="Skip winner-inspired sector / structural benchmark engines.")
    parser.add_argument("--winner_sector_top_n", type=int, default=3,
                        help="Number of sectors held by the winner-inspired rotation proxy.")
    parser.add_argument("--winner_riskoff_cash", type=float, default=0.40,
                        help="Cash fraction in the winner-inspired sector proxy during risk-off months.")

    # macOS shell quality-of-life: a command like `\ --canonical_run` passes
    # an option token with a leading space. Normalize only option-looking tokens
    # so this common copy/paste mistake does not trigger an opaque argparse failure.
    cleaned_argv: List[str] = []
    normalized_option_tokens = 0
    for tok in sys.argv[1:]:
        fixed = tok.lstrip() if tok.startswith(" ") and tok.lstrip().startswith("--") else tok
        normalized_option_tokens += int(fixed != tok)
        cleaned_argv.append(fixed)
    args = parser.parse_args(cleaned_argv)
    if normalized_option_tokens:
        print(f"NOTE: normalized {normalized_option_tokens} shell option token(s) with leading spaces.")

    _configure_project_paths(args.project_root or None, args.tiingo_missing_dir or None, args.factor_dir or None)
    if args.canonical_run or args.no_download:
        ALLOW_DOWNLOAD = False
    STRICT_DATA_MODE = bool(args.canonical_run or args.strict_data)

    # Effective reserve target: official/conservative headline is the full nominal
    # $500k ceiling. A yield-aware PV ladder can be selected explicitly as a
    # sensitivity/implementation scenario; it never assumes future Treasury yields
    # are known today.
    if args.wgy_reserve_mode == "pv_ladder":
        args.wgy_effective_reserve = _wgy_pv_ladder_reserve(args.wgy_reserve_yield)
    else:
        args.wgy_effective_reserve = float(args.wgy_reserve)

    set_global_seed(args.seed)

    # Use global constants, allowing CLI override for clamp values
    DD_CUTS = DD_THROTTLE_CUTS
    CLAMP = (args.clamp_low, args.clamp_high)

    # Parameter grids are defined globally in constants

    cli.banner("WGY 2027 Portfolio Evidence Engine V7.1 FINAL",
               "Survivorship-Aware Backtest • OOS • Multiple-Testing • Liability Engineering")

    cli.section("Run Configuration")
    cli.kv("Sample Window", f"{args.start} → {args.end}")
    cli.kv("EMA Period", str(args.ema))
    cli.kv("Min Sell Gain", f"{args.min_sell_gain:.0f}%")
    cli.kv("Target Vol", f"{args.target_vol:.2f}")
    cli.kv("Preload Years", str(args.preload_years))
    cli.kv("Seed", str(args.seed))
    cli.kv("Extra Prop. Cost", f"{args.per_side_bps:.1f} bps/side")
    cli.kv("WInS Stock Fee", f"${args.wins_stock_fee:.2f}/transaction")
    cli.kv("WInS ETF Fee", f"${args.wins_etf_fee:.2f}/transaction")
    cli.kv("WInS Treasury Fee", f"${args.wins_treasury_fee:.2f}/transaction")
    cli.kv("Slippage Mult", f"{args.slippage_mult:.2f}× range")
    cli.kv("ADV Window", f"{args.adv_window} days")
    cli.kv("Min Slippage", f"{args.min_slip_bps:.1f} bps/side")
    cli.kv("Project Root", PROJECT_ROOT)
    cli.kv("Primary Data", DATA_DIR)
    cli.kv("Tiingo Fallback", TIINGO_MISSING_DIR)
    cli.kv("Factor Cache", FACTOR_DIR)
    cli.kv("Output Directory", CSVA_DIR)
    cli.kv("Canonical / Frozen", "YES" if args.canonical_run else "NO")
    cli.kv("Yahoo Downloads", "enabled" if ALLOW_DOWNLOAD else "disabled")
    cli.kv("Strict Data Mode", "enabled" if STRICT_DATA_MODE else "disabled")
    cli.kv("WGY Case Engine", "enabled" if not args.skip_wgy_case else "skipped")
    if not args.skip_wgy_case:
        cli.kv("WGY Historical Replay", f"$300k @ t=0; +$150k @ t=+{args.wgy_second_contribution_years:g}y")
        cli.kv("WGY First Withdrawal", f"t=+{args.wgy_withdraw_start_years:g}y; then 10 × $50k annually")
        cli.kv("WGY Sponsor Checkpoint", f"{args.wgy_sponsor_lead_years:g}y before first withdrawal")
        cli.kv("WGY Allocation Policy", args.wgy_allocation_mode)
        cli.kv("WGY Reserve Sizing", args.wgy_reserve_mode)
        cli.kv("WGY Operating Reserve", _fmt_money(args.wgy_effective_reserve))
        cli.kv("WGY Reserve Yield Assumption", f"{args.wgy_reserve_yield * 100:.2f}%/yr")
        cli.kv("WGY Flexibility Rule", f"retain max({_fmt_money(args.wgy_flex_floor)}, {args.wgy_flex_pct * 100:.1f}% of post-reserve surplus)")
        cli.kv("WGY Inflation", f"{args.wgy_inflation * 100:.2f}% (real-dollar reporting)")
        cli.kv("WGY Official Tax Base", "0% (case-compliant headline)")
        cli.kv("WGY Tax Sensitivity", f"{args.wgy_tax_rate * 100:.1f}% × {args.wgy_tax_realization * 100:.0f}% realized-gain proxy")
        cli.kv("Mandate Frontier", "skipped" if args.skip_wgy_frontier else f"target ≥ {args.wgy_frontier_target_success*100:.1f}% success")
        cli.kv("Crisis Timing Tests", "skipped" if args.skip_wgy_crisis else f"{args.wgy_crisis_sims} paths/scenario")
        cli.kv("Winner Proxy Tests", "skipped" if args.skip_winner_proxies else
               f"enabled (top {args.winner_sector_top_n} sectors; risk-off cash {args.winner_riskoff_cash * 100:.0f}%)")

    ensure_dirs()
    pd.DataFrame([
        {"AssetType": "Stock", "WInSFlatFeePerTransaction": float(args.wins_stock_fee)},
        {"AssetType": "ETF", "WInSFlatFeePerTransaction": float(args.wins_etf_fee)},
        {"AssetType": "Treasury", "WInSFlatFeePerTransaction": float(args.wins_treasury_fee)},
    ]).to_csv(os.path.join(DIAG_DIR, "wins_platform_fee_assumptions.csv"), index=False)
    if not args.skip_self_tests:
        run_engine_self_tests()
    if args.self_test_only:
        cli.info("Self-test-only run complete.")
        return

    # Resolve factor inputs up front so a missing internet/cache problem is discovered
    # before the expensive portfolio/WFA/bootstrap batteries run. Manual paths override
    # the official auto-cache on a file-by-file basis.
    resolved_ff5_path = str(args.ff5_csv or "").strip()
    resolved_mom_path = str(args.mom_csv or "").strip()
    if not args.no_auto_factors and (not resolved_ff5_path or not resolved_mom_path):
        try:
            auto_ff5, auto_mom = ensure_kenneth_french_factors(
                factor_dir=FACTOR_DIR, refresh=bool(args.refresh_factors), required_end=args.end
            )
            resolved_ff5_path = resolved_ff5_path or auto_ff5
            resolved_mom_path = resolved_mom_path or auto_mom
        except Exception as exc:
            cli.warn(f"Automatic Kenneth French factor setup failed — {exc}")
    args.resolved_ff5_csv = resolved_ff5_path
    args.resolved_mom_csv = resolved_mom_path

    data_audit = run_data_integrity_audit(
        YEARLY_TOP20, DATA_DIR, args.start, args.end,
        strict=bool(args.canonical_run), min_coverage=float(args.data_min_coverage),
    )
    save_manifest(vars(args), out_dir=CSVA_DIR)
    write_yearly_top20_csv(YEARLY_TOP20, out_path=os.path.join(CSVA_DIR, "yearly_top20.csv"))

    # ===== FULL-SAMPLE BASELINE =====
    cli.section("Full-Sample Baseline Portfolio")
    port_raw, _, active_sets = build_portfolio_returns_from_csv(
        YEARLY_TOP20, DATA_DIR, start=args.start, end=args.end,
        ema_period=args.ema, min_sell_gain_pct=args.min_sell_gain, min_active_names=args.min_active,
        preload_years=args.preload_years,
        verbose=True  # This is a main run
    )
    master_trades = consolidate_all_trades(SIGNALS_DIR)
    costs, trade_costs = estimate_turnover_cost(
        port_raw.index, active_sets, per_side_bps=args.per_side_bps,
        rebalance_monthly=args.rebalance_monthly, master_trades=master_trades,
        data_dir=DATA_DIR, slippage_mult=args.slippage_mult, adv_window=args.adv_window,
        min_slip_bps=args.min_slip_bps, initial_capital=args.initial_capital,
        fixed_stock_fee=args.wins_stock_fee,
    )
    port_after_cost = (port_raw - costs)

    # --- Define scaling kwargs centrally ---
    scaling_kwargs = dict(
        de_risk_only=True,
        min_scale=args.min_scale,
        up_speed_cap=args.up_cap,
        down_speed_cap=args.down_cap,
        use_vol_target=True,
        use_dd_throttle=True
    )

    port_scaled = apply_vol_target_and_dd_throttle(
        port_after_cost, target_vol_yr=args.target_vol, lookback_m=args.lookback_m,
        clamp=CLAMP, dd_cuts=DD_CUTS, winsor=args.winsor,
        verbose=True,  # This is a main run
        **scaling_kwargs
    )
    baseline_stats = summarize_and_plot(port_scaled, initial_capital=args.initial_capital)

    # ===== Kenneth French factor regressions =====
    # Manual --ff5_csv/--mom_csv paths remain supported, but by default V6 fetches
    # the official Dartmouth/Tuck archives once, caches them under PROJECT_ROOT,
    # hashes them, and reuses the same files on subsequent/canonical runs.
    ff5_path = str(getattr(args, "resolved_ff5_csv", "") or "").strip()
    mom_path = str(getattr(args, "resolved_mom_csv", "") or "").strip()
    if ff5_path:
        try:
            run_ff5_regression_monthly(
                port_scaled, ff5_daily_csv=ff5_path, nw_lags=args.ff5_nw_lags,
                out_dir=CSVA_DIR, label="portfolio"
            )
        except Exception as e:
            cli.warn(f"FF5 regression failed — {e}")
    else:
        cli.warn("FF5 regression skipped: no cached/override factor file available.")

    if ff5_path and mom_path:
        try:
            run_ff6_momentum_regression_monthly(
                port_scaled, ff5_daily_csv=ff5_path, momentum_daily_csv=mom_path,
                nw_lags=args.ff5_nw_lags, out_dir=CSVA_DIR, label="portfolio"
            )
        except Exception as e:
            cli.warn(f"FF5+MOM regression failed — {e}")
    elif ff5_path:
        cli.warn("Momentum-controlled regression skipped because the momentum factor cache is unavailable.")

    # Consolidate trades
    if not master_trades.empty:
        master_path = os.path.join(CSVA_DIR, "ALL_TRADES_MASTER.csv")
        master_trades.to_csv(master_path, index=False)
        cli.saved(master_path)

        # Trade costs summary
        if trade_costs.empty:
            cli.warn("Trade cost model fell back to churn-based estimate (missing execution bars).")
        diag = summarize_turnover_and_trade_costs(
            active_sets, port_raw.index, master_trades,
            per_side_bps=args.per_side_bps, trade_costs=trade_costs, cost_series=costs,
        )
        box_items = [
            ("Avg Monthly Churn", f"{diag['avg_monthly_churn'] * 100:.1f}%"),
            ("Turnover / Year", f"{diag['turnover_per_year']:.1f}%"),
            ("Trades / Month", f"{diag['trades_per_month']:.2f}"),
            ("Cost Drag (bps/yr)", f"{diag['implied_cost_drag_bps_per_year']:.0f}"),
        ]
        if "median_round_trip_bps" in diag:
            box_items.append(("Median Round Trip (bps)", f"{diag['median_round_trip_bps']:.0f}"))
        if "avg_participation" in diag:
            box_items.append(("Avg %ADV Used", f"{diag['avg_participation'] * 100:.1f}%"))
        cli.box("CAPACITY & COSTS", box_items)

    # ===== YEAR-BY-YEAR COMPARISON WITH SPY AND QQQ =====
    yearly_comp = build_yearly_comparison_table_o2o(
        port_scaled, DATA_DIR, args.start, args.end,
        symbols=["SPY", "QQQ"]
    )

    # ===== MONTE CARLO BOOTSTRAP =====
    mc_results = monte_carlo_bootstrap(port_scaled, n_sims=args.mc_sims, block_size=args.mc_block, seed=args.seed)

    # ===== 2027 WGY LAURA GAO CASE-STUDY CASH-FLOW ANALYSIS =====
    # This is additive: no legacy test above or below is removed or changed.
    wgy_case = {}
    if not args.skip_wgy_case:
        try:
            wgy_case = run_wgy_2027_case_study(
                port_scaled,
                n_sims=args.wgy_sims,
                aux_sims=args.wgy_aux_sims,
                block_size=args.mc_block,
                seed=args.seed + 2027,
                confidence=args.wgy_confidence,
                historical_stride_months=args.wgy_hist_stride,
                second_contribution_month=max(1, int(round(args.wgy_second_contribution_years * 12))),
                withdrawal_start_month=max(1, int(round(args.wgy_withdraw_start_years * 12))),
                sponsor_lead_months=max(0, int(round(args.wgy_sponsor_lead_years * 12))),
                operating_reserve_target=args.wgy_effective_reserve,
                flex_floor_dollars=args.wgy_flex_floor,
                flex_floor_pct=args.wgy_flex_pct,
                reserve_yield_annual=args.wgy_reserve_yield,
                facility_cost=args.wgy_facility_cost,
                inflation=args.wgy_inflation,
                tax_rate=args.wgy_tax_rate,
                tax_realization_fraction=args.wgy_tax_realization,
                allocation_mode=args.wgy_allocation_mode,
                ratchet_buffer=args.wgy_ratchet_buffer,
                frontier_sims=args.wgy_frontier_sims,
                frontier_eval_sims=args.wgy_frontier_eval_sims,
                frontier_safe_yield=args.wgy_frontier_safe_yield,
                frontier_min_safe=args.wgy_frontier_min_safe,
                frontier_max_safe=args.wgy_frontier_max_safe,
                frontier_step=args.wgy_frontier_step,
                frontier_target_success=args.wgy_frontier_target_success,
                crisis_sims=args.wgy_crisis_sims,
                skip_frontier=args.skip_wgy_frontier,
                skip_crisis=args.skip_wgy_crisis,
            )
        except Exception as e:
            if args.canonical_run:
                raise
            cli.warn(f"WGY 2027 case-study analysis failed — {e}")

    # ======================================================================
    # DUMB UNIVERSE COMPARISON (CSV-ONLY) + YEAR-BY-YEAR VS SPY/QQQ
    # ======================================================================
    try:
        cli.info("Building dumb (equal-weight) universe baseline from CSVs...")
        dumb_monthly = build_dumb_universe_monthly(YEARLY_TOP20, args.start, args.end)
        dumb_eq = (1.0 + dumb_monthly).cumprod() * args.initial_capital
        months = len(dumb_monthly)
        years_span = months / 12.0 if months > 1 else 0.0
        dumb_total = (1.0 + dumb_monthly).prod() - 1.0 if months > 0 else 0.0
        dumb_cagr = (1.0 + dumb_total) ** (1.0 / years_span) - 1.0 if years_span > 0 else 0.0
        mu, sd = dumb_monthly.mean(), dumb_monthly.std()
        dumb_sharpe = (mu / sd) * np.sqrt(12) if sd > 0 else 0.0
        dumb_dd = (dumb_eq / dumb_eq.cummax() - 1).min()

        tbl = pd.DataFrame({
            "Metric": ["FinalCapital", "TotalReturn%", "CAGR", "Sharpe(ann)", "MaxDrawdown%", "Months"],
            "Value": [
                f"${dumb_eq.iloc[-1]:,.2f}" if len(dumb_eq) else "NA",
                f"{dumb_total * 100:.2f}%",
                f"{dumb_cagr * 100:.2f}%",
                f"{dumb_sharpe:.2f}",
                f"{dumb_dd * 100:.2f}%",
                f"{months}"
            ],
        })
        cli.section("Dumb Universe Comparison (Equal-Weight, CSV-only)")
        cli.table(tbl, "Equal-Weight Universe Only (No Timing)")

        spy_m = load_bench_monthlies("SPY")
        qqq_m = load_bench_monthlies("QQQ")

        # Proper yearly % returns
        eq_port_y = (1 + dumb_monthly).groupby([dumb_monthly.index.year]).prod() - 1
        eq_spy = (1 + spy_m).groupby([spy_m.index.year]).prod() - 1 if not spy_m.empty else pd.Series(dtype=float)
        eq_qqq = (1 + qqq_m).groupby([qqq_m.index.year]).prod() - 1 if not qqq_m.empty else pd.Series(dtype=float)

        years = sorted(set(eq_port_y.index) | set(eq_spy.index) | set(eq_qqq.index))
        rows = []
        for y in years:
            rows.append({
                "Year": y,
                "Portfolio": round(eq_port_y.get(y, np.nan) * 100, 2),
                "SPY": round(eq_spy.get(y, np.nan) * 100, 2) if not eq_spy.empty else np.nan,
                "QQQ": round(eq_qqq.get(y, np.nan) * 100, 2) if not qqq_m.empty else np.nan,
            })

        yearly_df = pd.DataFrame(rows).sort_values("Year")
        outp = os.path.join(CSVA_DIR, "yearly_vs_bench.csv")
        os.makedirs(os.path.dirname(outp), exist_ok=True)
        yearly_df.to_csv(outp, index=False)
        cli.section("Calendar-Year Returns (%): Portfolio vs SPY & QQQ (Dumb Universe)")
        cli.table(yearly_df, "Yearly % Returns (Dumb)")
        cli.info(f"Saved yearly table → {outp}")

        plot_yearly_line_charts(
            port_monthly=port_scaled,
            equal_weight_monthly=dumb_monthly,
            spy_monthly=spy_m,
            qqq_monthly=qqq_m,
        )

    except Exception as e:
        cli.warn(f"Dumb universe comparison failed — {e}")

    # ===== PARAMETER SENSITIVITY =====
    param_results, strategy_grid = monte_carlo_parameter_sensitivity(
        YEARLY_TOP20,
        DATA_DIR,
        args.start,
        args.end,
        preload_years=args.preload_years,
        n_trials=25,
        seed=args.seed,
        ema_range=EMA_GRID_RANGE,
        gain_range=GAIN_GRID_RANGE,
        per_side_bps=args.per_side_bps,
        target_vol=args.target_vol,
        lookback_m=args.lookback_m,
        clamp=CLAMP,
        dd_cuts=DD_CUTS,
        winsor=args.winsor,
        scaling_kwargs=scaling_kwargs,
        min_active_names=args.min_active,
    )

    wrc_result = {}
    spa_df = pd.DataFrame()
    spa_best = None
    if strategy_grid is not None and not strategy_grid.empty:
        wrc_result = whites_reality_check(
            strategy_grid,
            n_boot=args.reality_boot,
            block_size=args.mc_block,
        ) or {}
        spa_df = hansen_spa(
            strategy_grid,
            n_boot=args.spa_boot,
            block_size=args.mc_block,
        )
        if spa_df is not None and not spa_df.empty and "SPA_pvalue" in spa_df.columns:
            spa_best = spa_df.loc[spa_df["SPA_pvalue"].idxmin()]

    psr_dsr = run_probabilistic_deflated_sharpe(port_scaled, strategy_grid, out_dir=DIAG_DIR)

    # ======================================================================
    # ===== THE ULTIMATE SHOWDOWN: LION vs. CHAMELEON ======================
    # ======================================================================

    # --- Collect all WFA args into one dict ---
    WFA_KWARGS = {
        "yearly_map": YEARLY_TOP20, "data_dir": DATA_DIR,
        "start_date": args.start, "end_date": args.end,
        "train_years": WFA_TRAIN_YEARS, "test_years": WFA_TEST_YEARS, "step_years": WFA_STEP_YEARS,
        "preload_years": args.preload_years,
        "per_side_bps": args.per_side_bps,
        "target_vol": args.target_vol,
        "lookback_m": args.lookback_m,
        "clamp": CLAMP,
        "dd_cuts": DD_CUTS,
        "winsor": args.winsor,
        "scaling_kwargs": scaling_kwargs,
        "min_active_names": args.min_active,
    }

    # --- 1. RUN THE "LION" (STATIC) TEST ---
    # Uses the single set of params from the CLI args
    lion_df, lion_series = walk_forward_validation(
        static_ema=args.ema,
        static_gain=args.min_sell_gain,
        **WFA_KWARGS
    )

    # Summarize the final stitched "Lion" curve
    lion_stats = {}
    if lion_series is not None and not lion_series.empty:
        lion_stats = summarize_and_plot(
            lion_series, initial_capital=args.initial_capital,
            out_csv=os.path.join(OOS_DIR, "lion_oos_stitched_returns.csv"),
            out_png=os.path.join(OOS_DIR, "lion_oos_stitched_equity.png")
        )

    # --- 2. RUN THE "CHAMELEON" (ADAPTIVE) TEST ---
    # Uses the param grids to re-optimize
    chameleon_df, chameleon_series = walk_forward_optimization(
        ema_range=EMA_GRID_RANGE,
        gain_range=GAIN_GRID_RANGE,
        **WFA_KWARGS
    )

    # Summarize the final stitched "Chameleon" curve
    chameleon_stats = {}
    if chameleon_series is not None and not chameleon_series.empty:
        chameleon_stats = summarize_and_plot(
            chameleon_series, initial_capital=args.initial_capital,
            out_csv=os.path.join(OOS_DIR, "chameleon_oos_stitched_returns.csv"),
            out_png=os.path.join(OOS_DIR, "chameleon_oos_stitched_equity.png")
        )

    # --- 3. RUN THE "MONEY SHOT" COMPARISON PLOT ---
    plot_wfa_comparison(
        lion_series=lion_series,
        chameleon_series=chameleon_series,
        initial_capital=args.initial_capital
    )

    # ===== WINNER-INSPIRED ENGINE PROXIES (ADDITIVE) =====
    winner_proxies: Dict[str, pd.Series] = {}
    winner_sector_weights = pd.DataFrame()
    if not args.skip_winner_proxies:
        try:
            winner_proxies, winner_sector_weights = build_winner_inspired_growth_proxies(
                lion_series=lion_series,
                data_dir=DATA_DIR,
                start=args.start,
                end=args.end,
                top_n=args.winner_sector_top_n,
                riskoff_cash=args.winner_riskoff_cash,
                per_side_bps=args.per_side_bps,
            )
            if not winner_sector_weights.empty:
                wp = os.path.join(WGY_DIR, "winner_sector_rotation_weights.csv")
                winner_sector_weights.to_csv(wp)
                cli.saved(wp)
            if winner_proxies:
                for label, s in winner_proxies.items():
                    outp = os.path.join(
                        WGY_DIR,
                        "winner_proxy_" + re.sub(r"[^0-9A-Za-z]+", "_", label).strip("_").lower() + ".csv"
                    )
                    pd.DataFrame({"Return": s}).to_csv(outp)
                    cli.saved(outp)
                _winner_plot_engine_equity(
                    {"Lion static OOS": lion_series, **winner_proxies},
                    os.path.join(WGY_DIR, "winner_inspired_engine_equity.png"),
                    initial_capital=WGY_INITIAL_CONTRIBUTION,
                )
                cli.info(
                    "Winner-inspired proxies are experimental benchmarks, not a claim that "
                    "the winning team's proprietary model has been exactly reproduced."
                )
        except Exception as exc:
            cli.warn(f"Winner-inspired proxy construction failed — {exc}")
            winner_proxies = {}

    # ===== CLIENT-MANDATE GROWTH ENGINE COMPARISON =====
    # The old finale lesson was that the model is not the strategy. Here we test
    # the baseline, static-OOS Lion, and adaptive-OOS Chameleon as interchangeable
    # growth engines INSIDE the exact same cash-flow / reserve / tax / inflation
    # architecture. This answers "which engine helps Laura's mandate most?" rather
    # than merely "which engine has the highest standalone CAGR?"
    growth_engine_comp = pd.DataFrame()
    if not args.skip_wgy_case:
        try:
            growth_return_map: Dict[str, pd.Series] = {
                "Baseline full-sample": port_scaled,
                "Lion static OOS": lion_series,
                "Chameleon adaptive OOS": chameleon_series,
            }
            growth_return_map.update(winner_proxies)
            growth_engine_comp = _wgy_compare_growth_return_series(
                growth_return_map,
                n_sims=min(int(args.wgy_sims), 5000),
                block_size=args.mc_block,
                seed=args.seed + 7707,
                second_contribution_month=max(1, int(round(args.wgy_second_contribution_years * 12))),
                withdrawal_start_month=max(1, int(round(args.wgy_withdraw_start_years * 12))),
                sponsor_lead_months=max(0, int(round(args.wgy_sponsor_lead_years * 12))),
                operating_reserve_target=args.wgy_effective_reserve,
                flex_floor_dollars=args.wgy_flex_floor,
                flex_floor_pct=args.wgy_flex_pct,
                reserve_yield_annual=args.wgy_reserve_yield,
                facility_cost=args.wgy_facility_cost,
                inflation=args.wgy_inflation,
                tax_rate=0.0,
                tax_realization_fraction=0.0,
                allocation_mode=args.wgy_allocation_mode,
                ratchet_buffer=args.wgy_ratchet_buffer,
            )
            if not growth_engine_comp.empty:
                gep = os.path.join(WGY_DIR, "wgy_growth_engine_comparison.csv")
                growth_engine_comp.to_csv(gep, index=False)
                cli.saved(gep)
                cli.table(_wgy_format_growth_engine_comparison(growth_engine_comp),
                          "Growth Engine Comparison Under Laura's Mandate")
                if isinstance(wgy_case, dict):
                    wgy_case["growth_engine_comparison"] = growth_engine_comp
        except Exception as exc:
            cli.warn(f"WGY growth-engine comparison failed — {exc}")

    # ===== FINAL SUMMARY =====
    cli.section("FINAL ROBUSTNESS SUMMARY")

    cli.box("Baseline Full-Sample Performance", [
        ("CAGR", f"{baseline_stats.get('CAGR', 0) * 100:.2f}%"),
        ("Sharpe", f"{baseline_stats.get('Sharpe', 0):.2f}"),
        ("Max DD", f"{baseline_stats.get('MaxDrawdownPct', 0):.2f}%"),
    ])

    cli.box("Monte Carlo Bootstrap (on Baseline)", [
        ("Sims", f"{args.mc_sims}"),
        ("Median CAGR", f"{mc_results['CAGR'].median() * 100:.2f}%"),
        ("5th %ile CAGR", f"{mc_results['CAGR'].quantile(0.05) * 100:.2f}%"),
        ("Median Sharpe", f"{mc_results['Sharpe'].median():.2f}"),
        ("5th %ile Sharpe", f"{mc_results['Sharpe'].quantile(0.05):.2f}"),
    ])

    if not lion_df.empty:
        cli.box("WFA 'Lion' (Static OOS) Summary", [
            ("Avg OOS CAGR", f"{lion_df['OOS_CAGR'].mean() * 100:.2f}%"),
            ("Avg OOS Sharpe", f"{lion_df['OOS_Sharpe'].mean():.2f}"),
            ("Final Stitched CAGR", f"{lion_stats.get('CAGR', 0) * 100:.2f}%"),
            ("Final Stitched Sharpe", f"{lion_stats.get('Sharpe', 0):.2f}"),
        ])

    if not chameleon_df.empty:
        cli.box("WFA 'Chameleon' (Adaptive OOS) Summary", [
            ("Avg OOS CAGR", f"{chameleon_df['OOS_CAGR'].mean() * 100:.2f}%"),
            ("Avg OOS Sharpe", f"{chameleon_df['OOS_Sharpe'].mean():.2f}"),
            ("Final Stitched CAGR", f"{chameleon_stats.get('CAGR', 0) * 100:.2f}%"),
            ("Final Stitched Sharpe", f"{chameleon_stats.get('Sharpe', 0):.2f}"),
        ])

    if wgy_case:
        cli.box("WGY Historical Mandate Replay Summary", [
            ("Facility Range", f"{_fmt_money(wgy_case.get('pledge_low', np.nan))} – {_fmt_money(wgy_case.get('pledge_high', np.nan))}"),
            ("Median Facility", _fmt_money(wgy_case.get('pledge_median', np.nan))),
            ("Mandate Hurdle CAGR", f"{wgy_case.get('hurdle_cagr', 0.0) * 100:.2f}%"),
            ("Allocation Policy", str(wgy_case.get('allocation_mode', '-'))),
            ("Full Reserve", f"{wgy_case.get('reserve_funding_prob', 0.0) * 100:.2f}%"),
            ("Reserve-Only 10/10", f"{wgy_case.get('reserve_only_prob', 0.0) * 100:.2f}%"),
            ("Portfolio 10/10", f"{wgy_case.get('operating_prob', 0.0) * 100:.2f}%"),
        ])

    rc_spa_items = []
    if wrc_result:
        strat = wrc_result.get("best_strategy")
        if isinstance(strat, tuple) and len(strat) >= 2:
            strat_label = f"EMA {strat[0]}, Gain {strat[1]:.1f}%"
        else:
            strat_label = str(strat) if strat is not None else "-"
        rc_spa_items.extend([
            ("WRC Best Combo", strat_label),
            ("WRC p-value", _fmt_pvalue(float(wrc_result.get('p_value', float('nan'))))),
        ])
    if spa_best is not None:
        rc_spa_items.extend([
            ("SPA Best Combo", f"EMA {spa_best['EMA_Period']:.0f}, Gain {spa_best['Min_Gain_Pct']:.1f}%"),
            ("SPA p-value", _fmt_pvalue(float(spa_best['SPA_pvalue']))),
        ])
    if rc_spa_items:
        cli.box("Reality Check & SPA", rc_spa_items)

    if "SPY" in yearly_comp.columns and "QQQ" in yearly_comp.columns:
        cli.box("vs Benchmarks (Year-by-Year, Baseline)", [
            ("Years Analyzed", str(len(yearly_comp))),
            ("Win Rate vs SPY", f"{(yearly_comp['Portfolio'] > yearly_comp['SPY']).mean() * 100:.1f}%"),
            ("Win Rate vs QQQ", f"{(yearly_comp['Portfolio'] > yearly_comp['QQQ']).mean() * 100:.1f}%"),
            ("Avg vs SPY", f"{yearly_comp['vs_SPY'].mean() * 100:+.2f}%"),
            ("Avg vs QQQ", f"{yearly_comp['vs_QQQ'].mean() * 100:+.2f}%"),
        ])

    if DATA_QUALITY_EVENTS:
        dq_path = os.path.join(DIAG_DIR, "data_quality_events.csv")
        pd.DataFrame(DATA_QUALITY_EVENTS).to_csv(dq_path, index=False)
        cli.saved(dq_path)
        cli.warn(f"{len(DATA_QUALITY_EVENTS)} data-quality event(s) were logged; review {dq_path} before citing results.")

    cli.line("═", cli.HDR)
    cli.info("All robustness tests completed. Results saved to output directories.")
    cli.info(f"Saved files location: {CSVA_DIR}")


if __name__ == "__main__":
    main()