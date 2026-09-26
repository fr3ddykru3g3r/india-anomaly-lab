import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from indialab import costs, engine, research as rs, stats, study, synthetic as S, tax  # noqa: E402

LOOSE = rs.UniverseRule(size=10_000, min_price=0, min_history_months=1, min_days_last_month=0)


@pytest.fixture(scope="module")
def market():
    R, info = S.simulate(S.MarketParams(n_firms=300, months=120), seed=1)
    m, secs = S.to_monthly_panel(R, info)
    return R, info, rs.Panel.from_monthly(m, secs)


def test_equal_weight_matches_synthetic_truth_exactly(market):
    R, info, p = market
    ew = study.Study(p, LOOSE, first_hold=None).ew()
    truth = S.strategy_returns(R, "equal_weight")
    truth.index = pd.period_range("2006-01", periods=len(R), freq="M")[truth.index]
    j = pd.concat([ew, truth], axis=1).dropna()
    assert len(j) > 100 and np.allclose(j.iloc[:, 0], j.iloc[:, 1], atol=1e-12)


def test_primary_sample_starts_after_record_coverage(market):
    R, info, p = market
    assert study.Study(p, LOOSE).ew().index.min() == study.PRIMARY_FIRST_HOLD


def test_signals_do_not_look_ahead(market):
    R, info, p = market
    v = study.Variant("momentum", 12, 0.1)
    t = p.close.index[60]
    before = study.Study(p, LOOSE).members(v).loc[t]
    p2 = rs.Panel(**{k: (getattr(p, k).copy() if hasattr(getattr(p, k), "copy") else getattr(p, k))
                     for k in p.__dataclass_fields__})
    later = p2.px.index > t
    p2.px.loc[later] = p2.px.loc[later] * np.random.default_rng(0).uniform(0.2, 5, p2.px.loc[later].shape)
    p2.ret_cc.loc[later] = 0.3
    after = study.Study(p2, LOOSE).members(v).loc[t]
    assert before.equals(after)


def test_engine_without_frictions_reproduces_gross(market):
    R, info, p = market
    st = study.Study(p, LOOSE)
    w = rs.target_weights(st.members(study.Variant("low_vol", 12, 0.2)))
    out, _ = st.run(w, engine.RunConfig(costs=False, tax=False, band=0.0), "test")
    g = st.gross(w).loc[out.index]
    assert np.allclose(out["net"], g, atol=1e-10)


def test_engine_cost_for_a_single_purchase_matches_cost_model():
    months = pd.period_range("2025-01", periods=2, freq="M")
    w = pd.DataFrame({"A": [1.0, 1.0]}, index=months)
    zero = pd.DataFrame({"A": [0.0, 0.0]}, index=months)
    out, _ = engine.simulate(w, zero, zero, zero, zero + 1e12, engine.RunConfig(capital=1e5, tax=False))
    expected = costs.trade_cost(1e5, "buy", "2025-02-01", k=0).total
    assert out["cost_inr"].iloc[0] == pytest.approx(expected, rel=1e-9)
    assert out["cost_inr"].iloc[1] == 0.0                      # inside the band: no trade


def test_stcg_after_july_2024_is_20pct_plus_cess():
    b = tax.TaxBook()
    b.buy("X", pd.Timestamp("2024-08-01"), 100_000)
    b.grow("X", 1.5)
    b.sell_all("X", pd.Timestamp("2025-01-01"))
    assert b.settle(2024) == pytest.approx(50_000 * 0.20 * 1.04)


def test_ltcg_exemption_and_grandfathering():
    b = tax.TaxBook()
    b.buy("X", pd.Timestamp("2017-01-02"), 100_000)
    b.grow("X", 1.5)
    b.mark_grandfather()                                       # worth 150k on 31 Jan 2018
    b.grow("X", 2.0)                                           # 300k at sale
    b.sell_all("X", pd.Timestamp("2019-06-03"))
    # gain = 300k - 150k (grandfathered cost) = 150k; exemption 1 lakh; 10% + 4% cess
    assert b.settle(2019) == pytest.approx(50_000 * 0.10 * 1.04)


def test_ltcg_was_exempt_before_2018_and_losses_carry_forward():
    b = tax.TaxBook()
    b.buy("X", pd.Timestamp("2010-01-04"), 100_000)
    b.grow("X", 3.0)
    b.sell_all("X", pd.Timestamp("2012-01-02"))
    assert b.settle(2011) == 0.0
    b.buy("Y", pd.Timestamp("2012-05-01"), 100_000)
    b.grow("Y", 0.6)
    b.sell_all("Y", pd.Timestamp("2012-09-03"))                # 40k short-term loss
    assert b.settle(2012) == 0.0
    b.buy("Z", pd.Timestamp("2013-05-01"), 100_000)
    b.grow("Z", 1.5)
    b.sell_all("Z", pd.Timestamp("2013-09-02"))                # 50k STCG - 40k carried = 10k
    assert b.settle(2013) == pytest.approx(10_000 * 0.15 * 1.03)


def test_reality_check_size_and_power():
    idx = pd.period_range("2006-01", periods=240, freq="M")
    rejections = 0
    for seed in range(30):
        rng = np.random.default_rng(seed)
        variants = pd.DataFrame(rng.normal(0.01, 0.05, (240, 30)), index=idx)
        bench = pd.Series(rng.normal(0.01, 0.05, 240), index=idx)
        rejections += stats.reality_check(variants, bench, n_boot=500, seed=seed)["p_value"] < 0.05
        rejections += stats.spa(variants, bench, n_boot=500, seed=seed)["p_value"] < 0.05
    assert rejections <= 8                                     # ~5% nominal size, 2 tests x 30 noise studies
    variants[29] = bench + 0.01 + np.random.default_rng(9).normal(0, 0.01, 240)   # one real edge
    # The Reality Check lets 29 noisy variants hide the real one; the studentized SPA finds it.
    assert stats.spa(variants, bench, n_boot=1000)["p_value"] < 0.01


def test_deflated_sharpe_falls_with_more_trials():
    r = pd.Series(np.random.default_rng(1).normal(0.01, 0.04, 240))
    few = stats.deflated_sharpe(r, 1, 0.01)["dsr"]
    many = stats.deflated_sharpe(r, 100, 0.01)["dsr"]
    assert many < few
