/* Dataset Request Desk — single-page UI, no build step, no dependencies.
 * Security notes: every piece of server-provided text goes through textContent (the `h` helper never
 * uses innerHTML), so stored text can't become markup. The server remains the authority on what a role
 * may do; this file only decides what to *show*. */
'use strict';

const STATUS_LABEL = { submitted: 'Submitted', in_progress: 'In progress', delivered: 'Delivered',
                       accepted: 'Accepted', rejected: 'Rejected' };
const STAFF = ['operator', 'admin'];
const state = { token: sessionStorage.getItem('token'), user: JSON.parse(sessionStorage.getItem('user') || 'null') };
let current = null;          // the mounted view: { refresh() } — called when a live event arrives
let liveAbort = null;

/* ---------- tiny DOM helper ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function select(options, value, attrs) {  // options: [[value, label], ...]
  const el = h('select', attrs, options.map(([v, l]) => h('option', { value: v }, l)));
  el.value = value ?? options[0][0];
  return el;
}
const pill = (s) => h('span', { class: 'pill', 'data-s': s }, STATUS_LABEL[s] || s);
const qpill = (q) => h('span', { class: 'pill', 'data-q': q }, q);
const fmtDate = (iso) => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
const fmtDay = (d) => new Date(d + 'T00:00:00').toLocaleDateString([], { dateStyle: 'medium' });
function human(seconds) {
  if (seconds == null) return 'n/a';
  const d = Math.floor(seconds / 86400), hr = Math.floor((seconds % 86400) / 3600), m = Math.round((seconds % 3600) / 60);
  return d ? `${d}d ${hr}h` : hr ? `${hr}h ${m}m` : `${m}m`;
}
function toast(message, kind) {
  const el = h('div', { class: 'toast' + (kind === 'error' ? ' error' : '') }, message);
  document.getElementById('toast').append(el);
  setTimeout(() => el.remove(), kind === 'error' ? 10000 : 4000);
}

/* ---------- API ---------- */
class ApiError extends Error { constructor(status, message, data) { super(message); this.status = status; this.data = data; } }
function describe(data, status) {
  if (!data) return `Request failed (${status})`;
  if (Array.isArray(data.detail)) return data.detail.map((e) => `${(e.loc || []).slice(1).join('.')}: ${e.msg}`).join('; ');
  let msg = data.detail || `Request failed (${status})`;
  if (data.episode_ids) msg += ': ' + data.episode_ids.join(', ');
  if (data.episodes) msg += ': ' + Object.entries(data.episodes).map(([e, r]) => `${e} (request #${r})`).join(', ');
  return msg;
}
async function api(path, { method = 'GET', json, form } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = 'Bearer ' + state.token;
  let body;
  if (form) body = form;
  else if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  const res = await fetch(path, { method, headers, body });
  const data = await res.json().catch(() => null);
  if (res.status === 401 && state.token) { signOut('Your session has ended. Please sign in again.'); throw new ApiError(401, 'signed out', data); }
  if (!res.ok) throw new ApiError(res.status, describe(data, res.status), data);
  return data;
}
const query = (params) => new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null)).toString();
async function attempt(fn, okMessage) {   // run an action, surface errors as toasts
  try { const r = await fn(); if (okMessage) toast(okMessage); return r; }
  catch (e) { if (e.status !== 401) toast(e.message, 'error'); return undefined; }
}

/* ---------- session ---------- */
function signIn(payload) {
  state.token = payload.access_token; state.user = payload.user;
  sessionStorage.setItem('token', state.token); sessionStorage.setItem('user', JSON.stringify(state.user));
  location.hash = '#/requests';
  boot();
}
function signOut(message) {
  if (liveAbort) liveAbort.abort();
  state.token = null; state.user = null; sessionStorage.clear(); current = null;
  boot();
  if (message) toast(message);
}

/* ---------- live updates (Server-Sent Events over fetch, so the token stays out of the URL) ---------- */
let refreshTimer = null;
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => current && current.refresh && current.refresh(), 250);
}
async function startLive() {
  if (liveAbort) liveAbort.abort();
  const ctl = (liveAbort = new AbortController());
  const dot = () => document.getElementById('live');
  let delay = 1000;
  while (!ctl.signal.aborted && state.token) {
    try {
      const res = await fetch('/api/events', { headers: { Authorization: 'Bearer ' + state.token }, signal: ctl.signal });
      if (res.status === 401) return signOut('Your session has expired. Please sign in again.');
      if (!res.ok) throw new Error('stream failed');
      if (dot()) dot().dataset.on = 'true';
      delay = 1000;
      const reader = res.body.getReader(), decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          if (block.startsWith('event: expired')) return signOut('Your session has expired. Please sign in again.');
          if (block.includes('data: ')) scheduleRefresh();
        }
      }
    } catch (e) { if (ctl.signal.aborted) return; }
    if (dot()) dot().dataset.on = 'false';
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 2, 30000);
  }
}

/* ---------- shell + routing ---------- */
const app = document.getElementById('app');
const isStaff = () => STAFF.includes(state.user.role);

function shell(main) {
  const links = [['#/requests', isStaff() ? 'Requests' : 'My requests']];
  if (isStaff()) links.push(['#/episodes', 'Episodes & import'], ['#/analytics', 'Analytics']);
  if (state.user.role === 'admin') links.push(['#/users', 'Users']);
  const here = location.hash.startsWith('#/requests') ? '#/requests' : location.hash;
  return [
    h('header', { class: 'topbar' },
      h('div', { class: 'brand' }, 'Dataset Request ', h('span', null, 'Desk')),
      h('nav', { class: 'nav', 'aria-label': 'Main' },
        links.map(([href, label]) => h('a', { href, 'aria-current': href === here ? 'page' : null }, label))),
      h('div', { class: 'who' },
        h('span', { id: 'live', class: 'live', 'data-on': 'false', title: 'Live updates' }, 'Live'),
        h('span', null, state.user.name, ' ', h('span', { class: 'muted' }, `(${state.user.role})`)),
        h('button', { type: 'button', onclick: () => signOut() }, 'Sign out'))),
    main,
  ];
}

function loginView() {
  const email = h('input', { id: 'email', type: 'email', required: true, autocomplete: 'username' });
  const password = h('input', { id: 'password', type: 'password', required: true, autocomplete: 'current-password' });
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault();
    const r = await attempt(() => api('/api/auth/login', { method: 'POST', json: { email: email.value, password: password.value } }));
    if (r) signIn(r);
  } },
  h('label', { for: 'email' }, 'Email', email), h('label', { for: 'password' }, 'Password', password),
  h('button', { class: 'primary', type: 'submit' }, 'Sign in'));
  return h('div', { class: 'login' }, h('h1', null, 'Dataset Request Desk'),
    h('p', { class: 'muted' }, 'Internal platform for dataset requests and episode delivery.'), form);
}

function boot() {
  app.replaceChildren();
  if (!state.token || !state.user) { app.append(loginView()); return; }
  const main = h('main', { id: 'main' });
  app.append(...shell(main));
  render();
  startLive();
}

const ROUTES = [
  [/^#\/requests\/(\d+)$/, (m) => requestDetailView(Number(m[1])), null],
  [/^#\/requests$/, () => requestListView(), null],
  [/^#\/episodes$/, () => episodesView(), STAFF],
  [/^#\/analytics$/, () => analyticsView(), STAFF],
  [/^#\/users$/, () => usersView(), ['admin']],
];
function render() {
  if (!state.user) return;
  const main = document.getElementById('main');
  if (!main) return;
  document.querySelectorAll('.nav a').forEach((a) => {
    const here = location.hash.startsWith('#/requests') ? '#/requests' : location.hash;
    a.setAttribute('aria-current', a.getAttribute('href') === here ? 'page' : 'false');
  });
  for (const [re, view, roles] of ROUTES) {
    const m = location.hash.match(re);
    if (m && (!roles || roles.includes(state.user.role))) { main.replaceChildren(); current = null; view(m); return; }
  }
  location.hash = '#/requests';
}
window.addEventListener('hashchange', render);
const mount = (...nodes) => document.getElementById('main').append(...nodes);

/* ---------- requests: list ---------- */
function progress(assigned, wanted) {
  const meter = h('span', { class: 'meter' + (assigned >= wanted ? ' full' : '') }, h('i'));
  meter.firstChild.style.width = Math.min(100, Math.round((assigned / wanted) * 100)) + '%';
  return h('span', null, meter, ' ', `${assigned}/${wanted}`);
}

function requestListView() {
  const staff = isStaff();
  const view = { status: '', task: '', offset: 0, limit: 25 };
  const listBox = h('div');
  const formBox = h('div');

  async function load() {
    const data = await attempt(() => api('/api/requests?' + query({ status: view.status, task_name: view.task, limit: view.limit, offset: view.offset })));
    if (!data) return;
    listBox.replaceChildren(
      data.items.length === 0 ? h('p', { class: 'empty' }, staff ? 'No requests match.' : 'You have no requests yet. Create one to get started.')
        : h('div', { class: 'tablewrap' }, h('table', null,
          h('thead', null, h('tr', null, h('th', null, '#'), staff && h('th', null, 'Client'), h('th', null, 'Task'),
            h('th', null, 'Episodes'), h('th', null, 'Deadline'), h('th', null, 'Status'), h('th', null, 'Updated'))),
          h('tbody', null, data.items.map((r) => h('tr', { class: 'clickable', tabindex: 0,
            onclick: () => (location.hash = '#/requests/' + r.id),
            onkeydown: (e) => e.key === 'Enter' && (location.hash = '#/requests/' + r.id) },
          h('td', { class: 'mono' }, r.id), staff && h('td', null, r.client_organisation || r.client_name),
          h('td', null, r.task_name), h('td', null, progress(r.assigned_count, r.episodes_requested)),
          h('td', null, fmtDay(r.deadline)), h('td', null, pill(r.status)), h('td', { class: 'muted' }, fmtDate(r.updated_at))))))),
      h('div', { class: 'actions' },
        h('button', { type: 'button', disabled: view.offset === 0, onclick: () => { view.offset = Math.max(0, view.offset - view.limit); load(); } }, 'Previous'),
        h('button', { type: 'button', disabled: view.offset + view.limit >= data.total, onclick: () => { view.offset += view.limit; load(); } }, 'Next'),
        h('span', { class: 'muted' }, data.total ? `${view.offset + 1}–${Math.min(view.offset + view.limit, data.total)} of ${data.total}` : '')));
  }

  function newRequestForm() {
    const task = h('input', { id: 'nr-task', required: true, maxlength: 200, placeholder: 'e.g. pick cup' });
    const count = h('input', { id: 'nr-count', type: 'number', min: 1, max: 100000, value: 100, required: true });
    const deadline = h('input', { id: 'nr-deadline', type: 'date', required: true,
      min: new Date().toISOString().slice(0, 10) });
    const notes = h('textarea', { id: 'nr-notes', maxlength: 5000 });
    return h('form', { class: 'card', onsubmit: async (e) => {
      e.preventDefault();
      const r = await attempt(() => api('/api/requests', { method: 'POST', json: {
        task_name: task.value, episodes_requested: Number(count.value), deadline: deadline.value, notes: notes.value } }), 'Request submitted');
      if (r) { formBox.replaceChildren(); load(); }
    } },
    h('header', null, h('h2', null, 'New dataset request')),
    h('div', { class: 'form' },
      h('label', { for: 'nr-task' }, 'Task', task), h('label', { for: 'nr-count' }, 'Episodes needed', count),
      h('label', { for: 'nr-deadline' }, 'Deliver by', deadline),
      h('label', { class: 'wide', for: 'nr-notes' }, 'Notes', notes),
      h('div', { class: 'actions wide' }, h('button', { class: 'primary', type: 'submit' }, 'Submit request'),
        h('button', { type: 'button', onclick: () => formBox.replaceChildren() }, 'Cancel'))));
  }

  const statusSel = select([['', 'Any status'], ...Object.entries(STATUS_LABEL)], '', { id: 'f-status' });
  const taskIn = h('input', { id: 'f-task', placeholder: 'any task' });
  mount(
    h('div', { class: 'page-head' }, h('h1', null, staff ? 'Requests' : 'My requests'),
      !staff && h('button', { class: 'primary', type: 'button', onclick: () => formBox.replaceChildren(newRequestForm()) }, 'New request')),
    formBox,
    h('div', { class: 'card' },
      h('form', { class: 'filters', onsubmit: (e) => { e.preventDefault(); view.status = statusSel.value; view.task = taskIn.value; view.offset = 0; load(); } },
        h('label', { for: 'f-status' }, 'Status', statusSel), h('label', { for: 'f-task' }, 'Task', taskIn),
        h('button', { type: 'submit' }, 'Apply filters')),
      listBox));
  current = { refresh: load };
  load();
}

/* ---------- requests: detail ---------- */
function tracker(r) {
  const order = ['submitted', 'in_progress', 'delivered'];
  const idx = order.indexOf(r.status);
  const outcome = r.status === 'accepted' || r.status === 'rejected';
  const nodes = order.map((s, i) => h('li', { class: outcome || i < idx ? 'done' : i === idx ? 'current' : '' }, STATUS_LABEL[s]));
  nodes.push(h('li', { class: r.status === 'accepted' ? 'current' : r.status === 'rejected' ? 'bad' : 'pending-outcome' },
    r.status === 'rejected' ? 'Rejected' : r.status === 'accepted' ? 'Accepted' : 'Accepted or rejected'));
  return h('div', null, h('ol', { class: 'tracker', 'aria-label': 'Request progress' }, nodes),
    r.status === 'rejected' && h('p', { class: 'rework-note' }, 'The client rejected this delivery. It goes back to In progress for rework.'));
}

const ACTION_LABEL = { 'submitted>in_progress': 'Start work', 'in_progress>delivered': 'Mark as delivered',
  'delivered>accepted': 'Accept delivery', 'delivered>rejected': 'Reject delivery', 'rejected>in_progress': 'Reopen for rework' };

function requestDetailView(id) {
  const box = h('div', { class: 'grid2-stack' });
  box.style.display = 'grid'; box.style.gap = '1.25rem';
  let picker = null;

  async function load() {
    const [r, hist, eps] = await Promise.all([
      attempt(() => api(`/api/requests/${id}`)), attempt(() => api(`/api/requests/${id}/history`)),
      attempt(() => api(`/api/requests/${id}/episodes`))]);
    if (!r) { box.replaceChildren(h('p', { class: 'empty' }, 'Request not found.'), h('a', { href: '#/requests' }, 'Back to requests')); return; }
    const staff = isStaff();
    const note = h('textarea', { id: 'note', maxlength: 1000, placeholder: staff ? 'Optional note for the history' : 'Optional: what you liked, or what needs fixing' });
    const buttons = r.allowed_transitions.map((to) => {
      const label = ACTION_LABEL[`${r.status}>${to}`] || to;
      return h('button', { type: 'button', class: to === 'rejected' ? 'danger' : 'primary',
        onclick: async () => {
          if (to === 'accepted' && !confirm('Accept this delivery? This cannot be undone.')) return;
          if (await attempt(() => api(`/api/requests/${id}/transitions`, { method: 'POST', json: { to_status: to, comment: note.value } }), `${label}: done`)) load();
        } }, label);
    });
    const needMore = r.episodes_requested - r.assigned_count;
    box.replaceChildren(
      h('a', { href: '#/requests' }, 'Back to requests'),
      h('section', { class: 'card' },
        h('header', null, h('h1', null, `Request #${r.id}: ${r.task_name}`), pill(r.status)),
        tracker(r),
        h('p', { class: 'facts' },
          staff && h('span', null, 'Client ', h('b', null, r.client_organisation || r.client_name)),
          h('span', null, 'Episodes ', h('b', null, progress(r.assigned_count, r.episodes_requested))),
          h('span', null, 'Deadline ', h('b', null, fmtDay(r.deadline))), h('span', null, 'Opened ', h('b', null, fmtDate(r.created_at)))),
        r.notes && h('p', null, h('span', { class: 'muted' }, 'Notes: '), r.notes),
        buttons.length > 0 && h('div', { class: 'actions' },
          h('label', { for: 'note', class: 'wide' }, 'Note', note), buttons),
        staff && r.status === 'in_progress' && needMore > 0 && h('p', { class: 'hint' }, `${needMore} more episode${needMore === 1 ? '' : 's'} needed before this can be delivered.`)),
      assignedCard(r, eps || [], staff),
      staff && r.status === 'in_progress' && pickerCard(r),
      historyCard(hist || []));
  }

  function assignedCard(r, eps, staff) {
    const canEdit = staff && r.status === 'in_progress';
    return h('section', { class: 'card' }, h('header', null, h('h2', null, 'Assigned episodes'), h('span', { class: 'muted' }, `${eps.length} assigned`)),
      eps.length === 0 ? h('p', { class: 'empty' }, 'Nothing assigned yet.') : h('div', { class: 'tablewrap' }, h('table', null,
        h('thead', null, h('tr', null, h('th', null, 'Episode'), h('th', null, 'Robot'), h('th', null, 'Recorded'), h('th', { class: 'num' }, 'Seconds'), h('th', null, 'Quality'), canEdit && h('th'))),
        h('tbody', null, eps.map((e) => h('tr', null, h('td', { class: 'mono' }, e.episode_id), h('td', null, e.robot_id),
          h('td', null, fmtDate(e.recorded_at)), h('td', { class: 'num' }, e.duration_seconds), h('td', null, qpill(e.quality)),
          canEdit && h('td', { class: 'right' }, h('button', { type: 'button', class: 'link',
            onclick: async () => { if (await attempt(() => api(`/api/requests/${id}/episodes/${encodeURIComponent(e.episode_id)}`, { method: 'DELETE' }))) load(); } }, 'Remove'))))))));
  }

  function pickerCard(r) {
    const f = { task: r.task_name, quality: 'good', robot: '', offset: 0 };
    const selected = new Set();
    const rows = h('tbody'); const more = h('button', { type: 'button' }, 'Load more');
    const assignBtn = h('button', { class: 'primary', type: 'button', disabled: true }, 'Assign selected');
    const taskIn = h('input', { id: 'p-task', value: f.task });
    const qSel = select([['good', 'good'], ['usable', 'usable'], ['', 'any (bad can’t be assigned)']], 'good', { id: 'p-quality' });
    const robotSel = select([['', 'any robot'], ...['arm-01', 'arm-02', 'arm-03', 'mobile-01', 'humanoid-01'].map((x) => [x, x])], '', { id: 'p-robot' });
    const empty = h('p', { class: 'empty' }); const checkAll = h('input', { type: 'checkbox', 'aria-label': 'Select all on screen' });
    const sync = () => { assignBtn.disabled = selected.size === 0; assignBtn.textContent = selected.size ? `Assign ${selected.size} selected` : 'Assign selected'; };

    async function fetchPage(reset) {
      if (reset) { f.offset = 0; rows.replaceChildren(); selected.clear(); checkAll.checked = false; sync(); }
      const data = await attempt(() => api('/api/episodes?' + query({ task_name: f.task, quality: f.quality, robot_id: f.robot, assignment: 'unassigned', limit: 50, offset: f.offset })));
      if (!data) return;
      f.offset += data.items.length; more.hidden = !data.has_more;
      for (const e of data.items) {
        const box = h('input', { type: 'checkbox', 'aria-label': `Select ${e.episode_id}`, disabled: e.quality === 'bad',
          onchange: () => { box.checked ? selected.add(e.episode_id) : selected.delete(e.episode_id); sync(); } });
        box.dataset.id = e.episode_id;
        rows.append(h('tr', { class: e.quality === 'bad' ? 'dim' : '' }, h('td', null, box), h('td', { class: 'mono' }, e.episode_id), h('td', null, e.robot_id),
          h('td', null, fmtDate(e.recorded_at)), h('td', { class: 'num' }, e.duration_seconds), h('td', null, qpill(e.quality)), h('td', null, e.operator_name || '')));
      }
      empty.textContent = rows.children.length ? '' : 'No unassigned episodes match these filters.';
    }
    checkAll.addEventListener('change', () => rows.querySelectorAll('input[type=checkbox]:not(:disabled)').forEach((b) => {
      b.checked = checkAll.checked; checkAll.checked ? selected.add(b.dataset.id) : selected.delete(b.dataset.id); sync(); }));
    more.addEventListener('click', () => fetchPage(false));
    assignBtn.addEventListener('click', async () => {
      const ids = [...selected];
      const res = await attempt(() => api(`/api/requests/${id}/episodes`, { method: 'POST', json: { episode_ids: ids } }));
      if (res) { toast(`Assigned ${res.assigned.length} episode${res.assigned.length === 1 ? '' : 's'}`); load(); }
    });
    picker = { reload: () => fetchPage(true) };
    fetchPage(true);
    return h('section', { class: 'card' }, h('header', null, h('h2', null, 'Assign episodes'), h('span', { class: 'muted' }, 'Only unassigned episodes are listed')),
      h('form', { class: 'filters', onsubmit: (e) => { e.preventDefault(); f.task = taskIn.value; f.quality = qSel.value; f.robot = robotSel.value; fetchPage(true); } },
        h('label', { for: 'p-task' }, 'Task', taskIn), h('label', { for: 'p-quality' }, 'Quality', qSel), h('label', { for: 'p-robot' }, 'Robot', robotSel),
        h('button', { type: 'submit' }, 'Filter')),
      h('div', { class: 'tablewrap' }, h('table', null, h('thead', null, h('tr', null, h('th', null, checkAll), h('th', null, 'Episode'), h('th', null, 'Robot'),
        h('th', null, 'Recorded'), h('th', { class: 'num' }, 'Seconds'), h('th', null, 'Quality'), h('th', null, 'Operator'))), rows)),
      empty, h('div', { class: 'actions' }, assignBtn, more));
  }

  function historyCard(hist) {
    return h('section', { class: 'card' }, h('header', null, h('h2', null, 'History')),
      h('ol', { class: 'timeline' }, hist.slice().reverse().map((e) => h('li', null,
        h('span', { class: 'when' }, fmtDate(e.changed_at)),
        h('span', null, e.from_status ? [pill(e.from_status), ' to '] : 'Created as ', pill(e.to_status), ' by ', h('b', null, e.changed_by_name), ` (${e.changed_by_role})`),
        e.comment && h('span', { class: 'note' }, e.comment)))));
  }

  mount(box);
  current = { refresh: async () => { await load(); } };
  load();
}

/* ---------- episodes & import (staff) ---------- */
function episodesView() {
  const resultBox = h('div'); const historyBox = h('div'); const listBox = h('div');
  const file = h('input', { id: 'csv', type: 'file', accept: '.csv,text/csv' });
  const f = { task: '', quality: '', robot: '', assignment: 'any', offset: 0 };
  const limit = 50;

  function report(r) {
    const reasons = Object.entries(r.skipped_by_reason);
    return h('div', { class: 'card' },
      h('header', null, h('h2', null, `Import result: ${r.filename}`), h('span', { class: 'muted mono' }, `run #${r.import_run_id}`)),
      h('div', { class: 'stats' },
        h('div', { class: 'stat' }, h('b', null, r.total_rows), 'rows read'), h('div', { class: 'stat' }, h('b', null, r.imported), 'imported'),
        h('div', { class: 'stat' }, h('b', null, r.skipped), 'skipped'), h('div', { class: 'stat' }, h('b', null, r.normalised_rows), 'tidied (case, spacing, date format)')),
      Object.keys(r.warnings).length > 0 && h('p', { class: 'hint' }, 'Imported with warnings: ', Object.entries(r.warnings).map(([k, v]) => `${v} × ${k.replaceAll('_', ' ')}`).join(', ')),
      reasons.length === 0 ? h('p', { class: 'empty' }, 'Every row was imported.') : [
        h('h3', null, 'Why rows were skipped'),
        h('table', null, h('tbody', null, reasons.map(([k, v]) => h('tr', null, h('td', null, k.replaceAll('_', ' ')), h('td', { class: 'num' }, v))))),
        h('details', null, h('summary', null, `Row details${r.skipped_rows_truncated ? ` (first ${r.skipped_rows.length})` : ''}`),
          h('div', { class: 'tablewrap' }, h('table', null, h('thead', null, h('tr', null, h('th', { class: 'num' }, 'Line'), h('th', null, 'Episode'), h('th', null, 'Reason'), h('th', null, 'Detail'))),
            h('tbody', null, r.skipped_rows.map((x) => h('tr', null, h('td', { class: 'num' }, x.line), h('td', { class: 'mono' }, x.episode_id || '(none)'),
              h('td', null, x.reason.replaceAll('_', ' ')), h('td', { class: 'muted' }, x.detail || '')))))))]);
  }
  async function loadHistory() {
    const runs = await attempt(() => api('/api/episodes/imports'));
    if (!runs) return;
    historyBox.replaceChildren(runs.length === 0 ? h('p', { class: 'empty' }, 'No imports yet.') : h('div', { class: 'tablewrap' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Run'), h('th', null, 'File'), h('th', null, 'When'), h('th', { class: 'num' }, 'Rows'), h('th', { class: 'num' }, 'Imported'), h('th', { class: 'num' }, 'Skipped'))),
      h('tbody', null, runs.map((r) => h('tr', null, h('td', { class: 'mono' }, r.id), h('td', null, r.filename), h('td', { class: 'muted' }, fmtDate(r.started_at)),
        h('td', { class: 'num' }, r.total_rows), h('td', { class: 'num' }, r.imported), h('td', { class: 'num' }, r.skipped)))))));
  }
  async function loadEpisodes(reset) {
    if (reset) f.offset = 0;
    const data = await attempt(() => api('/api/episodes?' + query({ task_name: f.task, quality: f.quality, robot_id: f.robot, assignment: f.assignment, limit, offset: f.offset })));
    if (!data) return;
    listBox.replaceChildren(data.items.length === 0 ? h('p', { class: 'empty' }, 'No episodes match.') : h('div', { class: 'tablewrap' }, h('table', null,
      h('thead', null, h('tr', null, ['Episode', 'Robot', 'Task', 'Recorded', 'Seconds', 'Quality', 'Operator', 'Request'].map((t) => h('th', null, t)))),
      h('tbody', null, data.items.map((e) => h('tr', null, h('td', { class: 'mono' }, e.episode_id), h('td', null, e.robot_id), h('td', null, e.task_name), h('td', null, fmtDate(e.recorded_at)),
        h('td', { class: 'num' }, e.duration_seconds), h('td', null, qpill(e.quality)), h('td', null, e.operator_name || ''),
        h('td', null, e.assigned_request_id ? h('a', { href: '#/requests/' + e.assigned_request_id }, '#' + e.assigned_request_id) : '')))))),
    h('div', { class: 'actions' }, h('button', { type: 'button', disabled: f.offset === 0, onclick: () => { f.offset = Math.max(0, f.offset - limit); loadEpisodes(); } }, 'Previous'),
      h('button', { type: 'button', disabled: !data.has_more, onclick: () => { f.offset += limit; loadEpisodes(); } }, 'Next'),
      h('span', { class: 'muted' }, `Showing ${f.offset + 1}–${f.offset + data.items.length}`)));
  }

  const taskIn = h('input', { id: 'e-task' });
  const qSel = select([['', 'any'], ['good', 'good'], ['usable', 'usable'], ['bad', 'bad']], '', { id: 'e-q' });
  const robotSel = select([['', 'any'], ...['arm-01', 'arm-02', 'arm-03', 'mobile-01', 'humanoid-01'].map((x) => [x, x])], '', { id: 'e-r' });
  const aSel = select([['any', 'any'], ['unassigned', 'unassigned'], ['assigned', 'assigned']], 'any', { id: 'e-a' });
  const importBtn = h('button', { class: 'primary', type: 'submit' }, 'Import');
  mount(h('div', { class: 'page-head' }, h('h1', null, 'Episodes & import')),
    h('section', { class: 'card' }, h('header', null, h('h2', null, 'Import episode metadata'), h('span', { class: 'muted' }, 'Safe to repeat: episodes already stored are skipped')),
      h('form', { class: 'filters', onsubmit: async (e) => {
        e.preventDefault();
        if (!file.files[0]) return toast('Choose a CSV file first', 'error');
        const fd = new FormData(); fd.append('file', file.files[0]);
        importBtn.disabled = true;
        const r = await attempt(() => api('/api/episodes/import', { method: 'POST', form: fd }));
        importBtn.disabled = false;
        if (r) { resultBox.replaceChildren(report(r)); toast(`Imported ${r.imported}, skipped ${r.skipped}`); loadHistory(); loadEpisodes(true); }
      } }, h('label', { for: 'csv' }, 'CSV export', file), importBtn)),
    resultBox,
    h('section', { class: 'card' }, h('header', null, h('h2', null, 'Previous imports')), historyBox),
    h('section', { class: 'card' }, h('header', null, h('h2', null, 'Browse episodes')),
      h('form', { class: 'filters', onsubmit: (e) => { e.preventDefault(); Object.assign(f, { task: taskIn.value, quality: qSel.value, robot: robotSel.value, assignment: aSel.value }); loadEpisodes(true); } },
        h('label', { for: 'e-task' }, 'Task', taskIn), h('label', { for: 'e-q' }, 'Quality', qSel), h('label', { for: 'e-r' }, 'Robot', robotSel),
        h('label', { for: 'e-a' }, 'Assignment', aSel), h('button', { type: 'submit' }, 'Filter')), listBox));
  current = { refresh: () => loadEpisodes() };
  loadHistory(); loadEpisodes(true);
}

/* ---------- analytics (staff) ---------- */
function analyticsView() {
  const today = new Date().toISOString().slice(0, 10);
  const ago = new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10);
  const from = h('input', { id: 'a-from', type: 'date', value: ago }), to = h('input', { id: 'a-to', type: 'date', value: today });
  const out = h('div', { class: 'grid2-stack' }); out.style.display = 'grid'; out.style.gap = '1.25rem';

  async function run() {
    const d = await attempt(() => api('/api/analytics?' + query({ from: from.value, to: to.value })));
    if (!d) return;
    const robots = [...new Set(d.episodes_per_day_per_robot.map((x) => x.robot_id))].sort();
    const days = [...new Set(d.episodes_per_day_per_robot.map((x) => x.day))].sort();
    const cell = (day, robot) => (d.episodes_per_day_per_robot.find((x) => x.day === day && x.robot_id === robot) || {}).episodes || 0;
    const top = d.top_tasks_by_good_episodes, maxTop = Math.max(1, ...top.map((t) => t.good_episodes));
    out.replaceChildren(
      h('section', { class: 'card' }, h('header', null, h('h2', null, 'Request fulfilment'), h('span', { class: 'muted' }, 'Requests created in this range, by current status')),
        h('div', { class: 'stats' }, Object.entries(d.requests_by_status).map(([s, n]) => h('div', { class: 'stat' }, h('b', null, n), STATUS_LABEL[s])),
          h('div', { class: 'stat' }, h('b', null, human(d.median_submit_to_deliver_seconds)), `median time to deliver (${d.delivered_requests_in_range} first delivered in range)`))),
      h('section', { class: 'card' }, h('header', null, h('h2', null, 'Top tasks by good episodes')),
        top.length === 0 ? h('p', { class: 'empty' }, 'No good episodes in this range.') : h('table', null, h('tbody', null, top.map((t) => {
          const bar = h('span', { class: 'bar' }); bar.style.width = Math.round((t.good_episodes / maxTop) * 220) + 'px';
          return h('tr', null, h('td', null, t.task_name), h('td', { class: 'num' }, t.good_episodes), h('td', null, bar));
        })))),
      h('section', { class: 'card' }, h('header', null, h('h2', null, 'Episodes recorded per day, per robot')),
        days.length === 0 ? h('p', { class: 'empty' }, 'No episodes recorded in this range.') : h('div', { class: 'tablewrap' }, h('table', null,
          h('thead', null, h('tr', null, h('th', null, 'Day'), robots.map((r) => h('th', { class: 'num' }, r)), h('th', { class: 'num' }, 'Total'))),
          h('tbody', null, days.map((day) => h('tr', null, h('td', null, fmtDay(day)), robots.map((r) => h('td', { class: 'num' }, cell(day, r) || '')),
            h('td', { class: 'num' }, robots.reduce((s, r) => s + cell(day, r), 0)))))))));
  }
  mount(h('div', { class: 'page-head' }, h('h1', null, 'Analytics')),
    h('form', { class: 'card filters', onsubmit: (e) => { e.preventDefault(); run(); } },
      h('label', { for: 'a-from' }, 'From', from), h('label', { for: 'a-to' }, 'To', to), h('button', { class: 'primary', type: 'submit' }, 'Run report')), out);
  current = { refresh: run };
  run();
}

/* ---------- users (admin) ---------- */
function usersView() {
  const box = h('div');
  async function load() {
    const users = await attempt(() => api('/api/users'));
    if (!users) return;
    box.replaceChildren(h('div', { class: 'tablewrap' }, h('table', null,
      h('thead', null, h('tr', null, ['Name', 'Email', 'Organisation', 'Role', 'Active', ''].map((t) => h('th', null, t)))),
      h('tbody', null, users.map((u) => {
        const me = u.id === state.user.id;
        const role = select([['client', 'client'], ['operator', 'operator'], ['admin', 'admin']], u.role, { 'aria-label': `Role for ${u.name}`, disabled: me });
        role.addEventListener('change', async () => { await attempt(() => api('/api/users/' + u.id, { method: 'PATCH', json: { role: role.value } }), 'Role updated'); load(); });
        return h('tr', { class: u.is_active ? '' : 'dim' }, h('td', null, u.name), h('td', null, u.email), h('td', null, u.organisation || ''), h('td', null, role),
          h('td', null, u.is_active ? 'yes' : 'no'),
          h('td', { class: 'right' }, !me && h('button', { type: 'button', class: u.is_active ? 'danger' : '',
            onclick: async () => { await attempt(() => api('/api/users/' + u.id, { method: 'PATCH', json: { is_active: !u.is_active } }), u.is_active ? 'User deactivated' : 'User reactivated'); load(); } },
          u.is_active ? 'Deactivate' : 'Reactivate')));
      })))));
  }
  const name = h('input', { id: 'u-name', required: true }), email = h('input', { id: 'u-email', type: 'email', required: true });
  const org = h('input', { id: 'u-org' }), pw = h('input', { id: 'u-pw', type: 'password', required: true, minlength: 8, autocomplete: 'new-password' });
  const role = select([['client', 'client'], ['operator', 'operator'], ['admin', 'admin']], 'client', { id: 'u-role' });
  mount(h('div', { class: 'page-head' }, h('h1', null, 'Users')),
    h('form', { class: 'card', onsubmit: async (e) => {
      e.preventDefault();
      const r = await attempt(() => api('/api/users', { method: 'POST', json: { name: name.value, email: email.value, organisation: org.value || null, role: role.value, password: pw.value } }), 'User created');
      if (r) { e.target.reset(); load(); }
    } }, h('header', null, h('h2', null, 'Create user')),
    h('div', { class: 'form' }, h('label', { for: 'u-name' }, 'Name', name), h('label', { for: 'u-email' }, 'Email', email),
      h('label', { for: 'u-org' }, 'Organisation (clients)', org), h('label', { for: 'u-role' }, 'Role', role),
      h('label', { for: 'u-pw' }, 'Initial password (8+ characters)', pw), h('button', { class: 'primary', type: 'submit' }, 'Create user'))),
    h('section', { class: 'card' }, h('header', null, h('h2', null, 'All users')), box));
  current = { refresh: load };
  load();
}

boot();
