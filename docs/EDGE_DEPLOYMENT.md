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
| `WAMSS_API_KEY` | (empty) | Only needed when the backend has API key auth enabled (`WAMSS_API_KEY` set on the backend, see `docs/AUTH.md`). The detector sends it as the `X-API-Key` header. Use a placeholder like `<your-key>` and never commit the real key. |

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
2. Check the central backend: open `GET /positions` (e.g.
   `http://192.168.1.20:8000/positions` in a browser or with curl) and confirm
   your `CAMERA_ID` shows up with live positions.
3. Open the dashboard Room Map tab: the node should appear as a camera
   source.

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
`Person 2`, ...) live only while a person stays in view. Remembering *who*
a person is comes from face recognition against an enrolled gallery, and
that memory survives track-id changes, camera changes, and restarts.

### Enrolling people

Two ways, same result:

- **Command line, on the node or any machine with the repo:**
  `python vision/enroll_face.py --photo <photo.jpg> --name "Full Name"`.
  A clear front-facing photo works best. This writes an embedding sidecar
  to `vision/faces/` (never commit those files; only `.gitkeep` and
  `README.md` belong there). A node that is already running does **not**
  pick up a CLI enrollment on its own: restart the node after enrolling
  this way.
- **Dashboard:** the Unknown people panel lists recent unknown sightings
  with their first-sighting snapshot. Type a name next to one and it calls
  `POST /faces/enroll {"name", "snapshot_path"}`, which stores the face in
  the backend gallery and registry. The Known people panel lists everyone
  enrolled, with last seen, and can remove someone (`DELETE /faces/{name}`).

The backend is the source of truth for the gallery (embeddings live in its
faces table). `GET /faces` lists enrolled people, `GET /identities` is the
registry with last seen and sighting counts, `GET /unknowns` is the review
queue, and `GET /faces/gallery` serves name plus embedding pairs for node
sync.

### How memory works

- Every tracked person is identified, not just the first box in frame. One
  event is posted per person per interval, each with its own `person_name`,
  `person_name_confidence`, track id, position, and dwell time.
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
- The long-term memory is the enrolled gallery, not the per-track cache: a
  person who leaves and returns under a new track id is re-identified from
  the gallery on first sight of the new track, on any camera.
- Every new track gets a first-sighting snapshot (JPEG crop) whether it is
  recognised or not, so unknown people land in the review queue.

### Gallery sync (no restart for dashboard enrollments)

A running node polls `GET /faces/gallery/version` every
`WAMSS_GALLERY_POLL_SECONDS` (default 30 s). When the version changes it
re-fetches `GET /faces/gallery`, writes the entries as sidecars, and calls
`face_id.reload_gallery()`, so a person enrolled on the dashboard is
recognised within about 30 s without restarting any node. Sync is OFF
unless a backend is explicitly configured: set `WAMSS_GALLERY_BASE_URL`,
or set `WAMSS_BACKEND_URL` (the `/event` suffix is stripped). Any HTTP or
disk failure is logged and the local gallery is kept. Sync never runs in
privacy mode, and face recognition never runs in privacy mode
(`WAMSS_PRIVACY_MODE=1`): the privacy loop has no track ids, no names, and
no gallery fetch.

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
  401 means `WAMSS_API_KEY` is missing or wrong (see `docs/AUTH.md`).
- **Duplicate nodes on the dashboard:** two nodes share a `CAMERA_ID`. Give
  each node a unique id.
- **High CPU on the node:** YOLO runs on CPU by default. Reduce the input
  size or frame rate, or run the node on a machine with a GPU (torch with
  CUDA picks it up automatically).

Note: the event payload carries `camera_id`, `track_id` (stable per person
as `Person 1`, `Person 2`, ... via ByteTrack), `objects`, `confidence`,
`zone`, `person_name` (when face recognition is enrolled), `position_x` /
`position_y` (0..1 normalized, or meters when calibrated), `snapshot_path`,
and `dwell_seconds`. The backend exposes these through `GET /positions`
(live), `GET /heatmap`, `GET /occupancy`, `GET /snapshots/{file}`, and the
face endpoints (`POST /faces/enroll`, `GET /faces`, `GET /faces/gallery`).
