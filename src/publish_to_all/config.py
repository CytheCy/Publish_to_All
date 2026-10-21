"""Read local TOML configuration; never persist credentials."""

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from .errors import ConfigurationError


@dataclass(frozen=True)
class Config:
    publication_url: str | None = None
    website_root: Path | None = None

    def require_substack(self) -> str:
        if self.publication_url is None:
            raise ConfigurationError(
                "Set [substack].publication_url in config.toml before using Substack browser commands."
            )
        return self.publication_url


def load_config(path: Path) -> Config:
    """A missing config is allowed for offline validation and preview."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return Config()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # Do not echo TOML contents: an incorrectly placed secret may be present.
        raise ConfigurationError(f"Cannot read valid TOML configuration: {path}") from exc
    if set(data) - {"substack", "website"}:
        raise ConfigurationError("Only the [substack] and [website] configuration sections are supported.")
    website = data.get("website", {})
    if not isinstance(website, dict) or set(website) - {"site_root"}:
        raise ConfigurationError("[website] accepts only site_root.")
    website_root = website.get("site_root")
    if website_root is not None and (not isinstance(website_root, str) or not website_root
                                     or not Path(website_root).is_absolute()):
        raise ConfigurationError("[website].site_root must be an absolute directory path.")
    website_path = Path(website_root) if website_root is not None else None
    section = data.get("substack", {})
    if not isinstance(section, dict) or set(section) - {"publication_url"}:
        raise ConfigurationError("[substack] accepts only publication_url; do not put credentials here.")
    if "publication_url" not in section:
        return Config(website_root=website_path)
    value = section["publication_url"]
    message = "publication_url must be an HTTPS publication root URL without credentials, query, or fragment."
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ConfigurationError(message)
    try:
        url = urlsplit(value)
        valid = (
            url.scheme == "https" and url.hostname and "." in url.hostname
            and url.username is None and url.password is None
            and url.port is None and url.path in ("", "/")
            and not url.query and not url.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError(message)
    return Config(value.rstrip("/"), website_path)


@dataclass(frozen=True)
class RuntimePaths:
    """External runtime paths; calculating them creates no files."""

    data: Path
    database: Path
    state: Path
    browser_profile: Path
    logs: Path
    diagnostics: Path

    @property
    def substack_browser_profile(self) -> Path:
        return self.browser_profile / "substack"


def runtime_paths(project_root: Path) -> RuntimePaths:
    def external_base(variable: str, fallback: str) -> Path:
        base = Path(os.environ.get(variable) or str(Path.home() / fallback))
        if not base.is_absolute():
            raise ConfigurationError(f"{variable} must be an absolute path.")
        return (base / "publish-to-all").resolve()

    data = external_base("XDG_DATA_HOME", ".local/share")
    state = external_base("XDG_STATE_HOME", ".local/state")
    paths = RuntimePaths(data, data / "publications.sqlite3", state,
                         state / "browser-profile", state / "logs", state / "diagnostics")
    for path in (paths.data, paths.database, paths.state, paths.browser_profile,
                 paths.substack_browser_profile, paths.logs, paths.diagnostics):
        if path.resolve().is_relative_to(project_root.resolve()):
            raise ConfigurationError("Runtime state and browser sessions must live outside the project.")
    return paths
