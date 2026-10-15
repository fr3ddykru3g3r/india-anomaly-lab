"""Regression checks tied to observed reproduction defects, not alpha claims."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from indialab import engine, stockers, costs


def simulate(w, r, **kwargs):
    zeros = w * 0
    return engine.simulate(w, r, zeros, zeros, zeros + 1e9,
                           engine.RunConfig(costs=False, tax=False, **kwargs))


def test_cash_months_are_retained_and_earn_declared_cash_yield():
    idx = pd.period_range('2024-01', periods=4, freq='M')
    w = pd.DataFrame({'A': [0., 0., 0., 1.]}, index=idx)
    out, _ = simulate(w, w * 0, cash_yield=0.12)
    assert len(out) == 4
    assert out.index.equals(idx + 1)
    assert np.allclose(out.net.iloc[:3], 1.12 ** (1 / 12) - 1)


def test_rebalance_band_keeps_intended_cash_instead_of_investing_it():
    idx = pd.period_range('2024-01', periods=2, freq='M')
    w = pd.DataFrame({'A': [.4, .4], 'B': [.4, .2]}, index=idx)
    r = pd.DataFrame({'A': [0., 0.], 'B': [0., 1.]}, index=idx)
    out, _ = simulate(w, r)
    assert out.net.iloc[1] == pytest.approx(.2)


def test_missing_invested_return_cannot_silently_become_cash_or_zero():
    w = pd.DataFrame({'A': [1.]}, index=pd.period_range('2024-01', periods=1, freq='M'))
    with pytest.raises(ValueError, match='Missing holding return'):
        simulate(w, w * np.nan)


def test_empty_simulation_has_a_defined_wealth_summary():
    w = pd.DataFrame(columns=['A'], index=pd.PeriodIndex([], freq='M'), dtype=float)
    out, summary = simulate(w, w)
    assert out.empty and summary['final_wealth_pre_liquidation'] == 1e6


def test_capacity_breach_is_published_even_when_the_cost_formula_returns_a_number():
    w = pd.DataFrame({'A': [1.]}, index=pd.period_range('2024-01', periods=1, freq='M'))
    z = w * 0
    out, _ = engine.simulate(w, z, z, z, z + 100, engine.RunConfig(tax=False))
    assert out.capacity_breach.iloc[0]
    assert out.max_participation.iloc[0] == pytest.approx(10000)


def test_spread_estimate_never_reads_tomorrows_high_or_low():
    idx = pd.bdate_range('2024-01-01', periods=50)
    rng = np.random.default_rng(33)
    close = pd.Series(np.exp(rng.normal(0, .02, 50).cumsum()) * 100, index=idx)
    high, low = close * 1.03, close * .97
    before = costs.abdi_ranaldo_half_spread(high, low, close)
    high2, low2 = high.copy(), low.copy()
    high2.iloc[30:] *= 10
    low2.iloc[30:] /= 10
    after = costs.abdi_ranaldo_half_spread(high2, low2, close)
    pd.testing.assert_series_equal(before.iloc[:30], after.iloc[:30])


def daily_frame():
    rng = np.random.default_rng(64)
    idx = pd.bdate_range('2010-01-01', periods=600)
    close = 100 * np.exp(np.cumsum(rng.normal(.0005, .025, len(idx))))
    op = close * np.exp(rng.normal(0, .012, len(idx)))
    return pd.DataFrame({'date': idx, 'sec': 'TEST', 'open': op, 'close': close,
                         'high': np.maximum(op, close) * 1.01,
                         'low': np.minimum(op, close) * .99, 'px': close,
                         'volume': rng.integers(1000, 1_000_000, len(idx))})


def test_uploaded_engine_identity_and_original_lag_are_preserved():
    module = stockers.load_engine()
    assert module.ALLOW_DOWNLOAD is False
    d = daily_frame()
    strategy = stockers.symbol_replay(d, warmup=0)
    # Each BUY affects the next row's open/open position, never its own month.
    for i in range(1, len(strategy.signals)):
        if strategy.signals.Signal.iloc[i - 1] == 'BUY':
            assert strategy.position.iloc[i] == 1
    assert (strategy.position > 0).any()


def test_stockers_positions_do_not_change_when_future_daily_prices_change():
    d = daily_frame()
    cutoff = pd.Timestamp('2011-03-31')
    d2 = d.copy()
    mask = d2.date > cutoff
    for col in ('open', 'high', 'low', 'close', 'px'):
        d2.loc[mask, col] *= 50
    before = stockers.symbol_replay(d).position.loc[:cutoff]
    after = stockers.symbol_replay(d2).position.loc[:cutoff]
    pd.testing.assert_series_equal(before, after)


def test_stockers_execution_warmup_only_uses_previously_completed_sessions():
    strategy = stockers.symbol_replay(daily_frame(), warmup=100)
    eligible = strategy.signals['_cumobs'].shift(1).ge(100)
    assert strategy.signals.Tradable.equals(eligible)
    assert strategy.position.loc[~eligible].eq(0).all()


def test_duplicate_daily_bars_are_rejected_before_stockers_replay():
    d = daily_frame()
    with pytest.raises(ValueError, match='Duplicate'):
        stockers.symbol_replay(pd.concat([d, d.iloc[:1]]))


def test_study_excludes_the_holding_interval_without_a_following_month_open():
    from indialab import synthetic, research, study
    r, info = synthetic.simulate(synthetic.MarketParams(n_firms=50, months=100), seed=12)
    monthly, securities = synthetic.to_monthly_panel(r, info)
    panel = research.Panel.from_monthly(monthly, securities)
    st = study.Study(panel, first_hold=None)
    assert len(st.months)
    assert (st.months + 1 < panel.close.index.max()).all()


def test_missing_price_sensitivities_are_explicit_and_never_create_a_new_fill():
    idx = pd.period_range('2024-01', periods=2, freq='M')
    w = pd.DataFrame({'A': [0., 1.]}, index=idx)
    r = pd.DataFrame({'A': [0., float('nan')]}, index=idx)
    for policy in ('last_mark', 'total_loss'):
        result, _ = simulate(w, r, missing_return_policy=policy)
        assert result.net.eq(0).all()
        assert result.unfilled_target_weight.iloc[-1] == 1


def test_zero_recovery_sensitivity_books_an_existing_position_loss():
    idx = pd.period_range('2024-01', periods=2, freq='M')
    w = pd.DataFrame({'A': [.5, .5]}, index=idx)
    r = pd.DataFrame({'A': [0., float('nan')]}, index=idx)
    optimistic, _ = simulate(w, r, missing_return_policy='last_mark')
    severe, _ = simulate(w, r, missing_return_policy='total_loss')
    assert optimistic.net.iloc[-1] == 0
    assert severe.net.iloc[-1] == pytest.approx(-.5)
    assert severe.missing_exposure_start.iloc[-1] == pytest.approx(.5)


def test_stockers_does_not_invent_executable_bars_on_zero_volume_days():
    d = daily_frame()
    d.loc[0, ['open','high','low','volume']] = 0
    frame = stockers.ohlcv(d)
    assert d.date.iloc[0] not in frame.index
    assert len(frame) == len(d)-1


def test_paper_gross_returns_also_reject_missing_invested_prices():
    from indialab import study
    st = study.Study.__new__(study.Study)
    st.months = pd.period_range('2024-01', periods=1, freq='M')
    st.hold = pd.DataFrame({'A':[np.nan], 'B':[.1]}, index=st.months)
    weights = pd.DataFrame({'A':[.5], 'B':[.5]}, index=st.months)
    with pytest.raises(ValueError, match='Missing holding return'):
        st.gross(weights)
    weights['A'], weights['B'] = 0., 1.
    assert st.gross(weights).iloc[0] == pytest.approx(.1)


def test_corporate_action_on_a_nontrading_day_never_attaches_to_an_earlier_session():
    from indialab.data import corpactions
    daily = pd.DataFrame({'symbol':['A','A'], 'date':pd.to_datetime(['2024-01-05','2024-01-08'])})
    records = pd.DataFrame({'symbol':['A'], 'ex_date':pd.to_datetime(['2024-01-06']),
                            'factor':[2.], 'dividend':[5.], 'kind':['split+dividend']})
    attached = corpactions.attach(daily, records)
    assert attached.idx.tolist() == [1]
    assert (daily.loc[attached.idx, 'date'].to_numpy() >= records.ex_date.to_numpy()).all()


def test_a_fully_exited_position_needs_its_opening_mark_but_not_its_future_return():
    idx=pd.period_range('2024-01',periods=2,freq='M')
    w=pd.DataFrame({'A':[.5,0.]},index=idx);z=w*0
    r=pd.DataFrame({'A':[0.,np.nan]},index=idx)
    available=pd.DataFrame({'A':[True,True]},index=idx)
    cfg=engine.RunConfig(costs=False,tax=False)
    result,_=engine.simulate(w,r,z,z,z+1e9,cfg,execution_available=available)
    assert result.net.eq(0).all()
    available.iloc[1,0]=False
    with pytest.raises(ValueError,match='Missing executable opening price'):
        engine.simulate(w,r,z,z,z+1e9,cfg,execution_available=available)
    severe,_=engine.simulate(w,r,z,z,z+1e9,
        engine.RunConfig(costs=False,tax=False,missing_return_policy='total_loss'),execution_available=available)
    assert severe.net.iloc[1]==pytest.approx(-.5)


def test_a_later_trade_in_a_month_cannot_supply_the_markets_first_open():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    from run_research_audit import opening_availability
    daily=pd.DataFrame({'date':pd.to_datetime(['2024-05-01','2024-05-01','2024-06-03','2024-06-04']),
                        'sec':['A','B','B','A'],'open':[10.,10.,10.,10.],'volume':[100,100,100,100]})
    signals=pd.period_range('2024-04',periods=2,freq='M')
    available=opening_availability(daily,signals,['A','B'])
    assert available.loc[pd.Period('2024-04','M')].all()
    assert not available.at[pd.Period('2024-05','M'),'A']
    assert available.at[pd.Period('2024-05','M'),'B']


def test_april_tax_is_paid_in_full_after_a_current_year_assumed_loss():
    idx=pd.period_range('2023-12',periods=4,freq='M')
    w=pd.DataFrame({'A':[.5,0.,0.,0.],'B':[.5,.5,.5,.5]},index=idx)
    r=w*0;r.iloc[0,0]=1.
    z=w*0;available=pd.DataFrame(True,index=idx,columns=w.columns)
    available.iloc[-1,1]=False
    result,_=engine.simulate(w,r,z,z,z+1e9,
        engine.RunConfig(costs=False,missing_return_policy='total_loss'),execution_available=available)
    # Sell A in February: INR 500,000 ST gain. B's April loss belongs to
    # the next FY; INR 78,000 prior-FY tax must leave the remaining 750,000.
    assert result.tax_inr.iloc[-1]==pytest.approx(78000.)
    assert result.wealth.iloc[-1]==pytest.approx(672000.)
