"""The single page a human actually interacts with.

Hand-written HTML/CSS/JS in one string, no framework and no build step:
this is one page with one form and a list, and a toolchain would be more
machinery than the thing it builds. It shares the report's palette so
the two don't look like different products.

The design follows the one unusual fact about this workflow -- you
cannot finish in one sitting. So the check id is shown prominently and
persisted in localStorage, and the page's main job is telling you what
to go fetch and letting you come back and drop it in.
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BhoomiCheck</title>
<style>
  :root {
    --ink:#1a1a1a; --muted:#6b6b6b; --line:#e0ddd6; --bg:#faf9f7;
    --accent:#2e6b4f; --issue:#b3261e; --warn:#8a6100;
  }
  *{box-sizing:border-box}
  body{font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
       color:var(--ink);background:var(--bg);margin:0;padding:40px 20px}
  main{max-width:780px;margin:0 auto}
  h1{font-size:1.7rem;margin:0 0 4px}
  .sub{color:var(--muted);margin:0 0 28px}
  .card{background:#fff;border:1px solid var(--line);border-radius:4px;
        padding:22px;margin:0 0 16px}
  label{display:block;font-size:.85rem;font-weight:600;margin:0 0 4px}
  input[type=text],input[type=file]{width:100%;padding:9px 11px;border:1px solid var(--line);
        border-radius:3px;font:inherit;font-size:.95rem;background:#fff}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:0 0 14px}
  @media(max-width:600px){.row{grid-template-columns:1fr}}
  button{background:var(--accent);color:#fff;border:0;border-radius:3px;
         padding:11px 20px;font:inherit;font-weight:600;cursor:pointer}
  button:disabled{opacity:.55;cursor:default}
  button.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}
  .lookup{border-left:4px solid var(--warn);background:#fff;border:1px solid var(--line);
          border-radius:3px;padding:16px 18px;margin:0 0 12px}
  .lookup h3{margin:0 0 8px;font-size:1rem}
  .lookup pre{white-space:pre-wrap;font:inherit;font-size:.86rem;color:#444;
              background:#faf9f7;padding:12px;border-radius:3px;margin:0 0 12px}
  .checkid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#f0ede6;
           padding:3px 7px;border-radius:3px;font-size:.9rem}
  .banner{border-left:4px solid var(--accent);background:#f0f7f3;padding:14px 18px;
          margin:0 0 20px;font-size:.9rem}
  .err{border-left-color:var(--issue);background:#fdf0ef;color:#7a1a15}
  .muted{color:var(--muted);font-size:.86rem}
  .spin{display:inline-block;width:13px;height:13px;border:2px solid #fff;
        border-top-color:transparent;border-radius:50%;animation:s .7s linear infinite;
        vertical-align:-2px;margin-right:7px}
  @keyframes s{to{transform:rotate(360deg)}}
  a{color:var(--accent)}
</style>
</head>
<body>
<main>
  <h1>BhoomiCheck</h1>
  <p class="sub">Due diligence for land parcels in Warangal, Telangana.</p>

  <div id="banner" class="banner" hidden></div>

  <div class="card" id="startCard">
    <form id="startForm">
      <div class="row">
        <div><label>Survey number</label><input type="text" name="survey_no" required placeholder="123/A"></div>
        <div><label>Village</label><input type="text" name="village" required placeholder="Hasanparthy"></div>
      </div>
      <div class="row">
        <div><label>Mandal</label><input type="text" name="mandal" required placeholder="Hasanparthy"></div>
        <div><label>District</label><input type="text" name="district" value="Warangal"></div>
      </div>
      <div style="margin:0 0 14px">
        <label>Seller / owner name</label>
        <input type="text" name="seller_name" required placeholder="as printed on the deed">
        <div class="muted">Used to search court records and the registration department.</div>
      </div>
      <div style="margin:0 0 18px">
        <label>Deed documents (PDF)</label>
        <input type="file" name="documents" multiple accept=".pdf" required>
        <div class="muted">Sale deed, encumbrance certificate, pattadar passbook &mdash; whatever you have.</div>
      </div>
      <button type="submit" id="startBtn">Start check</button>
    </form>
  </div>

  <div id="resumeCard" class="card" hidden>
    <label>Resume a check you started earlier</label>
    <div class="row" style="grid-template-columns:1fr auto;margin:0">
      <input type="text" id="resumeId" placeholder="check id">
      <button class="secondary" id="resumeBtn" type="button">Resume</button>
    </div>
  </div>

  <div id="pendingArea"></div>
</main>

<script>
const $ = (id) => document.getElementById(id);
let checkId = localStorage.getItem('bhoomicheck:last');

function banner(msg, isError) {
  const b = $('banner');
  b.innerHTML = msg;
  b.className = 'banner' + (isError ? ' err' : '');
  b.hidden = false;
}

function render(state) {
  checkId = state.check_id;
  localStorage.setItem('bhoomicheck:last', checkId);
  $('startCard').hidden = true;
  $('resumeCard').hidden = true;

  if (state.complete) {
    banner(`Check <span class="checkid">${checkId}</span> is complete. ` +
           `<a href="/api/checks/${checkId}/report.html" target="_blank">Open the report</a> ` +
           `&middot; <a href="/api/checks/${checkId}/report.json" target="_blank">JSON</a>`);
    $('pendingArea').innerHTML = '';
    return;
  }

  banner(`Check <span class="checkid">${checkId}</span> &mdash; ${state.pending.length} lookup(s) left. ` +
         `You can close this page and come back; nothing is lost.`);

  $('pendingArea').innerHTML = state.pending.map((p) => `
    <div class="lookup">
      <h3>${p.agent.replace(/_/g, ' ')}</h3>
      <pre>${escapeHtml(p.instructions)}</pre>
      <input type="file" accept=".png,.jpg,.jpeg,.webp,.pdf" id="f-${p.interrupt_id}">
      <div style="margin-top:10px">
        <button type="button" onclick="upload('${p.interrupt_id}')" id="b-${p.interrupt_id}">Upload</button>
      </div>
    </div>`).join('');
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

async function upload(interruptId) {
  const input = $('f-' + interruptId);
  const btn = $('b-' + interruptId);
  if (!input.files.length) { banner('Choose a file first.', true); return; }
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>Reading it (~20s)';
  const fd = new FormData();
  fd.append('file', input.files[0]);
  try {
    const r = await fetch(`/api/checks/${checkId}/uploads/${interruptId}`, {method:'POST', body:fd});
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || 'upload failed');
    render(body);
  } catch (e) {
    banner(escapeHtml(e.message), true);
    btn.disabled = false;
    btn.textContent = 'Upload';
  }
}

$('startForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const btn = $('startBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>Reading your documents (~20s)';
  try {
    const r = await fetch('/api/checks', {method:'POST', body:new FormData(ev.target)});
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || 'could not start');
    render(body);
  } catch (e) {
    banner(escapeHtml(e.message), true);
    btn.disabled = false;
    btn.textContent = 'Start check';
  }
});

$('resumeBtn').addEventListener('click', async () => {
  const id = $('resumeId').value.trim();
  if (!id) return;
  const r = await fetch(`/api/checks/${id}`);
  if (!r.ok) { banner('No check with that id.', true); return; }
  render(await r.json());
});

// Offer to pick up where you left off.
if (checkId) {
  $('resumeCard').hidden = false;
  $('resumeId').value = checkId;
}
</script>
</body>
</html>
"""
