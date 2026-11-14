"""
Dataset capture utility for WAMSS real-world vision evaluation.

Captures pristine camera frames from the live stream at configurable intervals,
filters out near-identical duplicate frames, creates paired annotation placeholders,
and generates a structured session manifest.
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("dataset_capture")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class DatasetCaptureConfig:
    def __init__(
        self,
        stream_url: str = "http://127.0.0.1:8080/video",
        camera_id: str = "CAM_01",
        session_id: str = "session_01",
        split: str = "eval",
        output_dir: Path | str = "dataset",
        interval_sec: float = 1.0,
        max_duration_sec: Optional[float] = None,
        max_frames: Optional[int] = None,
        duplicate_motion_threshold: float = 0.015,
        jpeg_quality: int = 95,
        scenario_description: str = "",
        max_reconnect_attempts: int = 3,
    ):
        self.stream_url = stream_url
        self.camera_id = camera_id
        self.session_id = session_id
        self.split = split
        self.output_dir = Path(output_dir)
        self.interval_sec = interval_sec
        self.max_duration_sec = max_duration_sec
        self.max_frames = max_frames
        self.duplicate_motion_threshold = duplicate_motion_threshold
        self.jpeg_quality = jpeg_quality
        self.scenario_description = scenario_description
        self.max_reconnect_attempts = max_reconnect_attempts


class FrameDifferenceDetector:
    """Detects near-identical frames to avoid saving redundant static images."""

    def __init__(self, min_motion_threshold: float = 0.015):
        self.min_motion_threshold = min_motion_threshold
        self.last_frame_gray: Optional[np.ndarray] = None

    def compute_motion_score(self, frame_bgr: np.ndarray) -> float:
        """Returns normalized mean absolute pixel difference in [0.0, 1.0]."""
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if self.last_frame_gray is None:
            self.last_frame_gray = gray
            return 1.0  # First frame is always considered distinct

        diff = cv2.absdiff(gray, self.last_frame_gray)
        motion_score = float(np.mean(diff)) / 255.0
        return motion_score

    def update_reference(self, frame_bgr: np.ndarray) -> None:
        """Updates reference frame after saving."""
        self.last_frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)


class DatasetCapture:
    """Manages stream capture, frame saving, and manifest generation."""

    def __init__(self, config: DatasetCaptureConfig):
        self.config = config
        self.stop_event = threading.Event()
        self.motion_detector = FrameDifferenceDetector(
            min_motion_threshold=config.duplicate_motion_threshold
        )
        self.saved_frames: List[Dict[str, Any]] = []
        self.skipped_duplicates = 0

        # Destination directories
        split_dir = self.config.output_dir / self.config.split
        self.images_dir = split_dir / "images"
        self.labels_dir = split_dir / "labels"
        self.manifest_path = split_dir / f"manifest_{self.config.session_id}.json"

        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.labels_dir.mkdir(parents=True, exist_ok=True)

    def request_stop(self) -> None:
        """Signals capture loop to terminate safely."""
        self.stop_event.set()

    def _open_capture(self) -> cv2.VideoCapture:
        """Attempts to open camera capture with timeout and buffer configuration."""
        cap = cv2.VideoCapture(self.config.stream_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def run(self) -> int:
        """Executes the capture loop. Returns count of saved frames."""
        logger.info(
            f"Starting dataset capture: session='{self.config.session_id}', "
            f"camera='{self.config.camera_id}', stream='{self.config.stream_url}', "
            f"split='{self.config.split}'"
        )

        cap = self._open_capture()
        if not cap.isOpened():
            logger.error(f"Failed to open video source: {self.config.stream_url}")
            return 0

        start_time = time.time()
        last_saved_time = 0.0
        frame_idx = 0
        consecutive_read_failures = 0
        total_reconnects = 0

        try:
            while not self.stop_event.is_set():
                now = time.time()
                elapsed = now - start_time

                if self.config.max_duration_sec and elapsed >= self.config.max_duration_sec:
                    logger.info(f"Reached max duration of {self.config.max_duration_sec}s.")
                    break

                if self.config.max_frames and len(self.saved_frames) >= self.config.max_frames:
                    logger.info(f"Reached max frame count of {self.config.max_frames}.")
                    break

                try:
                    ok, frame = cap.read()
                except (StopIteration, GeneratorExit):
                    break
                if not ok or frame is None:
                    consecutive_read_failures += 1
                    if consecutive_read_failures >= 10:
                        total_reconnects += 1
                        if total_reconnects > self.config.max_reconnect_attempts:
                            logger.warning("Max reconnection attempts reached. Terminating capture.")
                            break
                        logger.warning("Stream disconnected. Attempting reconnection...")
                        cap.release()
                        time.sleep(1.0)
                        cap = self._open_capture()
                        consecutive_read_failures = 0
                    else:
                        time.sleep(0.01)
                    continue

                consecutive_read_failures = 0

                # Rate limit frame capture by interval
                if self.config.interval_sec > 0 and (now - last_saved_time < self.config.interval_sec):
                    time.sleep(0.005)
                    continue

                # Check for near-identical duplicate frames
                motion_score = self.motion_detector.compute_motion_score(frame)
                if (
                    len(self.saved_frames) > 0
                    and motion_score < self.config.duplicate_motion_threshold
                ):
                    self.skipped_duplicates += 1
                    last_saved_time = now
                    continue

                # Save pristine frame
                frame_idx += 1
                base_name = f"{self.config.camera_id}_{self.config.session_id}_{frame_idx:04d}"
                image_filename = f"{base_name}.jpg"
                label_filename = f"{base_name}.txt"

                image_path = self.images_dir / image_filename
                label_path = self.labels_dir / label_filename

                h, w, c = frame.shape
                cv2.imwrite(
                    str(image_path),
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, self.config.jpeg_quality],
                )

                # Create empty ground-truth label file if not existing
                if not label_path.exists():
                    label_path.touch()

                self.motion_detector.update_reference(frame)
                last_saved_time = now

                # Calculate SHA-256
                with open(image_path, "rb") as f:
                    file_sha256 = hashlib.sha256(f.read()).hexdigest()

                meta_entry = {
                    "frame_index": frame_idx,
                    "filename": image_filename,
                    "image_path": str(image_path.relative_to(self.config.output_dir)),
                    "label_path": str(label_path.relative_to(self.config.output_dir)),
                    "timestamp": round(now, 4),
                    "iso_timestamp": datetime.now(timezone.utc).isoformat(),
                    "width": w,
                    "height": h,
                    "channels": c,
                    "motion_score": round(motion_score, 4),
                    "sha256": file_sha256,
                }
                self.saved_frames.append(meta_entry)
                logger.info(
                    f"Saved [{len(self.saved_frames)}/{self.config.max_frames or '∞'}] "
                    f"{image_filename} ({w}x{h}, motion={motion_score:.3f})"
                )

        except KeyboardInterrupt:
            logger.info("Capture interrupted by user.")
        finally:
            cap.release()
            self._write_manifest(start_time, time.time())

        logger.info(
            f"Capture session '{self.config.session_id}' finished. "
            f"Saved {len(self.saved_frames)} frames, skipped {self.skipped_duplicates} duplicates."
        )
        return len(self.saved_frames)

    def _write_manifest(self, start_time: float, end_time: float) -> None:
        """Saves session metadata manifest to JSON."""
        manifest_data = {
            "session_id": self.config.session_id,
            "camera_id": self.config.camera_id,
            "split": self.config.split,
            "stream_url": self.config.stream_url,
            "start_iso": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
            "end_iso": datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat(),
            "duration_seconds": round(end_time - start_time, 2),
            "interval_seconds": self.config.interval_sec,
            "frames_saved": len(self.saved_frames),
            "duplicates_skipped": self.skipped_duplicates,
            "scenario_description": self.config.scenario_description,
            "frames": self.saved_frames,
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)
        logger.info(f"Manifest written to: {self.manifest_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture evaluation frames from WAMSS camera feed.")
    parser.add_argument(
        "--source",
        default=os.getenv("WAMSS_CAMERA_URL", "http://127.0.0.1:8080/video"),
        help="Camera stream URL or device index (default: http://127.0.0.1:8080/video)",
    )
    parser.add_argument("--camera-id", default="CAM_01", help="Camera identifier (default: CAM_01)")
    parser.add_argument(
        "--session",
        required=True,
        help="Session identifier (e.g. sitting_01, standing_01, empty_01)",
    )
    parser.add_argument(
        "--split",
        choices=["dev", "eval"],
        default="eval",
        help="Target dataset split (default: eval)",
    )
    parser.add_argument(
        "--output-dir",
        default="dataset",
        help="Base directory for dataset storage (default: dataset)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Capture interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Maximum capture duration in seconds (optional)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Maximum frame count to capture (optional)",
    )
    parser.add_argument(
        "--motion-threshold",
        type=float,
        default=0.015,
        help="Minimum normalized motion difference required to save frame (default: 0.015)",
    )
    parser.add_argument(
        "--scenario",
        default="",
        help="Short description of scenario (e.g. 'one person typing at desk')",
    )

    args = parser.parse_args()

    config = DatasetCaptureConfig(
        stream_url=args.source,
        camera_id=args.camera_id,
        session_id=args.session,
        split=args.split,
        output_dir=args.output_dir,
        interval_sec=args.interval,
        max_duration_sec=args.duration,
        max_frames=args.max_frames,
        duplicate_motion_threshold=args.motion_threshold,
        scenario_description=args.scenario,
    )

    capture = DatasetCapture(config)

    def sig_handler(signum, frame):
        logger.info("Signal caught, initiating graceful shutdown...")
        capture.request_stop()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    capture.run()


if __name__ == "__main__":
    main()
