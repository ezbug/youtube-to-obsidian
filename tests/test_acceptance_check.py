import copy
import sys
from contextlib import contextmanager
from pathlib import Path
from subprocess import CompletedProcess


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from acceptance_check import (
    COLOR_TOKENS,
    PDF_SETTINGS,
    PINNED_PLAYWRIGHT_COMMAND,
    _copy_matches_source,
    _pdf_page_box_is_a4,
    _reference_defect_status,
    _session,
    _visual_review,
    add_all_category_collision,
    aggregate_candidate_events,
    failed_hard_checks,
    normalize_for_parity,
    sha256_path,
)


def test_color_tokens_match_exact_css_custom_property_values():
    assert COLOR_TOKENS == {
        "--bg": "#f6f7fb",
        "--panel": "#fff",
        "--text": "#1d2330",
        "--muted": "#667085",
        "--border": "#e4e7ec",
        "--accent": "#6558e8",
        "--accent2": "#8b5cf6",
        "--accent-soft": "#efedff",
        "--key": "#fff2a8",
        "--key-border": "#e8c948",
        "--blue": "#2878f0",
        "--purple": "#7c4dce",
        "--orange": "#e98324",
        "--red": "#d64b4b",
        "--green": "#1f9d68",
        "--cyan": "#0f8b99",
        "--r": "18px",
    }


def test_playwright_command_is_exactly_pinned():
    assert PINNED_PLAYWRIGHT_COMMAND == (
        "npx",
        "--yes",
        "--package",
        "@playwright/cli@0.1.17",
        "playwright-cli",
    )


def test_all_category_collision_is_added_without_mutating_source():
    source = {
        "sections": [
            {
                "blocks": [
                    {
                        "id": "grid",
                        "type": "feature_grid",
                        "cards": [
                            {
                                "id": "setup-card",
                                "category": "setup",
                                "title": "Setup",
                                "description": "Setup card.",
                            }
                        ],
                    }
                ]
            }
        ]
    }
    original = copy.deepcopy(source)

    result = add_all_category_collision(source)

    assert source == original
    cards = result["sections"][0]["blocks"][0]["cards"]
    assert [card["category"] for card in cards] == ["setup", "all"]
    assert cards[1]["id"] == "acceptance-all-category-card"
    assert cards[1]["title"] == "Acceptance category all"


def test_normalize_for_parity_removes_only_runtime_identity(tmp_path):
    root = tmp_path / "repo"
    value = {
        "repository": str(root),
        "port": 43123,
        "served_url": "http://127.0.0.1:43123/acceptance/candidate.html",
        "file_url": f"file://{root}/acceptance/candidate.html",
        "session": "task5-abc",
        "measurements": {
            "shell_width": 1280,
            "note": f"kept text under {root}",
        },
    }

    assert normalize_for_parity(value, repo_root=root) == {
        "measurements": {
            "shell_width": 1280,
            "note": "kept text under <REPO>",
        }
    }


def test_normalize_for_parity_marks_pdf_runtime_hash_as_volatile():
    value = {
        "print": {
            "pdf_created": True,
            "settings": {"format": "A4"},
            "sha256": "runtime-specific",
            "bytes": 214206,
        }
    }

    assert normalize_for_parity(value) == {
        "print": {
            "pdf_created": True,
            "settings": {"format": "A4"},
            "sha256": "<PDF-RUNTIME-HASH>",
            "bytes": 214206,
        }
    }


def test_candidate_event_counts_cover_every_browser_phase():
    empty = {
        "consoleErrors": [],
        "pageErrors": [],
        "requestFailures": [],
        "httpErrors": [],
        "requests": [{"url": "document"}],
    }
    interaction = {
        **empty,
        "consoleErrors": ["late interaction error"],
        "requests": [{"url": "candidate"}, {"url": "interaction"}],
    }

    assert aggregate_candidate_events([empty, interaction]) == {
        "candidate_console_errors": 1,
        "candidate_page_errors": 0,
        "candidate_failed_requests": 0,
        "candidate_http_errors": 0,
        "candidate_requests": 3,
    }


def test_failed_hard_checks_returns_names_and_details():
    checks = [
        {"name": "passes", "passed": True, "actual": 1, "expected": 1},
        {"name": "fails", "passed": False, "actual": 2, "expected": 1},
    ]

    assert failed_hard_checks(checks) == [
        {"name": "fails", "actual": 2, "expected": 1}
    ]


def test_sha256_path_hashes_file_bytes(tmp_path):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"task-5")

    assert (
        sha256_path(artifact)
        == "e7c08de777760acda054e9e20c19133287d1c245ef5efb6abaea6ec1ee3020e9"
    )


def test_sessions_include_a_unique_run_nonce():
    root = Path("/tmp/task-5")

    assert _session("interactions", root, "first") != _session(
        "interactions", root, "second"
    )


def test_copy_hard_gate_requires_clipboard_text_to_match_source():
    assert _copy_matches_source(
        {"announcement": "已复制", "ariaLive": "polite", "sourceText": "print(1)", "clipboardText": "print(1)"}
    )
    assert not _copy_matches_source(
        {"announcement": "已复制", "ariaLive": "polite", "sourceText": "print(1)", "clipboardText": "other"}
    )


def test_pdf_settings_and_page_box_require_a4():
    assert PDF_SETTINGS == {
        "format": "A4",
        "printBackground": True,
        "margin": {"top": "12mm", "right": "12mm", "bottom": "12mm", "left": "12mm"},
    }
    assert _pdf_page_box_is_a4(
        {"pageCount": 4, "mediaBox": [0.0, 0.0, 595.92, 842.88]}
    )
    assert not _pdf_page_box_is_a4(
        {"pageCount": 4, "mediaBox": [0.0, 0.0, 612.0, 792.0]}
    )


def test_reference_defects_require_the_exact_favicon_probe_and_no_extra_errors():
    reference = {
        "viewports": {"390": {"document": {"scrollWidth": 394, "clientWidth": 390}}},
        "faviconProbe": {
            "documentUrl": "http://127.0.0.1:4567/reference.html",
            "iconLinks": [],
            "url": "http://127.0.0.1:4567/favicon.ico",
            "status": 404,
        },
    }
    events = {
        "consoleErrors": [{"text": "Failed to load resource: the server responded with a status of 404 (File not found)", "url": "http://127.0.0.1:4567/favicon.ico"}],
        "pageErrors": [],
        "requestFailures": [],
        "httpErrors": [{"status": 404, "url": "http://127.0.0.1:4567/favicon.ico"}],
        "requests": [],
    }

    status = _reference_defect_status(reference, events)

    assert status["approved"]
    assert status["favicon"]["url"] == "http://127.0.0.1:4567/favicon.ico"
    reference["viewports"]["390"]["document"]["scrollWidth"] = 395
    assert not _reference_defect_status(reference, events)["approved"]
    reference["viewports"]["390"]["document"]["scrollWidth"] = 394
    events["pageErrors"].append("unexpected")
    assert not _reference_defect_status(reference, events)["approved"]


def test_visual_review_binds_global_style_tolerances_to_check_names():
    base = {
        "shell": {"width": 1280},
        "nav": {"width": 250},
        "featureGrid": {"columnCount": 3},
        "document": {"scrollWidth": 1280},
    }
    layout = {
        "candidate": {"viewports": {label: copy.deepcopy(base) for label in ("1440", "1024", "768", "390")}},
        "reference": {"viewports": {label: copy.deepcopy(base) for label in ("1440", "1024", "768", "390")}},
    }
    checks = [
        {"name": "hero radius", "passed": True, "actual": "28px", "expected": "28px"},
        {"name": "section radius", "passed": True, "actual": "18px", "expected": "18px"},
        *[
            {"name": f"section padding {side}", "passed": True, "actual": 26, "expected": "26 +/- 2px"}
            for side in ("top", "right", "bottom", "left")
        ],
        {"name": "color token --bg", "passed": True, "actual": "#f6f7fb", "expected": "#f6f7fb"},
    ]

    review = _visual_review(Path("/tmp"), layout, checks)

    assert "Global style checks" in review
    assert "hero radius" in review
    assert "section padding top" in review
    assert "color token --bg" in review


def test_main_returns_nonzero_when_a_candidate_hard_gate_fails(tmp_path, monkeypatch):
    import acceptance_check

    root = tmp_path / "repo"
    fixture = root / "tests" / "fixtures" / "video-note-v2" / "all-blocks.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        '{"sections":[{"blocks":[{"type":"feature_grid","cards":[{"id":"setup"}]}]}]}',
        encoding="utf-8",
    )
    media = fixture.parent / "media"
    media.mkdir()
    (media / "frame.jpg").write_bytes(b"frame")
    golden = root / "references" / "html-golden" / "MindMap_Builder_中文实用指南_最新版.html"
    golden.parent.mkdir(parents=True)
    golden.write_text("<!doctype html>", encoding="utf-8")
    renderer = root / "scripts" / "render_html.py"
    renderer.parent.mkdir()
    renderer.write_text("# stub\n", encoding="utf-8")

    def fake_generate(_root, _fixture, output):
        output.write_text("<!doctype html>", encoding="utf-8")
        return {"command": ["renderer"], "stdout": "", "stderr": "", "sha256": "x", "bytes": 15}

    events = {
        "consoleErrors": [],
        "pageErrors": [],
        "requestFailures": [],
        "httpErrors": [],
        "requests": [],
    }
    capture = {"viewports": {"390": {"document": {"scrollWidth": 390, "clientWidth": 390}}}}

    @contextmanager
    def fake_server(_root):
        yield 43123

    monkeypatch.setattr(acceptance_check.shutil, "which", lambda _name: "/usr/bin/npx")
    monkeypatch.setattr(acceptance_check, "_generate_candidate", fake_generate)
    monkeypatch.setattr(acceptance_check, "serve_repository", fake_server)
    monkeypatch.setattr(
        acceptance_check.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess([], 0, "playwright-cli 0.1.17\n", ""),
    )
    monkeypatch.setattr(
        acceptance_check,
        "_capture_layout_and_screenshots",
        lambda *_args, **_kwargs: (capture, events),
    )
    monkeypatch.setattr(acceptance_check, "_run_interactions", lambda *_args: ({}, {}, events))
    monkeypatch.setattr(acceptance_check, "_run_offline", lambda *_args: ({}, events))
    monkeypatch.setattr(acceptance_check, "_run_print", lambda *_args: ({}, events))
    monkeypatch.setattr(acceptance_check, "_reference_defect_status", lambda *_args: {"approved": True})
    monkeypatch.setattr(
        acceptance_check,
        "_hard_checks",
        lambda *_args: [{"name": "390 candidate scroll width", "passed": False, "actual": 391, "expected": "<= 390"}],
    )
    monkeypatch.setattr(acceptance_check, "_visual_review", lambda *_args: "# review\n")

    assert acceptance_check.main(["--root", str(root)]) == 1
