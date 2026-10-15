"""Observed NSE P/E snapshots, not a full historical accounting database."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pandas as pd


def earnings_yield(raw: Path, monthly: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Match the last observed P/E in each month, retaining positive earnings.

    No future interpolation or cross-month backfill. Weekly snapshots can be
    stale within a month; missing/negative earnings are outside the value sample.
    """
    rows, files = [], []
    for path in sorted((Path(raw) / 'pr').rglob('*.zip')):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist()
                     if re.match(r'^pe(?:_|\d)', Path(name).name, re.I) and name.lower().endswith('.csv')]
            if not names:
                continue
            day = pd.to_datetime(path.stem[2:], format='%d%m%y')
            for name in names:
                frame = pd.read_csv(io.BytesIO(archive.read(name)), dtype=str)
                frame.columns = frame.columns.str.strip().str.upper()
                pe_column = next((c for c in ('SYMBOL P/E', 'P/E', 'PE') if c in frame), None)
                if 'SYMBOL' not in frame or pe_column is None:
                    raise ValueError(f'Unknown symbol/P/E schema: {path.name}/{name}')
                item = frame[['SYMBOL', pe_column]].rename(columns={pe_column:'pe', 'SYMBOL':'symbol'})
                item.symbol = item.symbol.str.strip()
                item.pe = pd.to_numeric(item.pe, errors='coerce')
                item['date'] = day
                rows.append(item)
                files.append({'archive': path.name, 'member':name, 'date':str(day.date()), 'rows':len(item)})
    if not rows:
        return pd.DataFrame(), {'status':'unrun', 'reason':'No symbol P/E snapshots in supplied PR archives'}
    frame = pd.concat(rows, ignore_index=True).sort_values('date')
    frame['month'] = frame.date.dt.to_period('M')
    frame = frame.groupby(['month','symbol'], as_index=False).tail(1)
    valid = frame.pe.gt(0)
    mapping = monthly[['month','symbol','sec']].copy()
    mapping.month = pd.PeriodIndex(mapping.month, freq='M')
    matched = frame.loc[valid].merge(mapping, on=['month','symbol'], how='inner', validate='one_to_one')
    matched['ey'] = 1 / matched.pe
    score = matched.pivot_table(index='month', columns='sec', values='ey', aggfunc='last')
    return score, {'status':'loaded', 'source_files':files, 'first_observed_date':str(frame.date.min().date()),
                   'last_observed_date':str(frame.date.max().date()), 'snapshots':len(frame),
                   'positive_pe_rows':int(valid.sum()), 'excluded_nonpositive_or_missing':int((~valid).sum()),
                   'note':'Observed daily ratios in weekly NSE files; not publication-lagged financial statements'}
