"""The unpacked extension must contain only Chrome-loadable entries."""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "extension_tree", ROOT / "extension-dev/extension_tree.py")
extension_tree = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extension_tree)


def test_load_unpacked_tree_has_no_forbidden_artifacts():
    assert not extension_tree.forbidden(ROOT / "chrome-extension"), (
        "Chrome extension tree contains forbidden load-unpacked artifact: "
        + ", ".join(str(path.relative_to(ROOT)) for path in
                    extension_tree.forbidden(ROOT / "chrome-extension")))


def test_guard_detects_generated_files_and_other_underscore_entries(tmp_path):
    root = tmp_path / "chrome-extension"
    (root / "test/__pycache__").mkdir(parents=True)
    for name in ("test/__pycache__/fixture.pyc", "fixture.pyo", "_blocked.js"):
        (root / name).touch()
    found = {path.relative_to(root).as_posix() for path in extension_tree.forbidden(root)}
    assert found == {"test/__pycache__", "test/__pycache__/fixture.pyc",
                     "fixture.pyo", "_blocked.js"}
    extension_tree.clean_generated(root)
    assert (root / "_blocked.js").exists()
    assert not (root / "test/__pycache__").exists()
    assert {path.relative_to(root).as_posix() for path in extension_tree.forbidden(root)} == {
        "_blocked.js"}
