# Release Drop Directory

The backend writes/reads release artifacts here. **Do not drop files
manually** — use the admin upload endpoint instead.

Files this directory will hold after the first release:

- `release.json` — `{version, notes, sha256, size, uploaded_at}` manifest
  written by the backend.
- `A4071-Tool.exe` — the binary served by `/api/download`.

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
both files atomically. No SSH, no restart needed.
