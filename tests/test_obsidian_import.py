from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from import_html_to_obsidian import (
    ImportFailure,
    ObsidianCLI,
    build_note_content,
    chunk_text,
    import_html,
    validate_vault_name,
    validate_vault_relative_path,
)


def test_build_note_content_contains_properties_and_finder_index(tmp_path: Path):
    archive_dir = tmp_path / "HTML 归档目录"
    html_path = archive_dir / "视频 笔记.html"
    html_path.parent.mkdir()
    html_path.write_text("<html></html>", encoding="utf-8")

    content = build_note_content(
        title="视频笔记",
        source="https://www.youtube.com/watch?v=abc123",
        source_id="abc123",
        platform="youtube",
        html_path=html_path,
        source_sha256="a" * 64,
        created="2026-08-22",
        markdown="## 先建立工作流地图\n\n![图](data:image/png;base64,AAAA)",
    )

    assert content.startswith("---\n")
    assert "title: \"视频笔记\"" in content
    assert 'source_id: "abc123"' in content
    assert "source_html_sha256: " + "a" * 64 in content
    assert "html_archive: " + json.dumps(str(archive_dir.resolve()), ensure_ascii=False) in content
    assert archive_dir.resolve().as_uri() + "/" in content
    assert "HTML 文件：`视频 笔记.html`" in content
    assert "## 先建立工作流地图" in content
    assert "data:image/png;base64,AAAA" in content


def test_validate_vault_relative_path_rejects_absolute_and_escape_paths():
    assert validate_vault_relative_path("Origin/video") == "Origin/video"
    with pytest.raises(ImportFailure):
        validate_vault_relative_path("/absolute/private/Vault")
    with pytest.raises(ImportFailure):
        validate_vault_relative_path("Origin/../private")
    with pytest.raises(ImportFailure):
        validate_vault_relative_path(".")
    with pytest.raises(ImportFailure):
        validate_vault_relative_path("")


def test_validate_vault_name_rejects_absolute_and_escape_paths():
    assert validate_vault_name("bugbg") == "bugbg"
    with pytest.raises(ImportFailure):
        validate_vault_name("/absolute/private/Vault")
    with pytest.raises(ImportFailure):
        validate_vault_name("../Vault")
    with pytest.raises(ImportFailure):
        validate_vault_name("Vault\\nested")


def test_chunk_text_preserves_unicode_and_respects_byte_limit():
    original = "中文内容" * 10000 + "\n![图](data:image/png;base64,AAAA)"
    chunks = list(chunk_text(original, max_bytes=64 * 1024))

    assert "".join(chunks) == original
    assert all(len(chunk.encode("utf-8")) <= 64 * 1024 for chunk in chunks)


def test_default_chunks_stay_below_obsidian_cli_utf8_transport_boundary():
    original = "中文内容" * 10000
    chunks = list(chunk_text(original))

    assert "".join(chunks) == original
    assert max(len(chunk.encode("utf-8")) for chunk in chunks) <= 4 * 1024


def test_build_note_content_rejects_untrusted_platform():
    with pytest.raises(ImportFailure):
        build_note_content(
            title="视频笔记",
            source="https://example.com/video",
            source_id="id",
            platform="other",
            html_path=Path("/tmp/video.html"),
            source_sha256="a" * 64,
            created="2026-08-22",
            markdown="正文",
        )


def test_obsidian_cli_treats_success_code_error_output_as_missing_file(monkeypatch):
    def fake_run(*args, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": "Error: File not found\n", "stderr": ""})()

    monkeypatch.setattr("import_html_to_obsidian.subprocess.run", fake_run)
    cli = ObsidianCLI("obsidian", "bugbg")

    assert cli.exists("Origin/video/missing.md") is False


def test_obsidian_cli_rejects_non_missing_error_output(monkeypatch):
    def fake_run(*args, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": "Error: Permission denied\n", "stderr": ""})()

    monkeypatch.setattr("import_html_to_obsidian.subprocess.run", fake_run)
    cli = ObsidianCLI("obsidian", "bugbg")

    with pytest.raises(ImportFailure, match="cannot determine"):
        cli.exists("Origin/video/note.md")


def test_apply_rolls_back_stage_when_move_fails(monkeypatch, tmp_path: Path):
    html_path = tmp_path / "video.html"
    html_path.write_text("<html><main>source</main></html>", encoding="utf-8")
    calls = []

    class FakeCLI:
        def __init__(self, binary, vault):
            self.files = {}

        def exists(self, path):
            return path in self.files

        def create(self, path, content):
            calls.append(("create", path))
            self.files[path] = content

        def append(self, path, content):
            self.files[path] += content

        def read(self, path):
            return self.files[path]

        def move(self, source, target):
            calls.append(("move", source, target))
            raise ImportFailure("simulated move failure")

        def delete(self, path):
            calls.append(("delete", path))
            self.files.pop(path, None)

        def invoke(self, *parts, **kwargs):
            return type("Result", (), {"stdout": "title: 视频笔记\nsource: https://example.com\n"})()

    monkeypatch.setattr("import_html_to_obsidian.ObsidianCLI", FakeCLI)
    monkeypatch.setattr(
        "import_html_to_obsidian.extract_markdown",
        lambda *args, **kwargs: {"content": "正文", "title": "视频笔记", "wordCount": 1},
    )
    args = Namespace(
        html=str(html_path),
        vault="bugbg",
        folder="Origin/video",
        source="https://www.youtube.com/watch?v=abc123",
        source_id="abc123",
        platform="youtube",
        title="视频笔记",
        expected_html_sha256=None,
        preview=None,
        created="2026-08-22",
        node_bin="node",
        obsidian_bin="obsidian",
        apply=True,
        dry_run=False,
    )

    with pytest.raises(ImportFailure, match="simulated move failure"):
        import_html(args)
    assert any(call[0] == "delete" for call in calls)


def test_dry_run_never_constructs_obsidian_cli(monkeypatch, tmp_path: Path):
    html_path = tmp_path / "video.html"
    html_path.write_text("<html><main>source</main></html>", encoding="utf-8")
    preview = tmp_path / "preview.md"
    monkeypatch.setattr(
        "import_html_to_obsidian.extract_markdown",
        lambda *args, **kwargs: {"content": "正文", "title": "视频笔记", "wordCount": 1},
    )

    def fail_if_constructed(*args, **kwargs):
        raise AssertionError("dry-run must not construct ObsidianCLI")

    monkeypatch.setattr("import_html_to_obsidian.ObsidianCLI", fail_if_constructed)
    args = Namespace(
        html=str(html_path),
        vault="bugbg",
        folder="Origin/video",
        source="https://www.youtube.com/watch?v=abc123",
        source_id="abc123",
        platform="youtube",
        title="视频笔记",
        expected_html_sha256=None,
        preview=str(preview),
        created="2026-08-22",
        node_bin="node",
        obsidian_bin="obsidian",
        apply=False,
        dry_run=True,
    )

    audit = import_html(args)
    assert audit["vault_write"] is False
    assert preview.read_text(encoding="utf-8").startswith("---\n")


def test_apply_refuses_existing_target_before_create(monkeypatch, tmp_path: Path):
    html_path = tmp_path / "video.html"
    html_path.write_text("<html><main>source</main></html>", encoding="utf-8")

    class FakeCLI:
        created = False

        def __init__(self, binary, vault):
            pass

        def exists(self, path):
            return path == "Origin/video/视频笔记.md"

        def create(self, path, content):
            self.created = True

    monkeypatch.setattr("import_html_to_obsidian.ObsidianCLI", FakeCLI)
    monkeypatch.setattr(
        "import_html_to_obsidian.extract_markdown",
        lambda *args, **kwargs: {"content": "正文", "title": "视频笔记", "wordCount": 1},
    )
    args = Namespace(
        html=str(html_path),
        vault="bugbg",
        folder="Origin/video",
        source="https://www.youtube.com/watch?v=abc123",
        source_id="abc123",
        platform="youtube",
        title="视频笔记",
        expected_html_sha256=None,
        preview=None,
        created="2026-08-22",
        node_bin="node",
        obsidian_bin="obsidian",
        apply=True,
        dry_run=False,
    )

    with pytest.raises(ImportFailure, match="refusing to overwrite"):
        import_html(args)
    assert FakeCLI.created is False


def test_hash_mismatch_stops_before_extraction(monkeypatch, tmp_path: Path):
    html_path = tmp_path / "video.html"
    html_path.write_text("<html><main>source</main></html>", encoding="utf-8")
    monkeypatch.setattr(
        "import_html_to_obsidian.extract_markdown",
        lambda *args, **kwargs: pytest.fail("extraction must not run after hash mismatch"),
    )
    args = Namespace(
        html=str(html_path),
        vault="bugbg",
        folder="Origin/video",
        source="https://www.youtube.com/watch?v=abc123",
        source_id="abc123",
        platform="youtube",
        title="视频笔记",
        expected_html_sha256="0" * 64,
        preview=None,
        created="2026-08-22",
        node_bin="node",
        obsidian_bin="obsidian",
        apply=False,
        dry_run=True,
    )

    with pytest.raises(ImportFailure, match="hash mismatch"):
        import_html(args)


def test_defuddle_adapter_extracts_main_and_preserves_data_image():
    fixture = ROOT / "tests" / "fixtures" / "obsidian-import" / "main.html"
    adapter = ROOT / "scripts" / "defuddle_adapter.mjs"
    result = subprocess.run(
        ["node", str(adapter), str(fixture)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "正文标题" in payload["content"]
    assert "navigation must not be extracted" not in payload["content"]
    assert "data:image/png;base64,AAAA" in payload["content"]
    assert payload["imageCount"] == 1
