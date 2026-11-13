# WAMSS Testing: What Has Been Done

This is the honest test inventory. Every claim here names what was run, where,
and the exact result. Anything not listed here has not been tested. Live
results that still need a real person, a real watch, or a second machine are
in the "Not yet proven live" section at the end, and each one has a GitHub
issue so someone can claim it.

Last updated: 2026-11-13. GitHub main: `fb40340`.

## Automated suites (run on the Muse VM)

### Vision suite: 73 passed, 0 failed
Run with pytest in `~/workspace/.venvs/wamss-vision-test` (pytest 9.2.0,
numpy 2.5.4, requests). Covers:
- Identity cache voting: 20 tests (strict majority of last 7 votes, min 2
  agreeing votes, mean confidence 0.5 or higher, sticky names, re-check
  every 30 frames / 5 seconds).
- Re-identification: 9 tests. The core promise is proven here with the real
  `face_id` cosine matcher and the real `identity_cache` voter (stubbed
  InsightFace backend, synthetic 4-d embeddings): the same enrolled face
  under a NEW track ID is recognised as the same name with the same
  confidence; a non-matching face stays unknown; a name survives re-checks
  that find no face; a gallery reload names a newly enrolled person on a
  fresh track without restart.
- Privacy: 16 tests, including 2 new tests proving the identity paths are
  disabled in privacy mode (gallery refresher returns None, no HTTP fetch).
- Gallery sync: 13 tests (version-change sync, same-version skip, poll
  interval, fetch failures keep the local gallery, stale sidecars removed on
  dashboard delete, all disabled states).
- Pre-existing privacy and flag tests: 15.

### Backend suite: 133 passed, 0 failed
Run from inside `backend/` with the `wamss-backend` venv. Covers events,
activity, presence, edge endpoints (positions, heatmap, occupancy, faces,
snapshots), identities registry (enroll, delete, list, unknowns), auth,
alerts, analytics, exports, focus, hours, policy, security, weekly digest.
6 pre-existing Starlette deprecation warnings, unrelated to this build.

### Frontend: `npm run build` clean
Built in 6.32 seconds. No TypeScript errors. Room map, events table,
identity panels, and unknown people review all compile.

## End to end proofs (earlier rounds)

- Fresh-clone proof (2026-10-30): 57/57 tests, 3 employees x 7 days seeded,
  all API flows returned 200/201.
- Round 3: 93/93 tests. Round 4: 104/104 tests.
- Auth proxy proof: dashboard works with no client key (nginx attaches
  `X-API-Key`); direct backend access without a key returns 401.
- Docker proof: backend healthy on :8000, frontend 200 on :8080, seeded demo
  data (280 vision events, 746 activity records, 12 security events).
- Secrets and em-dash greps: zero secrets in the tree, zero em-dashes in
  new or changed files. `vision/faces/*.json` is gitignored and no gallery
  JSON is committed (verified with `git check-ignore`).

## Live proofs (real laptop camera, agbeast-1)

- Person detection verified with a saved photo: YOLO box "Person 93.7%",
  17+ FPS, on the real Smart Connect Camera (index 1). The Integrated Camera
  (index 0) shows only a dark Lenovo logo screen and is not the room.
- Person tracking verified live: ByteTrack persistent "Person N" IDs, events
  72/73 at 18.6 FPS.
- Volume: 499 detection events logged, 151 unique person IDs tracked.
- Computer work monitor verified live: prints WORKING/IDLE from real
  keyboard and mouse input; showed IDLE for 24 hours while the laptop sat
  untouched, which is correct.
- Camera frame check 2026-11-03: the live frame was only blur (likely the
  lid closed or the lens covered). Nothing to detect is not a detection
  failure, but it shows the camera needs a physical check before live tests.

## Not yet proven live (each has a GitHub issue)

- Naming a real enrolled person on camera, and remembering them after they
  leave and come back. The proof exists only with synthetic embeddings.
- Real smartwatch pairing and continuous heart rate plus HRV.
- A second camera and a second computer on one dashboard.
- Phone camera feeding the live pipeline.
- Geofence entry and exit alerts (geofencing is not built yet).
- Naming latency after first sight, name stability across real track-ID
  changes, and the false-match rate of a real enrolled gallery at the
  default thresholds.
- Load behaviour: many cameras plus many persons at once (FPS, CPU, memory).

## How to run the suites

```bash
# Vision (from the repo root, needs pytest, numpy, requests)
python -m pytest vision/tests/ -q

# Backend (from inside backend/)
cd backend && python -m pytest -q

# Frontend (from inside frontend/)
cd frontend && npm run build
```

## Rules for contributors

- Every PR must keep all three suites green.
- Never log keystroke content. A test must prove any new activity logging
  records counts only.
- Face recognition never runs in privacy mode. Any new identity code path
  needs a privacy test proving it stays off.
- Biometric gallery files (`vision/faces/*.json`) are never committed.
- A live claim needs evidence: a photo, a log line, or a short clip attached
  to the PR. "It works on my machine" without evidence does not count.
