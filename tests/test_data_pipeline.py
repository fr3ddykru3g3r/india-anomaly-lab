"""End-to-end test of the NSE data pipeline on a fake archive with known truth."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from indialab.data import bhavcopy, build, fake  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("nse")
    info = fake.build(root / "raw")
    stats = build.build(root / "raw", root / "processed", log=lambda s: None)
    daily = pd.read_parquet(root / "processed" / "daily.parquet")
    secs = pd.read_parquet(root / "processed" / "securities.parquet")
    return info, stats, daily, secs


def test_identity_survives_rename_split_and_rejects_symbol_reuse(built):
    info, stats, daily, secs = built
    ids = set(secs.index)
    assert "NEWNAME|20100901" in ids                 # OLDNAME -> NEWNAME linked (pre-ISIN)
    assert "SPLITCO|20100901" in ids                 # new ISIN after split, same security
    assert len([i for i in ids if i.startswith("DEADCO|")]) == 2   # reuse = different company
    assert stats["symbol_change_links"] == 1 and stats["reused_symbols"] == 1


def test_returns_match_truth_through_splits_bonus_rename_and_missing_file(built):
    info, stats, daily, secs = built
    missing = info["missing"]
    assert stats["missing_session_days_repaired"] == [(info["days"][list(info["days"]).index(missing) + 1]).strftime("%Y-%m-%d")]
    name_to_sec = {"SPLITCO": "SPLITCO|20100901", "BONUSCO": "BONUSCO|20100901", "DIVCO": "DIVCO|20100901",
                   "OLDNAME": "NEWNAME|20100901", "PLAIN0": "PLAIN0|20100901", "DEADCO": "DEADCO|20100901"}
    for name, sec in name_to_sec.items():
        truth = info["truth"][name]
        # the day after the missing file carries two days of true return
        nxt = truth.index[truth.index.get_loc(missing) + 1] if missing in truth.index else None
        if nxt is not None:
            truth = truth.copy()
            truth[nxt] = (1 + truth[missing]) * (1 + truth[nxt]) - 1
            truth = truth.drop(missing)
        got = daily[daily["sec"] == sec].set_index("date")["ret"].dropna()
        pd.testing.assert_series_equal(got.sort_index(), truth.sort_index(), check_names=False,
                                       check_freq=False, check_index_type=False, rtol=1e-6)


def test_corporate_actions_from_records_and_dividend_yield(built):
    info, stats, daily, secs = built
    assert stats["ca_split_bonus_records_applied"] == 2           # SPLITCO split, BONUSCO bonus
    div = daily[(daily["sec"] == "DIVCO|20100901") & (daily["div_yield"] > 0)]
    assert len(div) == 1 and div["date"].iloc[0] == info["days"][180]
    assert div["div_yield"].iloc[0] == pytest.approx(5.0 / div["prevclose"].iloc[0])


def test_detector_recovers_splits_when_no_records_exist(tmp_path):
    info = fake.build(tmp_path / "raw", with_pr=False)
    stats = build.build(tmp_path / "raw", tmp_path / "p", log=lambda s: None)
    daily = pd.read_parquet(tmp_path / "p" / "daily.parquet")
    assert stats["ca_detector_events_applied"] == 2
    got = daily[daily["sec"] == "SPLITCO|20100901"].set_index("date")["ret"].dropna()
    truth = info["truth"]["SPLITCO"].drop(info["missing"])
    assert np.allclose(got.loc[got.index.intersection(truth.index)].drop(info["days"][201], errors="ignore"),
                       truth.loc[got.index.intersection(truth.index)].drop(info["days"][201], errors="ignore"))


def test_listing_day_is_not_investable_and_exit_is_flagged(built):
    info, stats, daily, secs = built
    ipo = daily[daily["sec"] == "IPOCO|20111026"].sort_values("date")
    assert np.isnan(ipo["ret"].iloc[0])
    dead = secs.loc["DEADCO|20100901"]
    assert dead["exited"] and dead["distressed_exit"]
    assert not secs.loc["DEADCO|20120328", "exited"]


def test_udiff_and_legacy_formats_parse_identically(tmp_path):
    info = fake.build(tmp_path / "a", start="2024-06-24", n_sessions=20, isin_from="2000-01-01", missing_session=-1,
                      with_pr=False)
    files = sorted((tmp_path / "a" / "bhavcopy").rglob("*.zip"))
    assert any(f.name.startswith("cm") for f in files) and any(f.name.startswith("BhavCopy") for f in files)
    df = bhavcopy.read_all(files)
    assert df["isin"].notna().all() and df["date"].nunique() == 20
    assert (df.groupby("date").size() == df.groupby("date").size().iloc[0]).all()


def test_bc_parser_reads_both_nse_date_formats():
    from indialab.data import corpactions as CA
    old = CA._split_line("EQ,HDFCBANK,HDFC Bank Ltd.,20/09/2019, , ,19/09/2019, , ,FV SPLT FRM RS 2 TO RS 1")
    new = CA._split_line("EQ,KOTAKBANK,Kotak Mahindra Bank, Ltd,2026-01-14,,,2026-01-14,,,FV SPLIT RS.5 TO RE.1")
    assert old["ex_dt"] == "19/09/2019" and new["ex_dt"] == "2026-01-14" and new["security"].endswith("Ltd")
