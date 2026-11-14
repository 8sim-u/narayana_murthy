"""
Unit tests for vision.dataset_validator.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from vision.dataset_validator import (
    parse_yolo_line,
    validate_dataset,
)


def test_parse_yolo_line_valid():
    cid, xc, yc, w, h, err = parse_yolo_line("0 0.5000 0.4000 0.2500 0.6000")
    assert err is None
    assert cid == 0
    assert xc == 0.5
    assert yc == 0.4
    assert w == 0.25
    assert h == 0.6


def test_parse_yolo_line_invalid_tokens():
    _, _, _, _, _, err = parse_yolo_line("0 0.5 0.5 0.2")
    assert "Expected 5 tokens" in err


def test_parse_yolo_line_invalid_class():
    _, _, _, _, _, err = parse_yolo_line("person 0.5 0.5 0.2 0.4")
    assert "not an integer" in err


def test_validate_clean_dataset(tmp_path: Path):
    split_dir = tmp_path / "eval"
    img_dir = split_dir / "images"
    lbl_dir = split_dir / "labels"
    img_dir.mkdir(parents=True)
    lbl_dir.mkdir(parents=True)

    # 1. Image with 1 person
    img1 = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(img_dir / "img1.jpg"), img1)
    with open(lbl_dir / "img1.txt", "w") as f:
        f.write("0 0.5 0.5 0.3 0.6\n")

    # 2. Image with 2 people
    img2 = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(img_dir / "img2.jpg"), img2)
    with open(lbl_dir / "img2.txt", "w") as f:
        f.write("0 0.3 0.5 0.2 0.5\n0 0.7 0.5 0.2 0.5\n")

    # 3. Image with 0 people (empty file)
    img3 = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(img_dir / "img3.jpg"), img3)
    (lbl_dir / "img3.txt").touch()

    report = validate_dataset(tmp_path)
    assert report.is_valid is True
    assert report.total_images == 3
    assert report.total_annotations == 3
    assert report.zero_person_images == 1
    assert report.single_person_images == 1
    assert report.multi_person_images == 1
    assert len(report.errors) == 0


def test_validate_detects_missing_label(tmp_path: Path):
    split_dir = tmp_path / "eval"
    img_dir = split_dir / "images"
    lbl_dir = split_dir / "labels"
    img_dir.mkdir(parents=True)
    lbl_dir.mkdir(parents=True)

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(img_dir / "unlabeled.jpg"), img)

    report = validate_dataset(tmp_path)
    assert report.is_valid is False
    assert len(report.errors) == 1
    assert "Missing label file" in report.errors[0].error_message


def test_validate_detects_out_of_bounds_coordinates(tmp_path: Path):
    split_dir = tmp_path / "eval"
    img_dir = split_dir / "images"
    lbl_dir = split_dir / "labels"
    img_dir.mkdir(parents=True)
    lbl_dir.mkdir(parents=True)

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(img_dir / "bad_bounds.jpg"), img)
    with open(lbl_dir / "bad_bounds.txt", "w") as f:
        # xc=0.1, w=0.4 => x_min = -0.1 (violates boundaries)
        f.write("0 0.1 0.5 0.4 0.5\n")

    report = validate_dataset(tmp_path)
    assert report.is_valid is False
    assert any("exceeds image boundaries" in err.error_message for err in report.errors)


def test_validate_detects_invalid_class_id(tmp_path: Path):
    split_dir = tmp_path / "eval"
    img_dir = split_dir / "images"
    lbl_dir = split_dir / "labels"
    img_dir.mkdir(parents=True)
    lbl_dir.mkdir(parents=True)

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(img_dir / "wrong_class.jpg"), img)
    with open(lbl_dir / "wrong_class.txt", "w") as f:
        f.write("2 0.5 0.5 0.2 0.4\n")  # class 2 instead of 0

    report = validate_dataset(tmp_path)
    assert report.is_valid is False
    assert any("Invalid class_id 2" in err.error_message for err in report.errors)
