// -----------------
// region: constants
// -----------------

// const locale = "de-DE";
const locale = navigator.language;

const naming = {
  DEFAULT_IMPORT_NAME: "New Import",
};

// -------------------------
// region: globals and setup
// -------------------------

let timestampFormatter = null;

function updateTimestampFormatter() {
  timestampFormatter = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" });
  dayjs.locale(locale.toLowerCase());
}

dayjs.extend(dayjs_plugin_relativeTime);
dayjs.extend(dayjs_plugin_localizedFormat);
updateTimestampFormatter();

let loginOffcanvas = null;

let importsCache = [];
let importWizardModal = null;
let importDetailsModal = null;

let exportsCache = [];
let exportEditModal = null;
let exportEditModalTargetId = null;
let exportEditModalSaveTimer = null;
let exportEditModalDirty = false;

let boardEditModal = null;
let boardEditModalTargetId = null;
let boardEditModalSaveTimer = null;
let boardEditModalDirty = false;

// ---------------
// region: helpers
// ---------------

function cloneTemplate(id) {
  const template = document.getElementById(id);
  return template.content.firstElementChild.cloneNode(true);
}

async function api(url, options = {}) {
  return fetch(url, { ...options, credentials: "same-origin" });
}

// Extracts a readable message from a failed response. FastAPI sends `detail` as a
// string for HTTPExceptions and as a list of {msg, loc, ...} objects for request
// validation errors (e.g. the import form).
async function errorMessage(response, fallback) {
  try {
    const body = await response.json();
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const messages = detail.map((item) => (typeof item === "string" ? item : item.msg)).filter(Boolean);
      if (messages.length) return messages.map((m) => m.replace(/^Value error, /, "")).join("; ");
    }
  } catch (error) {}
  return fallback;
}

function showToast(message, variant = "danger") {
  const root = document.getElementById("toast-root");
  const toastElement = cloneTemplate("template-toast");

  toastElement.classList.add(`text-bg-${variant}`);
  toastElement.querySelector("[data-text]").textContent = message;

  root.appendChild(toastElement);
  const toast = new bootstrap.Toast(toastElement, { delay: 4000 });
  toast.show();
  toastElement.addEventListener("hidden.bs.toast", () => toastElement.remove());
}

async function copyToClipboard(text, button = null) {
  await navigator.clipboard.writeText(text);
  if (button) {
    const icon = button.querySelector("i");

    // when clicking the copy button twice, it would copy the custom class as original class
    // and would never revert to the actual original class, as the second timeout restores
    // to the wrongly copied custom class => ignore the second click
    if (icon.dataset.copied === "true") return;
    icon.dataset.copied = "true";

    const originalClass = icon.className;
    icon.className = "fa-solid fa-check";
    setTimeout(() => {
      icon.className = originalClass;
      delete icon.dataset.copied;
    }, 1500);
  } else {
    showToast("Copied to clipboard", "success");
  }
}

function formatTimestamp(timestamp) {
  if (!timestamp) return;
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return;
  return timestampFormatter.format(date);
}

function formatRelativeTime(timestamp) {
  if (!timestamp) return;
  const date = dayjs(timestamp);
  if (!date.isValid()) return;
  return date.fromNow();
}

function formatTimeRange(start, end) {
  if (!start || !end) return;
  const startDate = new Date(start);
  const endDate = new Date(end);
  if (isNaN(startDate.getTime()) || isNaN(endDate.getTime())) return;
  return timestampFormatter.formatRange(start, end);
}

function formatUrl(fullUrl) {
  const url = new URL(fullUrl);
  const path = decodeURIComponent(url.pathname);
  return `${url.host}${path}`;
}

// Domain only, without scheme, path, query or fragment
function formatDomain(fullUrl) {
  try {
    return new URL(fullUrl).host;
  } catch (error) {
    return fullUrl;
  }
}

// Domain, plus "/..." if the URL has anything beyond it (path, query or fragment)
function formatUrlTrimmed(fullUrl) {
  try {
    const url = new URL(fullUrl);
    const hasMore = url.pathname !== "/" || url.search || url.hash;
    return hasMore ? `${url.host}/...` : url.host;
  } catch (error) {
    return fullUrl;
  }
}

function stripIcsSuffix(text) {
  return text.replace(/\.ics$/, "");
}

function transformToName(text) {
  return text.replace(/[_-]/g, " ");
}

// Guess a name for an import based on given URL or filename
function guessImportName({ fullUrl = null, filename = null }) {
  let guessed = null;

  if (filename) {
    guessed = filename;
  } else if (fullUrl) {
    try {
      const url = new URL(fullUrl);
      const pathSegments = url.pathname.split("/").filter(Boolean);

      if (pathSegments.length) {
        // last non-empty path segment
        guessed = decodeURIComponent(pathSegments[pathSegments.length - 1]);
      }
    } catch (error) {}
  }

  if (!guessed) return naming.DEFAULT_IMPORT_NAME;

  // strip calendar file extension
  guessed = guessed.replace(/\.(ics|ical|ifb|vcs)$/i, "");
  // turn underscores and hyphens into spaces
  guessed = guessed.replace(/[_-]/g, " ");
  // capitalize each word
  guessed = guessed.replace(/\b\w/g, (char) => char.toUpperCase());

  return guessed || naming.DEFAULT_IMPORT_NAME;
}

function generateBoardLinks(board) {
  if (!board.link_name) return { maskedLink: null, link: null };
  const maskedLink = `${window.location.origin}/board/${board.link_name}`;
  const link = board.protected ? `${maskedLink}/${board.token}` : maskedLink;
  return { maskedLink, link };
}

function generateCalendarExportLinks(export_) {
  if (!export_.link_name) return { maskedLink: null, link: null };
  const maskedLink = `${window.location.origin}/calendar/${export_.link_name}`;
  const link = export_.protected ? `${maskedLink}?token=${export_.token}` : maskedLink;
  return { maskedLink, link };
}

function pluralize(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

async function downloadFile(url, filename) {
  const response = await api(url);
  if (!response.ok) {
    showToast("Could not download file");
    return;
  }

  const blob = await response.blob();
  const element = document.createElement("a");
  element.href = URL.createObjectURL(blob);
  element.download = filename;
  element.click();
  URL.revokeObjectURL(element.href);
}

// Toggles between a permanent display/placeholder pair and a real link, e.g.
// the "Link" row in the export/board modals. Both elements always exist in
// the DOM; only one is ever shown.
function setLinkDisplay({ placeholderEl, linkEl, copyBtn, masked, target }) {
  if (!masked) {
    placeholderEl.classList.remove("d-none");
    linkEl.classList.add("d-none");
    linkEl.removeAttribute("href");
    copyBtn.disabled = true;
    delete copyBtn.dataset.copy;
    return;
  }
  placeholderEl.classList.add("d-none");
  linkEl.classList.remove("d-none");
  linkEl.textContent = masked;
  linkEl.href = target;
  copyBtn.disabled = false;
  copyBtn.dataset.copy = target;
}

// ----------------------
// region: authentication
// ----------------------

async function checkAuth() {
  const response = await api("/api/auth/me");
  return response.ok;
}

const PROVIDER_META = {
  github: {
    label: "Continue with GitHub",
    icon: "fa-brands fa-github",
    recommended: false,
  },
  google: {
    label: "Continue with Google",
    icon: "fa-brands fa-google",
    recommended: false,
  },
  pocketid: {
    label: "Continue with Pocket ID",
    icon: "fa-solid fa-key",
    recommended: true,
  },
};

async function loadLoginProviders() {
  const response = await fetch("/api/auth/providers");
  const providers = response.ok ? await response.json() : [];
  const box = document.getElementById("login-providers");
  const noProvidersElement = document.getElementById("login-providers-empty");
  box.querySelectorAll("[data-provider-button]").forEach((el) => el.remove());

  noProvidersElement.classList.toggle("d-none", providers.length > 0);
  if (providers.length === 0) return;

  // The recommended provider (if any) is highlighted and listed first, but
  // only when there's actually a choice to make.
  const hasChoice = providers.length > 1;
  const ordered = [...providers].sort((a, b) => {
    const ra = hasChoice && PROVIDER_META[a]?.recommended;
    const rb = hasChoice && PROVIDER_META[b]?.recommended;
    return ra === rb ? 0 : ra ? -1 : 1;
  });

  for (const p of ordered) {
    const meta = PROVIDER_META[p] || {
      label: `Continue with ${p}`,
      icon: "fa-solid fa-right-to-bracket",
      recommended: false,
    };
    const isDefault = hasChoice && meta.recommended;
    const button = cloneTemplate("template-login-provider");
    button.className = isDefault ? "btn btn-primary" : "btn btn-dark btn-sm";
    button.querySelector("[data-icon]").className = `${meta.icon} me-2`;
    button.querySelector("[data-label]").textContent = meta.label;
    if (isDefault) button.querySelector("[data-badge]").classList.remove("d-none");
    button.addEventListener("click", () => oauthLogin(p));
    box.appendChild(button);
  }
}

function oauthLogin(provider) {
  const width = 500;
  const height = 700;
  const left = window.screenX + (window.outerWidth - width) / 2;
  const top = window.screenY + (window.outerHeight - height) / 2;
  window.open(`/api/auth/login/${provider}`, "oauth", `width=${width},height=${height},left=${left},top=${top}`);
}

window.addEventListener("message", (event) => {
  if (event.data?.type === "oauth-success") {
    loginOffcanvas?.hide();
    boot();
  }
});

document.getElementById("logout-button").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  window.location.reload();
});

function showLoggedOut() {
  document.getElementById("info-page").classList.remove("d-none");
  document.getElementById("app").classList.add("d-none");
}

function showLoggedIn() {
  document.getElementById("info-page").classList.add("d-none");
  document.getElementById("app").classList.remove("d-none");
}

// -----------------------
// region: imports helpers
// -----------------------

async function loadImports() {
  const resp = await api("/api/imports");
  const items = resp.ok ? await resp.json() : [];
  importsCache = items;
  const list = document.getElementById("imports-list");
  document.getElementById("imports-empty").classList.toggle("d-none", items.length > 0);
  list.innerHTML = "";
  for (const import_ of items) list.appendChild(renderImportItem(import_));
  return items;
}

function isWebImport(import_) {
  return !!import_.url;
}

// A web calendar's last fetch succeeded if it was never attempted yet, or if
// the last attempt is not newer than the last success (see CalendarImport model).
function webImportStatus(import_) {
  if (!import_.last_fetched_at) return "pending";
  if (import_.last_success_at && new Date(import_.last_success_at) >= new Date(import_.last_fetched_at)) return "success";
  return "failed";
}

function importIconMeta(import_) {
  const addedLine = `Added ${formatTimestamp(import_.created_at)}`;
  if (isWebImport(import_)) {
    const status = webImportStatus(import_);
    const statusLine = {
      success: "Last fetch succeeded",
      pending: "Not fetched yet",
      failed: `Last fetch failed: ${import_.last_error || "Unknown error"}`,
    }[status];
    return {
      icon: "fa-cloud-arrow-down",
      colorClass: status === "failed" ? "text-danger" : "text-success",
      title: `Web calendar\n${addedLine}\n${statusLine}\nClick to view details`,
    };
  }
  return {
    icon: "fa-file-lines",
    colorClass: "text-success",
    title: `Uploaded file\n${addedLine}\nClick to view details`,
  };
}

function fmtRange(stats) {
  if (!stats.event_count || (!stats.range_start && !stats.range_end)) return "no events";
  return `${pluralize(stats.event_count, "event")} \u00b7 ${formatTimestamp(stats.range_start)} \u2013 ${formatTimestamp(stats.range_end)}`;
}

function renderImportItem(import_) {
  const div = cloneTemplate("template-calendar-import-item");

  const nameDisplay = div.querySelector("[data-name-display]");
  const nameControls = div.querySelector("[data-name-controls]");
  const nameInput = div.querySelector("[data-name-input]");
  const editBtn = div.querySelector("[data-edit-name]");
  const acceptBtn = div.querySelector("[data-accept]");
  const cancelBtn = div.querySelector("[data-cancel]");
  nameDisplay.textContent = import_.name;

  let originalName = import_.name;

  function exitEditMode(name) {
    nameDisplay.textContent = name;
    import_.name = name;
    nameControls.classList.add("d-none");
    nameDisplay.classList.remove("d-none");
    editBtn.classList.remove("d-none");
  }

  editBtn.addEventListener("click", () => {
    originalName = import_.name;
    nameInput.value = originalName;
    acceptBtn.disabled = true;
    nameDisplay.classList.add("d-none");
    editBtn.classList.add("d-none");
    nameControls.classList.remove("d-none");
    nameInput.focus();
    nameInput.select();
  });

  async function save(newName) {
    const r = await api(`/api/imports/${import_.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    });
    if (!r.ok) {
      showToast(await errorMessage(r, "Rename failed"));
      return false;
    }
    await loadImports();
    return true;
  }

  // No autosave here (unlike the export/board modals) -- a PATCH only ever
  // goes out when the user clicks accept.
  nameInput.addEventListener("input", () => {
    acceptBtn.disabled = nameInput.value.trim() === originalName || !nameInput.value.trim();
  });

  nameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (!acceptBtn.disabled) acceptBtn.click();
    } else if (event.key === "Escape") {
      cancelBtn.click();
    }
  });

  acceptBtn.addEventListener("click", async () => {
    const value = nameInput.value.trim();
    if (!value || value === originalName) return;
    const ok = await save(value);
    if (ok) exitEditMode(value);
  });

  cancelBtn.addEventListener("click", () => {
    exitEditMode(originalName);
  });

  // type icon, URL (web calendars) and stats
  const iconMeta = importIconMeta(import_);
  div.querySelector("[data-icon]").className = `fa-solid ${iconMeta.icon} ${iconMeta.colorClass}`;
  const openDetailsBtn = div.querySelector("[data-open-details]");
  openDetailsBtn.title = iconMeta.title;
  openDetailsBtn.addEventListener("click", () => openImportDetailsModal(import_));

  const urlEl = div.querySelector("[data-url]");
  urlEl.textContent = isWebImport(import_) ? `Web Calendar from ${formatDomain(import_.url)}` : "Uploaded file";

  div.querySelector("[data-stats]").textContent = fmtRange(import_);

  if (isWebImport(import_) && webImportStatus(import_) === "failed") {
    const errorEl = div.querySelector("[data-error]");
    errorEl.textContent = `Last fetch failed: ${import_.last_error || "Unknown error"}`;
    errorEl.classList.remove("d-none");
  }

  div.querySelector("[data-details]").addEventListener("click", () => openImportDetailsModal(import_));

  div.querySelector("[data-download]").addEventListener("click", () => {
    downloadFile(`/api/imports/${import_.id}/download`, `${import_.name}.ics`);
  });

  div.querySelector("[data-delete]").addEventListener("click", async () => {
    if (!confirm(`Delete import "${import_.name}"?`)) return;
    const r = await api(`/api/imports/${import_.id}`, { method: "DELETE" });
    if (!r.ok) {
      showToast(await errorMessage(r, "Could not delete import"));
      return;
    }
    // exports reference imports as sources, so they changed as well
    await loadImports();
    await loadExports();
  });

  return div;
}

// ---------- import details modal (web calendars and uploaded files) ----------

function renderCopyTargetItem(label, onClick) {
  const li = cloneTemplate("template-dropdown-target-item");
  const btn = li.querySelector("[data-target-button]");
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return li;
}

async function openImportDetailsModal(import_) {
  const isWeb = isWebImport(import_);

  document.getElementById("import-details-title").textContent = import_.name;
  document.getElementById("import-details-type-label").textContent = isWeb ? "Web calendar" : "Static file";

  const webFields = document.getElementById("import-details-web-fields");
  const backupSection = document.getElementById("import-details-backup-section");
  webFields.classList.toggle("d-none", !isWeb);
  backupSection.classList.toggle("d-none", !isWeb);

  if (isWeb) {
    const status = webImportStatus(import_);
    document.getElementById("import-details-status-success").classList.toggle("d-none", status !== "success");
    document.getElementById("import-details-status-fail").classList.toggle("d-none", status !== "failed");
    document.getElementById("import-details-status-pending").classList.toggle("d-none", status !== "pending");
    if (status === "failed") {
      document.getElementById("import-details-status-fail-reason").textContent = import_.last_error || "Unknown error";
    }

    document.getElementById("import-details-last-fetched").textContent = formatTimestamp(import_.last_fetched_at) || "\u2013";
    const urlEl = document.getElementById("import-details-url");
    urlEl.href = import_.url;
    urlEl.title = import_.url;
    urlEl.textContent = formatUrlTrimmed(import_.url);

    // NOTE: `backup` is only reflected if the API includes it in ImportSummaryResponse.
    const backupCheckbox = document.getElementById("import-details-backup");
    backupCheckbox.checked = !!import_.backup;
    backupCheckbox.onchange = async () => {
      const desired = backupCheckbox.checked;
      const r = await api(`/api/imports/${import_.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backup: desired }),
      });
      if (!r.ok) {
        showToast(await errorMessage(r, "Could not update backup setting"));
        backupCheckbox.checked = !desired;
        return;
      }
      import_.backup = desired;
      await loadImports();
    };
  }

  document.getElementById("import-details-created").textContent = formatTimestamp(import_.created_at) || "\u2013";
  document.getElementById("import-details-stats").textContent = fmtRange(import_);

  document.getElementById("import-details-download-button").onclick = () => {
    downloadFile(`/api/imports/${import_.id}/download`, `${import_.name}.ics`);
  };

  importDetailsModal.show();
  await renderFileReferences(import_);
}

async function renderFileReferences(import_) {
  const listDiv = document.getElementById("import-details-versions-list");
  const loadingEl = document.getElementById("import-details-versions-loading");
  const emptyEl = document.getElementById("import-details-versions-empty");
  listDiv.innerHTML = "";
  emptyEl.classList.add("d-none");
  loadingEl.classList.remove("d-none");

  const resp = await api(`/api/imports/${import_.id}/file-references`);
  loadingEl.classList.add("d-none");
  if (!resp.ok) {
    showToast(await errorMessage(resp, "Could not load stored versions"));
    return;
  }
  // newest first, as sent by the server
  const fileReferences = await resp.json();
  emptyEl.classList.toggle("d-none", fileReferences.length > 0);

  fileReferences.forEach((fileReference, i) => {
    const row = cloneTemplate("template-file-reference-row");
    row.querySelector("[data-created-at]").textContent = formatTimestamp(fileReference.created_at);
    row.querySelector("[data-latest-badge]").classList.toggle("d-none", i !== 0 || fileReferences.length === 1);
    row.querySelector("[data-stats]").textContent = fmtRange(fileReference);

    row.querySelector("[data-download-button]").addEventListener("click", () => {
      downloadFile(`/api/imports/${import_.id}/file-references/${fileReference.id}/download`, `${import_.name}-${fileReference.created_at}.ics`);
    });

    row.querySelector("[data-copy-button]").addEventListener("click", async () => {
      const r = await api(`/api/imports/${import_.id}/file-references/${fileReference.id}/copy`, { method: "POST" });
      if (!r.ok) {
        showToast(await errorMessage(r, "Could not create import from this version"));
        return;
      }
      const created = await r.json();
      showToast(`Created import "${created.name}"`, "success");
      await loadImports();
    });

    listDiv.appendChild(row);
  });
}

// -------------------------------
// region: imports event listeners
// -------------------------------

document.getElementById("new-import-button").addEventListener("click", () => {
  importWizardModal.show();
});

document.getElementById("import-wizard-modal").addEventListener("show.bs.modal", () => {
  document.getElementById("wizard-url").value = "";
  document.getElementById("wizard-file").value = "";
  const nameInput = document.getElementById("wizard-name");
  nameInput.value = "";
  nameInput.placeholder = naming.DEFAULT_IMPORT_NAME;
  nameInput.classList.remove("is-invalid");
  document.getElementById("wizard-error").textContent = "";
  setWizardBusy(false);
  bootstrap.Tab.getOrCreateInstance(document.getElementById("import-tab-url-button")).show();
});

function setWizardBusy(busy) {
  document.getElementById("wizard-ok-button").disabled = busy;
  document.getElementById("wizard-spinner").classList.toggle("d-none", !busy);
  document.getElementById("wizard-ok-icon").classList.toggle("d-none", busy);
}

document.getElementById("wizard-url").addEventListener("input", (event) => {
  const nameInput = document.getElementById("wizard-name");
  nameInput.placeholder = guessImportName({ fullUrl: event.target.value.trim() });
});

document.getElementById("wizard-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  const nameInput = document.getElementById("wizard-name");
  nameInput.placeholder = guessImportName({ filename: file?.name });
});

document.getElementById("wizard-ok-button").addEventListener("click", async () => {
  const kind = document.getElementById("import-tab-file").classList.contains("active") ? "file" : "url";
  const nameInput = document.getElementById("wizard-name");

  // The backend expects multipart form data with a name and exactly one of url / file
  const formData = new FormData();

  if (kind === "url") {
    const url = document.getElementById("wizard-url").value.trim();
    if (!url) {
      showToast("Enter a calendar URL");
      return;
    }
    formData.append("url", url);
    formData.append("name", nameInput.value.trim() || guessImportName({ fullUrl: url }));
  } else {
    const file = document.getElementById("wizard-file").files[0];
    if (!file) {
      showToast("Choose a file");
      return;
    }
    formData.append("file", file);
    formData.append("name", nameInput.value.trim() || guessImportName({ filename: file.name }));
  }

  // The server fetches web calendars while handling this request, which may take a moment.
  // Keep the modal open on failure so that the input is not lost.
  setWizardBusy(true);
  let resp;
  try {
    resp = await api("/api/imports", { method: "POST", body: formData });
  } catch (error) {
    showToast("Could not reach the server");
    setWizardBusy(false);
    return;
  }

  if (!resp.ok) {
    const message = await errorMessage(resp, "Could not create import");
    document.getElementById("wizard-error").textContent = message;
    // show the message below the name field, but only highlight the field if it is about the name
    nameInput.classList.toggle("is-invalid", message.toLowerCase().includes("name"));
    if (!message.toLowerCase().includes("name")) {
      document.getElementById("wizard-error").textContent = "";
      showToast(message);
    }
    setWizardBusy(false);
    return;
  }

  importWizardModal.hide();
  await loadImports();
});

// -----------------------
// region: exports helpers
// -----------------------

async function loadExports() {
  const resp = await api("/api/exports");
  const items = await resp.json();
  exportsCache = items;
  const list = document.getElementById("exports-list");
  document.getElementById("exports-empty").classList.toggle("d-none", items.length > 0);
  list.innerHTML = "";
  for (const f of items) list.appendChild(renderExportItem(f));
  return items;
}

function renderExportItem(export_) {
  const div = cloneTemplate("template-export-item");

  const showUnpublishedBadge = !export_.published;
  const showProtectedBadge = !showUnpublishedBadge && !!export_.protected;
  const showPublicBadge = !showUnpublishedBadge && !showProtectedBadge;

  div.querySelector("[data-badge-unpublished]").classList.toggle("d-none", !showUnpublishedBadge);
  div.querySelector("[data-badge-protected]").classList.toggle("d-none", !showProtectedBadge);
  div.querySelector("[data-badge-public]").classList.toggle("d-none", !showPublicBadge);

  div.querySelector("[data-name]").textContent = export_.name;

  // TODO: detect duplicates client side by comparing sources and their filter / transform settings and global transform
  const warnEl = div.querySelector("[data-duplicate-warning]");
  // TODO: outdated, server no longer sends this information
  if (export_.duplicate_warning) {
    warnEl.classList.remove("d-none");
    warnEl.title = `Same rule as: ${export_.duplicate_warning.map((d) => d.name).join(", ")}`;
  }

  div.querySelector("[data-description]").textContent = export_.description || "No description";
  div.querySelector("[data-stats]").textContent =
    `${pluralize(export_.sources.length, "input calendar")} \u00b7 ${pluralize(export_.event_count, "event")} \u00b7 updated ${formatTimestamp(export_.updated_at)}`;

  const { maskedLink, link } = generateCalendarExportLinks(export_);
  if (export_.published) {
    const linkRow = div.querySelector("[data-link-row]");
    linkRow.classList.remove("d-none");
    linkRow.querySelector("[data-link]").href = link;
    linkRow.querySelector("[data-link]").textContent = maskedLink;
    linkRow.querySelector("[data-copy-button]").addEventListener("click", (event) => copyToClipboard(link, event.currentTarget));
  }

  div.querySelector("[data-edit]").addEventListener("click", () => openExportModal(export_.id));
  div.querySelector("[data-delete]").addEventListener("click", async () => {
    if (!confirm(`Delete export "${export_.name}"?`)) return;
    await api(`/api/exports/${export_.id}`, { method: "DELETE" });
    await loadExports();
  });

  return div;
}

// Reference data shown in the "Sources" info popover, and used to derive the
// filter/transform inputs' placeholder text: available fields to filter on,
// and available transform commands, each keyed by name with a short
// human-readable description as the value.
//
// This is wrapped in getPredicateInfo() rather than being static top-level
// consts because it's expected to eventually come from the server (once the
// backend defines what predicates/transforms it actually supports); callers
// don't need to change when that happens, just this function's body. Until
// then, the object below is built once on first call and cached, so
// opening/closing the popover or re-rendering source blocks never rebuilds
// it or recomputes the derived placeholder strings.
let _predicateInfoCache = null;
function getPredicateInfo() {
  if (_predicateInfoCache) return _predicateInfoCache;

  // TODO: replace with data fetched from the server once it exposes the
  // supported predicates/transforms; the shape below (dicts of name ->
  // description, keyed the same way) is what the rest of this file expects.
  const filterFields = {
    summary: "Event title (text)",
    description: "Event description (text)",
    location: "Event location (text)",
    uid: "Unique event id (text)",
    dtstart: "Start, ISO 8601 in UTC (text, wrap in datetime())",
    dtend: "End, ISO 8601 in UTC; derived from duration if missing (text, wrap in datetime())",
    duration: "Length in seconds; derived from start/end if missing (number)",
    "all-day": "1 for all-day events, 0 for timed events",
    status: "TENTATIVE, CONFIRMED or CANCELLED (case-insensitive)",
    class: "PUBLIC, PRIVATE or CONFIDENTIAL (case-insensitive)",
    transp: "OPAQUE (busy) or TRANSPARENT (free) (case-insensitive)",
    sequence: "Revision number (number)",
    url: "Event URL (text)",
    created: "Creation time, ISO 8601 in UTC (text, wrap in datetime())",
    "last-modified": "Last modification time, ISO 8601 in UTC (text, wrap in datetime())",
    dtstamp: "Timestamp of the event's creation by the source, ISO 8601 in UTC (text, wrap in datetime())",
  };
  const filterNotes = [
    "A filter is an SQL WHERE clause; events that match are kept. Leave empty to keep all events.",
    'Quote names containing a hyphen: "all-day", "last-modified".',
    "Also available: REGEXP (summary REGEXP '^Meeting') and DURATION('PT1H30M'), which converts an ISO 8601 duration to seconds.",
    "Compare dates with datetime() on both sides, e.g. datetime(dtstart) >= datetime('2026-01-01T00:00:00Z'); plain text comparison breaks across timezones.",
  ];
  const transformCommands = {
    "shift:<duration>": "Move the event by an ISO 8601 duration; may be negative (shift:PT1H, shift:-PT30M)",
    "clip-min-duration:<duration>": "Extend events shorter than this to exactly this length (clip-min-duration:PT15M)",
    "clip-max-duration:<duration>": "Shorten events longer than this to exactly this length (clip-max-duration:PT2H)",
    "set-<property>:<value>": 'Overwrite an event property (set-location:"Room 1", set-summary:Busy). Not allowed: uid, dtstart, dtend, duration',
    "remove:<property>":
      "Delete an event property (remove:description). remove:extra-properties deletes all X- properties. uid, dtstamp, dtstart, dtend and duration can't be removed",
    "overlap-trim-end": "Where events overlap, end the earlier event when the later one starts",
    "overlap-trim-start": "Where events overlap, start the later event when the earlier one ends",
    "combine-all-day": "Merge consecutive all-day events with the same title into one multi-day event",
  };
  const transformNotes = [
    "Separate commands with spaces; they run in order, left to right. Use quotes around values containing spaces.",
    "shift, clip-* and overlap-* only affect timed events, not all-day events.",
  ];

  // Placeholder text is written as realistic examples that are valid for the
  // backend, so users can copy them as a starting point.
  _predicateInfoCache = {
    filterFields,
    filterNotes,
    transformCommands,
    transformNotes,
    filterPlaceholder: "e.g. summary LIKE '%standup%' AND \"all-day\" = 0",
    transformPlaceholder: 'e.g. shift:PT1H set-location:"Room 1" remove:description',
  };
  return _predicateInfoCache;
}

// Appends one <li> (from template-info-list-item) per dict entry into ulEl.
function populateInfoList(ulEl, dict) {
  for (const [key, desc] of Object.entries(dict)) {
    const item = cloneTemplate("template-info-list-item");
    item.querySelector("[data-key]").textContent = key;
    item.querySelector("[data-desc]").textContent = desc;
    ulEl.appendChild(item);
  }
}

// Appends one <p> per note string into containerEl.
function populateNotes(containerEl, notes) {
  for (const note of notes) {
    const item = cloneTemplate("template-info-note");
    item.textContent = note;
    containerEl.appendChild(item);
  }
}

// Builds the Sources info popover's content DOM once and caches it (same
// reasoning as getPredicateInfo() above): Bootstrap calls this again every
// time the popover is shown, but there's no need to rebuild the list markup
// each time since the underlying predicate info hasn't changed.
let _sourcesInfoContentCache = null;
function getSourcesInfoContent() {
  if (_sourcesInfoContentCache) return _sourcesInfoContentCache;
  const { filterFields, filterNotes, transformCommands, transformNotes } = getPredicateInfo();
  const content = cloneTemplate("template-sources-info-popover");
  populateInfoList(content.querySelector("[data-filter-fields-list]"), filterFields);
  populateInfoList(content.querySelector("[data-transform-commands-list]"), transformCommands);
  populateNotes(content.querySelector("[data-filter-notes]"), filterNotes);
  populateNotes(content.querySelector("[data-transform-notes]"), transformNotes);
  _sourcesInfoContentCache = content;
  return content;
}

// One-time setup for the Sources info popover: hover previews it on
// desktop; trigger also includes "focus" so a tap on mobile (which focuses
// the button, since it's a <button>) shows it too, and tapping elsewhere
// (which blurs it) closes it again -- Bootstrap handles that dismissal
// automatically for the focus trigger, no manual listeners needed.
function setupSourcesInfoPopover() {
  const infoButton = document.getElementById("export-sources-info-button");
  if (infoButton.dataset.infoPopoverWired) return; // boot() can run more than once (e.g. after OAuth login); wire listeners only once
  infoButton.dataset.infoPopoverWired = "true";

  bootstrap.Popover.getOrCreateInstance(infoButton, {
    html: true,
    trigger: "hover focus",
    placement: "bottom",
    customClass: "sources-info-popover",
    title: "", // no popover-header: the button has no `title` attribute to fall back to, and this makes that explicit
    content: getSourcesInfoContent,
  });
}

function updateModalLinkDisplay(export_) {
  const { maskedLink, link } = generateCalendarExportLinks(export_);
  setLinkDisplay({
    placeholderEl: document.getElementById("export-link-name-link-placeholder"),
    linkEl: document.getElementById("export-link-name-link-display"),
    copyBtn: document.getElementById("export-copy-link-button"),
    masked: maskedLink,
    target: link,
  });
}

// Local state for the source blocks currently shown in the export modal.
// Each entry: { localId, import_id, filter, transform }. `localId` is a
// client-only key used to find/update/remove a block's DOM element and its
// backing state entry -- it never leaves the browser. Order in this array is
// the same order the blocks render in, top to bottom, and is meaningful: when
// events from different sources share an id after filtering, later sources
// (further down the list) overwrite earlier ones.
let exportEditModalSources = [];
let exportEditModalSourceLocalIdCounter = 0;
// localId -> { filter_error, transform_error } from the last failed save
let exportEditModalSourceErrors = {};
let exportEditModalScheduleSave = () => {};

function nextSourceLocalId() {
  exportEditModalSourceLocalIdCounter += 1;
  return `src-${exportEditModalSourceLocalIdCounter}`;
}

function importNameFor(importId) {
  const imp = importsCache.find((i) => i.id === importId);
  return imp ? imp.name : "(deleted import)";
}

// Rebuilds the whole sources list in the DOM from exportEditModalSources.
// Called after every add/remove; individual field edits patch their own
// textarea's state entry directly instead of triggering a full re-render, so
// the user never loses cursor position while typing.
function renderExportSourceBlocks(scheduleSave) {
  const sourcesDiv = document.getElementById("export-sources");
  sourcesDiv.querySelectorAll("[data-source-block]").forEach((el) => el.remove());
  document.getElementById("export-sources-empty").classList.toggle("d-none", exportEditModalSources.length > 0);

  for (const source of exportEditModalSources) {
    const block = cloneTemplate("template-export-source-block");
    block.dataset.localId = source.localId;
    block.querySelector("[data-source-title]").textContent = importNameFor(source.import_id);

    const filterInput = block.querySelector("[data-source-filter]");
    filterInput.placeholder = getPredicateInfo().filterPlaceholder;
    filterInput.value = source.filter || "";
    filterInput.oninput = () => {
      source.filter = filterInput.value;
      filterInput.classList.remove("is-invalid");
      scheduleSave();
    };

    const transformInput = block.querySelector("[data-source-transform]");
    transformInput.placeholder = getPredicateInfo().transformPlaceholder;
    transformInput.value = source.transform || "";
    transformInput.oninput = () => {
      source.transform = transformInput.value;
      transformInput.classList.remove("is-invalid");
      scheduleSave();
    };

    // validation errors of the last failed save (keyed by import id, set in performExportSave)
    const sourceError = exportEditModalSourceErrors[source.localId];
    if (sourceError?.filter_error) {
      filterInput.classList.add("is-invalid");
      block.querySelector("[data-source-filter-error]").textContent = sourceError.filter_error;
    }
    if (sourceError?.transform_error) {
      transformInput.classList.add("is-invalid");
      block.querySelector("[data-source-transform-error]").textContent = sourceError.transform_error;
    }

    block.querySelector("[data-remove-source]").addEventListener("click", () => {
      exportEditModalSources = exportEditModalSources.filter((s) => s.localId !== source.localId);
      renderExportSourceBlocks(scheduleSave);
      scheduleSave();
    });

    sourcesDiv.appendChild(block);
  }
}

// Populates the "Add source" dropdown from importsCache. Every import is
// always offered, even ones already used by an existing block -- the same
// import can be added as a source more than once, each with its own
// filter/transform.
function renderAddSourceMenu(onAdd) {
  const menu = document.getElementById("export-add-source-menu");
  menu.querySelectorAll("[data-import-option]").forEach((el) => el.remove());
  document.getElementById("export-add-source-menu-empty").classList.toggle("d-none", importsCache.length > 0);

  for (const imp of importsCache) {
    const item = cloneTemplate("template-dropdown-target-item");
    item.dataset.importOption = "true";
    const button = item.querySelector("[data-target-button]");
    button.textContent = imp.name;
    button.addEventListener("click", () => onAdd(imp.id));
    menu.appendChild(item);
  }
}

async function openExportModal(exportId) {
  exportEditModalTargetId = exportId;
  exportEditModalDirty = false;
  const exportData = await (await api(`/api/exports/${exportId}`)).json();

  document.getElementById("export-name").value = exportData.name;
  document.getElementById("export-name").classList.remove("is-invalid");
  document.getElementById("export-name-error").textContent = "";
  document.getElementById("export-description").value = exportData.description || "";
  document.getElementById("export-link-name").value = exportData.link_name || "";
  document.getElementById("export-link-name").classList.remove("is-invalid");
  document.getElementById("export-link-name-error").textContent = "";
  document.getElementById("export-protected").checked = exportData.protected;
  document.getElementById("export-published").checked = exportData.published;
  document.getElementById("save-status").textContent = "All changes saved";
  updateModalLinkDisplay(exportData);

  // Seed local state from whatever the backend gives us for per-source
  // config. Until the backend implements this, `sources` will simply be
  // absent/empty and the modal opens with the empty-box placeholder.
  exportEditModalSourceLocalIdCounter = 0;
  exportEditModalSourceErrors = {};
  exportEditModalSources = (exportData.sources || []).map((source) => ({
    localId: nextSourceLocalId(),
    import_id: source.import_id,
    filter: source.filter || "",
    transform: source.transform || "",
  }));

  function scheduleSave() {
    exportEditModalDirty = true;
    document.getElementById("save-status").textContent = "Saving...";
    clearTimeout(exportEditModalSaveTimer);
    exportEditModalSaveTimer = setTimeout(() => saveExport(exportId), 900);
  }

  exportEditModalScheduleSave = scheduleSave;
  renderExportSourceBlocks(scheduleSave);
  renderAddSourceMenu((importId) => {
    exportEditModalSources.push({ localId: nextSourceLocalId(), import_id: importId, filter: "", transform: "" });
    renderExportSourceBlocks(scheduleSave);
    scheduleSave();
  });

  document.getElementById("export-name").oninput = scheduleSave;
  document.getElementById("export-description").oninput = scheduleSave;
  document.getElementById("export-link-name").oninput = scheduleSave;
  document.getElementById("export-protected").onchange = scheduleSave;
  document.getElementById("export-published").onchange = scheduleSave;

  exportEditModal.show();
}

// Runs at most one call at a time. Calls made while one is running are merged into a single
// follow-up run, so changes made during the request are saved with their own result.
function singleFlight(task) {
  let pending = null;
  let rerunRequested = false;

  async function runUntilSettled(args) {
    let result;
    do {
      rerunRequested = false;
      result = await task(...args);
    } while (rerunRequested);
    return result;
  }

  return (...args) => {
    if (pending) {
      rerunRequested = true;
      return pending;
    }
    pending = runUntilSettled(args).finally(() => {
      pending = null;
    });
    return pending;
  };
}

const saveExport = singleFlight(performExportSave);

// Returns true on success, false on failure -- callers (in particular the
// close-the-modal handler) must not blindly retry on failure, or a
// permanently-invalid value (e.g. a name that collides) turns into an
// infinite save loop every time hide() is called again.
async function performExportSave(exportId) {
  const payload = {
    name: document.getElementById("export-name").value.trim(),
    description: document.getElementById("export-description").value,
    link_name: document.getElementById("export-link-name").value.trim(),
    protected: document.getElementById("export-protected").checked,
    published: document.getElementById("export-published").checked,
    // Per-source config: one entry per source block, in display order (top
    // to bottom). `where` is the SQL WHERE-clause body used to select that
    // source's events; `transform` is the (ordered, event-level-only) list
    // of transformations applied afterward. Order matters beyond display:
    // sources are combined and, on duplicate event ids, the entry from the
    // later source in this array wins.
    sources: exportEditModalSources.map(({ import_id, filter, transform }) => ({ import_id, filter, transform })),
  };
  const resp = await api(`/api/exports/${exportId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const statusEl = document.getElementById("save-status");
  const nameInput = document.getElementById("export-name");
  const nameErrEl = document.getElementById("export-name-error");
  const pubErrEl = document.getElementById("export-link-name-error");
  const pubInput = document.getElementById("export-link-name");
  if (!resp.ok) {
    const err = await resp.json();
    statusEl.textContent = "Not saved";

    // Source validation errors arrive as a list of JSON strings, each one
    // { import_id, filter_error, transform_error }. Match them back to the
    // source blocks by import id (in order, as the same import may be used twice).
    if (resp.status === 422 && Array.isArray(err.detail) && err.detail.every((item) => typeof item === "string")) {
      const parsedErrors = err.detail.map((item) => JSON.parse(item));
      exportEditModalSourceErrors = {};
      const used = new Set();
      for (const parsed of parsedErrors) {
        const source = exportEditModalSources.find((s) => s.import_id === parsed.import_id && !used.has(s.localId));
        if (source) {
          used.add(source.localId);
          exportEditModalSourceErrors[source.localId] = parsed;
        }
      }
      renderExportSourceBlocks(exportEditModalScheduleSave);
      exportEditModalDirty = true;
      return false;
    }

    const detailLower = String(err.detail || "").toLowerCase();

    // guess which field the error is about, same approach as the board modal
    const isNameError = detailLower.includes("export name");
    const isLinkNameError = !isNameError && detailLower.includes("public");

    nameInput.classList.toggle("is-invalid", isNameError);
    nameErrEl.textContent = isNameError ? err.detail : "";

    pubErrEl.textContent = isLinkNameError ? err.detail : "";
    pubInput.classList.toggle("is-invalid", isLinkNameError);
    if (!isNameError && !isLinkNameError) showToast(err.detail || "Save failed");
    exportEditModalDirty = true;
    return false;
  }
  nameInput.classList.remove("is-invalid");
  nameErrEl.textContent = "";
  pubErrEl.textContent = "";
  pubInput.classList.remove("is-invalid");
  if (Object.keys(exportEditModalSourceErrors).length) {
    exportEditModalSourceErrors = {};
    renderExportSourceBlocks(exportEditModalScheduleSave);
  }
  const updated = await resp.json();
  updateModalLinkDisplay(updated);
  statusEl.textContent = "All changes saved";
  exportEditModalDirty = false;
  await loadExports();
  return true;
}

// -------------------------------
// region: exports event listeners
// -------------------------------

document.getElementById("new-export-button").addEventListener("click", async () => {
  // No name-picking step -- create with an auto-generated placeholder name
  // and go straight to the edit modal, where it can be renamed.
  const resp = await api("/api/exports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!resp.ok) {
    showToast((await resp.json()).detail || "Could not create export");
    return;
  }
  const f = await resp.json();
  await loadExports();
  openExportModal(f.id);
});

document.getElementById("export-copy-link-button").addEventListener("click", (event) => {
  const target = event.currentTarget.dataset.copy;
  if (target) copyToClipboard(target, event.currentTarget);
});

// Bug fix: this button existed in the HTML with no listener -- publishing
// state could never actually be changed. Mirrors board-reroll-button.
document.getElementById("export-reroll-button").addEventListener("click", async () => {
  if (!exportEditModalTargetId) return;
  if (!confirm("Generate a new secret link for this export? The old link will stop working immediately.")) return;
  const resp = await api(`/api/exports/${exportEditModalTargetId}/reroll-token`, {
    method: "POST",
  });
  if (!resp.ok) {
    showToast("Could not reroll token");
    return;
  }
  const updated = await resp.json();
  updateModalLinkDisplay(updated);
  showToast("Token rerolled", "success");
  await loadExports();
});

document.getElementById("export-delete-button").addEventListener("click", async () => {
  if (!exportEditModalTargetId) return;
  if (!confirm("Delete this export?")) return;
  await api(`/api/exports/${exportEditModalTargetId}`, { method: "DELETE" });
  exportEditModalDirty = false;
  exportEditModal.hide();
  await loadExports();
});

document.getElementById("export-modal").addEventListener("hide.bs.modal", async (event) => {
  if (!exportEditModalDirty || !exportEditModalTargetId) return;
  event.preventDefault();
  clearTimeout(exportEditModalSaveTimer);
  const ok = await saveExport(exportEditModalTargetId);
  // Only close if the save actually went through -- otherwise stay open so
  // the (still visible) validation error can be fixed, instead of retrying
  // the same failing save every time the user clicks "Close" again.
  if (ok) exportEditModal.hide();
});
document.getElementById("export-modal").addEventListener("hidden.bs.modal", () => {
  exportEditModalTargetId = null;
  loadExports();
});

// ----------------------
// region: boards helpers
// ----------------------

async function loadBoards() {
  const resp = await api("/api/boards");
  const items = await resp.json();
  const list = document.getElementById("boards-list");
  document.getElementById("boards-empty").classList.toggle("d-none", items.length > 0);
  list.innerHTML = "";
  for (const b of items) list.appendChild(renderBoardItem(b));
  return items;
}

function renderBoardItem(board) {
  const div = cloneTemplate("template-board-item");

  const showUnpublishedBadge = !board.published;
  const showProtectedBadge = !showUnpublishedBadge && !!board.protected;
  const showPublicBadge = !showUnpublishedBadge && !showProtectedBadge;

  div.querySelector("[data-badge-unpublished]").classList.toggle("d-none", !showUnpublishedBadge);
  div.querySelector("[data-badge-protected]").classList.toggle("d-none", !showProtectedBadge);
  div.querySelector("[data-badge-public]").classList.toggle("d-none", !showPublicBadge);

  div.querySelector("[data-name]").textContent = board.name;
  div.querySelector("[data-description]").textContent = board.description || "No description";
  div.querySelector("[data-stats]").textContent = pluralize(board.calendar_ids.length, "calendar");

  const { maskedLink, link } = generateBoardLinks(board);
  if (board.published) {
    const linkRow = div.querySelector("[data-link-row]");
    linkRow.classList.remove("d-none");
    linkRow.querySelector("[data-link]").href = link;
    linkRow.querySelector("[data-link]").textContent = maskedLink;
    linkRow.querySelector("[data-copy-button]").addEventListener("click", (event) => copyToClipboard(link, event.currentTarget));
  }

  div.querySelector("[data-edit]").addEventListener("click", () => openBoardModal(board.id));
  div.querySelector("[data-delete]").addEventListener("click", async () => {
    if (!confirm(`Delete board "${board.name}"?`)) return;
    await api(`/api/boards/${board.id}`, { method: "DELETE" });
    await loadBoards();
  });

  return div;
}

function updateBoardLinkDisplay(board) {
  const { maskedLink, link } = generateBoardLinks(board);
  setLinkDisplay({
    placeholderEl: document.getElementById("board-link-placeholder"),
    linkEl: document.getElementById("board-link-display"),
    copyBtn: document.getElementById("board-copy-link-button"),
    masked: maskedLink,
    target: link,
  });
}

async function openBoardModal(boardId) {
  boardEditModalTargetId = boardId;
  boardEditModalDirty = false;
  const board = await (await api(`/api/boards/${boardId}`)).json();

  document.getElementById("board-name").value = board.name;
  document.getElementById("board-name").classList.remove("is-invalid");
  document.getElementById("board-name-error").textContent = "";
  document.getElementById("board-description").value = board.description || "";
  document.getElementById("board-link-name").value = board.link_name || "";
  document.getElementById("board-link-name").classList.remove("is-invalid");
  document.getElementById("board-link-name-error").textContent = "";
  document.getElementById("board-protected").checked = board.protected;
  document.getElementById("board-published").checked = board.published;
  document.getElementById("board-save-status").textContent = "All changes saved";
  updateBoardLinkDisplay(board);

  const calendarsDiv = document.getElementById("board-calendars");
  calendarsDiv.querySelectorAll("[data-checkbox-row]").forEach((element) => element.remove()); // clear existing/old rows
  document.getElementById("board-calendars-empty").classList.toggle("d-none", exportsCache.length > 0);
  for (const export_ of exportsCache) {
    const row = cloneTemplate("template-checkbox-row");
    const checkbox = row.querySelector("[data-checkbox]");
    const label = row.querySelector("[data-label]");

    checkbox.id = `bchk-${export_.id}`;
    checkbox.value = export_.id;
    checkbox.checked = (board.calendar_ids || []).includes(export_.id);
    label.htmlFor = checkbox.id;
    label.textContent = export_.name;
    calendarsDiv.appendChild(row);
  }

  function scheduleSave() {
    boardEditModalDirty = true;
    document.getElementById("board-save-status").textContent = "Saving...";
    clearTimeout(boardEditModalSaveTimer);
    boardEditModalSaveTimer = setTimeout(() => saveBoard(boardId), 900);
  }

  document.getElementById("board-name").oninput = scheduleSave;
  document.getElementById("board-description").oninput = scheduleSave;
  document.getElementById("board-link-name").oninput = scheduleSave;
  document.getElementById("board-protected").onchange = scheduleSave;
  document.getElementById("board-published").onchange = scheduleSave;
  calendarsDiv.onchange = scheduleSave;

  boardEditModal.show();
}

const saveBoard = singleFlight(performBoardSave);

// Same success/failure contract as saveExport, for the same reason.
async function performBoardSave(boardId) {
  const payload = {
    name: document.getElementById("board-name").value.trim(),
    description: document.getElementById("board-description").value,
    link_name: document.getElementById("board-link-name").value.trim(),
    protected: document.getElementById("board-protected").checked,
    published: document.getElementById("board-published").checked,
    calendar_ids: Array.from(document.querySelectorAll("#board-calendars input[type=checkbox]:checked")).map((c) => c.value),
  };

  const response = await api(`/api/boards/${boardId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const statusElement = document.getElementById("board-save-status");
  const nameInput = document.getElementById("board-name");
  const nameErrorElement = document.getElementById("board-name-error");
  const linkNameInput = document.getElementById("board-link-name");
  const linkNameErrorElement = document.getElementById("board-link-name-error");

  if (!response.ok) {
    const error = await response.json();
    statusElement.textContent = "Not saved";
    const detailLower = String(error.detail || "").toLowerCase();

    // guess which field the error is about
    const isNameError = detailLower.includes("board name");
    const isLinkNameError = !isNameError && detailLower.includes("public");

    nameInput.classList.toggle("is-invalid", isNameError);
    nameErrorElement.textContent = isNameError ? error.detail : "";

    linkNameErrorElement.textContent = isLinkNameError ? error.detail : "";
    linkNameInput.classList.toggle("is-invalid", isLinkNameError);

    if (!isNameError && !isLinkNameError) showToast(error.detail || "Save failed");
    boardEditModalDirty = true;
    return false;
  }

  nameInput.classList.remove("is-invalid");
  nameErrorElement.textContent = "";
  linkNameInput.classList.remove("is-invalid");
  linkNameErrorElement.textContent = "";

  const updated = await response.json();
  updateBoardLinkDisplay(updated);
  statusElement.textContent = "All changes saved";
  boardEditModalDirty = false;
  await loadBoards();
  return true;
}

// region: boards event listeners

document.getElementById("new-board-button").addEventListener("click", async () => {
  // No name-picking step -- create with an auto-generated placeholder name
  // and go straight to the edit modal, where it can be renamed.
  const resp = await api("/api/boards", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!resp.ok) {
    showToast((await resp.json()).detail || "Could not create board");
    return;
  }
  const b = await resp.json();
  await loadBoards();
  openBoardModal(b.id);
});

document.getElementById("board-copy-link-button").addEventListener("click", (event) => {
  const target = event.currentTarget.dataset.copy;
  if (target) copyToClipboard(target, event.currentTarget);
});

document.getElementById("board-reroll-button").addEventListener("click", async () => {
  if (!boardEditModalTargetId) return;
  if (!confirm("Generate a new secret link for this board? The old link will stop working immediately.")) return;
  const resp = await api(`/api/boards/${boardEditModalTargetId}/reroll-token`, {
    method: "POST",
  });
  if (!resp.ok) {
    showToast("Could not reroll token");
    return;
  }
  const updated = await resp.json();
  updateBoardLinkDisplay(updated);
  showToast("Token rerolled", "success");
  await loadBoards();
});

document.getElementById("board-delete-button").addEventListener("click", async () => {
  if (!boardEditModalTargetId) return;
  if (!confirm("Delete this board?")) return;
  await api(`/api/boards/${boardEditModalTargetId}`, { method: "DELETE" });
  boardEditModalDirty = false;
  boardEditModal.hide();
  await loadBoards();
});

document.getElementById("board-modal").addEventListener("hide.bs.modal", async (event) => {
  if (!boardEditModalDirty || !boardEditModalTargetId) return;
  event.preventDefault();
  clearTimeout(boardEditModalSaveTimer);
  const ok = await saveBoard(boardEditModalTargetId);
  if (ok) boardEditModal.hide();
});
document.getElementById("board-modal").addEventListener("hidden.bs.modal", () => {
  boardEditModalTargetId = null;
  loadBoards();
});

// ------------
// region: init
// ------------

async function boot() {
  loginOffcanvas = loginOffcanvas || bootstrap.Offcanvas.getOrCreateInstance(document.getElementById("login-offcanvas"));
  exportEditModal = exportEditModal || bootstrap.Modal.getOrCreateInstance(document.getElementById("export-modal"));
  importWizardModal = importWizardModal || bootstrap.Modal.getOrCreateInstance(document.getElementById("import-wizard-modal"));
  importDetailsModal = importDetailsModal || bootstrap.Modal.getOrCreateInstance(document.getElementById("import-details-modal"));
  boardEditModal = boardEditModal || bootstrap.Modal.getOrCreateInstance(document.getElementById("board-modal"));
  setupSourcesInfoPopover();

  document.getElementById("loading-screen").classList.remove("d-none");

  const loggedIn = await checkAuth();
  if (loggedIn) {
    showLoggedIn();
    await loadExports();
    await Promise.all([loadImports(), loadBoards()]);
  } else {
    showLoggedOut();
    await loadLoginProviders();
  }

  document.getElementById("loading-screen").classList.add("d-none");
}

boot();
