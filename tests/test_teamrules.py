import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from indialab import research as rs, study, synthetic as S, teamrules as T  # noqa: E402

LOOSE = rs.UniverseRule(size=10_000, min_price=0, min_history_months=13, min_days_last_month=0)


def test_s5_step_holds_exits_and_fills_by_momentum():
    mom = pd.Series({"A": 0.5, "B": 0.4, "C": 0.9, "D": 0.1})
    oc = pd.Series({"A": -0.05, "B": -0.02, "C": -0.03, "D": 0.04})
    vd = pd.Series({"A": 10, "B": -5, "C": 3, "D": 8})
    # held X leaves eligibility -> exit; Y stays; setups are A (red, +vd) and C; B has negative volume delta
    new = T.s5_step(["X", "Y"], ["A", "B", "C", "D", "Y"], mom, oc, vd, capacity=2)
    assert new == ["Y", "C"]                          # one free slot, C has the strongest momentum
    blocked = T.s5_step(["Y"], ["A", "C", "Y"], mom, oc, vd, capacity=2, blocked={"C"})
    assert blocked == ["Y", "A"]                      # 'no chasing' skips C


@pytest.fixture(scope="module")
def setup():
    R, info = S.simulate(S.MarketParams(n_firms=300, months=120), seed=2)
    m, secs = S.to_monthly_panel(R, info)
    p = rs.Panel.from_monthly(m, secs)
    elig = rs.universe(p, LOOSE)
    rng = np.random.default_rng(0)
    feat = {"px_last": p.px, "ema200": p.px.rolling(10, min_periods=5).mean(), "vol63": p.daily_vol(3) * np.sqrt(252),
            "voldelta": pd.DataFrame(rng.normal(0, 1, p.px.shape), index=p.px.index, columns=p.px.columns),
            "month_ret_oc": p.ret_cc}
    return p, elig, feat


def test_weights_have_no_lookahead(setup):
    p, elig, feat = setup
    t = p.px.index[70]
    for spec in T.SPECS:
        before = T.weights(spec, p, elig, feat).loc[:t]
        p2 = rs.Panel(**{k: (getattr(p, k).copy() if hasattr(getattr(p, k), "copy") else getattr(p, k))
                         for k in p.__dataclass_fields__})
        f2 = {k: v.copy() for k, v in feat.items()}
        later = p.px.index > t
        for df in (p2.px, p2.ret_cc):
            df.loc[later] = df.loc[later] * np.random.default_rng(1).uniform(0.3, 3, df.loc[later].shape)
        for k in f2:
            f2[k].loc[later] = f2[k].loc[later] * 2.0
        after = T.weights(spec, p2, elig, f2).loc[:t]
        assert np.allclose(before.values, after.values), spec.key


def test_weights_are_valid_and_s0_is_the_universe(setup):
    p, elig, feat = setup
    for spec in T.SPECS:
        w = T.weights(spec, p, elig, feat)
        assert (w >= 0).all().all() and (w.sum(axis=1) <= 1 + 1e-9).all()
    w0 = T.weights(T.SPECS[0], p, elig, feat)
    assert np.allclose(w0.loc[elig.any(axis=1)].sum(axis=1), 1.0)
    assert ((w0 > 0) <= elig).all().all()
