import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from indialab import stats, integrity


def test_bootstrap_joint_resampling_has_zero_difference_for_identical_series():
    r = pd.Series(np.random.default_rng(8).normal(.01, .04, 120))
    result = stats.bootstrap_cagr_diff(r, r, n_boot=199, seed=4)
    assert result['diff'] == result['lo'] == result['hi'] == 0
    assert result['seed'] == 4 and result['n_boot'] == 199


def test_finite_bootstrap_never_claims_a_zero_p_value():
    rng = np.random.default_rng(5)
    benchmark = pd.Series(rng.normal(0, .01, 120), name='control')
    variants = pd.DataFrame({'strong': benchmark + rng.normal(.2, .01, 120),
                             'noise': benchmark + rng.normal(0, .01, 120)})
    for method in (stats.spa, stats.reality_check):
        result = method(variants, benchmark, n_boot=99)
        assert .01 <= result['p_value'] <= 1


def test_bootstrap_rejects_an_uninformative_one_month_sample():
    with pytest.raises(ValueError, match='two aligned months'):
        stats.bootstrap_cagr_diff(pd.Series([.1]), pd.Series([.2]))


def test_drawdown_includes_the_loss_from_initial_capital():
    r = pd.Series([-.5] + [0.] * 11)
    assert integrity.perf(r)['max_dd'] == pytest.approx(-.5)
