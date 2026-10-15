#!/usr/bin/env bash
# Run from any directory, using the existing project checkout and existing NSE files.
set -euo pipefail
TASK_PROJECT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$TASK_PROJECT_DIR"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-research.lock.txt
# Explicit real mode never falls back to synthetic data. Missing archives fail and
# produce a report; existing historical CSVs are not treated as fresh evidence.
.venv/bin/python scripts/run_research_audit.py --mode real --data-root "$TASK_PROJECT_DIR/data"
