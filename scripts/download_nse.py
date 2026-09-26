"""Download NSE bhavcopies into data/raw (resumable).

    python scripts/download_nse.py [start YYYY-MM-DD] [end YYYY-MM-DD]
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from indialab.data import nse  # noqa: E402

if __name__ == "__main__":
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2006, 1, 1)
    end = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 9, 25)
    nse.download(start, end, log=lambda s: print(s, flush=True))
