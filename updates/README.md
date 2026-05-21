# Release Drop Directory

Place release artifacts here:

- `A4071-Tool.exe` — the new binary to serve via `/api/download`.
- `A4071-Tool.exe.sha256` — single-line lowercase hex SHA-256 of the exe.

Then update `UPDATE_VERSION` and `UPDATE_NOTES` in `docker-compose.yml`
and run `docker compose up -d backend`.
