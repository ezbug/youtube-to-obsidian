"""Normalize and safely render portable video-note/v2 documents."""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse


SCHEMA_VERSION = "video-note/v2"
_FORBIDDEN_KEY_PARTS = (
    "evidence",
    "grounding",
    "confidence",
    "transcript",
    "subtitle",
    "ocr",
    "html",
    "svg",
    "timestamp_alignment",
    "extraction_trace",
)
_SENTINELS = ("evidence_sentinel", "evidence_leak_sentinel_9f2a")
_IMAGE_FORMATS = {
    ".jpg": ("image/jpeg", "JPEG"),
    ".jpeg": ("image/jpeg", "JPEG"),
    ".png": ("image/png", "PNG"),
    ".webp": ("image/webp", "WebP"),
}
_IMAGE_SUFFIXES = {
    suffix: media_type for suffix, (media_type, _) in _IMAGE_FORMATS.items()
}


def stable_json_dumps(note: dict) -> str:
    """Return canonical JSON suitable for byte-for-byte fixture comparison."""
    return (
        json.dumps(
            note,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _fail(message: str) -> None:
    raise ValueError(message)


def _path_child(path: str, key: str) -> str:
    if (
        key
        and key.isascii()
        and (key[0].isalpha() or key[0] == "_")
        and all(character.isalnum() or character == "_" for character in key[1:])
    ):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=True)}]"


def _path_with_id(path: str, source_id: str) -> str:
    return f"{path}[id={json.dumps(source_id, ensure_ascii=True)}]"


def _reject_invalid_unicode(value, path: str = "note") -> None:
    if isinstance(value, dict):
        for index, (key, child) in enumerate(value.items()):
            if not isinstance(key, str):
                _fail(f"{path}.<key[{index}]> must be a string")
            try:
                key.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                _fail(f"{path}.<key[{index}]> contains invalid Unicode")
            _reject_invalid_unicode(child, _path_child(path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_invalid_unicode(child, f"{path}[{index}]")
    elif isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            _fail(f"{path} contains invalid Unicode")


def _object(value, name: str) -> dict:
    if not isinstance(value, dict):
        _fail(f"{name} must be an object")
    return value


def _list(value, name: str) -> list:
    if not isinstance(value, list):
        _fail(f"{name} must be an array")
    return value


def _text(value, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(f"{name} must be a non-empty string")
    return value


def _non_negative_number(value, name: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{name} must be a finite non-negative number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < 0:
        _fail(f"{name} must be a finite non-negative number")
    return value


def _keys(value: dict, name: str, required: set[str], allowed: set[str]) -> None:
    missing = required - value.keys()
    if missing:
        _fail(f"{name} required fields missing: {', '.join(sorted(missing))}")
    unknown = value.keys() - allowed
    if unknown:
        _fail(f"{name} has unknown fields: {', '.join(sorted(unknown))}")


def _reject_evidence(value, path: str = "note") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = key.casefold().replace("-", "_")
            if any(part in normalized_key for part in _FORBIDDEN_KEY_PARTS):
                _fail(f"{_path_child(path, key)} is not reader-facing content")
            _reject_evidence(child, _path_child(path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_evidence(child, f"{path}[{index}]")
    elif isinstance(value, str) and any(
        sentinel in value.casefold() for sentinel in _SENTINELS
    ):
        _fail(f"{path} contains the evidence sentinel")


def _http_url(value, name: str) -> str:
    value = _text(value, name)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _fail(f"{name} must use HTTP or HTTPS")
    return value


def _detect_image_signature(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "WebP"
    return None


def _validate_image_signature(
    candidate: Path, suffix: str, field_path: str, raw: str
) -> None:
    try:
        with candidate.open("rb") as source:
            header = source.read(12)
    except OSError as exc:
        _fail(f"{field_path} cannot be read: {raw}: {exc}")
    actual = _detect_image_signature(header)
    expected = _IMAGE_FORMATS[suffix][1]
    if actual is None:
        _fail(f"{field_path} has unsupported image signature: {raw}")
    if actual != expected:
        _fail(
            f"{field_path} signature does not match {expected}: {raw}"
        )


def _image_path(value, base_dir: Path | None, field_path: str) -> str:
    raw = _text(value, field_path)
    path = Path(raw)
    suffix = path.suffix.casefold()
    if suffix not in _IMAGE_SUFFIXES:
        _fail(f"{field_path} must be JPEG, PNG, or WebP: {raw}")
    candidate = None
    if base_dir is not None:
        if path.is_absolute():
            _fail(f"{field_path} must be relative when base_dir is provided: {raw}")
        base = base_dir.resolve()
        candidate = (base / path).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            _fail(f"{field_path} escapes its base directory: {raw}")
        if not candidate.is_file():
            _fail(f"{field_path} does not exist: {raw}")
    elif not path.is_absolute() and ".." in path.parts:
        _fail(f"{field_path} must stay within its base directory: {raw}")
    elif path.is_absolute() or path.exists():
        candidate = path.resolve()
        if not candidate.is_file():
            _fail(f"{field_path} does not exist: {raw}")
    if candidate is not None:
        _validate_image_signature(candidate, suffix, field_path, raw)
    return raw


def _block_id(value: dict, used_ids: set[str], path: str) -> str:
    block_id = _text(value.get("id"), f"{path}.id")
    if block_id in used_ids:
        _fail(
            f"{path}.id duplicates a source ID; source IDs must be globally "
            f"unique: {block_id}"
        )
    used_ids.add(block_id)
    return block_id


def _normalize_block(
    value,
    used_ids: set[str],
    base_dir: Path | None,
    path: str,
    *,
    nested: bool = False,
) -> dict:
    block = _object(value, path)
    block_type = _text(block.get("type"), f"{path}.type")
    allowed_nested = {"paragraph", "list", "callout"}
    if nested and block_type not in allowed_nested:
        _fail(
            f"{path}.type must be paragraph, list, or callout inside an accordion"
        )
    block_id = _block_id(block, used_ids, path)
    block_path = _path_with_id(path, block_id)

    if block_type == "paragraph":
        _keys(
            block,
            block_path,
            {"id", "type", "text"},
            {"id", "type", "text"},
        )
        return {
            "id": block_id,
            "type": block_type,
            "text": _text(block["text"], f"{block_path}.text"),
        }

    if block_type == "callout":
        _keys(
            block,
            block_path,
            {"id", "type", "kind", "text"},
            {"id", "type", "kind", "text"},
        )
        kind = _text(block["kind"], f"{block_path}.kind")
        if kind not in {"key", "source", "recommendation", "inference", "notice"}:
            _fail(f"{block_path}.kind is unsupported: {kind}")
        return {
            "id": block_id,
            "type": block_type,
            "kind": kind,
            "text": _text(block["text"], f"{block_path}.text"),
        }

    if block_type == "list":
        _keys(
            block,
            block_path,
            {"id", "type", "items"},
            {"id", "type", "items"},
        )
        items_path = f"{block_path}.items"
        items = [
            _text(item, f"{items_path}[{index}]")
            for index, item in enumerate(_list(block["items"], items_path))
        ]
        if not items:
            _fail(f"{items_path} must not be empty")
        return {"id": block_id, "type": block_type, "items": items}

    if block_type == "table":
        _keys(
            block,
            block_path,
            {"id", "type", "columns", "rows"},
            {"id", "type", "columns", "rows"},
        )
        columns = []
        column_ids = set()
        columns_path = f"{block_path}.columns"
        for column_index, column in enumerate(
            _list(block["columns"], columns_path)
        ):
            column_path = f"{columns_path}[{column_index}]"
            column = _object(column, column_path)
            _keys(column, column_path, {"id", "label"}, {"id", "label"})
            column_id = _text(column["id"], f"{column_path}.id")
            if column_id in column_ids:
                _fail(f"{column_path}.id duplicates a table column: {column_id}")
            column_ids.add(column_id)
            columns.append(
                {
                    "id": column_id,
                    "label": _text(column["label"], f"{column_path}.label"),
                }
            )
        if not columns:
            _fail(f"{columns_path} must not be empty")
        rows = []
        rows_path = f"{block_path}.rows"
        for row_index, row in enumerate(_list(block["rows"], rows_path)):
            row_path = f"{rows_path}[{row_index}]"
            row = _object(row, row_path)
            if set(row) != column_ids:
                _fail(f"{row_path} must contain exactly the declared columns")
            normalized_row = {}
            for column in columns:
                column_id = column["id"]
                cell_path = _path_child(row_path, column_id)
                value = row[column_id]
                if isinstance(value, (dict, list)) or isinstance(value, bool) or value is None:
                    _fail(f"{cell_path} must be text or a number")
                normalized_row[column_id] = str(value)
            rows.append(normalized_row)
        return {"id": block_id, "type": block_type, "columns": columns, "rows": rows}

    if block_type == "media":
        _keys(
            block,
            block_path,
            {"id", "type", "image", "alt", "headline", "explanation"},
            {"id", "type", "image", "alt", "headline", "explanation", "usage", "timestamp_seconds", "deep_link"},
        )
        timestamp = block.get("timestamp_seconds")
        if timestamp is not None:
            timestamp = _non_negative_number(
                timestamp, f"{block_path}.timestamp_seconds"
            )
        deep_link = block.get("deep_link")
        return {
            "id": block_id,
            "type": block_type,
            "image": _image_path(block["image"], base_dir, f"{block_path}.image"),
            "alt": _text(block["alt"], f"{block_path}.alt"),
            "headline": _text(block["headline"], f"{block_path}.headline"),
            "explanation": _text(
                block["explanation"], f"{block_path}.explanation"
            ),
            "usage": _text(
                block.get("usage", ""),
                f"{block_path}.usage",
                allow_empty=True,
            ),
            "timestamp_seconds": timestamp,
            "deep_link": (
                None
                if deep_link is None
                else _http_url(deep_link, f"{block_path}.deep_link")
            ),
        }

    if block_type == "feature_grid":
        _keys(
            block,
            block_path,
            {"id", "type", "cards"},
            {"id", "type", "cards", "filter_label"},
        )
        cards = []
        card_ids = set()
        cards_path = f"{block_path}.cards"
        for card_index, card in enumerate(_list(block["cards"], cards_path)):
            card_path = f"{cards_path}[{card_index}]"
            card = _object(card, card_path)
            _keys(
                card,
                card_path,
                {"id", "category", "title", "description"},
                {"id", "category", "title", "description", "link"},
            )
            card_id = _text(card["id"], f"{card_path}.id")
            if card_id in card_ids:
                _fail(f"{card_path}.id duplicates a feature card: {card_id}")
            card_ids.add(card_id)
            link = card.get("link")
            cards.append({
                "id": card_id,
                "category": _text(card["category"], f"{card_path}.category"),
                "title": _text(card["title"], f"{card_path}.title"),
                "description": _text(
                    card["description"], f"{card_path}.description"
                ),
                "link": (
                    None
                    if link is None
                    else _http_url(link, f"{card_path}.link")
                ),
            })
        if not cards:
            _fail(f"{cards_path} must not be empty")
        return {
            "id": block_id,
            "type": block_type,
            "filter_label": _text(
                block.get("filter_label", "category"),
                f"{block_path}.filter_label",
            ),
            "cards": cards,
        }

    if block_type == "code":
        _keys(
            block,
            block_path,
            {"id", "type", "text"},
            {"id", "type", "text", "language"},
        )
        return {
            "id": block_id,
            "type": block_type,
            "text": _text(
                block["text"], f"{block_path}.text", allow_empty=True
            ),
            "language": _text(
                block.get("language", "text"), f"{block_path}.language"
            ),
        }

    if block_type == "flow":
        _keys(
            block,
            block_path,
            {"id", "type", "nodes", "edges"},
            {"id", "type", "nodes", "edges"},
        )
        nodes = []
        node_ids = set()
        nodes_path = f"{block_path}.nodes"
        for node_index, node in enumerate(_list(block["nodes"], nodes_path)):
            node_path = f"{nodes_path}[{node_index}]"
            node = _object(node, node_path)
            _keys(node, node_path, {"id", "label"}, {"id", "label"})
            node_id = _text(node["id"], f"{node_path}.id")
            if node_id in node_ids:
                _fail(f"{node_path}.id duplicates a flow node: {node_id}")
            node_ids.add(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "label": _text(node["label"], f"{node_path}.label"),
                }
            )
        edges = []
        edges_path = f"{block_path}.edges"
        for edge_index, edge in enumerate(_list(block["edges"], edges_path)):
            edge_path = f"{edges_path}[{edge_index}]"
            edge = _object(edge, edge_path)
            _keys(edge, edge_path, {"from", "to"}, {"from", "to", "label"})
            source = _text(edge["from"], f"{edge_path}.from")
            target = _text(edge["to"], f"{edge_path}.to")
            if source not in node_ids or target not in node_ids:
                _fail(f"{edge_path} must reference declared nodes")
            edges.append(
                {
                    "from": source,
                    "to": target,
                    "label": _text(
                        edge.get("label", ""),
                        f"{edge_path}.label",
                        allow_empty=True,
                    ),
                }
            )
        return {"id": block_id, "type": block_type, "nodes": nodes, "edges": edges}

    if block_type == "accordion":
        _keys(
            block,
            block_path,
            {"id", "type", "title", "blocks"},
            {"id", "type", "title", "blocks"},
        )
        child_path = f"{block_path}.blocks"
        child_blocks = [
            _normalize_block(
                child,
                used_ids,
                base_dir,
                f"{child_path}[{index}]",
                nested=True,
            )
            for index, child in enumerate(_list(block["blocks"], child_path))
        ]
        if not child_blocks:
            _fail(f"{child_path} must not be empty")
        return {
            "id": block_id,
            "type": block_type,
            "title": _text(block["title"], f"{block_path}.title"),
            "blocks": child_blocks,
        }

    _fail(f"{path}.type is unsupported: {block_type}")


def normalize_video_note(note: dict, *, base_dir: str | Path | None = None) -> dict:
    """Validate a strict v2 note and return its deterministic normalized form."""
    _reject_invalid_unicode(note)
    _reject_evidence(note)
    note = _object(note, "note")
    _keys(note, "note", {"schema_version", "meta", "summary", "sections"}, {"schema_version", "meta", "summary", "sections"})
    if note["schema_version"] != SCHEMA_VERSION:
        _fail(f"note.schema_version must be {SCHEMA_VERSION}")
    meta = _object(note["meta"], "note.meta")
    _keys(meta, "note.meta", {"platform", "source_id", "source_url", "title", "author", "duration_seconds", "language"}, {"platform", "source_id", "source_url", "title", "author", "duration_seconds", "language"})
    duration = _non_negative_number(
        meta["duration_seconds"], "note.meta.duration_seconds"
    )
    normalized_meta = {
        "platform": _text(meta["platform"], "note.meta.platform"),
        "source_id": _text(meta["source_id"], "note.meta.source_id"),
        "source_url": _http_url(meta["source_url"], "note.meta.source_url"),
        "title": _text(meta["title"], "note.meta.title"),
        "author": _text(meta["author"], "note.meta.author", allow_empty=True),
        "duration_seconds": duration,
        "language": _text(meta["language"], "note.meta.language"),
    }
    base = None if base_dir is None else Path(base_dir)
    used_ids = set()
    sections = []
    section_ids = set()
    for section_index, section in enumerate(
        _list(note["sections"], "note.sections")
    ):
        section_path = f"note.sections[{section_index}]"
        section = _object(section, section_path)
        _keys(section, section_path, {"id", "title", "summary", "blocks"}, {"id", "title", "summary", "blocks"})
        section_id = _text(section["id"], f"{section_path}.id")
        labelled_section_path = _path_with_id(section_path, section_id)
        if section_id in section_ids or section_id in used_ids:
            _fail(
                f"{section_path}.id duplicates a source ID; source IDs must be "
                f"globally unique: {section_id}"
            )
        section_ids.add(section_id)
        used_ids.add(section_id)
        blocks_path = f"{section_path}.blocks"
        blocks = [
            _normalize_block(
                block, used_ids, base, f"{blocks_path}[{block_index}]"
            )
            for block_index, block in enumerate(
                _list(section["blocks"], f"{labelled_section_path}.blocks")
            )
        ]
        sections.append({
            "id": section_id,
            "title": _text(section["title"], f"{labelled_section_path}.title"),
            "summary": _text(
                section["summary"], f"{labelled_section_path}.summary"
            ),
            "blocks": blocks,
        })
    if not sections:
        _fail("note.sections must not be empty")
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": normalized_meta,
        "summary": _text(note["summary"], "note.summary"),
        "sections": sections,
    }


def normalize_legacy_document(meta: dict, sections: list) -> dict:
    """Map the established Bilibili renderer input to the platform-neutral v2 form."""
    legacy_meta = _object(meta, "legacy meta")
    required_legacy_meta = {"title", "bvid", "duration", "author"}
    missing_legacy_meta = required_legacy_meta - legacy_meta.keys()
    if missing_legacy_meta:
        _fail(f"legacy meta required fields missing: {', '.join(sorted(missing_legacy_meta))}")
    legacy_sections = _list(sections, "legacy sections")
    if not legacy_sections:
        _fail("legacy sections must not be empty")
    normalized_sections = []
    for section_index, legacy_section in enumerate(legacy_sections, start=1):
        legacy_section = _object(legacy_section, "legacy section")
        _keys(legacy_section, "legacy section", {"title", "summary"}, {"title", "summary", "rows", "cards"})
        section_id = f"section-{section_index}"
        blocks = [{
            "id": f"{section_id}-summary",
            "type": "paragraph",
            "text": _text(legacy_section["summary"], "legacy section.summary"),
        }]
        rows = _list(
            legacy_section.get("rows", []),
            f"legacy sections[{section_index - 1}].rows",
        )
        if rows:
            normalized_rows = []
            for row_index, row in enumerate(rows):
                if not isinstance(row, (list, tuple)) or len(row) != 3:
                    _fail(
                        f"legacy sections[{section_index - 1}].rows[{row_index}] "
                        "must contain exactly 3 values"
                    )
                normalized_rows.append({
                    "name": str(row[0]),
                    "usage": str(row[1]),
                    "points": str(row[2]),
                })
            blocks.append({
                "id": f"{section_id}-table",
                "type": "table",
                "columns": [{"id": "name", "label": "名称"}, {"id": "usage", "label": "用途"}, {"id": "points", "label": "要点"}],
                "rows": normalized_rows,
            })
        for card_index, card in enumerate(legacy_section.get("cards", []), start=1):
            card = _object(card, "legacy card")
            _keys(
                card,
                "legacy card",
                {"image", "label", "analysis"},
                {
                    "image",
                    "label",
                    "analysis",
                    "quote",
                    "chapter",
                    "tool",
                    "ocr",
                    "vision_raw",
                },
            )
            label = _text(card["label"], "legacy card.label")
            blocks.append({
                "id": f"{section_id}-media-{card_index}",
                "type": "media",
                "image": str(card["image"]),
                "alt": label,
                "headline": label,
                "explanation": _text(card["analysis"], "legacy card.analysis"),
            })
        normalized_sections.append({
            "id": section_id,
            "title": _text(legacy_section["title"], "legacy section.title"),
            "summary": _text(legacy_section["summary"], "legacy section.summary"),
            "blocks": blocks,
        })
    bvid = _text(legacy_meta["bvid"], "legacy meta.bvid")
    document = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "platform": "bilibili",
            "source_id": bvid,
            "source_url": f"https://www.bilibili.com/video/{bvid}",
            "title": _text(legacy_meta["title"], "legacy meta.title"),
            "author": _text(legacy_meta["author"], "legacy meta.author", allow_empty=True),
            "duration_seconds": legacy_meta["duration"],
            "language": "zh-CN",
        },
        "summary": next((item["summary"] for item in normalized_sections if item["summary"].strip()), legacy_meta["title"]),
        "sections": normalized_sections,
    }
    return normalize_video_note(document)


def _image_uri(path: str, base_dir: Path | None) -> str:
    image = Path(path)
    if not image.is_absolute() and base_dir is not None:
        image = base_dir / image
    payload = base64.b64encode(image.read_bytes()).decode("ascii")
    return f"data:{_IMAGE_SUFFIXES[image.suffix.casefold()]};base64,{payload}"


_FAVICON = base64.b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    b'<rect width="64" height="64" rx="16" fill="#6558e8"/>'
    b'<path d="M18 20h28v6H18zm0 10h20v6H18zm0 10h14v6H18z" fill="white"/>'
    b"</svg>"
).decode("ascii")

_WEB_STYLE = """
:root {
  --bg:#f6f7fb; --panel:#fff; --text:#1d2330; --muted:#667085;
  --border:#e4e7ec; --accent:#6558e8; --accent2:#8b5cf6;
  --accent-soft:#efedff; --key:#fff2a8; --key-border:#e8c948;
  --blue:#2878f0; --purple:#7c4dce; --orange:#e98324;
  --red:#d64b4b; --green:#1f9d68; --cyan:#0f8b99;
  --shadow:0 10px 28px rgba(32,37,60,.075); --r:18px;
}
*{box-sizing:border-box;min-width:0}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;line-height:1.65;overflow-wrap:anywhere}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
.skip-link{position:fixed;z-index:100;left:16px;top:12px;padding:10px 14px;border-radius:10px;background:var(--text);color:#fff;transform:translateY(-160%)}
.skip-link:focus{transform:translateY(0)}
.shell{max-width:1280px;margin:auto;display:grid;grid-template-columns:250px minmax(0,1fr);gap:24px;padding:24px}
nav{position:sticky;top:16px;height:fit-content;background:var(--panel);border:1px solid var(--border);border-radius:var(--r);padding:16px;box-shadow:var(--shadow)}
nav strong{display:block;margin:4px 8px 10px}
nav a{display:block;color:var(--muted);padding:7px 9px;border-radius:9px;font-size:14px}
nav a:hover{background:var(--accent-soft);color:var(--accent);text-decoration:none}
main{min-width:0}
.hero{border-radius:28px;padding:34px;border:1px solid var(--border);background:linear-gradient(135deg,#fff 0%,#f2efff 60%,#eef8ff 100%);box-shadow:var(--shadow)}
.badge{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:13px;font-weight:700}
h1{font-size:clamp(31px,5vw,50px);line-height:1.12;margin:12px 0 10px}
h2{font-size:26px;margin:0 0 14px}
h3{font-size:19px;margin:0 0 8px}
.lead{font-size:18px;color:var(--muted);max-width:920px}
.status-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:22px}
.status{padding:14px;border:1px solid var(--border);border-radius:14px;background:#fff}
.status b{display:block;font-size:20px}
.status small,.section-summary,.media-explanation,.usage{color:var(--muted)}
section{margin-top:24px;background:var(--panel);border:1px solid var(--border);border-radius:var(--r);padding:26px;box-shadow:var(--shadow)}
.callout{margin:14px 0;padding:13px 15px;border-radius:10px}
.callout-key{border:2px solid var(--key-border);background:#fffbed}
.callout-source{border-left:4px solid var(--cyan);background:#edfafa}
.callout-recommendation{border-left:4px solid var(--green);background:#ecfdf5}
.callout-inference{border-left:4px solid var(--purple);background:#f5f0ff}
.callout-notice{border-left:4px solid var(--orange);background:#fff4e8}
.table-wrap,.codebox{max-width:100%;overflow:auto}
.table-wrap{margin:16px 0;border:1px solid var(--border);border-radius:14px}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;vertical-align:top;padding:11px;border-bottom:1px solid var(--border)}
th{background:#fafafe}
.figure-card{margin:18px 0;border:1px solid var(--border);border-radius:15px;overflow:hidden;background:#fff}
.figure-card img{display:block;width:100%;max-height:560px;object-fit:contain;background:#111827}
.card-copy{padding:16px}
.media-headline{margin:0 0 6px}
.media-explanation,.usage{margin:6px 0}
.media-timestamp{margin:7px 0 0;color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.feature-toolbar{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}
.filter-btn{border:1px solid var(--border);background:#fff;color:var(--text);padding:8px 11px;border-radius:10px;cursor:pointer}
.filter-btn.active,.filter-btn:hover{background:var(--accent);border-color:var(--accent);color:#fff}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.feature-card{border:1px solid var(--border);border-radius:15px;padding:16px;background:#fff}
.feature-card[hidden]{display:none}
.tag{display:inline-block;margin-bottom:9px;font-size:12px;border-radius:999px;padding:3px 8px;background:#f1f3f8;color:var(--muted)}
code,kbd{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.codebox{position:relative;margin:16px 0;background:#1f2430;border:1px solid #2c3343;border-radius:14px}
pre{margin:0;padding:52px 18px 18px;color:#e9edf5;overflow:auto;font-size:13px;line-height:1.6;white-space:pre}
.copy{position:absolute;right:10px;top:10px;border:0;border-radius:9px;padding:7px 10px;cursor:pointer}
.copy-status{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.flow-diagram{margin:16px 0;border:1px solid var(--border);border-radius:16px;background:#fafbfe;overflow:auto}
.flow-diagram svg{display:block;width:100%;height:auto}
.flow-edge{stroke:var(--accent);stroke-width:3;fill:none}
.flow-node{fill:#fff;stroke:var(--border);stroke-width:2}
.flow-node-label{fill:var(--text);font:700 14px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
.flow-edge-label{fill:var(--muted);font:12px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
.accordion{border:1px solid var(--border);border-radius:14px;overflow:hidden;margin-top:10px}
.accordion-toggle{width:100%;border:0;background:#fff;text-align:left;padding:14px 16px;font-weight:700;font-size:15px;cursor:pointer;display:flex;justify-content:space-between}
.accordion-panel{border-top:1px solid var(--border);padding:15px;background:#fbfbfe}
img,video,svg,canvas{max-width:100%;height:auto}
.footer{color:var(--muted);font-size:13px;padding:24px 4px}
@media(max-width:960px){
  .shell{grid-template-columns:1fr;padding:13px}
  nav{position:static}
  .cards{grid-template-columns:1fr 1fr}
  .status-grid{grid-template-columns:1fr}
}
@media(max-width:620px){
  .cards{grid-template-columns:1fr}
  section,.hero{padding:19px}
}
@media print{
  nav,.feature-toolbar,.copy,.accordion-toggle,.skip-link{display:none!important}
  .shell{display:block;max-width:none;padding:0}
  main{width:100%}
  section,.hero,.figure-card,.feature-card{box-shadow:none;break-inside:avoid}
  .feature-card[hidden]{display:block!important}
  .accordion-panel[hidden]{display:block}
}
"""

_WEB_SCRIPT = """
document.querySelectorAll('.feature-toolbar').forEach((toolbar) => {
  const cards = toolbar.closest('.feature-block').querySelectorAll('.feature-card');
  toolbar.querySelectorAll('.filter-btn').forEach((button) => {
    button.addEventListener('click', () => {
      const selected = button.dataset.filter;
      const showAll = button.dataset.filterMode === 'all';
      button.setAttribute('aria-pressed', 'true');
      toolbar.querySelectorAll('.filter-btn').forEach((item) => {
        const active = item === button;
        item.setAttribute('aria-pressed', String(active));
        item.classList.toggle('active', active);
      });
      cards.forEach((card) => {
        card.hidden = !showAll && card.dataset.category !== selected;
      });
    });
  });
});

document.querySelectorAll('.accordion-toggle').forEach((button) => {
  button.addEventListener('click', () => {
    const expanded = button.getAttribute('aria-expanded') === 'true';
    const panel = document.getElementById(button.getAttribute('aria-controls'));
    button.setAttribute('aria-expanded', String(!expanded));
    panel.hidden = expanded;
    button.querySelector('.accordion-symbol').textContent = expanded ? '＋' : '−';
  });
});

async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.setAttribute('readonly', '');
  helper.style.position = 'fixed';
  helper.style.opacity = '0';
  document.body.appendChild(helper);
  let copied = false;
  try {
    helper.select();
    copied = document.execCommand('copy');
  } finally {
    helper.remove();
  }
  if (!copied) throw new Error('copy unavailable');
}

document.querySelectorAll('.copy').forEach((button) => {
  button.addEventListener('click', async () => {
    const code = document.getElementById(button.dataset.copyTarget);
    const status = document.getElementById(button.dataset.statusTarget);
    try {
      await copyText(code.textContent);
      status.textContent = '已复制';
    } catch (error) {
      status.textContent = '复制失败，请手动复制';
    }
  });
});
"""


def _attribute(value) -> str:
    return html.escape(str(value), quote=True)


def _timestamp_label(seconds) -> str:
    whole_seconds = int(seconds)
    milliseconds = round((seconds - whole_seconds) * 1000)
    if milliseconds == 1000:
        whole_seconds += 1
        milliseconds = 0
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, second = divmod(remainder, 60)
    second_text = f"{second:02d}"
    if milliseconds:
        second_text += "." + f"{milliseconds:03d}".rstrip("0")
    if hours:
        return f"{hours:02d}:{minutes:02d}:{second_text}"
    return f"{minutes:02d}:{second_text}"


def _id_token(source_id: str) -> str:
    encoded = base64.urlsafe_b64encode(source_id.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def _source_dom_id(source_id: str) -> str:
    return f"vn-src-{_id_token(source_id)}"


def _owned_dom_id(role: str, source_id: str | None = None) -> str:
    if source_id is None:
        return f"vn-owned-{role}"
    return f"vn-owned-{role}-{_id_token(source_id)}"


def _render_block(block: dict, base_dir: Path | None) -> str:
    kind = block["type"]
    block_id = _source_dom_id(block["id"])
    if kind == "paragraph":
        return f'<p id="{block_id}">{html.escape(block["text"])}</p>'
    if kind == "callout":
        return (
            f'<aside id="{block_id}" class="callout callout-{block["kind"]}">'
            f'{html.escape(block["text"])}</aside>'
        )
    if kind == "list":
        items = "".join(f"<li>{html.escape(item)}</li>" for item in block["items"])
        return f'<ul id="{block_id}">{items}</ul>'
    if kind == "table":
        header = "".join(
            f'<th scope="col">{html.escape(column["label"])}</th>'
            for column in block["columns"]
        )
        rows = "".join(
            "<tr>"
            + "".join(
                f"<td>{html.escape(row[column['id']])}</td>"
                for column in block["columns"]
            )
            + "</tr>"
            for row in block["rows"]
        )
        return (
            f'<div class="table-wrap" tabindex="0" aria-label="表格，横向滚动查看">'
            f'<table id="{block_id}"><thead><tr>{header}</tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
        )
    if kind == "media":
        timestamp = (
            f'<p class="media-timestamp">时间点：'
            f'{html.escape(_timestamp_label(block["timestamp_seconds"]))}</p>'
            if block["timestamp_seconds"] is not None
            else ""
        )
        usage = (
            f'<p class="usage">{html.escape(block["usage"])}</p>'
            if block["usage"]
            else ""
        )
        deep_link = (
            f'<p><a href="{_attribute(block["deep_link"])}" target="_blank" '
            'rel="noopener noreferrer">打开对应视频位置</a></p>'
            if block["deep_link"]
            else ""
        )
        return (
            f'<article id="{block_id}" class="figure-card">'
            f'<img src="{_image_uri(block["image"], base_dir)}" '
            f'alt="{_attribute(block["alt"])}">'
            '<div class="card-copy">'
            f'<h3 class="media-headline">{html.escape(block["headline"])}</h3>'
            f'<p class="media-explanation">{html.escape(block["explanation"])}</p>'
            f"{timestamp}{usage}{deep_link}</div></article>"
        )
    if kind == "feature_grid":
        categories = list(dict.fromkeys(card["category"] for card in block["cards"]))
        buttons = [
            '<button type="button" class="filter-btn active" aria-pressed="true" '
            'data-filter-mode="all">全部</button>'
        ]
        buttons.extend(
            '<button type="button" class="filter-btn" aria-pressed="false" '
            f'data-filter-mode="category" data-filter="{_attribute(category)}">'
            f"{html.escape(category)}</button>"
            for category in categories
        )
        cards = []
        for card in block["cards"]:
            link = (
                f'<p><a href="{_attribute(card["link"])}" target="_blank" '
                'rel="noopener noreferrer">查看详情</a></p>'
                if card["link"]
                else ""
            )
            cards.append(
                '<article class="feature-card" '
                f'data-category="{_attribute(card["category"])}">'
                f'<span class="tag">{html.escape(card["category"])}</span>'
                f'<h3>{html.escape(card["title"])}</h3>'
                f'<p>{html.escape(card["description"])}</p>{link}</article>'
            )
        return (
            f'<div id="{block_id}" class="feature-block">'
            f'<div class="feature-toolbar" role="group" '
            f'aria-label="{_attribute(block["filter_label"])} 筛选">'
            f'{"".join(buttons)}</div><div class="feature-grid cards">'
            f'{"".join(cards)}</div></div>'
        )
    if kind == "code":
        code_id = _owned_dom_id("code", block["id"])
        status_id = _owned_dom_id("copy-status", block["id"])
        return (
            f'<div id="{block_id}" class="codebox">'
            f'<button type="button" class="copy" data-copy-target="{code_id}" '
            f'data-status-target="{status_id}" aria-label="复制代码">复制</button>'
            f'<pre><code id="{code_id}" class="language-{_attribute(block["language"])}">'
            f'{html.escape(block["text"])}</code></pre>'
            f'<span id="{status_id}" class="copy-status" aria-live="polite"></span>'
            "</div>"
        )
    if kind == "flow":
        labels = {node["id"]: node["label"] for node in block["nodes"]}
        if block["edges"]:
            items = [
                f"{labels[edge['from']]} → {labels[edge['to']]}"
                + (f": {edge['label']}" if edge["label"] else "")
                for edge in block["edges"]
            ]
        else:
            items = [node["label"] for node in block["nodes"]]
        accessible_label = "流程图" + (f"：{'；'.join(items)}" if items else "")
        node_width = 140
        node_height = 54
        node_gap = 50
        margin = 30
        diagram_height = 170
        positions = {
            node["id"]: (
                margin + index * (node_width + node_gap),
                72,
            )
            for index, node in enumerate(block["nodes"])
        }
        diagram_width = max(
            360,
            margin * 2
            + max(1, len(block["nodes"])) * node_width
            + max(0, len(block["nodes"]) - 1) * node_gap,
        )
        marker_id = _owned_dom_id("flow-arrow", block["id"])
        title_id = _owned_dom_id("flow-title", block["id"])
        edges = []
        for edge in block["edges"]:
            source_x, source_y = positions[edge["from"]]
            target_x, target_y = positions[edge["to"]]
            x1 = source_x + node_width / 2
            y1 = source_y + node_height / 2
            x2 = target_x + node_width / 2
            y2 = target_y + node_height / 2
            label = (
                f'<text class="flow-edge-label" x="{(x1 + x2) / 2:g}" '
                f'y="{min(y1, y2) - 38:g}" text-anchor="middle">'
                f'{html.escape(edge["label"])}</text>'
                if edge["label"]
                else ""
            )
            edges.append(
                f'<path class="flow-edge" d="M {x1:g} {y1:g} L {x2:g} {y2:g}" '
                f'marker-end="url(#{marker_id})"/>{label}'
            )
        nodes = []
        for node in block["nodes"]:
            x, y = positions[node["id"]]
            visible_label = (
                node["label"]
                if len(node["label"]) <= 18
                else node["label"][:17] + "…"
            )
            nodes.append(
                f'<g><rect class="flow-node" x="{x:g}" y="{y:g}" '
                f'width="{node_width}" height="{node_height}" rx="16"/>'
                f'<text class="flow-node-label" x="{x + node_width / 2:g}" '
                f'y="{y + 33:g}" text-anchor="middle">'
                f"{html.escape(visible_label)}</text></g>"
            )
        return (
            f'<figure id="{block_id}" class="flow-diagram">'
            f'<svg viewBox="0 0 {diagram_width} {diagram_height}" role="img" '
            f'aria-labelledby="{title_id}"><title id="{title_id}">'
            f"{html.escape(accessible_label)}</title>"
            f'<defs><marker id="{marker_id}" markerWidth="10" markerHeight="10" '
            'refX="8" refY="3" orient="auto" markerUnits="strokeWidth">'
            '<path d="M0,0 L0,6 L9,3 z" fill="#6558e8"/></marker></defs>'
            f'{"".join(edges)}{"".join(nodes)}</svg></figure>'
        )
    if kind == "accordion":
        toggle_id = _owned_dom_id("accordion-toggle", block["id"])
        panel_id = _owned_dom_id("accordion-panel", block["id"])
        children = "".join(_render_block(child, base_dir) for child in block["blocks"])
        return (
            f'<div id="{block_id}" class="accordion">'
            f'<button type="button" id="{toggle_id}" class="accordion-toggle" '
            f'aria-expanded="false" aria-controls="{panel_id}">'
            f'<span>{html.escape(block["title"])}</span>'
            '<span class="accordion-symbol" aria-hidden="true">＋</span></button>'
            f'<div id="{panel_id}" class="accordion-panel" role="region" '
            f'aria-labelledby="{toggle_id}" hidden>{children}</div></div>'
        )
    raise AssertionError(f"unknown normalized block: {kind}")


def _render_email_block(block: dict, base_dir: Path | None) -> str:
    """Render one validated block without CSS, JavaScript, or hidden content."""
    kind = block["type"]
    block_id = _source_dom_id(block["id"])
    text_style = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;color:#1d2330;line-height:1.65;"
    if kind == "paragraph":
        return f'<p id="{block_id}" style="{text_style}margin:14px 0;">{html.escape(block["text"])}</p>'
    if kind == "callout":
        colors = {
            "key": ("#fffbed", "#e8c948"),
            "source": ("#edfafa", "#0f8b99"),
            "recommendation": ("#ecfdf5", "#1f9d68"),
            "inference": ("#f5f0ff", "#7c4dce"),
            "notice": ("#fff4e8", "#e98324"),
        }
        background, border = colors[block["kind"]]
        return (
            f'<aside id="{block_id}" style="{text_style}margin:16px 0;padding:14px 16px;'
            f'background:{background};border-left:4px solid {border};">'
            f'{html.escape(block["text"])}</aside>'
        )
    if kind == "list":
        items = "".join(f"<li>{html.escape(item)}</li>" for item in block["items"])
        return f'<ul id="{block_id}" style="{text_style}margin:14px 0;padding-left:24px;">{items}</ul>'
    if kind == "table":
        header = "".join(
            f'<th scope="col" style="padding:10px;text-align:left;vertical-align:top;background:#f6f7fb;border-bottom:1px solid #e4e7ec;">{html.escape(column["label"])}</th>'
            for column in block["columns"]
        )
        rows = "".join(
            "<tr>"
            + "".join(
                f'<td style="padding:10px;text-align:left;vertical-align:top;border-bottom:1px solid #e4e7ec;">{html.escape(row[column["id"]])}</td>'
                for column in block["columns"]
            )
            + "</tr>"
            for row in block["rows"]
        )
        return (
            f'<table id="{block_id}" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="{text_style}margin:16px 0;border:1px solid #e4e7ec;border-collapse:collapse;">'
            f"<thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>"
        )
    if kind == "media":
        timestamp = (
            f'<p style="{text_style}margin:7px 0 0;color:#667085;font-size:13px;">'
            f'时间点：{html.escape(_timestamp_label(block["timestamp_seconds"]))}</p>'
            if block["timestamp_seconds"] is not None
            else ""
        )
        usage = (
            f'<p style="{text_style}margin:8px 0;color:#667085;">{html.escape(block["usage"])}</p>'
            if block["usage"]
            else ""
        )
        deep_link = (
            f'<p style="{text_style}margin:10px 0 0;"><a href="{_attribute(block["deep_link"])}" '
            'target="_blank" rel="noopener noreferrer" style="color:#6558e8;text-decoration:underline;">打开对应视频位置</a></p>'
            if block["deep_link"]
            else ""
        )
        return (
            f'<table id="{block_id}" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="margin:18px 0;border:1px solid #e4e7ec;border-collapse:separate;border-spacing:0;background:#ffffff;">'
            f'<tr><td><img src="{_image_uri(block["image"], base_dir)}" alt="{_attribute(block["alt"])}" '
            'style="display:block;width:100%;max-width:100%;height:auto;background:#111827;"></td></tr>'
            f'<tr><td style="padding:16px;"><h3 style="{text_style}margin:0 0 8px;font-size:19px;">{html.escape(block["headline"])}</h3>'
            f'<p style="{text_style}margin:0;color:#667085;">{html.escape(block["explanation"])}</p>{timestamp}{usage}{deep_link}</td></tr></table>'
        )
    if kind == "feature_grid":
        categories = list(dict.fromkeys(card["category"] for card in block["cards"]))
        groups = []
        for category in categories:
            cards = []
            for card in (item for item in block["cards"] if item["category"] == category):
                link = (
                    f'<p style="{text_style}margin:10px 0 0;"><a href="{_attribute(card["link"])}" '
                    'target="_blank" rel="noopener noreferrer" style="color:#6558e8;text-decoration:underline;">查看详情</a></p>'
                    if card["link"]
                    else ""
                )
                cards.append(
                    f'<article style="margin:10px 0;padding:14px;border:1px solid #e4e7ec;background:#ffffff;">'
                    f'<h4 style="{text_style}margin:0 0 7px;font-size:16px;">{html.escape(card["title"])}</h4>'
                    f'<p style="{text_style}margin:0;color:#667085;">{html.escape(card["description"])}</p>{link}</article>'
                )
            groups.append(
                f'<section style="margin:16px 0;"><h3 style="{text_style}margin:0 0 8px;font-size:18px;color:#6558e8;">'
                f'{html.escape(category)}</h3>{"".join(cards)}</section>'
            )
        return f'<div id="{block_id}" style="margin:18px 0;">{"".join(groups)}</div>'
    if kind == "code":
        return (
            f'<pre id="{block_id}" style="margin:16px 0;padding:16px;background:#1f2430;color:#e9edf5;'
            'font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;">'
            f'<code>{html.escape(block["text"])}</code></pre>'
        )
    if kind == "flow":
        labels = {node["id"]: node["label"] for node in block["nodes"]}
        if block["edges"]:
            items = [
                f"{labels[edge['from']]} → {labels[edge['to']]}"
                + (f": {edge['label']}" if edge["label"] else "")
                for edge in block["edges"]
            ]
            connected = {
                endpoint
                for edge in block["edges"]
                for endpoint in (edge["from"], edge["to"])
            }
            items.extend(
                node["label"]
                for node in block["nodes"]
                if node["id"] not in connected
            )
        else:
            items = [node["label"] for node in block["nodes"]]
        return (
            f'<ol id="{block_id}" style="{text_style}margin:16px 0;padding-left:24px;">'
            + "".join(f"<li style=\"margin:6px 0;\">{html.escape(item)}</li>" for item in items)
            + "</ol>"
        )
    if kind == "accordion":
        children = "".join(_render_email_block(child, base_dir) for child in block["blocks"])
        return (
            f'<section id="{block_id}" style="margin:18px 0;padding:16px;border:1px solid #e4e7ec;background:#fbfbfe;">'
            f'<h3 style="{text_style}margin:0 0 10px;font-size:19px;">{html.escape(block["title"])}</h3>{children}</section>'
        )
    raise AssertionError(f"unknown normalized block: {kind}")


def _render_email_video_note(note: dict, *, base_dir: str | Path | None = None) -> str:
    """Render an Apple Mail/static-preview HTML document with inline styles only."""
    base = None if base_dir is None else Path(base_dir)
    normalized = normalize_video_note(note, base_dir=base)
    meta = normalized["meta"]
    title = html.escape(meta["title"])
    badge = f"{html.escape(meta['platform'])} · {html.escape(meta['source_id'])}"
    author = html.escape(meta["author"] or "未提供")
    navigation = " · ".join(
        f'<a href="#{_source_dom_id(section["id"])}" style="color:#6558e8;text-decoration:underline;">{html.escape(section["title"])}</a>'
        for section in normalized["sections"]
    )
    sections = "".join(
        f'<section id="{_source_dom_id(section["id"])}" style="margin:24px 0;padding:24px;background:#ffffff;border:1px solid #e4e7ec;">'
        f'<h2 style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',\'PingFang SC\',\'Hiragino Sans GB\',\'Microsoft YaHei\',sans-serif;color:#1d2330;line-height:1.3;margin:0 0 10px;font-size:26px;">{html.escape(section["title"])}</h2>'
        f'<p style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',\'PingFang SC\',\'Hiragino Sans GB\',\'Microsoft YaHei\',sans-serif;color:#667085;line-height:1.65;margin:0 0 16px;">{html.escape(section["summary"])}</p>'
        f'{"".join(_render_email_block(block, base) for block in section["blocks"])}</section>'
        for section in normalized["sections"]
    )
    return f"""<!doctype html>
<html lang="{_attribute(meta["language"])}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="icon" href="data:image/svg+xml;base64,{_FAVICON}">
</head>
<body style="margin:0;background:#f6f7fb;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background:#f6f7fb;border-collapse:collapse;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:760px;border-collapse:collapse;">
<tr><td style="padding:28px;background:#6558e8;color:#ffffff;">
<p style="margin:0 0 10px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;font-size:13px;font-weight:700;">{badge}</p>
<h1 style="margin:0 0 12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;font-size:36px;line-height:1.15;">{title}</h1>
<p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;font-size:17px;line-height:1.65;">{html.escape(normalized["summary"])}</p>
</td></tr>
<tr><td style="padding:16px 24px;background:#ffffff;border:1px solid #e4e7ec;">
<p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;color:#667085;line-height:1.65;">作者：{author}　时长：{meta["duration_seconds"]:g} 秒　语言：{html.escape(meta["language"])}</p>
</td></tr>
<tr><td style="padding:16px 24px;background:#ffffff;border-left:1px solid #e4e7ec;border-right:1px solid #e4e7ec;">
<p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;color:#667085;line-height:1.65;">{navigation}</p>
</td></tr>
<tr><td>{sections}</td></tr>
<tr><td style="padding:18px 4px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;color:#667085;font-size:13px;line-height:1.65;">由 Codex 视频笔记工作流生成；媒体已嵌入，可离线打开或打印。</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def render_video_note(
    note: dict, *, base_dir: str | Path | None = None, profile: str = "web"
) -> str:
    """Render a strict, self-contained and deterministic v2 web note."""
    if profile == "email":
        return _render_email_video_note(note, base_dir=base_dir)
    if profile != "web":
        _fail(f"profile is unsupported: {profile}")
    base = None if base_dir is None else Path(base_dir)
    normalized = normalize_video_note(note, base_dir=base)
    meta = normalized["meta"]
    title = html.escape(meta["title"])
    badge = f"{html.escape(meta['platform'])} · {html.escape(meta['source_id'])}"
    author = html.escape(meta["author"] or "未提供")
    navigation = "".join(
        f'<a href="#{_source_dom_id(section["id"])}">'
        f'{html.escape(section["title"])}</a>'
        for section in normalized["sections"]
    )
    sections = "".join(
        f'<section id="{_source_dom_id(section["id"])}" '
        f'aria-labelledby="{_owned_dom_id("section-title", section["id"])}">'
        f'<h2 id="{_owned_dom_id("section-title", section["id"])}">'
        f'{html.escape(section["title"])}</h2>'
        f'<p class="section-summary">{html.escape(section["summary"])}</p>'
        f'{"".join(_render_block(block, base) for block in section["blocks"])}</section>'
        for section in normalized["sections"]
    )
    return f"""<!doctype html>
<html lang="{_attribute(meta["language"])}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="icon" href="data:image/svg+xml;base64,{_FAVICON}">
<style>{_WEB_STYLE}</style>
</head>
<body>
<a class="skip-link" href="#vn-owned-main">跳到主要内容</a>
<div class="shell">
<nav aria-label="视频笔记目录"><strong>目录</strong>{navigation}</nav>
<main id="vn-owned-main" tabindex="-1">
<header class="hero">
<span class="badge">{badge}</span>
<h1>{title}</h1>
<p class="lead">{html.escape(normalized["summary"])}</p>
<div class="status-grid">
<div class="status"><b>{author}</b><small>作者</small></div>
<div class="status"><b>{meta["duration_seconds"]:g} 秒</b><small>时长</small></div>
<div class="status"><b>{html.escape(meta["language"])}</b><small>语言</small></div>
</div>
</header>
{sections}
<footer class="footer">由 Codex 视频笔记工作流生成；媒体已嵌入，可离线打开或打印。</footer>
</main>
</div>
<script>{_WEB_SCRIPT}</script>
</body>
</html>
"""


def render_document(meta: dict, sections: list) -> str:
    """Retain the legacy Bilibili caller API while rendering through v2."""
    normalized = normalize_legacy_document(meta, sections)
    return render_video_note(normalized)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a portable video-note/v2 document.")
    parser.add_argument("--note", required=True, type=Path, help="Input note JSON path")
    parser.add_argument("--output", required=True, type=Path, help="Output HTML path")
    parser.add_argument("--profile", required=True, help="Renderer profile (web or email)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    note_path = args.note
    output_path = args.output
    try:
        if args.profile not in {"web", "email"}:
            _fail(f"profile is unsupported: {args.profile}")
        try:
            note = json.loads(note_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _fail(
                f"note JSON is invalid at line {exc.lineno}, column {exc.colno}: "
                f"{note_path}"
            )
        document = render_video_note(note, base_dir=note_path.parent, profile=args.profile)
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"unexpected renderer error: {exc}", file=sys.stderr)
        return 4

    try:
        document_bytes = document.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        print(f"unexpected renderer error: {exc}", file=sys.stderr)
        return 4

    temporary_path = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(document_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    except OSError as exc:
        print(f"output I/O error: {output_path}: {exc}", file=sys.stderr)
        return 3
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
