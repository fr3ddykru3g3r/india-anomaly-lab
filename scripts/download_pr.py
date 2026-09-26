"""Download one NSE PR archive per week (corporate actions), Jul 2010 onward (resumable).

    python scripts/download_pr.py [start YYYY-MM-DD] [end YYYY-MM-DD]
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from indialab.data import nse  # noqa: E402

if __name__ == "__main__":
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else nse.PR_FROM
    end = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 9, 25)
    nse.download_pr(start, end, log=lambda s: print(s, flush=True))
