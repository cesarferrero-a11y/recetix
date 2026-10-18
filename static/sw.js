// Service worker mínimo: hace la app instalable. Nunca cachea la API.
const SHELL = 'recetas-v1';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(['/manifest.json', '/icon-192.png'])).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.pathname.startsWith('/api/')) return;
  // red primero; si no hay conexión, lo último cacheado
  e.respondWith(fetch(e.request).then(r => {
    const copia = r.clone();
    caches.open(SHELL).then(c => c.put(e.request, copia)).catch(() => {});
    return r;
  }).catch(() => caches.match(e.request)));
});
