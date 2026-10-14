"""The packaged title and browser editing path use the publishing story source."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from publish_to_all.story import load_story


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome-extension"
EXTENSION_DEV = ROOT / "extension-dev"


def test_packaged_title_matches_story_pipeline():
    spec = importlib.util.spec_from_file_location(
        "export_story_title", EXTENSION_DEV / "export-story-title.py")
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "dont_write_bytecode", True):
        spec.loader.exec_module(module)
    packaged = (EXTENSION / "story-title.js").read_text(encoding="utf-8")
    assert packaged == module.render(ROOT)
    story = load_story(ROOT / "In")
    assert story.metadata.title == "Saturation of Artificial Intelligence"
    assert json.dumps(story.metadata.title) in packaged
    assert story.source_hash in packaged
    assert "fnv1a32:5e81665d" in packaged


def test_local_chrome_composite_title_fill():
    completed = subprocess.run(
        [sys.executable, "-B", str(EXTENSION_DEV / "fixtures/chrome_medium_title_fixture.py")],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=30,
    )
    report = json.loads(completed.stdout)
    assert report["filled"]["workflow"] == "MEDIUM_TITLE_VERIFIED"
    assert report["filled"]["bodyUnchangedAfterTitleFill"]


def test_local_chrome_title_control_diagnostics():
    result = subprocess.run(
        [sys.executable, "-B", str(EXTENSION_DEV / "fixtures/chrome_title_control_fixture.py")],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
    report = json.loads(result.stdout)
    assert report["readyState"]["hitTest"] == "BUTTON"
    assert report["activationCalls"] == 0
