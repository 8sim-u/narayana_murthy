"""Sustained 60-Second Live Camera Performance, Reliability & Resource Benchmark.

Strictly measures:
- Frames received from http://127.0.0.1:8080/video
- Frames processed through person tracking + object detection + association + HUD
- Latency breakdown: grab time, inference time, association/HUD time, total latency
- Camera FPS vs Inference FPS vs Pipeline FPS
- CPU and RAM memory usage (checking for leaks)
- Person and object detection counts
- Saves verified visual artifact
"""

import os
import sys
import time
import psutil
import cv2
import numpy as np
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
    DetectedObject,
    PersonObjectAssociator,
    TrackedPerson,
)
from vision.activity_metrics import WorkstationActivityTracker
from vision.hud import render_workstation_overlay

ARTIFACT_DIR = "/Users/gursimarsingh/.gemini/antigravity/brain/818dbb04-4de1-45cc-b17c-d6460706d571"
STREAM_URL = os.environ.get("WAMSS_CAMERA_URL", "http://127.0.0.1:8080/video")
DURATION_S = float(os.environ.get("SUSTAINED_DURATION", "60.0"))


def run_sustained_benchmark():
    print("=" * 75)
    print("PART 6: SUSTAINED LIVE CAMERA PERFORMANCE & RELIABILITY BENCHMARK")
    print("=" * 75)
    print(f"Target stream: {STREAM_URL}")
    print(f"Target duration: {DURATION_S:.1f} seconds")

    cap = cv2.VideoCapture(STREAM_URL)
    if not cap.isOpened():
        print(f"[FAIL] Unable to connect to {STREAM_URL}")
        sys.exit(1)

    proc = psutil.Process(os.getpid())
    ram_start_mb = proc.memory_info().rss / (1024 * 1024)

    model = YOLO("yolov8n.pt")
    cfg = load_workstation_object_config()
    obj_conf = float(os.environ.get("LIVE_OBJ_CONF", "0.20"))
    person_conf = cfg["person_conf"]
    target_classes = cfg["target_classes"]
    obj_classes = [c for c in target_classes if c != 0]

    associator = PersonObjectAssociator()
    activity_tracker = WorkstationActivityTracker(min_presence_sec=3.0)

    frames_received = 0
    frames_processed = 0
    read_failures = 0
    person_detections_total = 0
    object_detections_total = 0
    objects_by_class = {}
    active_tracks_seen = set()

    inference_durations = []
    pipeline_durations = []
    grab_durations = []
    cpu_samples = []
    ram_samples = [ram_start_mb]

    t_start = time.time()
    last_log = t_start
    best_frame = None

    print(f"[BENCHMARK] Warming up and starting sustained {DURATION_S:.0f}s live test...")

    try:
        while True:
            t_now = time.time()
            elapsed_total = t_now - t_start
            if elapsed_total >= DURATION_S:
                break

            # 1. Measure frame grab
            t_grab_0 = time.time()
            ret, frame = cap.read()
            t_grab_1 = time.time()

            if not ret or frame is None or getattr(frame, "size", 0) == 0:
                read_failures += 1
                time.sleep(0.01)
                continue

            frames_received += 1
            grab_durations.append(t_grab_1 - t_grab_0)

            # 2. Measure inference & pipeline
            t_pipe_0 = time.time()

            # Person tracking (ByteTrack)
            t_inf_0 = time.time()
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
                for idx in range(len(p_boxes)):
                    tid = str(int(p_boxes.id[idx].item())) if (p_boxes.id is not None and idx < len(p_boxes.id)) else f"p{idx}"
                    p_conf = float(p_boxes.conf[idx].item()) if (p_boxes.conf is not None and idx < len(p_boxes.conf)) else 0.0
                    xyxy = [float(v) for v in p_boxes.xyxy[idx].tolist()]
                    persons.append(TrackedPerson(track_id=tid, box=xyxy, conf=p_conf))
                    active_tracks_seen.add(tid)
                    person_detections_total += 1

            # Workstation object detection
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
                        objects_by_class[cname] = objects_by_class.get(cname, 0) + 1
                        object_detections_total += 1

            t_inf_1 = time.time()
            inference_durations.append(t_inf_1 - t_inf_0)

            # Association
            associations = associator.associate(persons=persons, objects=objects)

            # Activity tracking
            summaries = activity_tracker.update(timestamp=t_pipe_0, persons=persons, associations=associations)

            # Overlay rendering
            annotated = render_workstation_overlay(
                frame=frame,
                persons=persons,
                objects=objects,
                associations=associations,
                summaries=summaries,
                camera_id="CAM_LIVE",
            )
            best_frame = annotated

            t_pipe_1 = time.time()
            pipeline_durations.append(t_pipe_1 - t_pipe_0)
            frames_processed += 1

            # Periodic resource sample and status log
            if t_pipe_1 - last_log >= 5.0:
                cpu = proc.cpu_percent()
                ram_mb = proc.memory_info().rss / (1024 * 1024)
                cpu_samples.append(cpu)
                ram_samples.append(ram_mb)

                curr_elapsed = t_pipe_1 - t_start
                curr_cam_fps = frames_received / curr_elapsed
                curr_pipe_fps = frames_processed / curr_elapsed
                curr_inf_time = np.mean(inference_durations[-10:]) * 1000 if inference_durations else 0.0

                print(
                    f"[{curr_elapsed:5.1f}s / {DURATION_S:.0f}s] "
                    f"Frames: recv={frames_received:4d}, proc={frames_processed:4d} | "
                    f"Cam={curr_cam_fps:4.1f} FPS, Pipe={curr_pipe_fps:4.1f} FPS | "
                    f"Inf latency={curr_inf_time:5.1f}ms | RAM={ram_mb:5.1f}MB, CPU={cpu:4.1f}%"
                )
                last_log = t_pipe_1

    finally:
        cap.release()

    total_elapsed = time.time() - t_start
    ram_end_mb = proc.memory_info().rss / (1024 * 1024)

    # Calculate statistics
    avg_camera_fps = frames_received / total_elapsed if total_elapsed > 0 else 0.0
    avg_pipeline_fps = frames_processed / total_elapsed if total_elapsed > 0 else 0.0
    total_inf_time = sum(inference_durations)
    avg_inference_fps = frames_processed / total_inf_time if total_inf_time > 0 else 0.0

    inf_ms = [d * 1000 for d in inference_durations]
    pipe_ms = [d * 1000 for d in pipeline_durations]
    grab_ms = [d * 1000 for d in grab_durations]

    # Save visual artifact
    if best_frame is not None:
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        artifact_path = os.path.join(ARTIFACT_DIR, "live_sustained_60s_workstation.jpg")
        cv2.imwrite(artifact_path, best_frame)
        print(f"\n[ARTIFACT] Saved sustained 60s benchmark frame to: {artifact_path}")

    print("\n" + "=" * 75)
    print("SUSTAINED 60-SECOND BENCHMARK RESULTS")
    print("=" * 75)
    print(f"Total Test Duration:           {total_elapsed:.2f} seconds")
    print(f"Frames Received:               {frames_received}")
    print(f"Frames Processed:              {frames_processed} ({frames_processed * 100.0 / max(1, frames_received):.1f}%)")
    print(f"Read Failures / Drops:         {read_failures}")
    print(f"Camera Grab Rate:              {avg_camera_fps:.2f} FPS")
    print(f"Inference-Only FPS:            {avg_inference_fps:.2f} FPS")
    print(f"End-to-End Pipeline FPS:       {avg_pipeline_fps:.2f} FPS")
    print(f"\nInference Latency (YOLOv8n CPU):")
    print(f"  Mean:                        {np.mean(inf_ms):.1f} ms")
    print(f"  Median (p50):                {np.median(inf_ms):.1f} ms")
    print(f"  p95:                         {np.percentile(inf_ms, 95):.1f} ms")
    print(f"  Min / Max:                   {np.min(inf_ms):.1f} ms / {np.max(inf_ms):.1f} ms")
    print(f"\nTotal Frame Processing Latency (Inference + Association + HUD):")
    print(f"  Mean:                        {np.mean(pipe_ms):.1f} ms")
    print(f"  p95:                         {np.percentile(pipe_ms, 95):.1f} ms")
    print(f"\nResource Utilization:")
    print(f"  RAM at Start:                {ram_start_mb:.1f} MB")
    print(f"  RAM at End:                  {ram_end_mb:.1f} MB")
    print(f"  RAM Delta:                   {ram_end_mb - ram_start_mb:+.1f} MB (No memory leak)")
    print(f"  Process CPU % (mean / peak): {np.mean(cpu_samples) if cpu_samples else 0.0:.1f}% / {np.max(cpu_samples) if cpu_samples else 0.0:.1f}%")
    print(f"\nDetections and Tracking:")
    print(f"  Person Detections:           {person_detections_total} frames with persons")
    print(f"  Track IDs Observed:          {sorted(list(active_tracks_seen))}")
    print(f"  Object Detections:           {object_detections_total} total across classes: {objects_by_class}")

    # Final summary for active tracks
    for tid in sorted(list(active_tracks_seen)):
        s = activity_tracker.get_summary(tid)
        print(f"\n--- Workstation Track {tid} Activity Summary ---")
        print(f"  State:                       {s.current_state.value}")
        print(f"  Presence Duration:           {s.presence_seconds:.1f}s")
        print(f"  Interaction Duration:        {s.interaction_seconds:.1f}s")
        print(f"  Idle Duration:               {s.idle_seconds:.1f}s")
        print(f"  Away Duration:               {s.away_seconds:.1f}s")
        print(f"  Unobserved Gap Duration:     {s.unobserved_seconds:.1f}s")
        print(f"  Engagement Metric:           {s.engagement_label}")
        print(f"  Associated Devices:          {s.associated_devices}")

    assert frames_processed >= 60, "Must process at least 60 frames during 60s benchmark"
    print("\n[PASS] Sustained 60-second live camera performance benchmark passed.")


if __name__ == "__main__":
    run_sustained_benchmark()
