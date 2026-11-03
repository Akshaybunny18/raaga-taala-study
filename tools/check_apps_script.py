#!/usr/bin/env python3
"""Checks the Apps Script files before they're pasted into the editor.

All .gs files in an Apps Script project share one global scope: if two files define the
same top-level name, one silently replaces the other (a duplicate ensureCols_ in Grader.gs
broke every save on 3 Nov 2026). This lists every top-level function / const / let / var
defined in more than one file, and exits with an error if there is any.

Usage:  python tools/check_apps_script.py
"""
import re, sys
from collections import defaultdict
from pathlib import Path

GS_DIR = Path(__file__).resolve().parent.parent / 'apps-script'
TOP_LEVEL = re.compile(r'^(?:async\s+)?(?:function\s+([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*))', re.M)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    defs = defaultdict(list)
    files = sorted(GS_DIR.glob('*.gs'))
    for f in files:
        for m in TOP_LEVEL.finditer(f.read_text(encoding='utf-8')):
            line = f.read_text(encoding='utf-8')[:m.start()].count('\n') + 1
            defs[m.group(1) or m.group(2)].append(f'{f.name}:{line}')
    clashes = {k: v for k, v in defs.items() if len({p.split(':')[0] for p in v}) > 1}
    if not clashes:
        print(f'OK: {len(defs)} top-level names across {", ".join(f.name for f in files)}, no clashes.')
        return
    print('Names defined in more than one .gs file (one would silently replace the other):')
    for k, v in sorted(clashes.items()):
        print(f'  {k}: {", ".join(v)}')
    sys.exit(1)


if __name__ == '__main__':
    main()
