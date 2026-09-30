"""Build docs/site/tool.html: the educational backtesting tool and the "every model tested" table.

    python experiments/e7_tool_data.py && python scripts/build_tool.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"


def models() -> list[dict]:
    res = ROOT / "results" / "real"
    g = pd.read_csv(res / "e2_gross_summary.csv")
    ew = g[(g.portfolio == "same-universe EW")].set_index("period")["cagr"]
    rows = []
    for (p, k), grp in g[g.kind != "benchmark"].groupby(["portfolio", "kind"], sort=False):
        x = grp.set_index("period")
        rows.append({"model": p, "type": k, "full": x.loc["full", "cagr"], "test": x.loc["test", "cagr"],
                     "t": x.loc["full", "nw_t"],
                     "beats_ew_test": bool(k != "long-only top" or x.loc["test", "cagr"] > ew["test"])
                     if k == "long-only top" else None})
    e6 = json.loads((res / "e6_value_short_sample.json").read_text())
    rows.append({"model": "value (earnings yield) top10, 2024-26 only", "type": "long-only top (exploratory)",
                 "full": e6["value_top_decile_gross_cagr"], "test": None, "t": None, "beats_ew_test": False})
    return rows


def main() -> Path:
    data = json.loads((SITE / "tool_data.json").read_text())
    data["models"] = models()
    data["ew_test"] = None
    html = TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":"), default=float))
    p = SITE / "tool.html"
    p.write_text(html)
    return p


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest Reality Check</title>
<meta name="description" content="An educational backtesting tool: see how data shortcuts, costs and taxes change the results of Indian stock strategies, 2011-2026.">
<style>
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b6a64;--line:#e6e5e0;--card:#fff;--accent:#2a78d6;--warm:#eb6834;--good:#1baf7a;--bad:#c0392b;--grid:#eeede8}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#eceae4;--muted:#a3a19a;--line:#2e2d2a;--card:#1e1e1c;--accent:#6aa7ee;--warm:#f08a5d;--good:#3ccf97;--bad:#e8776b;--grid:#262624}}
:root[data-theme="dark"]{--bg:#161615;--fg:#eceae4;--muted:#a3a19a;--line:#2e2d2a;--card:#1e1e1c;--accent:#6aa7ee;--warm:#f08a5d;--good:#3ccf97;--bad:#e8776b;--grid:#262624}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 80px}h1{font-size:clamp(26px,4.5vw,38px);margin:0 0 8px;letter-spacing:-.02em}
h2{font-size:21px;margin:48px 0 10px}.lede{color:var(--muted);font-size:17px}a{color:var(--accent)}
.controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:22px 0 8px}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:4px}
select{font:inherit;width:100%;padding:7px 8px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg)}
.note{font-size:14px;color:var(--muted);min-height:1.4em}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.card b{display:block;font-size:22px}.card span{font-size:13px;color:var(--muted)}
.chart{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px}svg{width:100%;height:auto;display:block}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;color:var(--muted);padding:6px 8px}.sw{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.lesson{border-left:3px solid var(--warm);padding:8px 14px;margin:16px 0;background:var(--card);border-radius:0 10px 10px 0}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left}th{color:var(--muted)}td.n{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}.yes{color:var(--good);font-weight:600}.no{color:var(--bad);font-weight:600}
footer{margin-top:56px;font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px}
</style>
</head>
<body><main>
<a href="index.html">&larr; Project overview</a>
<h1>Backtest Reality Check</h1>
<p class="lede">Pick a strategy, then switch on the shortcuts most backtests take, or the costs and taxes they leave out. Every number here comes from real NSE data, 2011-2026, including companies that later disappeared.</p>

<div class="controls">
<div><label for="rule">Strategy</label><select id="rule">
<option value="momentum">Momentum (last year's winners)</option><option value="low_vol">Low volatility (calmest stocks)</option>
<option value="team_s5">Team rule S5 (trend + setup, 30 stocks)</option><option value="team_s7">Team rule S7 (S5, risk-adjusted ranking)</option><option value="team_s4">Team rule S4 (momentum + low vol + trend)</option><option value="team_s2">Momentum top 30 stocks</option>
<option value="reversal">Reversal (last month's losers)</option><option value="high_52w">Near 52-week high</option><option value="ew">Equal weight (all 500 stocks)</option></select></div>
<div><label for="view">Data used</label><select id="view">
<option value="honest">Honest: stocks as known at the time</option><option value="survivors">Shortcut: only companies that survived</option>
<option value="today">Shortcut: today's top 500 applied to the past</option></select></div>
<div><label for="fric">Frictions</label><select id="fric"><option value="gross">None (paper returns)</option><option value="costs">Trading costs</option><option value="tax">Costs + capital-gains tax</option></select></div>
<div><label for="cap">Portfolio size</label><select id="cap"><option value="10L">&#8377;10 lakh</option><option value="1Cr">&#8377;1 crore</option><option value="10Cr">&#8377;10 crore</option><option value="100Cr">&#8377;100 crore</option></select></div>
<div><label for="y0">From</label><select id="y0"></select></div>
<div><label for="y1">To</label><select id="y1"></select></div>
</div>
<div class="note" id="note"></div>
<div class="kpis" id="kpis"></div>
<div class="chart"><svg id="svg" viewBox="0 0 900 380" role="img" aria-label="Growth of 1 lakh rupees"></svg>
<div class="legend" id="legend"></div></div>
<div class="lesson" id="lesson"></div>

<h2>Every model we tested, including the ones that failed</h2>
<p class="note">Gross compound annual growth, primary sample (Aug 2011 - Sep 2026) and untouched test period (2018 onward). All 12 pre-registered variants are listed; none were dropped.</p>
<div class="scroll"><table id="models"></table></div>

<footer>Educational use only. Not investment advice; past returns do not predict future returns. Methods and code: see the project overview.</footer>
</main>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id);
const years = [...new Set(D.months.map(m => +m.slice(0, 4)))];
years.forEach(y => { $("y0").add(new Option(y, y)); $("y1").add(new Option(y, y)); });
$("y1").value = years[years.length - 1];
const pct = (x, d = 1) => x == null || !isFinite(x) ? "–" : (x * 100).toFixed(d) + "%";
const LESSON = {
  honest: "Honest data: the universe at each month is only what an investor could have known then, and companies that later collapsed stay in.",
  survivors: "Only survivors: companies that later died are removed. Their losses vanish, so every strategy looks better than it was.",
  today: "Today's list backwards: using the 500 biggest companies of 2026 to pick stocks in 2012 quietly selects the future winners. This is the most common mistake in amateur backtests, and the largest one measured here.",
  gross: "No frictions: returns as if trading were free and untaxed.",
  costs: "Trading costs: STT, stamp duty, exchange and SEBI fees, GST, depository charges, bid-ask spread and market impact, using the rules in force on each trade date. Strategies that trade a lot lose most.",
  tax: "Costs + tax: short- and long-term capital-gains tax as the law stood on each sale date (including the 2018 and 2024 changes), paid every April."
};
function series(rule, view, fric, cap) {
  if (view !== "honest") return D.series[`${rule}|${view}|gross`];
  if (fric === "gross") return D.series[`${rule}|honest|gross`];
  if (fric === "costs") return D.series[`${rule}|honest|costs|${cap}`];
  return D.series[`${rule}|honest|tax|10L`];
}
function stats(r) {
  const x = r.filter(v => v != null); if (x.length < 2) return {};
  let w = 1, peak = 1, dd = 0; for (const v of x) { w *= 1 + v; peak = Math.max(peak, w); dd = Math.min(dd, w / peak - 1); }
  const mu = x.reduce((a, b) => a + b, 0) / x.length, sd = Math.sqrt(x.reduce((a, b) => a + (b - mu) ** 2, 0) / (x.length - 1));
  return {cagr: Math.pow(w, 12 / x.length) - 1, vol: sd * Math.sqrt(12), sharpe: mu / sd * Math.sqrt(12), dd, final: w};
}
function draw(lines) {
  const W = 900, H = 380, L = 64, R = 16, T = 14, B = 34, svg = $("svg");
  const all = lines.flatMap(l => l.path).filter(v => v > 0);
  const lo = Math.log10(Math.min(...all)), hi = Math.log10(Math.max(...all));
  const n = lines[0].path.length, X = i => L + (W - L - R) * i / Math.max(1, n - 1), Y = v => T + (H - T - B) * (1 - (Math.log10(v) - lo) / (hi - lo || 1));
  let s = "";
  const css = getComputedStyle(document.documentElement);
  for (let e = Math.floor(lo * 2) / 2; e <= hi + 0.01; e += 0.5) { const v = 10 ** e; if (Math.log10(v) < lo - 0.01) continue;
    s += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${css.getPropertyValue('--grid')}"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end" font-size="11" fill="${css.getPropertyValue('--muted')}">₹${(v * 1).toLocaleString('en-IN', {maximumFractionDigits: 0})}L</text>`; }
  const ms = lines[0].months; let last = "";
  ms.forEach((m, i) => { const y = m.slice(0, 4); if (y !== last && m.endsWith("-01") || i === 0) { last = y; s += `<text x="${X(i)}" y="${H - 12}" font-size="11" text-anchor="middle" fill="${css.getPropertyValue('--muted')}">${y}</text>`; } });
  for (const l of lines) s += `<polyline fill="none" style="stroke:${l.color}" stroke-width="${l.w}" points="${l.path.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ")}"/>`;
  svg.innerHTML = s;
  $("legend").innerHTML = lines.map(l => `<span><i class="sw" style="background:${l.color}"></i>${l.label}</span>`).join("");
}
function path(r) { let w = 1; return r.map(v => (w *= 1 + (v ?? 0))); }
function update() {
  const rule = $("rule").value, view = $("view").value; let fric = $("fric").value, cap = $("cap").value;
  const y0 = +$("y0").value, y1 = Math.max(+$("y1").value, y0);
  const note = [];
  if (view !== "honest" && fric !== "gross") { note.push("Costs and taxes are modelled for honest data only; showing paper returns for this shortcut."); fric = "gross"; }
  if (fric === "tax" && cap !== "10L") { note.push("Tax is modelled for a ₹10 lakh investor."); cap = "10L"; }
  $("note").textContent = note.join(" ");
  const keep = D.months.map(m => +m.slice(0, 4) >= y0 && +m.slice(0, 4) <= y1);
  const cut = r => r.filter((_, i) => keep[i]);
  const months = cut(D.months), mine = cut(series(rule, view, fric, cap)), honest = cut(D.series[`${rule}|honest|gross`]);
  const ew = cut(D.series["ew|honest|gross"]), bench = cut(D.series["bench|gross"]);
  const s = stats(mine), h = stats(honest), e = stats(ew);
  const diff = s.cagr - h.cagr;
  $("kpis").innerHTML = [[pct(s.cagr), "a year (CAGR)"], [pct(s.dd, 0), "worst fall"], [s.sharpe ? s.sharpe.toFixed(2) : "–", "Sharpe ratio"],
    ["₹" + (s.final ? (s.final * 1e5).toLocaleString("en-IN", {maximumFractionDigits: 0}) : "–"), "₹1 lakh became"],
    [(diff >= 0 ? "+" : "") + (diff * 100).toFixed(1) + " pp", "vs honest paper result"], [pct(e.cagr), "equal weight, honest"]]
    .map(([a, b]) => `<div class="card"><b>${a}</b><span>${b}</span></div>`).join("");
  const names = {team_s5: "Team rule S5", team_s7: "Team rule S7", team_s4: "Team rule S4", team_s2: "Momentum top 30", momentum: "Momentum", low_vol: "Low volatility", reversal: "Reversal", high_52w: "52-week high", ew: "Equal weight"};
  const lines = [{path: path(mine), color: "var(--warm)", w: 2.4, label: `${names[rule]}: your settings`, months},
    {path: path(honest), color: "var(--accent)", w: 1.4, label: `${names[rule]}: honest, no frictions`, months},
    {path: path(ew), color: "var(--muted)", w: 1.2, label: "Equal weight of all 500 (honest)", months},
    {path: path(bench.map(v => v ?? 0)), color: "#9b9a94", w: 1, label: "Nifty 50 ETF", months}];
  draw(lines);
  $("lesson").textContent = LESSON[view] + " " + LESSON[fric];
}
["rule", "view", "fric", "cap", "y0", "y1"].forEach(id => $(id).addEventListener("change", update));
update();
$("models").innerHTML = "<tr><th>Model</th><th>Type</th><th class=n>Full period</th><th class=n>Test (2018+)</th><th class=n>t-stat</th><th>Beat equal weight in test?</th></tr>" +
  D.models.map(m => `<tr><td>${m.model}</td><td>${m.type}</td><td class=n>${pct(m.full)}</td><td class=n>${pct(m.test)}</td><td class=n>${m.t == null ? "–" : m.t.toFixed(2)}</td><td class="${m.beats_ew_test === null ? "" : m.beats_ew_test ? "yes" : "no"}">${m.beats_ew_test === null ? "–" : m.beats_ew_test ? "yes" : "no"}</td></tr>`).join("");
</script>
</body></html>
"""

if __name__ == "__main__":
    print(main())
