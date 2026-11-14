// mochi client. The server only relays (no history), so every conversation is stored here, per chat,
// including chats you are not looking at -- that is what makes the very first message show up.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
const low = s => s.toLowerCase();
const hhmm = t => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const load = () => { try { return JSON.parse(localStorage.getItem('mochi')) || {}; } catch { return {}; } };
const store = o => { try { localStorage.setItem('mochi', JSON.stringify(o)); } catch {} };

// chat keys: 'public' | 'u:<lowercase username>' | 'g:<group id>'
const S = {
  ws: null, user: null, me: null, authed: false, retry: 0, pinger: 0, active: null, draft: '',
  profile: { display_name: '', bio: '', avatar: '', ...load().profile },
  people: {}, online: new Set(), groups: {}, chats: {}, typing: {},
};
const chat = k => (S.chats[k] ||= { msgs: [], unread: 0, last: 0 });
const nameOf = u => S.people[low(u)]?.display_name || u;
const route = k => k === 'public' ? { scope: 'public' }
  : k[0] === 'u' ? { scope: 'private', recipient: S.people[k.slice(2)].username }
  : { scope: 'group', group_id: k.slice(2) };
const keyOf = m => m.scope === 'public' ? 'public' : m.scope === 'group' ? 'g:' + m.group_id
  : 'u:' + low(m.username === S.me ? m.recipient : m.username);

// ---------- helpers: avatar, toast, sound, title ----------
const TONES = ['#ffc2e2', '#d9c7ff', '#bfe3ff', '#fff0a8', '#c8f2d8', '#ffd2b8'];
const FACES = ['•ᴗ•', '◕‿◕', '≧◡≦', '^ω^', 'ᵔᴗᵔ', 'òᴗó', '>ᴗ<', '•ω•'];
const hash = s => [...s].reduce((h, c) => h + c.charCodeAt(0), 0);
function avatar(p, online) {
  const n = p.display_name || p.username || '?';
  const bg = p.avatar ? `background-image:url(${p.avatar})` : `background:${TONES[hash(p.username || n) % TONES.length]}`;
  const dot = online == null ? '' : `<u class="pd${online ? ' on' : ''}"></u>`;
  return `<span class="av" style="${bg}">${p.avatar ? '' : FACES[hash(p.username || n) % FACES.length]}${dot}</span>`;
}
function toast(text, onclick) {
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = text;
  if (onclick) { t.style.cursor = 'pointer'; t.onclick = () => { onclick(); t.remove(); }; }
  $('toasts').append(t); setTimeout(() => t.remove(), 3500);
}
let ac;
const audio = () => (ac ||= new AudioContext());
function ding() {
  try {
    const o = audio().createOscillator(), g = ac.createGain();
    o.frequency.value = 880; g.gain.setValueAtTime(.08, ac.currentTime);
    g.gain.exponentialRampToValueAtTime(.001, ac.currentTime + .25);
    o.connect(g).connect(ac.destination); o.start(); o.stop(ac.currentTime + .25);
  } catch {}
}
const title = () => {
  const n = Object.values(S.chats).reduce((a, c) => a + c.unread, 0);
  document.title = (n ? `(${n}) ` : '') + 'mochi';
};

// ---------- connection ----------
const send = o => S.ws?.readyState === 1 ? S.ws.send(JSON.stringify(o)) : toast('offline, reconnecting…');
function status(text, ok) { $('status').textContent = text; $('dot').className = 'dot' + (ok ? ' on' : ''); }

function connect() {
  const ws = S.ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  status('connecting…');
  ws.onopen = () => send({ type: 'LOGIN', username: S.user, display_name: S.profile.display_name });
  ws.onmessage = e => handle(JSON.parse(e.data));
  ws.onclose = () => {
    clearInterval(S.pinger); S.authed = false; S.online.clear();
    if (!S.user) return;                       // login was rejected: no auto-retry
    status('offline · retrying…'); if (S.me) render();
    setTimeout(connect, Math.min(1000 * 2 ** S.retry++, 8000));
  };
}

function handle(m) {
  switch (m.type) {
    case 'LOGIN_OK':
      S.authed = true; S.me = m.username; S.retry = 0; status('online', true);
      $('login').close(); store({ user: S.user, profile: S.profile });
      if (S.profile.bio || S.profile.avatar) send({ type: 'PROFILE_UPDATE', ...S.profile });
      S.pinger = setInterval(ping, 4000); ping(); render();
      break;
    case 'ERROR':
      if (S.authed) return toast(m.message);
      if (S.me) return S.ws.close();           // reconnecting (e.g. ghost session still online): retry later
      $('loginErr').textContent = m.message; S.user = null; S.ws.close();
      break;
    case 'USER_LIST':
      S.online = new Set(m.users.map(u => low(u.username)));
      m.users.forEach(u => S.people[low(u.username)] = u);
      render();
      break;
    case 'GROUP_LIST':
      m.groups.forEach(g => { if (!S.groups[g.id]) toast(`added to ${g.name}`); S.groups[g.id] = g; });
      render();
      break;
    case 'GROUP_CREATED':
      S.groups[m.id] = { id: m.id, name: m.name, members: [S.me] }; open('g:' + m.id);
      break;
    case 'PRESENCE': {
      if (m.username === S.me) break;
      const t = `${nameOf(m.username)} ${m.event === 'JOINED' ? 'joined' : 'left'}`;
      toast(t); chat('public').msgs.push({ sys: true, text: t });
      if (S.active === 'public') paint();
      break;
    }
    case 'HISTORY': addHistory(m.messages); break;
    case 'CHAT': onChat(m); break;
    case 'TYPING': onTyping(m); break;
    case 'PONG': status(`online · ${Math.round(performance.now() - m.id)} ms`, true); break;
    case 'PROFILE_OK': toast('profile saved ✨'); break;
  }
}
const ping = () => send({ type: 'PING', id: performance.now() });

// ---------- incoming chat ----------
// Replayed history: no unread/notification, deduped by server seq (reconnects resend the same messages).
function addHistory(msgs) {
  const touched = new Set();
  for (const m of msgs) {
    const key = keyOf(m), c = chat(key);
    if (c.msgs.some(x => x.seq === m.seq)) continue;
    if (key[0] === 'u' && !S.people[key.slice(2)]) S.people[key.slice(2)] = { username: m.username === S.me ? m.recipient : m.username };
    c.msgs.push(m); touched.add(key);
  }
  for (const k of touched) {
    const c = chat(k); c.msgs.sort((a, b) => (a.seq ?? 1e12) - (b.seq ?? 1e12));
    c.last = Math.max(c.last, ...c.msgs.filter(x => !x.sys).map(x => x.timestamp));
  }
  render(); if (S.active) paint();
}
function onChat(m) {
  const key = keyOf(m), c = chat(key), mine = m.username === S.me;
  if (key[0] === 'u' && !S.people[key.slice(2)]) S.people[key.slice(2)] = { username: mine ? m.recipient : m.username };
  c.msgs.push(m); c.last = m.timestamp;
  delete S.typing[key]?.[m.username];
  if (!mine && !(key === S.active && !document.hidden)) { c.unread++; notify(key, m); }
  if (key === S.active) paint(mine);
  render(); title();
}
function notify(key, m) {
  ding();
  const who = nameOf(m.username);
  const where = key === 'public' ? ' · lobby' : key[0] === 'g' ? ' · ' + S.groups[key.slice(2)].name : '';
  if (!document.hidden) return toast(`${who}${where}: ${m.text}`, () => open(key));
  if (window.Notification?.permission === 'granted') {
    const n = new Notification(who + where, { body: m.text, icon: S.people[low(m.username)]?.avatar || undefined });
    n.onclick = () => { window.focus(); open(key); n.close(); };
  }
}

// ---------- typing indicator ----------
function onTyping(m) {
  const key = m.scope === 'public' ? 'public' : m.scope === 'group' ? 'g:' + m.group_id : 'u:' + low(m.username);
  const t = (S.typing[key] ||= {});
  clearTimeout(t[m.username]); delete t[m.username];
  if (m.is_typing) t[m.username] = setTimeout(() => { delete t[m.username]; render(); }, 4000); // safety net if "stop" is lost
  render();
}
let typingOn = null, typingSent = 0, typingTimer;
function stopTyping() {
  clearTimeout(typingTimer);
  if (typingOn) { send({ type: 'TYPING', ...route(typingOn), is_typing: false }); typingOn = null; typingSent = 0; }
}
$('text').oninput = () => {
  if (typingOn !== S.active) stopTyping();
  if (Date.now() - typingSent > 2500) { typingOn = S.active; typingSent = Date.now(); send({ type: 'TYPING', ...route(S.active), is_typing: true }); }
  clearTimeout(typingTimer); typingTimer = setTimeout(stopTyping, 2000);
};

// ---------- rendering ----------
function meta(key) {
  const c = chat(key), id = key.slice(2), last = c.msgs.findLast(m => !m.sys), typers = Object.keys(S.typing[key] || {});
  let name, av, sub, on = null;
  if (key === 'public') { name = 'lobby'; av = '<span class="av lobby">★</span>'; sub = `${S.online.size} online`; }
  else if (key[0] === 'g') { const g = S.groups[id]; name = g.name; av = '<span class="av grp">♥</span>'; sub = g.members.map(nameOf).join(', '); }
  else { const p = S.people[id]; on = S.online.has(id); name = p.display_name || p.username; av = avatar(p, on); sub = on ? (p.bio || 'online') : 'offline'; }
  const prefix = last?.username === S.me ? 'you: ' : key[0] === 'u' ? '' : last ? nameOf(last.username) + ': ' : '';
  const preview = typers.length ? 'typing…' : last ? prefix + last.text : key[0] === 'u' ? sub : 'say hi 👋';
  return { name, av, sub, on, last: c.last, unread: c.unread, preview, typing: !!typers.length };
}

function entries() {
  const rows = [{ key: 'public', rank: 0 }, ...Object.keys(S.groups).map(id => ({ key: 'g:' + id, rank: 1 }))];
  for (const k in S.people)
    if (k !== low(S.me) && (S.online.has(k) || S.chats['u:' + k]?.msgs.length)) rows.push({ key: 'u:' + k, rank: 2 });
  // latest conversation first; chats with no messages yet fall back to lobby > groups > online people
  return rows.map(r => ({ ...r, ...meta(r.key) })).sort((a, b) => b.last - a.last || a.rank - b.rank || a.name.localeCompare(b.name));
}

function render() {
  $('list').innerHTML = entries().map(r => `
    <div class="row${r.key === S.active ? ' on' : ''}" data-k="${esc(r.key)}">${r.av}
      <div class="ri"><b>${esc(r.name)}</b><small${r.typing ? ' class="ty"' : ''}>${esc(r.preview)}</small></div>
      <div class="rm">${r.last ? `<time>${hhmm(r.last)}</time>` : ''}${r.unread ? `<i class="bd">${r.unread}</i>` : ''}</div>
    </div>`).join('');
  const me = { username: S.me, ...S.people[low(S.me)] };
  $('meAv').innerHTML = avatar(me); $('meName').textContent = me.display_name || me.username;
  if (S.active) header();
}

function header() {
  const m = meta(S.active), off = m.on === false || !S.authed;
  $('hAv').innerHTML = m.av; $('hName').textContent = m.name; $('hSub').textContent = m.sub;
  $('addBtn').hidden = S.active[0] !== 'g';
  $('tbMain').textContent = m.name + ' - conversation';
  $('text').disabled = off; $('text').placeholder = !S.authed ? 'reconnecting…' : off ? `${m.name} is offline` : 'say something…';
  const names = Object.keys(S.typing[S.active] || {}).map(nameOf);
  $('typing').innerHTML = names.length
    ? `${esc(names.length > 2 ? 'several people' : names.join(' & '))} ${names.length > 1 ? 'are' : 'is'} typing<span class="dots"><span></span><span></span><span></span></span>` : '';
}

function paint(forceBottom) {
  const el = $('msgs'), near = el.scrollHeight - el.scrollTop - el.clientHeight < 140, k = S.active, ms = chat(k).msgs;
  el.innerHTML = ms.map((m, i) => {
    if (m.sys) return `<div class="sys">${esc(m.text)}</div>`;
    const mine = m.username === S.me, who = !mine && k[0] !== 'u' && ms[i - 1]?.username !== m.username;
    return `<div class="m${mine ? ' me' : ''}">${who ? `<small class="who">${esc(nameOf(m.username))}</small>` : ''}<p>${esc(m.text)}</p><time>${hhmm(m.timestamp)}</time></div>`;
  }).join('');
  if (near || forceBottom) el.scrollTop = el.scrollHeight;
}

function open(key) {
  stopTyping(); S.active = key; chat(key).unread = 0;
  $('empty').hidden = true; $('chat').hidden = false; $('app').classList.add('open');
  render(); paint(true); title(); $('text').focus();
}
function close() {
  stopTyping(); S.active = null;
  $('chat').hidden = true; $('empty').hidden = false; $('tbMain').textContent = 'chat.exe'; $('app').classList.remove('open'); render();
}

// ---------- events ----------
$('list').onclick = e => { const r = e.target.closest('.row'); if (r) open(r.dataset.k); };
$('back').onclick = close;
$('empty').querySelector('.board').onclick = e => {   // quick-start cards on the empty screen
  const go = e.target.closest('button')?.dataset.go;
  if (go === 'hi') open('public'); else if (go === 'group') $('groupDlg').showModal(); else if (go === 'pic') $('meCard').click();
};
// window buttons: _ minimize, □ maximize, × close chat (chat window) / sign out (contacts window)
document.querySelectorAll('.wb button').forEach(b => b.onclick = () => {
  const win = b.closest('.win'), act = b.textContent;
  if (act === '_') win.classList.toggle('min');
  else if (act === '□') { win.classList.toggle('max'); $('app').classList.toggle('solo', win.classList.contains('max')); }
  else if (win.matches('aside')) { if (confirm('sign out of mochi?')) location.reload(); }
  else if (S.active) close();
  else win.classList.toggle('min');
});
$('composer').onsubmit = e => {
  e.preventDefault();
  const text = $('text').value.trim();
  if (!text || !S.active) return;
  send({ type: 'CHAT', text, ...route(S.active) }); $('text').value = ''; stopTyping();
};
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && S.active) { chat(S.active).unread = 0; render(); title(); }
});

$('login').oncancel = e => e.preventDefault();
$('loginForm').onsubmit = e => {
  e.preventDefault();
  S.user = $('user').value.trim(); if (!S.user) return;
  S.profile.display_name = $('disp').value.trim(); $('loginErr').textContent = '';
  audio().resume(); window.Notification?.requestPermission?.();   // both need a user gesture
  connect();
};

$('newGroup').onclick = () => $('groupDlg').showModal();
$('groupForm').onsubmit = e => {
  e.preventDefault(); send({ type: 'GROUP_CREATE', name: $('gName').value.trim() });
  $('gName').value = ''; $('groupDlg').close();
};
$('addBtn').onclick = () => {
  const g = S.groups[S.active.slice(2)];
  const free = [...S.online].filter(k => !g.members.some(m => low(m) === k));
  $('pick').innerHTML = free.length
    ? free.map(k => `<button data-u="${esc(S.people[k].username)}">${avatar(S.people[k], true)}<b>${esc(nameOf(S.people[k].username))}</b></button>`).join('')
    : '<p class="mut">everyone online is already in</p>';
  $('addDlg').showModal();
};
$('pick').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  send({ type: 'GROUP_ADD_MEMBER', group_id: S.active.slice(2), username: b.dataset.u }); $('addDlg').close();
};
document.querySelectorAll('[data-x]').forEach(b => b.onclick = () => b.closest('dialog').close());

// profile + photo (resized to a 128px square in the browser so the frame stays tiny)
const drawPic = () => $('pAv').innerHTML = avatar({ username: S.me, display_name: $('pName').value || S.me, avatar: S.draft });
const square = file => new Promise(done => {
  const img = new Image();
  img.onerror = () => done(S.draft);
  img.onload = () => {
    const s = Math.min(img.width, img.height), cv = Object.assign(document.createElement('canvas'), { width: 128, height: 128 });
    cv.getContext('2d').drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, 128, 128);
    done(cv.toDataURL('image/jpeg', .85));
  };
  img.src = URL.createObjectURL(file);
});
$('meCard').onclick = () => {
  S.draft = S.profile.avatar; $('pName').value = S.profile.display_name || S.me; $('pBio').value = S.profile.bio;
  drawPic(); $('profile').showModal();
};
$('pName').oninput = drawPic;
$('pAv').onclick = $('pPick').onclick = () => $('file').click();
$('pClear').onclick = () => { S.draft = ''; drawPic(); };
$('file').onchange = async e => { const f = e.target.files[0]; if (f) { S.draft = await square(f); drawPic(); } e.target.value = ''; };
$('profileForm').onsubmit = e => {
  e.preventDefault();
  S.profile = { display_name: $('pName').value.trim(), bio: $('pBio').value.trim(), avatar: S.draft };
  store({ user: S.user, profile: S.profile }); send({ type: 'PROFILE_UPDATE', ...S.profile }); $('profile').close();
};

$('user').value = load().user || ''; $('disp').value = S.profile.display_name;
$('login').showModal();
