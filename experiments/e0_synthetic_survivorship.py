"""E0 - How much does missing dead-firm data inflate common backtests? (known ground truth)

For each synthetic market (seed): compute each rule's TRUE CAGR / max drawdown, then
recompute after deleting a fraction of bankrupt firms' histories (as free data
sources do) and on a 'today's survivors' universe. Report bias = view - truth.

    python experiments/e0_synthetic_survivorship.py [n_seeds]
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from indialab import synthetic as S  # noqa: E402

RULES = ["equal_weight", "momentum", "low_vol", "trend"]
DELETE = np.round(np.arange(0, 1.01, 0.1), 1)


def run(n_seeds: int = 12) -> pd.DataFrame:
    rows = []
    for seed in range(n_seeds):
        R, info = S.simulate(seed=seed)
        truth = {r: S.strategy_returns(S.view(R, info, "truth"), r) for r in RULES}
        base = {r: (S.cagr(x), S.max_dd(x)) for r, x in truth.items()}
        surv = S.view(R, info, "survivors")
        for r in RULES:
            x = S.strategy_returns(surv, r)
            rows.append({"seed": seed, "view": "survivors", "delete_bankrupt": np.nan, "rule": r,
                         "cagr_bias": S.cagr(x) - base[r][0], "maxdd_understatement": S.max_dd(x) - base[r][1],
                         "true_cagr": base[r][0]})
        for d in DELETE:
            v = S.view(R, info, "vendor", delete_bankrupt=d, seed=seed)
            for r in RULES:
                x = S.strategy_returns(v, r)
                rows.append({"seed": seed, "view": "vendor", "delete_bankrupt": d, "rule": r,
                             "cagr_bias": S.cagr(x) - base[r][0], "maxdd_understatement": S.max_dd(x) - base[r][1],
                             "true_cagr": base[r][0]})
        n_b = int((info["exit"] == "bankrupt").sum()); n_t = int((info["exit"] == "takeover").sum())
        print(f"seed {seed}: firms {len(info)}, bankrupt {n_b}, takeovers {n_t}", flush=True)
    return pd.DataFrame(rows)


def main(n_seeds: int = 12):
    df = run(n_seeds)
    (ROOT / "results").mkdir(exist_ok=True)
    df.to_csv(ROOT / "results" / "e0_synthetic_survivorship_raw.csv", index=False)
    vend = df[df.view == "vendor"].groupby(["rule", "delete_bankrupt"])[["cagr_bias", "maxdd_understatement"]]
    summ = vend.agg(["mean", "std"]) * 100
    summ.to_csv(ROOT / "results" / "e0_bias_by_deletion_pp.csv")
    surv = df[df.view == "survivors"].groupby("rule")[["cagr_bias", "maxdd_understatement", "true_cagr"]].mean() * 100
    surv.to_csv(ROOT / "results" / "e0_survivors_view_pp.csv")

    colors = {"equal_weight": "#2a78d6", "momentum": "#eb6834", "low_vol": "#1baf7a", "trend": "#eda100"}
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    for r in RULES:
        g = df[(df.view == "vendor") & (df.rule == r)].groupby("delete_bankrupt")["cagr_bias"]
        mu, sd = g.mean() * 100, g.std() * 100 / np.sqrt(n_seeds)
        ax.plot(mu.index * 100, mu.values, marker="o", ms=4, color=colors[r], label=r.replace("_", " "))
        ax.fill_between(mu.index * 100, mu - 2 * sd, mu + 2 * sd, color=colors[r], alpha=0.15, lw=0)
    ax.axhline(0, color="#52514e", lw=0.8)
    ax.set_xlabel("Share of bankrupt firms' histories missing from the data (%)")
    ax.set_ylabel("Backtest CAGR minus true CAGR (pp/yr)")
    ax.set_title("Missing dead-firm data inflates backtests - by how much depends on the rule", loc="left")
    ax.legend(frameon=False, fontsize=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(color="#e6e5e0")
    fig.text(0.01, 0.01, f"{n_seeds} synthetic markets x 800 firms x 20 years; band = +/-2 standard errors.",
             fontsize=7.5, color="#8a8983")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    (ROOT / "figures").mkdir(exist_ok=True)
    fig.savefig(ROOT / "figures" / "e0_bias_vs_missing_data.png", dpi=170)
    return summ, surv


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    summ, surv = main(n)
    pd.set_option("display.width", 200)
    print(summ.round(2).to_string())
    print(surv.round(2).to_string())
