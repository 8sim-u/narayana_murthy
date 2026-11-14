# Room Map Calibration

The Room Map tab shows where each detected person is, per camera. For that
to mean anything in the real world, the detector must translate image pixels
into room coordinates. This page explains the two position modes and how to
calibrate a camera to meters.

Note: `vision/room_calib.py` provides the calibration helper described
below. If it is not in your checkout yet, pull the latest `main` before
proceeding; the rest of this guide assumes it is present.

## Normalized vs calibrated positions

Every event carries `position_x` and `position_y`. They mean different
things depending on calibration:

- **Uncalibrated (default):** positions are **normalized 0..1**, i.e.
  fractions of the image. `(0.5, 0.5)` is the center of the frame, `(0, 0)`
  the top-left corner. This tells you where someone is *in the picture*,
  not where they are *in the room*.
- **Calibrated:** positions are **meters** in a room coordinate system you
  define. `(2.5, 1.0)` means 2.5 meters along your X axis and 1.0 meter along
  your Y axis from the origin you chose (for example, a room corner).

Calibration is per camera node: each camera sees a different part of the
room, so each calibrated node needs its own calibration file.

## How 4-point homography works

You pick **4 points on the floor that are visible in the camera image** and
measure **where those same 4 points are in the real room, in meters**.
From those 4 correspondences the helper computes a homography matrix that
maps any image pixel to a room-meter coordinate (assuming people are on the
floor plane, which is true for foot positions).

Steps:

1. **Choose 4 floor points.** Good choices are floor tile corners, the
   corners of a rug, or marks you make with tape. They must all be visible
   in the camera frame and lie on the floor.
2. **Measure their real positions.** Pick an origin (e.g. the corner of the
   room nearest the camera) and two axes along the walls. Measure each
   point's `(x, y)` in meters with a tape measure.
3. **Read their pixel positions locally.** Use the detector's local preview or
   an image captured and authorized on the edge node; the server snapshot route
   is disabled because it exposes person-level data. Note each point's `(px,
   py)` pixel coordinates. Pixel `(0, 0)` is the top-left of the image.
4. **Write the calibration JSON.** `vision/room_calib.py` has
   `save_calibration_template()`, which writes a template JSON with the
   right shape. Fill in your measured values:

   ```bash
   python -c "from vision.room_calib import save_calibration_template; save_calibration_template('vision/calib_CAM_01.json')"
   ```

   Then edit `vision/calib_CAM_01.json` to look like this (example values):

   ```json
   {
     "camera_id": "CAM_01",
     "image_points": [[320, 700], [960, 700], [1100, 950], [180, 950]],
     "world_points": [[1.0, 0.5], [4.0, 0.5], [4.0, 3.5], [1.0, 3.5]],
     "unit": "meters"
   }
   ```

   `image_points` are the 4 pixel coordinates from step 3, in the same
   order as `world_points`, the 4 measured meter coordinates from step 2.

## Where to put the calibration file

Save the file as `vision/calib_<CAMERA_ID>.json` on the **edge node** that
runs that camera (e.g. `vision/calib_CAM_01.json` for node `CAM_01`). The
detector loads it at startup and switches that camera's positions to
meters. A camera with no calibration file keeps reporting 0..1 normalized
positions; mixing calibrated and uncalibrated cameras is allowed, but their
positions are not comparable on the same map.

Restart the detector process after adding or editing the file.

## Verifying calibration locally

1. Use the detector's local preview and a known spot in the room (e.g. exactly
   2 meters from the origin corner along the X wall).
2. Check the locally reported coordinate is near `(2.0, <your Y>)`.
3. If the point is mirrored or rotated, swap or negate the world coordinates
   in the JSON and retry. If positions are wildly off, re-measure the four
   calibration points.

The unscoped Room Map, `/positions`, and snapshot reads remain disabled. The
Issue 19 manager token authorizes only roster-scoped wellness check-in prompts;
it does not grant access to these broader records. For a no-walk sanity check,
inspect local detector logs for plausible meter values (e.g.
`0..6`) instead of normalized `0..1` fractions.

## Tips

- **Cover the floor area.** Spread the 4 points across as much of the
  visible floor as possible. Points clustered in one corner give a
  homography that is accurate there and garbage everywhere else.
- **Avoid collinear points.** No three points on a straight line, and do
  not use the four corners of a line segment. A rough rectangle or
  trapezoid on the floor is ideal.
- **Keep points on the floor plane.** Points on walls, tables, or doors
  break the floor-plane assumption and skew the mapping.
- **Re-calibrate if the camera moves.** Any bump, pan, tilt, or zoom
  invalidates the pixel correspondences. Re-do steps 3-4 (the meter
  measurements in step 2 stay valid if your tape marks are still on the
  floor).
- **One file per camera.** A hallway camera and a room camera need separate
  files with separate point sets, even if they are in the same building.
