
import threading
import time
import requests
import cv2
from ultralytics import YOLO

# Phone camera stream URLs
CAMERAS = {
    "CAM_01": "http://10.7.234.6:8080/video",
    "CAM_02": "http://10.7.203.112:8080/video",
}

MODEL_PATH = "yolov8n.pt"

# Your friend's Mac backend
BRIDGE_URL = "http://10.5.102.16:8000/event"

# Send detection events at most once per second per camera
EVENT_INTERVAL = 1.0


def run_camera(camera_id, stream_url):
    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(stream_url)
    last_event_time = 0.0

    if not cap.isOpened():
        print(f"[{camera_id}] ERROR: Cannot open camera stream")
        return

    print(f"[{camera_id}] Camera connected")

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

            # Send an event when a person is detected
            if (
                boxes is not None
                and len(boxes) > 0
                and now - last_event_time >= EVENT_INTERVAL
            ):
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
                        BRIDGE_URL,
                        json=event,
                        timeout=2.0,
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

            # Display detection boxes
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
        cv2.destroyAllWindows()


def main():
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
        cv2.destroyAllWindows()
        print("WAMSS stopped")


if __name__ == "__main__":
    main()