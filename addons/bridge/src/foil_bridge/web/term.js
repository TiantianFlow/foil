const token = new URLSearchParams(location.search).get("token") || "";
const term = document.querySelector("#term");
const note = document.querySelector("#term-note");
const openButton = document.querySelector("#term-open");
const closeButton = document.querySelector("#term-close");
const screen = document.querySelector("#term-screen");

let socket = null;
let raw = "";

function render(value) {
  const text = value
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\u001b\][^\u0007]*(?:\u0007|\u001b\\)/g, "")
    .replace(/\u001b./g, "")
    .replace(/\r/g, "");
  let out = "";
  for (const char of text) {
    if ((char === "\b" || char === "\u007f") && out && !out.endsWith("\n")) {
      out = out.slice(0, -1);
    } else if (char !== "\u0007") {
      out += char;
    }
  }
  return out;
}

function paint(chunk) {
  raw += chunk;
  if (raw.length > 100000) raw = raw.slice(-80000);
  screen.textContent = render(raw);
  screen.scrollTop = screen.scrollHeight;
}

function send(text) {
  if (!socket || socket.readyState !== 1) return;
  socket.send(new TextEncoder().encode(text));
}

function shut() {
  if (socket) socket.close();
}

if (openButton && closeButton && screen && term) {
  const decoder = new TextDecoder("utf-8");
  openButton.addEventListener("click", () => {
    if (socket) return;
    const url = new URL("/v1/terminal", location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    url.search = "";
    url.searchParams.set("token", token);
    url.searchParams.set("path", term.dataset.path || "");
    let opened = false;
    socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      opened = true;
      raw = "";
      screen.textContent = "";
      screen.hidden = false;
      openButton.hidden = true;
      closeButton.hidden = false;
      if (note) note.textContent = "";
      screen.focus();
    };
    socket.onmessage = (event) => {
      const bytes = event.data instanceof ArrayBuffer ? new Uint8Array(event.data) : event.data;
      paint(typeof bytes === "string" ? bytes : decoder.decode(bytes, { stream: true }));
    };
    socket.onclose = () => {
      socket = null;
      openButton.hidden = false;
      closeButton.hidden = true;
      if (!opened) {
        screen.hidden = true;
        if (note) note.textContent = "This machine refused the terminal.";
      }
    };
  });
  closeButton.addEventListener("click", () => {
    shut();
    screen.hidden = true;
    raw = "";
    screen.textContent = "";
  });
  window.addEventListener("pagehide", shut);
  screen.addEventListener("keydown", (event) => {
    let text = "";
    if (event.key === "Enter") text = "\r";
    else if (event.key === "Backspace") text = "\u007f";
    else if (event.key === "Tab") text = "\t";
    else if (event.ctrlKey && event.key.length === 1) {
      const code = event.key.toLowerCase().charCodeAt(0);
      if (code >= 97 && code <= 122) text = String.fromCharCode(code - 96);
    } else if (event.key.length === 1 && !event.metaKey && !event.ctrlKey) {
      text = event.key;
    }
    if (!text) return;
    send(text);
    event.preventDefault();
  });
  screen.addEventListener("paste", (event) => {
    const text = event.clipboardData ? event.clipboardData.getData("text") : "";
    if (text) send(text);
    event.preventDefault();
  });
}

for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy || "");
      button.textContent = "Copied";
    } catch {
      button.textContent = "Copy failed";
    }
  });
}
