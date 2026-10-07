#!/usr/bin/env python3
"""Song worker for the Raaga & Taala study.

Polls the Apps Script backend for participants' songs that haven't been fetched
yet, downloads each one (yt-dlp for YouTube, spotDL for Spotify), loudness-matches
it to the white noise (-18 LUFS), and uploads it to the study's Google Drive folder.
The website unlocks a song as soon as it's marked ready.

Setup:  pip install yt-dlp spotdl   (ffmpeg must be on PATH)
Usage:  python tools/fetch_songs.py --api <APPS_SCRIPT_URL> --key <WORKER_KEY> [--once]
        (or set STUDY_API_URL / STUDY_WORKER_KEY environment variables)

Keep it running (without --once) whenever participants may be doing the study.
"""
import argparse, base64, json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

TARGET_LUFS = -18          # must match NOISE_AMP / TARGET_LUFS in session.html
MAX_SECONDS = 480          # songs are looped in the browser, 8 minutes is plenty
POLL_SECONDS = 15
YOUTUBE_RE = re.compile(r'^https://(www\.|m\.|music\.)?(youtube\.com/(watch\?|shorts/)|youtu\.be/)', re.I)
SPOTIFY_RE = re.compile(r'^https://open\.spotify\.com/(intl-[a-z-]+/)?track/', re.I)
AUDIO_EXTS = {'.mp3', '.m4a', '.opus', '.webm', '.ogg', '.wav', '.flac', '.aac'}


def api_get(api, params):
    with urllib.request.urlopen(api + '?' + urllib.parse.urlencode(params), timeout=60) as r:
        return json.load(r)


def api_post(api, body):
    # Apps Script answers POST with a redirect; urllib follows it as a GET, which is what it expects
    req = urllib.request.Request(api, data=json.dumps(body).encode(), headers={'Content-Type': 'text/plain'})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        out = (p.stderr or p.stdout).strip()
        raise RuntimeError(out.splitlines()[-1] if out else 'command failed: ' + ' '.join(cmd[:4]))


def download(link, workdir):
    if YOUTUBE_RE.match(link):
        run([sys.executable, '-m', 'yt_dlp', '--no-playlist', '-x', '--audio-format', 'mp3',
             '-o', str(workdir / '%(id)s.%(ext)s'), link])
    elif SPOTIFY_RE.match(link):
        run([sys.executable, '-m', 'spotdl', 'download', link, '--format', 'mp3',
             '--output', str(workdir / '{title}.{output-ext}')])
    else:
        raise RuntimeError('not a YouTube video or Spotify track link')
    files = [f for f in workdir.iterdir() if f.suffix.lower() in AUDIO_EXTS]
    if not files:
        raise RuntimeError('download produced no audio file')
    return max(files, key=lambda f: f.stat().st_size)


def normalize(src, dst):
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src), '-t', str(MAX_SECONDS),
         '-af', f'loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11', '-ar', '44100', '-ac', '2', '-b:a', '128k', str(dst)])


def process(api, key, song):
    roll, idx, link = song['roll'], song['idx'], song['link']
    print(f'[{time.strftime("%H:%M:%S")}] {roll} song {idx}: {link}', flush=True)
    api_post(api, {'action': 'setSongStatus', 'key': key, 'roll': roll, 'idx': idx, 'status': 'downloading'})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw = download(link, tmp)
            out = tmp / 'normalized.mp3'
            normalize(raw, out)
            b64 = base64.b64encode(out.read_bytes()).decode()
        res = api_post(api, {'action': 'uploadAudio', 'key': key, 'roll': roll, 'idx': idx, 'b64': b64})
        if not res.get('ok'):
            raise RuntimeError('upload failed: ' + str(res.get('error')))
        print('    ✓ ready', flush=True)
    except Exception as e:
        print(f'    ✗ failed: {e}', flush=True)
        api_post(api, {'action': 'setSongStatus', 'key': key, 'roll': roll, 'idx': idx,
                       'status': 'failed', 'error': str(e)[:300]})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--api', default=os.environ.get('STUDY_API_URL'), help='Apps Script web-app URL (/exec)')
    ap.add_argument('--key', default=os.environ.get('STUDY_WORKER_KEY'), help='WORKER_KEY script property')
    ap.add_argument('--once', action='store_true', help='process pending songs once and exit')
    a = ap.parse_args()
    if not a.api or not a.key:
        ap.error('--api and --key are required (or set STUDY_API_URL / STUDY_WORKER_KEY)')

    print('Song worker running — Ctrl+C to stop.', flush=True)
    while True:
        try:
            res = api_get(a.api, {'action': 'pendingSongs', 'key': a.key})
            if not res.get('ok'):
                sys.exit('Server refused: ' + str(res.get('error')))
            for song in res['songs']:
                process(a.api, a.key, song)
        except (OSError, ValueError) as e:  # network hiccup / bad response — try again next poll
            print(f'    ! {e}', flush=True)
        if a.once:
            break
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
