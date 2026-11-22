const token = new URLSearchParams(location.search).get("token") || "";

function headers(extra) {
  const result = { Authorization: "Bearer " + token };
  if (extra) Object.assign(result, extra);
  return result;
}

async function readJSON(path) {
  const response = await fetch(path, { headers: headers() });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

function showDevices(rows) {
  const list = document.querySelector("#devices");
  list.replaceChildren();
  if (!rows.length) {
    const item = document.createElement("li");
    item.textContent = "None yet.";
    list.append(item);
    return;
  }
  for (const row of rows) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = row.id;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Revoke";
    button.addEventListener("click", async () => {
      const response = await fetch(
        "/v1/devices/" + encodeURIComponent(row.id) + "/revoke",
        { method: "POST", headers: headers() },
      );
      if (response.ok) loadDevices();
    });
    item.append(name, button);
    list.append(item);
  }
}

async function loadDevices() {
  showDevices(await readJSON("/v1/devices"));
}

async function loadSession() {
  const session = await readJSON("/v1/session");
  if (session.kind === "desk") return;
  document.querySelector("#mint").hidden = true;
  document.querySelector("#pair").hidden = true;
  document.querySelector("#lede").textContent = "Paired with this machine.";
}

document.querySelector("#mint").addEventListener("click", async () => {
  const response = await fetch("/v1/pairing", { method: "POST", headers: headers() });
  if (!response.ok) return;
  const body = await response.json();
  const code = document.querySelector("#code");
  code.hidden = false;
  code.textContent = body.code;
  const note = document.querySelector("#pair-note");
  note.hidden = false;
  const image = document.querySelector("#qr");
  if (body.url) {
    image.hidden = false;
    image.src =
      "/v1/pairing/" + encodeURIComponent(body.code) + "/qr.svg?token=" + encodeURIComponent(token);
    image.alt = "The pairing address";
    note.textContent = "Open this on the phone. It works once.";
  } else {
    image.hidden = true;
    note.textContent = "Run tailscale serve, then show a code again. This one still works once.";
  }
});

loadSession().then(loadDevices).catch(() => {
  document.querySelector("#lede").textContent = "This machine refused the page.";
});

if (token && "serviceWorker" in navigator && "caches" in window) {
  const worker = "sw.js?token=" + encodeURIComponent(token);
  caches
    .open("foil-bridge-1")
    .then((cache) => cache.add("/offline?token=" + encodeURIComponent(token)))
    .then(() => navigator.serviceWorker.register(worker))
    .catch(() => {});
}

if (token && "EventSource" in window) {
  const source = new EventSource("/v1/events?token=" + encodeURIComponent(token));
  source.onmessage = (event) => {
    let body;
    try {
      body = JSON.parse(event.data);
    } catch {
      return;
    }
    const line = document.querySelector("#changed");
    if (!line || !body.id) return;
    if (body.id === "*") {
      line.textContent = "Several files changed.";
      return;
    }
    const item = "/v1/items/" + body.id.split("/").map(encodeURIComponent).join("/");
    fetch(item, { headers: headers() })
      .then((response) => (response.ok ? response.json() : null))
      .then((row) => {
        if (row && row.id) line.textContent = "Updated " + row.id;
      })
      .catch(() => {});
  };
}
