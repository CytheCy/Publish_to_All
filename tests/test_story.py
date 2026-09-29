import hashlib

from PIL import Image
import pytest

from publish_to_all.errors import StoryError
from publish_to_all.story import content_hash, discover_inputs, load_story, parse_front_matter


def write_story(tmp_path, text='---\ntitle: Test Story\n---\nHello world.'):
    (tmp_path / "story.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_minimal_story_without_image(tmp_path):
    story = load_story(write_story(tmp_path))
    assert story.metadata.title == "Test Story"
    assert story.metadata.subtitle is None
    assert story.metadata.tags == ()
    assert story.word_count == 2
    assert story.image is None
    assert "No matching image" in story.warnings[0]


def test_full_metadata():
    metadata, body = parse_front_matter('''---
title: The Last Tree
subtitle: A story
description: A description
series: Horizons
episode: 4
tags:
  - science fiction
  - emerging technology
audio_id: ignored-extension
---
The body.
''')
    assert metadata.title == "The Last Tree"
    assert metadata.subtitle == "A story"
    assert metadata.description == "A description"
    assert metadata.series == "Horizons"
    assert metadata.episode == 4
    assert metadata.tags == ("science fiction", "emerging technology")
    assert body == "The body.\n"


@pytest.mark.parametrize("text, message", [
    ("Plain text", "title"), ("---\nsubtitle: hi\n---\nBody", "title"),
    ('---\ntitle: " "\n---\nBody', "title"),
    ("---\ntitle: 123\n---\nBody", "title"),
    ("---\ntitle: hi\nBody", "closing"),
    ("---\ntitle: [\n---\nBody", "Invalid YAML"),
    ("---\n- title\n---\nBody", "mapping"),
    ("---\ntitle: hi\ntitle: other\n---\nBody", "unique"),
    ("---\ntitle: hi\nepisode: true\n---\nBody", "episode"),
    ("---\ntitle: hi\nepisode: 0\n---\nBody", "episode"),
    ("---\ntitle: hi\nsubtitle: []\n---\nBody", "subtitle"),
    ("---\ntitle: hi\ntags: fiction\n---\nBody", "tags"),
    ('---\ntitle: hi\ntags: [""]\n---\nBody', "tags"),
    ("---\ntitle: !!python/object:bad {}\n---\nBody", "Invalid YAML"),
])
def test_invalid_front_matter(text, message):
    with pytest.raises(StoryError, match=message):
        parse_front_matter(text)


def test_markdown_structure(tmp_path):
    story = load_story(write_story(tmp_path, '''---
title: Formatting
---
# Heading

A **bold** and *italic* paragraph with a [link](https://example.com).

> Quoted text

1. First
2. Second

- One
- Two

---

<script>alert('bad')</script>
'''))
    for fragment in ("<h1>", "<strong>bold</strong>", "<em>italic</em>",
                     '<a href="https://example.com">', "<blockquote>", "<ol>",
                     "<ul>", "<hr", "<p>"):
        assert fragment in story.html
    assert "<script>" not in story.html
    assert any(token.type == "heading_open" for token in story.tokens)


def test_arbitrary_story_name(tmp_path):
    source = tmp_path / "01_01_2040.md"
    source.write_text("Story")
    assert discover_inputs(tmp_path).source == source


def test_valid_image_and_inputs_unchanged(tmp_path):
    write_story(tmp_path)
    Image.new("RGB", (4, 4)).save(tmp_path / "story.png")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    story = load_story(tmp_path)
    assert story.image == tmp_path / "story.png"
    assert not story.warnings
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert story.source_hash == hashlib.sha256(before["story.md"]).hexdigest()


def test_social_png_is_preferred_only_for_substack(tmp_path):
    write_story(tmp_path)
    general = tmp_path / "story.png"
    social = tmp_path / "Social.png"
    Image.new("RGB", (800, 450), "blue").save(general)
    Image.new("RGBA", (1200, 630), "red").save(social)

    story = load_story(tmp_path)

    assert story.image == general
    assert story.substack_image == social
    assert not story.substack_image_fallback
    assert (story.substack_image_info.width, story.substack_image_info.height) == (1200, 630)
    assert story.substack_image_info.mime_type == "image/png"
    assert story.substack_image_info.size_bytes == social.stat().st_size


def test_substack_image_falls_back_only_to_matching_general_image(tmp_path):
    write_story(tmp_path)
    general = tmp_path / "story.png"
    Image.new("RGB", (800, 450), "blue").save(general)
    Image.new("RGB", (1200, 630), "red").save(tmp_path / "unrelated.png")

    story = load_story(tmp_path)

    assert story.image == general
    assert story.substack_image == general
    assert story.substack_image_fallback


@pytest.mark.parametrize("kind", ["garbage", "jpeg", "truncated"])
def test_invalid_social_png_is_rejected(tmp_path, kind):
    write_story(tmp_path)
    social = tmp_path / "Social.png"
    if kind == "jpeg":
        Image.new("RGB", (1200, 630)).save(social, format="JPEG")
    elif kind == "truncated":
        Image.new("RGB", (1200, 630)).save(social)
        social.write_bytes(social.read_bytes()[:40])
    else:
        social.write_bytes(b"not a PNG")
    with pytest.raises(StoryError):
        load_story(tmp_path)


def test_unusually_small_social_png_warns(tmp_path):
    write_story(tmp_path)
    Image.new("RGB", (20, 20)).save(tmp_path / "Social.png")
    story = load_story(tmp_path)
    assert any("unusually small (20x20)" in warning for warning in story.warnings)


@pytest.mark.parametrize("kind", ["garbage", "jpeg", "directory", "truncated"])
def test_invalid_image(tmp_path, kind):
    write_story(tmp_path)
    image = tmp_path / "story.png"
    if kind == "directory":
        image.mkdir()
    elif kind == "jpeg":
        Image.new("RGB", (4, 4)).save(image, format="JPEG")
    elif kind == "truncated":
        Image.new("RGB", (4, 4)).save(image)
        image.write_bytes(image.read_bytes()[:40])
    else:
        image.write_bytes(b"not a PNG")
    with pytest.raises(StoryError):
        load_story(tmp_path)


def test_hash_tracks_content():
    assert content_hash(b"same") == content_hash(b"same")
    assert content_hash(b"same") != content_hash(b"changed")
    assert content_hash(b"title: A\nBody") != content_hash(b"title: B\nBody")
    assert content_hash(b"a\n") != content_hash(b"a\r\n")


def test_empty_body(tmp_path):
    with pytest.raises(StoryError, match="body is empty"):
        load_story(write_story(tmp_path, "---\ntitle: Test\n---\n\n"))


def test_invalid_utf8(tmp_path):
    (tmp_path / "story.md").write_bytes(b"\xff")
    with pytest.raises(StoryError, match="UTF-8"):
        load_story(tmp_path)


def test_bom_and_crlf(tmp_path):
    source = b'\xef\xbb\xbf---\r\ntitle: Test\r\n---\r\nBody\r\n'
    (tmp_path / "story.md").write_bytes(source)
    story = load_story(tmp_path)
    assert story.markdown == "Body\r\n"
    assert story.source_hash == content_hash(source)


@pytest.mark.parametrize("extension", ["png", "jpg", "jpeg", "webp", "PNG", "JPG"])
def test_same_basename_images(tmp_path, extension):
    write_story(tmp_path)
    (tmp_path / "story.md").rename(tmp_path / "any-story-name.md")
    image = tmp_path / f"any-story-name.{extension}"
    Image.new("RGB", (4, 4)).save(image)
    assert load_story(tmp_path).image == image


def test_unrelated_image_and_nested_story_ignored(tmp_path):
    write_story(tmp_path)
    (tmp_path / "other.png").write_bytes(b"irrelevant")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/other.md").write_text("irrelevant")
    assert load_story(tmp_path).image is None


def test_multiple_stories(tmp_path):
    write_story(tmp_path)
    (tmp_path / "other.md").write_text("Other")
    with pytest.raises(StoryError, match="Multiple story files") as error:
        discover_inputs(tmp_path)
    assert "other.md\nstory.md" in str(error.value)
    assert "Leave only one story" in str(error.value)


def test_multiple_images(tmp_path):
    write_story(tmp_path)
    for extension in ("png", "jpg"):
        Image.new("RGB", (4, 4)).save(tmp_path / f"story.{extension}")
    with pytest.raises(StoryError, match="Multiple matching images"):
        load_story(tmp_path)


@pytest.mark.parametrize("body, expected", [
    ("Hello, world!", 2),
    ("Don't stop—keep well-being and café’s joy.", 7),
    ("pre**fix**\nnext  \nline", 3),
    ("# Heading\n\n[Two words](https://example.org/many/words) ![excluded alt](cover.png)", 3),
    ("> Quote\n\n- First\n- Second\n\n---", 3),
    ("Use `some code`.\n\n```python\nmore code\n```", 5),
])
def test_word_count_body_only(tmp_path, body, expected):
    story = load_story(write_story(tmp_path, "---\ntitle: Many extra metadata words\n---\n" + body))
    assert story.word_count == expected
