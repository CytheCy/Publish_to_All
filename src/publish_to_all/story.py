"""Discover, parse, and validate canonical inputs with read-only operations."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import warnings

from markdown_it import MarkdownIt
from markdown_it.token import Token
from PIL import Image, UnidentifiedImageError
import yaml

from .errors import StoryError


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject ambiguous metadata rather than silently replacing duplicate keys."""


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise StoryError("Front matter requires unique string field names.")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class Metadata:
    title: str
    subtitle: str | None = None
    description: str | None = None
    series: str | None = None
    episode: int | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Inputs:
    source: Path
    image: Path | None


@dataclass(frozen=True)
class Story:
    source: Path
    metadata: Metadata
    markdown: str
    tokens: tuple[Token, ...]
    html: str
    source_hash: str
    word_count: int
    image: Path | None
    warnings: tuple[str, ...]


IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


def discover_inputs(input_dir: Path) -> Inputs:
    """Select one direct Markdown child and its unambiguous same-stem cover."""
    try:
        entries = sorted(input_dir.iterdir())
        sources = [p for p in entries if p.suffix.lower() == ".md" and p.is_file()]
        if not sources:
            raise StoryError(f"No Markdown story file found in {input_dir}/.")
        if len(sources) > 1:
            names = "\n".join(p.name for p in sources)
            raise StoryError(
                f"Multiple story files found in {input_dir}/.\n\n{names}\n\n"
                f"Leave only one story in {input_dir}/ before publishing."
            )
        source = sources[0]
        images = [p for p in entries if p.stem == source.stem and p.suffix.lower() in IMAGE_FORMATS]
        if len(images) > 1:
            names = "\n".join(p.name for p in images)
            raise StoryError(f"Multiple matching images found for {source.name}:\n\n{names}\n\nLeave only one matching image.")
        if images and not images[0].is_file():
            raise StoryError(f"Image is not a regular file: {images[0]}")
        return Inputs(source, images[0] if images else None)
    except FileNotFoundError as exc:
        raise StoryError(f"Input directory is missing: {input_dir}") from exc
    except OSError as exc:
        raise StoryError(f"Cannot read input directory: {input_dir}") from exc


def content_hash(source: bytes) -> str:
    """Hash exact source bytes, including metadata; independent of file name."""
    return hashlib.sha256(source).hexdigest()


def parse_front_matter(text: str) -> tuple[Metadata, str]:
    lines = text.splitlines(keepends=True)
    data = {}
    body = text
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise StoryError("YAML front matter is missing its closing --- delimiter.")
        try:
            data = yaml.load("".join(lines[1:end]), Loader=_UniqueKeyLoader)
        except (yaml.YAMLError, RecursionError) as exc:
            raise StoryError("Invalid YAML front matter; check field syntax and indentation.") from exc
        if not isinstance(data, dict):
            raise StoryError("Front matter must be a YAML mapping with a title field.")
        body = "".join(lines[end + 1:])
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise StoryError("A non-empty title is required in YAML front matter (title: \"Story Title\").")
    optional = {}
    for field in ("subtitle", "description", "series"):
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            raise StoryError(f"Front-matter {field} must be text when supplied.")
        optional[field] = value.strip() or None if value is not None else None
    episode = data.get("episode")
    if episode is not None and (type(episode) is not int or episode < 1):
        raise StoryError("Front-matter episode must be a positive integer when supplied.")
    tags = data.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise StoryError("Front-matter tags must be a list of non-empty strings.")
    return Metadata(title.strip(), **optional, episode=episode, tags=tuple(t.strip() for t in tags)), body


def validate_image(path: Path) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format != IMAGE_FORMATS.get(path.suffix.lower()):
                    raise StoryError(f"Image format does not match its extension: {path}")
                image.verify()
            with Image.open(path) as image:
                image.load()
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise StoryError(f"Cannot read a valid image: {path}") from exc


def count_words(tokens: list[Token]) -> int:
    """Count body text and code, excluding markup, image alt text and link URLs.

    Inline formatting does not split words; line breaks separate words.
    Apostrophes and internal hyphens are treated as part of a word.
    """
    blocks = []
    for token in tokens:
        if token.type in ("fence", "code_block"):
            blocks.append(token.content)
        if token.type == "inline":
            blocks.append("".join(
                child.content if child.type in ("text", "code_inline") else
                " " if child.type in ("softbreak", "hardbreak", "image") else ""
                for child in token.children or []
            ))
    return len(re.findall(r"\b\w+(?:['’\-]\w+)*\b", " ".join(blocks)))


def load_story(input_dir: Path) -> Story:
    inputs = discover_inputs(input_dir)
    try:
        source = inputs.source.read_bytes()
        text = source.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise StoryError(f"Cannot read story as UTF-8: {inputs.source}") from exc
    metadata, body = parse_front_matter(text)
    # CommonMark covers the requested formatting; raw HTML is escaped.
    parser = MarkdownIt("commonmark", {"html": False})
    tokens = parser.parse(body)
    if not tokens:
        raise StoryError("The Markdown article body is empty.")
    notices = []
    if inputs.image is not None:
        validate_image(inputs.image)
    else:
        notices.append("No matching image was found.")
    return Story(
        inputs.source, metadata, body, tuple(tokens),
        parser.renderer.render(tokens, parser.options, {}), content_hash(source),
        count_words(tokens),
        inputs.image, tuple(notices),
    )
