package main

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"
	"github.com/golang-jwt/jwt/v5"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type App struct {
	db        *pgxpool.Pool
	adminUser string
	adminPass string
	jwtSecret []byte
	update    updateConfig
}

type APIKey struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	Key       string    `json:"key,omitempty"`
	CreatedAt time.Time `json:"created_at"`
	LastUsed  *time.Time `json:"last_used,omitempty"`
}

func main() {
	ctx := context.Background()

	dsn := getenv("DATABASE_URL", "postgres://app:app_secret@localhost:5432/apikeys?sslmode=disable")
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		log.Fatalf("db connect: %v", err)
	}
	defer pool.Close()

	if err := waitForDB(ctx, pool); err != nil {
		log.Fatalf("db not ready: %v", err)
	}
	if err := migrate(ctx, pool); err != nil {
		log.Fatalf("migrate: %v", err)
	}

	app := &App{
		db:        pool,
		adminUser: getenv("ADMIN_USERNAME", "admin"),
		adminPass: getenv("ADMIN_PASSWORD", "adminpass"),
		jwtSecret: []byte(getenv("JWT_SECRET", "dev_secret")),
		update:    loadUpdateConfig(),
	}

	r := chi.NewRouter()
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   []string{"*"},
		AllowedMethods:   []string{"GET", "POST", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Authorization", "Content-Type", "X-API-Key"},
		AllowCredentials: false,
	}))

	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]string{"status": "ok"})
	})

	r.Post("/api/admin/login", app.handleLogin)

	r.Route("/api/admin/keys", func(r chi.Router) {
		r.Use(app.adminAuth)
		r.Get("/", app.listKeys)
		r.Post("/", app.createKey)
		r.Post("/generate", app.generateKey)
		r.Post("/{id}/rotate", app.rotateKey)
		r.Delete("/{id}", app.deleteKey)
	})

	r.With(app.adminAuth).Get("/api/admin/release", app.handleGetRelease)
	r.With(app.adminAuth).Post("/api/admin/release", app.handleUploadRelease)

	r.With(app.apiKeyAuth).Get("/api/verify", app.verifyKey)
	r.With(app.apiKeyAuth).Get("/api/me", app.verifyKey)
	r.With(app.apiKeyAuth).Get("/api/version", app.handleVersion)
	r.With(app.apiKeyAuth).Get("/api/download", app.handleDownload)

	port := getenv("PORT", "4071")
	log.Printf("listening on :%s", port)
	if err := http.ListenAndServe(":"+port, r); err != nil {
		log.Fatal(err)
	}
}

func waitForDB(ctx context.Context, pool *pgxpool.Pool) error {
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		if err := pool.Ping(ctx); err == nil {
			return nil
		}
		time.Sleep(time.Second)
	}
	return errors.New("timed out waiting for db")
}

func migrate(ctx context.Context, pool *pgxpool.Pool) error {
	_, err := pool.Exec(ctx, `
		CREATE TABLE IF NOT EXISTS api_keys (
			id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
			name TEXT NOT NULL,
			key_hash TEXT NOT NULL UNIQUE,
			created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
			last_used TIMESTAMPTZ
		);
	`)
	return err
}

type loginReq struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type loginResp struct {
	Token     string `json:"token"`
	ExpiresAt int64  `json:"expires_at"`
}

func (a *App) handleLogin(w http.ResponseWriter, r *http.Request) {
	var req loginReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid body"})
		return
	}
	if subtle.ConstantTimeCompare([]byte(req.Username), []byte(a.adminUser)) != 1 ||
		subtle.ConstantTimeCompare([]byte(req.Password), []byte(a.adminPass)) != 1 {
		writeJSON(w, 401, map[string]string{"error": "invalid credentials"})
		return
	}
	exp := time.Now().Add(12 * time.Hour)
	tok := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{
		"sub":  a.adminUser,
		"role": "admin",
		"exp":  exp.Unix(),
		"iat":  time.Now().Unix(),
	})
	s, err := tok.SignedString(a.jwtSecret)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "token sign"})
		return
	}
	writeJSON(w, 200, loginResp{Token: s, ExpiresAt: exp.Unix()})
}

func (a *App) adminAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := r.Header.Get("Authorization")
		if !strings.HasPrefix(h, "Bearer ") {
			writeJSON(w, 401, map[string]string{"error": "missing token"})
			return
		}
		raw := strings.TrimPrefix(h, "Bearer ")
		tok, err := jwt.Parse(raw, func(t *jwt.Token) (interface{}, error) {
			if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
				return nil, errors.New("bad alg")
			}
			return a.jwtSecret, nil
		})
		if err != nil || !tok.Valid {
			writeJSON(w, 401, map[string]string{"error": "invalid token"})
			return
		}
		claims, ok := tok.Claims.(jwt.MapClaims)
		if !ok || claims["role"] != "admin" {
			writeJSON(w, 403, map[string]string{"error": "forbidden"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (a *App) apiKeyAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("X-API-Key")
		if key == "" {
			ah := r.Header.Get("Authorization")
			if strings.HasPrefix(ah, "Bearer ") {
				key = strings.TrimPrefix(ah, "Bearer ")
			}
		}
		if key == "" {
			writeJSON(w, 401, map[string]string{"error": "missing api key"})
			return
		}
		hash := hashKey(key)
		var id, name string
		err := a.db.QueryRow(r.Context(),
			`UPDATE api_keys SET last_used = now() WHERE key_hash = $1 RETURNING id::text, name`,
			hash).Scan(&id, &name)
		if err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				writeJSON(w, 401, map[string]string{"error": "invalid api key"})
				return
			}
			writeJSON(w, 500, map[string]string{"error": "db error"})
			return
		}
		ctx := context.WithValue(r.Context(), ctxKey("key_id"), id)
		ctx = context.WithValue(ctx, ctxKey("key_name"), name)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

type ctxKey string

func (a *App) listKeys(w http.ResponseWriter, r *http.Request) {
	rows, err := a.db.Query(r.Context(),
		`SELECT id::text, name, created_at, last_used FROM api_keys ORDER BY created_at DESC`)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	defer rows.Close()
	out := []APIKey{}
	for rows.Next() {
		var k APIKey
		if err := rows.Scan(&k.ID, &k.Name, &k.CreatedAt, &k.LastUsed); err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		out = append(out, k)
	}
	writeJSON(w, 200, out)
}

type createReq struct {
	Name string `json:"name"`
}

func (a *App) createKey(w http.ResponseWriter, r *http.Request) {
	var req createReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid body"})
		return
	}
	name := strings.TrimSpace(req.Name)
	if name == "" {
		name = "unnamed"
	}
	plain, err := randomAPIKey()
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	hash := hashKey(plain)
	var id string
	var createdAt time.Time
	err = a.db.QueryRow(r.Context(),
		`INSERT INTO api_keys (name, key_hash) VALUES ($1, $2) RETURNING id::text, created_at`,
		name, hash).Scan(&id, &createdAt)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 201, APIKey{
		ID:        id,
		Name:      name,
		Key:       plain,
		CreatedAt: createdAt,
	})
}

func (a *App) generateKey(w http.ResponseWriter, r *http.Request) {
	var req createReq
	_ = json.NewDecoder(r.Body).Decode(&req)
	name := strings.TrimSpace(req.Name)
	if name == "" {
		name = "key_" + time.Now().UTC().Format("20060102_150405")
	}
	plain, err := randomAPIKey()
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	hash := hashKey(plain)
	var id string
	var createdAt time.Time
	err = a.db.QueryRow(r.Context(),
		`INSERT INTO api_keys (name, key_hash) VALUES ($1, $2) RETURNING id::text, created_at`,
		name, hash).Scan(&id, &createdAt)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 201, APIKey{
		ID:        id,
		Name:      name,
		Key:       plain,
		CreatedAt: createdAt,
	})
}

func (a *App) rotateKey(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	plain, err := randomAPIKey()
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	hash := hashKey(plain)
	var name string
	var createdAt time.Time
	err = a.db.QueryRow(r.Context(),
		`UPDATE api_keys SET key_hash = $1, last_used = NULL WHERE id = $2
		 RETURNING name, created_at`,
		hash, id).Scan(&name, &createdAt)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			writeJSON(w, 404, map[string]string{"error": "not found"})
			return
		}
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, APIKey{
		ID:        id,
		Name:      name,
		Key:       plain,
		CreatedAt: createdAt,
	})
}

func (a *App) deleteKey(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	tag, err := a.db.Exec(r.Context(), `DELETE FROM api_keys WHERE id = $1`, id)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	if tag.RowsAffected() == 0 {
		writeJSON(w, 404, map[string]string{"error": "not found"})
		return
	}
	writeJSON(w, 200, map[string]string{"status": "deleted"})
}

func (a *App) verifyKey(w http.ResponseWriter, r *http.Request) {
	id, _ := r.Context().Value(ctxKey("key_id")).(string)
	name, _ := r.Context().Value(ctxKey("key_name")).(string)
	writeJSON(w, 200, map[string]string{
		"status": "ok",
		"id":     id,
		"name":   name,
	})
}

func randomAPIKey() (string, error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return "sk_" + hex.EncodeToString(buf), nil
}

func hashKey(plain string) string {
	h := sha256Hex(plain)
	return h
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
