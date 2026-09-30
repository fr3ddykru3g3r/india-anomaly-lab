#!/usr/bin/env bash
# Full pipeline, in order. Real-data steps E2-E4 refuse to run until tag prereg-v1 exists.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
$PY -m pytest -q tests
$PY experiments/e0_synthetic_survivorship.py 12
$PY scripts/download_nse.py           # resumable; skips files already in the manifest
$PY scripts/download_pr.py
$PY scripts/build_dataset.py
$PY experiments/e1_data_coverage.py
$PY experiments/e2_replicate.py --data real
$PY experiments/e3_reality.py --data real
$PY experiments/e4_bias.py --data real
$PY experiments/e5_why_h6_failed.py
$PY experiments/e6_value_short_sample.py
$PY experiments/e7_tool_data.py
$PY experiments/e8_team_rules.py
$PY experiments/e9_all_models_ledger.py
$PY experiments/e10_robustness.py
$PY scripts/build_report.py real
$PY scripts/build_site.py real
$PY scripts/build_tool.py
$PY scripts/build_paper.py
$PY scripts/key_numbers.py real
