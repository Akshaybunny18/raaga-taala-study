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
| `r1A` / `r2A` | white-noise run: `task`, `summary`, `res_*` task scores, `duration_s`, `blur_count` (times the window lost focus), `audio_errors`, `audio_phase` (`reading_only` for Reading + Summary: audio plays while reading and stops for the summary; `whole_task` otherwise) |
| `r1quick` / `r2quick` | quick mood after the white-noise run: `valence`, `arousal` |
| `r1B` / `r2B` | song run: as above + `song_idx`, `song_title`, `song_source` (`download` / `upload`), `upload_lufs`, `upload_gain` |
| `r1end` / `r2end` | end of round — Part A: `valence`, `arousal`, `sleepiness`, `stress`; Part B: `conc_noise`, `conc_song`, `distract_noise`, `distract_song` |
| `final` | Part C: `a1`, `a2`, `b`, `c`, `c_why`, `d`, `e` |

Mood scales: valence/arousal/sleepiness 1–9, stress 1–5, Part B ratings 1–5.

Every Results row has `device` (phone / tablet / laptop, chosen by the participant — data only).

Test scores (`res_score` on run rows):
- **Symmetry, Rotation, Dot Memory, Number Sequence** (2 min, as many puzzles as possible):
  each fully correct puzzle +1; a puzzle with mistakes scores a penalty by the share wrong —
  under 25% → −0.25, 25–75% → −0.5, over 75% → −1. Dot Memory: wrong share = (missed + extra) / 7;
  Number Sequence: wrong clicks / 10; Symmetry/Rotation: a wrong answer = −1.
  `res_unfinished` = the puzzle cut off at 2:00 (not scored).
- **Reverse Typing** (3 min of random 10-word lines): words typed correctly in reverse order;
  small typos count as correct (`res_typo` tallies them). `res_score_pct` = score / words attempted.

Reading runs also get `score_*` columns from the automatic grader (§5).

Delete the Drive audio folder once the study is over.

## 5. Automatic grading of reading summaries

[`Grader.gs`](Grader.gs) scores every *Reading + Summary* run against 10 key ideas
per essay, using a free open-source LLM on Groq. It runs every 10 minutes on Google's
servers — no laptop needed — and writes the scores into the same Results row.

1. Get a free API key at <https://console.groq.com/keys>.
2. In the Apps Script editor: **＋ (Add a file) → Script**, name it `Grader`, and paste
   the contents of `Grader.gs`. Save. (No redeploy needed — the web app is unaffected.)
3. **Project Settings → Script properties → Add property**: `GROQ_API_KEY` = your key.
   Optional: `GRADER_MODEL` = a model id (default `llama-3.3-70b-versatile`).
4. In the editor, pick a function from the dropdown and press **▶ Run**:
   - `testGrader` — grades a sample summary and logs the result (checks key + model).
     If the model has been retired, run `listGroqModels` and set `GRADER_MODEL`.
   - `setupGrader` — installs the 10-minute trigger. Run once; approve the permissions.

Scores (Results tab, reading rows only):

| column | meaning |
|---|---|
| `score_recall_pct` | main score: (recalled + ½ × partial) / 10 × 100 |
| `score_recalled`, `score_partial` | number of key ideas recalled / partly recalled |
| `score_errors` | statements contradicting the essay |
| `score_detail` | JSON: verdict per key idea + the error statements (for auditing) |
| `score_model`, `scored_at` | which model graded it, and when |

Empty summaries get 0 without calling the LLM. If Groq is down or rate-limited, the
remaining rows are simply picked up on the next run. To re-grade a row, clear its
`score_recall_pct` cell. To edit the key ideas, change `RUBRICS` in `Grader.gs` (keep
the essay texts identical to `ESSAYS` in `session.html`).

For the report: hand-grade ~20 summaries with the same key-idea lists and compare them
with `score_recall_pct` to show the automatic grading agrees with a human.
