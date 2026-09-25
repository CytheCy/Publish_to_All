from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from publish_to_all.cli import main


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "In").mkdir()
    (tmp_path / "In/date.md").write_text("---\ntitle: The Last Tree\n---\nHello world.")
    (tmp_path / "config.toml").write_text('[substack]\npublication_url = "https://example.substack.com"')
    Image.new("RGB", (4, 4)).save(tmp_path / "In/date.png")
    return tmp_path


def test_check_success(project, capsys):
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert main(["check"]) == 0
    output = capsys.readouterr()
    for text in ("Story check passed\n", "Story: date.md", "Title: The Last Tree", "Image: date.png",
                 "Words: 2", "Content hash:", "Substack configuration: OK", "No files were modified.", "Nothing was published."):
        assert text in output.out
    assert not output.err
    assert before == {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}


@pytest.mark.parametrize("command", ["check", "preview"])
@pytest.mark.parametrize("failure, message", [
    ("missing_input", "Input directory is missing"),
    ("no_story", "No Markdown story"),
    ("multiple", "Multiple story files"),
    ("yaml", "Invalid YAML"),
    ("title", "title is required"),
    ("config", "valid TOML"),
    ("url", "HTTPS publication root"),
])
def test_errors(project, capsys, command, failure, message):
    source = project / "In/date.md"
    if failure == "missing_input":
        (project / "In").rename(project / "Elsewhere")
    elif failure == "no_story":
        source.unlink()
    elif failure == "multiple":
        (project / "In/other.md").write_text("Other")
    elif failure == "yaml":
        source.write_text("---\ntitle: [\n---\nBody")
    elif failure == "title":
        source.write_text("Body")
    elif failure == "config":
        (project / "config.toml").write_text("[broken")
    else:
        (project / "config.toml").write_text('[substack]\npublication_url = "http://example.com"')
    assert main([command]) == 1
    output = capsys.readouterr()
    assert message in output.err
    assert "Traceback" not in output.err
    assert not output.out


@pytest.mark.parametrize("command", ["check", "preview"])
def test_unreadable_story(project, monkeypatch, capsys, command):
    def denied(self):
        raise PermissionError("denied")
    monkeypatch.setattr(Path, "read_bytes", denied)
    assert main([command]) == 1
    assert "Cannot read story as UTF-8" in capsys.readouterr().err


def test_preview_optional_metadata(project, capsys):
    assert main(["preview"]) == 0
    output = capsys.readouterr().out
    for field in ("Subtitle", "Description", "Series", "Episode", "Tags"):
        assert f"{field}:" not in output
    source = project / "In/date.md"
    source.write_text("---\ntitle: The Last Tree\nsubtitle: A Horizons Story\ndescription: A description\nseries: Horizons\nepisode: 4\ntags: [science fiction, emerging technology]\n---\nHello world.")
    assert main(["preview"]) == 0
    output = capsys.readouterr().out
    for text in ("Story detected", "File: date.md", "Title: The Last Tree", "Subtitle: A Horizons Story",
                 "Description: A description", "Series: Horizons", "Episode: 4",
                 "Tags: science fiction, emerging technology", "Words: 2", "Image: date.png",
                 "Publication: https://example.substack.com", "Status: Ready", "Content hash:\n", "Nothing was published."):
        assert text in output


@pytest.mark.parametrize("command", ["check", "preview"])
def test_offline_without_cover(project, capsys, command):
    (project / "config.toml").unlink()
    (project / "In/date.png").unlink()
    assert main([command]) == 0
    output = capsys.readouterr().out
    assert "Image: none" in output
    assert "Warning: No matching image was found." in output
    assert "Not configured" in output
    if command == "check":
        assert "passed with warnings" in output


def test_console_entry_point(project):
    executable = str(Path(sys.executable).parent / "publish-to-all")
    for args, code in [(["--help"], 0), (["check"], 0), (["preview"], 0), (["publish"], 2)]:
        result = subprocess.run([executable, *args], capture_output=True, text=True)
        assert result.returncode == code
        if args == ["--help"]:
            assert "check" in result.stdout and "preview" in result.stdout
    (project / "In/date.md").write_text("No title")
    assert subprocess.run([executable, "check"], capture_output=True).returncode == 1
