
import os
import sys
import time

from ultralytics import YOLO
import cv2

try:
    from multicamera import _load_cameras, _load_env_files, HEADLESS, CONF_THRESHOLD, MODEL_PATH
except ImportError:
    # Standalone fallback when multicamera is not directly importable
    def _load_cameras():
        url = os.environ.get("WAMSS_CAMERA_URL") or os.environ.get("WAMSS_CAM_01_URL")
        return {"CAM_01": url} if url else {"CAM_01": 0}

    HEADLESS = os.environ.get("WAMSS_HEADLESS", "").lower() in ("1", "true", "yes")
    CONF_THRESHOLD = float(os.environ.get("WAMSS_CONF_THRESHOLD", 0.45))
    MODEL_PATH = os.environ.get("WAMSS_MODEL_PATH", "yolov8n.pt")


def main():
    # Load YOLO model
    model = YOLO(MODEL_PATH)

    # Resolve camera source: check configured cameras first, fallback to 0
    cameras = _load_cameras()
    source = cameras.get("CAM_01", 0) if cameras else 0
    if isinstance(source, str) and source.strip().isdigit():
        source = int(source.strip())

    camera = cv2.VideoCapture(source)

    if not camera.isOpened():
        print(f"Error: Could not open camera source '{source}'.")
        return

    print(f"WAMSS person detection started on source '{source}' (headless={HEADLESS}, conf={CONF_THRESHOLD}). Press Q or Ctrl+C to quit.")

    last_summary_time = 0.0

    try:
        while True:
            success, frame = camera.read()

            if not success or frame is None or getattr(frame, "size", 0) == 0:
                print("Could not read camera frame. Retrying...")
                time.sleep(0.1)
                continue

            # COCO class 0 = person
            results = model.predict(
                source=frame,
                classes=[0],
                conf=CONF_THRESHOLD,
                verbose=False,
            )

            boxes = results[0].boxes
            person_count = len(boxes) if boxes is not None else 0
            now = time.time()

            if HEADLESS:
                if person_count > 0 and now - last_summary_time >= 1.0:
                    confidences = [float(c) for c in boxes.conf.cpu().tolist()]
                    xyxy_list = [[round(coord, 1) for coord in b.tolist()] for b in boxes.xyxy.cpu().numpy()]
                    print(f"[detection] Detected {person_count} person(s): confidences={confidences}, boxes={xyxy_list}")
                    last_summary_time = now
            else:
                annotated_frame = results[0].plot()
                cv2.imshow("WAMSS - Person Detection", annotated_frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    except KeyboardInterrupt:
        print("\nStopping person detection...")

    finally:
        camera.release()
        if not HEADLESS:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
