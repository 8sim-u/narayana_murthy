"""Per-track identity cache with majority voting (Workstream 1: Vision).

Problem: ByteTrack gives a person a temporary track id ("Person N") that
only lives while they stay in view. When they leave and return under a new
track id, the pipeline used to start from zero and call face recognition
once, for the first person only, with no memory and no confidence.

This cache sits between the tracker and the event sender:

- Long-term memory is the enrolled gallery in vision/faces/, queried
  through face_id on first sight of a track. The cache does NOT remember
  faces itself; it remembers, per track id, the recent face-match votes
  for the person currently carrying that id.
- Short-term smoothing is majority voting over those votes, so one bad
  frame cannot flip a name, and an unknown track cannot flicker to a
  wrong name on a single stray match.
- Re-verification is periodic, not per frame: identify on first sight,
  then again every IDENTITY_RECHECK_FRAMES frames or
  IDENTITY_RECHECK_SECONDS seconds per track, whichever comes first.
  insightface on CPU is slow, so the per-frame cost stays at zero.

Voting rule (see IdentityCache.identity):
- A name is assigned only when it holds a strict majority of the votes
  in the window, has at least IDENTITY_MIN_VOTES votes, and its mean
  match confidence is at least IDENTITY_MIN_CONFIDENCE. Fewer agreeing
  votes, a tie, or weak confidence all leave the track "unknown".
- Once named, a track keeps its name until another name wins a strict
  majority. Ties and unknown votes never flip a name, so a named person
  whose face is briefly occluded does not flicker back to "unknown".

The cache never touches cameras, models, or disk, and it imports nothing
heavy, so it is unit-testable with a stub identify function. Privacy mode
never constructs it (see the privacy guarantee in multicamera.py).
"""

import os
from collections import deque


def _parse_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Identify again after this many frames on the same track, or after this
# many seconds, whichever comes first. Defaults: ~1 s at 30 fps, or 5 s
# at any frame rate.
IDENTITY_RECHECK_FRAMES = _parse_int(
    os.environ.get("WAMSS_IDENTITY_RECHECK_FRAMES"), 30)
IDENTITY_RECHECK_SECONDS = _parse_float(
    os.environ.get("WAMSS_IDENTITY_RECHECK_SECONDS"), 5.0)

# How many recent votes per track are kept for the majority vote.
IDENTITY_VOTE_WINDOW = _parse_int(
    os.environ.get("WAMSS_IDENTITY_VOTE_WINDOW"), 7)

# A name needs at least this many agreeing votes in the window before it
# is assigned to a track.
IDENTITY_MIN_VOTES = _parse_int(
    os.environ.get("WAMSS_IDENTITY_MIN_VOTES"), 2)

# Mean match confidence (0..1) the winning name's votes must reach.
IDENTITY_MIN_CONFIDENCE = _parse_float(
    os.environ.get("WAMSS_IDENTITY_MIN_CONFIDENCE"), 0.5)

# Drop a track's state after this long without a sighting, so a
# long-running node does not accumulate dead track ids forever.
IDENTITY_STALE_SECONDS = _parse_float(
    os.environ.get("WAMSS_IDENTITY_STALE_SECONDS"), 600.0)

UNKNOWN = "unknown"


class _TrackState:
    """Vote history and resolved identity for one track id."""

    __slots__ = ("votes", "last_frame", "last_time", "last_seen",
                 "name", "confidence")

    def __init__(self, vote_window):
        self.votes = deque(maxlen=vote_window)  # (name, confidence|None)
        self.last_frame = None  # frame_no of the last identify call
        self.last_time = None   # time of the last identify call
        self.last_seen = None   # time of the last sighting (any frame)
        self.name = UNKNOWN     # resolved name, sticky once assigned
        self.confidence = None  # mean confidence of the resolving votes


class IdentityCache:
    """Smoothed per-track identity from a face-match function.

    identify_fn(frame, bbox) -> (name, confidence|None), where name is an
    enrolled person name or "unknown" and confidence is a float in 0..1
    (or None when no face matched). The function is called only when a
    track is first seen or a re-check is due; it is never called every
    frame. Exceptions from it are treated as an "unknown" vote.
    """

    def __init__(self, identify_fn, recheck_frames=None,
                 recheck_seconds=None, vote_window=None, min_votes=None,
                 min_confidence=None, stale_seconds=None):
        self._identify_fn = identify_fn
        self._recheck_frames = (IDENTITY_RECHECK_FRAMES
                                if recheck_frames is None
                                else recheck_frames)
        self._recheck_seconds = (IDENTITY_RECHECK_SECONDS
                                 if recheck_seconds is None
                                 else recheck_seconds)
        self._vote_window = (IDENTITY_VOTE_WINDOW
                             if vote_window is None else vote_window)
        self._min_votes = (IDENTITY_MIN_VOTES
                           if min_votes is None else min_votes)
        self._min_confidence = (IDENTITY_MIN_CONFIDENCE
                                if min_confidence is None
                                else min_confidence)
        self._stale_seconds = (IDENTITY_STALE_SECONDS
                               if stale_seconds is None else stale_seconds)
        self._tracks = {}  # track_key -> _TrackState

    def observe(self, track_key, frame, bbox, frame_no, now):
        """Record a sighting of a track; identify when due.

        Returns the track's current smoothed (name, confidence): a name
        with a float in 0..1, or ("unknown", None).
        """
        state = self._tracks.get(track_key)
        if state is None:
            state = _TrackState(self._vote_window)
            self._tracks[track_key] = state
        state.last_seen = now

        due = (state.last_frame is None
               or frame_no - state.last_frame >= self._recheck_frames
               or now - state.last_time >= self._recheck_seconds)
        if due:
            try:
                name, confidence = self._identify_fn(frame, bbox)
            except Exception:
                name, confidence = UNKNOWN, None
            if not name:
                name = UNKNOWN
            state.votes.append((name, confidence))
            state.last_frame = frame_no
            state.last_time = now
            self._resolve(state)

        self._purge_stale(now)
        return state.name, state.confidence

    def identity(self, track_key):
        """Current smoothed (name, confidence) for a track.

        ("unknown", None) when the track has never been seen, has no
        agreeing votes yet, or has no face match.
        """
        state = self._tracks.get(track_key)
        if state is None:
            return UNKNOWN, None
        return state.name, state.confidence

    def _resolve(self, state):
        """Apply the voting rule to a track's vote window.

        Assigns (or changes) the resolved name only when one name holds
        a strict majority of all votes in the window, has at least
        min_votes votes, and its mean confidence clears the threshold.
        Otherwise the previously resolved name is kept, so a tie, a
        single bad frame, or a run of unknown votes cannot flip a name.
        """
        votes = list(state.votes)
        if not votes:
            return
        named = {}  # name -> [confidences]
        for name, confidence in votes:
            if name and name != UNKNOWN:
                conf = confidence if confidence is not None else 0.0
                named.setdefault(name, []).append(conf)
        if not named:
            return
        # Winner: most votes, ties broken by higher mean confidence.
        best_name, best_confs = max(
            named.items(),
            key=lambda kv: (len(kv[1]), sum(kv[1]) / len(kv[1])))
        mean_conf = sum(best_confs) / len(best_confs)
        if (len(best_confs) >= self._min_votes
                and len(best_confs) * 2 > len(votes)
                and mean_conf >= self._min_confidence):
            state.name = best_name
            state.confidence = float(mean_conf)

    def _purge_stale(self, now):
        """Drop tracks not seen for longer than stale_seconds."""
        stale = [key for key, state in self._tracks.items()
                 if state.last_seen is not None
                 and now - state.last_seen > self._stale_seconds]
        for key in stale:
            del self._tracks[key]

    def __len__(self):
        return len(self._tracks)
