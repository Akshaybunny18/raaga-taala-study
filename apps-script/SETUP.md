# Phase 1 backend setup

The site is static (GitHub Pages). All data goes to one Google Sheet through a small
Apps Script web app, and participants' songs are fetched by a Python worker that runs
on a team laptop.

```
intake.html ──register──▶ Apps Script ──▶ Sheet: Participants ◀──poll / upload── tools/fetch_songs.py
session.html ─profile/submit─▶ Apps Script ──▶ Sheet: Results                 (yt-dlp / spotDL / ffmpeg)
                                           └─▶ Drive folder: song MP3s
```

## 1. Google Sheet + Apps Script (once)

1. Create a new Google Sheet (e.g. "Raaga Taala Study — Data") with the team account.
2. **Extensions → Apps Script**. Replace the contents of `Code.gs` with
   [`Code.gs`](Code.gs) from this folder. Save.
3. **Project Settings (⚙) → Script properties → Add property**:
   `WORKER_KEY` = any long random string (e.g. from `python -c "import secrets;print(secrets.token_urlsafe(24))"`).
   Keep it private — it lets the worker write song files.
4. **Deploy → New deployment → ⚙ Web app**:
   - Execute as: **Me**
   - Who has access: **Anyone**
   - Deploy, authorise the requested permissions (Sheets + Drive), and copy the URL ending in `/exec`.
5. Paste that URL into `API_URL` at the top of [`../api.js`](../api.js) and push.

The `Participants` and `Results` tabs and the `raaga-taala-study-audio` Drive folder
are created automatically on first use.

**After editing `Code.gs`** you must publish a new version: Deploy → Manage deployments →
✏️ → Version: *New version* → Deploy. The `/exec` URL stays the same.

## 2. Song worker (team laptop, during study sessions)

Needs Python 3.9+, ffmpeg on PATH, and a normal home/hostel internet connection
(YouTube blocks most cloud servers).

```bash
pip install yt-dlp spotdl
python tools/fetch_songs.py --api "https://script.google.com/macros/s/…/exec" --key "<WORKER_KEY>"
```

Leave it running while participants may be taking the study. Every 15 s it picks up
new songs, downloads them, normalises loudness to −18 LUFS (the same level as the
in-browser white noise), trims to 8 min, and uploads to Drive. A participant's songs
are usually ready within 1–2 minutes of registering — long before the break where
they're needed.

If a song fails (removed video, region block, wrong link) its status becomes `failed`
and the error is written to `songN_error`. The participant can upload their own audio
file for that song during the break instead. To retry a song, set its `songN_status`
back to `pending` in the sheet.

Keep yt-dlp current — YouTube changes often: `pip install -U yt-dlp spotdl`.

## 3. Testing without the backend

- With `API_URL` empty, the whole site runs in **demo mode** (data stays in the browser).
- With `API_URL` set, the roll number **`DEMO`** still uses demo mode — handy for
  checking the flow without polluting the sheet. In demo mode songs take 45 s to
  "download", and demo song 3 always fails so you can try the upload fallback.
- Reset demo data: in the browser console run `localStorage.removeItem('demo_db')`.

## 4. Reading the data

**Participants** — one row per registration: intake answers, the 3 songs
(`songN_title/artist/link/status/audio/error`) and the two assigned tests
(`p1_task_round1`, `p1_task_round2`).

**Results** — one row per saved step, identified by `step_key`:

| step_key | what |
|---|---|
| `start` | starting mood check: `valence`, `arousal`, `sleepiness`, `stress`, `sleep_hours`, `caffeine` |
| `r1A` / `r2A` | white-noise run: `task`, `summary`, `res_*` task scores, `duration_s`, `blur_count` (times the window lost focus), `audio_errors` |
| `r1quick` / `r2quick` | quick mood after the white-noise run: `valence`, `arousal` |
| `r1B` / `r2B` | song run: as above + `song_idx`, `song_title`, `song_source` (`download` / `upload`), `upload_lufs`, `upload_gain` |
| `r1end` / `r2end` | end of round — Part A: `valence`, `arousal`, `sleepiness`, `stress`; Part B: `conc_noise`, `conc_song`, `distract_noise`, `distract_song` |
| `final` | Part C: `a1`, `a2`, `b`, `c`, `c_why`, `d`, `e` |

Mood scales: valence/arousal/sleepiness 1–9, stress 1–5, Part B ratings 1–5.

Delete the Drive audio folder once the study is over.
