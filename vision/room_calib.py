"""2D room position mapping for the WAMSS edge node (Workstream 1: Vision).

map_position(bbox_xyxy, frame_w, frame_h, calib=None) -> (x, y)

Without calibration, positions are normalized frame coordinates:
  x = bottom-center x of the box, 0..1 across the frame width
  y = box height / frame height, 0..1 (taller box = closer = larger y,
      a simple depth proxy)

With a calibration loaded by load_calibration(), the bottom-center of the
box is mapped through a 4-point homography to real room coordinates in
meters, clamped to the room bounds.

The homography is solved in pure Python (no numpy dependency), so this
module imports cleanly anywhere.
"""

import json
import os


def _clamp(value, low, high):
    return max(low, min(high, value))


def _solve_linear(A, b):
    """Solve Ax = b for a square system via Gaussian elimination.

    Raises ValueError on a singular matrix (e.g. degenerate calibration
    points).
    """
    n = len(A)
    M = [list(row) + [bi] for row, bi in zip(A, b)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError(
                "degenerate calibration points (singular matrix)")
        M[col], M[piv] = M[piv], M[col]
        for r in range(col + 1, n):
            factor = M[r][col] / M[col][col]
            for c in range(col, n + 1):
                M[r][c] -= factor * M[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = M[r][n] - sum(M[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / M[r][r]
    return x


def _homography_from_points(src, dst):
    """Return the 3x3 homography H (h33 = 1) mapping src points to dst."""
    A = []
    b = []
    for (x, y), (X, Y) in zip(src, dst):
        A.append([-x, -y, -1, 0, 0, 0, x * X, y * X])
        b.append(-X)
        A.append([0, 0, 0, -x, -y, -1, x * Y, y * Y])
        b.append(-Y)
    h = _solve_linear(A, b)
    return [[h[0], h[1], h[2]],
            [h[3], h[4], h[5]],
            [h[6], h[7], 1.0]]


def _apply_homography(H, x, y):
    """Map one point through H; returns (X, Y)."""
    denom = H[2][0] * x + H[2][1] * y + H[2][2]
    if abs(denom) < 1e-12:
        raise ValueError("homography maps point to infinity")
    X = (H[0][0] * x + H[0][1] * y + H[0][2]) / denom
    Y = (H[1][0] * x + H[1][1] * y + H[1][2]) / denom
    return X, Y


def _validate_points(points, label):
    if (not isinstance(points, list) or len(points) != 4
            or any(not isinstance(p, (list, tuple)) or len(p) != 2
                   or not all(isinstance(v, (int, float)) for v in p)
                   for p in points)):
        raise ValueError(f"{label} must be a list of 4 [x, y] pairs")
    return [[float(p[0]), float(p[1])] for p in points]


def load_calibration(path):
    """Load a room calibration JSON file.

    Expected format:
      {"image_points": [[x, y], ... 4 items ...],
       "room_points":  [[mx, my], ... 4 items, same order ...],
       "room_size":    [width_m, height_m]}

    Returns {"H": 3x3 homography, "room_size": (w_m, h_m), "path": path}.
    Raises ValueError on malformed files or degenerate points.
    """
    if not os.path.isfile(path):
        raise ValueError(f"calibration file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("calibration file must contain a JSON object")
    image_points = _validate_points(data.get("image_points"), "image_points")
    room_points = _validate_points(data.get("room_points"), "room_points")
    room_size = data.get("room_size")
    if (not isinstance(room_size, (list, tuple)) or len(room_size) != 2
            or not all(isinstance(v, (int, float)) and v > 0
                       for v in room_size)):
        raise ValueError("room_size must be [width_m, height_m] of "
                         "positive numbers")
    H = _homography_from_points(image_points, room_points)
    return {"H": H,
            "room_size": (float(room_size[0]), float(room_size[1])),
            "path": path}


def save_calibration_template(path):
    """Write an empty calibration template with fill-in instructions."""
    template = {
        "instructions": (
            "Fill image_points with the pixel coordinates of 4 floor "
            "reference points as seen by this camera. The order must match "
            "room_points. Fill room_points with the same 4 points in meters "
            "on the room plan (origin at one corner, e.g. bottom-left). Set "
            "room_size to [width_m, height_m]. Then point WAMSS_ROOM_CALIB "
            "at this file. Load it with "
            "vision.room_calib.load_calibration()."
        ),
        "image_points": [[0, 0], [0, 0], [0, 0], [0, 0]],
        "room_points": [[0, 0], [0, 0], [0, 0], [0, 0]],
        "room_size": [10.0, 8.0],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(template, fh, indent=2)


def map_position(bbox_xyxy, frame_w, frame_h, calib=None):
    """Map a person bounding box to a 2D room position -> (x, y).

    bbox_xyxy is [x1, y1, x2, y2] in pixels. The anchor point is the
    bottom-center of the box (the person's feet on the floor plane).

    With calib (from load_calibration): returns (x_m, y_m) in meters,
    clamped to the room bounds. Without calib: returns normalized frame
    coordinates, x in 0..1 across the width and y in 0..1 where a taller
    box (closer person) gives a larger y.
    """
    x1, y1, x2, y2 = (float(v) for v in bbox_xyxy)
    px = (x1 + x2) / 2.0   # bottom-center x
    py = y2                # bottom-center y (feet)
    if calib is not None and calib.get("H") is not None:
        w_m, h_m = calib["room_size"]
        mx, my = _apply_homography(calib["H"], px, py)
        return (_clamp(mx, 0.0, w_m), _clamp(my, 0.0, h_m))
    x = _clamp(px / frame_w, 0.0, 1.0) if frame_w else 0.0
    y = _clamp((y2 - y1) / frame_h, 0.0, 1.0) if frame_h else 0.0
    return (x, y)
