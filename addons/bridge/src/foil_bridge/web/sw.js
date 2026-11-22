const cacheName = "foil-bridge-1";
const token = new URL(self.location.href).searchParams.get("token") || "";

function withToken(path) {
  return path + "?token=" + encodeURIComponent(token);
}

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    fetch(event.request).catch(async () => {
      const cache = await caches.open(cacheName);
      const saved = await cache.match(withToken("/offline"));
      if (saved) return saved;
      return new Response("This machine can't be reached. Check Tailscale.", {
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      });
    }),
  );
});
