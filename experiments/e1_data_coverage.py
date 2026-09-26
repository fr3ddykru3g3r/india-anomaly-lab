"""E1 - Audit of the point-in-time NSE dataset (no anomaly returns are computed here).

Run after scripts/build_dataset.py. Writes results/real/e1_*.csv and a list of the largest
corporate-action adjustments for manual verification against NSE announcements.

    python experiments/e1_data_coverage.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import _common as C
from indialab import research as rs

def main():
    proc = C.ROOT / "data" / "processed"
    out = C.ROOT / "results" / "real"
    out.mkdir(parents=True, exist_ok=True)
    daily = pd.read_parquet(proc / "daily.parquet",
                            columns=["date", "sec", "symbol", "series", "isin", "open", "close", "prevclose", "ret",
                                     "ca_factor", "ca_source", "div_yield", "value"])
    monthly, secs = C.build.load(proc)
    stats = json.loads((proc / "build_stats.json").read_text())
    eq = secs[~secs["is_fund"]]

    year = daily["date"].dt.year
    firsts = eq["first_date"].dt.year.value_counts()
    exits = eq.loc[eq["exited"], "last_date"].dt.year.value_counts()
    dexits = eq.loc[eq["exited"] & eq["distressed_exit"], "last_date"].dt.year.value_counts()
    cov = pd.DataFrame({
        "sessions": daily.groupby(year)["date"].nunique(),
        "securities_traded": daily[daily["sec"].isin(eq.index)].groupby(year)["sec"].nunique(),
        "share_rows_with_isin": daily.groupby(year)["isin"].apply(lambda s: s.notna().mean()),
        "first_seen": firsts, "exits": exits, "distressed_exits": dexits,
        "median_daily_value_cr": daily.groupby(year)["value"].median() / 1e7,
    }).fillna(0)
    p = rs.Panel.from_monthly(monthly, secs)
    elig = rs.universe(p)
    cov["universe_size_dec"] = elig.groupby(elig.index.year).apply(lambda e: int(e.iloc[-1].sum()))
    cov["universe_500th_liquidity_cr"] = [
        float(p.liquidity(6).where(elig).loc[elig.index.year == y].iloc[-1].min() / 1e7) if (elig.index.year == y).any() else np.nan
        for y in cov.index]
    cov.to_csv(out / "e1_coverage_by_year.csv")

    # ---- corporate actions: records vs detector ----------------------------------------
    applied = daily.dropna(subset=["ca_factor"])
    ca_summary = {
        "detector_used_before": stats["detector_used_before"],
        "factors_applied_by_source": applied["ca_source"].value_counts().to_dict(),
        "detector": stats.get("detector"),
        "dividend_events_with_amount": int((daily["div_yield"] > 0).sum()),
        "missing_session_days_repaired": stats.get("missing_session_days_repaired", []),
        "linking": {k: stats[k] for k in ("nodes", "securities", "isin_links", "symbol_change_links", "reused_symbols")},
        "record_stats": {k: stats.get(k) for k in ("ca_events_records", "ca_split_bonus_records",
                                                   "ca_split_bonus_records_applied", "ca_detector_events_applied")},
    }
    grid = stats.get("detector_grid")
    if grid:
        pd.DataFrame(grid).to_csv(out / "e1_detector_grid.csv", index=False)
    opn = daily["open"].where(daily["open"] > 0, daily["close"])
    gap = np.log(daily["prevclose"] / opn).abs()
    unexplained = daily[(gap > np.log(1.4)) & daily["ca_factor"].isna() & daily["ret"].notna()]
    unexplained[["date", "symbol", "sec", "open", "prevclose", "close", "ret", "value"]].sort_values(
        "value", ascending=False).to_csv(out / "e1_unexplained_gaps.csv", index=False)
    ca_summary["unexplained_overnight_gaps_gt_40pct"] = int(len(unexplained))
    verify = applied.assign(abs_log=np.log(applied["ca_factor"]).abs()).sort_values("value", ascending=False)
    verify[["date", "symbol", "sec", "isin", "ca_factor", "ca_source"]].head(40).to_csv(
        out / "e1_corporate_actions_to_verify.csv", index=False)

    r = daily["ret"]
    extreme = daily[(r.abs() > 0.5)][["date", "symbol", "sec", "series", "close", "prevclose", "ret"]]
    extreme.to_csv(out / "e1_extreme_daily_returns.csv", index=False)
    ca_summary["daily_returns_abs_gt_50pct"] = int(len(extreme))
    ca_summary["daily_returns_total"] = int(r.notna().sum())

    nb = secs.index[secs["last_symbol"].astype(str) == "NIFTYBEES"]
    if len(nb):
        px = daily.loc[daily["sec"] == nb[0]].set_index("date")["ret"].fillna(0)
        yrs = (px.index[-1] - px.index[0]).days / 365.25
        ca_summary["niftybees_cagr"] = float((1 + px).prod() ** (1 / yrs) - 1)
        ca_summary["niftybees_span"] = [str(px.index[0].date()), str(px.index[-1].date())]
    (out / "e1_summary.json").write_text(json.dumps(ca_summary, indent=2, default=str))

    fig, ax = C.plt.subplots(1, 2, figsize=(11, 3.6))
    ax[0].bar(cov.index, cov["securities_traded"], color="#b8b7b0", label="equities traded")
    ax[0].bar(cov.index, cov["universe_size_dec"], color="#2a78d6", label="study universe (Dec)")
    ax[0].set_title("Coverage: every NSE equity that traded, by year", loc="left")
    ax[0].legend(frameon=False)
    ax[1].bar(cov.index, cov["exits"], color="#b8b7b0", label="stopped trading")
    ax[1].bar(cov.index, cov["distressed_exits"], color="#eb6834", label="distressed exits")
    ax[1].set_title("Dead companies kept in the data", loc="left")
    ax[1].legend(frameon=False)
    fig.tight_layout()
    fig.savefig(C.fig_path("real", "e1_coverage"), dpi=160)
    pd.set_option("display.width", 200)
    print(cov.round(3).to_string())
    print(json.dumps(ca_summary, indent=1, default=str))


if __name__ == "__main__":
    main()
