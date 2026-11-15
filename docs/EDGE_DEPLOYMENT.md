# Running an Edge Detector Node

WAMSS vision runs at the edge: each computer with a camera runs its own
detector process, and the detector POSTs JSON events to one central backend.
The central backend and the dashboard do not touch cameras directly.

One node = one camera host. A node is just a Python process; it runs on any
machine with a camera (a laptop with a webcam, a desktop with a USB camera,
or a machine pointed at an IP/RTSP stream).

## Prerequisites

- Python 3.10 or newer (`python3 --version`).
- This repo checked out on the node (only `vision/` is used, but cloning the
  whole repo is simplest).
- A camera: built-in webcam, USB webcam, RTSP IP camera, or an MJPEG stream
  (see `docs/PHONE_CAMERA.md` for using an Android phone).

Install the vision dependencies (heavy: torch, ultralytics, OpenCV; several
GB):

```bash
pip install -r vision/requirements.txt
```

The YOLO weights (`yolov8n.pt`) download automatically on first run. Keep
these deps off the backend server; they are edge-only.

## Configuration

The detector is configured entirely with environment variables. You can set
them in the shell or copy `vision/.env.example` to `vision/.env` and edit it
(shell values override `.env`). Never commit a real `.env` with LAN
addresses.

| Variable | Default | Purpose |
|---|---|---|
| `CAMERA_STREAM_URL` | (none) | Camera source. A numeric string like `"1"` means a local webcam device index; anything else is treated as a URL (RTSP or MJPEG). See "Camera source options" below. |
| `CAMERA_ID` | `CAM_01` | Identity of this node. Every event posted to the backend carries this value. Must be unique per node. |
| `WAMSS_BACKEND_URL` | `http://127.0.0.1:8000/event` | Full URL of the backend `/event` endpoint that receives the node's events. Point it at the central backend machine (e.g. `http://192.168.1.20:8000/event`). |
| `WAMSS_API_KEY` | (empty) | Must match the backend key for identity-mode face-gallery access; if the backend key is unset or missing here, identity matching fails closed. The detector sends it as `X-API-Key`. Use a placeholder like `<your-key>` and never commit the real key. |

Example for a node on the same LAN as the backend at `192.168.1.20`:

```bash
export CAMERA_STREAM_URL="1"            # laptop's built-in or first webcam
export CAMERA_ID="CAM_01"
export WAMSS_BACKEND_URL="http://192.168.1.20:8000/event"
```

## Camera source options

`CAMERA_STREAM_URL` accepts three kinds of source. Pick one:

1. **Webcam index** (numeric string). `"0"` is usually the built-in camera,
   `"1"` the first USB camera. Try each index if the wrong camera opens.

   ```bash
   export CAMERA_STREAM_URL="1"
   ```

2. **RTSP URL** (IP cameras, NVRs). Any `rtsp://` URL OpenCV can open:

   ```bash
   export CAMERA_STREAM_URL="rtsp://192.168.1.50:554/live"
   ```

3. **MJPEG URL** (phone cameras, some IP cameras). An `http://` URL that
   serves a continuous JPEG stream:

   ```bash
   export CAMERA_STREAM_URL="http://192.168.1.61:8080/video"
   ```

Note: the checkout also supports a multi-stream style (`WAMSS_CAMERAS` as a
JSON map of camera id to stream URL, documented in `vision/.env.example`),
which runs several streams from one host. For the simple one-node-per-camera
setup described here, `CAMERA_STREAM_URL` plus `CAMERA_ID` is enough.

## Running the detector

```bash
python vision/multicamera.py
```

On start the node prints the camera id, the stream it connected to, and
whether it is headless. Headless mode (no preview windows, log summaries
only) is automatic on servers with no `DISPLAY`; on a desktop you get a
preview window per camera. Override with `WAMSS_HEADLESS=1` (headless) or
`WAMSS_HEADLESS=0` (windows).

Keep it running: on a permanent edge node, run it under a process manager
(`systemd` on Linux, a Scheduled Task on Windows) so it restarts on reboot.

## Verifying

1. Watch the node log: it should print `[CAM_01] Camera connected` and then
   per-frame detection summaries when people walk into view.
2. Confirm the node reports successful event delivery in its logs and that
   the backend receives the configured camera ID. Broad person-level map and
   history views remain disabled; the Issue 19 manager token authorizes only
   roster-scoped wellness check-in prompts, not these broader records.
3. Do not use an unscoped dashboard map or person-history endpoint as a health
   check; use the backend health endpoint and node delivery logs instead.

If events arrive but positions are `0..1` decimals, the camera is
uncalibrated. See `docs/ROOM_MAP_SETUP.md` to calibrate it to room meters.

## Running multiple nodes

Every node is an independent process. Rules:

- Each node gets a **unique `CAMERA_ID`** (`CAM_01`, `CAM_02`, `PHONE_01`,
  ...). Two nodes with the same id will overwrite each other's live state on
  the backend.
- Each node points `WAMSS_BACKEND_URL` at the same central backend.
- Nodes can be on different machines, different LANs (as long as each can
  reach the backend), or different subnets via Tailscale.

Example: three nodes (office laptop webcam, hallway USB camera, phone camera)
all reporting to one backend:

```bash
# Node 1 (laptop)
CAMERA_STREAM_URL="0" CAMERA_ID="CAM_01" \
  WAMSS_BACKEND_URL="http://192.168.1.20:8000/event" python vision/multicamera.py

# Node 2 (hallway desktop)
CAMERA_STREAM_URL="1" CAMERA_ID="CAM_02" \
  WAMSS_BACKEND_URL="http://192.168.1.20:8000/event" python vision/multicamera.py

# Node 3 (phone as camera, see docs/PHONE_CAMERA.md)
CAMERA_STREAM_URL="http://192.168.1.61:8080/video" CAMERA_ID="PHONE_01" \
  WAMSS_BACKEND_URL="http://192.168.1.20:8000/event" python vision/multicamera.py
```

## Central server requirements

- The backend must be **reachable over the LAN** from every node. When you
  test with `127.0.0.1` URLs, remember that means "this machine only"; nodes
  on other machines need the backend's LAN address (e.g.
  `http://192.168.1.20:8000/event`).
- **Firewall:** allow inbound TCP on port `8000` on the backend machine.
  On Windows, that is an inbound rule for the backend process or port 8000;
  on Linux, `ufw allow 8000/tcp` or the equivalent.
- Keep the dashboard port (`8080`) reachable for browsers; edge nodes do not
  need it.

## Identity: remembering who a person is

YOLO alone only says "a person is here", and ByteTrack ids (`Person 1`,
`Person 2`, ...) live only while a person stays in view. Face identification
is separately gated by active, per-camera employee consent. Unlisted or
malformed camera privacy-mode configuration is aggregate-only; a missing,
ambiguous, or withdrawn consent never authorizes a name.

### Enrolling people

Before enabling identity for a camera, configure the backend environment,
for example `WAMSS_CAMERA_PRIVACY_MODES='{"CAM_01":"identity"}'`. Add the
display-name-to-employee-ID mapping to
`backend/app/attendance_identity_map.json` and an affirmative, camera-scoped
record keyed by employee ID to `backend/app/attendance_camera_consents.json`:

```json
{"Employee Display Name":"employee-001"}
```

```json
{"employee-001":{"consented":true,"recorded_at":"2026-11-14T10:00:00Z","camera_ids":["CAM_01"]}}
```

Enroll an authorized photo only through the backend (which checks the same
camera scope before storage or encoding):

```bash
python vision/enroll_face.py --photo <photo.jpg> --name "Full Name" --camera-id CAM_01
```

The CLI does not write an unrestricted local gallery. `GET /faces/gallery`
requires a configured, valid deployment API key plus `camera_id`, identity mode,
and active consent; it returns only that camera's consented identities with
usable embeddings. If API-key authentication is disabled or the key is missing,
the endpoint fails closed. The running node refreshes this allow-list every
`WAMSS_CONSENT_POLL_SECONDS` (default 1 second); a failed refresh empties the
allow-list and disables identification. The camera loop also avoids writing
first-sighting snapshots of unidentified people.

### How memory works

- A face is matched only against the current camera-scoped consent gallery.
  A name is emitted only if it remains authorized at event-send time; missing
  consent or backend connectivity produces an anonymous event.
- Identification runs on first sight of a track, then re-verifies
  periodically (every `WAMSS_IDENTITY_RECHECK_FRAMES` frames or
  `WAMSS_IDENTITY_RECHECK_SECONDS` seconds, whichever comes first;
  defaults 30 frames / 5 s), never every frame.
- Names are smoothed by majority voting over the last
  `WAMSS_IDENTITY_VOTE_WINDOW` votes (default 7). A name is assigned only
  when it holds a strict majority, has at least `WAMSS_IDENTITY_MIN_VOTES`
  agreeing votes (default 2), and its mean match confidence clears
  `WAMSS_IDENTITY_MIN_CONFIDENCE` (default 0.5). Once named, a track keeps
  its name until another name wins a strict majority, so one bad frame, a
  tie, or a run of unknown votes cannot flip it. Unknown tracks stay
  `"unknown"` with a null confidence.
- The long-term memory is the backend gallery, filtered independently per
  camera; one camera's consent does not authorize another camera.
- When consent changes or a refresh fails, the vision node clears cached
  per-track names. Withdrawal stops future matching and name storage; it
  does not rewrite or delete historical events.

`WAMSS_GALLERY_BASE_URL` or `WAMSS_BACKEND_URL` must point to the backend
for identity matching. `WAMSS_PRIVACY_MODE=1` remains presence-only and
never queries the gallery or identifies faces. An aggregate-only camera mode
also rejects person-level events at backend ingestion.

### Thresholds

- `WAMSS_FACE_SIM_THRESHOLD` (insightface cosine similarity, default
  `0.5`): below this, a face stays `"unknown"`. Raise it to cut false
  matches; lower it to recognise harder angles, at the cost of more
  false matches.
- `WAMSS_LBPH_THRESHOLD` (LBPH distance, default `70`, smaller is a
  better match): the fallback when insightface is not installed.
- `WAMSS_FACE_ID=0` disables recognition entirely (all names
  `"unknown"`).

**Not yet verified:** identity has been unit-tested with synthetic frames
and a stubbed face backend only. Before production use, validate against a
real camera: naming latency after first sight, stability of names across
track-id changes, and the false-match rate of your enrolled gallery at the
default thresholds.

## Troubleshooting

- **Camera busy / "Cannot open camera stream":** another app (Zoom, Teams,
  browser) has the webcam. Close it and retry. For a wrong camera, try the
  next index (`"0"`, `"1"`, `"2"`).
- **0 FPS / no frames ("No frame received"):** the stream URL is wrong or
  unreachable. For MJPEG, open the URL in a browser first: you should see a
  live image. For RTSP, check the camera's own app shows video and the
  credentials/port in the URL are right.
- **Events not arriving at the backend:** from the node, `curl
  http://<backend-host>:8000/health`. If that fails, it is a network/firewall
  problem, not a detector problem. If health works but `/positions` never
  shows the node, check the node log for "Backend request failed" lines: a
  401 means `WAMSS_API_KEY` is missing or wrong (see `docs/AUTH.md`). A `403`
  from broad position, heatmap, identity, geofence-history, unknown-sighting, or
  snapshot endpoints is intentional. The Issue 19 manager token is limited to
  wellness check-in prompts and does not authorize these endpoints.
- **Duplicate nodes on the dashboard:** two nodes share a `CAMERA_ID`. Give
  each node a unique id.
- **High CPU on the node:** YOLO runs on CPU by default. Reduce the input
  size or frame rate, or run the node on a machine with a GPU (torch with
  CUDA picks it up automatically).

Note: the event payload carries `camera_id`, `track_id` (stable per person
as `Person 1`, `Person 2`, ... via ByteTrack), `objects`, `confidence`,
`zone`, `person_name` (when face recognition is enrolled), `position_x` /
`position_y` (0..1 normalized, or meters when calibrated), `snapshot_path`,
and `dwell_seconds`. The deployment key is not employee or manager identity. Unscoped person-level
position, geofence-history, heatmap, face-list, identity, unknown-sighting, and
snapshot read endpoints are disabled. Personal event history must use the
employee-token-scoped API; `GET /occupancy` returns counts only. Consent-filtered
`GET /faces/gallery` and face enrollment/deletion require an enabled deployment
API key, while gallery entries also require active camera-specific employee
consent.
