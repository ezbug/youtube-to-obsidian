from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "archive_html.py"
sys.path.insert(0, str(ROOT / "scripts"))

from archive_html import (
    ArchiveError,
    archive_html,
    normalize_title,
    resolve_archive_dir,
)


def _html(path: Path, text: str = "accepted") -> bytes:
    payload = f"<!doctype html><html><body>{text}</body></html>".encode()
    path.write_bytes(payload)
    return payload


def test_archive_dir_precedence_and_json_config(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"archive_dir": str(tmp_path / "from-config")}),
        encoding="utf-8",
    )

    assert resolve_archive_dir(
        cli_value=str(tmp_path / "from-cli"),
        config_path=str(config),
        environ={"VIDEO_NOTE_ARCHIVE_DIR": str(tmp_path / "from-env")},
    ) == tmp_path / "from-cli"
    assert resolve_archive_dir(
        cli_value=None,
        config_path=str(config),
        environ={"VIDEO_NOTE_ARCHIVE_DIR": str(tmp_path / "from-env")},
    ) == tmp_path / "from-env"
    assert resolve_archive_dir(
        cli_value=None,
        config_path=str(config),
        environ={},
    ) == tmp_path / "from-config"


def test_missing_archive_configuration_fails() -> None:
    with pytest.raises(ArchiveError, match="archive directory is not configured"):
        resolve_archive_dir(cli_value=None, config_path=None, environ={})


def test_archive_uses_representative_title_and_verifies_bytes(tmp_path: Path) -> None:
    source = tmp_path / "build" / "note.html"
    source.parent.mkdir()
    payload = _html(source)

    result = archive_html(
        source,
        tmp_path / "archive",
        "早睡实验：规律作息比单日睡眠更重要",
        source_id="video-test",
    )
    target = Path(str(result["path"]))

    assert target.name == "早睡实验：规律作息比单日睡眠更重要.html".replace(":", " ")
    assert target.read_bytes() == payload
    assert result["reused"] is False

    repeated = archive_html(
        source,
        tmp_path / "archive",
        "早睡实验：规律作息比单日睡眠更重要",
        source_id="video-test",
    )
    assert repeated["path"] == result["path"]
    assert repeated["reused"] is True


def test_collision_uses_source_id_then_counter(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    third = tmp_path / "third.html"
    _html(first, "one")
    _html(second, "two")
    _html(third, "three")

    one = archive_html(first, archive, "同一主题的代表性结论", source_id="abc")
    two = archive_html(second, archive, "同一主题的代表性结论", source_id="abc")
    three = archive_html(third, archive, "同一主题的代表性结论", source_id="abc")

    assert Path(str(one["path"])).name == "同一主题的代表性结论.html"
    assert Path(str(two["path"])).name == "同一主题的代表性结论（abc）.html"
    assert Path(str(three["path"])).name == "同一主题的代表性结论（abc）（2）.html"


def test_email_suffix_and_generic_title_rejection(tmp_path: Path) -> None:
    source = tmp_path / "note.html"
    _html(source)
    result = archive_html(
        source,
        tmp_path / "archive",
        "睡眠规律决定恢复质量",
        profile="email",
    )
    assert Path(str(result["path"])).name == "睡眠规律决定恢复质量—Email.html"

    with pytest.raises(ArchiveError, match="generic title"):
        normalize_title("视频笔记")


def test_cli_reports_machine_readable_success(tmp_path: Path) -> None:
    source = tmp_path / "note.html"
    _html(source)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--html",
            str(source),
            "--archive-dir",
            str(tmp_path / "archive"),
            "--title",
            "可移植归档由调用者决定位置",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert Path(payload["path"]).is_file()
    assert payload["profile"] == "web"
