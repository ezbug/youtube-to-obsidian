from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_package_contract():
    assert (ROOT / "SKILL.md").is_file()
    assert (ROOT / "README.md").is_file()
    assert (ROOT / "scripts" / "install.sh").is_file()
    assert (ROOT / "scripts" / "doctor.sh").is_file()
    assert (ROOT / "scripts" / "uninstall.sh").is_file()
    frontmatter = (ROOT / "SKILL.md").read_text(encoding="utf-8").splitlines()
    assert frontmatter[0] == "---"
    assert any(line.startswith("name:") for line in frontmatter[:20])
    assert any(line.startswith("description:") for line in frontmatter[:20])


def test_readme_documents_codex_workflow():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required = (
        "Codex",
        "uv sync --frozen",
        "./scripts/install.sh --target codex --mode symlink",
        "scripts/extract_subtitles.py",
        "youtube-transcript-api",
        "yt-dlp",
        ".env.example",
    )
    for item in required:
        assert item in readme


def test_no_machine_specific_paths():
    ignored = {".venv", "node_modules", ".git", "__pycache__"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        if path.name == "doctor.sh":
            continue
        if path.as_posix().endswith(".github/workflows/ci.yml"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "/" + "Users/" not in text
        assert ".openclaw/" + "workspace" not in text
        assert ".claude/" + "mcp-servers" not in text
