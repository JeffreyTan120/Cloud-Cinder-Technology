import { LINK_LIFE, SIG_LIFE, browserName, linkProof, linkUri, phoneKey, randomToken, roomSig, sameHex, scanFolder, serveChannel } from './core.js';

const params = new URLSearchParams(location.search);
const DEFAULT_RELAY = 'wss://pulse-relay.cloudcinder.workers.dev';
const relay = (() => {
  const r = params.get('relay');
  return r && /^wss?:\/\/[A-Za-z0-9.:_-]+$/.test(r) ? r : DEFAULT_RELAY;
})();
const testFolder = params.get('folder') === 'private';
const ICE = [{ urls: ['stun:stun.cloudflare.com:3478', 'stun:stun.l.google.com:19302'] }];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

const KEY = 'pulse.sync';
let state;
try {
  state = JSON.parse(localStorage.getItem(KEY));
} catch {}
if (!state?.id) state = { id: randomToken(16), name: browserName(), phones: {} };
state.phones ??= {};
const save = () => {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {}
};
save();

function idb(mode, fn) {
  return new Promise((ok, fail) => {
    const open = indexedDB.open('pulse-sync', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('kv');
    open.onerror = () => fail(open.error);
    open.onsuccess = () => {
      const tx = open.result.transaction('kv', mode);
      const req = fn(tx.objectStore('kv'));
      tx.oncomplete = () => ok(req?.result);
      tx.onerror = () => fail(tx.error);
    };
  });
}
const getFolderHandle = () => idb('readonly', (s) => s.get('folder')).catch(() => null);
const setFolderHandle = (h) => idb('readwrite', (s) => s.put(h, 'folder')).catch(() => {});

let folder = null;
let access = 'none'; // granted | prompt | none
let list = null;
let scanning = false;

async function loadFolder() {
  if (testFolder) {
    folder = await navigator.storage.getDirectory();
    access = 'granted';
  } else {
    folder = await getFolderHandle();
    access = folder ? await folder.queryPermission({ mode: 'read' }).catch(() => 'prompt') : 'none';
  }
  render();
  if (access === 'granted') await rescan();
}

async function rescan() {
  if (!folder || access !== 'granted') return null;
  scanning = true;
  render();
  try {
    list = await scanFolder(folder, (n) => {
      $('folderInfo').textContent = `Reading the folder… ${n.toLocaleString()} songs so far`;
    });
  } catch {
    access = 'prompt'; // moved, renamed or no longer allowed
    list = null;
  } finally {
    scanning = false;
    render();
  }
  return list;
}

$('pick').addEventListener('click', async () => {
  if (folder && access === 'prompt') {
    access = await folder.requestPermission({ mode: 'read' }).catch(() => 'prompt');
    if (access === 'granted') return rescan();
  }
  try {
    const h = await window.showDirectoryPicker({ id: 'pulse-music', mode: 'read', startIn: 'music' });
    folder = h;
    access = 'granted';
    await setFolderHandle(h);
    await rescan();
  } catch {
  }
});

let code = null;
let showLink = false;

function newCode() {
  code = { token: randomToken(16), until: Date.now() + LINK_LIFE };
  drawQr();
}

function drawQr() {
  const text = linkUri({ relay, room: state.id, name: state.name, token: code.token });
  $('qr').dataset.link = text; // read by the tests
  const qr = window.qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount();
  const c = $('qr');
  const scale = Math.floor(c.width / n);
  const off = Math.floor((c.width - scale * n) / 2);
  const g = c.getContext('2d');
  g.fillStyle = '#fff';
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = '#0b0d12';
  for (let r = 0; r < n; r++) for (let k = 0; k < n; k++) if (qr.isDark(r, k)) g.fillRect(off + k * scale, off + r * scale, scale, scale);
}

setInterval(() => {
  if (code && Date.now() > code.until - 15000) newCode();
}, 5000);

$('another').addEventListener('click', () => {
  showLink = !showLink;
  render();
  if (showLink) $('linkCard').scrollIntoView({ behavior: 'smooth', block: 'center' });
});

let ws = null;
let relayState = 'connecting'; // connecting | on | off
let retry = 1000;
const sessions = new Map();
const activity = {};
const seenSigs = new Map();

function connect() {
  relayState = 'connecting';
  render();
  ws = new WebSocket(`${relay}/room/${state.id}?role=host`);
  ws.onopen = () => {
    relayState = 'on';
    retry = 1000;
    render();
  };
  ws.onmessage = (e) => onRelay(String(e.data));
  ws.onclose = (e) => {
    relayState = 'off';
    render();
    if (e.code === 4000) {
      $('conn').textContent = 'Open in another tab';
      return;
    }
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 30000);
  };
}
setInterval(() => {
  if (ws?.readyState === 1) ws.send('ping');
}, 25000);

const to = (guest, msg) => ws?.readyState === 1 && ws.send(JSON.stringify({ to: guest, data: JSON.stringify(msg) }));

function onRelay(text) {
  if (text === 'pong') return;
  let m;
  try {
    m = JSON.parse(text);
  } catch {
    return;
  }
  if (m.relay === 'leave') {
    if (sessions.get(m.from)?.dc.readyState === 'open') return;
    return endSession(m.from, true);
  }
  if (m.relay) return;
  let msg;
  try {
    msg = JSON.parse(m.data);
  } catch {
    return;
  }
  onGuest(m.from, msg).catch((err) => console.warn('phone message', err));
}

async function verify(msg, what) {
  const p = state.phones[msg.phone];
  if (!p) return null;
  const t = Number(msg.time);
  if (!Number.isFinite(t) || Math.abs(Date.now() - t) > SIG_LIFE) return null;
  if (seenSigs.has(msg.sig)) return null;
  if (!sameHex(msg.sig, await roomSig(p.key, what, state.id, t))) return null;
  seenSigs.set(msg.sig, Date.now());
  for (const [s, at] of seenSigs) if (Date.now() - at > 2 * SIG_LIFE) seenSigs.delete(s);
  return p;
}

async function onGuest(guest, msg) {
  switch (msg.type) {
    case 'link': {
      if (typeof msg.phone !== 'string' || !msg.phone) return;
      if (!code || Date.now() > code.until) {
        return to(guest, { type: 'error', code: 'expired', error: 'That code expired. Scan the new one on the page.' });
      }
      if (!sameHex(msg.proof, await linkProof(code.token, msg.phone))) {
        return to(guest, { type: 'error', code: 'wrong', error: 'Wrong code. Scan the code on the page again.' });
      }
      state.phones[msg.phone] = {
        name: String(msg.name || 'Phone').slice(0, 60),
        key: await phoneKey(code.token, msg.phone),
        linked: Date.now(),
      };
      save();
      newCode();
      showLink = false;
      render();
      return to(guest, { type: 'linked', id: state.id, name: state.name });
    }
    case 'hello': {
      const p = await verify(msg, 'HELLO');
      if (!p) return to(guest, { type: 'unlinked' });
      if (!folder || access !== 'granted') {
        return to(guest, {
          type: 'error',
          code: 'nofolder',
          error: folder ? 'On your computer, allow this page to read your music folder.' : 'On your computer, choose your music folder on this page.',
        });
      }
      to(guest, { type: 'welcome', name: state.name });
      return startPeer(guest, msg.phone);
    }
    case 'unlink': {
      const p = await verify(msg, 'UNLINK');
      if (p) {
        delete state.phones[msg.phone];
        save();
        render();
      }
      return to(guest, { type: 'bye' });
    }
    case 'answer': {
      const s = sessions.get(guest);
      if (!s || typeof msg.sdp !== 'string') return;
      await s.pc.setRemoteDescription({ type: 'answer', sdp: msg.sdp });
      s.remote = true;
      for (const c of s.ice.splice(0)) await s.pc.addIceCandidate(c).catch(() => {});
      return;
    }
    case 'ice': {
      const s = sessions.get(guest);
      if (!s || !msg.c) return;
      if (s.remote) await s.pc.addIceCandidate(msg.c).catch(() => {});
      else s.ice.push(msg.c);
      return;
    }
  }
}

async function startPeer(guest, phone) {
  endSession(guest, true);
  const pc = new RTCPeerConnection({ iceServers: ICE });
  const dc = pc.createDataChannel('sync', { ordered: true });
  const s = { phone, pc, dc, ice: [], remote: false };
  sessions.set(guest, s);
  const act = (activity[phone] = { state: 'connecting', count: 0 });
  render();
  pc.onicecandidate = (e) => {
    if (e.candidate) to(guest, { type: 'ice', c: { candidate: e.candidate.candidate, sdpMid: e.candidate.sdpMid, sdpMLineIndex: e.candidate.sdpMLineIndex } });
  };
  pc.onconnectionstatechange = () => {
    if (pc.connectionState === 'failed') endSession(guest, true);
  };
  dc.onopen = () => {
    act.state = 'connected';
    render();
  };
  dc.onclose = () => endSession(guest, true);
  let last = 0;
  serveChannel(dc, {
    manifest: async () => {
      act.state = 'listing';
      render();
      const l = await rescan();
      if (!l) throw Object.assign(new Error('The music folder could not be read. Choose it again on the page.'), { code: 'nofolder' });
      act.state = 'connected';
      render();
      return l;
    },
    file: async (path) => {
      const h = list?.handles.get(path);
      if (!h) throw Object.assign(new Error('Not in the folder any more.'), { code: 'notfound' });
      act.count++;
      return h.getFile();
    },
    sending: (path, sent, size) => {
      act.state = 'sending';
      act.file = path.split('/').pop();
      act.sent = sent;
      act.size = size;
      const now = performance.now();
      if (now - last > 150 || sent === size) {
        last = now;
        renderPhones();
      }
    },
    done: (copied) => {
      const p = state.phones[phone];
      if (p) {
        p.lastSync = Date.now();
        p.copied = copied;
        save();
      }
      act.state = 'done';
      render();
    },
  });
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  to(guest, { type: 'offer', sdp: offer.sdp });
}

function endSession(guest, close) {
  const s = sessions.get(guest);
  if (!s) return;
  sessions.delete(guest);
  if (close) {
    try {
      s.dc.close();
      s.pc.close();
    } catch {}
  }
  const act = activity[s.phone];
  if (act && act.state !== 'done') delete activity[s.phone];
  render();
}

const mb = (b) => (b >= 1 << 30 ? `${(b / (1 << 30)).toFixed(1)} GB` : `${Math.round(b / (1 << 20))} MB`);

function ago(t) {
  const s = (Date.now() - t) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

function render() {
  const phones = Object.keys(state.phones);
  $('dot').className = `dot ${relayState === 'on' ? 'on' : relayState === 'off' ? 'bad' : ''}`;
  $('conn').textContent = relayState === 'on' ? 'Ready' : relayState === 'off' ? 'Reconnecting…' : 'Connecting…';

  if (!folder) {
    $('folderName').textContent = 'No folder chosen';
    $('folderInfo').textContent = 'Choose the folder with your music. Its subfolders come along.';
    $('pick').textContent = 'Choose folder';
    $('folderNote').hidden = true;
  } else if (access !== 'granted') {
    $('folderName').textContent = folder.name;
    $('folderInfo').textContent = 'Your browser needs your OK to read this folder again.';
    $('pick').textContent = 'Allow access';
    $('folderNote').hidden = true;
  } else {
    $('folderName').textContent = testFolder ? 'Test folder' : folder.name;
    $('pick').textContent = 'Change folder';
    if (list && !scanning) {
      $('folderInfo').textContent = `${list.files.length.toLocaleString()} ${list.files.length === 1 ? 'song' : 'songs'} · ${mb(list.bytes)}`;
      $('folderNote').hidden = list.skipped === 0;
      $('folderNote').textContent =
        list.skipped === 1 ? "1 WMA file is left out: phones can't play it." : `${list.skipped.toLocaleString()} WMA files are left out: phones can't play them.`;
    } else if (scanning) {
      $('folderInfo').textContent = 'Reading the folder…';
    }
  }
  $('pick').className = folder && access === 'granted' ? '' : 'primary';

  const linking = phones.length === 0 || showLink;
  $('linkCard').hidden = !linking;
  $('another').textContent = showLink ? 'Done' : 'Link another phone';
  $('qrVeil').hidden = relayState === 'on';
  $('qrVeil').textContent = relayState === 'off' ? "Can't reach the link service. Check your internet; this page keeps trying." : 'Getting a code…';

  $('phonesCard').hidden = phones.length === 0;
  $('readyCard').hidden = phones.length === 0 || !folder || access !== 'granted' || relayState !== 'on';
  const busy = Object.values(activity).some((a) => a.state !== 'done');
  $('readyText').textContent = busy ? 'Syncing. Keep this tab open until it finishes.' : 'Ready. Tap Sync now in Music Player on your phone.';
  renderPhones();
}

function renderPhones() {
  const ul = $('phones');
  const ids = Object.keys(state.phones).sort((a, b) => (state.phones[b].lastSync ?? 0) - (state.phones[a].lastSync ?? 0));
  ul.innerHTML = ids
    .map((id) => {
      const p = state.phones[id];
      const a = activity[id];
      let line;
      let bar = '';
      if (a?.state === 'connecting') line = 'Connecting…';
      else if (a?.state === 'listing' || a?.state === 'connected') line = 'Comparing songs…';
      else if (a?.state === 'sending') {
        line = `Sending ${esc(a.file)}`;
        bar = `<div class="bar"><i style="width:${a.size ? Math.round((a.sent / a.size) * 100) : 0}%"></i></div>`;
      } else if (p.lastSync) {
        const c = p.copied ?? 0;
        line = `Synced ${ago(p.lastSync)}${c ? ` · ${c.toLocaleString()} new ${c === 1 ? 'song' : 'songs'}` : ''}`;
      } else line = `Linked ${ago(p.linked)} · not synced yet`;
      return `<li><span class="icon" aria-hidden="true"><svg width="16" height="22" viewBox="0 0 16 22" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1" y="1" width="14" height="20" rx="3"/><path d="M6 18h4"/></svg></span>
        <div class="what"><p class="name ellipsis">${esc(p.name)}</p><p class="sub ellipsis">${line}</p>${bar}</div>
        <button class="quiet" data-unlink="${esc(id)}">Unlink</button></li>`;
    })
    .join('');
}

$('phones').addEventListener('click', (e) => {
  const id = e.target?.dataset?.unlink;
  if (!id || !state.phones[id]) return;
  if (!confirm(`Unlink ${state.phones[id].name}? It can't sync from this computer until it is linked again. Songs already on it stay.`)) return;
  delete state.phones[id];
  delete activity[id];
  for (const [g, s] of sessions) if (s.phone === id) endSession(g, true);
  save();
  render();
});

setInterval(renderPhones, 30000);

const supported = (testFolder || 'showDirectoryPicker' in window) && 'RTCPeerConnection' in window && 'indexedDB' in window;
if (!supported) {
  $('unsupported').hidden = false;
  $('conn').textContent = '';
} else {
  $('app').hidden = false;
  newCode();
  render();
  connect();
  loadFolder();
}

window.pulseSync = { state, activity, get list() { return list; }, get link() { return $('qr').dataset.link; }, rescan };
