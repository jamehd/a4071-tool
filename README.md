# A4071 API Key Tool

Minimal API-key auth backend in Go + admin web UI to create and delete keys.

## Stack
- Backend: Go 1.22 (chi router, pgx/v5, JWT for admin session)
- Database: PostgreSQL 16
- Frontend: static HTML/CSS/JS served by nginx (with API proxy)

## Run

```bash
docker compose up --build
```

- Admin UI: http://localhost:4072
- Backend API: http://localhost:4071
- Postgres: localhost:4073 (mapped from container :5432)
- Admin login: `admin` / `adminpass` (override via env in `docker-compose.yml`)

## API

### Admin (Bearer JWT after login)
- `POST /api/admin/login` body `{username, password}` -> `{token, expires_at}`
- `GET  /api/admin/keys/` -> list keys (without secret)
- `POST /api/admin/keys/` body `{name}` -> creates a key, returns the plaintext `key` ONCE
- `DELETE /api/admin/keys/{id}` -> deletes a key

### Consumer (use a generated API key)
- `GET /api/verify` header `X-API-Key: sk_xxx` (or `Authorization: Bearer sk_xxx`)
  -> `{status: "ok", id, name}` or 401 if invalid

Keys are stored as SHA-256 hashes; the plaintext is shown only at creation time.

## Configuration

Environment variables (see `docker-compose.yml`):
- `DATABASE_URL`
- `ADMIN_USERNAME` (default `admin`)
- `ADMIN_PASSWORD` (default `adminpass`)
- `JWT_SECRET` - change this in any non-dev setup
- `PORT` (default `8080`)
