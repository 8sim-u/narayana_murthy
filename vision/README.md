# WAMSS Vision

Edge-side person detection for WAMSS (Person A). Runs YOLOv8 + ByteTrack on
one or more camera streams and POSTs consented person events to `/event` or
aggregate-only privacy events to `/event/aggregate`.

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
- `WAMSS_BACKEND_URL` - person-event endpoint. Default: `http://localhost:8000/event`
- `WAMSS_AGGREGATE_BACKEND_URL` - optional privacy aggregate endpoint. Default:
  derived by replacing the trailing `/event` with `/event/aggregate`.
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

`WAMSS_PRIVACY_MODE=1` enables aggregate-only occupancy counting. Frames are
processed in memory; the edge node sends only camera ID, occupied/vacant state,
and an aggregate person count to `/event/aggregate`. It does not generate pose,
track IDs, employee names, coordinates, bounding boxes, or snapshots.

The backend accepts that payload only through a strict schema that rejects extra
identity-like fields, and stores it in a separate aggregate table with no
person-level columns. The table is covered by `WAMSS_RETENTION_DAYS` in
`backend/scripts/purge_old_logs.py`. The privacy endpoint is not a person-event
fallback: `/event` rejects privacy-mode requests and anonymous or unconsented
person events before movement, geofence, or alert processing. Each camera must
remain in aggregate mode for the aggregate endpoint to accept it; identity mode
requires explicit camera configuration plus active employee consent.

Occupancy transitions remain pending until the aggregate POST succeeds. Network
or server failures cause the edge node to retry the transition instead of
advancing its local state. MediaPipe is optional and counts face detections
locally; without it, the worker uses YOLO's person count. Neither path sends
per-person details off the camera node. Privacy mode never opens preview windows
and never writes frames to disk.

Configuration:

- `WAMSS_PRIVACY_MODE` - `1` enables privacy mode. Default: `0`.
- `WAMSS_OCCUPANCY_DEBOUNCE_S` - sustained presence/absence seconds before an
  occupied/vacant transition. Default: `5.0`.
- `WAMSS_AGGREGATE_BACKEND_URL` - optional explicit aggregate endpoint URL;
  otherwise derived from `WAMSS_BACKEND_URL`.

**Not yet verified:** the debounce timing and detector count accuracy have not
been checked against live hardware. No hardware or live-camera checks are part
of this patch.

## Face recognition (per-camera consent required)

Identity is denied by default. A camera can identify anyone only when the
backend explicitly configures it as `identity` and an employee has active,
separate consent for that exact camera. All missing, malformed, or ambiguous
configuration fails closed. The backend returns only that camera's consented
gallery; if the consent check cannot be refreshed, vision emits no name.

Before enabling a camera, configure `WAMSS_CAMERA_PRIVACY_MODES` on the
backend, for example `{"CAM_01":"identity"}`. Add a unique display-name to
employee-ID mapping in `backend/app/attendance_identity_map.json`, and an
affirmative record keyed by employee ID in
`backend/app/attendance_camera_consents.json`:

```json
{"employee-001":{"consented":true,"recorded_at":"2026-11-14T10:00:00Z","camera_ids":["CAM_01"]}}
```

Set `consented` to `false` to withdraw consent; future recognition, enrollment,
and named event storage stop. The application does not rewrite or delete old
event history. `WAMSS_CONSENT_POLL_SECONDS` controls the vision node's refresh
interval (default 1 second); a failed refresh disables identification.

Enroll only through the backend, which verifies consent before storing or
encoding the photo:

```bash
python vision/enroll_face.py --photo <photo.jpg> --name "Full Name" --camera-id CAM_01
```

The CLI forwards the image to `/faces/enroll`; it never creates a local
unrestricted gallery. The backend is the source of truth for camera-specific
embeddings. A detection without a current, unambiguous camera-specific consent match is
discarded at the edge and cannot create a person-level movement event or alert;
the backend independently rejects it if a client nevertheless submits one.
Cameras not explicitly configured for identity mode accept no person events.

How it works (`vision/identity_cache.py`, wired into `run_camera()`):

- Only identities in the current camera-specific consent gallery are
  considered by face matching. Other detections are discarded at the edge.
  One event is posted per consented person per interval in identity mode; the
  backend independently rechecks active consent before persistence or alerts.
- Identification runs on first sight of a track, then re-verifies
  periodically (every `WAMSS_IDENTITY_RECHECK_FRAMES` frames or
  `WAMSS_IDENTITY_RECHECK_SECONDS` seconds per track, whichever comes
  first; defaults 30 frames / 5 s), never every frame.
- Names are smoothed by majority voting over the last
  `WAMSS_IDENTITY_VOTE_WINDOW` votes (default 7). A name is assigned only
  when it holds a strict majority of the votes, has at least
  `WAMSS_IDENTITY_MIN_VOTES` agreeing votes (default 2), and its mean
  match confidence clears `WAMSS_IDENTITY_MIN_CONFIDENCE` (default 0.5).
  Once named, a track keeps its name until another name wins a strict
  majority, so one bad frame, a tie, or a run of unknown votes cannot
  flip it. Unknown tracks stay `"unknown"` with a `null` confidence.
- The long-term memory is the enrolled gallery, not the cache: a person
  who leaves and returns under a new track id is re-identified from the
  gallery on first sight of the new track.
- The camera loop does not save first-sighting snapshots; it therefore does
  not create an unknown-person image queue from unconsented detections.
- Face recognition never runs in privacy mode (`WAMSS_PRIVACY_MODE=1`).

Configuration:

- `WAMSS_FACE_ID` - `0` disables face recognition. Default: `1`, but it has no
  effect without the backend's active camera-specific consent allow-list.
- `WAMSS_CONSENT_POLL_SECONDS` - refresh rate for the camera-specific consent
  allow-list. Default: `1.0`; errors clear the allow-list.
- `WAMSS_SNAPSHOT_DIR` - retained for an explicit legacy snapshot helper.
  The active camera loop does not save first-sighting images, regardless of
  this setting.
- `WAMSS_ROOM_CALIB` - optional room calibration JSON (see
  `vision/room_calib.py`). Default: empty (normalized frame coordinates).
- `WAMSS_FACE_SIM_THRESHOLD` - insightface cosine-similarity match
  threshold. Default: `0.5`.
- `WAMSS_LBPH_THRESHOLD` - LBPH distance match threshold. Default: `70`.
- `WAMSS_IDENTITY_RECHECK_FRAMES` / `WAMSS_IDENTITY_RECHECK_SECONDS` -
  re-verification cadence per track. Defaults: `30` / `5.0`.
- `WAMSS_IDENTITY_VOTE_WINDOW` - votes kept per track. Default: `7`.
- `WAMSS_IDENTITY_MIN_VOTES` - agreeing votes needed to name a track.
  Default: `2`.
- `WAMSS_IDENTITY_MIN_CONFIDENCE` - mean confidence a name needs.
  Default: `0.5`.
- `WAMSS_IDENTITY_STALE_SECONDS` - drop a track's state after this long
  unseen. Default: `600`.

**Not yet verified:** identity has been unit-tested with synthetic frames
and a stubbed face backend only. Before production use, validate against
a real camera: naming latency after first sight, stability of names
across track-id changes, and the false-match rate of your enrolled
gallery at the default thresholds.
