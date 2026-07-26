import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "video-note-v2"
sys.path.insert(0, str(ROOT / "scripts"))

from render_html import (
    normalize_legacy_document,
    normalize_video_note,
    render_document,
    stable_json_dumps,
)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_schema_declares_the_v2_contract_and_strict_objects():
    schema = json.loads(
        (ROOT / "references" / "schema" / "video-note-v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema["$id"] == "https://video-note.dev/schema/video-note-v2.schema.json"
    assert schema["properties"]["schema_version"]["const"] == "video-note/v2"

    def assert_strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
            for value in node.values():
                assert_strict(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict(value)

    assert_strict(schema)


@pytest.mark.parametrize(
    "name", ["minimal.json", "full.json", "all-blocks.json", "jpeg.json", "png.json", "webp.json"]
)
def test_valid_fixtures_normalize_to_stable_json(name):
    normalized = normalize_video_note(fixture(name), base_dir=FIXTURES)
    serialized = stable_json_dumps(normalized)
    assert serialized.endswith("\n")
    assert serialized == stable_json_dumps(json.loads(serialized))
    assert normalized["schema_version"] == "video-note/v2"
    assert [section["id"] for section in normalized["sections"]] == sorted(
        section["id"] for section in normalized["sections"]
    )


def test_normalization_applies_shared_defaults():
    normalized = normalize_video_note(fixture("all-blocks.json"), base_dir=FIXTURES)
    blocks = {
        block["type"]: block
        for section in normalized["sections"]
        for block in section["blocks"]
    }
    assert blocks["code"]["language"] == "text"
    assert blocks["media"]["usage"] == ""
    assert blocks["media"]["timestamp_seconds"] is None
    assert blocks["media"]["deep_link"] is None
    assert blocks["feature_grid"]["filter_label"] == "category"


@pytest.mark.parametrize(
    "name",
    [
        "malicious.json",
        "missing-image.json",
        "invalid-url.json",
        "unknown-field.json",
        "duplicate-id.json",
        "forbidden-evidence.json",
        "evidence-sentinel.json",
    ],
)
def test_invalid_fixtures_are_rejected(name):
    with pytest.raises(ValueError):
        normalize_video_note(fixture(name), base_dir=FIXTURES)


@pytest.mark.parametrize(
    "path",
    [
        ("meta", "title"),
        ("meta", "source_url"),
        ("summary",),
        ("sections",),
    ],
)
def test_required_fields_are_rejected(path):
    note = fixture("minimal.json")
    target = note
    for key in path[:-1]:
        target = target[key]
    target.pop(path[-1])
    with pytest.raises(ValueError, match="required"):
        normalize_video_note(note, base_dir=FIXTURES)


def test_only_http_and_https_user_links_are_allowed():
    note = fixture("full.json")
    note["sections"][0]["blocks"][1]["deep_link"] = "file:///private/frame.jpg"
    with pytest.raises(ValueError, match="HTTP"):
        normalize_video_note(note, base_dir=FIXTURES)


def test_ids_are_globally_unique_including_nested_blocks():
    note = fixture("all-blocks.json")
    accordion = next(
        block
        for block in note["sections"][0]["blocks"]
        if block["type"] == "accordion"
    )
    accordion["blocks"][0]["id"] = note["sections"][0]["blocks"][0]["id"]
    with pytest.raises(ValueError, match="unique"):
        normalize_video_note(note, base_dir=FIXTURES)


def test_legacy_document_maps_exactly_and_discards_quote(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    normalized = normalize_legacy_document(
        {"title": "旧版标题", "bvid": "BV1Legacy", "duration": 45, "author": "作者"},
        [
            {
                "title": "章节",
                "summary": "章节摘要",
                "rows": [["名称", "用途", "要点"]],
                "cards": [
                    {
                        "image": image,
                        "label": "00:12",
                        "analysis": "图片解释",
                        "quote": "这句话必须被丢弃",
                    }
                ],
            }
        ],
    )
    assert normalized["meta"] == {
        "platform": "bilibili",
        "source_id": "BV1Legacy",
        "source_url": "https://www.bilibili.com/video/BV1Legacy",
        "title": "旧版标题",
        "author": "作者",
        "duration_seconds": 45,
        "language": "zh-CN",
    }
    assert normalized["summary"] == "章节摘要"
    blocks = normalized["sections"][0]["blocks"]
    assert blocks[0] == {"id": "section-1-summary", "type": "paragraph", "text": "章节摘要"}
    assert blocks[1]["columns"] == [
        {"id": "name", "label": "名称"},
        {"id": "usage", "label": "用途"},
        {"id": "points", "label": "要点"},
    ]
    assert blocks[1]["rows"] == [{"name": "名称", "usage": "用途", "points": "要点"}]
    assert blocks[2]["headline"] == "00:12"
    assert blocks[2]["explanation"] == "图片解释"
    assert "quote" not in stable_json_dumps(normalized)

    rendered = render_document(
        {"title": "旧版标题", "bvid": "BV1Legacy", "duration": 45, "author": "作者"},
        [{
            "title": "章节",
            "summary": "章节摘要",
            "rows": [["名称", "用途", "要点"]],
            "cards": [{"image": image, "label": "00:12", "analysis": "图片解释", "quote": "这句话必须被丢弃"}],
        }],
    )
    assert "这句话必须被丢弃" not in rendered


def test_legacy_mapping_ignores_unmapped_caller_metadata():
    normalized = normalize_legacy_document(
        {
            "title": "旧版标题",
            "bvid": "BV1Extra",
            "duration": 1,
            "author": "作者",
            "chapters": [{"title": "caller-only"}],
        },
        [{"title": "章节", "summary": "章节摘要"}],
    )
    assert normalized["meta"]["source_id"] == "BV1Extra"


def test_legacy_mapping_preserves_an_empty_optional_author():
    normalized = normalize_legacy_document(
        {"title": "旧版标题", "bvid": "BV1NoAuthor", "duration": 1, "author": ""},
        [{"title": "章节", "summary": "章节摘要"}],
    )
    assert normalized["meta"]["author"] == ""
