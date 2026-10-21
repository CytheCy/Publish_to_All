"""Copy a validated story into the In 20xx Hugo archive and build /read."""

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

from .config import load_config
from .errors import ConfigurationError, PublishToAllError
from .story import load_story


class WebsiteError(PublishToAllError):
    """Website checkout, build, or output is invalid."""


@dataclass(frozen=True)
class WebsiteResult:
    source: Path
    image: Path
    page: Path
    index: Path
    url_path: str


def _copy_if_changed(source: Path, destination: Path) -> None:
    if destination.exists() and destination.read_bytes() == source.read_bytes():
        return
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as temp:
        temporary = Path(temp.name)
        try:
            with source.open("rb") as handle:
                shutil.copyfileobj(handle, temp)
            os.chmod(temporary, source.stat().st_mode & 0o777)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def _build_hugo(site_root: Path) -> None:
    hugo = shutil.which("hugo")
    if hugo:
        command = [hugo, "--quiet"]
    elif shutil.which("podman"):
        command = ["podman", "run", "--rm", "-v", f"{site_root}:/src:Z", "-w", "/src",
                   "docker.io/hugomods/hugo:exts-0.150.0", "hugo", "--quiet"]
    else:
        raise WebsiteError("Hugo or Podman is required to build the website archive.")
    try:
        result = subprocess.run(command, cwd=site_root, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WebsiteError(f"Could not finish the Hugo build: {exc}") from exc
    if result.returncode:
        details = (result.stderr or result.stdout).strip()
        raise WebsiteError(f"Hugo build failed: {details[:2000]}")


def publish_website(project_root: Path, site_root: Path | None = None, *, replace: bool = False) -> WebsiteResult:
    """Update a local checkout only; deployment is managed by the website repo."""
    story = load_story(project_root / "In")
    if story.image is None:
        raise WebsiteError(f"A matching website banner image is required for {story.source.name}.")
    if site_root is None:
        site_root = load_config(project_root / "config.toml").website_root
    if site_root is None:
        raise ConfigurationError("Set [website].site_root in config.toml or pass --site-root.")
    site_root = site_root.expanduser().resolve()
    content = site_root / "content" / "read"
    if not (site_root / "hugo.toml").is_file() or not content.is_dir() or not (site_root / "layouts/read/single.html").is_file():
        raise WebsiteError(f"Not an In 20xx Hugo website checkout: {site_root}")
    if content.resolve() != content or not content.resolve().is_relative_to(site_root):
        raise WebsiteError("Website content/read must be a real directory within the site checkout.")
    markdown = content / story.source.name
    image = content / story.image.name
    for source, destination in ((story.source, markdown), (story.image, image)):
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise WebsiteError(f"Website destination is not a regular file: {destination}")
        if destination.exists() and destination.read_bytes() != source.read_bytes() and not replace:
            raise WebsiteError(f"Website file already differs: {destination}. Use --replace to update this story.")
    try:
        _copy_if_changed(story.source, markdown)
        _copy_if_changed(story.image, image)
    except OSError as exc:
        raise WebsiteError(f"Could not copy story into website checkout: {exc}") from exc
    _build_hugo(site_root)
    url_path = f"/read/{story.source.stem}/"
    page = site_root / "read" / story.source.stem / "index.html"
    index = site_root / "read" / "index.html"
    if not page.is_file() or not index.is_file():
        raise WebsiteError(f"Hugo did not create the expected website pages for {story.source.name}.")
    page_html = page.read_text(encoding="utf-8")
    index_html = index.read_text(encoding="utf-8")
    if (story.metadata.title not in page_html or story.metadata.title not in index_html
            or url_path not in index_html or f"/read/{story.image.name}" not in page_html):
        raise WebsiteError(f"Built website pages do not contain the expected story and banner: {story.source.name}.")
    return WebsiteResult(markdown, image, page, index, url_path)
