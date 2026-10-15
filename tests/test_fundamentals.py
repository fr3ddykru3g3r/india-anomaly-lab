import sys
from pathlib import Path
import zipfile

import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from indialab.data.fundamentals import earnings_yield


def write(raw,name,csv):
    path=raw/'pr'/name
    path.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('PE_snapshot.csv',csv)


def test_value_uses_observed_month_and_excludes_missing_or_negative_earnings(tmp_path):
    write(tmp_path,'PR310124.zip','SYMBOL,SYMBOL P/E\nA,10\nB,-5\nC,-\n')
    write(tmp_path,'PR070224.zip','SYMBOL,SYMBOL P/E\nA,100\nB,20\n')
    mapping=pd.DataFrame({'month':['2024-01','2024-01','2024-01','2024-02','2024-02'],
                          'symbol':['A','B','C','A','B'],'sec':['A1','B1','C1','A1','B1']})
    score,info=earnings_yield(tmp_path,mapping)
    assert score.at[pd.Period('2024-01','M'),'A1']==.1
    assert score.at[pd.Period('2024-02','M'),'A1']==.01
    assert pd.isna(score.at[pd.Period('2024-01','M'),'B1'])
    assert 'C1' not in score
    assert info['excluded_nonpositive_or_missing']==2


def test_symbol_rename_is_matched_to_the_observed_month_not_todays_name(tmp_path):
    write(tmp_path,'PR310124.zip','SYMBOL,SYMBOL P/E\nOLD,10\n')
    mapping=pd.DataFrame({'month':['2024-01','2024-02'],'symbol':['OLD','NEW'],'sec':['S1','S1']})
    score,_=earnings_yield(tmp_path,mapping)
    assert len(score)==1 and score.columns.tolist()==['S1']


def test_missing_fundamentals_are_unrun_instead_of_a_price_proxy(tmp_path):
    score,info=earnings_yield(tmp_path,pd.DataFrame())
    assert score.empty and info['status']=='unrun'


def test_value_observation_window_does_not_count_later_cash_as_fundamental_evidence():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    from run_research_audit import evaluation_months
    from indialab import engine
    months=pd.period_range('2024-01','2026-08',freq='M')
    weights=pd.DataFrame({'A':1.},index=months)
    weights.attrs['first_signal_month']=pd.Period('2024-02','M')
    weights.attrs['last_signal_month']=pd.Period('2025-03','M')
    selected=evaluation_months(weights,months)
    z=weights.loc[selected]*0
    result,_=engine.simulate(weights.loc[selected],z+.01,z,z,z+1e9,
                             engine.RunConfig(tax=False,costs=False))
    assert len(result)==14
    assert str(result.index[0])=='2024-03' and str(result.index[-1])=='2025-04'
