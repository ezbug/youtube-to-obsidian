#!/usr/bin/env python3
"""Generate direct, portable contract and CLI acceptance evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_html.py"
SCHEMA = ROOT / "references" / "schema" / "video-note-v2.schema.json"
FIXTURES = ROOT / "tests" / "fixtures" / "video-note-v2"

sys.path.insert(0, str(ROOT / "scripts"))
from render_html import normalize_video_note, render_document  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_case(
    work: Path,
    *,
    name: str,
    note: Path,
    profile: str,
    expected_exit: int,
    output_parent_is_file: bool = False,
) -> dict:
    if output_parent_is_file:
        parent = work / f"{name}-parent"
        parent.write_text("not a directory\n", encoding="utf-8")
        output = parent / "output.html"
        before_hash = None
    else:
        output = work / f"{name}.html"
        output.write_text("ATOMIC_OUTPUT_SENTINEL\n", encoding="utf-8")
        before_hash = _sha256(output)
    command = [
        sys.executable,
        str(RENDERER),
        "--note",
        str(note),
        "--output",
        str(output),
        "--profile",
        profile,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    after_hash = _sha256(output) if output.is_file() else None
    clean = lambda text: text.replace(str(ROOT), "<REPO>").replace(  # noqa: E731
        str(work), "<WORK>"
    )
    atomic_preserved = (
        before_hash == after_hash if expected_exit != 0 and before_hash else None
    )
    return {
        "name": name,
        "fixture": note.name,
        "profile": profile,
        "expected_exit": expected_exit,
        "actual_exit": completed.returncode,
        "error_class": {
            0: "success",
            2: "input_validation",
            3: "output_io",
            4: "unexpected_renderer",
        }.get(completed.returncode, "unclassified"),
        "stdout": clean(completed.stdout),
        "stderr": clean(completed.stderr),
        "output_existed_before": before_hash is not None,
        "output_sha256_before": before_hash,
        "output_sha256_after": after_hash,
        "atomic_output_preserved": atomic_preserved,
        "passed": completed.returncode == expected_exit
        and (atomic_preserved is not False),
    }


def generate(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_output = output_dir / "fixtures"
    fixture_output.mkdir(parents=True, exist_ok=True)
    schema_output = output_dir / "video-note-v2.schema.json"
    shutil.copyfile(SCHEMA, schema_output)
    for fixture in sorted(FIXTURES.glob("*.json")):
        shutil.copyfile(fixture, fixture_output / fixture.name)

    all_blocks = FIXTURES / "all-blocks.json"
    normalized = normalize_video_note(
        json.loads(all_blocks.read_text(encoding="utf-8")),
        base_dir=all_blocks.parent,
    )
    normalized_output = output_dir / "normalized-all-blocks.json"
    _write_json(normalized_output, normalized)

    malformed = output_dir / "fixtures" / "malformed.json"
    malformed.write_text('{"schema_version": "video-note/v2",\n', encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="contract-evidence-") as directory:
        work = Path(directory)
        cases = [
            _run_case(
                work,
                name="valid-web",
                note=all_blocks,
                profile="web",
                expected_exit=0,
            ),
            _run_case(
                work,
                name="valid-email",
                note=all_blocks,
                profile="email",
                expected_exit=0,
            ),
            _run_case(
                work,
                name="unknown-field",
                note=FIXTURES / "unknown-field.json",
                profile="web",
                expected_exit=2,
            ),
            _run_case(
                work,
                name="invalid-url",
                note=FIXTURES / "invalid-url.json",
                profile="web",
                expected_exit=2,
            ),
            _run_case(
                work,
                name="missing-image",
                note=FIXTURES / "missing-image.json",
                profile="web",
                expected_exit=2,
            ),
            _run_case(
                work,
                name="hostile-svg",
                note=FIXTURES / "malicious.json",
                profile="web",
                expected_exit=2,
            ),
            _run_case(
                work,
                name="evidence-sentinel",
                note=FIXTURES / "evidence-sentinel.json",
                profile="web",
                expected_exit=2,
            ),
            _run_case(
                work,
                name="malformed-json",
                note=malformed,
                profile="web",
                expected_exit=2,
            ),
            _run_case(
                work,
                name="unsupported-profile",
                note=FIXTURES / "minimal.json",
                profile="rss",
                expected_exit=2,
            ),
            _run_case(
                work,
                name="output-io",
                note=FIXTURES / "minimal.json",
                profile="web",
                expected_exit=3,
                output_parent_is_file=True,
            ),
        ]

        legacy_frame = work / "legacy-frame.jpg"
        legacy_frame.write_bytes(b"\xff\xd8\xff\xd9")
        legacy_html = render_document(
            {
                "title": "Legacy acceptance",
                "bvid": "BV1LegacyAcceptance",
                "duration": 45,
                "author": "Acceptance",
            },
            [
                {
                    "title": "Legacy section",
                    "summary": "Legacy summary",
                    "rows": [["Name", "Use", "Point"]],
                    "cards": [
                        {
                            "image": legacy_frame,
                            "label": "00:12",
                            "analysis": "Legacy image explanation",
                            "quote": "LEGACY_EVIDENCE_MUST_NOT_LEAK",
                        }
                    ],
                }
            ],
        )
    legacy_output = output_dir / "legacy-output.html"
    legacy_output.write_text(legacy_html, encoding="utf-8")
    legacy_result = {
        "api": "render_document(meta, sections)",
        "output": legacy_output.name,
        "output_sha256": _sha256(legacy_output),
        "contains_embedded_jpeg": "data:image/jpeg;base64," in legacy_html,
        "contains_table": "<table" in legacy_html,
        "contains_figure": "figure-card" in legacy_html,
        "evidence_sentinel_absent": "LEGACY_EVIDENCE_MUST_NOT_LEAK"
        not in legacy_html,
    }
    legacy_result["passed"] = all(
        value
        for key, value in legacy_result.items()
        if key
        in {
            "contains_embedded_jpeg",
            "contains_table",
            "contains_figure",
            "evidence_sentinel_absent",
        }
    )

    result = {
        "schema_path": schema_output.name,
        "schema_sha256": _sha256(schema_output),
        "fixture_directory": "fixtures",
        "all_blocks_fixture_sha256": _sha256(fixture_output / "all-blocks.json"),
        "normalized_output": normalized_output.name,
        "normalized_sha256": _sha256(normalized_output),
        "cli_cases": cases,
        "legacy": legacy_result,
    }
    result["passed"] = (
        all(case["passed"] for case in cases) and legacy_result["passed"]
    )
    _write_json(output_dir / "contract-results.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "acceptance" / "contract",
    )
    args = parser.parse_args(argv)
    result = generate(args.output_dir)
    print(
        f"contract evidence: {len(result['cli_cases'])} CLI cases; "
        f"legacy={'PASS' if result['legacy']['passed'] else 'FAIL'}"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
