function fmtDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

async function copyToClipboard(text, btn) {
  await navigator.clipboard.writeText(text);
  if (btn) {
    const icon = btn.querySelector("i");
    const oldClass = icon.className;
    icon.className = "fa-solid fa-check";
    setTimeout(() => (icon.className = oldClass), 1500);
  }
}

function maskLinkText(fullUrl) {
  return fullUrl.split("?")[0];
}

function pluralize(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function renderCalendarCard(c) {
  const tpl = document.getElementById("template-calendar-card");
  const node = tpl.content.firstElementChild.cloneNode(true);

  node.querySelector("[data-name]").textContent = c.name;

  const descEl = node.querySelector("[data-description]");
  if (c.description) {
    descEl.textContent = c.description;
    descEl.classList.remove("d-none");
  }

  const ruleEl = node.querySelector("[data-rule-text]");
  if (c.rule_text) {
    ruleEl.textContent = c.rule_text;
    ruleEl.classList.remove("d-none");
  }

  node.querySelector("[data-stats]").textContent = `${pluralize(c.event_count, "event")} \u00b7 updated ${fmtDateTime(c.last_output_change_at)}`;

  const link = c.download_link ? `${window.location.origin}${c.download_link}` : null;

  if (link) {
    const linkRow = node.querySelector("[data-link-row]");
    linkRow.classList.remove("d-none");
    const linkEl = linkRow.querySelector("[data-link]");
    linkEl.href = link;
    linkEl.textContent = maskLinkText(link);
    linkRow.querySelector("[data-download]").href = link;
    linkRow.querySelector("[data-copy]").addEventListener("click", (e) => copyToClipboard(link, e.currentTarget));
  } else {
    node.querySelector("[data-no-link-row]").classList.remove("d-none");
  }

  return node;
}

function showContent(board) {
  document.getElementById("board-name").textContent = board.name;
  document.getElementById("board-description").textContent = board.description || "";

  const list = document.getElementById("calendars-list");
  list.innerHTML = "";
  document.getElementById("calendars-empty").classList.toggle("d-none", board.calendars.length > 0);
  for (const c of board.calendars) list.appendChild(renderCalendarCard(c));

  document.getElementById("error-state").classList.add("d-none");
  document.getElementById("content").classList.remove("d-none");
}

function showError(message) {
  document.getElementById("error-message").textContent = message || "This board is unavailable or protected.";
  document.getElementById("content").classList.add("d-none");
  document.getElementById("error-state").classList.remove("d-none");
}

async function init() {
  // Path is /board/<name>, /board/<name>/, or /board/<name>/<token>.
  const segments = window.location.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  const boardName = segments[1];
  const token = segments[2];

  document.getElementById("content").classList.add("d-none");
  document.getElementById("error-state").classList.add("d-none");

  if (!boardName) {
    document.getElementById("loading-screen").classList.add("d-none");
    showError("No board name was given.");
    return;
  }

  const url = `/api/public/boards/${encodeURIComponent(boardName)}${token ? `?token=${encodeURIComponent(token)}` : ""}`;
  let resp;
  try {
    resp = await fetch(url);
  } catch {
    document.getElementById("loading-screen").classList.add("d-none");
    showError("Could not reach the server.");
    return;
  }

  document.getElementById("loading-screen").classList.add("d-none");

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    showError(err.detail);
    return;
  }

  const board = await resp.json();
  showContent(board);
}

init();
