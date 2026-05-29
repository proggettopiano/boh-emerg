const APP_CACHE = "scorelib-app-v2";
const PDF_CACHE = "scorelib-pdfs-v2";
const APP_SHELL = ["/", "/manifest.json", "/scorelib-icon.svg"];
const MAX_PDF_CACHE_ITEMS = 20;

async function trimCache(cacheName, maxItems) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= maxItems) return;
  await cache.delete(keys[0]);
  await trimCache(cacheName, maxItems);
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(APP_CACHE)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => ![APP_CACHE, PDF_CACHE].includes(key))
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  const isPdfFile = url.pathname.includes("/api/pdfs/") && url.pathname.endsWith("/file");

  if (isPdfFile) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(PDF_CACHE)
              .then((cache) => cache.put(request, copy))
              .then(() => trimCache(PDF_CACHE, MAX_PDF_CACHE_ITEMS))
              .catch(() => {});
          }
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(APP_CACHE).then((cache) => cache.put("/", copy)).catch(() => {});
          return response;
        })
        .catch(() => caches.match("/") || caches.match(request))
    );
    return;
  }

  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(APP_CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
        }
        return response;
      }))
    );
  }
});
