#!/usr/bin/env python3
"""Submit a face enrollment to the backend's per-camera consent gate.

Usage:
    python vision/enroll_face.py --photo /path/to/photo.jpg \
        --name "Full Name" --camera-id CAM_01

This client never computes or stores a local face embedding. The backend
accepts the image only when the employee has active, explicit consent for the
specified identity-mode camera.
"""

import argparse
import os
import sys

import requests


def _backend_base(value):
    raw = (value or "http://localhost:8000").strip().rstrip("/")
    if raw.endswith("/event"):
        raw = raw[:-len("/event")]
    return raw


def main():
    parser = argparse.ArgumentParser(
        description="Enroll a face through the consent-gated backend API.")
    parser.add_argument("--photo", required=True,
                        help="Path to an authorized photo containing the employee's face")
    parser.add_argument("--name", required=True,
                        help="Employee display name configured in the identity map")
    parser.add_argument("--camera-id", required=True,
                        help="Camera ID for which active identity consent is recorded")
    parser.add_argument("--backend-url", default=os.environ.get("WAMSS_BACKEND_URL", ""),
                        help="Backend base URL or /event URL; defaults to WAMSS_BACKEND_URL")
    args = parser.parse_args()

    if not os.path.isfile(args.photo):
        print(f"FAILED: photo not found: {args.photo}")
        return 1

    headers = {}
    api_key = os.environ.get("WAMSS_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key

    try:
        with open(args.photo, "rb") as photo:
            response = requests.post(
                _backend_base(args.backend_url) + "/faces/enroll",
                data={"name": args.name, "camera_id": args.camera_id},
                files={"photo": (os.path.basename(args.photo), photo, "image/jpeg")},
                headers=headers,
                timeout=30.0,
            )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 403:
            print("FAILED: active employee consent for this identity camera is required.")
        else:
            print(f"FAILED: backend enrollment request failed ({type(exc).__name__}).")
        return 1
    except (OSError, ValueError) as exc:
        print(f"FAILED: could not submit enrollment ({type(exc).__name__}).")
        return 1

    print(f"OK: {result.get('status', 'submitted')} enrollment for camera {args.camera_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
