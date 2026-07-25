"""HTML page templates for the login, chat, and dashboard views."""

LOGIN_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in</title>
<style>
body{{background:#0f1115;color:#e6e6e6;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
form{{background:#1a1d24;padding:2rem;border-radius:12px;width:min(320px,90vw);box-shadow:0 8px 24px rgba(0,0,0,.4)}}
h1{{font-size:1.1rem;margin:0 0 1rem}}
input{{width:100%;padding:.6rem;border-radius:8px;border:1px solid #333;background:#0f1115;color:#eee;box-sizing:border-box;margin-bottom:.8rem}}
button{{width:100%;padding:.6rem;border:0;border-radius:8px;background:#5865f2;color:#fff;font-weight:600;cursor:pointer}}
.err{{color:#ff6b6b;font-size:.85rem;margin-top:-.4rem;margin-bottom:.8rem}}
</style></head>
<body><form method="POST" action="/login">
<h1>Enter password</h1>
{error}
<input type="password" name="password" autofocus required>
<button type="submit">Unlock</button>
</form></body></html>"""

CHAT_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Console</title>
<style>
body{{background:#0f1115;color:#e6e6e6;font-family:system-ui,sans-serif;margin:0;display:flex;flex-direction:column;height:100vh}}
header{{display:flex;align-items:center;justify-content:space-between;padding:.6rem 1rem;border-bottom:1px solid #222;gap:.6rem}}
header a{{color:#9aa0f2;text-decoration:none;font-size:.85rem}}
.hdr-right{{display:flex;align-items:center;gap:.6rem;min-width:0}}
#sessionPicker{{max-width:min(46vw,360px)}}
#log{{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.6rem}}
.msg{{max-width:80%;padding:.6rem .9rem;border-radius:10px;white-space:pre-wrap;word-wrap:break-word}}
.me{{align-self:flex-end;background:#5865f2;color:#fff}}
.bot{{align-self:flex-start;background:#1a1d24}}
.sys{{align-self:center;color:#888;font-size:.8rem}}
form{{display:flex;padding:.8rem;gap:.5rem;border-top:1px solid #222}}
input{{flex:1;padding:.7rem;border-radius:8px;border:1px solid #333;background:#1a1d24;color:#eee}}
select{{padding:.7rem;border-radius:8px;border:1px solid #333;background:#1a1d24;color:#eee}}
button{{padding:.7rem 1.1rem;border:0;border-radius:8px;background:#5865f2;color:#fff;font-weight:600;cursor:pointer}}
button:disabled{{opacity:.5}}
</style></head>
<body>
<header>
<strong>Console</strong>
<div class="hdr-right">
<select id="sessionPicker" title="Switch conversation"><option value="">+ New chat</option></select>
<a href="/dashboard">Dashboard &rarr;</a>
</div>
</header>
<div id="log"></div>
<form id="f">
<select id="timeout" title="Minimum + max time to let this question run; - skips the minimum floor entirely">
<option value="-">-</option>
<option value="5m">5m</option>
<option value="15m">15m</option>
<option value="30m">30m</option>
</select>
<input id="i" autocomplete="off" placeholder="Message..." autofocus><button id="b">Send</button></form>
<script>
const log = document.getElementById('log');
const form = document.getElementById('f');
const input = document.getElementById('i');
const btn = document.getElementById('b');
const timeoutSel = document.getElementById('timeout');
const sessionPicker = document.getElementById('sessionPicker');
function add(text, cls) {{
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}}
function fmtElapsed(ms) {{
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return m + ':' + String(s % 60).padStart(2, '0');
}}
function renderMessages(messages) {{
  log.innerHTML = '';
  for (const m of messages) add(m.text, m.role === 'user' ? 'me' : 'bot');
}}
function sessionLabel(s) {{
  const when = s.last_ts ? new Date(s.last_ts).toLocaleString() : '';
  return s.preview + '  ·  ' + s.turns + ' turns  ·  ' + when;
}}
async function loadSessionList() {{
  const res = await fetch('/api/sessions');
  if (res.status === 401) {{ location.reload(); return null; }}
  const data = await res.json();
  sessionPicker.innerHTML = '<option value="">+ New chat</option>';
  for (const s of data.sessions) {{
    const opt = document.createElement('option');
    opt.value = s.session_id;
    opt.textContent = sessionLabel(s);
    sessionPicker.appendChild(opt);
  }}
  sessionPicker.value = data.current || '';
  return data;
}}
sessionPicker.addEventListener('change', async () => {{
  const sid = sessionPicker.value;
  if (!sid) {{
    await fetch('/api/sessions/new', {{method:'POST'}});
    log.innerHTML = '';
    input.focus();
    return;
  }}
  const res = await fetch('/api/sessions/resume', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{session_id: sid}})}});
  if (res.status === 401) {{ location.reload(); return; }}
  const data = await res.json();
  renderMessages(data.messages || []);
  input.focus();
}});
(async () => {{
  const data = await loadSessionList();
  if (data && data.current) {{
    const res = await fetch('/api/sessions/messages?id=' + encodeURIComponent(data.current));
    if (res.ok) {{
      const d = await res.json();
      renderMessages(d.messages || []);
    }}
  }}
}})();
// night_mode.py runs as a detached background process with no connection
// back to this tab, so QA-round summaries (see /night, /night_deep) can
// only reach the chat log by polling -- same shape as the /api/chat/status
// poll above, just on a slower interval and running continuously rather
// than only while a single reply is in flight, since a night run can take
// up to 8h across many rounds.
let nightSince = 0;
async function pollNightSummaries() {{
  const res = await fetch('/api/night/summary?since=' + nightSince);
  if (res.status === 401) return;
  const data = await res.json();
  nightSince = data.since;
  for (const s of (data.summaries || [])) add(s.text, 'bot');
}}
pollNightSummaries();
setInterval(pollNightSummaries, 5000);
form.addEventListener('submit', async (e) => {{
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  add(text, 'me');
  input.value = '';
  btn.disabled = true;
  const started = Date.now();
  const waitEl = add('... (0:00)', 'sys');
  const ticker = setInterval(() => {{
    waitEl.textContent = '... (' + fmtElapsed(Date.now() - started) + ')';
  }}, 1000);
  try {{
    const res = await fetch('/api/chat', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{message: text, timeout: timeoutSel.value}})}});
    if (res.status === 401) {{ clearInterval(ticker); location.reload(); return; }}
    const data = await res.json();
    let result;
    if (data.job_id) {{
      // Long-running replies run off-thread server-side and get polled for,
      // instead of one HTTP request staying open for the whole wait -- a
      // proxy in front of this server can close a connection well before
      // this app's own 5m/15m/30m tiers would.
      while (true) {{
        await new Promise(r => setTimeout(r, 2000));
        const sres = await fetch('/api/chat/status?job_id=' + encodeURIComponent(data.job_id));
        if (sres.status === 401) {{ clearInterval(ticker); location.reload(); return; }}
        const sdata = await sres.json();
        if (sdata.status === 'running') continue;
        result = sdata;
        break;
      }}
    }} else {{
      result = data;  // synchronous replies (e.g. /night) still come back directly
    }}
    clearInterval(ticker);
    log.removeChild(waitEl);
    add(result.reply || result.error || '(no reply)', 'bot');
    if (!result.error) loadSessionList();
  }} catch (err) {{
    clearInterval(ticker);
    log.removeChild(waitEl);
    add('Error: ' + err, 'sys');
  }}
  btn.disabled = false;
  input.focus();
}});
</script>
</body></html>"""

DASHBOARD_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard</title>
<style>
:root{
  --surface:#1a1d24; --page:#0f1115; --ink:#e6e6e6; --ink-2:#9a9a9a; --ink-muted:#6b6b6b;
  --grid:#2a2d34; --series-in:#3987e5; --series-out:#d95926; --series-window:#9aa0f2;
  --good:#0ca30c; --warn:#fab219; --crit:#e05252;
}
body{background:var(--page);color:var(--ink);font-family:system-ui,sans-serif;margin:0}
header{display:flex;align-items:center;justify-content:space-between;padding:.6rem 1rem;border-bottom:1px solid #222;position:sticky;top:0;background:var(--page)}
header a{color:#9aa0f2;text-decoration:none;font-size:.85rem}
main{padding:1rem;max-width:900px;margin:0 auto;display:flex;flex-direction:column;gap:1.2rem}
h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;color:var(--ink-2);margin:0 0 .6rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.7rem}
.tile{background:var(--surface);border-radius:10px;padding:.8rem .9rem}
.tile .label{font-size:.75rem;color:var(--ink-2)}
.tile .value{font-size:1.4rem;font-weight:600;margin-top:.2rem;font-variant-numeric:tabular-nums}
.card{background:var(--surface);border-radius:10px;padding:1rem}
.meter-row{display:flex;flex-direction:column;gap:.7rem}
.meter{display:grid;grid-template-columns:90px 1fr 70px;align-items:center;gap:.6rem;font-size:.85rem}
.meter .track{height:10px;border-radius:6px;background:#0f1115;overflow:hidden}
.meter .fill{height:100%;border-radius:6px;transition:width .4s ease}
.meter .pct{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink-2)}
.legend{display:flex;gap:1rem;font-size:.8rem;color:var(--ink-2);margin-bottom:.5rem}
.legend span{display:inline-flex;align-items:center;gap:.35rem}
.swatch{width:10px;height:10px;border-radius:2px;display:inline-block}
#chart{width:100%;height:180px;display:block}
.axis-label{font-size:.65rem;fill:var(--ink-muted)}
.tablewrap{display:none;margin-top:.6rem;max-height:220px;overflow:auto}
.tablewrap.show{display:block}
table{width:100%;border-collapse:collapse;font-size:.78rem}
th,td{text-align:right;padding:.25rem .5rem;border-bottom:1px solid var(--grid)}
th:first-child,td:first-child{text-align:left}
.linklike{background:none;border:0;color:#9aa0f2;font-size:.78rem;cursor:pointer;padding:0}
.stale{color:var(--ink-muted);font-size:.75rem}
</style></head>
<body>
<header><strong>Dashboard</strong><a href="/">&larr; Console</a></header>
<main>
  <section>
    <h2>Claude token usage</h2>
    <div class="tiles">
      <div class="tile"><div class="label">Input tokens</div><div class="value" id="t-in">-</div></div>
      <div class="tile"><div class="label">Output tokens</div><div class="value" id="t-out">-</div></div>
      <div class="tile"><div class="label">Cache (read+write)</div><div class="value" id="t-cache">-</div></div>
      <div class="tile"><div class="label">Last 5h vs limit</div><div class="value" id="t-total">-</div></div>
    </div>
  </section>

  <section class="card">
    <div class="legend">
      <span><i class="swatch" style="background:var(--series-in)"></i>Input / 5 min</span>
      <span><i class="swatch" style="background:var(--series-out)"></i>Output / 5 min</span>
      <span><i class="swatch" style="background:var(--series-window)"></i>Last 5h window</span>
      <span class="stale" id="last-collected" style="margin-left:auto"></span>
    </div>
    <svg id="chart" viewBox="0 0 640 180" preserveAspectRatio="none"></svg>
    <button class="linklike" id="toggle-table">Show as table</button>
    <div class="tablewrap" id="tablewrap">
      <table><thead><tr><th>Time</th><th>Input</th><th>Output</th></tr></thead><tbody id="tbody"></tbody></table>
    </div>
  </section>

  <section class="card">
    <h2>Token budget (rolling 5h window)</h2>
    <div class="meter-row">
      <div class="meter"><span>Used / 5h limit</span><div class="track"><div class="fill" id="m-limit-fill"></div></div><span class="pct" id="m-limit-pct">-</span></div>
    </div>
  </section>

  <section class="card">
    <h2>Host resources</h2>
    <div class="meter-row">
      <div class="meter"><span>CPU</span><div class="track"><div class="fill" id="m-cpu-fill"></div></div><span class="pct" id="m-cpu-pct">-</span></div>
      <div class="meter"><span>RAM</span><div class="track"><div class="fill" id="m-ram-fill"></div></div><span class="pct" id="m-ram-pct">-</span></div>
      <div class="meter"><span>Disk</span><div class="track"><div class="fill" id="m-disk-fill"></div></div><span class="pct" id="m-disk-pct">-</span></div>
    </div>
  </section>
</main>
<script>
function fmt(n) {
  if (n == null) return '-';
  return n.toLocaleString();
}
function statusColor(pct) {
  if (pct >= 90) return 'var(--crit)';
  if (pct >= 70) return 'var(--warn)';
  return 'var(--good)';
}
function setMeter(prefix, used, total, pct) {
  document.getElementById('m-' + prefix + '-fill').style.width = Math.min(100, pct) + '%';
  document.getElementById('m-' + prefix + '-fill').style.background = statusColor(pct);
  document.getElementById('m-' + prefix + '-pct').textContent = pct.toFixed(1) + '%';
}
function drawChart(history, windowStartTs) {
  const svg = document.getElementById('chart');
  const W = 640, H = 180, PAD = 20;
  svg.innerHTML = '';
  const pts = history;
  if (pts.length < 2) {
    svg.innerHTML = '<text x="20" y="90" fill="#6b6b6b" font-size="12">Not enough samples yet (collects every 5 min)</text>';
    return;
  }
  const deltas = [];
  for (let i = 1; i < pts.length; i++) {
    deltas.push({
      ts: pts[i].ts,
      input: Math.max(0, pts[i].input_tokens - pts[i-1].input_tokens),
      output: Math.max(0, pts[i].output_tokens - pts[i-1].output_tokens),
    });
  }
  const maxVal = Math.max(1, ...deltas.map(d => d.input + d.output));
  const bw = (W - PAD*2) / deltas.length;
  const scaleY = v => (H - PAD*2) * (v / maxVal);
  const yBase = H - PAD;
  let bars = '';
  deltas.forEach((d, i) => {
    const x = PAD + i * bw;
    const hIn = scaleY(d.input);
    const hOut = scaleY(d.output);
    bars += `<rect x="${x}" y="${yBase - hIn}" width="${Math.max(1, bw*0.4)}" height="${hIn}" fill="var(--series-in)"></rect>`;
    bars += `<rect x="${x + bw*0.45}" y="${yBase - hOut}" width="${Math.max(1, bw*0.4)}" height="${hOut}" fill="var(--series-out)"></rect>`;
  });

  // Mark where the trailing 5h window starts, so it's clear at a glance how
  // much of the visible history counts toward the budget meter above.
  let marker = '';
  if (windowStartTs != null && windowStartTs >= deltas[0].ts && windowStartTs <= deltas[deltas.length - 1].ts) {
    let idx = deltas.findIndex(d => d.ts >= windowStartTs);
    if (idx < 0) idx = deltas.length - 1;
    const markerX = PAD + idx * bw;
    marker += `<rect x="${markerX}" y="${PAD}" width="${W - PAD - markerX}" height="${yBase - PAD}" fill="var(--series-window)" opacity="0.08"></rect>`;
    marker += `<line x1="${markerX}" y1="${PAD}" x2="${markerX}" y2="${yBase}" stroke="var(--series-window)" stroke-width="1.5" stroke-dasharray="4,3"></line>`;
    marker += `<text x="${markerX + 4}" y="${PAD + 10}" fill="var(--series-window)" font-size="10">last 5h</text>`;
  }

  const gridline = `<line x1="${PAD}" y1="${yBase}" x2="${W-PAD}" y2="${yBase}" stroke="var(--grid)"></line>`;
  svg.innerHTML = marker + gridline + bars;

  const tbody = document.getElementById('tbody');
  tbody.innerHTML = deltas.slice().reverse().map(d =>
    `<tr><td>${new Date(d.ts*1000).toLocaleString()}</td><td>${fmt(d.input)}</td><td>${fmt(d.output)}</td></tr>`
  ).join('');
}
async function refresh() {
  try {
    const res = await fetch('/api/stats');
    if (res.status === 401) { location.href = '/'; return; }
    const data = await res.json();
    document.getElementById('t-in').textContent = fmt(data.tokens.input_tokens);
    document.getElementById('t-out').textContent = fmt(data.tokens.output_tokens);
    document.getElementById('t-cache').textContent = fmt(data.tokens.cache_creation_tokens + data.tokens.cache_read_tokens);
    const windowTotal = data.tokens.window_input_tokens + data.tokens.window_output_tokens;
    document.getElementById('t-total').textContent = fmt(windowTotal) + (data.tokens.limit ? ' / ' + fmt(data.tokens.limit) : '');
    if (data.tokens.limit) {
      const pct = Math.min(999, windowTotal / data.tokens.limit * 100);
      setMeter('limit', windowTotal, data.tokens.limit, pct);
    } else {
      document.getElementById('m-limit-pct').textContent = 'no limit set';
    }
    setMeter('cpu', null, null, data.system.cpu_percent);
    setMeter('ram', data.system.mem.used_mb, data.system.mem.total_mb, data.system.mem.percent);
    setMeter('disk', data.system.disk.used_gb, data.system.disk.total_gb, data.system.disk.percent);
    drawChart(data.history, data.window_start_ts);
    if (data.last_collected) {
      document.getElementById('last-collected').textContent = 'Last collected: ' + new Date(data.last_collected*1000).toLocaleTimeString();
    }
  } catch (err) {
    console.error(err);
  }
}
document.getElementById('toggle-table').addEventListener('click', () => {
  const w = document.getElementById('tablewrap');
  const on = w.classList.toggle('show');
  document.getElementById('toggle-table').textContent = on ? 'Hide table' : 'Show as table';
});
refresh();
setInterval(refresh, 15000);
</script>
</body></html>"""
