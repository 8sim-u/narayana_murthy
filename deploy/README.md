# Starting WAMSS on agbeast-1 (Windows)

These launchers are the supported way to start the live WAMSS system on
agbeast-1. They exist because the plain `.cmd` launchers break: the API
key value contains `<` and `>`, which cmd parses as redirection, and the
backend reads its camera privacy-mode config from `os.environ` at call
time, which a WMI-launched process does not inherit.

## Files

- `run_backend.py` - starts the backend on `http://127.0.0.1:8000`.
- `run_pipeline.py` - starts the vision pipeline (YOLO + ByteTrack).
- `README.md` - this note.

## How to start

Copy both `.py` files into `C:\Users\Ayyg\wamss-vision\` (next to
`run-vision.cmd`), then launch each detached via WMI so it survives the
SSH session closing:

```
powershell -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine='C:\Users\Ayyg\wamss-vision\.venv\Scripts\python.exe C:\Users\Ayyg\wamss-vision\run_backend.py'; CurrentDirectory='C:\Users\Ayyg\wamss-vision' }"
powershell -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine='C:\Users\Ayyg\wamss-vision\.venv\Scripts\python.exe C:\Users\Ayyg\wamss-vision\run_pipeline.py'; CurrentDirectory='C:\Users\Ayyg\wamss-vision' }"
```

## What each launcher does

- `run_backend.py` parses `backend/.env` into `os.environ` itself, so
  `WAMSS_CAMERA_PRIVACY_MODES` is present in the process environment.
  The backend must run with
  `WAMSS_CAMERA_PRIVACY_MODES={"*":"identity"}` or the camera-mode guard
  in `backend/app/main.py` blocks person events with 403. It then runs
  uvicorn in-process on `127.0.0.1:8000`.
- `run_pipeline.py` reads the env assignments straight from
  `run-vision.cmd` (line based, no cmd parsing, so the `<` and `>` in
  the API key are safe), fixes the working directory so the relative
  `yolov8n.pt` path resolves, and runs `multicamera.py` in-process.

## Secrets

Neither launcher contains a secret. The API key is read at runtime from
`run-vision.cmd` (pipeline) and from `backend/.env` (backend), and is
never printed or committed. `backend/.env` has the API key commented out,
so API key auth is disabled on the backend exactly as it runs today.

## Notes

- These scripts hardcode the agbeast-1 paths
  (`C:\Users\Ayyg\narayana_murthy-design\...`). On another machine,
  update `VISION_DIR`, `BACKEND_DIR`, and `CMD_FILE` to match.
- The backend and pipeline are long-running. Do not start them with
  `Start-Process` over SSH; the process tree is killed when the session
  closes. Use WMI `Win32_Process Create` as above.
- Verify with `curl http://localhost:8000/health` and
  `curl http://localhost:8000/events?limit=2`. Event timestamps are UTC.
