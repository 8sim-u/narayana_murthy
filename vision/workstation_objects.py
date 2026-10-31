"""
Workstation Object Definitions and Configurations for WAMSS Vision.

Defines supported COCO classes for workstation items (laptops, phones,
monitors/screens, keyboards, mice, books, seating), explicitly documents
unsupported classes, and handles object detection thresholds.
"""

import os
from typing import Dict, List, Set, Union

# COCO 80-class mapping for YOLOv8
CLASS_NAME_MAP: Dict[int, str] = {
    0: "person",
    56: "chair",
    62: "tv",          # Monitors, desktop screens, television displays
    63: "laptop",
    64: "mouse",
    66: "keyboard",
    67: "cell phone",
    73: "book",        # Books, documentation binders
}

NAME_TO_CLASS_MAP: Dict[str, int] = {v: k for k, v in CLASS_NAME_MAP.items()}
# Alternate names
NAME_TO_CLASS_MAP.update({
    "monitor": 62,
    "screen": 62,
    "mobile phone": 67,
    "phone": 67,
    "pc": 63,
})

SUPPORTED_WORKSTATION_CLASSES: List[int] = sorted(list(CLASS_NAME_MAP.keys()))

# Interactive devices that indicate hands-on workstation operation
INTERACTIVE_DEVICES: Set[int] = {
    63,  # laptop
    64,  # mouse
    66,  # keyboard
    67,  # cell phone
}

# Explicitly documented unsupported classes that COCO yolov8n weights do NOT detect natively
UNSUPPORTED_OBJECT_CLASSES: Dict[str, str] = {
    "loose paper": "Loose sheets of paper are not a separate class in YOLOv8n (COCO); books/binders are detected as class 73 ('book').",
    "document page": "Single document pages are not distinct in COCO; require custom document-detection weights.",
    "pen": "Pens and pencils are not represented in the 80 standard COCO classes.",
    "pencil": "Writing stationery is not represented in COCO 80 classes.",
    "stapler": "Desk stationery (staplers, hole punchers) is not present in COCO classes.",
    "paperclip": "Desk accessories are not represented in COCO classes.",
    "coffee mug": "Coffee mugs/cups are class 41 in full COCO, but not part of default workstation electronics.",
    "badge": "Lanyards and employee badges require specialized facial/wearable models.",
}


def is_class_supported(target: Union[str, int]) -> bool:
    """Check if a given class name or id is in supported workstation classes."""
    if isinstance(target, int):
        return target in CLASS_NAME_MAP
    if isinstance(target, str):
        target_clean = target.strip().lower()
        if target_clean in NAME_TO_CLASS_MAP:
            return True
        for name in CLASS_NAME_MAP.values():
            if name in target_clean or target_clean in name:
                return True
    return False


def is_interactive_device(target: Union[str, int]) -> bool:
    """Returns True if the target object is an interactive device (laptop, mouse, keyboard, phone)."""
    if isinstance(target, int):
        return target in INTERACTIVE_DEVICES
    if isinstance(target, str):
        cid = NAME_TO_CLASS_MAP.get(target.strip().lower())
        if cid is not None:
            return cid in INTERACTIVE_DEVICES
        for k, v in CLASS_NAME_MAP.items():
            if (v in target.strip().lower() or target.strip().lower() in v) and k in INTERACTIVE_DEVICES:
                return True
    return False


def explain_class_support(target: str) -> str:
    """Provides a clear explanation of whether a class is supported or why it is not."""
    target_clean = target.strip().lower()
    if is_class_supported(target_clean):
        cid = NAME_TO_CLASS_MAP.get(target_clean, 0)
        cname = CLASS_NAME_MAP.get(cid, target_clean)
        return f"Supported workstation object in COCO dataset: '{cname}' (Class ID {cid})."

    for unsupp, reason in UNSUPPORTED_OBJECT_CLASSES.items():
        if unsupp in target_clean or target_clean in unsupp:
            return f"Unsupported class '{target}': {reason}"

    return (
        f"Unsupported object class '{target}'. Standard YOLOv8n (COCO-trained) only detects 80 standard classes. "
        "Detecting custom office items requires a domain-specific fine-tuned model."
    )


def load_workstation_object_config() -> Dict[str, Union[float, List[int]]]:
    """Loads workstation detection configuration from environment variables."""
    person_conf = float(os.environ.get("WAMSS_PERSON_CONF", os.environ.get("WAMSS_CONF_THRESHOLD", "0.45")))
    object_conf = float(os.environ.get("WAMSS_OBJECT_CONF", "0.25"))

    custom_classes = os.environ.get("WAMSS_OBJECT_CLASSES")
    if custom_classes:
        target_classes = [int(x.strip()) for x in custom_classes.split(",") if x.strip().isdigit()]
    else:
        target_classes = list(SUPPORTED_WORKSTATION_CLASSES)

    return {
        "person_conf": person_conf,
        "object_conf": object_conf,
        "target_classes": target_classes,
    }
