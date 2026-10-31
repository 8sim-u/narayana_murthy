"""Sustained end-to-end verification of the camera input pipeline."""
import os
import sys
import time
import numpy as np

# Ensure vision path is reachable
VISION_PATH = os.path.abspath("vision")
if VISION_PATH not in sys.path:
    sys.path.insert(0, VISION_PATH)

import multicamera
import cv2

print("=== Starting Sustained Live Camera Pipeline Verification ===")
cameras = multicamera._load_cameras()
print(f"Loaded cameras: {cameras}")

target_url = cameras.get("CAM_01")
assert target_url == "http://127.0.0.1:8080/video", f"Expected http://127.0.0.1:8080/video, got {target_url}"

cap = cv2.VideoCapture(target_url)
assert cap.isOpened(), "Failed to open camera stream"

frame_count = 0
prev_frame = None
differences = []
dimensions = None
start_time = time.time()
test_duration = 5.0  # 5 seconds sustained test

while time.time() - start_time < test_duration:
    success, frame = cap.read()
    if not success or frame is None or getattr(frame, "size", 0) == 0:
        print(f"Frame read failure at frame {frame_count}")
        break

    frame_count += 1
    if dimensions is None:
        dimensions = frame.shape

    if prev_frame is not None:
        diff = np.mean(np.abs(frame.astype(float) - prev_frame.astype(float)))
        differences.append(diff)
    prev_frame = frame.copy()

elapsed = time.time() - start_time
cap.release()

avg_fps = frame_count / elapsed if elapsed > 0 else 0
avg_diff = float(np.mean(differences)) if differences else 0.0

print(f"Sustained verification complete:")
print(f"  Status: {'SUCCESS' if frame_count > 10 else 'FAILURE'}")
print(f"  Elapsed Time: {elapsed:.2f}s")
print(f"  Frames Read: {frame_count}")
print(f"  Frame Dimensions: {dimensions}")
print(f"  Average FPS: {avg_fps:.1f}")
print(f"  Mean Absolute Inter-Frame Pixel Difference: {avg_diff:.3f}")
print(f"  Feed Active & Dynamically Changing: {avg_diff > 0.01}")
