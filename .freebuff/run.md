# Run: HERMUS Gateway + JARVIS Control Room (Preview)

## Reproduce artifacts
- Python venv: `.venv` already exists at the repo root (created via `make setup` / `python -m venv .venv` + `pip install -r requirements*.txt`). No build step; static assets in `gateway/static/` are served directly by FastAPI.
- No env files required. Optional: `HERMUS_SKIP_VALIDATION=1` skips the P0-2 startup config validation (use when the Ollama endpoint is down; the flag is defined in `gateway/gateway.py`).

## Run the server
- Default port is 8642 (free at launch; kill any stale listener first: `netstat -ano | grep ":8642"`).
- Detached launch (PowerShell, separate stdout/stderr files):
  `powershell -NoProfile -Command "(Start-Process -FilePath 'C:\Users\rishi\hermus-agent-free\.venv\Scripts\python.exe' -ArgumentList 'hermus.py','gateway','start','--port','8642' -WorkingDirectory 'C:\Users\rishi\hermus-agent-free' -RedirectStandardOutput '<log>' -RedirectStandardError '<log>.err' -WindowStyle Hidden -PassThru).Id"`
- Wait for `Application startup complete` in the log, then check `http://127.0.0.1:8642/control` returns 200.
- Register the preview with `http://127.0.0.1:8642/control` and the printed pid.
