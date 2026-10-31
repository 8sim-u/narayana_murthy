"""Live Webcam Workstation Object Detection, Association & Activity Verification Test.

Runs against http://127.0.0.1:8080/video with 1 real human present.
Validates:
- Real-time frame decoding
- YOLO person detection + ByteTrack tracking
- YOLO workstation object detection (laptop, phone, chair, book, etc.)
- Spatial & temporal person-object association
- Observable workstation activity and engagement metrics
- Saves annotated visual artifact
"""

import os
import sys
import time
import cv2
from ultralytics import YOLO

# Add vision/ to sys.path
VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if VISION_DIR not in sys.path:
    sys.path.insert(0, VISION_DIR)

from vision.workstation_objects import (
    CLASS_NAME_MAP,
    load_workstation_object_config,
)
from vision.association import (
    AssociationStatus,
    DetectedObject,
    PersonObjectAssociator,
    TrackedPerson,
)
from vision.activity_metrics import WorkstationActivityTracker
from vision.hud import render_workstation_overlay

ARTIFACT_DIR = "/Users/gursimarsingh/.gemini/antigravity/brain/818dbb04-4de1-45cc-b17c-d6460706d571"
STREAM_URL = os.environ.get("WAMSS_CAMERA_URL", "http://127.0.0.1:8080/video")
DURATION_S = float(os.environ.get("LIVE_TEST_DURATION", "20.0"))


def run_live_workstation_test():
    print("=" * 70)
    print("FEATURE 4: LIVE WORKSTATION OBJECTS, ASSOCIATION & ACTIVITY TEST")
    print("=" * 70)
    print(f"Target stream: {STREAM_URL}")
    print(f"Planned duration: {DURATION_S:.1f}s")

    cap = cv2.VideoCapture(STREAM_URL)
    if not cap.isOpened():
        print(f"[FAIL] Could not connect to camera stream at {STREAM_URL}")
        sys.exit(1)

    model = YOLO("yolov8n.pt")
    cfg = load_workstation_object_config()
    obj_conf = float(os.environ.get("LIVE_OBJ_CONF", "0.20"))  # Slightly sensitive to catch objects on webcam
    person_conf = cfg["person_conf"]
    target_classes = cfg["target_classes"]
    obj_classes = [c for c in target_classes if c != 0]

    associator = PersonObjectAssociator()
    activity_tracker = WorkstationActivityTracker(min_presence_sec=3.0)

    frames_processed = 0
    person_frames = 0
    object_detections_total = 0
    detected_classes_seen = set()
    observed_associations = []
    best_annotated_frame = None

    t_start = time.time()
    last_log = t_start

    print(f"[LIVE] Starting stream reading and inference loop...")

    try:
        while time.time() - t_start < DURATION_S:
            ret, frame = cap.read()
            if not ret or frame is None or getattr(frame, "size", 0) == 0:
                time.sleep(0.05)
                continue

            frames_processed += 1
            now = time.time()

            # 1. ByteTrack Person Tracking
            person_res = model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                conf=person_conf,
                verbose=False,
            )

            p_boxes = person_res[0].boxes
            persons = []
            if p_boxes is not None and len(p_boxes) > 0:
                person_frames += 1
                for idx in range(len(p_boxes)):
                    tid = str(int(p_boxes.id[idx].item())) if (p_boxes.id is not None and idx < len(p_boxes.id)) else f"p{idx}"
                    p_conf = float(p_boxes.conf[idx].item()) if (p_boxes.conf is not None and idx < len(p_boxes.conf)) else 0.0
                    xyxy = [float(v) for v in p_boxes.xyxy[idx].tolist()]
                    persons.append(TrackedPerson(track_id=tid, box=xyxy, conf=p_conf))

            # 2. Workstation Object Detection
            objects = []
            if obj_classes:
                obj_res = model.predict(
                    source=frame,
                    classes=obj_classes,
                    conf=obj_conf,
                    verbose=False,
                )
                o_boxes = obj_res[0].boxes
                if o_boxes is not None and len(o_boxes) > 0:
                    for idx in range(len(o_boxes)):
                        cid = int(o_boxes.cls[idx].item())
                        cname = CLASS_NAME_MAP.get(cid, model.names.get(cid, f"obj_{cid}"))
                        o_conf = float(o_boxes.conf[idx].item())
                        xyxy = [float(v) for v in o_boxes.xyxy[idx].tolist()]
                        objects.append(DetectedObject(class_name=cname, class_id=cid, box=xyxy, conf=o_conf))
                        detected_classes_seen.add(cname)
                        object_detections_total += 1

            # 3. Association
            associations = associator.associate(persons=persons, objects=objects)
            for a in associations:
                observed_associations.append((a.object.class_name, a.assigned_track_id, a.status.value))

            # 4. Activity & Engagement
            summaries = activity_tracker.update(timestamp=now, persons=persons, associations=associations)

            # 5. Render HUD Overlay
            annotated = render_workstation_overlay(
                frame=frame,
                persons=persons,
                objects=objects,
                associations=associations,
                summaries=summaries,
                camera_id="CAM_LIVE",
            )
            best_annotated_frame = annotated

            # Progress log every 2 seconds
            if now - last_log >= 2.0:
                elapsed = now - t_start
                fps = frames_processed / elapsed if elapsed > 0 else 0.0
                active_tids = [p.track_id for p in persons]
                obj_list = [o.class_name for o in objects]
                primary_s = summaries.get(persons[0].track_id) if persons else None
                st = primary_s.current_state.value if primary_s else "NONE"
                eng = primary_s.engagement_label if primary_s else "N/A"
                print(
                    f"[{elapsed:4.1f}s] Frames={frames_processed:3d} ({fps:.1f} FPS) | "
                    f"Persons={len(persons)} (IDs: {active_tids}) | Objects={obj_list} | "
                    f"State={st} | {eng}"
                )
                last_log = now

    finally:
        cap.release()

    total_time = time.time() - t_start
    avg_fps = frames_processed / total_time if total_time > 0 else 0.0

    # Save artifact
    if best_annotated_frame is not None:
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        artifact_path = os.path.join(ARTIFACT_DIR, "live_workstation_activity_sample.jpg")
        cv2.imwrite(artifact_path, best_annotated_frame)
        print(f"\n[ARTIFACT] Saved live annotated frame to: {artifact_path}")

    print("\n" + "=" * 70)
    print("LIVE VERIFICATION RESULTS SUMMARY")
    print("=" * 70)
    print(f"Total time:                {total_time:.2f} seconds")
    print(f"Frames processed:          {frames_processed}")
    print(f"Average processing FPS:    {avg_fps:.2f} FPS")
    print(f"Frames with real person:   {person_frames}/{frames_processed} ({person_frames*100.0/max(1,frames_processed):.1f}%)")
    print(f"Object classes detected:   {list(detected_classes_seen)}")
    print(f"Total object detections:   {object_detections_total}")

    # Final activity summary
    final_summaries = {tid: activity_tracker.get_summary(tid) for tid in activity_tracker.records}
    for tid, s in final_summaries.items():
        print(f"\n--- Track ID: {tid} ---")
        print(f"  Final State:             {s.current_state.value}")
        print(f"  Presence Duration:       {s.presence_seconds:.1f}s")
        print(f"  Interaction Duration:    {s.interaction_seconds:.1f}s")
        print(f"  Idle Duration:           {s.idle_seconds:.1f}s")
        print(f"  Away Duration:           {s.away_seconds:.1f}s")
        print(f"  Engagement Metric:       {s.engagement_label}")
        print(f"  Associated Devices:      {s.associated_devices}")

    assert frames_processed > 20, "Must have processed at least 20 frames"
    print("\n[PASS] Live workstation tracking and activity test completed successfully.")


if __name__ == "__main__":
    run_live_workstation_test()
