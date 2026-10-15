#!/usr/bin/env python3
"""Verify preserved replay paths and evaluate block-length sensitivity.

Reads existing outputs only. Comparisons stay conditional on each recorded layer;
they do not cure missing-data assumptions or constitute new held-out tests.
"""
from pathlib import Path
import argparse
import sys
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from indialab import stats, study
from run_research_audit import dump


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',type=Path,required=True)
    args=ap.parse_args();run=args.run.resolve()
    audits=[];comparisons=[];original_family=[]
    for file in sorted((run/'returns').glob('*.csv')):
        frame=pd.read_csv(file,index_col=0)
        ok=frame.index.is_unique and np.isfinite(frame.net).all() and (frame.net>=-1).all()
        ok=bool(ok and (frame.cost_inr>=0).all() and (frame.tax_inr>=0).all())
        if len(frame)>1:
            implied=frame.wealth.iloc[1:].to_numpy()/frame.wealth.iloc[:-1].to_numpy()-1
            ok=bool(ok and np.allclose(implied,frame.net.iloc[1:].to_numpy(),atol=1e-12))
        audits.append({'file':str(file.relative_to(run)),'months':len(frame),'passed':ok})
    for prefix in ('','assume_last_mark:','assume_total_loss:'):
        for suffix in ('gross','costs','costs_tax'):
            layer=prefix+suffix
            control=run/'returns'/f'all_histories-{layer}-EW.csv'
            if not control.exists():continue
            ew=pd.read_csv(control,index_col=0).net
            paths={v.name:run/'returns'/f'all_histories-{layer}-{v.name}.csv' for v in study.GRID}
            missing=[name for name,path in paths.items() if not path.exists()]
            if missing:
                original_family.append({'layer':layer,'family':'original_preregistered_12',
                                        'status':'blocked','missing_models':missing})
            else:
                grid=pd.DataFrame({name:pd.read_csv(path,index_col=0).net for name,path in paths.items()})
                grid.index=pd.PeriodIndex(grid.index,freq='M');ew.index=pd.PeriodIndex(ew.index,freq='M')
                for window in ('full','2018_on'):
                    idx=grid.index.intersection(ew.index)
                    if window=='2018_on':idx=idx[idx>=pd.Period('2018-01','M')]
                    if len(idx)<24:continue
                    original_family.append({'layer':layer,'period':window,'family':'original_preregistered_12',
                        'status':'completed_conditional','months':len(idx),'first':str(idx.min()),'last':str(idx.max()),
                        'note':'Historical family and seen data; sensitivity assumptions are not causal fills.',
                        'spa':stats.spa(grid.loc[idx],ew.loc[idx],n_boot=1999,seed=7),
                        'white':stats.reality_check(grid.loc[idx],ew.loc[idx],n_boot=1999,seed=7)})
            for model in ('momentum12_top10','low_vol12_top10','reversal_top10','S5','Stockers_EMA200'):
                path=run/'returns'/f'all_histories-{layer}-{model}.csv'
                if not path.exists():continue
                r=pd.read_csv(path,index_col=0).net
                r.index=pd.PeriodIndex(r.index,freq='M');ew.index=pd.PeriodIndex(ew.index,freq='M')
                for period in ('full','2018_on'):
                    idx=r.index.intersection(ew.index)
                    if period=='2018_on':idx=idx[idx>=pd.Period('2018-01','M')]
                    if len(idx)<24:continue
                    for block in (3,6,12,24):
                        comparisons.append({'layer':layer,'model':model,'period':period,
                            'first':str(idx.min()),'last':str(idx.max()),
                            **stats.bootstrap_cagr_diff(r.loc[idx],ew.loc[idx],n_boot=1999,seed=7,mean_block=block)})
    dump(run/'output_path_checks.json',audits)
    dump(run/'original_grid_statistics.json',original_family)
    pd.DataFrame(comparisons).to_csv(run/'block_length_sensitivity.csv',index=False)
    print(f'{sum(x["passed"] for x in audits)}/{len(audits)} paths pass consistency checks; {len(comparisons)} conditional block comparisons')
    return int(any(not x['passed'] for x in audits))


if __name__=='__main__':raise SystemExit(main())
