// -----------------
// region: constants
// -----------------

// const locale = "de-DE";
const locale = navigator.language;

const naming = {
  DEFAULT_SOURCE_LABEL: "New Source",
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
let importWizardModalTargetId = null;
let importSourceEditModal = null;

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

function stripIcsSuffix(text) {
  return text.replace(/\.ics$/, "");
}

function transformToName(text) {
  return text.replace(/[_-]/g, " ");
}

// Guess a name for a source label based on given URL or filename
function guessSourceLabel({ fullUrl = null, filename = null }) {
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

  if (!guessed) return naming.DEFAULT_SOURCE_LABEL;

  // strip calendar file extension
  guessed = guessed.replace(/\.(ics|ical|ifb|vcs)$/i, "");
  // turn underscores and hyphens into spaces
  guessed = guessed.replace(/[_-]/g, " ");
  // capitalize each word
  guessed = guessed.replace(/\b\w/g, (char) => char.toUpperCase());

  return guessed || naming.DEFAULT_SOURCE_LABEL;
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
  const items = await resp.json();
  importsCache = items;
  const list = document.getElementById("imports-list");
  document.getElementById("imports-empty").classList.toggle("d-none", items.length > 0);
  list.innerHTML = "";
  for (const import_ of items) list.appendChild(renderImportItem(import_));
  return items;
}

function sourceIconMeta(s) {
  const addedLine = `Added ${formatTimestamp(s.created_at)}`;
  if (s.kind === "web") {
    const statusLine = s.last_success ? "Last pull succeeded" : `Failed: ${s.last_error || "Unknown error"}`;
    return {
      icon: "fa-cloud-arrow-down",
      colorClass: s.last_success ? "text-success" : "text-danger",
      title: `Web calendar\n${addedLine}\n${statusLine}\nClick to view or edit`,
    };
  }
  return {
    icon: "fa-file-lines",
    colorClass: "text-success",
    title: `Uploaded file\n${addedLine}\nClick to view or edit`,
  };
}

function fmtRange(s) {
  if (!s.range_start && !s.range_end) return "no events";
  return `${pluralize(s.event_count, "event")} \u00b7 ${formatTimestamp(s.range_start)} \u2013 ${formatTimestamp(s.range_end)}`;
}

// One flat line at every width: radio, clickable type icon, label, and an
// edit/delete button group that's just hidden below sm (rather than
// reordered/reflowed the way it used to be). Stats sit on their own line.
function renderSourceRow(imp, s, index) {
  const div = cloneTemplate("template-source-row");
  if (index % 2 === 1) div.classList.add("bg-body-tertiary");

  const iconMeta = sourceIconMeta(s);
  const icon = div.querySelector("[data-icon]");
  icon.className = `fa-solid ${iconMeta.icon} ${iconMeta.colorClass}`;

  const openEditBtn = div.querySelector("[data-open-edit]");
  openEditBtn.title = iconMeta.title;
  openEditBtn.addEventListener("click", () => openSourceEditModal(imp, s));

  const radio = div.querySelector("[data-activate]");
  radio.name = `active-source-${imp.id}`;
  radio.title = s.is_active ? "Active source" : "Make active";
  radio.checked = s.is_active;
  radio.addEventListener("change", async () => {
    const r = await api(`/api/imports/${imp.id}/sources/${s.id}/activate`, {
      method: "POST",
    });
    if (!r.ok) {
      showToast((await r.json()).detail || "Could not activate source");
      radio.checked = false;
      return;
    }
    await loadImports();
  });

  div.querySelector("[data-label]").textContent = s.label || "";

  div.querySelector("[data-actions] [data-edit]").addEventListener("click", () => openSourceEditModal(imp, s));

  const deleteBtn = div.querySelector("[data-actions] [data-delete-source]");
  deleteBtn.title = s.is_active ? "Cannot remove the active source" : "Remove source";
  deleteBtn.disabled = s.is_active;
  if (!s.is_active) {
    deleteBtn.addEventListener("click", async () => {
      if (!confirm(`Remove source "${s.label || s.kind}"?`)) return;
      await api(`/api/imports/${imp.id}/sources/${s.id}`, { method: "DELETE" });
      await loadImports();
    });
  }

  div.querySelector("[data-stats]").textContent = fmtRange(s);

  return div;
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
      showToast((await r.json()).detail || "Rename failed");
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

  acceptBtn.addEventListener("click", async () => {
    const value = nameInput.value.trim();
    if (!value || value === originalName) return;
    const ok = await save(value);
    if (ok) exitEditMode(value);
  });

  cancelBtn.addEventListener("click", () => {
    exitEditMode(originalName);
  });

  const sourcesDiv = div.querySelector("[data-sources]");
  div.querySelector("[data-sources-empty]").classList.toggle("d-none", import_.sources.length > 0);
  for (const [i, s] of import_.sources.entries()) sourcesDiv.appendChild(renderSourceRow(import_, s, i));

  div.querySelector("[data-delete]").addEventListener("click", async () => {
    if (!confirm(`Delete import "${import_.name}"?`)) return;
    await api(`/api/imports/${import_.id}`, { method: "DELETE" });
    await loadImports();
    await loadExports();
  });

  div.querySelector("[data-add-source]").addEventListener("click", () => {
    openImportWizard(import_.id);
  });

  return div;
}

// ---------- source edit modal (both web and uploaded-file sources) ----------

function renderCopyTargetItem(label, onClick) {
  const li = cloneTemplate("template-dropdown-target-item");
  const btn = li.querySelector("[data-target-button]");
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return li;
}

async function openSourceEditModal(imp, source) {
  // ----- title: label display + inline rename, same accept/cancel pattern
  // used for the label on the old overview row -----
  const titleDisplay = document.getElementById("source-edit-title-display");
  const titleControls = document.getElementById("source-edit-title-controls");
  const titleInput = document.getElementById("source-edit-title-input");
  const titleEditBtn = document.getElementById("source-edit-title-edit");
  const titleAcceptBtn = document.getElementById("source-edit-title-accept");
  const titleCancelBtn = document.getElementById("source-edit-title-cancel");

  titleDisplay.textContent = source.label || "";
  titleControls.classList.add("d-none");
  titleDisplay.classList.remove("d-none");
  titleEditBtn.classList.remove("d-none");

  let originalLabel = source.label || "";

  titleEditBtn.onclick = () => {
    originalLabel = source.label || "";
    titleInput.value = originalLabel;
    titleAcceptBtn.disabled = true;
    titleDisplay.classList.add("d-none");
    titleEditBtn.classList.add("d-none");
    titleControls.classList.remove("d-none");
    titleInput.focus();
    titleInput.select();
  };

  function exitTitleEditMode(label) {
    titleDisplay.textContent = label;
    source.label = label;
    titleControls.classList.add("d-none");
    titleDisplay.classList.remove("d-none");
    titleEditBtn.classList.remove("d-none");
  }

  async function saveLabel(newLabel) {
    const response = await api(`/api/imports/${imp.id}/sources/${source.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: newLabel }),
    });
    if (!response.ok) {
      showToast((await response.json()).detail || "Rename failed");
      return false;
    }
    await loadImports();
    return true;
  }

  titleInput.oninput = () => {
    titleAcceptBtn.disabled = titleInput.value.trim() === originalLabel || !titleInput.value.trim();
  };
  titleInput.onkeydown = (event) => {
    if (event.key === "Enter" && !titleAcceptBtn.disabled) titleAcceptBtn.click();
    if (event.key === "Escape") titleCancelBtn.click();
  };
  titleAcceptBtn.onclick = async () => {
    const value = titleInput.value.trim();
    if (!value || value === originalLabel) return;
    const ok = await saveLabel(value);
    if (ok) exitTitleEditMode(value);
  };
  titleCancelBtn.onclick = () => exitTitleEditMode(originalLabel);

  // ----- low-key type indicator + active badge, both behind the title -----
  document.getElementById("source-edit-type-label").textContent = source.kind === "web" ? "Web calendar" : "Static file";
  document.getElementById("source-edit-active-badge").classList.toggle("d-none", !source.is_active);

  const deleteBtn = document.getElementById("source-edit-delete-button");
  deleteBtn.title = source.is_active ? "Cannot remove the active source" : "Remove source";
  deleteBtn.disabled = source.is_active;
  deleteBtn.onclick = source.is_active
    ? null
    : async () => {
        if (!confirm(`Remove source "${source.label || source.kind}"?`)) return;
        await api(`/api/imports/${imp.id}/sources/${source.id}`, { method: "DELETE" });
        importSourceEditModal.hide();
        await loadImports();
      };

  const webFields = document.getElementById("source-edit-web-fields");
  const webBackupSection = document.getElementById("source-edit-web-backup-section");
  const fileFields = document.getElementById("source-edit-file-fields");
  const fileActions = document.getElementById("source-edit-file-actions");
  const isWeb = source.kind === "web";
  webFields.classList.toggle("d-none", !isWeb);
  webBackupSection.classList.toggle("d-none", !isWeb);
  fileFields.classList.toggle("d-none", isWeb);
  fileActions.classList.toggle("d-none", isWeb);
  fileActions.classList.toggle("d-flex", !isWeb);

  if (isWeb) {
    const successEl = document.getElementById("source-edit-status-success");
    const failEl = document.getElementById("source-edit-status-fail");
    successEl.classList.toggle("d-none", !source.last_success);
    failEl.classList.toggle("d-none", !!source.last_success);
    if (!source.last_success) {
      document.getElementById("source-edit-status-fail-reason").textContent = source.last_error || "Unknown error";
    }

    document.getElementById("source-edit-last-pulled").textContent = formatTimestamp(source.last_pulled_at);
    document.getElementById("source-edit-url").href = source.url;
    document.getElementById("source-edit-url").textContent = formatUrl(source.url);

    const backupChk = document.getElementById("source-edit-contribute-backup");
    backupChk.checked = !!source.contribute_backup;
    backupChk.onchange = async () => {
      const r = await api(`/api/imports/${imp.id}/sources/${source.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contribute_backup: backupChk.checked }),
      });
      if (!r.ok) {
        showToast((await r.json()).detail || "Could not update backup setting");
        backupChk.checked = !backupChk.checked;
        return;
      }
      await loadImports();
    };

    const listDiv = document.getElementById("source-edit-snapshots-list");
    const loadingEl = document.getElementById("source-edit-snapshots-loading");
    const emptyEl = document.getElementById("source-edit-snapshots-empty");
    listDiv.innerHTML = "";
    emptyEl.classList.add("d-none");
    loadingEl.classList.remove("d-none");

    const [snapshots, allImports] = await Promise.all([
      api(`/api/imports/${imp.id}/sources/${source.id}/snapshots`).then((r) => r.json()),
      Promise.resolve(importsCache),
    ]);

    loadingEl.classList.add("d-none");
    emptyEl.classList.toggle("d-none", snapshots.length > 0);

    snapshots.forEach((snap, i) => {
      const row = cloneTemplate("template-snapshot-row");
      const range = snap.range_start || snap.range_end ? `${formatTimestamp(snap.range_start)} \u2013 ${formatTimestamp(snap.range_end)}` : "no events";
      row.querySelector("[data-fetched-at]").textContent = formatTimestamp(snap.fetched_at);
      row.querySelector("[data-latest-badge]").classList.toggle("d-none", i !== 0);
      row.querySelector("[data-stats]").textContent = `${pluralize(snap.event_count, "event")} \u00b7 ${range}`;

      row.querySelector("[data-download-button]").addEventListener("click", () => {
        downloadFile(`/api/imports/${imp.id}/sources/${source.id}/snapshots/${snap.id}/download`, `${source.label || "calendar"}-${snap.fetched_at}.ics`);
      });

      const menu = row.querySelector("[data-target-list]");
      for (const target of allImports) {
        menu.appendChild(
          renderCopyTargetItem(target.name, async () => {
            const label = `${source.label} Snapshot from ${formatTimestamp(snap.fetched_at)}`;
            const r = await api(`/api/imports/${target.id}/snapshot`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ snapshot_id: snap.id, label }),
            });
            if (!r.ok) {
              showToast((await r.json()).detail || "Could not save snapshot");
              return;
            }
            showToast(`Saved as a new file source on "${target.name}"`, "success");
            await loadImports();
          }),
        );
      }
      listDiv.appendChild(row);
    });
  } else {
    document.getElementById("source-edit-file-added").textContent = formatTimestamp(source.created_at);
    document.getElementById("source-edit-file-stats").textContent = fmtRange(source);

    const downloadBtn = document.getElementById("source-edit-file-download");
    downloadBtn.onclick = (event) => {
      event.preventDefault();
      downloadFile(`/api/imports/${imp.id}/sources/${source.id}/download`, `${source.label || "calendar"}.ics`);
    };

    const copyMenu = document.getElementById("source-edit-file-copy-targets");
    copyMenu.innerHTML = "";
    for (const target of importsCache) {
      copyMenu.appendChild(
        renderCopyTargetItem(target.name, async () => {
          const r = await api(`/api/imports/${target.id}/sources/${source.id}/copy`, {
            method: "POST",
          });
          if (!r.ok) {
            showToast((await r.json()).detail || "Could not copy source");
            return;
          }
          showToast(`Copied to "${target.name}"`, "success");
          await loadImports();
        }),
      );
    }
  }

  importSourceEditModal.show();
}

function openImportWizard(importId) {
  importWizardModalTargetId = importId;
  importWizardModal.show();
}

// -------------------------------
// region: imports event listeners
// -------------------------------

document.getElementById("new-import-button").addEventListener("click", async () => {
  // No name-picking step -- create with an auto-generated placeholder name
  // and go straight into the add-source wizard, matching export/board.
  const resp = await api("/api/imports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!resp.ok) {
    showToast((await resp.json()).detail || "Could not create import");
    return;
  }
  const newImport = await resp.json();
  await loadImports();
  openImportWizard(newImport.id);
});

document.getElementById("import-wizard-modal").addEventListener("show.bs.modal", () => {
  document.getElementById("wizard-url").value = "";
  document.getElementById("wizard-file").value = "";
  document.getElementById("wizard-label").value = "";
  document.getElementById("wizard-label").placeholder = naming.DEFAULT_SOURCE_LABEL;
  bootstrap.Tab.getOrCreateInstance(document.getElementById("import-tab-url-button")).show();
});

document.getElementById("wizard-url").addEventListener("input", (event) => {
  const labelInput = document.getElementById("wizard-label");
  labelInput.placeholder = labelInput.value || guessSourceLabel({ fullUrl: event.target.value.trim() });
});

document.getElementById("wizard-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  const labelInput = document.getElementById("wizard-label");
  labelInput.placeholder = labelInput.value || guessSourceLabel({ filename: file?.name });
});

document.getElementById("wizard-ok-button").addEventListener("click", async () => {
  const kind = document.getElementById("import-tab-file").classList.contains("active") ? "file" : "url";
  const importId = importWizardModalTargetId;
  const labelInput = document.getElementById("wizard-label");

  if (kind === "url") {
    const url = document.getElementById("wizard-url").value.trim();
    if (!url) {
      showToast("Enter a calendar URL");
      return;
    }
    const label = labelInput.value.trim() || guessSourceLabel({ fullUrl: url });
    const resp = await api(`/api/imports/${importId}/urls`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, label }),
    });
    if (!resp.ok) {
      showToast((await resp.json()).detail || "Could not add calendar URL");
      importWizardModalTargetId = null;
      importWizardModal.hide();
      await loadImports();
      return;
    }
  } else {
    const fileInput = document.getElementById("wizard-file");
    const file = fileInput.files[0];
    if (!file) {
      showToast("Choose a file");
      return;
    }
    const label = labelInput.value.trim() || guessSourceLabel({ filename: file.name });
    const fd = new FormData();
    fd.append("file", file);
    fd.append("label", label);
    const resp = await api(`/api/imports/${importId}/files`, {
      method: "POST",
      body: fd,
    });
    if (!resp.ok) {
      showToast((await resp.json()).detail || "Could not add file");
      importWizardModalTargetId = null;
      importWizardModal.hide();
      await loadImports();
      return;
    }
  }

  importWizardModalTargetId = null;
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

  const warnEl = div.querySelector("[data-duplicate-warning]");
  if (export_.duplicate_warning) {
    warnEl.classList.remove("d-none");
    warnEl.title = `Same rule as: ${export_.duplicate_warning.map((d) => d.name).join(", ")}`;
  }

  div.querySelector("[data-description]").textContent = export_.description || "No description";
  div.querySelector("[data-stats]").textContent =
    `${pluralize(export_.input_count, "input calendar")} \u00b7 ${pluralize(export_.output_count, "event")} \u00b7 updated ${formatTimestamp(export_.last_output_change_at)}`;

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

const SPECIALS = [
  "today()",
  "tomorrow()",
  "yesterday()",
  "thisweek()",
  "lastweek()",
  "nextweek()",
  "thismonth()",
  "lastmonth()",
  "nextmonth()",
  "thisyear()",
  "lastyear()",
  "nextyear()",
];

// This builds markup from arbitrary, unbounded user-typed rule text (the
// filter DSL): the number and kind of highlight spans depend entirely on
// what's been typed, so there's no fixed template that could describe it.
// It stays as a dedicated syntax highlighter rather than a static template;
// every literal piece of user text still goes in via escapeHtml, so nothing
// unescaped ever reaches innerHTML.
function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function highlightRule(text) {
  return text
    .split(/(\s+)/)
    .map((token) => {
      if (!token.trim()) return token;
      const match = token.match(/^(-)?([a-zA-Z-]+):(.*)$/);
      if (!match) return `<span class="token-value">${escapeHtml(token)}</span>`;
      const [, isNegated, name, value] = match;
      const values = value
        .split(",")
        .map((value) => {
          if (SPECIALS.includes(value.toLowerCase())) return `<span class="tok-special" title="${value}">${escapeHtml(value)}</span>`;
          return `<span class="token-value">${escapeHtml(value)}</span>`;
        })
        .join('<span class="token-colon">,</span>');
      return `${isNegated ? '<span class="token-negated">-</span>' : ""}<span class="token-predicate">${escapeHtml(name)}</span><span class="token-colon">:</span>${values}`;
    })
    .join("");
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
  document.getElementById("export-rule-error").textContent = "";
  document.getElementById("save-status").textContent = "All changes saved";
  updateModalLinkDisplay(exportData);

  const sourcesDiv = document.getElementById("export-sources");
  sourcesDiv.querySelectorAll("[data-checkbox-row]").forEach((el) => el.remove());
  document.getElementById("export-sources-empty").classList.toggle("d-none", importsCache.length > 0);
  for (const imp of importsCache) {
    const row = cloneTemplate("template-checkbox-row");
    const checkbox = row.querySelector("[data-checkbox]");
    checkbox.id = `chk-${imp.id}`;
    checkbox.value = imp.id;
    checkbox.checked = (exportData.import_ids || []).includes(imp.id);
    const label = row.querySelector("[data-label]");
    label.htmlFor = checkbox.id;
    label.textContent = imp.name;
    sourcesDiv.appendChild(row);
  }

  const ruleTextarea = document.getElementById("export-rule");
  ruleTextarea.value = exportData.rule_text || "";
  const preview = document.getElementById("export-rule-preview");
  const renderPreview = () => (preview.innerHTML = highlightRule(ruleTextarea.value) || "&nbsp;");
  renderPreview();

  function scheduleSave() {
    exportEditModalDirty = true;
    document.getElementById("save-status").textContent = "Saving...";
    clearTimeout(exportEditModalSaveTimer);
    exportEditModalSaveTimer = setTimeout(() => saveExport(exportId), 900);
  }

  document.getElementById("export-name").oninput = scheduleSave;
  document.getElementById("export-description").oninput = scheduleSave;
  document.getElementById("export-link-name").oninput = scheduleSave;
  document.getElementById("export-protected").onchange = scheduleSave;
  document.getElementById("export-published").onchange = scheduleSave;
  ruleTextarea.oninput = () => {
    renderPreview();
    scheduleSave();
  };
  sourcesDiv.onchange = scheduleSave;

  exportEditModal.show();
}

// Returns true on success, false on failure -- callers (in particular the
// close-the-modal handler) must not blindly retry on failure, or a
// permanently-invalid value (e.g. a name that collides) turns into an
// infinite save loop every time hide() is called again.
async function saveExport(exportId) {
  const payload = {
    name: document.getElementById("export-name").value.trim(),
    description: document.getElementById("export-description").value,
    link_name: document.getElementById("export-link-name").value.trim(),
    protected: document.getElementById("export-protected").checked,
    published: document.getElementById("export-published").checked,
    rule_text: document.getElementById("export-rule").value,
    import_ids: Array.from(document.querySelectorAll("#export-sources input[type=checkbox]:checked")).map((c) => c.value),
  };
  const resp = await api(`/api/exports/${exportId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const statusEl = document.getElementById("save-status");
  const ruleErrEl = document.getElementById("export-rule-error");
  const nameInput = document.getElementById("export-name");
  const nameErrEl = document.getElementById("export-name-error");
  const pubErrEl = document.getElementById("export-link-name-error");
  const pubInput = document.getElementById("export-link-name");
  if (!resp.ok) {
    const err = await resp.json();
    statusEl.textContent = "Not saved";
    const detailLower = String(err.detail || "").toLowerCase();

    // guess which field the error is about, same approach as the board modal
    const isNameError = detailLower.includes("export name");
    const isLinkNameError = !isNameError && detailLower.includes("public");

    nameInput.classList.toggle("is-invalid", isNameError);
    nameErrEl.textContent = isNameError ? err.detail : "";

    pubErrEl.textContent = isLinkNameError ? err.detail : "";
    pubInput.classList.toggle("is-invalid", isLinkNameError);
    if (!isNameError && !isLinkNameError) showToast(err.detail || "Save failed");
    return false;
  }
  nameInput.classList.remove("is-invalid");
  nameErrEl.textContent = "";
  pubErrEl.textContent = "";
  pubInput.classList.remove("is-invalid");
  const updated = await resp.json();
  const ruleErrs = (updated.rule_errors || []).map((event) => event.message).join("; ");
  ruleErrEl.textContent = ruleErrs;
  document.getElementById("export-rule").classList.toggle("is-invalid", !!ruleErrs);
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

// Same success/failure contract as saveExport, for the same reason.
async function saveBoard(boardId) {
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
  importSourceEditModal = importSourceEditModal || bootstrap.Modal.getOrCreateInstance(document.getElementById("source-edit-modal"));
  boardEditModal = boardEditModal || bootstrap.Modal.getOrCreateInstance(document.getElementById("board-modal"));

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
