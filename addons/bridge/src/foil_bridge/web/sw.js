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

self.addEventListener("push", (event) => {
  let label = "Foil";
  let card = "";
  try {
    const data = event.data ? event.data.json() : {};
    if (data && typeof data.label === "string" && data.label) label = data.label;
    if (data && typeof data.card === "string") card = data.card;
  } catch (error) {
    label = "Foil";
  }
  event.waitUntil(
    self.registration.showNotification(label, {
      body: "",
      tag: card || "foil",
      data: { card },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const card = (event.notification.data && event.notification.data.card) || "";
  const url = new URL("/", self.location.origin);
  if (token) url.searchParams.set("token", token);
  if (card) url.searchParams.set("card", card);
  event.waitUntil(self.clients.openWindow(url.href));
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
