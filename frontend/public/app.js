const API = `${window.location.protocol}//${window.location.hostname}:4071/api`;

const $ = (id) => document.getElementById(id);

const state = {
  token: localStorage.getItem("token") || null,
  user: localStorage.getItem("user") || null,
};

function setToken(token, user) {
  state.token = token;
  state.user = user;
  if (token) {
    localStorage.setItem("token", token);
    localStorage.setItem("user", user);
  } else {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
  }
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401) {
    setToken(null, null);
    showLogin();
    throw new Error("unauthorized");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(data.error || "request failed");
  return data;
}

async function apiForm(path, formData) {
  const headers = {};
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  const res = await fetch(API + path, { method: "POST", headers, body: formData });
  if (res.status === 401) {
    setToken(null, null);
    showLogin();
    throw new Error("unauthorized");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(data.error || "request failed");
  return data;
}

async function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  ta.setSelectionRange(0, ta.value.length);
  try {
    if (!document.execCommand("copy")) throw new Error("execCommand returned false");
  } finally {
    document.body.removeChild(ta);
  }
}

function showLogin() {
  $("login-view").classList.remove("hidden");
  $("app-view").classList.add("hidden");
}

function showApp() {
  $("login-view").classList.add("hidden");
  $("app-view").classList.remove("hidden");
  $("who").textContent = state.user || "admin";
  loadKeys();
  loadCurrentRelease();
}

async function loadCurrentRelease() {
  const el = $("current-release");
  try {
    const m = await api("/admin/release");
    const sizeMB = (m.size / 1024 / 1024).toFixed(1);
    const uploaded = fmtDate(m.uploaded_at);
    el.innerHTML = `Current: <b>v${escapeHtml(m.version)}</b> &middot; ${sizeMB} MB &middot; uploaded ${escapeHtml(uploaded)}`;
  } catch (err) {
    if (err.message === "no release") {
      el.textContent = "No release published yet.";
    } else {
      el.textContent = "Could not load current version.";
    }
  }
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  const username = $("username").value.trim();
  const password = $("password").value;
  try {
    const res = await fetch(API + "/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "login failed");
    setToken(data.token, username);
    showApp();
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

$("logout").addEventListener("click", () => {
  setToken(null, null);
  showLogin();
});

$("create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("key-name").value.trim();
  if (!name) {
    alert("Enter a name, or click Generate for an auto-named key.");
    return;
  }
  await createOrGenerate("/admin/keys/", { name });
});

$("generate-btn").addEventListener("click", async () => {
  const name = $("key-name").value.trim();
  await createOrGenerate("/admin/keys/generate", name ? { name } : {});
});

async function createOrGenerate(path, body) {
  try {
    const k = await api(path, { method: "POST", body: JSON.stringify(body) });
    $("key-name").value = "";
    showNewKey(k.key, `Key "${k.name}" created.`);
    await loadKeys();
  } catch (err) {
    alert("Failed: " + err.message);
  }
}

function showNewKey(value, label) {
  $("new-key").classList.remove("hidden");
  $("new-key-value").textContent = value;
  $("new-key-label").textContent = label || "Save this key now. It will not be shown again.";
}

$("copy-key").addEventListener("click", async () => {
  const val = $("new-key-value").textContent;
  try {
    await copyToClipboard(val);
    $("copy-key").textContent = "Copied";
    setTimeout(() => { $("copy-key").textContent = "Copy"; }, 1500);
  } catch (err) {
    $("copy-key").textContent = "Copy failed";
    setTimeout(() => { $("copy-key").textContent = "Copy"; }, 2000);
  }
});

async function loadKeys() {
  $("list-error").textContent = "";
  try {
    const keys = await api("/admin/keys/");
    const body = $("keys-body");
    body.innerHTML = "";
    if (!keys.length) {
      $("empty").classList.remove("hidden");
      return;
    }
    $("empty").classList.add("hidden");
    for (const k of keys) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(k.name)}</td>
        <td>${fmtDate(k.created_at)}</td>
        <td>${k.last_used ? fmtDate(k.last_used) : '<span class="muted">never</span>'}</td>
        <td><code>${k.id.slice(0, 8)}...</code></td>
        <td class="actions">
          <button class="ghost" data-act="rotate">Rotate</button>
          <button class="ghost danger" data-act="delete">Delete</button>
        </td>
      `;
      tr.querySelector('[data-act="rotate"]').addEventListener("click", () => rotateKey(k.id, k.name));
      tr.querySelector('[data-act="delete"]').addEventListener("click", () => deleteKey(k.id, k.name));
      body.appendChild(tr);
    }
  } catch (err) {
    $("list-error").textContent = err.message;
  }
}

async function rotateKey(id, name) {
  if (!confirm(`Rotate key "${name}"? The current secret will be invalidated immediately.`)) return;
  try {
    const k = await api("/admin/keys/" + id + "/rotate", { method: "POST" });
    showNewKey(k.key, `Key "${k.name}" rotated. Old secret is now invalid.`);
    await loadKeys();
  } catch (err) {
    alert("Rotate failed: " + err.message);
  }
}

async function deleteKey(id, name) {
  if (!confirm(`Delete key "${name}"? This cannot be undone.`)) return;
  try {
    await api("/admin/keys/" + id, { method: "DELETE" });
    await loadKeys();
  } catch (err) {
    alert("Delete failed: " + err.message);
  }
}

$("release-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const version = $("release-version").value.trim();
  const notes = $("release-notes").value;
  const fileInput = $("release-file");
  const file = fileInput.files[0];
  const errEl = $("release-error");
  const progressEl = $("release-progress");
  const statusEl = $("release-status");
  const btn = $("release-submit");

  errEl.textContent = "";
  statusEl.classList.add("hidden");
  if (!version) { errEl.textContent = "Version required"; return; }
  if (!file) { errEl.textContent = "Choose an .exe file"; return; }

  btn.disabled = true;
  btn.textContent = "Uploading...";
  progressEl.classList.remove("hidden");
  progressEl.textContent = `Uploading ${(file.size / 1024 / 1024).toFixed(1)} MB...`;

  try {
    const fd = new FormData();
    fd.append("version", version);
    fd.append("notes", notes);
    fd.append("file", file);
    const resp = await apiForm("/admin/release", fd);
    progressEl.classList.add("hidden");
    statusEl.classList.remove("hidden");
    statusEl.innerHTML = `
      <div><b>Published v${escapeHtml(resp.version)}</b></div>
      <div class="muted">sha256: <code>${escapeHtml(resp.sha256)}</code></div>
      <div class="muted">size: ${(resp.size / 1024 / 1024).toFixed(1)} MB</div>
    `;
    $("release-version").value = "";
    $("release-notes").value = "";
    fileInput.value = "";
    loadCurrentRelease();
  } catch (err) {
    errEl.textContent = err.message;
    progressEl.classList.add("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Publish";
  }
});

function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

if (state.token) showApp(); else showLogin();
