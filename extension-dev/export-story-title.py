"""Package the authoritative story title for the local Medium extension."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from publish_to_all.story import load_story  # noqa: E402


def fingerprint(value: str) -> str:
    value = " ".join(value.replace("\xa0", " ").split())
    number = 2166136261
    for character in value:
        number = ((number ^ ord(character)) * 16777619) & 0xffffffff
    return f"fnv1a32:{number:08x}"


def render(root: Path = ROOT) -> str:
    story = load_story(root / "In")
    data = {"title": story.metadata.title, "sourceHash": story.source_hash,
            "fingerprint": fingerprint(story.metadata.title)}
    return "/* Generated from In/ through publish_to_all.story.load_story. */\n" + \
        "globalThis.PublishToAllStory = Object.freeze(" + \
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ");\n"


if __name__ == "__main__":
    target = ROOT / "chrome-extension/story-title.js"
    target.write_text(render(), encoding="utf-8")
    print(target)
