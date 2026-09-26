"""Build a point-in-time security panel from parsed bhavcopies.

Steps
1. identity   link rows into securities that survive symbol changes, ISIN changes
              (face-value splits issue new ISINs) and exclude symbol reuse
2. daily      adjusted returns from NSE's own prevclose (split/bonus-safe), with a
              repair for sessions missing from our archive
3. monthly    signal inputs (close-to-close) and holding returns (next-open to
              next-open) per security-month, plus liquidity and spread estimates
4. exits      last trading date and a distress flag for every security that stopped trading

Nothing here decides which securities are "good"; that is the universe module's job.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .bhavcopy import is_fund

NEW_EPOCH_GAP = 250          # sessions without trading after which a symbol without a
                             # matching ISIN is treated as a different company (symbol reuse)
MISSING_SESSION_SHARE = 0.5  # if most securities' prevclose disagrees with our last close,
                             # the previous session is missing from the archive
CA_LOG_BAND = np.log(1.35)   # on such days, larger gaps are still treated as corporate actions


class _DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def assign_ids(df: pd.DataFrame, symbol_changes: pd.DataFrame | None = None) -> tuple[pd.Series, dict]:
    """Return a security id per row and linkage statistics.

    Nodes are (symbol, epoch). Nodes are merged when they share an ISIN or when NSE's
    symbol-change list says old -> new on a date where old stops and new starts.
    """
    sessions = pd.Index(np.sort(df["date"].unique()))
    sess = pd.Series(sessions.get_indexer(df["date"]), index=df.index)
    order = np.lexsort((sess.values, df["symbol"].astype(str).values))
    # Plain numpy strings/bools here: nullable Arrow booleans would propagate NA into epochs.
    sym = df["symbol"].astype(str).to_numpy(dtype=object)[order]
    isin = df["isin"].astype(object).where(df["isin"].notna(), None).to_numpy(dtype=object)[order]
    d = pd.DataFrame({"symbol": sym, "sess": sess.to_numpy()[order], "isin": isin}, index=df.index[order])
    same_sym = np.r_[False, sym[1:] == sym[:-1]]
    gap = np.r_[0, np.diff(d["sess"].to_numpy())]
    prev_isin = d.groupby("symbol", sort=False)["isin"].ffill().shift().to_numpy(dtype=object)
    cur_isin = d["isin"].to_numpy(dtype=object)
    unknown = pd.isna(cur_isin) | pd.isna(prev_isin)
    isin_changed_or_unknown = unknown | (cur_isin != prev_isin)
    new_epoch = ~same_sym | ((gap > NEW_EPOCH_GAP) & isin_changed_or_unknown & same_sym)
    epoch = pd.Series(new_epoch.astype(int), index=d.index).groupby(d["symbol"], sort=False).cumsum()
    d["node"] = d["symbol"] + "#" + epoch.astype(str)

    dsu = _DSU()
    for n in d["node"].unique():
        dsu.find(n)
    stats = {"nodes": int(d["node"].nunique()), "reused_symbols": int((epoch > 1).groupby(d["symbol"]).any().sum())}
    by_isin = d.dropna(subset=["isin"]).groupby("isin")["node"].unique()
    linked_isin = 0
    for nodes in by_isin:
        for n in nodes[1:]:
            dsu.union(nodes[0], n)
            linked_isin += 1
    stats["isin_links"] = linked_isin

    linked_sc = 0
    if symbol_changes is not None and len(symbol_changes):
        span = d.groupby("node")["sess"].agg(["min", "max"])
        span["symbol"] = span.index.str.rsplit("#", n=1).str[0]
        by_sym = {s: g for s, g in span.groupby("symbol")}
        for old, new, when in symbol_changes[["old", "new", "date"]].itertuples(index=False):
            if old not in by_sym or new not in by_sym:
                continue
            k = sessions.searchsorted(when)
            o = by_sym[old][(by_sym[old]["max"] >= k - 5) & (by_sym[old]["max"] <= k + 1)]
            n = by_sym[new][(by_sym[new]["min"] >= k - 1) & (by_sym[new]["min"] <= k + 5)]
            if len(o) == 1 and len(n) == 1:
                dsu.union(o.index[0], n.index[0])
                linked_sc += 1
    stats["symbol_change_links"] = linked_sc

    root = d["node"].map(dsu.find)
    comp = pd.DataFrame({"root": root, "sess": d["sess"], "symbol": d["symbol"]})
    first = comp.groupby("root")["sess"].transform("min")
    last_sym = comp.sort_values("sess").groupby("root")["symbol"].transform("last").reindex(comp.index)
    start = pd.Series(sessions[first.values].strftime("%Y%m%d"), index=comp.index)
    sec = last_sym.astype(str) + "|" + start
    stats["securities"] = int(sec.nunique())
    return sec.reindex(df.index), stats


def daily_panel(df: pd.DataFrame, symbol_changes: pd.DataFrame | None = None,
                records: pd.DataFrame | None = None, detect_before: pd.Timestamp | None = None
                ) -> tuple[pd.DataFrame, dict]:
    """Daily returns adjusted for splits/bonuses.

    records        NSE corporate-action records (corpactions.load_records)
    detect_before  use the gap detector for dates before this (no records exist then)
    """
    from . import corpactions as CA

    df = df.copy()
    df["sec"], stats = assign_ids(df, symbol_changes)
    # Two rows of one security on the same day (overlap after a link): keep the larger.
    dup = df.duplicated(["sec", "date"], keep=False)
    stats["overlap_rows_dropped"] = int(dup.sum() - df[dup].drop_duplicates(["sec", "date"]).shape[0])
    df = df.sort_values(["sec", "date", "value"]).drop_duplicates(["sec", "date"], keep="last")
    df = df.sort_values(["sec", "date"]).reset_index(drop=True)

    prev_close = df.groupby("sec")["close"].shift()
    gap = np.log(prev_close / df["prevclose"])              # 0 unless NSE's base differs from our last close
    mismatch = gap.abs() > 0.005
    share = mismatch.groupby(df["date"]).mean()
    bad_days = share[(share > MISSING_SESSION_SHARE) & (df.groupby("date").size() >= 5)].index
    stats["missing_session_days_repaired"] = [d.strftime("%Y-%m-%d") for d in bad_days]
    on_bad = df["date"].isin(bad_days) & prev_close.notna() & (gap.abs() < CA_LOG_BAND)
    base = df["prevclose"].where(~on_bad, prev_close)
    df["prevclose_gap"] = np.exp(gap).where(mismatch & ~on_bad)

    # ---- corporate actions: records where they exist, validated detector before ----------
    opn_all = df["open"].where(df["open"] > 0, df["close"])

    def usable(ev: pd.DataFrame) -> pd.Series:
        # Apply only if the price actually gapped by the recorded ratio (guards against
        # mis-mapped symbols and against days where NSE already adjusted the base).
        g_obs = np.log(base.loc[ev.index] / opn_all.loc[ev.index])
        g_rec = np.log(ev["factor"].astype(float))
        return (ev["factor"] != 1.0) & ((g_obs - g_rec).abs() < g_obs.abs())

    rec = CA.attach(df, records) if records is not None else pd.DataFrame(columns=["idx", "factor", "dividend", "kind"])
    rec = rec.set_index("idx")
    rec["source"] = "record"
    if len(rec):
        unparsed = rec["kind"].astype(str).str.contains("ratio_unparsed") & (rec["factor"] == 1.0)
        if unparsed.any():
            g = np.log(base.loc[rec.index[unparsed]] / opn_all.loc[rec.index[unparsed]]).to_numpy()
            snapped = CA.snap_ratio(g)
            rec.loc[unparsed, "factor"] = np.where(np.isfinite(snapped), snapped, 1.0)
            stats["ca_records_ratio_from_gap"] = int(np.isfinite(snapped).sum())
    rec["use"] = usable(rec) if len(rec) else pd.Series(dtype=bool)
    events = rec
    if detect_before is not None:
        params = {}
        post = df["date"] >= detect_before
        if len(rec) and post.any():
            rec_idx = set(rec.index[rec["use"]])
            split_date = detect_before + (df["date"].max() - detect_before) * 0.3
            tuned = CA.tune_detector(df.loc[post], rec_idx, split_date)
            params = tuned["params"]
            stats["detector"] = {k: v for k, v in tuned.items() if k != "grid"}
            stats["detector_grid"] = tuned["grid"]
        det = CA.detect(df.loc[~post], **params).set_index("idx")
        det["source"] = "detector"
        # NSE's weekly record files are incomplete in later years (e.g. no entry for some 2025
        # splits/bonuses), so large unrecorded gaps in the record era are filled by the detector too.
        fill = CA.detect(df.loc[post], **params).set_index("idx")
        fill = fill[~fill.index.isin(rec.index)]
        fill["source"] = "detector_gapfill"
        det = pd.concat([det, fill])
        det["use"] = usable(det) if len(det) else pd.Series(dtype=bool)
        events = pd.concat([rec, det[~det.index.isin(rec.index)]])
    factor = pd.Series(1.0, index=df.index)
    dividend = pd.Series(0.0, index=df.index)
    applied = 0
    if len(events):
        ev = events
        use = ev["use"].astype(bool)
        factor.loc[ev.index[use]] = ev.loc[use, "factor"].astype(float)
        dividend.loc[ev.index] = ev["dividend"].astype(float).fillna(0.0)
        applied = int(use.sum())
        stats["ca_events_records"] = int((ev["source"] == "record").sum())
        stats["ca_split_bonus_records"] = int(((ev["source"] == "record") & (ev["factor"] != 1.0)).sum())
        stats["ca_split_bonus_records_applied"] = int((use & (ev["source"] == "record")).sum())
        stats["ca_detector_events_applied"] = int((use & (ev["source"] == "detector")).sum())
        stats["ca_gapfill_events_applied"] = int((use & (ev["source"] == "detector_gapfill")).sum())
        df.loc[ev.index, "ca_source"] = ev["source"].values
    # Demergers: holders receive shares of the new company, so the ex-date price gap is not a
    # loss. For recorded schemes with an overnight gap > 10%, the overnight move is set to zero.
    if len(rec):
        sch = rec.index[rec["kind"].astype(str).str.contains("scheme") & (factor.loc[rec.index] == 1.0).values]
        g = np.log(base.loc[sch] / opn_all.loc[sch])
        neutral = sch[(g.abs() > np.log(1.1)).values]
        factor.loc[neutral] = (base.loc[neutral] / opn_all.loc[neutral]).values
        df.loc[neutral, "ca_source"] = "record_scheme_neutral"
        stats["ca_scheme_neutral_applied"] = int(len(neutral))
        applied += int(len(neutral))
    stats["ca_factors_applied"] = applied
    base = base / factor
    df["ca_factor"] = factor.where(factor != 1.0)
    r = df["close"] / base - 1
    r[prev_close.isna()] = np.nan                           # first session: listing day not investable
    df["ret"] = r
    df["div_yield"] = (dividend / base).where(r.notna(), 0.0).fillna(0.0)   # for total-return sensitivity

    opn = df["open"].where(df["open"] > 0)
    df["ret_overnight"] = (opn / base - 1).where(r.notna())
    df["ret_intraday"] = (df["close"] / opn - 1).where(r.notna())
    miss_open = opn.isna() & r.notna()
    df.loc[miss_open, "ret_overnight"] = 0.0
    df.loc[miss_open, "ret_intraday"] = r[miss_open]

    lr = np.log1p(df["ret"].fillna(0.0))
    df["px"] = np.exp(lr.groupby(df["sec"]).cumsum())      # adjusted price index
    scale = df["px"] / df["close"]
    df["high_adj"], df["low_adj"] = df["high"] * scale, df["low"] * scale
    eta = (np.log(df["high_adj"]) + np.log(df["low_adj"])) / 2
    c = np.log(df["px"])
    eta_next = eta.groupby(df["sec"]).shift(-1)
    # Two-day products are averaged over the month BEFORE clipping at zero (Abdi & Ranaldo's
    # monthly estimator). Clipping each day first biases spreads upward (amendment 1).
    df["ar_s2"] = 4 * (c - eta) * (c - eta_next)
    return df, stats


def monthly_panel(daily: pd.DataFrame) -> pd.DataFrame:
    """One row per security-month.

    ret_cc    close-to-close return over the month (signal input)
    ret_hold  return of a position bought at the first open of the month and sold at
              the first open of the next month (execution one session after the signal)
    """
    d = daily
    month = d["date"].dt.to_period("M")
    first_in_month = ~(month.eq(month.shift()) & d["sec"].eq(d["sec"].shift()))
    # Overnight gap of a month's first session belongs to the previous month's holding.
    hold_month = month.where(~first_in_month, month - 1)
    g_cc = pd.DataFrame({"sec": d["sec"], "m": month, "lr": np.log1p(d["ret"])})
    ret_cc = np.expm1(g_cc.groupby(["sec", "m"])["lr"].sum(min_count=1))
    parts = pd.concat([
        pd.DataFrame({"sec": d["sec"], "m": hold_month, "lr": np.log1p(d["ret_overnight"])}),
        pd.DataFrame({"sec": d["sec"], "m": month, "lr": np.log1p(d["ret_intraday"])}),
    ])
    ret_hold = np.expm1(parts.groupby(["sec", "m"])["lr"].sum(min_count=1))
    # Total-return variant: the ex-date's cash dividend is added to that day's return.
    dy = d["div_yield"].fillna(0.0) if "div_yield" in d else pd.Series(0.0, index=d.index)
    parts_tr = pd.concat([
        pd.DataFrame({"sec": d["sec"], "m": hold_month, "lr": np.log1p(d["ret_overnight"] + dy)}),
        pd.DataFrame({"sec": d["sec"], "m": month, "lr": np.log1p(d["ret_intraday"])}),
    ])
    ret_hold_tr = np.expm1(parts_tr.groupby(["sec", "m"])["lr"].sum(min_count=1))

    g = d.assign(m=month).groupby(["sec", "m"])
    out = pd.DataFrame({
        "ret_cc": ret_cc,
        "close": g["close"].last(),
        "px": g["px"].last(),
        "px_max": g["px"].max(),
        "last_date": g["date"].last(),
        "n_days": g["ret"].count(),
        "sum_r": g["ret"].sum(),
        "sum_r2": (d["ret"] ** 2).groupby([d["sec"], month]).sum(),
        "value_med": g["value"].median(),
        "ar_s2": g["ar_s2"].mean(),
        "series": g["series"].last(),
        "symbol": g["symbol"].last(),
        "isin": g["isin"].last(),
    })
    out["ret_hold"] = ret_hold.reindex(out.index)
    out["ret_hold_tr"] = ret_hold_tr.reindex(out.index)
    out.index = out.index.set_names(["sec", "month"])
    out = out.reset_index()
    # Known at the signal date: did the security still trade in the month's final week?
    mkt_last = d.groupby(month)["date"].max()
    out["active_end"] = out["last_date"] >= out["month"].map(mkt_last) - pd.Timedelta(days=7)
    return out


def security_table(daily: pd.DataFrame, monthly: pd.DataFrame, listed_now: set[str] | None = None) -> pd.DataFrame:
    """Per-security facts: life span, ISINs, fund flag, exit and a distress heuristic."""
    end = daily["date"].max()
    sessions = pd.Index(np.sort(daily["date"].unique()))
    g = daily.groupby("sec")
    t = pd.DataFrame({
        "first_date": g["date"].min(), "last_date": g["date"].max(), "sessions": g.size(),
        "last_symbol": g["symbol"].last(), "last_series": g["series"].last(), "last_close": g["close"].last(),
        "any_isin": g["isin"].agg(lambda s: s.dropna().iloc[-1] if s.notna().any() else None).astype(object),
    })
    t["is_fund"] = is_fund(t["last_symbol"].astype("string"), t["any_isin"].astype("string")).values
    gap = len(sessions) - 1 - sessions.get_indexer(t["last_date"])
    t["exited"] = gap > 20
    m = monthly.sort_values(["sec", "month"])
    px_12m_before = m.groupby("sec")["px"].apply(lambda s: s.iloc[-1] / s.iloc[max(0, len(s) - 13)] - 1)
    t["ret_last_12m"] = px_12m_before.reindex(t.index)
    t["distressed_exit"] = t["exited"] & ((t["last_series"].isin(["BE", "BZ"])) | (t["last_close"] < 10)
                                           | (t["ret_last_12m"] < -0.5))
    if listed_now is not None:
        t["still_listed"] = (t["any_isin"].astype(object).isin(listed_now)
                             | t["last_symbol"].astype(str).isin(listed_now))
        t["suspended_not_delisted"] = t["exited"] & t["still_listed"]
    t.attrs["data_end"] = str(end.date())
    return t
