"""Rebuild data/processed from data/raw (unmodified NSE files).

    python scripts/build_dataset.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from indialab.data import build  # noqa: E402

if __name__ == "__main__":
    t0 = time.time()
    stats = build.build(ROOT / "data" / "raw", ROOT / "data" / "processed", log=lambda s: print(s, flush=True))
    print(json.dumps({k: v for k, v in stats.items() if k != "missing_session_days_repaired"}, indent=1))
    print(f"repaired sessions: {len(stats['missing_session_days_repaired'])}; {time.time() - t0:.0f}s")
