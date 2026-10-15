# Final report data snapshot

This small derivative snapshot supports the figures and tables in the final
report. It was extracted from the frozen independent NSE price-return audit;
no strategy was rerun or tuned for the report.

| File | Contents |
| --- | --- |
| `summary.csv` | All-history-view summaries, including shorter histories, cost/tax scenarios and period slices. |
| `monthly_paths.csv` | Ten selected strategies, each with 116 saved monthly observations in the assumed-zero-recovery annual-tax scenario. |
| `bootstrap_intervals.csv` | Individual-rule intervals across four block lengths, in the headline annual-tax scenario. |
| `family_tests.json` | Original 12-rule and broader 29-rule tests, with their different dates and assumptions. |
| `provenance.json` | Source hashes, source-run date, scope, selected scenario, software checks and execution counts. |

Returns use missing-price assumptions that include future-availability checks.
They do not establish causal or executable trading performance. Read the report's
limitations alongside the tables. Sharpe ratios use a zero risk-free rate.

From the repository root, after installing `requirements-research.lock.txt`:

```bash
python scripts/build_final_project_report.py
node scripts/render_final_report.cjs
```

The PDF renderer additionally needs Node, Playwright and Chromium. Set
`AUDIT_CHROMIUM` if Chromium is installed outside `/usr/bin/chromium`.

The exporter `scripts/export_final_report_evidence.py` requires the separate
full frozen evidence directory. Regenerating the report from this snapshot does
not require that directory or network access. Full input authentication,
corporate-action resolution and raw-data execution verification are outside the
scope of this snapshot; hashes establish byte identity rather than authenticity.
