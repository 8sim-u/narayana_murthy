import pytest
from vision.workstation_objects import (
    SUPPORTED_WORKSTATION_CLASSES,
    UNSUPPORTED_OBJECT_CLASSES,
    CLASS_NAME_MAP,
    is_class_supported,
    is_interactive_device,
    explain_class_support,
    load_workstation_object_config,
)


def test_supported_classes_contain_required_workstation_items():
    # Must support person, laptop, cell phone, tv/monitor, keyboard, mouse, book
    assert 0 in SUPPORTED_WORKSTATION_CLASSES  # person
    assert 62 in SUPPORTED_WORKSTATION_CLASSES  # tv / monitor
    assert 63 in SUPPORTED_WORKSTATION_CLASSES  # laptop
    assert 64 in SUPPORTED_WORKSTATION_CLASSES  # mouse
    assert 66 in SUPPORTED_WORKSTATION_CLASSES  # keyboard
    assert 67 in SUPPORTED_WORKSTATION_CLASSES  # cell phone
    assert 73 in SUPPORTED_WORKSTATION_CLASSES  # book
    assert 56 in SUPPORTED_WORKSTATION_CLASSES  # chair (seating)


def test_is_interactive_device():
    # Laptop, keyboard, mouse, cell phone are interactive devices
    assert is_interactive_device("laptop") is True
    assert is_interactive_device(63) is True
    assert is_interactive_device("keyboard") is True
    assert is_interactive_device(66) is True
    assert is_interactive_device("mouse") is True
    assert is_interactive_device(64) is True
    assert is_interactive_device("cell phone") is True
    assert is_interactive_device(67) is True

    # Person, chair, book, tv/monitor are not considered interactive input devices
    assert is_interactive_device("person") is False
    assert is_interactive_device(0) is False
    assert is_interactive_device("chair") is False
    assert is_interactive_device("book") is False
    assert is_interactive_device("tv") is False


def test_unsupported_classes_documentation():
    # Unsupported classes explicitly documented
    for item in ["loose paper", "document page", "pen", "pencil", "stapler", "coffee mug", "badge"]:
        assert is_class_supported(item) is False
        explanation = explain_class_support(item)
        assert "unsupported" in explanation.lower() or "not supported" in explanation.lower()


def test_supported_class_explanation():
    explanation = explain_class_support("laptop")
    assert "supported" in explanation.lower()
    assert "coco" in explanation.lower() or "class id 63" in explanation.lower()


def test_load_workstation_object_config(monkeypatch):
    monkeypatch.setenv("WAMSS_PERSON_CONF", "0.5")
    monkeypatch.setenv("WAMSS_OBJECT_CONF", "0.3")
    monkeypatch.setenv("WAMSS_OBJECT_CLASSES", "0,63,67")

    cfg = load_workstation_object_config()
    assert cfg["person_conf"] == 0.5
    assert cfg["object_conf"] == 0.3
    assert cfg["target_classes"] == [0, 63, 67]
