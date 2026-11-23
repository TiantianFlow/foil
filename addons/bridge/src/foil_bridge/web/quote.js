const token = new URLSearchParams(location.search).get("token") || "";

function headers() {
  return {
    Authorization: "Bearer " + token,
    "Content-Type": "application/json",
  };
}

function mark(items, start, end) {
  const first = Number(start.value);
  const last = Number(end.value);
  items.forEach((item, index) => {
    const line = index + 1;
    const on = Number.isInteger(first) && Number.isInteger(last) && line >= first && line <= last;
    item.classList.toggle("picked", on);
  });
}

async function send(box, note) {
  const start = Number(box.querySelector("[data-start]").value);
  const end = Number(box.querySelector("[data-end]").value);
  const text = box.querySelector("textarea").value;
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) {
    note.textContent = "Choose a line range.";
    return;
  }
  if (!text.trim()) {
    note.textContent = "Add a sentence.";
    return;
  }
  const response = await fetch("/v1/quote", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      project: box.dataset.project,
      fleet: box.dataset.fleet,
      path: box.dataset.path,
      start,
      end,
      text,
    }),
  });
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok) {
    note.textContent = body.error || "Could not send.";
    return;
  }
  note.textContent = "Sent to " + body.to;
  box.querySelector("textarea").value = "";
}

for (const box of document.querySelectorAll("[data-quote]")) {
  const items = [...box.querySelectorAll("ol.pick li")];
  const start = box.querySelector("[data-start]");
  const end = box.querySelector("[data-end]");
  const note = box.querySelector("[data-note]");
  let anchor = 1;
  items.forEach((item, index) => {
    item.addEventListener("click", (event) => {
      const line = index + 1;
      if (event.shiftKey) {
        start.value = String(Math.min(anchor, line));
        end.value = String(Math.max(anchor, line));
      } else {
        anchor = line;
        start.value = String(line);
        end.value = String(line);
      }
      mark(items, start, end);
    });
  });
  start.addEventListener("input", () => mark(items, start, end));
  end.addEventListener("input", () => mark(items, start, end));
  box.querySelector("form").addEventListener("submit", (event) => {
    event.preventDefault();
    note.textContent = "";
    send(box, note);
  });
}
