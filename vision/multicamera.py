"""Multi-camera person detection for the WAMSS edge node (Person A).

Configuration is driven entirely by environment variables (see
vision/.env.example). Nothing in this file contains a real camera IP or a
real backend address: defaults are placeholders that point at RFC 5737
documentation addresses, so running without config is safe and inert.
"""
import json
import os
import re
import sys
import threading
import time

import requests
import cv2
from ultralytics import YOLO


def _parse_bool(value, default):
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_float(value, default):
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        print(f"[config] WARNING: invalid float '{value}', using {default}")
        return default


def _load_cameras():
    """Resolve the camera map, highest priority source first.

    1. WAMSS_CAMERAS: a JSON object mapping camera id to stream URL, e.g.
       WAMSS_CAMERAS='{"CAM_01":"http://10.0.0.5:8080/video"}'
    2. Numbered vars: WAMSS_CAM_<id>_URL, e.g. WAMSS_CAM_01_URL=...
    3. SAMPLE_CAMERAS: placeholder URLs only (never real cameras).
    """
    raw = os.environ.get("WAMSS_CAMERAS")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data:
                print(f"[config] Cameras from WAMSS_CAMERAS: {sorted(data)}")
                return dict(data)
            print("[config] WARNING: WAMSS_CAMERAS is empty or not an "
                  "object, falling back to numbered vars")
        except json.JSONDecodeError as exc:
            print(f"[config] WARNING: WAMSS_CAMERAS is not valid JSON "
                  f"({exc}), falling back to numbered vars")

    numbered = {}
    for key, value in sorted(os.environ.items()):
        match = re.fullmatch(r"WAMSS_CAM_([A-Z0-9]+)_URL", key)
        if match and value.strip():
            numbered[f"CAM_{match.group(1)}"] = value.strip()
    if numbered:
        print(f"[config] Cameras from numbered vars: {sorted(numbered)}")
        return numbered

    print("[config] WARNING: no cameras configured; using SAMPLE-ONLY "
          "placeholder URLs (192.0.2.x documentation addresses). Set "
          "WAMSS_CAMERAS or WAMSS_CAM_<id>_URL to run for real.")
    return dict(SAMPLE_CAMERAS)


# SAMPLE ONLY: placeholder URLs using RFC 5737 TEST-NET-1 documentation
# addresses. They will never reach a real camera. These exist only so the
# module imports safely without configuration; real deployments MUST set
# WAMSS_CAMERAS or WAMSS_CAM_<id>_URL.
SAMPLE_CAMERAS = {
    "CAM_01": "http://192.0.2.10:8080/video",
    "CAM_02": "http://192.0.2.11:8080/video",
}

CAMERAS = _load_cameras()

MODEL_PATH = os.environ.get("WAMSS_MODEL_PATH", "yolov8n.pt")

# The real backend /event endpoint (NOT the deprecated standalone bridge).
BACKEND_URL = os.environ.get("WAMSS_BACKEND_URL", "http://localhost:8000/event")

# Shared secret for the backend API key auth (docs/AUTH.md). Sent as the
# X-API-Key header when set; when empty, auth stays disabled on the backend.
BACKEND_API_KEY = os.environ.get("WAMSS_API_KEY", "")


def _backend_headers():
    """Headers for backend POSTs; includes X-API-Key only when configured."""
    return {"X-API-Key": BACKEND_API_KEY} if BACKEND_API_KEY else {}

# Send detection events at most this often per camera, in seconds.
EVENT_INTERVAL = _parse_float(os.environ.get("WAMSS_EVENT_INTERVAL"), 1.0)

# Headless mode: skip cv2.imshow / waitKey window calls and log detection
# summaries instead. Defaults to true when there is no X/Wayland display on
# Linux; override explicitly with WAMSS_HEADLESS=0/1.
if sys.platform.startswith("linux"):
    _headless_default = "DISPLAY" not in os.environ
else:
    _headless_default = False
HEADLESS = _parse_bool(os.environ.get("WAMSS_HEADLESS"), _headless_default)

# Throttle for headless per-frame detection summaries (seconds).
HEADLESS_LOG_INTERVAL = 5.0


def run_camera(camera_id, stream_url):
    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(stream_url)
    last_event_time = 0.0
    last_summary_time = 0.0

    if not cap.isOpened():
        print(f"[{camera_id}] ERROR: Cannot open camera stream")
        return

    print(f"[{camera_id}] Camera connected (headless={HEADLESS})")

    try:
        while True:
            success, frame = cap.read()

            if not success:
                print(f"[{camera_id}] No frame received")
                time.sleep(0.2)
                continue

            results = model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                conf=0.45,
                verbose=False,
            )

            boxes = results[0].boxes
            now = time.time()

            person_count = len(boxes) if boxes is not None else 0

            # Send an event when a person is detected
            if person_count > 0 and now - last_event_time >= EVENT_INTERVAL:
                confidences = boxes.conf.cpu().tolist()

                track_id = "unknown"
                if boxes.id is not None:
                    track_id = str(int(boxes.id[0].item()))

                event = {
                    "camera_id": camera_id,
                    "track_id": track_id,
                    "objects": ["person"],
                    "confidence": float(max(confidences)),
                    "zone": "UNKNOWN",
                }

                try:
                    response = requests.post(
                        BACKEND_URL,
                        json=event,
                        timeout=2.0,
                        headers=_backend_headers(),
                    )
                    response.raise_for_status()

                    data = response.json()
                    print(
                        f"[{camera_id}] Person detected | "
                        f"Backend: {data.get('decision', 'UNKNOWN')} | "
                        f"Track: {track_id}"
                    )

                except requests.RequestException as exc:
                    print(f"[{camera_id}] Backend request failed: {exc}")

                last_event_time = now
                last_summary_time = now

            elif HEADLESS and person_count > 0 \
                    and now - last_summary_time >= HEADLESS_LOG_INTERVAL:
                # Headless mode: no preview window, so log a short summary
                # between event sends instead of drawing frames.
                max_conf = float(max(boxes.conf.cpu().tolist()))
                print(f"[{camera_id}] Headless summary: {person_count} "
                      f"person(s) in view, max confidence {max_conf:.2f}")
                last_summary_time = now

            if HEADLESS:
                # No display available: keep processing frames without any
                # window calls.
                continue

            # Windowed preview (only reached when a display is available)
            display = results[0].plot()

            cv2.putText(
                display,
                camera_id,
                (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 255, 0),
                2,
            )

            cv2.imshow(camera_id, display)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    except Exception as exc:
        print(f"[{camera_id}] Error: {exc}")

    finally:
        cap.release()
        if not HEADLESS:
            cv2.destroyAllWindows()


def main():
    if not CAMERAS:
        print("[config] ERROR: no cameras configured and no sample default "
              "is in use. Set WAMSS_CAMERAS or WAMSS_CAM_<id>_URL.")
        return

    print(f"[config] Backend: {BACKEND_URL}")
    print(f"[config] Model: {MODEL_PATH}")
    print(f"[config] Event interval: {EVENT_INTERVAL}s, headless: {HEADLESS}")

    threads = []

    for camera_id, stream_url in CAMERAS.items():
        thread = threading.Thread(
            target=run_camera,
            args=(camera_id, stream_url),
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    try:
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0.2)

    except KeyboardInterrupt:
        print("Stopping WAMSS...")

    finally:
        if not HEADLESS:
            cv2.destroyAllWindows()
        print("WAMSS stopped")


if __name__ == "__main__":
    main()
