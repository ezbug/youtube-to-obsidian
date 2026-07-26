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
from render_html import render_document, render_video_note

main = getattr(render_html, "main", None)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def compact_css(document):
    match = re.search(r"<style>(.*?)</style>", document, re.DOTALL)
    assert match
    return re.sub(r"\s+", "", match.group(1))


def emitted_ids(document):
    return re.findall(r'\sid="([^"]+)"', document)


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_render_document_keeps_legacy_caller_and_embeds_media(tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    document = render_document(
        {"title": "测试视频", "bvid": "BV1TEST", "duration": 60, "author": "测试作者"},
        [{
            "title": "核心模组",
            "summary": "这一段说明模组用途。",
            "rows": [["Skyve", "管理模组", "减少冲突"]],
            "cards": [{"image": frame, "label": "00:10", "analysis": "设置界面", "quote": "丢弃证据"}],
        }],
    )
    assert "data:image/jpeg;base64," in document
    assert "核心模组" in document
    assert "<table" in document
    assert "figure-card" in document
    assert "丢弃证据" not in document


def test_web_profile_uses_exact_golden_tokens_and_geometry():
    css = compact_css(render_video_note(fixture("minimal.json"), base_dir=FIXTURES))
    for token in (
        "--bg:#f6f7fb", "--panel:#fff", "--text:#1d2330", "--muted:#667085",
        "--border:#e4e7ec", "--accent:#6558e8", "--accent2:#8b5cf6",
        "--accent-soft:#efedff", "--key:#fff2a8", "--key-border:#e8c948",
        "--blue:#2878f0", "--purple:#7c4dce", "--orange:#e98324",
        "--red:#d64b4b", "--green:#1f9d68", "--cyan:#0f8b99",
        "--shadow:010px28pxrgba(32,37,60,.075)", "--r:18px",
    ):
        assert token in css
    assert 'font-family:-apple-system,BlinkMacSystemFont,"SegoeUI","PingFangSC","HiraginoSansGB","MicrosoftYaHei",sans-serif' in css
    assert ".shell{max-width:1280px" in css
    assert "grid-template-columns:250pxminmax(0,1fr)" in css
    assert "gap:24px;padding:24px" in css
    assert "nav{position:sticky;top:16px" in css
    assert ".hero{border-radius:28px;padding:34px" in css
    assert "background:linear-gradient(135deg,#fff0%,#f2efff60%,#eef8ff100%)" in css
    assert "section{margin-top:24px" in css
    assert "border-radius:var(--r);padding:26px" in css
    assert ".cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}" in css
    assert "@media(max-width:960px)" in css
    assert ".shell{grid-template-columns:1fr;padding:13px}" in css
    assert ".cards{grid-template-columns:1fr1fr}" in css
    assert "@media(max-width:620px)" in css
    assert ".cards{grid-template-columns:1fr}" in css


def test_web_profile_has_landmarks_skip_link_favicon_and_status_cards():
    document = render_video_note(fixture("full.json"), base_dir=FIXTURES)
    assert '<link rel="icon" href="data:image/svg+xml;base64,' in document
    assert document.split("<body>", 1)[1].lstrip().startswith(
        '<a class="skip-link" href="#vn-owned-main">'
    )
    assert '<div class="shell">' in document
    assert '<nav aria-label="视频笔记目录">' in document
    assert '<main id="vn-owned-main" tabindex="-1">' in document
    assert document.count("<h1>") == 1
    assert '<span class="badge">bilibili · BV1Full</span>' in document
    assert '<p class="lead">A complete summary.</p>' in document
    assert document.count('class="status"') == 3
    assert "Author" in document
    assert "61 秒" in document
    section_id = re.search(r'<section id="([^"]+)"', document).group(1)
    assert section_id.startswith("vn-src-")
    assert f'href="#{section_id}"' in document


def test_all_blocks_have_semantics_and_renderer_owned_interactions():
    document = render_video_note(fixture("all-blocks.json"), base_dir=FIXTURES)
    for kind in ("key", "source", "recommendation", "inference", "notice"):
        note = fixture("all-blocks.json")
        note["sections"][0]["blocks"][1]["kind"] = kind
        assert f'class="callout callout-{kind}"' in render_video_note(
            note, base_dir=FIXTURES
        )
    expected = (
        '<div class="table-wrap" tabindex="0"',
        '<div class="feature-toolbar" role="group"',
        '<button type="button" class="filter-btn active" aria-pressed="true"',
        'data-filter="setup"',
        '<span class="tag">setup</span>',
        'class="feature-card" data-category="setup"',
        'class="codebox">',
        '<button type="button" class="copy"',
        'aria-live="polite"',
        "已复制",
        'class="flow" aria-label="流程图：Start → End: next">',
        'class="accordion">',
        'aria-expanded="false"',
        'aria-controls="vn-owned-accordion-panel-',
        'id="vn-owned-accordion-panel-',
        'aria-labelledby="vn-owned-accordion-toggle-',
    )
    for fragment in expected:
        assert fragment in document
    for forbidden in ("innerHTML", "fetch(", "alert(", "console."):
        assert forbidden not in document
    assert not re.search(r'tabindex="[1-9]', document)


@pytest.mark.parametrize(
    ("name", "mime"),
    [("jpeg.json", "image/jpeg"), ("png.json", "image/png"), ("webp.json", "image/webp")],
)
def test_media_is_mime_aware_and_reader_facing(name, mime):
    document = render_video_note(fixture(name), base_dir=FIXTURES)
    assert f"data:{mime};base64," in document
    assert re.search(r'<img [^>]*alt="[^"]+"', document)
    assert 'class="media-headline"' in document
    assert 'class="media-explanation"' in document


def test_media_usage_and_deep_link_are_safe():
    note = fixture("full.json")
    media = note["sections"][0]["blocks"][1]
    media["usage"] = "用于定位设置"
    media["deep_link"] = "https://example.test/watch?t=12&mode=deep"
    document = render_video_note(note, base_dir=FIXTURES)
    assert '<p class="usage">用于定位设置</p>' in document
    assert 'href="https://example.test/watch?t=12&amp;mode=deep"' in document
    assert 'rel="noopener noreferrer"' in document


def test_all_user_text_and_section_ids_are_escaped():
    note = fixture("minimal.json")
    section = note["sections"][0]
    section["id"] = 'unsafe"><script>alert(1)</script>'
    section["title"] = "<em>section</em>"
    section["blocks"][0]["text"] = '<img src=x onerror="alert(2)">'
    note["meta"]["title"] = "<b>title</b>"
    document = render_video_note(note, base_dir=FIXTURES)
    for raw in (
        "<script>alert(1)</script>",
        "<em>section</em>",
        "<img src=x",
        "<b>title</b>",
    ):
        assert raw not in document
    assert "&lt;em&gt;section&lt;/em&gt;" in document
    section_id = re.search(r'<section id="([^"]+)"', document).group(1)
    assert section_id.startswith("vn-src-")
    assert f'href="#{section_id}"' in document
    assert "unsafe&quot;" not in section_id


def test_dom_ids_use_disjoint_namespaces_and_resist_adversarial_collisions():
    note = fixture("all-blocks.json")
    first = note["sections"][0]
    first["id"] = "alpha-code"
    first["blocks"][0]["id"] = "main-content"
    code = next(block for block in first["blocks"] if block["type"] == "code")
    code["id"] = "alpha"
    accordion = next(
        block for block in first["blocks"] if block["type"] == "accordion"
    )
    accordion["id"] = "clash"
    note["sections"][1]["id"] = "clash-panel"

    document = render_video_note(note, base_dir=FIXTURES)
    ids = emitted_ids(document)

    assert ids
    assert len(ids) == len(set(ids))
    assert all(value.startswith(("vn-src-", "vn-owned-")) for value in ids)
    source_ids = re.findall(r'<section id="([^"]+)"', document)
    nav_targets = re.findall(r'<a href="#([^"]+)">', document)
    assert source_ids == nav_targets[-len(source_ids):]
    assert all(value.startswith("vn-src-") for value in source_ids)
    assert 'id="main-content"' not in document
    assert render_video_note(note, base_dir=FIXTURES) == document

    referenced_ids = re.findall(
        r'(?:aria-labelledby|aria-controls|data-copy-target|data-status-target)="([^"]+)"',
        document,
    )
    referenced_ids.append(re.search(r'class="skip-link" href="#([^"]+)"', document).group(1))
    assert set(referenced_ids) <= set(ids)


def test_focus_overflow_media_and_print_rules_are_present():
    css = compact_css(render_video_note(fixture("all-blocks.json"), base_dir=FIXTURES))
    assert "*{box-sizing:border-box;min-width:0}" in css
    assert "img,video,svg,canvas{max-width:100%;height:auto}" in css
    assert ".table-wrap,.codebox{max-width:100%;overflow:auto}" in css
    assert ":focus-visible{outline:3pxsolidvar(--accent);outline-offset:3px}" in css
    assert ".skip-link" in css
    assert "@mediaprint" in css
    for selector in ("nav", ".feature-toolbar", ".copy", ".accordion-toggle"):
        assert selector in css
    assert "break-inside:avoid" in css
    assert ".feature-card[hidden]{display:block!important}" in css


def test_javascript_updates_aria_hidden_and_live_copy_status():
    document = render_video_note(fixture("all-blocks.json"), base_dir=FIXTURES)
    for source in (
        "button.setAttribute('aria-pressed'",
        "card.hidden",
        "button.setAttribute('aria-expanded'",
        "panel.hidden",
        "navigator.clipboard.writeText",
        "textContent = '已复制'",
        "textContent = '复制失败，请手动复制'",
        "data-filter-mode",
        "const showAll",
        "finally {",
        "helper.remove();",
    ):
        assert source in document
    assert "selected !== 'all'" not in document


def test_filter_all_category_cannot_collide_with_renderer_all_control():
    note = fixture("all-blocks.json")
    grid = next(
        block
        for block in note["sections"][0]["blocks"]
        if block["type"] == "feature_grid"
    )
    grid["cards"][0]["category"] = "all"

    document = render_video_note(note, base_dir=FIXTURES)

    assert document.count('data-filter-mode="all"') == 1
    assert document.count('data-filter-mode="category"') == 1
    assert document.count('data-filter="all"') == 1
    assert 'class="feature-card" data-category="all"' in document
    assert 'class="feature-card" data-category="all" hidden' not in document
    assert "button.dataset.filterMode === 'all'" in document


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            b"\x89PNG\r\n\x1a\npayload",
            'note.sections[0].blocks[1][id="full-media"].image signature '
            "does not match JPEG: bad.jpg",
        ),
        (
            b"not-an-image",
            'note.sections[0].blocks[1][id="full-media"].image has unsupported '
            "image signature: bad.jpg",
        ),
    ],
)
def test_media_signature_must_match_extension(tmp_path, payload, expected):
    note = fixture("full.json")
    note["sections"][0]["blocks"][1]["image"] = "bad.jpg"
    (tmp_path / "bad.jpg").write_bytes(payload)

    with pytest.raises(ValueError, match=re.escape(expected)):
        render_video_note(note, base_dir=tmp_path)


def test_cli_success_creates_parent_resolves_media_and_is_deterministic(tmp_path):
    note_dir = tmp_path / "input"
    (note_dir / "media").mkdir(parents=True)
    (note_dir / "media" / "frame.jpg").write_bytes(
        (FIXTURES / "media" / "frame.jpg").read_bytes()
    )
    note_path = note_dir / "note.json"
    note_path.write_text(json.dumps(fixture("full.json")), encoding="utf-8")
    output = tmp_path / "nested" / "web" / "note.html"
    result = run_cli("--note", note_path, "--output", output, "--profile", "web")
    assert result.returncode == 0
    assert result.stderr == ""
    first = output.read_bytes()
    assert b"data:image/jpeg;base64," in first
    assert run_cli("--note", note_path, "--output", output, "--profile", "web").returncode == 0
    assert output.read_bytes() == first


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "missing-image.json",
            'note.sections[0].blocks[0][id="missing-media"].image '
            "does not exist: media/nope.png",
        ),
        ("invalid-url.json", "meta.source_url must use HTTP or HTTPS"),
    ],
)
def test_cli_input_errors_are_precise_and_atomic(tmp_path, name, expected):
    output = tmp_path / "existing.html"
    output.write_text("KEEP", encoding="utf-8")
    result = run_cli(
        "--note", FIXTURES / name, "--output", output, "--profile", "web"
    )
    assert result.returncode == 2
    assert expected in result.stderr
    assert output.read_text(encoding="utf-8") == "KEEP"
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda note: note["meta"].__setitem__("title", "\ud800"),
            "note.meta.title contains invalid Unicode",
        ),
        (
            lambda note: note["sections"][0]["blocks"][0].__setitem__(
                "\udfff", "bad"
            ),
            "note.sections[0].blocks[0].<key[3]> contains invalid Unicode",
        ),
    ],
)
def test_cli_rejects_unpaired_surrogates_atomically(tmp_path, mutate, expected):
    note = fixture("minimal.json")
    mutate(note)
    note_path = tmp_path / "surrogate.json"
    note_path.write_text(json.dumps(note), encoding="utf-8")
    output = tmp_path / "existing.html"
    output.write_text("KEEP", encoding="utf-8")

    result = run_cli("--note", note_path, "--output", output, "--profile", "web")

    assert result.returncode == 2
    assert expected in result.stderr
    result.stderr.encode("utf-8")
    assert output.read_text(encoding="utf-8") == "KEEP"
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []


def test_cli_rejects_bad_json_without_output(tmp_path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    malformed_output = tmp_path / "malformed.html"
    result = run_cli("--note", malformed, "--output", malformed_output, "--profile", "web")
    assert result.returncode == 2
    assert "note JSON" in result.stderr
    assert not malformed_output.exists()


def test_cli_output_io_failure_returns_3_and_cleans_temp(tmp_path, monkeypatch, capsys):
    assert main is not None
    output = tmp_path / "existing.html"
    output.write_text("KEEP", encoding="utf-8")
    monkeypatch.setattr(os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("denied")))
    exit_code = main([
        "--note", str(FIXTURES / "minimal.json"),
        "--output", str(output),
        "--profile", "web",
    ])
    assert exit_code == 3
    assert "output I/O error" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "KEEP"
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []


def test_cli_unexpected_failure_returns_4_without_output(tmp_path, monkeypatch, capsys):
    assert main is not None
    output = tmp_path / "never.html"
    monkeypatch.setattr(
        render_html,
        "render_video_note",
        lambda note, *, base_dir=None, profile="web": (_ for _ in ()).throw(RuntimeError("boom")),
    )
    exit_code = main([
        "--note", str(FIXTURES / "minimal.json"),
        "--output", str(output),
        "--profile", "web",
    ])
    assert exit_code == 4
    assert "unexpected renderer error" in capsys.readouterr().err
    assert not output.exists()


def test_cli_defensively_rejects_unencodable_renderer_output(
    tmp_path, monkeypatch, capsys
):
    assert main is not None
    output = tmp_path / "existing.html"
    output.write_text("KEEP", encoding="utf-8")
    monkeypatch.setattr(
        render_html,
        "render_video_note",
        lambda note, *, base_dir=None, profile="web": "\ud800",
    )

    exit_code = main([
        "--note", str(FIXTURES / "minimal.json"),
        "--output", str(output),
        "--profile", "web",
    ])

    assert exit_code == 4
    assert "unexpected renderer error" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "KEEP"
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []
