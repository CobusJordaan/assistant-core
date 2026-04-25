// Draadloze AI — Service Worker (app shell caching)
const CACHE_NAME = 'draadloze-ai-v1';
const SHELL_ASSETS = [
    '/static/portal/portal.css',
    '/static/portal/chat.js',
];

self.addEventListener('install', (e) => {
    e.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_ASSETS))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (e) => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (e) => {
    const url = new URL(e.request.url);

    // Only cache static assets — never cache API calls or HTML pages
    if (url.pathname.startsWith('/static/portal/')) {
        e.respondWith(
            caches.match(e.request).then(cached => cached || fetch(e.request))
        );
        return;
    }

    // Everything else: network first (pages, API)
    e.respondWith(fetch(e.request));
});
