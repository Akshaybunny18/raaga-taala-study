#!/usr/bin/env python3
"""Uploads participants' backup JSONs (the "Download backup" file) to the study's Results sheet.

Each backup is the list of saves that couldn't reach the server. They go through the normal
`submit` action, which skips any step (roll + phase + step_key) the sheet already has, so
importing a file twice, or a file whose saves were later re-sent by the browser, adds nothing twice.

If a participant did a step twice (they reloaded after a save failed, so the site asked again),
the earliest attempt (by client_ts) is the one sent; the later ones are listed and skipped.

Usage:  python tools/import_backups.py backups/*.json            # dry run: shows what would be sent
        python tools/import_backups.py backups/*.json --send     # actually writes to the sheet
        (the web-app URL is read from api.js; override with --api <URL>)
"""
import argparse, json, re, sys, urllib.request
from collections import defaultdict
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent


def api_url():
    m = re.search(r"const API_URL = '([^']*)'", (SITE / 'api.js').read_text(encoding='utf-8'))
    return m.group(1) if m else ''


def api_post(api, body):
    # Apps Script answers POST with a redirect; urllib follows it as a GET, which is what it expects
    data = json.dumps(body).encode()
    req = urllib.request.Request(api, data=data, headers={'Content-Type': 'text/plain'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def load(paths):
    recs = []
    for p in paths:
        try:
            data = json.loads(Path(p).read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            print(f'⚠ skipping {p}: {e}')
            continue
        for r in data if isinstance(data, list) else [data]:
            if not isinstance(r, dict) or not r.get('roll') or not r.get('step_key') or r.get('phase') in (None, ''):
                print(f'⚠ skipping a record in {p} without roll / phase / step_key')
                continue
            if str(r['roll']).strip().upper() == 'DEMO':
                continue
            recs.append((r, Path(p).name))
    return recs


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--send', action='store_true', help='write to the sheet (default: dry run)')
    ap.add_argument('--api', default=api_url())
    a = ap.parse_args()

    groups = defaultdict(list)
    for r, f in load(a.files):
        groups[(str(r['roll']).strip().upper(), str(r['phase']), r['step_key'])].append((r, f))
    if not groups:
        sys.exit('No records found.')

    todo = []
    for (roll, phase, key), items in sorted(groups.items()):
        items.sort(key=lambda x: x[0].get('client_ts', ''))
        first = items[0][0]
        # the same save can sit in two files (e.g. two backups from one person); only real re-dos matter
        redos = [x for x in items[1:] if x[0].get('client_ts') != first.get('client_ts')]
        if redos:
            print(f'↺ {roll} phase {phase} {key}: done {1 + len(redos)}× — keeping the first '
                  f'({first.get("client_ts", "?")}), skipping {", ".join(x[0].get("client_ts", "?") for x in redos)}')
        todo.append(first)
    todo.sort(key=lambda r: (str(r['roll']), r.get('client_ts', '')))

    rolls = sorted({str(r['roll']).strip().upper() for r in todo})
    print(f'{len(todo)} step(s) from {len(rolls)} participant(s): {", ".join(rolls)}')
    if not a.send:
        for r in todo:
            print(f'  {r["roll"]}  phase {r["phase"]}  {r["step_key"]:<10} {r.get("client_ts", "")}')
        print('Dry run — nothing sent. Add --send to write these to the sheet.')
        return
    if not a.api:
        sys.exit('No web-app URL: set API_URL in api.js or pass --api.')

    added = dup = failed = 0
    for r in todo:
        try:
            out = api_post(a.api, dict(r, action='submit'))
        except Exception as e:  # network / server error: report and keep going
            out = {'ok': False, 'error': str(e)}
        if out.get('ok') and out.get('duplicate'):
            dup += 1
        elif out.get('ok'):
            added += 1
            print(f'  + {r["roll"]} phase {r["phase"]} {r["step_key"]}')
        else:
            failed += 1
            print(f'  ✗ {r["roll"]} phase {r["phase"]} {r["step_key"]}: {out.get("error")}')
    print(f'Done: {added} added, {dup} already in the sheet, {failed} failed.')
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
