"""Parse NSE bhavcopy files (legacy and UDiFF) into one normalized schema.

Output columns (one row per security per session):
    date, symbol, series, isin, open, high, low, close, prevclose, volume, value

`prevclose` is NSE's base price for the day. On a corporate-action ex-date NSE
adjusts it (e.g. halves it for a 2:1 split), so close / prevclose - 1 is the
day's return free of split/bonus jumps. That is the key fact the panel relies on.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

# Equity series kept. BE/BZ (trade-for-trade, non-compliant) matter: stocks are
# moved there when in trouble, so dropping them would itself create survivorship bias.
EQUITY_SERIES = ("EQ", "BE", "BZ")
COLUMNS = ["date", "symbol", "series", "isin", "open", "high", "low", "close", "prevclose", "volume", "value"]

LEGACY = {"SYMBOL": "symbol", "SERIES": "series", "OPEN": "open", "HIGH": "high", "LOW": "low",
          "CLOSE": "close", "PREVCLOSE": "prevclose", "TOTTRDQTY": "volume", "TOTTRDVAL": "value",
          "TIMESTAMP": "date", "ISIN": "isin"}
UDIFF = {"TckrSymb": "symbol", "SctySrs": "series", "OpnPric": "open", "HghPric": "high", "LwPric": "low",
         "ClsPric": "close", "PrvsClsgPric": "prevclose", "TtlTradgVol": "volume", "TtlTrfVal": "value",
         "TradDt": "date", "ISIN": "isin"}


def read_csv_bytes(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), dtype=str, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]
    if "TckrSymb" in df.columns:
        df = df.rename(columns=UDIFF)
        date_fmt = "%Y-%m-%d"
    elif "SYMBOL" in df.columns:
        df = df.rename(columns=LEGACY)
        date_fmt = "%d-%b-%Y"
    else:
        raise ValueError(f"unknown bhavcopy format: {list(df.columns)[:6]}")
    if "isin" not in df.columns:
        df["isin"] = pd.NA
    df = df[COLUMNS].copy()
    for c in ("symbol", "series", "isin"):
        df[c] = df[c].astype("string").str.strip()
    raw_date = df["date"].str.strip().str.title()
    parsed = pd.to_datetime(raw_date, format=date_fmt, errors="coerce")
    if parsed.isna().any() and date_fmt == "%d-%b-%Y":      # some 2020 files use 2-digit years
        parsed = parsed.fillna(pd.to_datetime(raw_date, format="%d-%b-%y", errors="coerce"))
    if parsed.isna().any():
        raise ValueError(f"unparsed dates, e.g. {raw_date[parsed.isna()].iloc[0]!r}")
    df["date"] = parsed
    for c in ("open", "high", "low", "close", "prevclose", "volume", "value"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["series"].isin(EQUITY_SERIES)]
    df = df[(df["close"] > 0) & (df["prevclose"] > 0)]
    return df.reset_index(drop=True)


def read_file(path: Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            return read_csv_bytes(z.read(name))
    return read_csv_bytes(path.read_bytes())


def read_all(files: list[Path], log=None) -> pd.DataFrame:
    parts = []
    for i, f in enumerate(sorted(files)):
        parts.append(read_file(f))
        if log and i % 500 == 0:
            log(f"parsed {i + 1}/{len(files)} {f.name}")
    df = pd.concat(parts, ignore_index=True)
    # The same security can appear in two series on one day only transiently; keep the
    # row with the larger traded value.
    df = df.sort_values(["date", "symbol", "value"]).drop_duplicates(["date", "symbol"], keep="last")
    df["symbol"] = df["symbol"].astype("string")
    return df.reset_index(drop=True)


# Funds that traded before NSE files carried ISINs (checked against the 2010 files).
LEGACY_FUND_PATTERN = (r"BEES$|ETF$|^QNIFTY$|^QGOLDHALF$|^KOTAKGOLD$|^KOTAKNIFTY$|^RELGOLD$|"
                       r"^GOLDSHARE$|^UTINIFTY$|^M50$|^HDFCMFGETF$")


def is_fund(symbol: pd.Series, isin: pd.Series) -> pd.Series:
    """ETFs / mutual-fund units: ISIN prefix INF, or legacy names before ISINs were published."""
    by_isin = isin.fillna("").str.startswith("INF")
    by_name = symbol.fillna("").str.contains(LEGACY_FUND_PATTERN, regex=True)
    return by_isin | (isin.isna() & by_name)


