#!/usr/bin/env python3
"""Prepares the fixed Phase 2 tracks for the Raaga & Taala study.

Loudness-matches each track to the white noise (-18 LUFS, two-pass ffmpeg loudnorm) and
writes it to audio/phase2/<condition>.mp3, which phase2-session.html plays.
After running it, set each track's title in CONDS at the top of phase2-session.html.

Setup:  ffmpeg must be on PATH
Usage:  python tools/prep_phase2_audio.py western=path/to/piece.flac indian_vocal=... indian_instr=...
        (any subset; existing files for the other conditions are left alone)
"""
import json, re, subprocess, sys
from pathlib import Path

TARGET_LUFS = -18          # must match NOISE_AMP in phase2-session.html
CONDITIONS = ('western', 'indian_vocal', 'indian_instr')
MIN_SECONDS = 240          # 30 s listening + the longest test (Reading, ~3.5 min); shorter tracks loop
OUT_DIR = Path(__file__).resolve().parent.parent / 'audio' / 'phase2'


def ffmpeg(*args):
    return subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-y', *args], capture_output=True, text=True, check=True).stderr


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def prepare(cond, src):
    base = f'loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11'
    # Pass 1: measure. Pass 2: apply the measured values with linear gain (no dynamic squashing when possible).
    m = json.loads(re.search(r'\{[^{}]*"input_i"[^{}]*\}', ffmpeg('-i', str(src), '-af', base + ':print_format=json', '-f', 'null', '-')).group(0))
    filt = (f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    dst = OUT_DIR / f'{cond}.mp3'
    ffmpeg('-i', str(src), '-af', filt, '-ar', '44100', '-ac', '2', '-b:a', '128k', str(dst))
    secs = duration(dst)
    print(f'{cond}: {src.name} → {dst.relative_to(OUT_DIR.parent.parent)}  (was {float(m["input_i"]):.1f} LUFS, {secs / 60:.1f} min)')
    if secs < MIN_SECONDS:
        print(f'  ⚠ shorter than {MIN_SECONDS // 60} min — it will loop during the round')


def main():
    jobs = []
    for arg in sys.argv[1:]:
        cond, _, path = arg.partition('=')
        if cond not in CONDITIONS or not path:
            sys.exit(f'bad argument {arg!r}: use <condition>=<file>, condition one of {", ".join(CONDITIONS)}')
        if not Path(path).is_file():
            sys.exit(f'no such file: {path}')
        jobs.append((cond, Path(path)))
    if not jobs:
        sys.exit(__doc__)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for cond, src in jobs:
        prepare(cond, src)


if __name__ == '__main__':
    main()
