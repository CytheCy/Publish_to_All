"""Check or explicitly clean generated files in the load-unpacked extension."""

import argparse
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome-extension"


def entries(root: Path):
    """Yield all entries, including hidden files and symlinks, without following links."""
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            yield Path(directory) / name


def forbidden(root: Path) -> list[Path]:
    return sorted((path for path in entries(root)
                   if path.name.startswith("_") or path.name.endswith((".pyc", ".pyo"))),
                  key=lambda path: str(path))


def clean_generated(root: Path) -> list[Path]:
    """Remove bytecode files and empty cache directories; preserve other contents."""
    removed = []
    for path in entries(root):
        if path.name.endswith((".pyc", ".pyo")) and (path.is_file() or path.is_symlink()):
            path.unlink()
            removed.append(path)
    caches = (path for path in entries(root)
              if path.name == "__pycache__" and path.is_dir() and not path.is_symlink())
    for path in sorted(caches, key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            continue
        removed.append(path)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "clean"))
    args = parser.parse_args()
    if args.command == "clean":
        for path in clean_generated(EXTENSION):
            print(f"Removed: {path.relative_to(ROOT)}")
        return 0
    found = forbidden(EXTENSION)
    for path in found:
        print(f"Chrome extension tree contains forbidden load-unpacked artifact: "
              f"{path.relative_to(ROOT)}")
    return int(bool(found))


if __name__ == "__main__":
    raise SystemExit(main())
