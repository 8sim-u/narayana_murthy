# Face gallery (vision/faces/)

This directory holds the enrolled face gallery used by `vision/face_id.py`
for person identification on the NON-PRIVACY pipeline path. Face
recognition never runs in privacy mode.

## Enrolling

Run from `vision/` (or from the repo root as `python vision/enroll_face.py`):

    python enroll_face.py --photo /path/to/photo.jpg --name "Full Name"

The script detects the largest face in the photo and writes
`<sanitized_name>.json` here with the person's name, the embedding vector,
the backend used (`insightface` or `lbph`), and the enrollment timestamp.
Restart the vision pipeline afterwards so the new entry takes effect.

## File format

Each `<sanitized_name>.json`:

    {"name": "Full Name", "method": "insightface",
     "embedding": [...], "enrolled_at": "2026-11-02T07:00:00Z"}

- `method` must match the backend face_id.py selects at runtime; entries
  from the other backend are skipped.
- `enrolled_at` is UTC in ISO 8601.

## Privacy

No real face data ships with this repository. Only this README and the
`.gitkeep` in this directory are tracked; enrolled `*.json` files are
created on the edge node by the operator and are git-ignored. Treat them
as sensitive biometric data: keep the node's disk encrypted and do not
copy gallery files off the node.
