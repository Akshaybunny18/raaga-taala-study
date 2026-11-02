# Backend setup (Phase 1 + Phase 2)

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

Leave it running while participants may be taking the study. It processes 3 songs at a time
(change with `--workers N`; `--workers 1` shows a single live progress line). Every 15 s it picks up
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
the essay texts identical to `ESSAYS` in `session.html` and `phase2-session.html`; the last three essays
exist only in Phase 2).

For the report: hand-grade ~20 summaries with the same key-idea lists and compare them
with `score_recall_pct` to show the automatic grading agrees with a human.

## 6. Phase 2

`phase2-session.html` uses the same Sheet and web app. Before Phase 2 opens:
1. Paste the current **`Code.gs`** into the Apps Script editor and **redeploy** (new version, §1).
   If saves fail with `TypeError: Cannot read properties of undefined (reading 'map')` (found on 3 Nov:
   reads worked, every save failed), the deployed code is out of sync with this file. Paste this file
   again in full and deploy a new version. Unsent saves wait in each participant's browser and are re-sent
   on their next save.
2. Paste the current **`Grader.gs`** too (it has rubrics for the 3 Phase-2-only essays). The grading
   trigger runs the saved code, so no redeploy is needed for it, but it must be saved.

No song worker is needed: the tracks are fixed and served with the site.

**Who can take it:** only roll numbers whose Phase 1 `final` step is saved. Others are sent back
to Phase 1.

**Assignment** (written once to Participants on first login, as `p2_task` and `p2_order`):
- `p2_task`: one of the 6 tests, whichever has been used least in Phase 2 so far (ties at random),
  independent of the participant's Phase 1 tests. Dot Memory counts double (`P2_TASK_WEIGHT` in
  `Code.gs`), so it gets about half as many people: it was the least sensitive test in Phase 1.
- `p2_order`: a row of a balanced 4×4 Latin square (each condition once in every round position, and
  each condition directly after every other once), the one used least among people with the same test:

  | p2_order | round 1 | round 2 | round 3 | round 4 |
  |---|---|---|---|---|
  | 1 | white_noise | western | indian_instr | indian_vocal |
  | 2 | western | indian_vocal | white_noise | indian_instr |
  | 3 | indian_vocal | indian_instr | western | white_noise |
  | 4 | indian_instr | white_noise | indian_vocal | western |

**Tracks** (chosen and measured in `Project/phase2/phase2_track_selection.md`):

| file | piece | segment used |
|---|---|---|
| `audio/phase2/indian_instr.mp3` | *Raghunayaka* (raga Hamsadhwani, Adi tala, Tyagaraja) — Ambi Subramaniam, violin | 0:02–4:02 |
| `audio/phase2/indian_vocal.mp3` | the same kriti — Sanjay Subrahmanyan, vocal | 0:18–4:18 |
| `audio/phase2/western.mp3` | J.S. Bach, Violin Concerto in E major BWV 1042, I. Allegro — Hilary Hahn | 0:01–4:01 |

Each is exactly 4:00 with a 1 s fade-in and 3 s fade-out, made with
`python tools/prep_phase2_audio.py western=<file> indian_vocal=<file> indian_instr=<file>` from lossless
cuts of the originals (all measure −18.4 LUFS, matched to the white noise). A round needs at most
30 s + 3 min (Reverse Typing), so 4 min never loops. To swap a track, cut the new segment to 4:00, run
the tool, and update its `title` in `CONDS` at the top of `phase2-session.html`.
These are commercial recordings: they are used here for study playback only.

**Flow:** audio check → instructions → `start` mood check → `practice` (3 untimed, silent puzzles for
Symmetry / Rotation / Dot Memory / Number Sequence, 1 line for Reverse Typing, none for Reading) →
4 rounds, each: 30 s of the round's audio with nothing to do → the test, audio continuing (Reading:
audio stops for the summary, as in Phase 1) → mood form → **60 s silent break** before rounds 2–4
(`BREAK_SECS`; not a saved step, so a reload during it shows it again) → `final`. About 25–30 minutes.

**Test changes from Phase 1** (Phase 2 only; `session.html` is unchanged):
- **Number Sequence:** 7 circles (0–6) instead of 10 (`NS_COUNT`), because most Phase 1 white-noise
  runs had no fully correct layout. Runs record `res_circles` = 7.
- **Symmetry / Rotated Image:** presses within 350 ms of a new shape are ignored (`res_fast_presses`);
  after 10 answers, accuracy below 60% shows an "accuracy counts" reminder at most every 20 s
  (`res_guess_warnings`). Practice puzzles are not affected.
- **Reverse Typing:** nothing can be erased or edited, only added at the end of the line. Backspace,
  Delete, cut, paste, drop, undo and clicks/arrows into the middle are blocked; blocked attempts are
  counted in `res_erase_blocked`. Some Android keyboards don't let a page stop a backspace inside a
  word that is still being composed (underlined); committed text can never be removed.
- **Reading:** 8 essays (the 5 from Phase 1 + 3 new ones). Each round shows a different essay, never
  one of the two this roll number could have read in Phase 1 (`phase2Essay`).

**Results** rows have `phase` = 2 and `order_id` (= `p2_order`):

| step_key | what |
|---|---|
| `start` | as Phase 1: `valence`, `arousal`, `sleepiness`, `stress`, `sleep_hours`, `caffeine` |
| `practice` | `task`, `practice_n`, `practice_correct` |
| `r1run` … `r4run` | `round` (1–4 = position), `condition`, `track_title`, `order`, `task`, `res_*` scores (same scoring as Phase 1), `listen_s` (30), `duration_s` (test only), `blur_count`, `run_restarts`, `audio_phase` |
| `r1mood` … `r4mood` | `round`, `condition`; Part A: `valence`, `arousal` (1–9); Part B (1–5): `concentration`, `distraction`, `liking`, and, after music rounds only (not white noise): `familiar_feel` (how familiar it felt), `indian_feel` (how Indian it felt). Sleepiness and stress are only in `start` |
| `final` | `rank_white_noise`, `rank_western`, `rank_indian_vocal`, `rank_indian_instr` (1 = best for focus … 4), `best` (a condition or `no_difference`), `best_why`, `vocal`, `vs_phase1`, `usual` |

Reading runs are graded by `Grader.gs` exactly like Phase 1 (each round gets a different essay).

## 7. Mini phase (track listening check)

`mini-phase.html` (not linked from the homepage; share the URL directly) asks people to listen to 7
thirty-second clips, `audio/mini/clip_1.mp3` … `clip_7.mp3`, in a random order, and rate each one 1–7 for
pleasantness, energy, familiarity and how distracting it would be while studying, plus whether they could
make out the words. Questions unlock after 25 s of actual listening. The clips are the 0:30–1:00 excerpts
of each chosen track and its backup; which clip is which is kept out of this public site, in
`Project/phase2/audio/mini_phase_clips/CLIP_KEY_do_not_show_respondents.csv`.

Results go to the same Results tab with **`phase` = 0**: one row per clip (`step_key` `mini_clip1` …
`mini_clip7`, with `clip`, `position`, `listened_s`, `pleasant`, `energy`, `familiar`, `distracting`, `words`)
and one `mini_final` row (`order`, `comment`). Filter by `phase` so these never mix with Phase 1/2 data.
Roll number `DEMO` keeps everything in the browser for testing.
