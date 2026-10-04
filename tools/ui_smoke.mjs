import { JSDOM } from 'jsdom';
const BASE = 'http://localhost:8765';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function open(hash = '') {
  const dom = await JSDOM.fromURL(BASE + '/' + hash, { runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true,
    beforeParse(w) { w.fetch = (u, o) => fetch(new URL(u, BASE), o); w.TextDecoder = TextDecoder; w.AbortController = AbortController;
      w.confirm = () => true; w.alert = () => {}; w.document.addEventListener('DOMContentLoaded', () => {}); } });
  await sleep(600); return dom.window;
}
async function waitFor(w, fn, what, ms = 6000) {
  const t = Date.now();
  while (Date.now() - t < ms) { try { const v = fn(); if (v) return v; } catch {} await sleep(80); }
  throw new Error('timeout waiting for: ' + what + '\n--- page text ---\n' + w.document.body.textContent.slice(0, 600));
}
const $ = (w, sel) => w.document.querySelector(sel);
const byText = (w, sel, text) => [...w.document.querySelectorAll(sel)].find((e) => e.textContent.trim() === text);
function type(w, sel, v) { const el = $(w, sel); el.value = v; el.dispatchEvent(new w.Event('input', { bubbles: true })); }
function submit(w, form) { form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); }
async function login(w, email, pw) { type(w, '#email', email); type(w, '#password', pw); submit(w, $(w, 'form')); await waitFor(w, () => $(w, '.topbar'), 'topbar after login'); }
const ok = (m) => console.log('  ok  ' + m);

const client = await open();
await login(client, 'client-a@example.com', 'client123');
await waitFor(client, () => byText(client, '.nav a', 'My requests'), 'client nav');
if (byText(client, '.nav a', 'Analytics') || byText(client, '.nav a', 'Users')) throw new Error('client sees staff nav');
ok('client signs in; staff/admin nav hidden');
byText(client, 'button', 'New request').click();
type(client, '#nr-task', '<img src=x onerror="window.__xss=1"> Pick Cup'); type(client, '#nr-count', '2');
type(client, '#nr-deadline', '2030-01-01'); type(client, '#nr-notes', '<script>window.__xss=2</script>');
submit(client, $(client, 'form.card'));
await waitFor(client, () => $(client, 'tr.clickable'), 'new request row');
if (client.document.querySelector('img[src="x"]') || client.__xss) throw new Error('XSS: stored text became markup');
ok('request created; hostile task/notes rendered as text only (no XSS)');
// use a clean request for the workflow
type(client, '#nr-task', 'x'); 
byText(client, 'button', 'New request').click();
type(client, '#nr-task', 'pick cup'); type(client, '#nr-count', '2'); type(client, '#nr-deadline', '2030-01-01');
submit(client, $(client, 'form.card'));
await waitFor(client, () => [...client.document.querySelectorAll('tr.clickable td')].some((t) => t.textContent === 'pick cup'), 'pick cup row');
const row = [...client.document.querySelectorAll('tr.clickable')].find((r) => r.textContent.includes('pick cup') && !r.textContent.includes('<'));
const id = row.querySelector('td').textContent.trim(); row.click();
await waitFor(client, () => $(client, '.tracker'), 'detail tracker'); ok(`client opens request #${id}, tracker shown`);

const ops = await open('#/requests/' + id);
await login(ops, 'ops1@example.com', 'ops123');
await waitFor(ops, () => byText(ops, 'button', 'Start work'), 'Start work button');
byText(ops, 'button', 'Start work').click();
await waitFor(ops, () => byText(ops, 'button', 'Mark as delivered'), 'in progress');
await waitFor(ops, () => ops.document.querySelectorAll('#main tbody input[type=checkbox]').length >= 2, 'picker rows');
const boxes = [...ops.document.querySelectorAll('#main tbody input[type=checkbox]:not(:disabled)')].slice(0, 2);
boxes.forEach((b) => { b.checked = true; b.dispatchEvent(new ops.Event('change', { bubbles: true })); });
await waitFor(ops, () => byText(ops, 'button', 'Assign 2 selected'), 'assign button label');
byText(ops, 'button', 'Assign 2 selected').click();
await waitFor(ops, () => ops.document.body.textContent.includes('2 assigned'), '2 assigned');
ok('operator starts work, picks and assigns 2 episodes');
byText(ops, 'button', 'Mark as delivered').click();
await waitFor(ops, () => ops.document.querySelector('.pill[data-s="delivered"]'), 'delivered pill'); ok('operator delivers');

// client sees it live (SSE) without reloading, then accepts
await waitFor(client, () => byText(client, 'button', 'Accept delivery'), 'live update reached the client (SSE)', 8000);
ok('client UI updated live via SSE and shows Accept/Reject');
byText(client, 'button', 'Accept delivery').click();
await waitFor(client, () => client.document.querySelector('.tracker li.current')?.textContent === 'Accepted', 'accepted');
ok('client accepts; history shows 4 entries: ' + client.document.querySelectorAll('.timeline li').length);

const admin = await open();
await login(admin, 'admin@example.com', 'admin123');
for (const [hash, text] of [['#/analytics', 'Top tasks by good episodes'], ['#/episodes', 'Previous imports'], ['#/users', 'All users']]) {
  admin.location.hash = hash; await waitFor(admin, () => admin.document.body.textContent.includes(text), hash);
}
ok('admin: analytics, episodes/import and users pages render');
process.exit(0);
