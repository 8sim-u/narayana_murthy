import os
import sys
import time

from ultralytics import YOLO
import cv2

# Ensure vision/ is importable
VISION_DIR = os.path.dirname(os.path.abspath(__file__))
if VISION_DIR not in sys.path:
    sys.path.insert(0, VISION_DIR)

from vision.workstation_objects import (
    CLASS_NAME_MAP,
    load_workstation_object_config,
)
from vision.association import (
    DetectedObject,
    PersonObjectAssociator,
    TrackedPerson,
)
from vision.activity_metrics import WorkstationActivityTracker
from vision.hud import render_workstation_overlay

try:
    from multicamera import _load_cameras, _load_env_files, HEADLESS, CONF_THRESHOLD, MODEL_PATH
except ImportError:
    def _load_cameras():
        url = os.environ.get("WAMSS_CAMERA_URL") or os.environ.get("WAMSS_CAM_01_URL")
        return {"CAM_01": url} if url else {"CAM_01": 0}

    HEADLESS = os.environ.get("WAMSS_HEADLESS", "").lower() in ("1", "true", "yes")
    CONF_THRESHOLD = float(os.environ.get("WAMSS_CONF_THRESHOLD", 0.45))
    MODEL_PATH = os.environ.get("WAMSS_MODEL_PATH", "yolov8n.pt")


def main():
    model = YOLO(MODEL_PATH)
    cfg = load_workstation_object_config()
    obj_conf = cfg["object_conf"]
    target_classes = cfg["target_classes"]
    obj_target_classes = [c for c in target_classes if c != 0]

    associator = PersonObjectAssociator()
    activity_tracker = WorkstationActivityTracker()

    cameras = _load_cameras()
    source = cameras.get("CAM_01", 0) if cameras else 0
    if isinstance(source, str) and source.strip().isdigit():
        source = int(source.strip())

    camera = cv2.VideoCapture(source)

    if not camera.isOpened():
        print(f"Error: Could not open camera source '{source}'.")
        return

    print(
        f"WAMSS person & workstation tracking started on '{source}' "
        f"(headless={HEADLESS}, conf={CONF_THRESHOLD}, obj_conf={obj_conf}). Press Q or Ctrl+C to quit."
    )

    last_summary_time = 0.0

    try:
        while True:
            success, frame = camera.read()

            if not success or frame is None or getattr(frame, "size", 0) == 0:
                print("Could not read camera frame. Retrying...")
                time.sleep(0.1)
                continue

            now = time.time()

            # 1. ByteTrack human tracking (strictly on people: class 0)
            person_results = model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                conf=CONF_THRESHOLD,
                verbose=False,
            )

            person_boxes = person_results[0].boxes
            persons = []
            if person_boxes is not None and len(person_boxes) > 0:
                for idx in range(len(person_boxes)):
                    tid = (
                        str(int(person_boxes.id[idx].item()))
                        if (person_boxes.id is not None and idx < len(person_boxes.id))
                        else f"p{idx}"
                    )
                    conf = (
                        float(person_boxes.conf[idx].item())
                        if (person_boxes.conf is not None and idx < len(person_boxes.conf))
                        else 0.0
                    )
                    xyxy = [float(v) for v in person_boxes.xyxy[idx].tolist()]
                    persons.append(TrackedPerson(track_id=tid, box=xyxy, conf=conf))

            # 2. Workstation object detection (laptops, phones, keyboards, mice, monitors, books)
            objects = []
            if obj_target_classes:
                obj_results = model.predict(
                    source=frame,
                    classes=obj_target_classes,
                    conf=obj_conf,
                    verbose=False,
                )
                obj_boxes = obj_results[0].boxes
                if obj_boxes is not None and len(obj_boxes) > 0:
                    for idx in range(len(obj_boxes)):
                        cid = int(obj_boxes.cls[idx].item())
                        cname = CLASS_NAME_MAP.get(cid, model.names.get(cid, f"obj_{cid}"))
                        conf = float(obj_boxes.conf[idx].item())
                        xyxy = [float(v) for v in obj_boxes.xyxy[idx].tolist()]
                        objects.append(DetectedObject(class_name=cname, class_id=cid, box=xyxy, conf=conf))

            # 3. Spatial & temporal association
            associations = associator.associate(persons=persons, objects=objects)

            # 4. Activity & engagement metrics
            summaries = activity_tracker.update(timestamp=now, persons=persons, associations=associations)

            # 5. Output / Display
            if HEADLESS:
                if (persons or objects) and now - last_summary_time >= 1.0:
                    tracks = [p.track_id for p in persons]
                    obj_names = [o.class_name for o in objects]
                    primary_summary = summaries.get(persons[0].track_id) if persons else None
                    state_str = primary_summary.current_state.value if primary_summary else "NO_PERSON"
                    eng_str = primary_summary.engagement_label if primary_summary else "N/A"
                    print(
                        f"[tracking] Active tracks: {tracks} | Objects: {obj_names} | "
                        f"State: {state_str} | {eng_str}"
                    )
                    last_summary_time = now
            else:
                annotated_frame = render_workstation_overlay(
                    frame=frame,
                    persons=persons,
                    objects=objects,
                    associations=associations,
                    summaries=summaries,
                    camera_id="CAM_01",
                )
                cv2.imshow("WAMSS - Person & Workstation Tracking", annotated_frame)

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    except KeyboardInterrupt:
        print("\nStopping workstation tracking...")

    finally:
        camera.release()
        if not HEADLESS:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
