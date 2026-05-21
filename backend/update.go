package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
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
	releaseManifestName = "release.json"
	maxUploadBytes      = 200 * 1024 * 1024
)

type updateConfig struct {
	dir string
	mu  sync.RWMutex
}

type releaseManifest struct {
	Version    string    `json:"version"`
	Notes      string    `json:"notes"`
	Sha256     string    `json:"sha256"`
	Size       int64     `json:"size"`
	UploadedAt time.Time `json:"uploaded_at"`
}

func loadUpdateConfig() updateConfig {
	return updateConfig{
		dir: getenv("UPDATE_DIR", "/srv/updates"),
	}
}

func (c *updateConfig) manifestPath() string { return filepath.Join(c.dir, releaseManifestName) }
func (c *updateConfig) exePath() string      { return filepath.Join(c.dir, releaseExeName) }

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
	a.update.mu.RLock()
	defer a.update.mu.RUnlock()

	m, err := a.update.readManifest()
	if err != nil || m.Version == "" {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	if _, err := os.Stat(a.update.exePath()); err != nil {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	writeJSON(w, 200, map[string]any{
		"latest": m.Version,
		"notes":  m.Notes,
		"sha256": m.Sha256,
		"size":   m.Size,
	})
}

func (a *App) handleDownload(w http.ResponseWriter, r *http.Request) {
	a.update.mu.RLock()
	m, err := a.update.readManifest()
	if err != nil || m.Version == "" {
		a.update.mu.RUnlock()
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	f, err := os.Open(a.update.exePath())
	if err != nil {
		a.update.mu.RUnlock()
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		a.update.mu.RUnlock()
		writeJSON(w, 500, map[string]string{"error": "stat failed"})
		return
	}
	a.update.mu.RUnlock()

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", `attachment; filename="A4071-Tool.exe"`)
	http.ServeContent(w, r, releaseExeName, st.ModTime(), f)
}

func (a *App) handleUploadRelease(w http.ResponseWriter, r *http.Request) {
	a.update.mu.Lock()
	defer a.update.mu.Unlock()

	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			writeJSON(w, 413, map[string]string{"error": "file too large"})
			return
		}
		writeJSON(w, 400, map[string]string{"error": "invalid multipart: " + err.Error()})
		return
	}
	defer func() {
		if r.MultipartForm != nil {
			_ = r.MultipartForm.RemoveAll()
		}
	}()

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
	written, err := io.Copy(io.MultiWriter(out, h), file)
	if err != nil {
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

	manifest := releaseManifest{
		Version:    version,
		Notes:      notes,
		Sha256:     sum,
		Size:       written,
		UploadedAt: time.Now().UTC(),
	}
	manifestData, err := json.Marshal(manifest)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "marshal manifest failed"})
		return
	}

	manifestPart := a.update.manifestPath() + ".part"
	if err := os.WriteFile(manifestPart, manifestData, 0o644); err != nil {
		writeJSON(w, 500, map[string]string{"error": "write manifest part failed: " + err.Error()})
		return
	}
	if err := os.Rename(manifestPart, a.update.manifestPath()); err != nil {
		os.Remove(manifestPart)
		writeJSON(w, 500, map[string]string{"error": "rename manifest failed: " + err.Error()})
		return
	}

	writeJSON(w, 200, map[string]any{
		"status":  "ok",
		"version": version,
		"sha256":  sum,
		"size":    written,
	})
}
