"""Polite, resumable downloader for NSE's daily cash-market bhavcopy archives.

Each bhavcopy lists every security that traded on that day, including companies
that were later delisted, so a history built from them is point-in-time by
construction. Two formats exist:

    legacy  content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip  (to 5 Jul 2024)
    UDiFF   content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip     (from 8 Jul 2024)

Raw files are stored unmodified; every request (including 404 = no session) is
appended to data/raw/manifest.csv with its SHA-256 so the dataset is auditable
and the download can resume.
"""
from __future__ import annotations

import csv
import hashlib
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
MANIFEST = RAW / "manifest.csv"
BASE = "https://nsearchives.nseindia.com/"
USER_AGENT = "india-anomaly-lab/0.1 (student research; contact via repository)"
UDIFF_FROM = date(2024, 7, 8)
# Weekend sessions (budget days, disaster-recovery drills). Missing one would make the
# next day's PREVCLOSE disagree with our last close and look like a corporate action.
EXTRA_SESSIONS = [date(2015, 2, 28), date(2020, 2, 1), date(2024, 1, 20), date(2024, 3, 2),
                  date(2024, 5, 18), date(2025, 2, 1)]
REFERENCE_FILES = {
    "symbolchange.csv": "content/equities/symbolchange.csv",
    "EQUITY_L.csv": "content/equities/EQUITY_L.csv",
}


def bhavcopy_url(d: date) -> str:
    if d >= UDIFF_FROM:
        return BASE + f"content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    mon = d.strftime("%b").upper()
    return BASE + f"content/historical/EQUITIES/{d.year}/{mon}/cm{d:%d}{mon}{d.year}bhav.csv.zip"


def local_path(d: date) -> Path:
    return RAW / "bhavcopy" / str(d.year) / Path(bhavcopy_url(d)).name


def candidate_dates(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 or d in EXTRA_SESSIONS:
            out.append(d)
        d += timedelta(days=1)
    return out


def read_manifest(path: Path = MANIFEST) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open() as f:
        return {r["key"]: r for r in csv.DictReader(f)}


def _append(row: dict, path: Path = MANIFEST) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "url", "status", "bytes", "sha256", "fetched_at"])
        if new:
            w.writeheader()
        w.writerow(row)


def fetch(url: str, dest: Path, timeout: float = 30) -> tuple[int, int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        return e.code, 0, ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(dest)
    return 200, len(body), hashlib.sha256(body).hexdigest()


def download(start: date, end: date, pause: float = 1.0, max_errors: int = 5, log=print) -> dict:
    """Download every candidate date not already in the manifest. Stops on repeated blocks."""
    done = read_manifest()
    todo = [d for d in candidate_dates(start, end) if d.isoformat() not in done]
    counts = {"ok": 0, "no_session": 0, "error": 0}
    consecutive = 0
    for name, rel in REFERENCE_FILES.items():
        status, n, sha = fetch(BASE + rel, RAW / "reference" / name)
        log(f"reference {name}: HTTP {status}, {n} bytes")
        time.sleep(pause)
    for i, d in enumerate(todo):
        url = bhavcopy_url(d)
        for attempt in range(3):
            try:
                status, n, sha = fetch(url, local_path(d))
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                status, n, sha = -1, 0, ""
                time.sleep(pause * 5 * (attempt + 1))
        if status in (200, 404):
            _append({"key": d.isoformat(), "url": url, "status": status, "bytes": n, "sha256": sha,
                     "fetched_at": datetime.now().isoformat(timespec="seconds")})
            counts["ok" if status == 200 else "no_session"] += 1
            consecutive = 0
        else:
            counts["error"] += 1
            consecutive += 1
            log(f"{d} HTTP {status}")
            if consecutive >= max_errors:
                log("Stopping: repeated errors (possible block). Resume later; nothing is lost.")
                break
            time.sleep(pause * 30)
        if i % 100 == 0:
            log(f"{d}  {i + 1}/{len(todo)}  {counts}")
        time.sleep(pause)
    log(f"finished: {counts}")
    return counts


# ---- corporate actions: NSE daily "PR" archives (from July 2010) --------------------------
# Each PR zip contains Bc<ddmmyy>.csv: bonus / split / dividend / rights / demerger
# announcements with ex-dates about two weeks either side of the file date, so one file
# per week covers every event.
PR_MANIFEST = RAW / "manifest_pr.csv"
PR_FROM = date(2010, 7, 1)


def pr_url(d: date) -> str:
    return BASE + f"archives/equities/bhavcopy/pr/PR{d:%d%m%y}.zip"


def download_pr(start: date, end: date, pause: float = 1.0, log=print) -> dict:
    done = read_manifest(PR_MANIFEST)
    counts = {"ok": 0, "no_file": 0, "error": 0}
    wk = start - timedelta(days=start.weekday()) + timedelta(days=2)      # Wednesdays
    while wk <= end:
        key = f"week-{wk.isoformat()}"
        if key not in done:
            got = False
            for d in (wk, wk + timedelta(days=1), wk - timedelta(days=1), wk + timedelta(days=2)):
                dest = RAW / "pr" / str(d.year) / f"PR{d:%d%m%y}.zip"
                try:
                    status, n, sha = fetch(pr_url(d), dest)
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    status, n, sha = -1, 0, ""
                time.sleep(pause)
                if status == 200:
                    _append({"key": key, "url": pr_url(d), "status": 200, "bytes": n, "sha256": sha,
                             "fetched_at": datetime.now().isoformat(timespec="seconds")}, PR_MANIFEST)
                    counts["ok"] += 1
                    got = True
                    break
                if status not in (404,):
                    counts["error"] += 1
            if not got:
                counts["no_file"] += 1
                log(f"{key}: no PR file found")
            if (counts["ok"] + counts["no_file"]) % 50 == 0:
                log(f"{key} {counts}")
        wk += timedelta(days=7)
    log(f"finished PR: {counts}")
    return counts
