# WAMSS Real-World Person Detection Evaluation Dataset

This directory contains the ground-truth evaluation and development datasets for benchmarking and evaluating person detection accuracy in the WAMSS vision pipeline.

## Directory Structure

```text
dataset/
├── README.md                      # This overview and annotation tool setup guide
├── ANNOTATION_GUIDELINES.md       # Ground-truth bounding box placement rules
├── dev/                           # Development split (tuning, threshold analysis)
│   ├── images/                    # Captured camera frames (.jpg) [git-ignored]
│   ├── labels/                    # Ground-truth annotations (.txt)
│   └── manifest.json              # Frame metadata, camera ID, session info
└── eval/                          # Held-out evaluation split (final benchmark)
    ├── images/                    # Captured camera frames (.jpg) [git-ignored]
    ├── labels/                    # Ground-truth annotations (.txt)
    └── manifest.json              # Frame metadata, camera ID, session info
```

> **Privacy Notice**: Raw camera images (`.jpg`, `.jpeg`, `.png`, `.mp4`) are explicitly excluded from version control via `.gitignore`. Footage stays entirely local on the development machine.

---

## Annotation Format (YOLO Standard)

Each image `CAM_<ID>_<session>_<frame>.jpg` in `images/` corresponds to a single text file `CAM_<ID>_<session>_<frame>.txt` in `labels/`.

Each line in a `.txt` label represents one detected person:

```text
<class_id> <x_center> <y_center> <width> <height>
```

- **`<class_id>`**: Always `0` for the `person` class.
- **`<x_center>`**: Horizontal center of bounding box normalized to $[0.0, 1.0]$: $\frac{x_{\min} + x_{\max}}{2 \times \text{image\_width}}$.
- **`<y_center>`**: Vertical center of bounding box normalized to $[0.0, 1.0]$: $\frac{y_{\min} + y_{\max}}{2 \times \text{image\_height}}$.
- **`<width>`**: Bounding box width normalized to $[0.0, 1.0]$: $\frac{x_{\max} - x_{\min}}{\text{image\_width}}$.
- **`<height>`**: Bounding box height normalized to $[0.0, 1.0]$: $\frac{y_{\max} - y_{\min}}{\text{image\_height}}$.

### Special Cases
- **Multiple People**: One row per person (multiple lines in the file).
- **Zero People (Empty Scene)**: An empty text file (0 bytes). Do **not** omit the `.txt` file; an empty file explicitly confirms zero people were present.

---

## How to Annotate Captured Frames

Ground truth annotations must be created manually or verified by human review, **never** by accepting unverified YOLO predictions.

### Recommended Tool 1: `labelImg` (Desktop, Fast, Native YOLO Export)
`labelImg` is a lightweight Python tool that directly opens images and reads/writes YOLO `.txt` files.

1. **Install:**
   ```bash
   pip install labelImg
   ```
2. **Launch:**
   ```bash
   labelImg dataset/eval/images predefined_classes.txt dataset/eval/labels
   ```
   *(Create a `predefined_classes.txt` with a single line: `person`)*
3. **Usage:**
   - Press `w` to draw a box.
   - Select `person` (class 0).
   - Press `d` for next image, `a` for previous image.
   - Press `Ctrl+S` to save. Empty scenes should simply be saved without drawing boxes (creates a 0-byte `.txt` file).

### Recommended Tool 2: Label Studio (Browser-based)
1. **Install & Run:**
   ```bash
   pip install label-studio
   label-studio start
   ```
2. **Project Setup:**
   - Create project: "WAMSS Person Evaluation".
   - Select template: **Object Detection with Bounding Boxes**.
   - Configure label: `<Label value="person" background="red"/>`.
   - Import images from `dataset/eval/images/`.
   - Export format: **YOLO** into `dataset/eval/labels/`.

### Recommended Tool 3: CVAT (Computer Vision Annotation Tool)
1. Visit https://app.cvat.ai or run locally via Docker.
2. Upload task images from `dataset/eval/images/`.
3. Add single label `person`.
4. Export task dataset in **YOLO 1.1** format.

---

## Validation

After creating or modifying annotations, always validate the dataset with the project validator:

```bash
PYTHONPATH=. .venv/bin/python -m vision.dataset_validator --dataset-dir dataset/
```

This verifies that:
1. Every image has an accompanying label file.
2. Coordinates are bounded in $[0.0, 1.0]$ with positive dimensions.
3. Class ID is strictly `0`.
4. No corrupt or mismatched labels exist.
