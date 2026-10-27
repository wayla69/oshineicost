/* Oshinei service worker — served at /sw.js by backend/main.py with __BUILD__ replaced
   by the deploy's content hash (so a deploy = a new shell cache) and Cache-Control:
   no-cache (so the browser re-checks it on every visit).

   Two caches:
   - shell-<build>: the page, the versioned /js slices, /tabs.html, vendor, fonts, the
     ui-kit css, the pwa icons. The page is network-first (it carries the js hashes);
     versioned assets (?v=) cache-first; the rest stale-while-revalidate.
   - api-v1: the GET endpoints the phone summary reads, network-first. When the network
     is gone the last copy is served with X-SW-Cache: <when it was stored> and the
     page is told (postMessage offline-fallback) so it can show "ออฟไลน์ · ข้อมูลเมื่อ".
     Nothing else under /api is cached (never /api/auth/me); no POST ever is; logout, a sign-in and an expired session clear it (message
     clear-api-cache).

   A new build: the page compares its meta app-build with GET /api/meta/build, calls registration.update() and waits
   until this worker (skipWaiting on install) has activated - the old shell caches are deleted in activate - before
   it reloads (js/01-core.js appUpdateCheck).

   Push: the morning brief arrives as a notification (backend/routers/push_api.py);
   a tap opens or focuses the app. */
'use strict';

const BUILD = '__BUILD__';
// the prebuilt Tailwind sheet with its ?v=<content hash> (backend/main.py fills it in): precached on install, so a new
// build's first offline open still has its styles (it replaced the in-browser runtime vendor/tailwind.js, 2026-10-26)
const TW_CSS = '__TW_CSS__';
const SHELL = 'oshinei-shell-' + BUILD;
// v2 (2026-10-26): v1 held /api/auth/me — who is signed in — which a later visitor on the same phone could be served
// offline; activate deletes every cache that is not SHELL or API, so the old v1 copy goes with this change.
const API = 'oshinei-api-v2';
const API_MAX = 80;

// /api/auth/me is deliberately NOT here: the session's identity is never answered from a cache.
const API_OK = [
  /^\/api\/reports\/management(\/months)?(\?|$)/, /^\/api\/home\/brief(\?|$)/, /^\/api\/home\/branch-scorecard(\?|$)/,
  /^\/api\/sales\/daily(\?|$)/, /^\/api\/sales\/top_ordered(\?|$)/, /^\/api\/sales\/cost-per-head(\?|$)/,
  /^\/api\/forecast(\?|$)/, /^\/api\/analysis\/(table-turnover|daypart-economics)(\?|$)/,
  /^\/api\/feedback\/items(\?|$)/, /^\/api\/months(\?|$)/, /^\/api\/branches(\?|$)/,
  /^\/api\/data-coverage(\?|$)/,
];
const SHELL_PREFIX = /^\/(js\/|vendor\/|docs\/ui-kit\/|pwa\/|tabs\.html)/;

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(['/', '/pwa/manifest.json', TW_CSS]).catch(() => {})).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k !== SHELL && k !== API).map((k) => caches.delete(k))
  )).then(() => self.clients.claim()));
});

function withStamp(resp) {
  // a copy with the time it was stored, read back as X-SW-Cache when served offline
  const h = new Headers(resp.headers);
  h.set('X-SW-Stored', new Date().toISOString());
  return resp.blob().then((b) => new Response(b, { status: resp.status, statusText: resp.statusText, headers: h }));
}

async function trimApi(cache) {
  const keys = await cache.keys();
  if (keys.length <= API_MAX) return;
  for (const k of keys.slice(0, keys.length - API_MAX)) await cache.delete(k);
}

async function tellClients(msg) {
  const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  all.forEach((c) => c.postMessage(msg));
}

async function apiNetworkFirst(req) {
  const cache = await caches.open(API);
  try {
    const resp = await fetch(req);
    if (resp && resp.ok) {
      const stamped = await withStamp(resp.clone());
      await cache.put(req, stamped);
      trimApi(cache);
    }
    return resp;
  } catch (err) {
    const hit = await cache.match(req);
    if (hit) {
      const stored = hit.headers.get('X-SW-Stored') || '';
      const h = new Headers(hit.headers); h.set('X-SW-Cache', stored);
      tellClients({ type: 'offline-fallback', url: new URL(req.url).pathname, cachedAt: stored });
      return new Response(await hit.blob(), { status: 200, headers: h });
    }
    throw err;
  }
}

async function shellCacheFirst(req) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(req);
  if (hit) return hit;
  const resp = await fetch(req);
  if (resp && resp.ok) cache.put(req, resp.clone());
  return resp;
}

async function shellStaleWhileRevalidate(req) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(req);
  const net = fetch(req).then((resp) => { if (resp && resp.ok) cache.put(req, resp.clone()); return resp; }).catch(() => null);
  return hit || (await net) || Response.error();
}

// The page. A deploy restarts the server for a few seconds: a failed fetch (or a 5xx from the proxy) is tried once more
// after 2 s before the cached page is served, so a reload for a new version does not land on the old cached '/'.
async function pageFetchOnce(req) {
  const resp = await fetch(req);
  if (resp && resp.status >= 500) throw Object.assign(new Error('page ' + resp.status), { resp });
  return resp;
}
async function pageNetworkFirst(req) {
  const cache = await caches.open(SHELL);
  let last = null;
  for (let i = 0; i < 2; i++) {
    try {
      const resp = await pageFetchOnce(req);
      // only the app shell itself is stored as '/': any other page a navigation reaches (/docs/…, a report link,
      // /survey/…) would otherwise overwrite the offline shell with itself
      if (resp && resp.ok && new URL(req.url).pathname === '/') cache.put('/', resp.clone());
      return resp;
    } catch (err) {
      last = err;
      if (i === 0) await new Promise((r) => setTimeout(r, 2000));
    }
  }
  const hit = await cache.match('/');
  if (hit) { tellClients({ type: 'offline-fallback', url: '/', cachedAt: '' }); return hit; }
  if (last && last.resp) return last.resp;
  throw last;
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const path = url.pathname + (url.search || '');
  if (url.pathname.startsWith('/api/')) {
    if (API_OK.some((re) => re.test(path))) e.respondWith(apiNetworkFirst(req));
    return;                                             // every other API call goes straight through
  }
  if (req.mode === 'navigate' || url.pathname === '/') { e.respondWith(pageNetworkFirst(req)); return; }
  if (SHELL_PREFIX.test(url.pathname)) {
    e.respondWith(url.searchParams.has('v') ? shellCacheFirst(req) : shellStaleWhileRevalidate(req));
  }
});

self.addEventListener('message', (e) => {
  const d = e.data || {};
  if (d.type === 'clear-api-cache') e.waitUntil(caches.delete(API));
  if (d.type === 'skip-waiting') self.skipWaiting();             // the page found a new build (js/01-core.js appUpdateCheck)
});

// ── push ──
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { title: 'Oshinei', body: e.data ? e.data.text() : '' }; }
  const title = d.title || 'Oshinei';
  e.waitUntil(self.registration.showNotification(title, {
    body: d.body || '', icon: '/pwa/icon-192.png', badge: '/pwa/icon-192.png',
    tag: d.tag || 'oshinei', renotify: true, data: { url: d.url || '/' }, lang: 'th',
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((all) => {
    const open = all.find((c) => c.url.startsWith(self.location.origin));
    if (open) { open.postMessage({ type: 'notification-open', url }); return open.focus(); }
    return self.clients.openWindow(url);
  }));
});

self.addEventListener('pushsubscriptionchange', (e) => {
  // the push service rotated the subscription: subscribe again with the server's key and store it
  e.waitUntil((async () => {
    try {
      const r = await fetch('/api/push/vapid-public'); const j = await r.json();
      if (!j || !j.key) return;
      const sub = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8(j.key) });
      await fetch('/api/push/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...sub.toJSON(), ua: 'sw-resubscribe' }) });
    } catch (err) { /* next visit's toggle will fix it */ }
  })());
});

function urlB64ToUint8(s) {
  const pad = '='.repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}
