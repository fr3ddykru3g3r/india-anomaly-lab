#!/usr/bin/env python3
"""Rate-limited, resumable NSE download with ZIP/schema validation and hash logs.

All request starts share a one-second limiter, including concurrent I/O workers.
HTTP 404 means unavailable, not a verified exchange holiday. TLS stays verified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from indialab.data import bhavcopy, nse

LOCK = threading.Lock()
NEXT_START = 0.0


def rate_limit():
    global NEXT_START
    with LOCK:
        now = time.monotonic()
        reserved = max(now, NEXT_START)
        NEXT_START = reserved + 1.0
    delay = reserved - now
    if delay > 0:
        time.sleep(delay)


def fetch(url, dest, expected_date=None):
    rate_limit()
    req = urllib.request.Request(url, headers={'User-Agent': nse.USER_AGENT})
    status, body, error = -1, b'', ''
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            status = response.status
            body = response.read()
        if status == 200:
            if dest.suffix == '.zip':
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    if z.testzip() is not None:
                        raise ValueError('ZIP CRC failure')
                    members = [name for name in z.namelist() if name.lower().endswith('.csv')]
                    if not members:
                        raise ValueError('Archive has no CSV members')
                    if expected_date is not None:
                        frame = bhavcopy.read_csv_bytes(z.read(members[0]))
                        if frame.empty or set(frame.date.dt.date) != {expected_date}:
                            raise ValueError('Bhavcopy date/schema differs from requested session')
            elif b'<html' in body[:500].lower():
                raise ValueError('HTML response for a reference CSV')
            dest.parent.mkdir(parents=True, exist_ok=True)
            temp = dest.with_suffix(dest.suffix + '.part')
            temp.write_bytes(body)
            temp.replace(dest)
    except urllib.error.HTTPError as exc:
        status, error = exc.code, str(exc)
    except Exception as exc:
        status, error = -1, f'{type(exc).__name__}: {exc}'
    return {'url': url, 'status': status, 'bytes': len(body),
            'sha256': hashlib.sha256(body).hexdigest() if status == 200 else '',
            'fetched_at': datetime.now(timezone.utc).isoformat(), 'error': error}


def append(path, record, fields):
    new = not path.exists()
    with path.open('a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        if new:
            writer.writeheader()
        writer.writerow(record)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--start', type=date.fromisoformat, default=date(2016, 1, 1))
    ap.add_argument('--end', type=date.fromisoformat, default=date(2026, 10, 13))
    ap.add_argument('--raw', type=Path, default=ROOT / 'data/raw')
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--weekdays-only', action='store_true', help='Diagnostic shortcut; omits unexpected weekend sessions')
    args = ap.parse_args()
    raw = args.raw.resolve()
    raw.mkdir(parents=True, exist_ok=True)
    fields = ['key', 'url', 'status', 'bytes', 'sha256', 'fetched_at']
    attempts = raw / 'download_attempts.csv'
    counts = {'ok': 0, 'unavailable_404': 0, 'errors': 0, 'cached': 0}
    for name, rel in nse.REFERENCE_FILES.items():
        result = fetch(nse.BASE + rel, raw / 'reference' / name)
        result['key'] = f'reference:{name}'
        append(attempts, result, fields + ['error'])
        print(result['key'], result['status'], result['bytes'], flush=True)
    done = nse.read_manifest(raw / 'manifest.csv')
    jobs = []
    candidates = nse.candidate_dates(args.start, args.end) if args.weekdays_only else [
        args.start + timedelta(days=i) for i in range((args.end-args.start).days+1)]
    for d in candidates:
        dest = raw / 'bhavcopy' / str(d.year) / Path(nse.bhavcopy_url(d)).name
        entry = done.get(d.isoformat())
        if entry and int(entry['status']) == 200 and dest.exists():
            if hashlib.sha256(dest.read_bytes()).hexdigest() == entry['sha256']:
                counts['cached'] += 1
                continue
        # An already checked 404 is resumable but remains an unverified absence.
        if entry and int(entry['status']) == 404:
            counts['cached'] += 1
            continue
        jobs.append((d, dest))
    start = time.monotonic()
    print(f'Bhavcopy candidates remaining: {len(jobs)}; cached records: {counts["cached"]}', flush=True)
    consecutive = 0
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
        pending = {}
        iterator = iter(jobs)
        for _ in range(max(1, min(args.workers, 4))):
            item = next(iterator, None)
            if item:
                d, dest = item
                pending[pool.submit(fetch, nse.bhavcopy_url(d), dest, d)] = d
        while pending:
            future = next(as_completed(pending))
            d = pending.pop(future)
            result = future.result()
            result['key'] = d.isoformat()
            append(attempts, result, fields + ['error'])
            if result['status'] in (200, 404):
                append(raw / 'manifest.csv', result, fields)
                counts['ok' if result['status'] == 200 else 'unavailable_404'] += 1
                consecutive = 0
            else:
                counts['errors'] += 1
                consecutive += 1
                print('Download error:', d, result['status'], result['error'], flush=True)
            if consecutive >= 5:
                for job in pending:
                    job.cancel()
                raise RuntimeError('Five consecutive failures; stopped rather than repeatedly requesting a blocked service')
            item = next(iterator, None)
            if item:
                nxt, dest = item
                pending[pool.submit(fetch, nse.bhavcopy_url(nxt), dest, nxt)] = nxt
            completed = counts['ok'] + counts['unavailable_404'] + counts['errors']
            if completed % 100 == 0 or not pending:
                print(f'{d}: {completed}/{len(jobs)}; {counts}; elapsed {time.monotonic()-start:.0f}s', flush=True)
                (raw / 'download_progress.json').write_text(json.dumps({'phase': 'bhavcopy', 'completed': completed,
                     'candidates': len(jobs), 'counts': counts, 'last_date': str(d)}, indent=2))
    # One weekly PR archive covers corporate actions around that week. Alternative
    # weekdays are used only when the Wednesday file is unavailable.
    done_pr = nse.read_manifest(raw / 'manifest_pr.csv')
    wk = args.start - timedelta(days=args.start.weekday()) + timedelta(days=2)
    pr_ok, pr_missing, pr_errors, consecutive_pr = 0, 0, 0, 0
    while wk <= args.end:
        key = f'week-{wk.isoformat()}'
        if key not in done_pr:
            got = False
            for d in (wk, wk + timedelta(days=1), wk - timedelta(days=1), wk + timedelta(days=2)):
                dest = raw / 'pr' / str(d.year) / f'PR{d:%d%m%y}.zip'
                result = fetch(nse.pr_url(d), dest)
                result['key'] = key
                append(attempts, result, fields + ['error'])
                if result['status'] == 200:
                    append(raw / 'manifest_pr.csv', result, fields)
                    pr_ok += 1
                    got = True
                    consecutive_pr = 0
                    break
                if result['status'] != 404:
                    pr_errors += 1
                    consecutive_pr += 1
                    print('PR download error:', key, result['status'], result['error'], flush=True)
                    if consecutive_pr >= 5:
                        raise RuntimeError('Repeated PR access errors; stopped. Successful bhavcopies are retained.')
                else:
                    consecutive_pr = 0
            if not got:
                pr_missing += 1
        if (pr_ok + pr_missing) % 50 == 0 and (pr_ok + pr_missing):
            print(f'PR through {wk}: downloaded {pr_ok}, unavailable weeks {pr_missing}', flush=True)
        wk += timedelta(days=7)
    summary = {'start': str(args.start), 'end': str(args.end), 'counts': counts,
               'pr_downloaded': pr_ok, 'pr_unavailable_weeks': pr_missing, 'pr_errors': pr_errors,
               'note': '404 dates require exchange-calendar reconciliation; they are not assumed holidays.'}
    (raw / 'download_summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    return 1 if counts['errors'] or pr_errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
