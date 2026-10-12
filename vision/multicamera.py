
import threading
import time
import requests
import cv2
from ultralytics import YOLO

# Camera stream URLs
CAMERAS = {
    "CAM_01": "http://10.7.234.6:8080/video",
    "CAM_02": "http://10.7.203.112:8080/video",
}

MODEL_PATH = "yolov8n.pt"
BRIDGE_URL = "http://127.0.0.1:8000/event"


def run_camera(camera_id, stream_url):
    # Each camera gets its own model and tracker state.
    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(stream_url)
    last_event_time = 0.0

    if not cap.isOpened():
        print(f"[{camera_id}] Could not open stream: {stream_url}")
        return

    print(f"[{camera_id}] Connected")

    try:
        while True:
            success, frame = cap.read()

            if not success:
                print(f"[{camera_id}] Frame unavailable")
                break

            results = model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                conf=0.45,
                verbose=False,
            )

            # Send person detections to the central decision bridge.
            now = time.time()

            if now - last_event_time >= 1.0:
                boxes = results[0].boxes

                if boxes is not None and len(boxes) > 0:
                    track_ids = (
                        boxes.id.int().cpu().tolist()
                        if boxes.id is not None
                        else [None] * len(boxes)
                    )
                    confidences = boxes.conf.cpu().tolist()

                    # Send at most one event per person per reporting interval.
                    for track_id, confidence in zip(
                        track_ids, confidences
                    ):
                        event = {
                            "camera_id": camera_id,
                            "track_id": (
                                str(track_id)
                                if track_id is not None
                                else "unknown"
                            ),
                            "objects": ["person"],
                            "confidence": float(confidence),
                            "zone": "UNKNOWN",
                        }

                        try:
                            response = requests.post(
                                BRIDGE_URL,
                                json=event,
                                timeout=0.5,
                            )
                            response.raise_for_status()

                            decision = response.json().get(
                                "decision", "UNKNOWN"
                            )
                            print(
                                f"[{camera_id}] "
                                f"Person detected | Decision: {decision}"
                            )

                        except requests.RequestException as exc:
                            print(
                                f"[{camera_id}] "
                                f"Bridge request failed: {exc}"
                            )

                last_event_time = now

            # Display annotated video.
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

            # Press Q to stop the camera windows.
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    except Exception as exc:
        print(f"[{camera_id}] Error: {exc}")

    finally:
        cap.release()


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
                thread.join(timeout=0.1)

    except KeyboardInterrupt:
        print("Stopping WAMSS...")

    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
