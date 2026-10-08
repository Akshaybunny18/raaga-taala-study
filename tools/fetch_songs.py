#!/usr/bin/env python3
"""Song worker for the Raaga & Taala study.

Polls the Apps Script backend for participants' songs that haven't been fetched
yet, downloads each one (yt-dlp for YouTube, spotDL for Spotify), loudness-matches
it to the white noise (-18 LUFS), and uploads it to the study's Google Drive folder.
The website unlocks a song as soon as it's marked ready.

Setup:  pip install yt-dlp spotdl   (ffmpeg must be on PATH)
Usage:  python tools/fetch_songs.py --api <APPS_SCRIPT_URL> --key <WORKER_KEY> [--once] [--workers N]
        (or set STUDY_API_URL / STUDY_WORKER_KEY environment variables)

Keep it running (without --once) whenever participants may be doing the study.
"""
import argparse, base64, io, json, os, queue, re, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

TARGET_LUFS = -18          # must match NOISE_AMP / TARGET_LUFS in session.html
MAX_SECONDS = 480          # songs are looped in the browser, 8 minutes is plenty
POLL_SECONDS = 15
WORKERS = 3                # songs processed at once (download → convert → upload each)
YOUTUBE_RE = re.compile(r'^https://(www\.|m\.|music\.)?(youtube\.com/(watch\?|shorts/)|youtu\.be/)', re.I)
SPOTIFY_RE = re.compile(r'^https://open\.spotify\.com/(intl-[a-z-]+/)?track/', re.I)
AUDIO_EXTS = {'.mp3', '.m4a', '.opus', '.webm', '.ogg', '.wav', '.flac', '.aac'}


def api_get(api, params):
    with urllib.request.urlopen(api + '?' + urllib.parse.urlencode(params), timeout=60) as r:
        return json.load(r)


PRINT_LOCK = threading.Lock()
LIVE_LINE = False  # set in main(): rewrite one line in place only when songs run one at a time on a terminal


def say(line, stamp=False):
    with PRINT_LOCK:
        print(f'[{time.strftime("%H:%M:%S")}] {line}' if stamp else line, flush=True)


class Status:
    """Progress for one song: a single line rewritten in place (one song at a time on a terminal),
    otherwise one line per stage."""

    def __init__(self, label):
        self.label, self.t0, self.tty, self.stage, self.shown = label, time.time(), LIVE_LINE, None, 0.0
        self.start = time.strftime('%H:%M:%S')

    def elapsed(self):
        s = int(time.time() - self.t0)
        return f'{s // 60}:{s % 60:02d}'

    def set(self, stage, detail='', force=False):
        if self.tty:
            if stage == self.stage and not force and time.time() - self.shown < 0.2:
                return  # throttle redraws
            sys.stdout.write(f'\r\033[K[{self.start}] {self.label}  {stage} {detail}'.rstrip())
            sys.stdout.flush()
        elif stage != self.stage:
            say(f'{self.label}  {stage}', stamp=True)
        self.stage, self.shown = stage, time.time()

    def done(self, text):
        line = f'{self.label}  {text} ({self.elapsed()})'
        if self.tty:
            sys.stdout.write(f'\r\033[K[{self.start}] {line}\n')
            sys.stdout.flush()
        else:
            say(line, stamp=True)


class _UploadBody(io.BytesIO):
    """Request body that reports how much has been sent."""

    def __init__(self, data, on_progress):
        super().__init__(data)
        self.total, self.on_progress = len(data), on_progress

    def read(self, size=-1):
        chunk = super().read(size)
        self.on_progress(self.tell(), self.total)
        return chunk


def api_post(api, body, on_progress=None):
    # Apps Script answers POST with a redirect; urllib follows it as a GET, which is what it expects
    data = json.dumps(body).encode()
    req = urllib.request.Request(api, data=_UploadBody(data, on_progress) if on_progress else data,
                                 headers={'Content-Type': 'text/plain', 'Content-Length': str(len(data))})
    with urllib.request.urlopen(req, timeout=420) as r:   # uploads of long songs through Apps Script can be slow
        return json.load(r)


def run(cmd, on_tick=None):
    """Runs a command; on_tick(line_or_None) is called for each output line and about twice a second."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    lines, q = [], queue.Queue()
    threading.Thread(target=lambda: [q.put(l) for l in p.stdout] + [q.put(None)], daemon=True).start()
    while True:
        try:
            line = q.get(timeout=0.5)
        except queue.Empty:
            line = ''
        if line is None:
            break
        if line:
            lines.append(line.strip())
            del lines[:-50]
        if on_tick:
            on_tick(line.strip() or None)
    if p.wait() != 0:
        errs = [l for l in lines if l.startswith('ERROR')] or [l for l in lines if l]
        raise RuntimeError(errs[-1] if errs else 'command failed: ' + ' '.join(cmd[:4]))


DL_PCT = re.compile(r'\[download\]\s+([\d.]+)%')


def download(link, workdir, status):
    if YOUTUBE_RE.match(link):
        pct = ['']
        def tick(line):
            m = DL_PCT.search(line or '')
            if m:
                pct[0] = f'{float(m.group(1)):.0f}%'
            status.set('⬇ downloading', pct[0] or status.elapsed())
        run([sys.executable, '-m', 'yt_dlp', '--no-playlist', '--newline', '-x', '--audio-format', 'mp3',
             '-o', str(workdir / '%(id)s.%(ext)s'), link], tick)
    elif SPOTIFY_RE.match(link):
        run([sys.executable, '-m', 'spotdl', 'download', link, '--format', 'mp3',
             '--output', str(workdir / '{title}.{output-ext}')],
            lambda _: status.set('⬇ downloading', status.elapsed()))
    else:
        raise RuntimeError('not a YouTube video or Spotify track link')
    files = [f for f in workdir.iterdir() if f.suffix.lower() in AUDIO_EXTS]
    if not files:
        raise RuntimeError('download produced no audio file')
    return max(files, key=lambda f: f.stat().st_size)


def normalize(src, dst):
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src), '-t', str(MAX_SECONDS),
         '-af', f'loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11', '-ar', '44100', '-ac', '2', '-b:a', '96k', str(dst)])


def process(api, key, song):
    roll, idx, link = song['roll'], song['idx'], song['link']
    status = Status(f'{roll} song {idx}')
    status.set('⏳ starting')
    api_post(api, {'action': 'setSongStatus', 'key': key, 'roll': roll, 'idx': idx, 'status': 'downloading'})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw = download(link, tmp, status)
            status.set('🎚 converting')
            out = tmp / 'normalized.mp3'
            normalize(raw, out)
            b64 = base64.b64encode(out.read_bytes()).decode()
        def sent(done, total):
            status.set('⬆ uploading', f'{done * 100 // total}%' if done < total else '100% · saving to Drive…', force=done >= total)
        res = api_post(api, {'action': 'uploadAudio', 'key': key, 'roll': roll, 'idx': idx, 'b64': b64}, sent)
        if not res.get('ok'):
            raise RuntimeError('upload failed: ' + str(res.get('error')))
        status.done('✓ ready')
    except Exception as e:
        status.done(f'✗ failed: {e} — link: {link}')
        api_post(api, {'action': 'setSongStatus', 'key': key, 'roll': roll, 'idx': idx,
                       'status': 'failed', 'error': str(e)[:300]})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--api', default=os.environ.get('STUDY_API_URL'), help='Apps Script web-app URL (/exec)')
    ap.add_argument('--key', default=os.environ.get('STUDY_WORKER_KEY'), help='WORKER_KEY script property')
    ap.add_argument('--once', action='store_true', help='process pending songs once and exit')
    ap.add_argument('--workers', type=int, default=WORKERS, help=f'songs processed at once (default {WORKERS})')
    a = ap.parse_args()
    if not a.api or not a.key:
        ap.error('--api and --key are required (or set STUDY_API_URL / STUDY_WORKER_KEY)')
    global LIVE_LINE
    LIVE_LINE = a.workers == 1 and sys.stdout.isatty()

    say(f'Song worker running ({a.workers} at a time) — Ctrl+C to stop.')
    pool = ThreadPoolExecutor(max_workers=max(1, a.workers))
    lock = threading.Lock()
    inflight, finished_at, futures = set(), {}, []   # (roll, idx) being processed / when each last finished
    def finished(key):
        with lock:
            inflight.discard(key)
            finished_at[key] = time.time()
    retry_failed = True  # each (re)start retries failed songs once; later polls only take new ones
    while True:
        try:
            params = {'action': 'pendingSongs', 'key': a.key}
            if retry_failed:
                params['retryFailed'] = '1'
            asked = time.time()
            res = api_get(a.api, params)
            if not res.get('ok'):
                sys.exit('Server refused: ' + str(res.get('error')))
            retry_failed = False
            with lock:   # skip songs in progress, or finished after this list was requested (it may be stale)
                new = [s for s in res['songs'] if (s['roll'], s['idx']) not in inflight
                       and finished_at.get((s['roll'], s['idx']), 0) < asked]
                inflight.update((s['roll'], s['idx']) for s in new)
            if new:
                say(f'{len(new)} song(s) to fetch')
            for song in new:
                fut = pool.submit(process, a.api, a.key, song)
                fut.add_done_callback(lambda _f, k=(song['roll'], song['idx']): finished(k))
                futures.append(fut)
        except (OSError, ValueError) as e:  # network hiccup / bad response — try again next poll
            say(f'    ! {e}')
        if a.once:
            wait(futures)
            break
        futures = [f for f in futures if not f.done()]
        time.sleep(POLL_SECONDS)
    pool.shutdown()


if __name__ == '__main__':
    main()
