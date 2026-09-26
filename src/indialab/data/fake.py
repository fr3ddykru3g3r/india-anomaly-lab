"""Fake NSE archive with known truth, for end-to-end tests of the data pipeline.

Writes bhavcopy zips in the real formats (legacy without ISIN, legacy with ISIN,
UDiFF) for a handful of companies, with the events that break naive pipelines:

    split       2:1 on an ex-date; NSE halves prevclose; a new ISIN is issued
    bonus       1:1, same mechanics
    rename      symbol changes (before ISINs existed, so only the symbol-change list links it)
    delisting   distressed firm moves to series BE, collapses, then disappears
    reuse       a new company later trades under the dead company's symbol
    ipo         a company lists mid-sample (listing day is not investable)
    missing     one session's file is absent from the archive

`truth` holds every company's true daily return; the panel must reproduce it.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .nse import UDIFF_FROM, bhavcopy_url

LEGACY_COLS = ["SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE", "TOTTRDQTY",
               "TOTTRDVAL", "TIMESTAMP"]


@dataclass
class Company:
    name: str
    symbols: list          # [(from_session, symbol)]
    isins: list            # [(from_session, isin)]
    start: int
    end: int | None = None
    series: list = field(default_factory=lambda: [(0, "EQ")])
    splits: dict = field(default_factory=dict)   # session -> ratio (price divides by ratio)
    purposes: dict = field(default_factory=dict) # session -> NSE purpose text for the Bc file
    dividends: dict = field(default_factory=dict)  # session -> rupees per share

    def at(self, lst, t):
        v = lst[0][1]
        for s, x in lst:
            if t >= s:
                v = x
        return v


def build(root: Path, start: str = "2010-09-01", n_sessions: int = 420, isin_from: str = "2011-01-03",
          seed: int = 7, missing_session: int = 200, with_pr: bool = True) -> dict:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n_sessions)
    isin_from = pd.Timestamp(isin_from)
    comps = [Company(f"PLAIN{i}", [(0, f"PLAIN{i}")], [(0, f"INE00{i}A01010")], 0) for i in range(6)]
    comps += [
        Company("SPLITCO", [(0, "SPLITCO")], [(0, "INE100A01010"), (250, "INE100A01028")], 0, splits={250: 2.0},
                purposes={250: "FV SPLIT RS.10 TO RS.5"}),
        Company("BONUSCO", [(0, "BONUSCO")], [(0, "INE200A01010")], 0, splits={90: 2.0},
                purposes={90: "BONUS 1:1"}),
        Company("DIVCO", [(0, "DIVCO")], [(0, "INE700A01010")], 0, dividends={180: 5.0},
                purposes={180: "DIVIDEND - RS 5 PER SHARE"}),
        Company("OLDNAME", [(0, "OLDNAME"), (60, "NEWNAME")], [(0, "INE300A01010")], 0),
        Company("DEADCO", [(0, "DEADCO")], [(0, "INE400A01010")], 0, end=150, series=[(0, "EQ"), (120, "BE")]),
        Company("REUSER", [(0, "DEADCO")], [(0, "INE500A01010")], 150 + 260),
        Company("IPOCO", [(0, "IPOCO")], [(0, "INE600A01010")], 300),
    ]
    truth, rows = {}, {t: [] for t in range(n_sessions)}
    changes, events = [], []
    for c in comps:
        if c.start >= n_sessions:
            continue
        end = min(c.end if c.end is not None else n_sessions - 1, n_sessions - 1)
        price = 100.0 * np.exp(rng.normal(0, 0.3))
        rets = {}
        for t in range(c.start, end + 1):
            r = rng.normal(0.0004, 0.02)
            if c.name == "DEADCO" and t > 120:
                r = rng.normal(-0.03, 0.03)
            prev = price
            ratio = c.splits.get(t, 1.0)
            base = prev / ratio                          # economically comparable previous price
            close = base * (1 + r)
            opn = base * (1 + r * rng.uniform(0, 1))
            hi, lo = max(opn, close) * (1 + abs(rng.normal(0, 0.005))), min(opn, close) * (1 - abs(rng.normal(0, 0.005)))
            if t > c.start:
                rets[days[t]] = r
            price = close
            rows[t].append({"symbol": c.at(c.symbols, t), "series": c.at(c.series, t), "open": opn, "high": hi,
                            "low": lo, "close": close, "prevclose": prev,   # NSE: NOT adjusted on ex-dates
                            "volume": int(rng.integers(1e4, 1e6)), "isin": c.at(c.isins, t)})
            if t in c.purposes:
                events.append({"series": "EQ", "symbol": c.at(c.symbols, t), "security": f"{c.name} Ltd, India",
                               "ex": days[t], "purpose": c.purposes[t]})
        truth[c.name] = pd.Series(rets)
        for s, sym in [x for x in c.symbols[1:] if x[0] < n_sessions]:
            old = c.at(c.symbols, s - 1)
            changes.append({"company": c.name, "old": old, "new": sym, "date": days[s]})
    root = Path(root)
    for t, d in enumerate(days):
        if t == missing_session:
            continue
        df = pd.DataFrame(rows[t])
        df["value"] = df["volume"] * df["close"]
        _write(root, d.date(), df, with_isin=d >= isin_from)
    sc = pd.DataFrame(changes, columns=["company", "old", "new", "date"])
    sc["date"] = pd.to_datetime(sc["date"])
    (root / "reference").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"NAME": sc["company"], "OLD": sc["old"], "NEW": sc["new"],
                  "DATE": sc["date"].dt.strftime("%d-%b-%Y").str.upper()}).to_csv(
        root / "reference" / "symbolchange.csv", index=False, header=False)
    if with_pr:
        _write_pr(root, days, events)
    return {"truth": truth, "days": days, "missing": days[missing_session] if missing_session >= 0 else None,
            "companies": comps, "events": events}


def _write_pr(root: Path, days: pd.DatetimeIndex, events: list) -> None:
    """Weekly PR zips whose Bc file lists events with ex-dates within +/-14 days (as NSE does)."""
    ev = pd.DataFrame(events, columns=["series", "symbol", "security", "ex", "purpose"])
    for d in days[days.weekday == 2]:
        near = ev[(ev["ex"] >= d - pd.Timedelta(days=14)) & (ev["ex"] <= d + pd.Timedelta(days=14))]
        lines = ["SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE"]
        lines += [f"{e.series},{e.symbol},{e.security},{(e.ex + pd.Timedelta(days=1)):%d/%m/%Y}, , ,"
                  f"{e.ex:%d/%m/%Y}, , ,{e.purpose}   " for e in near.itertuples()]
        lines.append("EQ,NOISE,Noise Ltd, ,01/01/2011,05/01/2011,30/12/2010, , ,ANNUAL GENERAL MEETING")
        path = root / "pr" / str(d.year) / f"PR{d:%d%m%y}.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"Bc{d:%d%m%y}.csv", "\n".join(lines) + "\n")


def _write(root: Path, d: date, df: pd.DataFrame, with_isin: bool) -> None:
    url = bhavcopy_url(d)
    path = root / "bhavcopy" / str(d.year) / Path(url).name
    path.parent.mkdir(parents=True, exist_ok=True)
    if d >= UDIFF_FROM:
        out = pd.DataFrame({"TradDt": d.isoformat(), "BizDt": d.isoformat(), "Sgmt": "CM", "Src": "NSE",
                            "FinInstrmTp": "STK", "FinInstrmId": range(len(df)), "ISIN": df["isin"],
                            "TckrSymb": df["symbol"], "SctySrs": df["series"], "OpnPric": df["open"],
                            "HghPric": df["high"], "LwPric": df["low"], "ClsPric": df["close"],
                            "LastPric": df["close"], "PrvsClsgPric": df["prevclose"], "TtlTradgVol": df["volume"],
                            "TtlTrfVal": df["value"]})
    else:
        out = pd.DataFrame({"SYMBOL": df["symbol"], "SERIES": df["series"], "OPEN": df["open"], "HIGH": df["high"],
                            "LOW": df["low"], "CLOSE": df["close"], "LAST": df["close"], "PREVCLOSE": df["prevclose"],
                            "TOTTRDQTY": df["volume"], "TOTTRDVAL": df["value"],
                            "TIMESTAMP": pd.Timestamp(d).strftime("%d-%b-%Y").upper()})
        if with_isin:
            out["TOTALTRADES"] = 100
            out["ISIN"] = df["isin"].values
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(path.name.removesuffix(".zip"), buf.getvalue())
