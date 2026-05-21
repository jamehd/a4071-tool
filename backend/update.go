package main

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

type updateConfig struct {
	dir     string
	version string
	notes   string
}

func loadUpdateConfig() updateConfig {
	return updateConfig{
		dir:     getenv("UPDATE_DIR", "/srv/updates"),
		version: strings.TrimSpace(os.Getenv("UPDATE_VERSION")),
		notes:   os.Getenv("UPDATE_NOTES"),
	}
}

func (a *App) handleVersion(w http.ResponseWriter, r *http.Request) {
	if a.update.version == "" {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	exePath := filepath.Join(a.update.dir, "A4071-Tool.exe")
	shaPath := filepath.Join(a.update.dir, "A4071-Tool.exe.sha256")
	st, err := os.Stat(exePath)
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	shaBytes, err := os.ReadFile(shaPath)
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	writeJSON(w, 200, map[string]any{
		"latest": a.update.version,
		"notes":  a.update.notes,
		"sha256": strings.ToLower(strings.TrimSpace(string(shaBytes))),
		"size":   st.Size(),
	})
}

func (a *App) handleDownload(w http.ResponseWriter, r *http.Request) {
	if a.update.version == "" {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	exePath := filepath.Join(a.update.dir, "A4071-Tool.exe")
	f, err := os.Open(exePath)
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
	http.ServeContent(w, r, "A4071-Tool.exe", st.ModTime(), f)
}
