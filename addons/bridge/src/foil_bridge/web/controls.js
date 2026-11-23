const token = new URLSearchParams(location.search).get("token") || "";
const root = document.querySelector("#controls");

function headers() {
  return {
    Authorization: "Bearer " + token,
    "Content-Type": "application/json",
  };
}

async function readError(response) {
  try {
    const body = await response.json();
    return body.error || "This machine refused the page.";
  } catch {
    return "This machine refused the page.";
  }
}

async function send(payload) {
  const response = await fetch("/v1/control", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

function fields(button) {
  const op = button.dataset.op;
  const payload = { op, project: root.dataset.project, fleet: root.dataset.fleet };
  if (button.dataset.seat) payload.seat = button.dataset.seat;
  if (button.dataset.role) payload.role = button.dataset.role;
  if (op === "fleet") {
    payload.fleet = document.querySelector("#goal-fleet").value;
    const task = document.querySelector("#goal-task").value;
    if (task) payload.task = task;
  }
  return payload;
}

async function run(button) {
  const note = document.querySelector("#control-error");
  note.textContent = "";
  const payload = fields(button);
  const label = button.textContent.trim();
  if (button.dataset.confirm === "yes") {
    if (!window.confirm(label + "?")) return;
    payload.confirm = true;
  }
  button.disabled = true;
  try {
    await send(payload);
    location.reload();
  } catch (error) {
    if (error.message === "foil: confirm this" && window.confirm(label + "?")) {
      try {
        await send(Object.assign({}, payload, { confirm: true }));
        location.reload();
        return;
      } catch (again) {
        note.textContent = again.message;
      }
    } else if (error.message !== "foil: confirm this") {
      note.textContent = error.message;
    }
    button.disabled = false;
  }
}

if (root) {
  for (const button of root.querySelectorAll("button")) {
    button.addEventListener("click", () => run(button));
  }
}
