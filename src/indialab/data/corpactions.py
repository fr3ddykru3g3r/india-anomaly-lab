"""Corporate actions: NSE's own records (Bc files in PR archives) + a validated gap detector.

NSE bhavcopy prices are NOT adjusted for splits or bonuses: on an ex-date PREVCLOSE is
the unadjusted previous close, so a 2:1 split looks like a -50% day. Adjustment factors
therefore come from

    records   Bc<ddmmyy>.csv inside NSE's daily PR archives (Jul 2010 onward):
              BONUS a:b, face-value SPLIT / CONSOLIDATION, DIVIDEND amounts
    detector  before Jul 2010 (no records): an overnight price gap that matches a
              standard bonus/split ratio. Its precision and recall are measured on the
              2010-2026 period where the records exist (experiments/e1_data_coverage.py).

A factor R means one old share became R shares; prices divide by R.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .bhavcopy import EQUITY_SERIES

DATE_OR_BLANK = re.compile(r"^\s*(\d{2}/\d{2}/\d{4}|\d{2}-[A-Za-z]{3}-\d{4}|\d{4}-\d{2}-\d{2})?\s*$")
NUM = r"(\d+(?:\.\d+)?)"
BONUS = re.compile(rf"BON(?:US)?\s*(?:ISSUE)?\s*[-:@]?\s*{NUM}\s*:\s*{NUM}", re.I)

# Typical Indian bonus (a:b -> (a+b)/b) and split (FV x -> y) ratios, for the detector.
STANDARD_RATIOS = np.array(sorted({1.1, 1.2, 1.25, 4 / 3, 1.5, 5 / 3, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 10.0,
                                   20.0, 2 * 1.5, 2 * 2, 5 * 2, 1.75}))


def _split_line(line: str) -> dict | None:
    t = line.rstrip("\r\n").split(",")
    if len(t) < 10:
        return None
    for i in range(3, len(t) - 6):
        if all(DATE_OR_BLANK.match(x) for x in t[i:i + 6]):
            return {"series": t[0].strip(), "symbol": t[1].strip(), "security": ",".join(t[2:i]).strip(),
                    "record_dt": t[i].strip(), "ex_dt": t[i + 3].strip(), "purpose": ",".join(t[i + 6:]).strip()}
    return None


def read_bc(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if Path(n).name.lower().startswith("bc") and n.lower().endswith(".csv")]
        if not names:
            return pd.DataFrame()
        text = z.read(names[0]).decode("latin-1")
    rows = [r for r in map(_split_line, text.splitlines()[1:]) if r]
    return pd.DataFrame(rows)


SPLIT_SEG = re.compile(rf"(?:RS|RE)?\.?\s*{NUM}\s*/?-?\s*(?:PER SHARE)?\s*TO\s*(?:RS|RE)?\.?\s*{NUM}", re.I)
AMOUNT = re.compile(rf"(?:RS|RE|INR)?\.?\s*{NUM}\s*(%)?", re.I)


def parse_purpose(p: str) -> dict:
    """Split/bonus factor and cash dividend (rupees per share) from an NSE purpose string.

    Purposes combine several actions ("AGM/DIV-FIN RE.1+SPL RS.3", "DIV-2.50/FV SPL RS10TORS2"),
    so the string is cut at '/' and '+' and each piece is classified on its own. 'SPL' means a
    special dividend unless the piece describes a face-value change ("x TO y").
    """
    p = " ".join(str(p).upper().replace("/-", " ").split())
    out = {"factor": 1.0, "dividend": 0.0, "kind": []}
    m = BONUS.search(p)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a > 0 and b > 0:
            out["factor"] *= (a + b) / b
            out["kind"].append("bonus")
    div_context = False
    for seg in re.split(r"[/+]", p):
        seg = seg.strip()
        if not seg or "BONUS" in seg or "WRNT" in seg or "WARRANT" in seg:
            continue
        fv = SPLIT_SEG.search(seg)
        if fv and re.search(r"SPLI?T|SPLT|SPL\b|SUB|FV|FACE|CONSOL", seg):
            x, y = float(fv.group(1)), float(fv.group(2))
            if x > 0 and y > 0 and x != y:
                out["factor"] *= x / y
                out["kind"].append("consolidation" if x < y else "split")
            continue
        if re.search(r"DIV|FIN\b|SPL\b|INT\b|SPECIAL", seg) or (div_context and re.search(r"\d", seg)):
            div_context = True
            amt = AMOUNT.findall(re.sub(r"\d+(ST|ND|RD|TH)\b", "", seg))
            vals = [float(a) for a, pct in amt if not pct]
            if vals:
                out["dividend"] += vals[0]
                out["kind"].append("dividend")
            else:
                out["kind"].append("dividend_unparsed")
    if out["factor"] == 1.0 and re.search(r"\bBON(US)?\b|SPLI?T|SPLT|SUB-?DIV", p) and "WRNT" not in p \
            and "DEB" not in p:
        out["kind"].append("ratio_unparsed")        # factor taken from the observed gap later
    if "RIGHTS" in p:
        out["kind"].append("rights")
    if re.search(r"DEMERGER|SCHEME OF ARRANGEMENT|AMALGAMATION", p):
        out["kind"].append("scheme")
    out["kind"] = "+".join(dict.fromkeys(out["kind"]))
    return out


def load_records(raw: Path) -> pd.DataFrame:
    """All distinct corporate-action records in the downloaded PR archives."""
    parts = [read_bc(f) for f in sorted((Path(raw) / "pr").rglob("*.zip"))]
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame(columns=["symbol", "ex_date", "factor", "dividend", "kind", "purpose"])
    bc = pd.concat(parts, ignore_index=True)
    bc = bc[bc["series"].isin(EQUITY_SERIES)]
    # NSE switched from dd/mm/yyyy to ISO dates in its 2026 files
    ex = bc["ex_dt"].str.strip()
    bc["ex_date"] = pd.to_datetime(ex, format="%d/%m/%Y", errors="coerce").fillna(
        pd.to_datetime(ex, format="%Y-%m-%d", errors="coerce"))
    bc = bc.dropna(subset=["ex_date"])
    bc = bc.drop_duplicates(["symbol", "ex_date", "purpose"])
    parsed = pd.DataFrame([parse_purpose(p) for p in bc["purpose"]], index=bc.index)
    bc = pd.concat([bc[["symbol", "ex_date", "purpose"]], parsed], axis=1)
    bc = bc[(bc["factor"] != 1.0) | (bc["dividend"] > 0) | bc["kind"].str.contains("rights|scheme|ratio_unparsed")]
    # one row per symbol and ex-date (a bonus and a dividend can share an ex-date)
    agg = bc.groupby(["symbol", "ex_date"]).agg(factor=("factor", "prod"), dividend=("dividend", "sum"),
                                                kind=("kind", lambda s: "+".join(sorted({k for k in s if k}))),
                                                purpose=("purpose", " | ".join)).reset_index()
    return agg


def attach(daily: pd.DataFrame, records: pd.DataFrame, max_days: int = 5) -> pd.DataFrame:
    """Map each record to the security's first session on/after the ex-date (same symbol)."""
    if records is None or not len(records):
        return pd.DataFrame(columns=["idx", "factor", "dividend", "kind"])
    left = records.sort_values("ex_date").rename(columns={"ex_date": "date"})
    right = daily[["symbol", "date"]].reset_index().rename(columns={"index": "idx"})
    right["symbol"] = right["symbol"].astype(str)
    left["symbol"] = left["symbol"].astype(str)
    # nearest session: ex-dates fall on holidays (-> next session) and are occasionally a day late
    m = pd.merge_asof(left, right.sort_values("date"), on="date", by="symbol", direction="nearest",
                      tolerance=pd.Timedelta(days=max_days))
    m = m.dropna(subset=["idx"])
    m["idx"] = m["idx"].astype(int)
    return m.groupby("idx").agg(factor=("factor", "prod"), dividend=("dividend", "sum"),
                                kind=("kind", lambda k: "+".join(sorted(set(k))))).reset_index()


def detect(daily: pd.DataFrame, tol: float = 0.05, min_ratio: float = 1.3) -> pd.DataFrame:
    """Candidate split/bonus events from unadjusted overnight gaps.

    A gap prevclose/open within `tol` (log) of a standard ratio >= `min_ratio`, with the
    close on the same side (the new price level holds). Ratios below ~1.3 are excluded by
    default: a stock hitting the 20% lower circuit gaps by exactly 1/(1-0.2) = 1.25, which
    is indistinguishable from a 1:4 bonus.
    """
    ratios = STANDARD_RATIOS[STANDARD_RATIOS >= min_ratio]
    opn = daily["open"].where(daily["open"] > 0, daily["close"])
    g_open = np.log(daily["prevclose"] / opn).to_numpy()
    g_close = np.log(daily["prevclose"] / daily["close"]).to_numpy()
    pre = np.flatnonzero((g_open > np.log(min_ratio) - tol) & (g_close > np.log(min_ratio) - 2 * tol))
    lr = np.log(ratios)
    dist = np.abs(g_open[pre, None] - lr[None, :])
    best = dist.argmin(axis=1)
    ok = dist[np.arange(len(pre)), best] < tol
    return pd.DataFrame({"idx": daily.index[pre[ok]], "factor": ratios[best[ok]], "dividend": 0.0, "kind": "detected"})


def score_detector(daily: pd.DataFrame, record_idx: set, **params) -> dict:
    det = set(detect(daily, **params)["idx"])
    tp = len(det & record_idx)
    prec = tp / len(det) if det else 0.0
    rec = tp / len(record_idx) if record_idx else 0.0
    return {**params, "detected": len(det), "records": len(record_idx), "matched": tp, "precision": prec,
            "recall": rec, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0}


def tune_detector(daily: pd.DataFrame, record_idx: set, split_date: pd.Timestamp) -> dict:
    """Choose detector parameters on records before `split_date`, report accuracy after it.

    Scored on liquid rows only (>= Rs 1 crore traded that day), where the study operates.
    """
    liquid = daily["value"] >= 1e7
    tune = daily[liquid & (daily["date"] < split_date)]
    test = daily[liquid & (daily["date"] >= split_date)]
    grid = [score_detector(tune, record_idx & set(tune.index), tol=t, min_ratio=m)
            for t in (0.03, 0.05, 0.08, 0.12) for m in (1.18, 1.3, 1.45)]
    best = max(grid, key=lambda g: g["f1"])
    params = {"tol": best["tol"], "min_ratio": best["min_ratio"]}
    return {"params": params, "tuning": best, "validation": score_detector(test, record_idx & set(test.index), **params),
            "grid": grid, "split_date": str(split_date.date())}


def snap_ratio(gap_log: np.ndarray, tol: float = 0.08) -> np.ndarray:
    """Nearest standard ratio to an observed gap (NaN if none within tol). Used only when a
    record confirms that a bonus/split happened but does not state the ratio."""
    lr = np.log(STANDARD_RATIOS)
    d = np.abs(np.asarray(gap_log)[:, None] - lr[None, :])
    j = d.argmin(axis=1)
    return np.where(d[np.arange(len(j)), j] < tol, STANDARD_RATIOS[j], np.nan)
