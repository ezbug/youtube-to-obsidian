import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "video-note-v2"
SCRIPT = ROOT / "scripts" / "render_html.py"
sys.path.insert(0, str(ROOT / "scripts"))

import render_html
from render_html import render_video_note


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def render_email(note):
    return render_video_note(note, base_dir=FIXTURES, profile="email")


def test_email_cli_creates_parent_embeds_media_and_is_deterministic(tmp_path):
    note_dir = tmp_path / "input"
    (note_dir / "media").mkdir(parents=True)
    (note_dir / "media" / "frame.jpg").write_bytes(
        (FIXTURES / "media" / "frame.jpg").read_bytes()
    )
    note_path = note_dir / "note.json"
    note_path.write_text(json.dumps(fixture("full.json")), encoding="utf-8")
    output = tmp_path / "nested" / "email" / "note.html"

    first = run_cli("--note", note_path, "--output", output, "--profile", "email")

    assert first.returncode == 0
    assert first.stderr == ""
    contents = output.read_bytes()
    assert b"data:image/jpeg;base64," in contents
    assert run_cli("--note", note_path, "--output", output, "--profile", "email").returncode == 0
    assert output.read_bytes() == contents


def test_email_is_static_inline_and_has_a_table_shell():
    document = render_email(fixture("all-blocks.json"))
    lower = document.casefold()

    for forbidden in (
        "<script",
        "<style",
        "--",
        "position:sticky",
        "<button",
        "<details",
        "<summary",
        "onclick=",
        "onload=",
        "javascript:",
        " hidden",
        "data-filter",
        "data-copy",
        'rel="stylesheet"',
    ):
        assert forbidden not in lower
    assert '<table role="presentation"' in document
    assert "max-width:760px" in document
    assert "style=\"" in document
    assert not re.search(r'<(?:img|script|link)\b[^>]*(?:src|href)="https?://', document, re.I)


def test_email_expands_accordions_and_flattens_cards_in_category_order():
    note = fixture("all-blocks.json")
    grid = next(
        block for block in note["sections"][0]["blocks"] if block["type"] == "feature_grid"
    )
    grid["cards"].extend(
        [
            {"id": "card-2", "category": "analysis", "title": "Second", "description": "Second card."},
            {"id": "card-3", "category": "setup", "title": "Third", "description": "Third card."},
        ]
    )

    document = render_email(note)

    assert "Details" in document
    assert "Nested text." in document
    assert "Nested item" in document
    assert "筛选" not in document
    setup = document.index(">setup</h3>")
    first_card = document.index(">Card</h4>")
    third_card = document.index(">Third</h4>")
    analysis = document.index(">analysis</h3>")
    second_card = document.index(">Second</h4>")
    assert setup < first_card < third_card < analysis < second_card


def test_email_preserves_every_block_and_media_mime_with_nonempty_alt():
    document = render_email(fixture("all-blocks.json"))
    for content in (
        "A paragraph.", "A key point.", "First", "Name", "One", "A frame",
        "What the frame shows.", "Card", "A filterable card.", "print(&#x27;safe&#x27;)",
        "Start → End: next", "Details", "Nested text.", "Nested item", "Last.",
    ):
        assert content in document
    assert re.search(r'<img [^>]*alt="[^"]+"', document)

    for name, mime in (("jpeg.json", "image/jpeg"), ("png.json", "image/png"), ("webp.json", "image/webp")):
        assert f"data:{mime};base64," in render_email(fixture(name))


@pytest.mark.parametrize(
    ("kind", "color"),
    [
        ("key", "#fffbed"),
        ("source", "#edfafa"),
        ("recommendation", "#ecfdf5"),
        ("inference", "#f5f0ff"),
        ("notice", "#fff4e8"),
    ],
)
def test_email_preserves_semantic_colors_safe_links_and_escaping(kind, color):
    note = fixture("all-blocks.json")
    note["sections"][0]["blocks"][1]["kind"] = kind
    note["sections"][0]["blocks"][0]["text"] = '<img src=x onerror="alert(1)">'
    document = render_email(note)

    assert f"background:{color}" in document
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in document
    assert '<img src=x onerror="alert(1)">' not in document

    unsafe = fixture("all-blocks.json")
    grid = next(block for block in unsafe["sections"][0]["blocks"] if block["type"] == "feature_grid")
    grid["cards"][0]["link"] = "javascript:alert(1)"
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        render_email(unsafe)


def test_email_cli_input_output_and_unexpected_errors_are_atomic(tmp_path, monkeypatch, capsys):
    output = tmp_path / "existing.html"
    output.write_text("KEEP", encoding="utf-8")
    missing = render_html.main([
        "--note", str(FIXTURES / "missing-image.json"), "--output", str(output), "--profile", "email",
    ])
    assert missing == 2
    assert output.read_text(encoding="utf-8") == "KEEP"

    monkeypatch.setattr(render_html.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("denied")))
    output_error = render_html.main([
        "--note", str(FIXTURES / "minimal.json"), "--output", str(output), "--profile", "email",
    ])
    assert output_error == 3
    assert output.read_text(encoding="utf-8") == "KEEP"
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []

    monkeypatch.setattr(
        render_html,
        "render_video_note",
        lambda note, *, base_dir=None, profile="web": (_ for _ in ()).throw(RuntimeError("boom")),
    )
    unexpected = render_html.main([
        "--note", str(FIXTURES / "minimal.json"), "--output", str(tmp_path / "never.html"), "--profile", "email",
    ])
    assert unexpected == 4
    stderr = capsys.readouterr().err
    assert "input error:" in stderr
    assert "output I/O error:" in stderr
    assert "unexpected renderer error: boom" in stderr


def test_web_profile_remains_the_default_and_explicit_web_is_identical():
    note = fixture("full.json")
    assert render_video_note(note, base_dir=FIXTURES) == render_video_note(
        note, base_dir=FIXTURES, profile="web"
    )
