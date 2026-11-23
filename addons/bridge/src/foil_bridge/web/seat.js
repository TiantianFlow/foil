const token = new URLSearchParams(location.search).get("token") || "";
const chat = document.querySelector("#chat");
const project = chat.dataset.project;
const fleet = chat.dataset.fleet;
const seat = chat.dataset.seat;

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
  if (!rows.length) return;
  for (const row of rows) {
    const card = document.createElement("article");
    card.className = "card";
    line(card, row.ask || row.id);
    if (row.why) line(card, row.why, "quiet");
    if (row.recommended) line(card, "Recommended: " + row.recommended, "quiet");
    for (const item of row.links || []) linkFor(card, item);
    if (row.state === "answered") {
      line(card, "answered, waiting for lead", "quiet");
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
    if (row.state) line(item, row.state, "quiet");
    list.append(item);
  }
}

async function readError(response) {
  try {
    const body = await response.json();
    return body.error || "This machine refused the page.";
  } catch {
    return "This machine refused the page.";
  }
}

function query() {
  const params = new URLSearchParams({ project, fleet, seat });
  return "/v1/chat?" + params.toString();
}

async function load() {
  const response = await fetch(query(), { headers: headers() });
  const note = document.querySelector("#chat-note");
  if (!response.ok) {
    note.textContent = "This machine refused the page.";
    return;
  }
  const body = await response.json();
  note.textContent = body.notice || "";
  const questions = document.querySelector("#questions");
  const field = document.activeElement;
  const typing = field && field.tagName === "TEXTAREA" && questions.contains(field);
  if (!typing) showQuestions(body.questions || []);
  showMessages(body.messages || []);
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
    await post("/v1/answer", { project, fleet, id: row.id, text });
    await load();
  } catch (error) {
    line(card, error.message, "quiet");
  }
}

document.querySelector("#say").addEventListener("submit", async (event) => {
  event.preventDefault();
  const field = document.querySelector("#message");
  const note = document.querySelector("#say-error");
  note.textContent = "";
  try {
    await post("/v1/chat", { project, fleet, seat, text: field.value });
    field.value = "";
    await load();
  } catch (error) {
    note.textContent = error.message;
  }
});

load();

if (token && "EventSource" in window) {
  const source = new EventSource("/v1/events?token=" + encodeURIComponent(token));
  source.onmessage = () => load();
}
