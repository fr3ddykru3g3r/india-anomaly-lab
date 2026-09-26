"""One command from data/raw to data/processed: `python scripts/build_dataset.py`."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import bhavcopy, corpactions, panel


def load_symbol_changes(path: Path) -> pd.DataFrame:
    rows = []
    if not Path(path).exists():
        return pd.DataFrame(columns=["old", "new", "date"])
    for line in Path(path).read_text(errors="replace").splitlines():
        parts = line.rsplit(",", 3)
        if len(parts) != 4:
            continue
        _, old, new, when = (p.strip() for p in parts)
        d = pd.to_datetime(when.title(), format="%d-%b-%Y", errors="coerce")
        if pd.notna(d) and old and new:
            rows.append({"old": old, "new": new, "date": d})
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def load_listed_now(path: Path) -> set[str]:
    if not Path(path).exists():
        return set()
    df = pd.read_csv(path, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return set(df["SYMBOL"].str.strip()) | set(df["ISIN NUMBER"].str.strip())


def build(raw: Path, out: Path, log=print) -> dict:
    raw, out = Path(raw), Path(out)
    files = sorted((raw / "bhavcopy").rglob("*.zip"))
    log(f"{len(files)} bhavcopy files")
    df = bhavcopy.read_all(files, log=log)
    log(f"{len(df):,} security-days parsed")
    sc = load_symbol_changes(raw / "reference" / "symbolchange.csv")
    records = corpactions.load_records(raw)
    log(f"{len(records):,} corporate-action records")
    # Records exist from the first PR archive onward; before that, the detector fills in.
    detect_before = (records["ex_date"].min() + pd.Timedelta(days=14)) if len(records) else df["date"].max() + pd.Timedelta(days=1)
    daily, stats = panel.daily_panel(df, sc, records, detect_before)
    stats["detector_used_before"] = str(pd.Timestamp(detect_before).date())
    monthly = panel.monthly_panel(daily)
    listed = load_listed_now(raw / "reference" / "EQUITY_L.csv")
    secs = panel.security_table(daily, monthly, listed or None)
    out.mkdir(parents=True, exist_ok=True)
    records.to_parquet(out / "corporate_actions.parquet", index=False)
    daily.to_parquet(out / "daily.parquet", index=False)
    monthly["month"] = monthly["month"].astype(str)
    monthly.to_parquet(out / "monthly.parquet", index=False)
    secs.to_parquet(out / "securities.parquet")
    stats.update({"files": len(files), "rows": int(len(df)), "first_date": str(df["date"].min().date()),
                  "last_date": str(df["date"].max().date())})
    (out / "build_stats.json").write_text(json.dumps(stats, indent=2))
    return stats


def load(processed: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    monthly = pd.read_parquet(Path(processed) / "monthly.parquet")
    monthly["month"] = pd.PeriodIndex(monthly["month"], freq="M")
    secs = pd.read_parquet(Path(processed) / "securities.parquet")
    return monthly, secs
