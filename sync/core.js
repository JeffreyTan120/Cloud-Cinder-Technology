
export const AUDIO = ['.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wav'];
export const UNPLAYABLE = ['.wma', '.ape', '.aiff', '.aif'];
export const LINK_LIFE = 5 * 60 * 1000;
export const SIG_LIFE = 2 * 60 * 1000;
export const CHUNK = 64 * 1024;
export const WINDOW = 8 * 1024 * 1024;

export function randomToken(bytes = 16) {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...b)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
}

const enc = new TextEncoder();

export async function hmacHex(key, message) {
  const k = await crypto.subtle.importKey('raw', enc.encode(key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', k, enc.encode(message)));
  return Array.from(sig, (x) => x.toString(16).padStart(2, '0')).join('');
}

export const phoneKey = (token, phoneId) => hmacHex(token, `pulse-sync-phone:${phoneId}`);

export const linkProof = (token, phoneId) => hmacHex(token, `pulse-sync-link:${phoneId}`);

export const roomSig = (key, what, room, time) => hmacHex(key, `${what}\n${room}\n${time}`);

export function sameHex(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export function linkUri({ relay, room, name, token }) {
  const q = new URLSearchParams({ r: relay, id: room, n: name, t: token });
  return `pulse://link-web?${q}`;
}

const ext = (name) => {
  const i = name.lastIndexOf('.');
  return i < 0 ? '' : name.slice(i).toLowerCase();
};

export async function scanFolder(dir, onCount) {
  const files = [];
  const handles = new Map();
  let skipped = 0;
  let bytes = 0;
  async function walk(d, prefix) {
    for await (const [name, h] of d.entries()) {
      if (name.startsWith('.') || name.startsWith('$')) continue;
      if (h.kind === 'directory') {
        await walk(h, `${prefix}${name}/`);
        continue;
      }
      const e = ext(name);
      if (UNPLAYABLE.includes(e)) skipped++;
      if (!AUDIO.includes(e)) continue;
      try {
        const f = await h.getFile();
        const p = prefix + name;
        files.push({ p, s: f.size, m: f.lastModified });
        handles.set(p, h);
        bytes += f.size;
        if (onCount && files.length % 50 === 0) onCount(files.length);
      } catch {
      }
    }
  }
  await walk(dir, '');
  files.sort((a, b) => (a.p < b.p ? -1 : a.p > b.p ? 1 : 0));
  return { files, skipped, bytes, handles };
}

export function serveChannel(dc, source) {
  dc.binaryType = 'arraybuffer';
  dc.bufferedAmountLowThreshold = 1024 * 1024;
  let acked = 0;
  let wake = null;
  let closed = false;
  const queue = [];
  let busy = false;

  const nudge = () => {
    const w = wake;
    wake = null;
    w?.();
  };
  dc.addEventListener('bufferedamountlow', nudge);
  dc.addEventListener('close', () => {
    closed = true;
    nudge();
  });

  dc.addEventListener('message', (e) => {
    if (typeof e.data !== 'string') return;
    let m;
    try {
      m = JSON.parse(e.data);
    } catch {
      return;
    }
    if (typeof m.ack === 'number') {
      acked = m.ack;
      nudge();
      return;
    }
    queue.push(m);
    pump();
  });

  async function pump() {
    if (busy) return;
    busy = true;
    while (queue.length && !closed) {
      const m = queue.shift();
      try {
        await answer(m);
      } catch (err) {
        if (closed) break;
        dc.send(JSON.stringify({ id: m.id, ok: false, error: String(err?.message ?? err), code: err?.code ?? 'error' }));
      }
    }
    busy = false;
  }

  async function sendBytes(id, bytes, size, path) {
    dc.send(JSON.stringify({ id, ok: true, size }));
    acked = 0;
    let sent = 0;
    const reader = bytes.getReader();
    let pending = new Uint8Array(0);
    for (;;) {
      const { done, value } = await reader.read();
      if (value) pending = pending.length ? concat(pending, value) : value;
      while (pending.length >= CHUNK || (done && pending.length > 0)) {
        const n = Math.min(CHUNK, pending.length);
        while (!closed && (dc.bufferedAmount > 4 * 1024 * 1024 || sent - acked > WINDOW)) {
          await new Promise((ok) => {
            wake = ok;
            setTimeout(ok, 250);
          });
        }
        if (closed) {
          reader.cancel().catch(() => {});
          return;
        }
        dc.send(pending.slice(0, n));
        pending = pending.subarray(n);
        sent += n;
        if (path) source.sending?.(path, sent, size);
      }
      if (done) break;
    }
    if (sent !== size) throw Object.assign(new Error('The file changed while it was being sent.'), { code: 'changed' });
  }

  async function answer(m) {
    if (m.op === 'manifest') {
      const list = await source.manifest();
      const json = enc.encode(JSON.stringify({ files: list.files, skipped: list.skipped, folder: list.folder }));
      await sendBytes(m.id, new Blob([json]).stream(), json.length);
    } else if (m.op === 'file') {
      const f = await source.file(m.path);
      await sendBytes(m.id, f.stream(), f.size, m.path);
    } else if (m.op === 'done') {
      await source.done?.(m.copied | 0);
      dc.send(JSON.stringify({ id: m.id, ok: true, size: 0 }));
    } else {
      throw Object.assign(new Error('Unknown request.'), { code: 'unknown' });
    }
  }
}

function concat(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a);
  out.set(b, a.length);
  return out;
}

export function browserName(ua = globalThis.navigator?.userAgent ?? '') {
  const b = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : 'Browser';
  const o = /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'Mac' : /CrOS/.test(ua) ? 'Chromebook' : /Linux/.test(ua) ? 'Linux' : '';
  return o ? `${b} on ${o}` : b;
}
