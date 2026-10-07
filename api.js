// ===== Raaga & Taala Study — backend API =====
// Paste the Apps Script web-app URL (ends in /exec) here — see apps-script/SETUP.md.
// Demo mode (nothing is sent anywhere, data stays in this browser) is used when
// API_URL is empty, or for the roll number DEMO.
const API_URL = 'https://script.google.com/macros/s/AKfycbyAublk9f2VS90b9nM878e7VzAUFVQfNAQkH81Fa287LXp2j_p6fC0o8qQXLe1-X2gl/exec';

const API = (() => {
  const isDemo = roll => !API_URL || String(roll || '').trim().toUpperCase() === 'DEMO';

  async function get(params) {
    const res = await fetch(API_URL + '?' + new URLSearchParams(params));
    if (!res.ok) throw new Error('Server returned ' + res.status);
    return res.json();
  }

  // text/plain body avoids a CORS preflight, which Apps Script can't answer
  async function post(body) {
    const res = await fetch(API_URL, { method: 'POST', body: JSON.stringify(body) });
    if (!res.ok) throw new Error('Server returned ' + res.status);
    return res.json();
  }

  // ── Demo backend (localStorage) ──
  const DEMO_SONGS = [
    { title: 'Demo Tune 1', artist: 'Sine Ensemble', audio: 'audio/demo/demo1.mp3' },
    { title: 'Demo Tune 2', artist: 'Sine Ensemble', audio: 'audio/demo/demo2.mp3' },
    { title: 'Demo Tune 3 (fails to download)', artist: 'Sine Ensemble', audio: '' },
  ];
  const db = {
    load() { try { return JSON.parse(localStorage.getItem('demo_db')) || { p: {}, done: {}, results: [] }; } catch (_) { return { p: {}, done: {}, results: [] }; } },
    save(d) { try { localStorage.setItem('demo_db', JSON.stringify(d)); } catch (_) {} },
  };
  const demo = {
    profile(roll, pool) {
      const d = db.load();
      if (!d.p[roll] && roll === 'DEMO') d.p[roll] = { name: 'Demo Participant', at: Date.now() };
      const p = d.p[roll];
      if (!p) return { ok: true, registered: false };
      if (!p.tasks && pool.length >= 2) { const s = [...pool].sort(() => Math.random() - 0.5); p.tasks = [s[0], s[1]]; }
      db.save(d);
      const downloading = Date.now() - p.at < 45000; // pretend the worker needs 45 s
      const songs = DEMO_SONGS.map((s, i) => ({
        title: p['song' + (i + 1) + '_title'] || s.title, artist: p['song' + (i + 1) + '_artist'] || s.artist,
        status: downloading ? 'downloading' : (s.audio ? 'ready' : 'failed'), audio: downloading ? '' : s.audio,
      }));
      return { ok: true, registered: true, name: p.name, songs, tasks: p.tasks, done: d.done[roll] || [], workerAlive: true };
    },
    register(data) {
      const d = db.load();
      if (d.p[data.roll]) return { ok: false, error: 'already_registered' };
      d.p[data.roll] = Object.assign({}, data, { at: Date.now() });
      db.save(d);
      return { ok: true };
    },
    submit(rec) {
      const d = db.load();
      (d.done[rec.roll] = d.done[rec.roll] || []).push(rec.step_key);
      d.results.push(rec);
      db.save(d);
      console.log('[demo] saved', rec);
      return { ok: true };
    },
  };

  const norm = r => String(r || '').trim().toUpperCase();

  return {
    isDemo,
    profile: (roll, pool) => isDemo(roll) ? Promise.resolve(demo.profile(norm(roll), pool))
                                          : get({ action: 'profile', roll: norm(roll), pool: pool.join(',') }),
    register: data => isDemo(data.roll) ? Promise.resolve(demo.register(Object.assign({}, data, { roll: norm(data.roll) })))
                                        : post(Object.assign({ action: 'register' }, data)),
    // Saves one step; failed saves are queued in localStorage and retried on the next call.
    async submit(rec) {
      if (isDemo(rec.roll)) return demo.submit(rec);
      let queue = [];
      try { queue = JSON.parse(localStorage.getItem('study_unsent') || '[]'); } catch (_) {}
      queue.push(rec);
      const left = [];
      for (const r of queue) {
        try { const out = await post(Object.assign({ action: 'submit' }, r)); if (!out.ok) throw new Error(out.error); }
        catch (_) { left.push(r); }
      }
      try { localStorage.setItem('study_unsent', JSON.stringify(left)); } catch (_) {}
      return { ok: !left.includes(rec), pending: left.length };
    },
  };
})();
