# Implementation Plan: Feature 4 - Workstation Object Detection, Person-Object Association, and Activity/Engagement Analysis

**Date:** 2026-11-01  
**Project:** `narayana_murthy`  
**Goal:** Implement and verify workstation object detection (laptops, phones, monitors, keyboards, mice, books), spatial/temporal person-object association linked to ByteTrack IDs, observable workstation activity/engagement metrics, visual HUD & backend event integration, and comprehensive synthetic and live camera verification.

---

## 1. Architecture & Design Principles

### 1.1 Object Detection (`vision/workstation_objects.py`)
- **YOLOv8n Supported Classes**:
  - `0`: `person`
  - `56`: `chair` (workstation seat)
  - `62`: `tv` (monitors, desktop screens, displays)
  - `63`: `laptop`
  - `64`: `mouse`
  - `66`: `keyboard`
  - `67`: `cell phone` (mobile phone)
  - `73`: `book` (books, physical documentation binders)
- **Documented Unsupported Classes**:
  - Loose paper sheets / single-sheet paper documents (not distinct in COCO)
  - Pens, pencils, markers, stationery (not distinct in COCO)
  - Office desk accessories (staplers, paperclips)
  - ID badges / lanyard passes
  *(Any request to detect these must report that YOLOv8n COCO weights do not support them without custom domain-specific fine-tuning).*

### 1.2 Person-Object Association (`vision/association.py`)
- **Spatial Metrics**:
  - Bounding box coordinates $[x_1, y_1, x_2, y_2]$.
  - Object center $C_o = (\frac{x_{1o}+x_{2o}}{2}, \frac{y_{1o}+y_{2o}}{2})$.
  - Person interaction zone: lower 65% of person bounding box (lap/desk level) expanded by a horizontal margin $M_x = 0.35 \times W_p$.
  - Intersection over Object (IoO): $\frac{\text{Area}(B_p \cap B_o)}{\text{Area}(B_o)}$.
  - Proximity distance normalized by person scale: $d_{\text{norm}} = \frac{\|C_o - C_{p,\text{desk}}\|}{\max(W_p, H_p)}$.
- **Association States**:
  1. `UNASSOCIATED`: Object is beyond reach threshold ($d_{\text{norm}} > 1.2$) or ambiguous between two people with comparable distances.
  2. `NEAR`: Object is within proximity zone ($d_{\text{norm}} \le 1.2$), but not in the active desk/lap interaction zone.
  3. `ASSOCIATED`: Object is in the person's workstation sector (desk plane, direct alignment).
  4. `INTERACTING`: Active device interaction estimated — high overlap ($\text{IoO} \ge 0.25$ or center inside person lower-half interaction zone) with interactive devices (laptop, keyboard, mouse, cell phone).
- **Ambiguity & Disambiguation**:
  - Each object is associated with at most one person unless explicitly flagged as shared.
  - If two people are within similar proximity ($|\Delta d| < 0.1$), and neither has significant containment, the object is marked `UNASSOCIATED` or `SHARED`.
- **Temporal Consistency**:
  - Association history buffer ($N=10$ frames) prevents single-frame flickering or detection dropouts from resetting the association.

### 1.3 Workstation Activity & Engagement Metrics (`vision/activity_metrics.py`)
- **Metrics Tracked per ByteTrack Person ID**:
  - `presence_duration_s`: Total elapsed time person was visible at workstation.
  - `device_interaction_duration_s`: Cumulative time person was observed actively interacting with an associated device.
  - `device_near_duration_s`: Cumulative time a device was near/associated without active interaction.
  - `away_duration_s`: Time elapsed since person was last seen.
  - `current_state`: `INTERACTING`, `PRESENT_IDLE`, `PRESENT_NO_DEVICE`, `AWAY`.
  - `engagement_score`:
    $$\text{Engagement \%} = \frac{\text{Device Interaction Time}}{\text{Total Workstation Presence Time}} \times 100$$
- **Safeguards**:
  - If `presence_duration_s < 5.0`: Return `Insufficient evidence (warming up)`.
  - If no interactive devices are detected: Return `Insufficient evidence (no devices visible)`.
  - Transparent documentation: Metrics represent *observable workstation device engagement*, NOT employee productivity, task completion, or work quality.

### 1.4 Dashboard, Visual HUD, & Backend Integration (`vision/multicamera.py` & `vision/tracking.py`)
- Visual frame annotations:
  - Person boxes with Track ID, confidence, and associated devices badge.
  - Object boxes with class name, confidence, and association status (`Assoc: ID 1 [INTERACTING]`, `Unassociated`).
  - Corner HUD showing Track ID, Presence Time, Interaction Time, Engagement %, and Status.
- Headless logging: structured JSON-like lines every 1s.
- Backend `/event` ingestion: Emits payload with `objects: ["person", "laptop", "cell phone"]`, confidence, track ID, and associated metadata.

---

## 2. Step-by-Step Task Decomposition

### Task 1: Workstation Object Definitions & Configurations
- **Files**: `vision/workstation_objects.py`, `vision/tests/test_workstation_objects.py`
- **Steps**:
  1. Write unit tests in `vision/tests/test_workstation_objects.py` testing:
     - Target COCO class mappings (person=0, tv=62, laptop=63, mouse=64, keyboard=66, phone=67, book=73, chair=56).
     - Unsupported class documentation helper returning unsupported status for "paper", "pen", "notebook", "mug".
     - Configuration loader parsing `WAMSS_OBJECT_CLASSES`, `WAMSS_PERSON_CONF`, `WAMSS_OBJECT_CONF`.
  2. Implement `vision/workstation_objects.py` with classes, constants, and helper functions.
  3. Run `pytest vision/tests/test_workstation_objects.py`.

### Task 2: Spatial & Temporal Person-Object Association Engine
- **Files**: `vision/association.py`, `vision/tests/test_association.py`
- **Steps**:
  1. Write unit tests in `vision/tests/test_association.py` covering:
     - Spatial proximity calculation (center distance, IoO).
     - Single person with laptop and phone (correct association & interaction level).
     - Unassociated distant object (laptop across room -> UNASSOCIATED).
     - Multi-person association disambiguation (Person 1 at Desk 1 with Laptop 1, Person 2 at Desk 2 with Laptop 2).
     - Ambiguous boundary case (object centered exactly midway between two people -> UNASSOCIATED).
     - Temporal smoothing (single frame dropout maintains track association).
  2. Implement `vision/association.py` with `PersonObjectAssociator` class.
  3. Run `pytest vision/tests/test_association.py`.

### Task 3: Observable Workstation Activity & Engagement Analyzer
- **Files**: `vision/activity_metrics.py`, `vision/tests/test_activity_metrics.py`
- **Steps**:
  1. Write unit tests in `vision/tests/test_activity_metrics.py` covering:
     - Presence duration accumulation over time.
     - Device interaction duration accumulation when `INTERACTING`.
     - Present-idle accumulation when `PRESENT_IDLE`.
     - Engagement formula verification: $\frac{t_{\text{interact}}}{t_{\text{present}}} \times 100$.
     - "Insufficient evidence" safeguard when presence < 5s or no devices detected.
     - Away transition when track disappears.
  2. Implement `vision/activity_metrics.py` with `WorkstationActivityTracker`.
  3. Run `pytest vision/tests/test_activity_metrics.py`.

### Task 4: Pipeline Integration & Visual HUD Overlay
- **Files**: `vision/tracking.py`, `vision/multicamera.py`, `vision/hud.py`
- **Steps**:
  1. Implement HUD overlay drawer in `vision/hud.py` (draws person badges, object association tags, and corner activity summary HUD).
  2. Update `vision/tracking.py` to run multi-class YOLO detection/tracking, feed into `PersonObjectAssociator` and `WorkstationActivityTracker`, render annotations/HUD, and output headless summaries.
  3. Update `vision/multicamera.py` to integrate object association and enriched event payloads to backend `/event`.
  4. Write automated integration tests in `vision/tests/test_tracking.py` and `vision/tests/test_multicamera_objects.py`.
  5. Run `pytest`.

### Task 5: Live Camera Verification (`http://127.0.0.1:8080/video`)
- **Constraint**: ONLY ONE real person in front of webcam. Multi-person tested via synthetic scenarios.
- **Script**: `vision/tests/live_workstation_test.py`
- **Verifications**:
  - Test A: Real object detection (laptop, phone, book/screen).
  - Test B: Spatial person-object association (person near laptop, changing position, unassociated objects).
  - Test C: Temporal consistency over 30s live stream.
  - Test D: Activity metrics and engagement score computation on live feed.
  - Save annotated frame artifact showing HUD and association tags.

### Task 6: Full Regression & Final Report
- **Steps**:
  1. Run full project test suite (`pytest`) to ensure all 241+ tests pass and no regressions exist in camera pipeline, YOLO human detection, or ByteTrack tracking.
  2. Prepare comprehensive Final Report covering all required points.
