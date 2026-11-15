"""Face recognition for the WAMSS edge node (Workstream 1: Vision).

Matching requires a non-empty gallery explicitly supplied by the caller. In
production, this must be the backend-filtered gallery for the active camera;
omitting it always returns "unknown".

Backend priority, chosen lazily on first use:
  1. insightface (FaceAnalysis, buffalo_l pack) when importable and its
     models load. Gallery embeddings are compared with cosine similarity.
  2. OpenCV LBPH face recognizer when cv2.face is available (this lives in
     opencv-contrib-python, not in opencv-python). Gallery entries store a
     grayscale face patch for training.
  3. No backend: identify() always returns "unknown".

Everything degrades gracefully: all imports and model loads are wrapped in
try/except, identify() never raises, and this module imports cleanly even
when OpenCV itself is missing. The pipeline calls identify() only on the
non-privacy path; privacy mode never imports this module (see the privacy
guarantee in vision/multicamera.py).

Legacy gallery layout (vision/faces/):
  <sanitized_name>.json -> {"name": ..., "method": "insightface"|"lbph",
                             "embedding": [...], "enrolled_at": ...}
Local sidecars are not an authorization source. Vision enrollment forwards
photos to the backend, and the active camera loop passes only the fresh,
camera-scoped consent gallery to the matcher.
"""

import json
import os

GALLERY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faces")

try:
    import cv2
except ImportError:
    cv2 = None

# Lazy backend state. Never load models at import time: importing this
# module must be cheap and must never fail.
_backend = "pending"   # "insightface" | "lbph" | "none"
_backend_ready = False
_face_app = None       # insightface FaceAnalysis instance
_lbph_rec = None       # cv2 LBPH recognizer
_lbph_labels = {}      # int label -> enrolled name
_lbph_filtered_cache = {}  # consent-filtered name set -> (model, labels)
_lbph_size = (64, 64)  # width, height of the stored grayscale face patch
_gallery = None        # cached list of gallery entry dicts


def _cosine(a, b):
    """Cosine similarity of two equal-length vectors (pure Python)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _load_gallery():
    """Read and validate every JSON sidecar in the gallery dir (cached)."""
    global _gallery
    if _gallery is not None:
        return _gallery
    _gallery = []
    try:
        files = sorted(os.listdir(GALLERY_DIR))
    except OSError:
        return _gallery
    for fname in files:
        if not fname.endswith(".json"):
            continue
        path = os.path.join(GALLERY_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                entry = json.load(fh)
            if (isinstance(entry, dict) and entry.get("name")
                    and isinstance(entry.get("embedding"), list)):
                _gallery.append(entry)
            else:
                print(f"[face_id] WARNING: skipping malformed gallery "
                      f"file {fname}")
        except Exception as exc:
            print(f"[face_id] WARNING: could not read gallery file "
                  f"{fname}: {exc}")
    return _gallery


def reload_gallery():
    """Drop the cached gallery (and retrain LBPH) after a new enrollment."""
    global _gallery
    _gallery = None
    _lbph_filtered_cache.clear()
    if _backend == "lbph" and _lbph_rec is not None:
        try:
            _train_lbph()
        except Exception as exc:
            print(f"[face_id] WARNING: LBPH retrain failed: {exc}")


def _train_lbph():
    """Train the LBPH recognizer from gallery patches of method "lbph"."""
    global _lbph_labels
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("numpy is missing")
    entries = [e for e in _load_gallery()
               if e.get("method") == "lbph" and e.get("embedding")]
    if not entries:
        raise RuntimeError("no LBPH gallery entries to train on")
    images = []
    labels = []
    _lbph_labels = {}
    for i, entry in enumerate(entries):
        patch = np.array(entry["embedding"], dtype="uint8").reshape(
            _lbph_size[1], _lbph_size[0])
        images.append(patch)
        labels.append(i)
        _lbph_labels[i] = entry["name"]
    _lbph_rec.train(images, np.array(labels))


def _largest_face_patch(image_bgr):
    """Largest detected face as a 64x64 grayscale patch, or None.

    Uses the Haar cascade shipped with opencv-python, so this works without
    insightface. Returns None when no face is found.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    cascade_path = (cv2.data.haarcascades
                    + "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(cascade_path)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
    face = gray[y:y + h, x:x + w]
    return cv2.resize(face, _lbph_size)


def _init_backend():
    """Pick the best available backend, once. Never raises."""
    global _backend, _backend_ready, _face_app, _lbph_rec
    if _backend_ready:
        return
    _backend_ready = True

    # 1. insightface (preferred: real face embeddings).
    try:
        from insightface.app import FaceAnalysis
        # ctx_id -1 = CPU, 0 = first GPU. CPU is the safe edge default.
        ctx = int(os.environ.get("WAMSS_FACE_CTX", "-1"))
        app = FaceAnalysis(name="buffalo_l")
        app.prepare(ctx_id=ctx)
        _face_app = app
        _backend = "insightface"
        print("[face_id] backend: insightface")
        return
    except Exception as exc:
        print(f"[face_id] insightface unavailable ({exc}); trying LBPH")

    # 2. OpenCV LBPH fallback (needs opencv-contrib-python for cv2.face).
    try:
        if cv2 is None or not hasattr(cv2, "face"):
            raise RuntimeError(
                "cv2.face is missing (install opencv-contrib-python)")
        # Do not pre-train against an unrestricted gallery. The camera loop
        # supplies its current consent-filtered entries to identify().
        _lbph_rec = cv2.face.LBPHFaceRecognizer_create()
        _backend = "lbph"
        print("[face_id] backend: LBPH (opencv-contrib; consent-filtered at match time)")
    except Exception as exc:
        print(f"[face_id] LBPH unavailable ({exc}); face recognition is "
              f"disabled, identify() will always return \"unknown\"")
        _backend = "none"


def backend_name():
    """Return the active backend: "insightface", "lbph", or "none"."""
    _init_backend()
    return _backend


def extract_embedding(image_bgr):
    """Embedding of the largest face in a BGR image, or None.

    Returns (method, embedding) where embedding is a list of floats: a
    normalized ArcFace vector for insightface, or a flattened 64x64
    grayscale patch (0-255) for the LBPH fallback. Used by enroll_face.py.
    None means no backend, no face found, or an unreadable image.
    """
    try:
        _init_backend()
        if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
            return None
        if _backend == "insightface":
            faces = _face_app.get(image_bgr)
            if not faces:
                return None
            best = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0])
                       * (f.bbox[3] - f.bbox[1]))
            return ("insightface",
                    [float(v) for v in best.normed_embedding])
        if _backend == "lbph":
            patch = _largest_face_patch(image_bgr)
            if patch is None:
                return None
            return ("lbph", [int(v) for v in patch.flatten().tolist()])
        return None
    except Exception as exc:
        print(f"[face_id] embedding extraction failed: {exc}")
        return None


def _identify_insightface_scored(crop_bgr, gallery_entries=None):
    """(name, confidence) via insightface, or ("unknown", None).

    Confidence is the cosine similarity of the best gallery match
    (0..1 for a normalized embedding at or above the match threshold).
    """
    if not gallery_entries:
        return "unknown", None
    faces = _face_app.get(crop_bgr)
    if not faces:
        return "unknown", None
    best = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0])
               * (f.bbox[3] - f.bbox[1]))
    emb = [float(v) for v in best.normed_embedding]
    source = gallery_entries or []
    entries = [e for e in source
               if e.get("method") == "insightface" and e.get("embedding")]
    if not entries:
        return "unknown", None
    threshold = float(os.environ.get("WAMSS_FACE_SIM_THRESHOLD", "0.5"))
    score, name = max(
        ((_cosine(emb, e["embedding"]), e["name"]) for e in entries),
        key=lambda s: s[0])
    if score < threshold:
        return "unknown", None
    return name, float(max(0.0, min(1.0, score)))


def _filtered_lbph_model(entries):
    """Train/cache an LBPH recognizer using only consent-eligible entries."""
    try:
        import numpy as np
        if cv2 is None or not hasattr(cv2, "face"):
            return None, {}
        usable = [e for e in entries
                  if e.get("method") == "lbph" and e.get("embedding")]
        key = tuple(sorted(
            (e["name"], hash(tuple(e["embedding"]))) for e in usable))
        if not usable:
            return None, {}
        cached = _lbph_filtered_cache.get(key)
        if cached is not None:
            return cached
        model = cv2.face.LBPHFaceRecognizer_create()
        images = [np.array(e["embedding"], dtype="uint8").reshape(
            _lbph_size[1], _lbph_size[0]) for e in usable]
        labels = list(range(len(usable)))
        label_names = {label: entry["name"]
                       for label, entry in zip(labels, usable)}
        model.train(images, np.array(labels))
        _lbph_filtered_cache[key] = (model, label_names)
        return model, label_names
    except Exception:
        return None, {}


def _identify_lbph_scored(crop_bgr, gallery_entries=None):
    """(name, confidence) via LBPH, or ("unknown", None).

    LBPH reports a distance (smaller is a better match), so confidence is
    mapped linearly from the match threshold: 1.0 at distance 0, 0.0 at
    the threshold, clamped to 0..1.
    """
    if not gallery_entries:
        return "unknown", None
    patch = _largest_face_patch(crop_bgr)
    if patch is None:
        return "unknown", None
    model, labels = _filtered_lbph_model(gallery_entries)
    if model is None:
        return "unknown", None
    label, distance = model.predict(patch)
    threshold = float(os.environ.get("WAMSS_LBPH_THRESHOLD", "70"))
    name = labels.get(int(label))
    if name is None or distance > threshold:
        return "unknown", None
    if threshold <= 0:
        return name, 0.0
    confidence = 1.0 - float(distance) / threshold
    return name, float(max(0.0, min(1.0, confidence)))


def identify(crop_bgr, gallery_entries=None):
    """Return the enrolled person name for a BGR person crop, or "unknown".

    Never raises: missing backends, an empty gallery, no detectable face,
    or a bad crop all yield "unknown", so the pipeline keeps running.
    """
    name, _confidence = identify_with_confidence(crop_bgr, gallery_entries)
    return name


def identify_with_confidence(crop_bgr, gallery_entries=None):
    """Return (name, confidence) for a BGR person crop.

    Confidence is a float in 0..1 when a face matched an enrolled person,
    and None when no face matched (or no backend / no face was available).
    Never raises: every failure path yields ("unknown", None).
    """
    try:
        _init_backend()
        if crop_bgr is None or getattr(crop_bgr, "size", 0) == 0:
            return "unknown", None
        if not gallery_entries:
            return "unknown", None
        if _backend == "insightface":
            return _identify_insightface_scored(crop_bgr, gallery_entries)
        if _backend == "lbph":
            return _identify_lbph_scored(crop_bgr, gallery_entries)
        return "unknown", None
    except Exception:
        return "unknown", None
