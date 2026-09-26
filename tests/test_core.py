import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from indialab import costs, integrity, synthetic as S  # noqa: E402


def test_current_delivery_round_trip_statutory_cost():
    # Rs 1 lakh round trip, zero impact, worked by hand:
    # STT 100+100, stamp 15 (buy), NSE 3.07+3.07, SEBI 0.10+0.10, GST 18% of (NSE+SEBI)=1.1412, DP 15.34 (sell)
    b = costs.trade_cost(100_000, "buy", "2026-01-15", k=0)
    s = costs.trade_cost(100_000, "sell", "2026-01-15", k=0)
    assert b.total + s.total == pytest.approx(237.8212, abs=1e-3)


def test_stt_regime_is_dated():
    assert costs._stt_rate(pd.Timestamp("2004-01-01")) == 0.0
    assert costs._stt_rate(pd.Timestamp("2010-01-01")) == pytest.approx(0.00125)
    assert costs._stt_rate(pd.Timestamp("2026-01-01")) == pytest.approx(0.001)


def test_impact_grows_with_participation():
    small = costs.trade_cost(1e5, "buy", "2026-01-15", sigma_daily=0.02, adv_inr=1e8).impact
    large = costs.trade_cost(1e7, "buy", "2026-01-15", sigma_daily=0.02, adv_inr=1e8).impact
    assert large / 1e7 > small / 1e5


def test_selection_vs_timing_signs():
    idx = pd.RangeIndex(120)
    u = pd.Series(np.r_[np.full(60, 0.01), np.full(10, -0.08), np.full(50, 0.01)], index=idx)
    s = u.where(u > 0, 0.0)                      # a rule that sits out the crash
    t = integrity.selection_vs_timing(s, u)
    assert t.attrs["risk_contribution"] > 0 and t.attrs["return_contribution"] > 0


def test_grade_fatal_integrity_failure_is_D_even_if_stats_pass():
    c = integrity.Credibility({k: True for k, _ in integrity.CHECKS})
    assert c.grade() == "A"
    c.results["point_in_time_universe"] = False
    assert c.grade() == "D"


def test_synthetic_truth_has_no_bias_and_deletion_inflates_equal_weight():
    R, info = S.simulate(S.MarketParams(n_firms=200, months=120), seed=3)
    truth = S.cagr(S.strategy_returns(R, "equal_weight"))
    same = S.cagr(S.strategy_returns(S.view(R, info, "vendor", 0.0), "equal_weight"))
    assert same == pytest.approx(truth)
    biased = S.cagr(S.strategy_returns(S.view(R, info, "vendor", 1.0), "equal_weight"))
    assert biased > truth


def test_strategy_has_no_lookahead_on_exits():
    # A firm that goes bankrupt at month t must deliver its terminal loss to a holder.
    R, info = S.simulate(S.MarketParams(n_firms=100, months=60), seed=1)
    dead = info[info.exit == "bankrupt"]
    assert len(dead) and (R.loc[dead.iloc[0].end, dead.index[0]] <= -0.89)
