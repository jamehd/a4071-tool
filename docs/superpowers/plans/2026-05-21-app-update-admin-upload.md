# Auto-Update Admin Upload — Implementation Plan (delta)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the env-var-driven release configuration with an authenticated admin upload endpoint, so admins can ship a new exe without SSHing into the server or restarting the backend.

**Architecture:** Backend persists release state in a `release.json` manifest plus the existing `A4071-Tool.exe` + `A4071-Tool.exe.sha256` files inside `UPDATE_DIR`. A new `POST /api/admin/release` endpoint (gated by the existing JWT admin auth) accepts a multipart upload of `version` + `notes` + `file`, streams it to disk while hashing, writes the sidecar files atomically, and updates the manifest. `/api/version` and `/api/download` are unchanged in shape but now sourced from disk state rather than env. The `./updates` bind mount becomes rw. A PowerShell helper `tool/release.ps1` wraps login + upload as a single command for the admin.

**Tech Stack:** Go 1.22 stdlib (`crypto/sha256`, `mime/multipart`, `net/http.MaxBytesReader`, `os`, `encoding/json`). PowerShell 7 for the helper. No new third-party deps.

**Spec:** Extends `docs/superpowers/specs/2026-05-21-app-update-design.md`. The original "Server-side configuration" section (env-var-driven release) is superseded by the manifest-driven design below.

---

## What changes vs the original plan

| Aspect | Original (Tasks 1-3) | This delta |
|---|---|---|
| Where the release version comes from | `UPDATE_VERSION` env var | `release.json` on disk under `UPDATE_DIR` |
| Where release notes come from | `UPDATE_NOTES` env var | `release.json` on disk |
| How a new release lands on the server | SCP file + edit compose + restart | `POST /api/admin/release` (multipart) |
| Bind mount | `./updates:/srv/updates:ro` | `./updates:/srv/updates` (rw) |
| Restart on new release | Required | Not required |

---

## File Structure

**Backend:**
- Modify `backend/update.go` — replace env-driven `loadUpdateConfig` with disk-driven; rewrite `handleVersion`/`handleDownload` to read from a manifest; add `handleUploadRelease`.
- Modify `backend/main.go` — wire `POST /api/admin/release` behind `adminAuth`.

**Infra:**
- Modify `docker-compose.yml` — drop `UPDATE_VERSION`/`UPDATE_NOTES` env, change volume to rw.
- Modify `updates/README.md` — document the new workflow.

**Tooling:**
- Create `tool/release.ps1` — local helper invoking login + upload.

---

## Task 11: Backend — manifest-driven release + upload endpoint

**Files:**
- Rewrite: `backend/update.go`
- Modify: `backend/main.go`

- [ ] **Step 1: Rewrite `backend/update.go`**

Replace the entire contents of `backend/update.go` with:

```go
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	releaseExeName      = "A4071-Tool.exe"
	releaseShaName      = "A4071-Tool.exe.sha256"
	releaseManifestName = "release.json"
	maxUploadBytes      = 200 * 1024 * 1024
)

type updateConfig struct {
	dir string
	mu  sync.Mutex
}

type releaseManifest struct {
	Version    string    `json:"version"`
	Notes      string    `json:"notes"`
	UploadedAt time.Time `json:"uploaded_at"`
}

func loadUpdateConfig() updateConfig {
	return updateConfig{
		dir: getenv("UPDATE_DIR", "/srv/updates"),
	}
}

func (c *updateConfig) manifestPath() string { return filepath.Join(c.dir, releaseManifestName) }
func (c *updateConfig) exePath() string      { return filepath.Join(c.dir, releaseExeName) }
func (c *updateConfig) shaPath() string      { return filepath.Join(c.dir, releaseShaName) }

func (c *updateConfig) readManifest() (releaseManifest, error) {
	var m releaseManifest
	data, err := os.ReadFile(c.manifestPath())
	if err != nil {
		return m, err
	}
	if err := json.Unmarshal(data, &m); err != nil {
		return m, err
	}
	return m, nil
}

func (a *App) handleVersion(w http.ResponseWriter, r *http.Request) {
	m, err := a.update.readManifest()
	if err != nil || m.Version == "" {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	st, err := os.Stat(a.update.exePath())
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	shaBytes, err := os.ReadFile(a.update.shaPath())
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	writeJSON(w, 200, map[string]any{
		"latest": m.Version,
		"notes":  m.Notes,
		"sha256": strings.ToLower(strings.TrimSpace(string(shaBytes))),
		"size":   st.Size(),
	})
}

func (a *App) handleDownload(w http.ResponseWriter, r *http.Request) {
	m, err := a.update.readManifest()
	if err != nil || m.Version == "" {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	f, err := os.Open(a.update.exePath())
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "stat failed"})
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", `attachment; filename="A4071-Tool.exe"`)
	http.ServeContent(w, r, releaseExeName, st.ModTime(), f)
}

func (a *App) handleUploadRelease(w http.ResponseWriter, r *http.Request) {
	a.update.mu.Lock()
	defer a.update.mu.Unlock()

	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid multipart: " + err.Error()})
		return
	}

	version := strings.TrimSpace(r.FormValue("version"))
	if version == "" {
		writeJSON(w, 400, map[string]string{"error": "version required"})
		return
	}
	notes := r.FormValue("notes")

	file, _, err := r.FormFile("file")
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": "file required"})
		return
	}
	defer file.Close()

	if err := os.MkdirAll(a.update.dir, 0o755); err != nil {
		writeJSON(w, 500, map[string]string{"error": "mkdir failed: " + err.Error()})
		return
	}

	partPath := a.update.exePath() + ".part"
	out, err := os.Create(partPath)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "create temp failed: " + err.Error()})
		return
	}
	h := sha256.New()
	if _, err := io.Copy(io.MultiWriter(out, h), file); err != nil {
		out.Close()
		os.Remove(partPath)
		writeJSON(w, 500, map[string]string{"error": "write failed: " + err.Error()})
		return
	}
	if err := out.Close(); err != nil {
		os.Remove(partPath)
		writeJSON(w, 500, map[string]string{"error": "close failed: " + err.Error()})
		return
	}
	sum := hex.EncodeToString(h.Sum(nil))

	if err := os.Rename(partPath, a.update.exePath()); err != nil {
		os.Remove(partPath)
		writeJSON(w, 500, map[string]string{"error": "rename failed: " + err.Error()})
		return
	}

	if err := os.WriteFile(a.update.shaPath(), []byte(sum), 0o644); err != nil {
		writeJSON(w, 500, map[string]string{"error": "write sha256 failed: " + err.Error()})
		return
	}

	manifest := releaseManifest{
		Version:    version,
		Notes:      notes,
		UploadedAt: time.Now().UTC(),
	}
	manifestData, err := json.Marshal(manifest)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "marshal manifest failed"})
		return
	}
	if err := os.WriteFile(a.update.manifestPath(), manifestData, 0o644); err != nil {
		writeJSON(w, 500, map[string]string{"error": "write manifest failed: " + err.Error()})
		return
	}

	st, _ := os.Stat(a.update.exePath())
	writeJSON(w, 200, map[string]any{
		"status":  "ok",
		"version": version,
		"sha256":  sum,
		"size":    st.Size(),
	})
}
```

- [ ] **Step 2: Wire the upload route**

In `backend/main.go`, find the existing admin-keys route group (around line 79-86) and add the release route alongside. Final state of that block:

```go
r.Route("/api/admin/keys", func(r chi.Router) {
    r.Use(app.adminAuth)
    r.Get("/", app.listKeys)
    r.Post("/", app.createKey)
    r.Post("/generate", app.generateKey)
    r.Post("/{id}/rotate", app.rotateKey)
    r.Delete("/{id}", app.deleteKey)
})

r.With(app.adminAuth).Post("/api/admin/release", app.handleUploadRelease)
```

- [ ] **Step 3: Build verification**

```bash
cd D:/MMO/a4071-tool/backend
go build ./...
```

Expected: clean exit.

- [ ] **Step 4: Commit**

```bash
git add backend/update.go backend/main.go
git commit -m "feat(backend): replace env-driven release with admin upload endpoint"
```

---

## Task 12: Infra — docker-compose rw mount + drop env vars

**Files:**
- Modify: `docker-compose.yml`
- Modify: `updates/README.md`

- [ ] **Step 1: Update the backend service in `docker-compose.yml`**

Replace the `backend` service's `environment:` and `volumes:` blocks:

```yaml
  backend:
    build: ./backend
    container_name: a4071_backend
    restart: unless-stopped
    environment:
      DATABASE_URL: postgres://app:app_secret@postgres:5432/apikeys?sslmode=disable
      ADMIN_USERNAME: admin
      ADMIN_PASSWORD: adminpass
      JWT_SECRET: change_me_in_prod_super_secret_key
      PORT: "4071"
      UPDATE_DIR: /srv/updates
    volumes:
      - ./updates:/srv/updates
    ports:
      - "4071:4071"
    depends_on:
      postgres:
        condition: service_healthy
```

Changes vs current: removed `UPDATE_VERSION` and `UPDATE_NOTES` env keys; removed `:ro` suffix on the volume.

- [ ] **Step 2: Rewrite `updates/README.md`**

```markdown
# Release Drop Directory

The backend writes/reads release artifacts here. **Do not drop files
manually** — use the admin upload endpoint instead.

Files this directory will hold after the first release:

- `release.json` — `{version, notes, uploaded_at}` manifest written by
  the backend.
- `A4071-Tool.exe` — the binary served by `/api/download`.
- `A4071-Tool.exe.sha256` — single-line lowercase hex SHA-256 of the exe.

## How to publish a new release

From a developer machine with the built `tool/dist/A4071-Tool.exe`:

```powershell
.\tool\release.ps1 `
    -Base http://a4071-tool.j4m.dev:4071 `
    -User admin `
    -Pass <admin-password> `
    -Version 0.2.0 `
    -Notes "- Sửa lỗi X\n- Cải tiến Y" `
    -File tool\dist\A4071-Tool.exe
```

The script logs in, posts the multipart upload, and the backend writes
all three files atomically. No SSH, no restart needed.
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml updates/README.md
git commit -m "feat(infra): drop UPDATE_* env vars and switch updates volume to rw"
```

---

## Task 13: Tool — `release.ps1` helper

**Files:**
- Create: `tool/release.ps1`

- [ ] **Step 1: Create `tool/release.ps1`**

```powershell
<#
.SYNOPSIS
    Upload a new A4071-Tool release to the backend.

.EXAMPLE
    .\release.ps1 -Base http://a4071-tool.j4m.dev:4071 `
        -User admin -Pass secret `
        -Version 0.2.0 -Notes "- Sửa lỗi" `
        -File dist\A4071-Tool.exe
#>
param(
    [Parameter(Mandatory)][string]$Base,
    [Parameter(Mandatory)][string]$User,
    [Parameter(Mandatory)][string]$Pass,
    [Parameter(Mandatory)][string]$Version,
    [string]$Notes = "",
    [Parameter(Mandatory)][string]$File
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $File)) {
    throw "File not found: $File"
}
$fileItem = Get-Item -LiteralPath $File
Write-Host "Uploading $($fileItem.FullName) ($([math]::Round($fileItem.Length / 1MB, 1)) MB) as v$Version"

$loginBody = @{ username = $User; password = $Pass } | ConvertTo-Json -Compress
$login = Invoke-RestMethod -Uri "$Base/api/admin/login" `
    -Method Post -ContentType "application/json" -Body $loginBody
$token = $login.token
if (-not $token) { throw "Login failed: no token in response" }

$form = @{
    version = $Version
    notes   = $Notes
    file    = $fileItem
}
$resp = Invoke-RestMethod -Uri "$Base/api/admin/release" -Method Post `
    -Headers @{ Authorization = "Bearer $token" } -Form $form

Write-Host "Released v$($resp.version)  sha256=$($resp.sha256)  size=$($resp.size) bytes"
```

- [ ] **Step 2: Smoke check — syntax only**

```powershell
pwsh -NoProfile -Command "Get-Command -Syntax -Name .\tool\release.ps1"
```

Expected: prints the parameter signature without error. (Don't actually invoke against a real backend from this subagent — that requires credentials and the live server.)

- [ ] **Step 3: Commit**

```bash
git add tool/release.ps1
git commit -m "feat(tool): add release.ps1 helper for admin upload"
```

---

## Task 14: End-to-end manual verification (user-driven)

This is a verification task — no code changes.

- [ ] **Step 1: Deploy backend changes to the server**

```
ssh server
cd /path/to/a4071-tool
git pull
docker compose up -d --build backend
```

- [ ] **Step 2: From the dev machine, build A4071-Tool.exe**

```cmd
cd D:\MMO\a4071-tool\tool
build.bat
```

- [ ] **Step 3: Push it via release.ps1**

```powershell
cd D:\MMO\a4071-tool
.\tool\release.ps1 `
    -Base http://a4071-tool.j4m.dev:4071 `
    -User admin -Pass <admin-pass> `
    -Version 0.2.0 -Notes "Smoke test" `
    -File tool\dist\A4071-Tool.exe
```

Expected: `Released v0.2.0  sha256=...  size=... bytes`

- [ ] **Step 4: Verify `/api/version` from a client with an API key**

```powershell
curl.exe -H "X-API-Key: sk_..." http://a4071-tool.j4m.dev:4071/api/version
```

Expected: `{"latest":"0.2.0","notes":"Smoke test","sha256":"...","size":...}`

- [ ] **Step 5: Run the previously-built v0.1.0 sandbox exe**

The update dialog should appear and the existing in-app flow (download
+ swap + relaunch) should land v0.2.0 on the user's disk. Same as the
original Task 10 from `2026-05-21-app-update.md`.

- [ ] **Step 6: Re-upload to validate idempotence**

Run `release.ps1` again with `-Version 0.3.0`. The exe + sha256 +
manifest should atomically swap to the new release. Sandbox app should
pick up v0.3.0 on next launch.
