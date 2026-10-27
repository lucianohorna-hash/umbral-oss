const CACHE = 'umbral-shell-v4';
// Only the public app shell is cached. No /api, auth state, canon, turns, or backups.
const ASSETS = new Set([
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/auth.js',
  '/static/mobile.js',
  '/static/chatgpt-plan.js',
  '/static/manifest.webmanifest',
  '/static/icon-192.png',
  '/static/icon-512.png',
]);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll([...ASSETS])));
  self.skipWaiting();
});

self.addEventListener('activate', event => event.waitUntil(
  caches.keys()
    .then(keys => Promise.all(keys
      .filter(key => key.startsWith('umbral-shell-') && key !== CACHE)
      .map(key => caches.delete(key))))
    .then(() => self.clients.claim()),
));

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || !ASSETS.has(url.pathname)) return;

  // Network first: always try the current shell, with a cached copy only as fallback.
  event.respondWith(fetch(request).then(response => {
    if (response.ok && response.type === 'basic') {
      const copy = response.clone();
      event.waitUntil(caches.open(CACHE).then(cache => cache.put(request, copy)));
    }
    return response;
  }).catch(async () => (await caches.match(request)) || Response.error()));
});
