from pathlib import Path

from PIL import Image
import pytest

from publish_to_all.cli import main
from publish_to_all.website import publish_website, WebsiteError


@pytest.fixture
def website_project(tmp_path, monkeypatch):
    project = tmp_path / "publisher"
    incoming = project / "In"
    incoming.mkdir(parents=True)
    (incoming / "02_2040.md").write_text('---\ntitle: Cracking Cryptocurrency\n---\n# 01 Road Trip\nText.\n')
    Image.new("RGB", (20, 10), "blue").save(incoming / "02_2040.png")
    site = tmp_path / "in20xx"
    (site / "content/read").mkdir(parents=True)
    (site / "layouts/read").mkdir(parents=True)
    (site / "hugo.toml").write_text('title = "In 20xx"\n')
    (site / "layouts/read/single.html").write_text("{{ .Content }}")
    (project / "config.toml").write_text(f'[website]\nsite_root = "{site}"\n')
    monkeypatch.chdir(project)

    def fake_build(root):
        page = root / "read/02_2040/index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text('Cracking Cryptocurrency <img src="/read/02_2040.png">')
        (root / "read/index.html").write_text('Cracking Cryptocurrency <a href="/read/02_2040/">Read</a>')

    monkeypatch.setattr("publish_to_all.website._build_hugo", fake_build)
    return project, site


def test_website_command_copies_and_builds(website_project, capsys):
    project, site = website_project
    source = project / "In/02_2040.md"
    before = source.read_bytes()
    assert main(["website"]) == 0
    assert source.read_bytes() == before
    assert (site / "content/read/02_2040.md").read_bytes() == before
    assert (site / "content/read/02_2040.png").read_bytes() == (project / "In/02_2040.png").read_bytes()
    assert "Path: /read/02_2040/" in capsys.readouterr().out
    assert main(["website"]) == 0


def test_website_requires_replace_for_changed_story(website_project):
    project, site = website_project
    publish_website(project)
    (project / "In/02_2040.md").write_text('---\ntitle: Cracking Cryptocurrency\n---\nRevised.\n')
    with pytest.raises(WebsiteError, match="--replace"):
        publish_website(project)
    publish_website(project, replace=True)
    assert "Revised." in (site / "content/read/02_2040.md").read_text()


def test_website_rejects_wrong_checkout_before_copy(website_project):
    project, site = website_project
    (site / "hugo.toml").unlink()
    with pytest.raises(WebsiteError, match="Not an In 20xx Hugo"):
        publish_website(project)
    assert not (site / "content/read/02_2040.md").exists()
