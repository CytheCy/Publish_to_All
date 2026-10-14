"""Package the canonical Medium body using the Python story loader and parser."""

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from publish_to_all.story import load_story  # noqa: E402


def normalize(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def fingerprint(value: str) -> str:
    number = 2166136261
    for character in value:
        number = ((number ^ ord(character)) * 16777619) & 0xffffffff
    return f"fnv1a32:{number:08x}"


def analyze(story):
    blocks = []
    unsupported = set()
    counts = Counter({name: 0 for name in (
        "paragraph", "heading", "block_quote", "horizontal_rule", "emphasis",
        "link", "list", "code_block", "image", "html")})
    kinds = {"blockquote_open": "block_quote", "hr": "horizontal_rule",
             "em_open": "emphasis", "strong_open": "emphasis",
             "link_open": "link", "bullet_list_open": "list",
             "ordered_list_open": "list", "fence": "code_block",
             "code_block": "code_block", "code_inline": "code_block",
             "image": "image", "html_block": "html", "html_inline": "html"}
    tokens = story.tokens
    for index, token in enumerate(tokens):
        if token.type not in ("heading_open", "heading_close", "paragraph_open",
                              "paragraph_close", "inline"):
            unsupported.add(token.type)
            counts[kinds.get(token.type, token.type)] += 1
        if token.type not in ("heading_open", "paragraph_open"):
            continue
        kind = "heading" if token.type == "heading_open" else "paragraph"
        inline = tokens[index + 1]
        if inline.type != "inline":
            unsupported.add("unexpected_block_shape")
            continue
        for child in inline.children or ():
            if child.type not in ("text", "softbreak"):
                unsupported.add(child.type)
                counts[kinds.get(child.type, child.type)] += 1
        text = normalize(inline.content)
        if not text:
            unsupported.add("empty_block")
        blocks.append({"kind": kind, "level": int(token.tag[1:]) if kind == "heading" else None,
                       "text": text})
        counts[kind] += 1
    # html=False escapes HTML rather than emitting an html token; detect it explicitly.
    if re.search(r"<\/?[A-Za-z][^>]*>", story.markdown):
        unsupported.add("raw_html")
        counts["html"] += 1
    normalized = "\n\n".join(block["text"] for block in blocks)
    return {"source": "In/01_01_2040.md", "sourceHash": story.source_hash,
            "characterCount": len(story.markdown),
            "normalizedCharacterCount": len(normalized),
            "fingerprint": fingerprint(normalized),
            "paragraphCount": counts["paragraph"], "headingCount": counts["heading"],
            "features": dict(sorted(counts.items())),
            "unsupported": sorted(unsupported), "blocks": blocks}


def render(root: Path = ROOT) -> str:
    story = load_story(root / "In")
    data = analyze(story)
    return "/* Generated from In/ through publish_to_all.story.load_story. */\n" + \
        "globalThis.PublishToAllBody = Object.freeze(" + \
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ");\n"


if __name__ == "__main__":
    target = ROOT / "chrome-extension/story-body.js"
    target.write_text(render(), encoding="utf-8")
    print(f"Expected body source: In/01_01_2040.md")
    data = analyze(load_story(ROOT / "In"))
    print(f"Expected body character count: {data['characterCount']}")
    print(f"Expected body normalized character count: {data['normalizedCharacterCount']}")
    print(f"Expected body fingerprint: {data['fingerprint']}")
    print(f"Expected body paragraph count: {data['paragraphCount']}")
    print(f"Markdown features: {data['features']}")
    print(f"Unsupported structures: {data['unsupported']}")
