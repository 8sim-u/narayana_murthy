"""Independent Verification Script for Feature 2: YOLO Human Detection.

Executes tests A, B, C, D, and E against the live camera stream and real YOLO model.
"""
import os
import sys
import time
import numpy as np

VISION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if VISION_DIR not in sys.path:
    sys.path.insert(0, VISION_DIR)

import cv2
from ultralytics import YOLO
import multicamera

def main():
    print("=" * 70)
    print("INDEPENDENT VERIFICATION OF FEATURE 2 (YOLO HUMAN DETECTION)")
    print("=" * 70)

    # 1. Config & Source Resolution
    cameras = multicamera._load_cameras()
    stream_url = cameras.get("CAM_01", "http://127.0.0.1:8080/video")
    model_path = os.environ.get("WAMSS_MODEL_PATH", "yolov8n.pt")
    conf_thresh = float(os.environ.get("WAMSS_CONF_THRESHOLD", 0.45))

    print(f"Target Stream URL: {stream_url}")
    print(f"Model Path:        {model_path}")
    print(f"Conf Threshold:    {conf_thresh}")

    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        print(f"[ERROR] Failed to open VideoCapture for {stream_url}")
        sys.exit(1)

    print(f"[OK] Stream successfully opened.")

    # Load Model
    print(f"Loading YOLO model...")
    model = YOLO(model_path)
    device = str(model.device)
    print(f"[OK] Model loaded on device: {device}")

    # =========================================================================
    # SUBTEST A: Camera and Inference Smoke Test (Process >= 100 live frames)
    # =========================================================================
    print("\n" + "-" * 70)
    print("SUBTEST A: Camera and Inference Smoke Test (>= 100 Live Frames)")
    print("-" * 70)

    target_frames = 100
    processed_frames = 0
    camera_read_times = []
    inference_times = []
    read_errors = 0
    decode_errors = 0
    inference_errors = 0
    frame_dimensions = None
    prev_frame_hash = None
    differing_frames_count = 0

    t_start = time.time()

    while processed_frames < target_frames and (time.time() - t_start) < 15.0:
        t0 = time.time()
        success, frame = cap.read()
        t1 = time.time()

        if not success:
            read_errors += 1
            time.sleep(0.02)
            continue

        if frame is None or getattr(frame, "size", 0) == 0:
            decode_errors += 1
            time.sleep(0.02)
            continue

        camera_read_times.append(t1 - t0)
        processed_frames += 1

        if frame_dimensions is None:
            frame_dimensions = frame.shape

        # Verify dynamic change
        cur_hash = hash(frame.tobytes()[:2000])
        if prev_frame_hash is not None and cur_hash != prev_frame_hash:
            differing_frames_count += 1
        prev_frame_hash = cur_hash

        # Real YOLO inference call
        t2 = time.time()
        try:
            results = model.predict(
                source=frame,
                classes=[0],
                conf=conf_thresh,
                verbose=False,
            )
            t3 = time.time()
            inference_times.append(t3 - t2)
        except Exception as exc:
            inference_errors += 1
            print(f"[ERROR] Inference failed at frame {processed_frames}: {exc}")

    t_total = time.time() - t_start

    avg_read_ms = float(np.mean(camera_read_times) * 1000) if camera_read_times else 0.0
    avg_inf_ms = float(np.mean(inference_times) * 1000) if inference_times else 0.0
    camera_fps = 1000.0 / avg_read_ms if avg_read_ms > 0 else 0.0
    inference_fps = 1000.0 / avg_inf_ms if avg_inf_ms > 0 else 0.0
    pipeline_fps = processed_frames / t_total if t_total > 0 else 0.0

    print(f"Processed Frames:       {processed_frames} / {target_frames}")
    print(f"Frame Dimensions:       {frame_dimensions}")
    print(f"Total Elapsed Time:     {t_total:.2f} s")
    print(f"Camera Read Latency:    {avg_read_ms:.2f} ms (~{camera_fps:.1f} FPS)")
    print(f"YOLO Inference Latency: {avg_inf_ms:.2f} ms (~{inference_fps:.1f} FPS)")
    print(f"Pipeline Throughput:    {pipeline_fps:.1f} FPS")
    print(f"Dynamic Frame Changes:  {differing_frames_count} / {processed_frames - 1}")
    print(f"Connection/Read Errors: {read_errors}")
    print(f"Decode Errors:          {decode_errors}")
    print(f"Inference Errors:       {inference_errors}")
    subtest_a_pass = (processed_frames >= target_frames and read_errors == 0 and decode_errors == 0 and inference_errors == 0)
    print(f"SUBTEST A RESULT:       {'PASS' if subtest_a_pass else 'FAIL'}")

    # =========================================================================
    # SUBTEST B: Person Detection Test (Real Live Person in View)
    # =========================================================================
    print("\n" + "-" * 70)
    print("SUBTEST B: Person Detection Test (Live Webcam Scene)")
    print("-" * 70)

    # Read latest fresh frame
    ret, live_frame = cap.read()
    if not ret or live_frame is None:
        print("[ERROR] Failed to read live frame for Subtest B")
        subtest_b_pass = False
    else:
        results = model.predict(source=live_frame, classes=[0], conf=conf_thresh, verbose=False)
        boxes = results[0].boxes
        num_people = len(boxes) if boxes is not None else 0
        print(f"Live Frame Person Detections: {num_people}")

        subtest_b_pass = False
        if num_people >= 1:
            h, w = live_frame.shape[:2]
            all_valid_coords = True
            for i, b in enumerate(boxes):
                xyxy = b.xyxy[0].cpu().tolist()
                conf = float(b.conf[0])
                cls_id = int(b.cls[0])
                cls_name = results[0].names[cls_id]
                x1, y1, x2, y2 = xyxy

                within_bounds = (0 <= x1 < x2 <= w) and (0 <= y1 < y2 <= h)
                if not within_bounds:
                    all_valid_coords = False

                print(f"  Person {i+1}:")
                print(f"    Class ID:   {cls_id} ('{cls_name}')")
                print(f"    Confidence: {conf:.4f} (>= {conf_thresh})")
                print(f"    BBox xyxy:  [{x1:.1f}, {y1:.1f}, {x2:.1f}, {y2:.1f}]")
                print(f"    Within Bounds (W={w}, H={h}): {within_bounds}")

            if all_valid_coords:
                subtest_b_pass = True

    print(f"SUBTEST B RESULT:       {'PASS' if subtest_b_pass else 'FAIL'}")

    # =========================================================================
    # SUBTEST C: No-Person Test
    # =========================================================================
    print("\n" + "-" * 70)
    print("SUBTEST C: No-Person Test (Occluded / Blank Frame Evaluation)")
    print("-" * 70)

    blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    res_blank = model.predict(source=blank_frame, classes=[0], conf=conf_thresh, verbose=False)
    blank_detections = len(res_blank[0].boxes) if res_blank[0].boxes is not None else 0
    print(f"Detections on Blank / Occluded Frame: {blank_detections}")
    subtest_c_pass = (blank_detections == 0)
    print(f"SUBTEST C RESULT:       {'PASS' if subtest_c_pass else 'FAIL'}")

    # =========================================================================
    # SUBTEST D: Multiple-Person Test (Live Camera Only)
    # =========================================================================
    print("\n" + "-" * 70)
    print("SUBTEST D: Real Multiple-Person Test (Live Camera View)")
    print("-" * 70)

    # Check live webcam to see how many real people are currently visible
    ret, live_multi = cap.read()
    res_multi = model.predict(source=live_multi, classes=[0], conf=conf_thresh, verbose=False)
    live_people_count = len(res_multi[0].boxes) if res_multi[0].boxes is not None else 0
    print(f"Actual Real People Visible in Current Live Webcam Feed: {live_people_count}")

    if live_people_count >= 2:
        subtest_d_status = "PASS"
        print(f"Found {live_people_count} distinct real people in live frame!")
        for i, b in enumerate(res_multi[0].boxes):
            print(f"  Person {i+1}: conf={float(b.conf[0]):.3f}, box={b.xyxy[0].cpu().tolist()}")
    else:
        subtest_d_status = "BLOCKED"
        print(f"Only {live_people_count} person is physically present in front of the MacBook webcam.")
        print("Per strict instructions, synthetic composites or recorded media are NOT substituted.")
        print("SUBTEST D is BLOCKED until a second physical person is in the camera field of view.")

    print(f"SUBTEST D RESULT:       {subtest_d_status}")

    # =========================================================================
    # SUBTEST E: Output / Display Test (Annotated Visualization Artifact)
    # =========================================================================
    print("\n" + "-" * 70)
    print("SUBTEST E: Output / Display Test")
    print("-" * 70)

    if ret and live_frame is not None and len(boxes) > 0:
        annotated = results[0].plot()
        artifact_path = "/Users/gursimarsingh/.gemini/antigravity/brain/818dbb04-4de1-45cc-b17c-d6460706d571/live_detection_sample.jpg"
        cv2.imwrite(artifact_path, annotated)
        print(f"[OK] Rendered visual detection plot saved to: {artifact_path}")
        print(f"Annotated Frame Shape: {annotated.shape}, dtype: {annotated.dtype}")
        subtest_e_pass = os.path.isfile(artifact_path) and os.path.getsize(artifact_path) > 0
    else:
        subtest_e_pass = False

    print(f"SUBTEST E RESULT:       {'PASS' if subtest_e_pass else 'FAIL'}")

    cap.release()
    print("\n" + "=" * 70)
    print("SUMMARY OF SUBTESTS:")
    print(f"  Subtest A (Smoke Test >=100 frames): {subtest_a_pass}")
    print(f"  Subtest B (Live Person Detection):   {subtest_b_pass}")
    print(f"  Subtest C (No-Person Blank/Occluded):{subtest_c_pass}")
    print(f"  Subtest D (Real Multi-Person):       {subtest_d_status}")
    print(f"  Subtest E (Display Output Artifact): {subtest_e_pass}")
    print("=" * 70)

if __name__ == "__main__":
    main()
