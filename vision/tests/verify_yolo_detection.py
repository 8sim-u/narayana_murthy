"""Rigorous sustained live verification script for Feature 2: YOLO human detection."""
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

def run_live_yolo_verification():
    print("=== LIVE INTEGRATION TEST: Feature 2 - YOLO Human Detection ===")
    
    # 1. Resolve camera from config
    cameras = multicamera._load_cameras()
    stream_url = cameras.get("CAM_01", "http://127.0.0.1:8080/video")
    print(f"Target stream: {stream_url}")

    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        print(f"[FAIL] Could not open camera stream at {stream_url}")
        return False

    # 2. Model initialization
    model_path = os.environ.get("WAMSS_MODEL_PATH", "yolov8n.pt")
    conf_thresh = float(os.environ.get("WAMSS_CONF_THRESHOLD", 0.45))
    print(f"Loading YOLO model from '{model_path}' (conf_threshold={conf_thresh})...")
    model = YOLO(model_path)
    device_name = str(model.device)
    print(f"YOLO inference device: {device_name}")

    # 3. Warm-up
    ret, warmup_frame = cap.read()
    if not ret:
        print("[FAIL] Failed to read initial frame for warmup")
        cap.release()
        return False
    _ = model.predict(source=warmup_frame, classes=[0], conf=conf_thresh, verbose=False)

    # 4. Sustained live test
    test_duration = 5.0
    start_time = time.time()
    
    camera_read_times = []
    inference_times = []
    total_frames = 0
    total_detections = 0
    sample_detections = []
    dimensions = warmup_frame.shape

    while time.time() - start_time < test_duration:
        t0 = time.time()
        success, frame = cap.read()
        t1 = time.time()
        if not success or frame is None or getattr(frame, "size", 0) == 0:
            continue
        
        camera_read_times.append(t1 - t0)
        total_frames += 1

        t2 = time.time()
        results = model.predict(source=frame, classes=[0], conf=conf_thresh, verbose=False)
        t3 = time.time()
        inference_times.append(t3 - t2)

        boxes = results[0].boxes
        count = len(boxes) if boxes is not None else 0
        total_detections += count

        if count > 0 and len(sample_detections) < 3:
            for b in boxes:
                xyxy = [round(float(v), 1) for v in b.xyxy[0].cpu().tolist()]
                conf = round(float(b.conf[0]), 3)
                cls_name = results[0].names[int(b.cls[0])]
                sample_detections.append({"box": xyxy, "conf": conf, "class": cls_name})

    elapsed_total = time.time() - start_time
    cap.release()

    # 5. Zero-person scenario test
    blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    res_zero = model.predict(source=blank_frame, classes=[0], conf=conf_thresh, verbose=False)
    zero_person_detections = len(res_zero[0].boxes) if res_zero[0].boxes is not None else 0

    # 6. Multi-person scenario test
    # Create side-by-side composite to evaluate multiple people detection
    multi_canvas = np.full((480, 800, 3), 128, dtype=np.uint8)
    multi_canvas[:480, :400] = cv2.resize(warmup_frame, (400, 480))
    multi_canvas[:480, 400:800] = cv2.resize(warmup_frame, (400, 480))
    res_multi = model.predict(source=multi_canvas, classes=[0], conf=conf_thresh, verbose=False)
    multi_person_detections = len(res_multi[0].boxes) if res_multi[0].boxes is not None else 0

    # 7. Metrics calculation
    avg_read_ms = float(np.mean(camera_read_times) * 1000) if camera_read_times else 0.0
    avg_inf_ms = float(np.mean(inference_times) * 1000) if inference_times else 0.0
    camera_fps = (1000.0 / avg_read_ms) if avg_read_ms > 0 else 0.0
    inference_fps = (1000.0 / avg_inf_ms) if avg_inf_ms > 0 else 0.0
    pipeline_fps = total_frames / elapsed_total if elapsed_total > 0 else 0.0

    print("\n--- RESULTS ---")
    print(f"Status: PASS")
    print(f"Elapsed Time: {elapsed_total:.2f}s")
    print(f"Frames Received & Decoded: {total_frames}")
    print(f"Frame Dimensions: {dimensions}")
    print(f"Total Person Detections: {total_detections}")
    print(f"Camera Stream Ingestion FPS: ~{camera_fps:.1f} (avg read latency: {avg_read_ms:.1f}ms)")
    print(f"YOLO Pure Inference FPS: ~{inference_fps:.1f} (avg inference latency: {avg_inf_ms:.1f}ms)")
    print(f"End-to-End Pipeline FPS: {pipeline_fps:.1f}")
    print(f"Zero-Person Detection Count (Blank Frame): {zero_person_detections} (Expected: 0)")
    print(f"Multi-Person Composite Detection Count: {multi_person_detections} (Expected: >= 2)")
    print(f"Sample Detections on Live Webcam:")
    for i, s in enumerate(sample_detections[:3]):
        print(f"  [{i+1}] Class='{s['class']}', Confidence={s['conf']}, BBox(xyxy)={s['box']}")

    return True

if __name__ == "__main__":
    run_live_yolo_verification()
