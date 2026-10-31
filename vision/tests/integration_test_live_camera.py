"""Live integration test against the real MacBook webcam stream.

This test:
1. Connects to the real webcam stream at http://127.0.0.1:8080/video.
2. Decodes live frames using OpenCV.
3. Feeds each decoded frame directly into the actual vision-processing entry point:
   model.track(source=frame, persist=True, tracker='bytetrack.yaml', classes=[0], conf=0.45, verbose=False)
4. Measures total frames, frame dimensions, elapsed time, inter-frame differences, and errors.
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
    stream_url = "http://127.0.0.1:8080/video"
    print(f"[TEST] Target camera stream: {stream_url}")

    # 1. Open VideoCapture
    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        print(f"[FAIL] Unable to connect to {stream_url}")
        sys.exit(1)

    # 2. Load the project's actual vision model
    print("[TEST] Loading YOLO model (yolov8n.pt)...")
    model = YOLO("yolov8n.pt")

    print("[TEST] Starting live feed consumption and vision processing entry test...")
    start_time = time.time()
    test_duration = 5.0  # Run for 5 seconds

    frames_received = 0
    frames_processed_by_vision = 0
    errors = []
    dimensions = None
    prev_frame = None
    differences = []

    while time.time() - start_time < test_duration:
        success, frame = cap.read()

        if not success or frame is None or getattr(frame, "size", 0) == 0:
            errors.append(f"Read/decode failure at frame index {frames_received}")
            time.sleep(0.05)
            continue

        frames_received += 1
        if dimensions is None:
            dimensions = frame.shape

        if prev_frame is not None:
            diff = np.mean(np.abs(frame.astype(float) - prev_frame.astype(float)))
            differences.append(diff)
        prev_frame = frame.copy()

        # 3. Pass frame into the actual vision-processing entry point (vision/multicamera.py:374)
        try:
            results = model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],
                conf=0.45,
                verbose=False,
            )
            frames_processed_by_vision += 1
        except Exception as exc:
            errors.append(f"Vision entry point exception at frame {frames_received}: {exc}")

    elapsed_time = time.time() - start_time
    cap.release()

    avg_fps = frames_received / elapsed_time if elapsed_time > 0 else 0
    mean_diff = float(np.mean(differences)) if differences else 0.0

    print("=== LIVE INTEGRATION TEST RESULTS ===")
    print(f"Status: {'PASS' if (frames_received > 0 and len(errors) == 0) else 'FAIL'}")
    print(f"Frames Received & Decoded: {frames_received}")
    print(f"Frames Ingested by Vision Entry Point: {frames_processed_by_vision}")
    print(f"Elapsed Time: {elapsed_time:.2f} seconds")
    print(f"Average Ingestion FPS: {avg_fps:.1f}")
    print(f"Frame Dimensions (H, W, C): {dimensions}")
    print(f"Inter-frame Pixel Difference (Mean Abs): {mean_diff:.3f}")
    print(f"Stream Dynamic / Non-Static: {mean_diff > 0.01}")
    print(f"Connection or Decoding Errors: {len(errors)}")
    if errors:
        for err in errors[:5]:
            print(f"  Error detail: {err}")

if __name__ == "__main__":
    main()
