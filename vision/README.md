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

## Privacy mode

`WAMSS_PRIVACY_MODE=1` switches the pipeline to presence-without-identity:
it reports that someone is there, never who. The recipe is MediaPipe
on-device, frames kept strictly in memory, and person-count / coarse
pose / occupancy aggregates only.

Guarantees (enforced in `multicamera.py`, see the disk-write audit and the
privacy-guarantee comment block there):

- **In-memory only.** Frames are processed in memory and never written to
  disk. Every frame-persistence call goes through `save_frame_snapshot()`,
  which is the only place `cv2.imwrite` may be called.
- **Aggregates only.** The backend receives occupancy transitions
  (`occupied` / `vacant`), `person_count`, a coarse `pose`
  (`standing` / `sitting` / `unknown`), and `privacy_mode: true`. There are
  no track ids and no bounding boxes in privacy-mode events.
- **No identity, no gaze, no recognition.** Identity/gaze/recognition model
  paths are explicitly refused by `_assert_privacy_model_allowed()`; the two
  allowed kinds are on-device person pose and face detection used only for
  blurring.
- **Face blurring on stored frames.** The only frames that may be stored are
  anonymized occupancy snapshots (opt-in via `WAMSS_PRIVACY_SNAPSHOT_DIR`).
  Faces are blurred first with MediaPipe face detection. If MediaPipe is not
  installed, the frame is NOT stored and the reason is logged.
- **No preview windows.** Privacy mode never opens `cv2.imshow`, even when a
  display is available, so raw faces are never shown on screen.

Configuration:

- `WAMSS_PRIVACY_MODE` - `1` enables privacy mode. Default: `0` (existing
  YOLO + ByteTrack path, unchanged).
- `WAMSS_OCCUPANCY_DEBOUNCE_S` - seconds of sustained presence
  (`person_count > 0`) before a zone reports `occupied`, and seconds of
  sustained absence before it clears. Default: `5.0`.
- `WAMSS_PRIVACY_SNAPSHOT_DIR` - optional directory for anonymized
  occupancy snapshots (faces blurred before writing). Default: empty, which
  means no snapshots are stored at all.

MediaPipe is an **optional** dependency: `pip install mediapipe` on the edge
node to enable it. Without it, privacy mode falls back to the YOLO
person-count aggregate path (no pose, no track ids) and refuses to store any
snapshot. The non-privacy path never needs MediaPipe.

**Not yet verified:** privacy mode has been unit-tested with synthetic numpy
frames only. Before production use, validate against a real camera: the
5 s debounce against real walk-bys, the standing/sitting heuristic against
real postures, MediaPipe face-detection recall on your camera angles, and
confirm no unblurred frame ever lands in `WAMSS_PRIVACY_SNAPSHOT_DIR`.
