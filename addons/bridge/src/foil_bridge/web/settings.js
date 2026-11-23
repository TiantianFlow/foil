const token = new URLSearchParams(location.search).get("token") || "";
const pageProject = new URLSearchParams(location.search).get("project") || "";
const pageFleet = new URLSearchParams(location.search).get("fleet") || "";

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

function href(project, fleet) {
  const query = new URLSearchParams({ token });
  if (project) query.set("project", project);
  if (fleet) query.set("fleet", fleet);
  return "/settings?" + query.toString();
}

async function readBody(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

async function post(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await readBody(response);
    const error = new Error(body.error || "This machine refused the page.");
    error.diff = body.diff || null;
    throw error;
  }
  return response.json();
}

function pairLabel(entry) {
  return entry.model ? entry.id + "/" + entry.model : entry.id;
}

function orderOf(list) {
  return [...list.querySelectorAll("li")].map((item) => item.dataset.pair).join(",");
}

function showDiff(parent, diff) {
  line(parent, "before: " + diff.before, "quiet");
  line(parent, "now: " + diff.now, "quiet");
  line(parent, "after: " + diff.after, "quiet");
}

function bindDrag(list, onChange) {
  let dragged = null;
  list.addEventListener("dragstart", (event) => {
    const item = event.target.closest("li");
    if (!item || item.parentElement !== list) return;
    dragged = item;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", item.dataset.pair || "");
  });
  list.addEventListener("dragover", (event) => {
    event.preventDefault();
    const item = event.target.closest("li");
    if (!dragged || !item || item === dragged || item.parentElement !== list) return;
    const bounds = item.getBoundingClientRect();
    const after = event.clientY > bounds.top + bounds.height / 2;
    list.insertBefore(dragged, after ? item.nextSibling : item);
  });
  list.addEventListener("drop", (event) => {
    event.preventDefault();
    dragged = null;
    onChange();
  });
  list.addEventListener("dragend", () => {
    dragged = null;
    onChange();
  });
}

function showRole(row, bases) {
  const section = document.createElement("section");
  section.className = "card";
  line(section, row.role);
  if (row.description) line(section, row.description, "quiet");
  line(section, "layer: " + (row.layer || ""), "quiet");
  if (row.error || !row.harness) {
    line(section, "invalid", "quiet");
    return section;
  }
  const original = row.harness.map(pairLabel).join(",");
  const list = document.createElement("ol");
  list.className = "pairs";
  list.dataset.role = row.role;
  row.harness.forEach((entry, index) => {
    const item = document.createElement("li");
    item.draggable = true;
    item.dataset.pair = pairLabel(entry);
    item.textContent = item.dataset.pair;
    const source = document.createElement("span");
    source.textContent = (row.preset || [])[index] || "";
    item.append(source);
    list.append(item);
  });
  const mapping = document.createElement("p");
  mapping.className = "quiet";
  mapping.textContent = "mapping: " + row.mapping;
  section.append(list, mapping);
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Apply this order";
  button.hidden = true;
  const note = document.createElement("div");
  bindDrag(list, () => {
    button.hidden = orderOf(list) === original;
  });
  button.addEventListener("click", () => applyOrder(row, list, bases, original, button, note));
  section.append(button, note);
  return section;
}

async function applyOrder(row, list, bases, original, button, note) {
  note.replaceChildren();
  const layer = row.mapping;
  const payload = {
    role: row.role,
    field: "harness",
    value: orderOf(list),
    base: bases[layer] || "",
    layer,
    before: original,
  };
  if (pageProject) payload.project = pageProject;
  if (layer === "fleet") payload.fleet = pageFleet;
  button.disabled = true;
  try {
    await post("/v1/settings", payload);
    await load();
  } catch (error) {
    button.disabled = false;
    line(note, error.message, "quiet");
    if (error.diff) showDiff(note, error.diff);
  }
}

function showScopes(body) {
  const nav = document.querySelector("#scopes");
  nav.replaceChildren();
  const global = document.createElement("a");
  global.href = href("", "");
  global.textContent = "Global";
  nav.append(global);
  for (const project of body.projects || []) {
    const link = document.createElement("a");
    link.href = href(project.key, "");
    link.textContent = project.key;
    nav.append(link);
    if (project.key !== pageProject) continue;
    for (const fleet of project.fleets || []) {
      const child = document.createElement("a");
      child.href = href(project.key, fleet);
      child.textContent = fleet;
      nav.append(child);
    }
  }
}

function showFiles(id, rows, label) {
  const box = document.querySelector(id);
  box.replaceChildren();
  if (!rows.length) {
    line(box, "None.", "quiet");
    return;
  }
  for (const row of rows) {
    line(box, label(row));
  }
}

function render(body) {
  document.querySelector("#lede").textContent =
    "The roster for " + body.scope + ". These are the same rows as foil roster list.";
  showScopes(body);
  const roles = document.querySelector("#roles");
  roles.replaceChildren();
  const templates = body.templates || [];
  if (!templates.length) line(roles, "None.", "quiet");
  for (const row of templates) roles.append(showRole(row, body.bases || {}));
  showFiles("#personas", body.persona_files || [], (row) => row.name + "  " + row.layer);
  showFiles("#presets", body.presets || [], (row) => row.id + "  " + row.layer);
}

async function load() {
  const query = new URLSearchParams();
  if (pageProject) query.set("project", pageProject);
  if (pageFleet) query.set("fleet", pageFleet);
  const path = "/v1/settings" + (query.toString() ? "?" + query.toString() : "");
  const response = await fetch(path, { headers: headers() });
  if (!response.ok) {
    document.querySelector("#lede").textContent = "This machine refused the page.";
    return;
  }
  render(await response.json());
}

load();
