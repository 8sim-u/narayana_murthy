"""
Dataset validation utility for WAMSS real-world vision evaluation.

Validates that:
- Every image has a corresponding annotation file.
- Annotations follow standard normalized YOLO format (<class_id> <x_center> <y_center> <w> <h>).
- Class IDs are valid (class 0 = person).
- Coordinates and dimensions are bounded in [0.0, 1.0], w > 0, h > 0.
- Bounding boxes remain within image boundaries.
- Empty annotation files (0 people) are supported.
- Manifest accurately describes on-disk dataset files.
- Generates a structured audit report.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2

logger = logging.getLogger("dataset_validator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
TOLERANCE = 1e-4  # Floating point boundary tolerance


@dataclass
class AnnotationError:
    file_path: str
    line_number: int
    raw_line: str
    error_message: str


@dataclass
class DatasetValidationReport:
    is_valid: bool = True
    total_images: int = 0
    total_annotations: int = 0
    zero_person_images: int = 0
    single_person_images: int = 0
    multi_person_images: int = 0
    sessions: Set[str] = field(default_factory=set)
    resolutions: Set[Tuple[int, int]] = field(default_factory=set)
    scenario_coverage: Dict[str, int] = field(default_factory=dict)
    errors: List[AnnotationError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "total_images": self.total_images,
            "total_annotated_people": self.total_annotations,
            "distribution": {
                "zero_person_images": self.zero_person_images,
                "single_person_images": self.single_person_images,
                "multi_person_images": self.multi_person_images,
            },
            "recording_sessions": sorted(list(self.sessions)),
            "resolutions": [f"{w}x{h}" for w, h in sorted(list(self.resolutions))],
            "scenario_coverage": self.scenario_coverage,
            "error_count": len(self.errors),
            "errors": [
                {
                    "file": err.file_path,
                    "line": err.line_number,
                    "content": err.raw_line,
                    "message": err.error_message,
                }
                for err in self.errors
            ],
            "warnings": self.warnings,
        }

    def summary_markdown(self) -> str:
        status_tag = "VALID" if self.is_valid else "INVALID"
        lines = [
            f"# Dataset Validation Summary: {status_tag}",
            "",
            "| Metric | Value |",
            "| :--- | :--- |",
            f"| **Total Images** | {self.total_images} |",
            f"| **Total Annotated People** | {self.total_annotations} |",
            f"| **Images with 0 People** | {self.zero_person_images} |",
            f"| **Images with 1 Person** | {self.single_person_images} |",
            f"| **Images with Multiple People** | {self.multi_person_images} |",
            f"| **Distinct Sessions** | {len(self.sessions)} ({', '.join(sorted(self.sessions)) or 'None'}) |",
            f"| **Image Resolutions** | {', '.join(f'{w}x{h}' for w, h in sorted(self.resolutions)) or 'None'} |",
            f"| **Validation Errors** | {len(self.errors)} |",
            "",
        ]

        if self.scenario_coverage:
            lines.extend([
                "### Scenario Coverage",
                "",
                "| Scenario / Keyword | Image Count |",
                "| :--- | :--- |",
            ])
            for scen, count in sorted(self.scenario_coverage.items()):
                lines.append(f"| `{scen}` | {count} |")
            lines.append("")

        if self.errors:
            lines.extend([
                "### Validation Errors",
                "",
                "| File | Line | Issue |",
                "| :--- | :--- | :--- |",
            ])
            for err in self.errors[:25]:  # limit display
                rel_path = Path(err.file_path).name
                lines.append(f"| `{rel_path}` | {err.line_number} | {err.error_message} |")
            if len(self.errors) > 25:
                lines.append(f"| ... and {len(self.errors) - 25} more errors | | |")
            lines.append("")

        if self.warnings:
            lines.extend([
                "### Warnings",
                "",
            ])
            for w in self.warnings:
                lines.append(f"- ⚠️ {w}")
            lines.append("")

        return "\n".join(lines)


def parse_yolo_line(line: str) -> Tuple[Optional[int], Optional[float], Optional[float], Optional[float], Optional[float], Optional[str]]:
    """
    Parses a single YOLO annotation line.
    Returns (class_id, x_center, y_center, width, height, error_str).
    """
    tokens = line.strip().split()
    if not tokens:
        return None, None, None, None, None, "Empty line"

    if len(tokens) != 5:
        return None, None, None, None, None, f"Expected 5 tokens (<class_id> <x> <y> <w> <h>), found {len(tokens)}"

    try:
        class_id = int(tokens[0])
    except ValueError:
        return None, None, None, None, None, f"class_id '{tokens[0]}' is not an integer"

    try:
        xc = float(tokens[1])
        yc = float(tokens[2])
        w = float(tokens[3])
        h = float(tokens[4])
    except ValueError:
        return None, None, None, None, None, "Coordinates must be floating point numbers"

    return class_id, xc, yc, w, h, None


def validate_split(
    split_dir: Path,
    report: DatasetValidationReport,
    allowed_class_ids: Set[int] = {0},
) -> None:
    """Validates an individual split directory (e.g. dataset/eval or dataset/dev)."""
    images_dir = split_dir / "images"
    labels_dir = split_dir / "labels"

    if not images_dir.exists():
        report.warnings.append(f"Images directory missing: {images_dir}")
        return

    image_files = [f for f in sorted(images_dir.iterdir()) if f.suffix.lower() in IMAGE_EXTENSIONS]

    manifest_files = list(split_dir.glob("manifest*.json"))
    manifest_images: Set[str] = set()
    for mf in manifest_files:
        try:
            with open(mf, "r", encoding="utf-8") as f:
                mdata = json.load(f)
                session_id = mdata.get("session_id", "unknown")
                report.sessions.add(session_id)
                scen_desc = mdata.get("scenario_description", session_id)
                for frame_info in mdata.get("frames", []):
                    fname = frame_info.get("filename")
                    if fname:
                        manifest_images.add(fname)
                        report.scenario_coverage[scen_desc] = report.scenario_coverage.get(scen_desc, 0) + 1
        except Exception as exc:
            report.warnings.append(f"Failed to read manifest {mf}: {exc}")

    for img_path in image_files:
        report.total_images += 1

        # Check resolution
        img = cv2.imread(str(img_path))
        if img is None:
            report.is_valid = False
            report.errors.append(
                AnnotationError(str(img_path), 0, "", "Corrupt image file; unable to decode with OpenCV")
            )
            continue
        h, w, _ = img.shape
        report.resolutions.add((w, h))

        # Check matching label file
        label_filename = img_path.stem + ".txt"
        label_path = labels_dir / label_filename

        if not label_path.exists():
            report.is_valid = False
            report.errors.append(
                AnnotationError(
                    str(img_path),
                    0,
                    "",
                    f"Missing label file: expected {label_path.name} in {labels_dir}",
                )
            )
            continue

        # Parse annotations
        person_count_in_image = 0
        with open(label_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Handle empty annotation file (0 people)
        non_empty_lines = [line.strip() for line in lines if line.strip()]
        if not non_empty_lines:
            report.zero_person_images += 1
            continue

        for line_no, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue

            cid, xc, yc, bw, bh, err = parse_yolo_line(stripped)
            if err:
                report.is_valid = False
                report.errors.append(AnnotationError(str(label_path), line_no, stripped, err))
                continue

            # Check class ID
            if cid not in allowed_class_ids:
                report.is_valid = False
                report.errors.append(
                    AnnotationError(
                        str(label_path),
                        line_no,
                        stripped,
                        f"Invalid class_id {cid}; expected one of {allowed_class_ids}",
                    )
                )

            # Check coordinate bounds
            if not (0.0 <= xc <= 1.0 and 0.0 <= yc <= 1.0):
                report.is_valid = False
                report.errors.append(
                    AnnotationError(
                        str(label_path),
                        line_no,
                        stripped,
                        f"Box center ({xc}, {yc}) outside normalized range [0.0, 1.0]",
                    )
                )

            if bw <= 0.0 or bh <= 0.0:
                report.is_valid = False
                report.errors.append(
                    AnnotationError(
                        str(label_path),
                        line_no,
                        stripped,
                        f"Box dimensions ({bw}, {bh}) must be strictly positive",
                    )
                )

            if bw > 1.0 or bh > 1.0:
                report.is_valid = False
                report.errors.append(
                    AnnotationError(
                        str(label_path),
                        line_no,
                        stripped,
                        f"Box dimensions ({bw}, {bh}) cannot exceed 1.0",
                    )
                )

            # Check bounding box containment within image boundaries
            x_min = xc - bw / 2.0
            x_max = xc + bw / 2.0
            y_min = yc - bh / 2.0
            y_max = yc + bh / 2.0

            if x_min < -TOLERANCE or x_max > 1.0 + TOLERANCE:
                report.is_valid = False
                report.errors.append(
                    AnnotationError(
                        str(label_path),
                        line_no,
                        stripped,
                        f"Horizontal extent [{x_min:.4f}, {x_max:.4f}] exceeds image boundaries [0.0, 1.0]",
                    )
                )

            if y_min < -TOLERANCE or y_max > 1.0 + TOLERANCE:
                report.is_valid = False
                report.errors.append(
                    AnnotationError(
                        str(label_path),
                        line_no,
                        stripped,
                        f"Vertical extent [{y_min:.4f}, {y_max:.4f}] exceeds image boundaries [0.0, 1.0]",
                    )
                )

            person_count_in_image += 1
            report.total_annotations += 1

        if person_count_in_image == 1:
            report.single_person_images += 1
        elif person_count_in_image > 1:
            report.multi_person_images += 1

    # Check for orphaned label files
    if labels_dir.exists():
        for l_path in labels_dir.glob("*.txt"):
            matching_img = [img for img in image_files if img.stem == l_path.stem]
            if not matching_img:
                report.warnings.append(f"Orphaned label file (no matching image): {l_path.name}")


def validate_dataset(dataset_dir: Path | str) -> DatasetValidationReport:
    """Validates all splits inside dataset_dir."""
    base_path = Path(dataset_dir)
    report = DatasetValidationReport()

    splits = ["eval", "dev"]
    found_any_split = False
    for sp in splits:
        sp_dir = base_path / sp
        if sp_dir.exists():
            found_any_split = True
            validate_split(sp_dir, report)

    if not found_any_split:
        # Check if dataset_dir itself is a split
        if (base_path / "images").exists() or (base_path / "labels").exists():
            validate_split(base_path, report)
        else:
            report.is_valid = False
            report.errors.append(
                AnnotationError(str(base_path), 0, "", f"No dataset splits found in {base_path}")
            )

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate WAMSS person detection evaluation dataset.")
    parser.add_argument(
        "--dataset-dir",
        default="dataset",
        help="Root path to evaluation dataset (default: dataset)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON formatted report",
    )
    args = parser.parse_args()

    report = validate_dataset(args.dataset_dir)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary_markdown())

    if not report.is_valid:
        exit(1)


if __name__ == "__main__":
    main()
