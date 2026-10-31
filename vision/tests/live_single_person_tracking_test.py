"""Live single-person tracking test for 35 seconds on http://127.0.0.1:8080/video.

Measures track stability, frame count, detections, ID switches, and errors.
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
    print("LIVE WEBCAM SINGLE-PERSON TRACKING TEST (35 SECONDS)")
    print("=" * 70)

    cameras = multicamera._load_cameras()
    stream_url = cameras.get("CAM_01", "http://127.0.0.1:8080/video")
    model_path = os.environ.get("WAMSS_MODEL_PATH", "yolov8n.pt")
    conf_thresh = float(os.environ.get("WAMSS_CONF_THRESHOLD", 0.45))

    print(f"Connecting to stream: {stream_url}")
    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        print(f"[FAIL] Could not open camera stream at {stream_url}")
        sys.exit(1)

    print(f"Loading YOLO model '{model_path}' with ByteTrack...")
    model = YOLO(model_path)

    test_duration = 35.0  # Run for 35 seconds
    start_time = time.time()

    processed_frames = 0
    frames_with_detections = 0
    frames_with_track_id = 0
    read_errors = 0
    decode_errors = 0
    inference_errors = 0

    track_id_history = []
    id_switches = 0
    last_id = None
    confidence_history = []
    bbox_history = []
    inference_latencies = []

    print(f"\nStarting 35-second tracking test at {time.strftime('%H:%M:%S')}...")
    last_log_time = time.time()

    last_results = None
    last_frame = None

    while (time.time() - start_time) < test_duration:
        t0 = time.time()
        ret, frame = cap.read()
        if not ret:
            read_errors += 1
            time.sleep(0.02)
            continue
        if frame is None or getattr(frame, "size", 0) == 0:
            decode_errors += 1
            time.sleep(0.02)
            continue

        processed_frames += 1
        last_frame = frame

        t1 = time.time()
        try:
            results = model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                conf=conf_thresh,
                verbose=False,
            )
            t2 = time.time()
            inference_latencies.append((t2 - t1) * 1000)
            last_results = results
        except Exception as exc:
            inference_errors += 1
            print(f"[ERROR] Inference exception at frame {processed_frames}: {exc}")
            continue

        boxes = results[0].boxes
        if boxes is not None and len(boxes) > 0:
            frames_with_detections += 1
            conf = float(boxes.conf[0])
            confidence_history.append(conf)
            xyxy = [round(float(v), 1) for v in boxes.xyxy[0].cpu().tolist()]
            bbox_history.append(xyxy)

            if boxes.id is not None and len(boxes.id) > 0:
                frames_with_track_id += 1
                current_id = int(boxes.id[0].item())
                track_id_history.append(current_id)

                if last_id is not None and current_id != last_id:
                    id_switches += 1
                    print(f"  [ID SWITCH] Frame {processed_frames}: Track ID changed from {last_id} to {current_id} (conf={conf:.2f})")
                last_id = current_id
            else:
                track_id_history.append(None)
        else:
            track_id_history.append(None)

        # Periodic status log every 5 seconds
        if time.time() - last_log_time >= 5.0:
            elapsed = time.time() - start_time
            current_fps = processed_frames / elapsed if elapsed > 0 else 0
            print(f"  [{elapsed:4.1f}s] Frames: {processed_frames:4d} | Current Track ID: {last_id} | Ingestion FPS: {current_fps:4.1f}")
            last_log_time = time.time()

    total_elapsed = time.time() - start_time
    cap.release()

    # Save visual plot artifact
    if last_results is not None:
        annotated = last_results[0].plot()
        artifact_path = "/Users/gursimarsingh/.gemini/antigravity/brain/818dbb04-4de1-45cc-b17c-d6460706d571/live_single_person_tracking_35s.jpg"
        cv2.imwrite(artifact_path, annotated)
        print(f"\n[OK] Visual tracking artifact saved to: {artifact_path}")

    # Compute metrics
    valid_ids = [tid for tid in track_id_history if tid is not None]
    if valid_ids:
        from collections import Counter
        id_counts = Counter(valid_ids)
        dominant_id, dominant_count = id_counts.most_common(1)[0]
        stability_pct = (dominant_count / len(valid_ids)) * 100.0
    else:
        dominant_id, dominant_count, stability_pct = None, 0, 0.0

    avg_inf_ms = float(np.mean(inference_latencies)) if inference_latencies else 0.0
    inf_fps = (1000.0 / avg_inf_ms) if avg_inf_ms > 0 else 0.0
    pipeline_fps = processed_frames / total_elapsed if total_elapsed > 0 else 0.0

    print("\n" + "=" * 70)
    print("LIVE SINGLE-PERSON TRACKING RESULTS (35 SECONDS)")
    print("=" * 70)
    print(f"Total Elapsed Time:         {total_elapsed:.2f} seconds")
    print(f"Decoded & Processed Frames: {processed_frames}")
    print(f"Frames with Person Detected:{frames_with_detections} ({frames_with_detections/processed_frames*100:.1f}%)")
    print(f"Frames with Track ID:       {frames_with_track_id} ({frames_with_track_id/processed_frames*100:.1f}%)")
    print(f"Dominant Track ID:          {dominant_id}")
    print(f"Dominant Track Persistence: {dominant_count}/{len(valid_ids)} frames ({stability_pct:.1f}%)")
    print(f"Track ID Switches:          {id_switches}")
    print(f"All Unique Track IDs Seen:  {sorted(list(set(valid_ids)))}")
    print(f"Average Inference Latency:  {avg_inf_ms:.1f} ms (~{inf_fps:.1f} inference FPS)")
    print(f"End-to-End Pipeline FPS:    {pipeline_fps:.1f} FPS")
    print(f"Connection/Read Errors:     {read_errors}")
    print(f"Decode Errors:              {decode_errors}")
    print(f"Inference Errors:           {inference_errors}")
    if confidence_history:
        print(f"Confidence Range:           min={min(confidence_history):.3f}, max={max(confidence_history):.3f}, mean={np.mean(confidence_history):.3f}")
    if bbox_history:
        print(f"Sample BBoxes (xyxy):       {bbox_history[0]} -> {bbox_history[-1]}")
    print("=" * 70)

if __name__ == "__main__":
    main()
