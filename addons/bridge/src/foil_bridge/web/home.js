const token = new URLSearchParams(location.search).get("token") || "";
const acted = new Set();

function headers(extra) {
  const result = { Authorization: "Bearer " + token };
  if (extra) Object.assign(result, extra);
  return result;
}

function line(parent, text, className) {
  const node = document.createElement("p");
  if (className) node.className = className;
  node.textContent = text;
  parent.append(node);
  return node;
}

function linkFor(parent, item) {
  const anchor = document.createElement("a");
  const query = new URLSearchParams({ target: item.target, token });
  anchor.href = "/evidence?" + query.toString();
  anchor.textContent = item.path || item.target;
  parent.append(anchor);
}

function showQuestions(rows) {
  const box = document.querySelector("#questions");
  box.replaceChildren();
  if (!rows.length) {
    line(box, "None.", "quiet");
    return;
  }
  for (const row of rows) {
    const card = document.createElement("article");
    card.className = "card";
    line(card, row.fleet ? row.project + " / " + row.fleet : row.project, "who");
    line(card, row.ask || row.id);
    if (row.why) line(card, row.why, "quiet");
    if (row.recommended) line(card, "Recommended: " + row.recommended, "quiet");
    for (const item of row.links || []) linkFor(card, item);
    if (row.state === "answered") {
      line(card, "answered, waiting for lead");
      if (row.answer) line(card, row.answer);
    } else {
      for (const option of row.options || []) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = option;
        button.addEventListener("click", () => reply(row, option, card));
        card.append(button);
      }
      const words = document.createElement("textarea");
      words.rows = 2;
      const label = document.createElement("label");
      label.textContent = "Or reply in words";
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Send this reply";
      button.addEventListener("click", () => reply(row, words.value, card));
      card.append(label, words, button);
    }
    box.append(card);
  }
}

function showMessages(rows) {
  const list = document.querySelector("#thread");
  list.replaceChildren();
  if (!rows.length) {
    const item = document.createElement("li");
    item.textContent = "No messages yet.";
    list.append(item);
    return;
  }
  for (const row of rows) {
    const item = document.createElement("li");
    const who = document.createElement("p");
    who.className = "who";
    who.textContent = row.from + "  " + row.time;
    item.append(who);
    if (row.body) line(item, row.body);
    if (row.queued) line(item, "queued behind " + row.queued, "quiet");
    for (const link of row.links || []) linkFor(item, link);
    if (row.card) showCard(item, row);
    list.append(item);
  }
}

function showCard(item, row) {
  const card = document.createElement("div");
  card.className = "card";
  for (const field of row.card.fields) line(card, field.key + ": " + field.value);
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = acted.has(row.id) ? "Done." : row.card.label;
  button.disabled = acted.has(row.id);
  button.addEventListener("click", () => act(row.id, button));
  card.append(button);
  item.append(card);
}

function showWorking(working) {
  const section = document.querySelector("#working");
  const tail = document.querySelector("#tail");
  section.hidden = !working.running;
  tail.textContent = working.tail || "The turn is running.";
}

async function readError(response) {
  try {
    const body = await response.json();
    return body.error || "This machine refused the page.";
  } catch {
    return "This machine refused the page.";
  }
}

async function load() {
  const response = await fetch("/v1/home", { headers: headers() });
  if (!response.ok) {
    document.querySelector("#lede").textContent = "This machine refused the page.";
    return;
  }
  const body = await response.json();
  showWorking(body.working);
  const questions = document.querySelector("#questions");
  const field = document.activeElement;
  const typing = field && field.tagName === "TEXTAREA" && questions.contains(field);
  if (!typing) showQuestions(body.questions);
  showMessages(body.messages);
  if (body.working.running) schedule();
}

let timer = 0;
function schedule() {
  if (timer) return;
  timer = window.setTimeout(() => {
    timer = 0;
    load();
  }, 2000);
}

async function post(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

async function reply(row, text, card) {
  try {
    await post("/v1/answer", {
      project: row.project,
      fleet: row.fleet,
      id: row.id,
      text,
    });
    await load();
  } catch (error) {
    line(card, error.message, "quiet");
  }
}

async function act(id, button) {
  button.disabled = true;
  try {
    await post("/v1/act", { id });
    acted.add(id);
    button.textContent = "Done.";
  } catch (error) {
    button.disabled = false;
    button.textContent = error.message;
  }
}

document.querySelector("#say").addEventListener("submit", async (event) => {
  event.preventDefault();
  const field = document.querySelector("#message");
  const note = document.querySelector("#say-error");
  note.textContent = "";
  try {
    await post("/v1/send", { text: field.value });
    field.value = "";
    await load();
  } catch (error) {
    note.textContent = error.message;
  }
});

load();

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
  source.onmessage = () => load();
}
