"""Build the static project site (docs/site/) from results - GitHub Pages ready.

    python scripts/build_site.py [real|synthetic]
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"


def load(kind: str) -> dict:
    res = ROOT / "results" / kind
    data = {"kind": kind}
    cfg = json.loads((ROOT / "config" / "india_costs.json").read_text())["current_regime"]
    data["costs"] = {k: cfg[k] for k in ("stt_delivery_buy", "stt_delivery_sell", "stamp_duty_buy", "exchange_txn_nse",
                                         "sebi_turnover_fee", "gst_rate_on_fees", "dp_charge_per_scrip_sold_inr")}
    net = pd.read_csv(res / "e3_net_of_reality.csv")
    data["net"] = net[net.period == "full"][["portfolio", "gross_cagr", "net_cagr", "net_after_tax_cagr",
                                             "avg_monthly_turnover"]].to_dict("records")
    data["hyp"] = pd.read_csv(res / "e3_hypotheses.csv").to_dict("records")
    e0 = pd.read_csv(ROOT / "results" / "e0_bias_by_deletion_pp.csv", header=[0, 1], index_col=[0, 1])
    data["e0"] = {r: e0.xs(r, level=0)[("cagr_bias", "mean")].round(3).to_dict()
                  for r in ("equal_weight", "momentum", "low_vol")}
    b = res / "e4_bias_summary.csv"
    if b.exists():
        bs = pd.read_csv(b)
        v = bs[bs.view == "vendor"]
        data["e4"] = {r: dict(zip(g["fraction"].round(1), g["mean"].round(3))) for r, g in v.groupby("rule")}
        data["e4_views"] = bs[bs.view != "vendor"][["view", "rule", "mean"]].to_dict("records")
    s = res / "e1_summary.json"
    if s.exists():
        data["e1"] = json.loads(s.read_text())
    st = ROOT / "data" / "processed" / "build_stats.json"
    if kind == "real" and st.exists():
        data["build"] = {k: v for k, v in json.loads(st.read_text()).items() if k != "missing_session_days_repaired"}
    return data


def main(kind: str = "real") -> Path:
    SITE.mkdir(parents=True, exist_ok=True)
    img = SITE / "img"
    img.mkdir(exist_ok=True)
    figs = ["e2_cumulative_gross", "e2_decile_profiles", "e3_gross_net_tax", "e3_capacity", "e4_bias_vs_deletion"]
    if kind == "real":
        figs.insert(0, "e1_coverage")
    for f in figs:
        src = ROOT / "figures" / kind / f"{f}.png"
        if src.exists():
            shutil.copy(src, img / f"{f}.png")
    shutil.copy(ROOT / "figures" / "e0_bias_vs_missing_data.png", img / "e0_bias_vs_missing_data.png")
    html = TEMPLATE.replace("__DATA__", json.dumps(load(kind), default=str))
    path = SITE / "index.html"
    path.write_text(html)
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>India Anomaly Lab</title>
<meta name="description" content="Do momentum, low-volatility and reversal strategies in Indian equities survive point-in-time data, costs, taxes and multiple testing?">
<style>
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b6a64;--line:#e6e5e0;--card:#ffffff;--accent:#2a78d6;--warm:#eb6834;--good:#1baf7a;--bad:#c0392b}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#eceae4;--muted:#a3a19a;--line:#2e2d2a;--card:#1e1e1c;--accent:#6aa7ee;--warm:#f08a5d;--good:#3ccf97;--bad:#e8776b}}
:root[data-theme="dark"]{--bg:#161615;--fg:#eceae4;--muted:#a3a19a;--line:#2e2d2a;--card:#1e1e1c;--accent:#6aa7ee;--warm:#f08a5d;--good:#3ccf97;--bad:#e8776b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:40px 16px 80px}
h1{font-size:clamp(28px,5vw,42px);line-height:1.15;margin:0 0 12px;letter-spacing:-.02em}
h2{font-size:22px;margin:56px 0 12px;letter-spacing:-.01em}
p{margin:0 0 14px}.lede{font-size:18px;color:var(--muted)}
.tag{display:inline-block;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:14px}
.banner{border:1px solid var(--warm);border-radius:10px;padding:10px 14px;margin:16px 0;color:var(--warm)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.card b{display:block;font-size:26px;letter-spacing:-.02em}.card span{color:var(--muted);font-size:14px}
table{width:100%;border-collapse:collapse;font-size:14px;margin:12px 0}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}
.yes{color:var(--good);font-weight:600}.no{color:var(--bad);font-weight:600}
figure{margin:18px 0}figure img{width:100%;border:1px solid var(--line);border-radius:10px;background:#fff}
figcaption{font-size:13px;color:var(--muted);margin-top:6px}
.scroll{overflow-x:auto}
label{font-size:14px;color:var(--muted)}input[type=range]{width:100%}
input[type=number],select{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);width:100%}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.bar{height:10px;border-radius:5px;background:var(--accent)}
footer{margin-top:64px;color:var(--muted);font-size:13px;border-top:1px solid var(--line);padding-top:16px}
code{font-size:13px}
</style>
</head>
<body>
<main>
<span class="tag">Research project · Indian equities · 2006–2026</span>
<h1>Do equity anomalies survive reality in India?</h1>
<p class="lede">Momentum, low volatility and short-term reversal look good in most published backtests. This project re-tests them on every stock that traded on NSE, including the companies that later disappeared, with Indian trading costs and taxes, realistic liquidity and correction for the number of strategies tried. The rules were frozen in a pre-registration before any real result was computed.</p>
<div id="banner"></div>
<div class="grid" id="kpis"></div>

<h2>What survives</h2>
<p>Gross, after costs (₹10 lakh portfolio, dated statutory charges, spread and impact), and after Indian capital-gains tax, full period.</p>
<div class="scroll"><table id="net"></table></div>
<figure><img src="img/e3_gross_net_tax.png" alt="Gross vs net vs after-tax CAGR"><figcaption>Costs and taxes by strategy.</figcaption></figure>

<h2>Pre-registered hypotheses</h2>
<div class="scroll"><table id="hyp"></table></div>

<h2>How much do data shortcuts inflate a backtest?</h2>
<p>Free data sources silently drop companies that died. Drag the slider to delete a share of the distressed exits and see how much each strategy's backtest inflates. Solid: this dataset. Faint: the prediction from a synthetic market with known truth (E0).</p>
<label for="del">Share of dead companies missing: <b id="delv">60%</b></label>
<input id="del" type="range" min="0" max="100" step="20" value="60">
<div id="biasbars"></div>
<figure><img src="img/e4_bias_vs_deletion.png" alt="Bias vs deletion share"><figcaption>Bias in CAGR (percentage points a year) as dead companies are deleted.</figcaption></figure>

<h2>Cost of one trade in India today</h2>
<p>Delivery trade on NSE with a discount broker: STT, stamp duty, exchange and SEBI fees, GST and the depository charge. Spread and impact come on top.</p>
<div class="row">
<div><label for="amt">Trade value (₹)</label><input id="amt" type="number" value="100000" min="1000" step="1000"></div>
<div><label for="hs">Half-spread (bps)</label><input id="hs" type="number" value="10" min="0" step="1"></div>
</div>
<div class="scroll"><table id="cost"></table></div>

<h2>Growth, deciles and capacity</h2>
<figure><img src="img/e2_cumulative_gross.png" alt="Cumulative gross growth"><figcaption>Gross growth of ₹1, log scale. The shaded line marks the untouched test period.</figcaption></figure>
<figure><img src="img/e2_decile_profiles.png" alt="Decile profiles"><figcaption>Gross CAGR by decile for the three primary signals.</figcaption></figure>
<figure><img src="img/e3_capacity.png" alt="Capacity"><figcaption>Net CAGR as portfolio size grows.</figcaption></figure>

<h2>Method in one paragraph</h2>
<p>Prices come from NSE's daily bhavcopy files, which list every stock that traded each day. Securities are linked across symbol and ISIN changes. Splits and bonuses are adjusted from NSE's own corporate-action records (and, before 2010, a detector validated against those records). The universe is the 500 most liquid stocks at each month-end, known at the time. Portfolios are bought at the next day's open. Costs follow the rules in force on each trade date; taxes follow STCG/LTCG law by sale date, including the 2018 grandfathering and 2024 changes. Every variant tried is logged and corrected for with Hansen's SPA test and the deflated Sharpe ratio.</p>
<footer>Research code and data pipeline: <code>india-anomaly-lab</code>. Not investment advice; no real-money trading. AI assistance was used to write code; research decisions and write-up are the author's.</footer>
</main>
<script>
const D = __DATA__;
const pct = (x, d = 1) => (x == null || isNaN(x)) ? "–" : (x * 100).toFixed(d) + "%";
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
if (D.kind !== "real") document.getElementById("banner").innerHTML = '<div class="banner">Showing a dry run on a synthetic market. Real results appear after the pre-registered study is run.</div>';
const k = [];
if (D.build) k.push([D.build.securities.toLocaleString("en-IN"), "securities, dead ones included"], [D.build.files.toLocaleString("en-IN"), "NSE daily files"]);
const ew = D.net.find(r => r.portfolio.includes("EW"));
const mom = D.net.find(r => r.portfolio.startsWith("H1"));
if (mom && ew) k.push([pct(mom.gross_cagr), "momentum, gross"], [pct(mom.net_after_tax_cagr), "momentum after costs and tax"], [pct(ew.net_after_tax_cagr), "equal weight after costs and tax"]);
document.getElementById("kpis").innerHTML = k.map(([a, b]) => `<div class="card"><b>${a}</b><span>${b}</span></div>`).join("");
document.getElementById("net").innerHTML = "<tr><th>Portfolio</th><th class=num>Gross</th><th class=num>After costs</th><th class=num>After tax</th><th class=num>Monthly turnover</th></tr>" +
  D.net.map(r => `<tr><td>${esc(r.portfolio)}</td><td class=num>${pct(r.gross_cagr)}</td><td class=num>${pct(r.net_cagr)}</td><td class=num>${pct(r.net_after_tax_cagr)}</td><td class=num>${pct(r.avg_monthly_turnover, 0)}</td></tr>`).join("");
document.getElementById("hyp").innerHTML = "<tr><th>ID</th><th>Hypothesis</th><th>Evidence</th><th>Supported</th></tr>" +
  D.hyp.map(h => `<tr><td>${esc(h.id)}</td><td>${esc(h.claim)}</td><td>${esc(h.evidence)}</td><td class="${h.supported === true || h.supported === "True" ? "yes" : "no"}">${h.supported === true || h.supported === "True" ? "yes" : "no"}</td></tr>`).join("");
const rules = [["equal_weight", "Equal weight"], ["momentum", "Momentum"], ["low_vol", "Low volatility"]];
function bias() {
  const f = +document.getElementById("del").value / 100;
  document.getElementById("delv").textContent = Math.round(f * 100) + "%";
  const get = (o, r) => o && o[r] ? (o[r][f.toFixed(1)] ?? o[r][String(f)] ?? o[r][f]) : null;
  const all = rules.flatMap(([r]) => [get(D.e4, r), get(D.e0, r)]).filter(v => v != null);
  const mx = Math.max(1, ...all.map(Math.abs));
  document.getElementById("biasbars").innerHTML = rules.map(([r, n]) => {
    const a = get(D.e4, r), b = get(D.e0, r);
    return `<div style="margin:10px 0"><div style="display:flex;justify-content:space-between;font-size:14px"><span>${n}</span><span>${a == null ? "–" : "+" + a.toFixed(2) + " pp/yr"} <span style="color:var(--muted)">(synthetic ${b == null ? "–" : "+" + b.toFixed(2)})</span></span></div>
      <div class="bar" style="width:${Math.max(0, (a ?? 0) / mx * 100)}%"></div><div class="bar" style="opacity:.3;margin-top:3px;width:${Math.max(0, (b ?? 0) / mx * 100)}%"></div></div>`;
  }).join("");
}
document.getElementById("del").addEventListener("input", bias); bias();
function cost() {
  const v = Math.max(0, +document.getElementById("amt").value || 0), hs = (+document.getElementById("hs").value || 0) / 1e4, c = D.costs;
  const fee = v * (c.exchange_txn_nse + c.sebi_turnover_fee), gst = fee * c.gst_rate_on_fees;
  const buy = {STT: v * c.stt_delivery_buy, "Stamp duty": v * c.stamp_duty_buy, "Exchange + SEBI": fee, GST: gst, "DP charge": 0, "Half-spread": v * hs};
  const sell = {STT: v * c.stt_delivery_sell, "Stamp duty": 0, "Exchange + SEBI": fee, GST: gst, "DP charge": c.dp_charge_per_scrip_sold_inr, "Half-spread": v * hs};
  const f = x => "₹" + x.toLocaleString("en-IN", {maximumFractionDigits: 2});
  let rows = Object.keys(buy).map(n => `<tr><td>${n}</td><td class=num>${f(buy[n])}</td><td class=num>${f(sell[n])}</td></tr>`).join("");
  const tb = Object.values(buy).reduce((a, b) => a + b, 0), ts = Object.values(sell).reduce((a, b) => a + b, 0);
  rows += `<tr><th>Total</th><th class=num>${f(tb)}</th><th class=num>${f(ts)}</th></tr><tr><td colspan=3>Round trip: <b>${f(tb + ts)}</b> = ${v ? ((tb + ts) / v * 1e4).toFixed(1) : "–"} bps of the trade.</td></tr>`;
  document.getElementById("cost").innerHTML = "<tr><th></th><th class=num>Buy</th><th class=num>Sell</th></tr>" + rows;
}
["amt", "hs"].forEach(id => document.getElementById(id).addEventListener("input", cost)); cost();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print(main(sys.argv[1] if len(sys.argv) > 1 else "real"))
