#!/usr/bin/env python3
"""Freeze available input bytes and rebuild in a new evidence directory."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from indialab.data import build,corpactions
import pandas as pd


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--raw',type=Path,default=ROOT/'data/raw')
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--supplied-actions',type=Path,
                    help='Explicit alternative to downloaded PR action records; recorded in lineage')
    args=ap.parse_args();out=args.out.resolve();raw=args.raw.resolve()
    out.mkdir(parents=True,exist_ok=False)
    files=sorted(p for p in raw.rglob('*') if p.is_file() and p.suffix!='.part')
    for file in files:
        dest=out/'raw'/file.relative_to(raw);dest.parent.mkdir(parents=True,exist_ok=True)
        if file.suffix=='.zip':
            try:os.link(file,dest)
            except OSError:shutil.copy2(file,dest)
        else:shutil.copy2(file,dest)
    lineage={'source_raw':str(raw),'frozen_raw_files':len(files),
             'corporate_action_policy':'downloaded PR records',
             'note':'ZIP files are immutable hardlinks where available; mutable manifests/reference files are copied.'}
    if args.supplied_actions:
        actions=out/'supplied_corporate_actions.parquet'
        shutil.copy2(args.supplied_actions,actions)
        lineage.update(corporate_action_policy='explicit supplied processed action records',
                       supplied_action_source=str(args.supplied_actions.resolve()),
                       supplied_action_sha256=hashlib.sha256(actions.read_bytes()).hexdigest())
        corpactions.load_records=lambda ignored:pd.read_parquet(actions)
    (out/'lineage.json').write_text(json.dumps(lineage,indent=2)+'\n')
    stats=build.build(out/'raw',out/'processed',log=lambda x:print(x,flush=True))
    print('Frozen input period:',stats['first_date'],stats['last_date'],flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
