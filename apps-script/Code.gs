// ===== Raaga & Taala Study — Google Apps Script backend =====
// Setup: see apps-script/SETUP.md.
//
// Tabs (created automatically on first use):
//   Participants — one row per intake registration + song download status.
//   Results      — one row per saved step (start mood, each run, quick mood,
//                  end-of-round form, end-of-phase form). `step_key` identifies it.
//
// Script properties (Project Settings → Script properties):
//   WORKER_KEY      — shared secret used by tools/fetch_songs.py (required)
//   AUDIO_FOLDER_ID — Drive folder for song files (auto-created if missing)

const P_SHEET = 'Participants';
const R_SHEET = 'Results';
const P_BASE  = ['roll','name','email','registered_at','p1_task_round1','p1_task_round2']
  .concat([1, 2, 3].reduce((a, i) => a.concat(['title','artist','link','status','audio','error'].map(f => 'song' + i + '_' + f)), []));
const R_BASE  = ['submitted_at','roll','phase','step_key','step','round','run','condition','task'];
const LINK_RE = /^https:\/\/(www\.|m\.|music\.)?(youtube\.com\/(watch\?|shorts\/)|youtu\.be\/)|^https:\/\/open\.spotify\.com\/(intl-[a-z-]+\/)?track\//i;

function doGet(e)  { return handle_(e.parameter || {}); }
function doPost(e) { return handle_(JSON.parse(e.postData.contents || '{}')); }

function handle_(p) {
  if (p.action === 'audio') { // read-only and slow-ish → no lock, so it never holds up saves
    try { return json_(audio_(p)); } catch (err) { return json_({ ok: false, error: String(err) }); }
  }
  if (p.action === 'uploadAudio') { // the slow Drive write runs outside the lock, so uploads can run in parallel
    try { return json_(workerOk_(p) ? uploadAudio_(p) : { ok: false, error: 'bad_key' }); }
    catch (err) { return json_({ ok: false, error: String(err) }); }
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(25000);
  try {
    const pub    = { profile: profile_, register: register_, submit: submit_ };
    const worker = { pendingSongs: pendingSongs_, setSongStatus: setSongStatus_ };
    let out;
    if (pub[p.action]) out = pub[p.action](p);
    else if (worker[p.action]) out = workerOk_(p) ? worker[p.action](p) : { ok: false, error: 'bad_key' };
    else out = { ok: false, error: 'unknown_action' };
    return json_(out);
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

// Checks the worker's key and records its heartbeat (shown to participants as workerAlive)
function workerOk_(p) {
  const props = PropertiesService.getScriptProperties(), key = props.getProperty('WORKER_KEY');
  if (!key || p.key !== key) return false;
  props.setProperty('WORKER_SEEN', String(Date.now()));
  return true;
}

// ── Participant-facing actions ───────────────────────────

function profile_(p) {
  const roll = norm_(p.roll);
  if (!roll) return { ok: false, error: 'missing_roll' };
  const sh = sheet_(P_SHEET, P_BASE);
  const row = findRow_(sh, roll);
  if (!row) return { ok: true, registered: false };

  const rec = readRow_(sh, row);
  const songs = [1, 2, 3].map(i => ({
    title : rec['song' + i + '_title'],
    artist: rec['song' + i + '_artist'],
    status: rec['song' + i + '_audio'] ? 'ready' : (rec['song' + i + '_status'] || 'pending'), // a Drive link means it succeeded
    audio : rec['song' + i + '_audio'],
  }));

  // Assign the two Phase-1 tests once (persisted, so a reload can't re-roll them)
  let tasks = [rec.p1_task_round1, rec.p1_task_round2].filter(String);
  const pool = String(p.pool || '').split(',').filter(String);
  if (tasks.length < 2 && pool.length >= 2) {
    tasks = pickBalanced_(sh, pool);
    writeCell_(sh, row, 'p1_task_round1', tasks[0]);
    writeCell_(sh, row, 'p1_task_round2', tasks[1]);
  }

  const seen = Number(PropertiesService.getScriptProperties().getProperty('WORKER_SEEN') || 0);
  return { ok: true, registered: true, name: rec.name, songs, tasks,
           done: doneKeys_(roll, 1), workerAlive: Date.now() - seen < 180000 }; // a 3-song batch can take ~2 min between heartbeats
}

function register_(p) {
  const roll = norm_(p.roll);
  if (!roll || !p.name) return { ok: false, error: 'missing_fields' };
  for (let i = 1; i <= 3; i++) {
    if (!p['song' + i + '_title'] || !LINK_RE.test(p['song' + i + '_link'] || '')) return { ok: false, error: 'bad_song_' + i };
  }
  const sh = sheet_(P_SHEET, P_BASE);
  if (findRow_(sh, roll)) return { ok: false, error: 'already_registered' };
  const rec = Object.assign({}, p, { roll, registered_at: new Date().toISOString(),
                                      song1_status: 'pending', song2_status: 'pending', song3_status: 'pending' });
  delete rec.action;
  appendObj_(sh, rec);
  return { ok: true };
}

function submit_(p) {
  const roll = norm_(p.roll);
  if (!roll || !p.step_key) return { ok: false, error: 'missing_fields' };
  if (doneKeys_(roll, Number(p.phase)).indexOf(p.step_key) !== -1) return { ok: true, duplicate: true };
  const rec = Object.assign({}, p, { roll, submitted_at: new Date().toISOString() });
  delete rec.action;
  appendObj_(sheet_(R_SHEET, R_BASE), rec);
  return { ok: true };
}

// Returns a song's MP3 as base64. Browsers can't play Drive download links directly
// (the response is blocked as a cross-site media source), but they can read the web app's replies.
function audio_(p) {
  const sh = sheet_(P_SHEET, P_BASE), row = findRow_(sh, norm_(p.roll)), idx = Number(p.idx);
  if (!row || [1, 2, 3].indexOf(idx) === -1) return { ok: false, error: 'not_found' };
  const m = String(readRow_(sh, row)['song' + idx + '_audio'] || '').match(/[?&]id=([\w-]+)/);
  if (!m) return { ok: false, error: 'not_ready' };
  return { ok: true, b64: Utilities.base64Encode(DriveApp.getFileById(m[1]).getBlob().getBytes()) };
}

// ── Worker actions (tools/fetch_songs.py) ────────────────

// p.retryFailed: also return failed songs (the worker asks for this once per start)
function pendingSongs_(p) {
  const sh = sheet_(P_SHEET, P_BASE);
  const vals = sh.getDataRange().getValues(), h = vals[0], out = [];
  vals.slice(1).forEach((r, n) => {
    const rec = {}; h.forEach((k, i) => { rec[k] = r[i]; });
    [1, 2, 3].forEach(i => {
      const st = String(rec['song' + i + '_status'] || '').trim().toLowerCase(); // blank = pending (as the site assumes)
      if (rec['song' + i + '_audio']) {
        if (st !== 'ready') markReady_(sh, n + 2, i); // has a Drive link → tidy a stale status
      } else if (rec['song' + i + '_link'] && (st === '' || st === 'pending' || st === 'downloading' || (st === 'failed' && p.retryFailed))) out.push({ roll: norm_(rec.roll), idx: i, link: rec['song' + i + '_link'] });
    });
  });
  return { ok: true, songs: out };
}

function setSongStatus_(p) {
  const sh = sheet_(P_SHEET, P_BASE), row = findRow_(sh, norm_(p.roll));
  if (!row) return { ok: false, error: 'not_found' };
  if (readRow_(sh, row)['song' + p.idx + '_audio']) { markReady_(sh, row, p.idx); return { ok: true, kept: 'ready' }; } // upload already succeeded
  writeCell_(sh, row, 'song' + p.idx + '_status', p.status);
  writeCell_(sh, row, 'song' + p.idx + '_error', p.error || '');
  return { ok: true };
}

// Runs without the global lock: the Drive write (the slow part) happens in parallel,
// and the lock is only held briefly to find the folder and to write the sheet cells.
function uploadAudio_(p) {
  const lock = LockService.getScriptLock();
  let folder;
  lock.waitLock(25000);
  try {
    if (!findRow_(sheet_(P_SHEET, P_BASE), norm_(p.roll))) return { ok: false, error: 'not_found' };
    folder = audioFolder_();   // under the lock, so parallel first uploads don't each create a folder
  } finally { lock.releaseLock(); }

  const blob = Utilities.newBlob(Utilities.base64Decode(p.b64), 'audio/mpeg', norm_(p.roll) + '_song' + p.idx + '.mp3');
  const file = folder.createFile(blob);
  file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);

  lock.waitLock(25000);
  try {
    const sh = sheet_(P_SHEET, P_BASE), row = findRow_(sh, norm_(p.roll)); // re-find: rows may have moved meanwhile
    if (!row) { file.setTrashed(true); return { ok: false, error: 'not_found' }; }
    writeCell_(sh, row, 'song' + p.idx + '_audio', 'https://drive.google.com/uc?export=download&id=' + file.getId());
    markReady_(sh, row, p.idx);
    return { ok: true };
  } finally { lock.releaseLock(); }
}

function markReady_(sh, row, idx) {
  writeCell_(sh, row, 'song' + idx + '_status', 'ready');
  writeCell_(sh, row, 'song' + idx + '_error', '');
}

function audioFolder_() {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty('AUDIO_FOLDER_ID');
  if (id) return DriveApp.getFolderById(id);
  const f = DriveApp.createFolder('raaga-taala-study-audio');
  props.setProperty('AUDIO_FOLDER_ID', f.getId());
  return f;
}

// ── Helpers ──────────────────────────────────────────────

// Balanced random assignment: the two tests used least so far (ties broken at random), and
// the one that has been in round 1 less often goes first. So every test ends up with about
// the same number of participants, split evenly between round 1 and round 2.
function pickBalanced_(sh, pool) {
  const vals = sh.getDataRange().getValues(), h = vals[0];
  const c1 = h.indexOf('p1_task_round1'), c2 = h.indexOf('p1_task_round2');
  const used = {}, first = {};
  pool.forEach(t => { used[t] = 0; first[t] = 0; });
  vals.slice(1).forEach(r => {
    if (used.hasOwnProperty(r[c1])) { used[r[c1]]++; first[r[c1]]++; }
    if (used.hasOwnProperty(r[c2])) used[r[c2]]++;
  });
  const [a, b] = pool.map(t => [used[t], Math.random(), t])
                     .sort((x, y) => x[0] - y[0] || x[1] - y[1]).slice(0, 2).map(x => x[2]);
  if (first[a] !== first[b]) return first[a] < first[b] ? [a, b] : [b, a];
  return Math.random() < 0.5 ? [a, b] : [b, a];
}

function doneKeys_(roll, phase) {
  const vals = sheet_(R_SHEET, R_BASE).getDataRange().getValues();
  const h = vals[0], ri = h.indexOf('roll'), pi = h.indexOf('phase'), ki = h.indexOf('step_key');
  return vals.slice(1).filter(r => norm_(r[ri]) === roll && Number(r[pi]) === phase).map(r => String(r[ki]));
}

function sheet_(name, baseHeaders) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, baseHeaders.length).setValues([baseHeaders]).setFontWeight('bold');
    sh.setFrozenRows(1);
    sh.getRange(1, 1, sh.getMaxRows(), sh.getMaxColumns()).setNumberFormat('@'); // keep roll numbers etc. as plain text
  }
  return sh;
}

function headers_(sh) {
  return sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0];
}

// Appends an object as a row; adds any unseen keys as new header columns.
function appendObj_(sh, obj) {
  const h = headers_(sh);
  Object.keys(obj).forEach(k => {
    if (h.indexOf(k) === -1) {
      h.push(k);
      if (sh.getMaxColumns() < h.length) sh.insertColumnsAfter(sh.getMaxColumns(), 10);
      sh.getRange(1, h.length).setValue(k).setFontWeight('bold');
    }
  });
  const row = h.map(k => {
    const v = obj[k];
    return v === undefined || v === null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
  });
  sh.appendRow(row);
}

function findRow_(sh, roll) {
  const col = headers_(sh).indexOf('roll') + 1;
  if (sh.getLastRow() < 2) return 0;
  const vals = sh.getRange(2, col, sh.getLastRow() - 1, 1).getValues();
  for (let i = 0; i < vals.length; i++) if (norm_(vals[i][0]) === roll) return i + 2;
  return 0;
}

function readRow_(sh, row) {
  const h = headers_(sh), v = sh.getRange(row, 1, 1, h.length).getValues()[0];
  const o = {}; h.forEach((k, i) => { o[k] = v[i]; }); return o;
}

function writeCell_(sh, row, key, val) {
  sh.getRange(row, headers_(sh).indexOf(key) + 1).setValue(val);
}

function norm_(r) { return String(r || '').trim().toUpperCase(); }

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
