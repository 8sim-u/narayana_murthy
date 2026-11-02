#!/usr/bin/env python3
"""Enroll a face into the WAMSS vision gallery.

Usage:
    python enroll_face.py --photo /path/to/photo.jpg --name "Full Name"

Detects the largest face in the photo, computes its embedding with the
same backend that face_id.py uses at runtime (insightface when available,
otherwise the OpenCV LBPH fallback), and saves it to
vision/faces/<sanitized_name>.json.

Run from the vision/ directory, or from the repo root as
`python vision/enroll_face.py`. Restart the vision pipeline afterwards so
the new face takes effect.

Face recognition never runs in privacy mode; enrollment is a non-privacy
operation by design.
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import cv2
except ImportError:
    cv2 = None

import face_id


def sanitize_name(name):
    """Make a filesystem-safe gallery filename stem from a display name."""
    safe = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return safe or "unnamed"


def main():
    parser = argparse.ArgumentParser(
        description="Enroll a face into the WAMSS vision gallery.")
    parser.add_argument("--photo", required=True,
                        help="Path to a photo containing the person's face")
    parser.add_argument("--name", required=True,
                        help='Full name, e.g. "Jane Doe"')
    args = parser.parse_args()

    if cv2 is None:
        print("FAILED: OpenCV is not installed, cannot read the photo.")
        return 1
    if not os.path.isfile(args.photo):
        print(f"FAILED: photo not found: {args.photo}")
        return 1
    image = cv2.imread(args.photo)
    if image is None:
        print(f"FAILED: could not decode image: {args.photo}")
        return 1

    backend = face_id.backend_name()
    if backend == "none":
        print("FAILED: no face-recognition backend is available. Install "
              "insightface, or opencv-contrib-python for the LBPH fallback.")
        return 1

    result = face_id.extract_embedding(image)
    if result is None:
        print("FAILED: no face detected in the photo. Use a clear, "
              "front-facing photo and try again.")
        return 1
    method, embedding = result

    os.makedirs(face_id.GALLERY_DIR, exist_ok=True)
    filename = sanitize_name(args.name) + ".json"
    path = os.path.join(face_id.GALLERY_DIR, filename)
    existed = os.path.isfile(path)
    entry = {
        "name": args.name.strip(),
        "method": method,
        "embedding": embedding,
        "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entry, fh)
    face_id.reload_gallery()

    action = "Updated" if existed else "Enrolled"
    print(f"OK: {action} '{entry['name']}' -> {path} "
          f"(backend: {method}). Restart the vision pipeline for the new "
          f"face to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
