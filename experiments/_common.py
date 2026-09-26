"""Shared loading for experiments: real NSE data (pre-registration enforced) or a synthetic market."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from indialab import research as rs, study, synthetic as S  # noqa: E402
from indialab.data import build  # noqa: E402

PREREG_TAG = "prereg-v1"
COLORS = {"ew": "#52514e", "bench": "#8a8983", "momentum": "#eb6834", "low_vol": "#1baf7a",
          "reversal": "#2a78d6", "high_52w": "#9b59b6"}
plt.rcParams.update({"text.parse_math": False, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": "#e6e5e0", "font.size": 9.5})


def prereg_guard() -> None:
    """Refuse to compute real-data anomaly returns unless the pre-registration is frozen
    (a prereg-v1* tag exists) and the document is unchanged since the newest such tag.
    Amendments are allowed only as new tags (prereg-v1-amendN) with a dated entry."""
    def git(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    tags = git("tag", "-l", f"{PREREG_TAG}*", "--sort=-creatordate").stdout.split()
    if not tags:
        sys.exit(f"Blocked: tag '{PREREG_TAG}' not found. Freeze docs/PREREGISTRATION.md first "
                 f"(git commit + git tag {PREREG_TAG}); run with --data synthetic to dry-run.")
    if git("diff", "--quiet", tags[0], "--", "docs/PREREGISTRATION.md").returncode != 0:
        sys.exit(f"Blocked: docs/PREREGISTRATION.md changed since tag {tags[0]}. Record changes as a dated "
                 "amendment and tag it, or revert.")


def args(desc: str) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--data", choices=["real", "synthetic"], default="real")
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args()


def load(kind: str, seed: int = 0):
    """Returns (monthly, secs, benchmark_sec, out_dir)."""
    if kind == "real":
        prereg_guard()
        monthly, secs = build.load(ROOT / "data" / "processed")
        bench = secs.index[secs["last_symbol"].astype(str) == "NIFTYBEES"]
        bench = bench[0] if len(bench) else None
    else:
        R, info = S.simulate(S.MarketParams(n_firms=1200, months=249), seed=seed)
        monthly, secs = S.to_monthly_panel(R, info, seed=seed)
        bench = None
    out = ROOT / "results" / kind
    out.mkdir(parents=True, exist_ok=True)
    (ROOT / "figures" / kind).mkdir(parents=True, exist_ok=True)
    return monthly, secs, bench, out


def make_study(monthly, secs, bench, label, terminal: float = 0.0, log: bool = True,
               total_return: bool = False, extended: bool = False) -> study.Study:
    """Primary sample by default; extended=True starts as early as the data allow."""
    p = rs.Panel.from_monthly(monthly, secs, total_return=total_return)
    return study.Study(p, label=label, benchmark_sec=bench, terminal=terminal,
                       log_path=(ROOT / "results" / "variants_log.csv") if log else None,
                       first_hold=None if extended else study.PRIMARY_FIRST_HOLD)


def fig_path(kind: str, name: str) -> Path:
    return ROOT / "figures" / kind / f"{name}.png"
