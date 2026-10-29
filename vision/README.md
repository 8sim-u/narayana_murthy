# WAMSS Vision

Edge-side person detection for WAMSS (Person A). Runs YOLOv8 + ByteTrack on
one or more camera streams and POSTs detection events to the backend
`/event` endpoint.

**Live-camera verification has NOT been done.** This pipeline has been
syntax-checked only. Expect to shake out camera connectivity, model
thresholds, and tracking parameters against a real feed.

## Files

- `multicamera.py` - multi-camera tracker, the main entry point
- `detection.py` - single-webcam detection demo
- `tracking.py` - single-webcam ByteTrack demo
- `.env.example` - documented copy of every supported env var
- `requirements.txt` - edge-node dependencies

## Install

The vision deps are heavy (multi-GB: torch, ultralytics, opencv). They are
deliberately kept out of `backend/requirements.txt` because the backend runs
headless on a server while this runs on a camera machine.

```bash
pip install -r vision/requirements.txt
```

Run:

```bash
python vision/multicamera.py
```

The YOLO weights (`yolov8n.pt` by default) download automatically on first run.

## Configuration (environment variables)

No camera IP or backend address is hardcoded. Copy `.env.example` to `.env`
and set your values (a real `.env` is never committed):

- `WAMSS_CAMERAS` - JSON map of camera id to stream URL, e.g.
  `WAMSS_CAMERAS='{"CAM_01":"http://10.0.0.5:8080/video"}'`
- `WAMSS_CAM_<id>_URL` - numbered fallback, one var per camera, e.g.
  `WAMSS_CAM_01_URL=http://10.0.0.5:8080/video` (key becomes `CAM_<id>`)
- `WAMSS_BACKEND_URL` - event endpoint. Default: `http://localhost:8000/event`
- `WAMSS_MODEL_PATH` - YOLO weights path. Default: `yolov8n.pt`
- `WAMSS_EVENT_INTERVAL` - minimum seconds between event POSTs per camera.
  Default: `1.0`
- `WAMSS_HEADLESS` - `1` skips the preview windows and logs detection
  summaries instead; `0` forces windows. Default: headless when no `DISPLAY`
  is set on Linux, windowed otherwise.

If no cameras are configured, the pipeline falls back to clearly marked
SAMPLE-ONLY placeholder URLs (RFC 5737 documentation addresses) and warns
loudly. It will connect to nothing real until you configure cameras.

## Headless mode

On servers (no `DISPLAY`), preview windows are skipped automatically. You get:

- one line per detection event sent to the backend (camera id, backend
  decision, track id),
- a periodic summary line while people are in view between events,
- graceful Ctrl+C shutdown.

Force windowed mode on a machine with a display by setting
`WAMSS_HEADLESS=0` (or run headless on a desktop with `WAMSS_HEADLESS=1`).
