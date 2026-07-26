"""Normalize and safely render portable video-note/v2 documents."""

from __future__ import annotations

import base64
import html
import json
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
)
_SENTINEL = "evidence_sentinel"
_IMAGE_SUFFIXES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def stable_json_dumps(note: dict) -> str:
    """Return canonical JSON suitable for byte-for-byte fixture comparison."""
    return json.dumps(note, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _fail(message: str) -> None:
    raise ValueError(message)


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
                _fail(f"{path}.{key} is not reader-facing content")
            _reject_evidence(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_evidence(child, f"{path}[{index}]")
    elif isinstance(value, str) and _SENTINEL in value.casefold().replace("-", "_"):
        _fail(f"{path} contains the evidence sentinel")


def _http_url(value, name: str) -> str:
    value = _text(value, name)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        _fail(f"{name} must use HTTP or HTTPS")
    return value


def _image_path(value, base_dir: Path | None) -> str:
    raw = _text(value, "media.image")
    path = Path(raw)
    if path.suffix.casefold() not in _IMAGE_SUFFIXES:
        _fail("media.image must be JPEG, PNG, or WebP")
    if not path.is_absolute() and ".." in path.parts:
        _fail("media.image must stay within its base directory")
    candidate = path if path.is_absolute() or base_dir is None else base_dir / path
    if base_dir is not None and not candidate.is_file():
        _fail(f"media.image does not exist: {raw}")
    return raw


def _block_id(value: dict, used_ids: set[str], name: str) -> str:
    block_id = _text(value.get("id"), f"{name}.id")
    if block_id in used_ids:
        _fail(f"IDs must be globally unique: {block_id}")
    used_ids.add(block_id)
    return block_id


def _normalize_block(value, used_ids: set[str], base_dir: Path | None, *, nested: bool = False) -> dict:
    block = _object(value, "block")
    block_type = _text(block.get("type"), "block.type")
    allowed_nested = {"paragraph", "list", "callout"}
    if nested and block_type not in allowed_nested:
        _fail("accordion blocks may only be paragraph, list, or callout")
    block_id = _block_id(block, used_ids, "block")

    if block_type == "paragraph":
        _keys(block, "paragraph", {"id", "type", "text"}, {"id", "type", "text"})
        return {"id": block_id, "type": block_type, "text": _text(block["text"], "paragraph.text")}

    if block_type == "callout":
        _keys(block, "callout", {"id", "type", "kind", "text"}, {"id", "type", "kind", "text"})
        kind = _text(block["kind"], "callout.kind")
        if kind not in {"key", "source", "recommendation", "inference", "notice"}:
            _fail("callout.kind is unsupported")
        return {"id": block_id, "type": block_type, "kind": kind, "text": _text(block["text"], "callout.text")}

    if block_type == "list":
        _keys(block, "list", {"id", "type", "items"}, {"id", "type", "items"})
        items = [_text(item, "list item") for item in _list(block["items"], "list.items")]
        if not items:
            _fail("list.items must not be empty")
        return {"id": block_id, "type": block_type, "items": items}

    if block_type == "table":
        _keys(block, "table", {"id", "type", "columns", "rows"}, {"id", "type", "columns", "rows"})
        columns = []
        column_ids = set()
        for column in _list(block["columns"], "table.columns"):
            column = _object(column, "table column")
            _keys(column, "table column", {"id", "label"}, {"id", "label"})
            column_id = _text(column["id"], "table column.id")
            if column_id in column_ids:
                _fail(f"table columns must be unique: {column_id}")
            column_ids.add(column_id)
            columns.append({"id": column_id, "label": _text(column["label"], "table column.label")})
        if not columns:
            _fail("table.columns must not be empty")
        rows = []
        for row in _list(block["rows"], "table.rows"):
            row = _object(row, "table row")
            if set(row) != column_ids:
                _fail("table rows must contain exactly the declared columns")
            normalized_row = {}
            for column_id in columns:
                value = row[column_id["id"]]
                if isinstance(value, (dict, list)) or isinstance(value, bool) or value is None:
                    _fail("table cell values must be text or numbers")
                normalized_row[column_id["id"]] = str(value)
            rows.append(normalized_row)
        return {"id": block_id, "type": block_type, "columns": columns, "rows": rows}

    if block_type == "media":
        _keys(
            block,
            "media",
            {"id", "type", "image", "alt", "headline", "explanation"},
            {"id", "type", "image", "alt", "headline", "explanation", "usage", "timestamp_seconds", "deep_link"},
        )
        timestamp = block.get("timestamp_seconds")
        if timestamp is not None and (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or timestamp < 0):
            _fail("media.timestamp_seconds must be non-negative")
        deep_link = block.get("deep_link")
        return {
            "id": block_id,
            "type": block_type,
            "image": _image_path(block["image"], base_dir),
            "alt": _text(block["alt"], "media.alt"),
            "headline": _text(block["headline"], "media.headline"),
            "explanation": _text(block["explanation"], "media.explanation"),
            "usage": _text(block.get("usage", ""), "media.usage", allow_empty=True),
            "timestamp_seconds": timestamp,
            "deep_link": None if deep_link is None else _http_url(deep_link, "media.deep_link"),
        }

    if block_type == "feature_grid":
        _keys(block, "feature_grid", {"id", "type", "cards"}, {"id", "type", "cards", "filter_label"})
        cards = []
        card_ids = set()
        for card in _list(block["cards"], "feature_grid.cards"):
            card = _object(card, "feature card")
            _keys(card, "feature card", {"id", "category", "title", "description"}, {"id", "category", "title", "description", "link"})
            card_id = _text(card["id"], "feature card.id")
            if card_id in card_ids:
                _fail(f"feature card IDs must be unique: {card_id}")
            card_ids.add(card_id)
            link = card.get("link")
            cards.append({
                "id": card_id,
                "category": _text(card["category"], "feature card.category"),
                "title": _text(card["title"], "feature card.title"),
                "description": _text(card["description"], "feature card.description"),
                "link": None if link is None else _http_url(link, "feature card.link"),
            })
        if not cards:
            _fail("feature_grid.cards must not be empty")
        return {
            "id": block_id,
            "type": block_type,
            "filter_label": _text(block.get("filter_label", "category"), "feature_grid.filter_label"),
            "cards": cards,
        }

    if block_type == "code":
        _keys(block, "code", {"id", "type", "text"}, {"id", "type", "text", "language"})
        return {
            "id": block_id,
            "type": block_type,
            "text": _text(block["text"], "code.text", allow_empty=True),
            "language": _text(block.get("language", "text"), "code.language"),
        }

    if block_type == "flow":
        _keys(block, "flow", {"id", "type", "nodes", "edges"}, {"id", "type", "nodes", "edges"})
        nodes = []
        node_ids = set()
        for node in _list(block["nodes"], "flow.nodes"):
            node = _object(node, "flow node")
            _keys(node, "flow node", {"id", "label"}, {"id", "label"})
            node_id = _text(node["id"], "flow node.id")
            if node_id in node_ids:
                _fail(f"flow node IDs must be unique: {node_id}")
            node_ids.add(node_id)
            nodes.append({"id": node_id, "label": _text(node["label"], "flow node.label")})
        edges = []
        for edge in _list(block["edges"], "flow.edges"):
            edge = _object(edge, "flow edge")
            _keys(edge, "flow edge", {"from", "to"}, {"from", "to", "label"})
            source = _text(edge["from"], "flow edge.from")
            target = _text(edge["to"], "flow edge.to")
            if source not in node_ids or target not in node_ids:
                _fail("flow edges must reference declared nodes")
            edges.append({"from": source, "to": target, "label": _text(edge.get("label", ""), "flow edge.label", allow_empty=True)})
        return {"id": block_id, "type": block_type, "nodes": nodes, "edges": edges}

    if block_type == "accordion":
        _keys(block, "accordion", {"id", "type", "title", "blocks"}, {"id", "type", "title", "blocks"})
        child_blocks = [_normalize_block(child, used_ids, base_dir, nested=True) for child in _list(block["blocks"], "accordion.blocks")]
        if not child_blocks:
            _fail("accordion.blocks must not be empty")
        return {"id": block_id, "type": block_type, "title": _text(block["title"], "accordion.title"), "blocks": child_blocks}

    _fail(f"unsupported block type: {block_type}")


def normalize_video_note(note: dict, *, base_dir: str | Path | None = None) -> dict:
    """Validate a strict v2 note and return its deterministic normalized form."""
    _reject_evidence(note)
    note = _object(note, "note")
    _keys(note, "note", {"schema_version", "meta", "summary", "sections"}, {"schema_version", "meta", "summary", "sections"})
    if note["schema_version"] != SCHEMA_VERSION:
        _fail(f"schema_version must be {SCHEMA_VERSION}")
    meta = _object(note["meta"], "meta")
    _keys(meta, "meta", {"platform", "source_id", "source_url", "title", "author", "duration_seconds", "language"}, {"platform", "source_id", "source_url", "title", "author", "duration_seconds", "language"})
    duration = meta["duration_seconds"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        _fail("meta.duration_seconds must be non-negative")
    normalized_meta = {
        "platform": _text(meta["platform"], "meta.platform"),
        "source_id": _text(meta["source_id"], "meta.source_id"),
        "source_url": _http_url(meta["source_url"], "meta.source_url"),
        "title": _text(meta["title"], "meta.title"),
        "author": _text(meta["author"], "meta.author", allow_empty=True),
        "duration_seconds": duration,
        "language": _text(meta["language"], "meta.language"),
    }
    base = None if base_dir is None else Path(base_dir)
    used_ids = set()
    sections = []
    section_ids = set()
    for section in _list(note["sections"], "sections"):
        section = _object(section, "section")
        _keys(section, "section", {"id", "title", "summary", "blocks"}, {"id", "title", "summary", "blocks"})
        section_id = _text(section["id"], "section.id")
        if section_id in section_ids or section_id in used_ids:
            _fail(f"IDs must be globally unique: {section_id}")
        section_ids.add(section_id)
        used_ids.add(section_id)
        blocks = [_normalize_block(block, used_ids, base) for block in _list(section["blocks"], "section.blocks")]
        sections.append({
            "id": section_id,
            "title": _text(section["title"], "section.title"),
            "summary": _text(section["summary"], "section.summary"),
            "blocks": blocks,
        })
    if not sections:
        _fail("sections must not be empty")
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": normalized_meta,
        "summary": _text(note["summary"], "summary"),
        "sections": sorted(sections, key=lambda item: item["id"]),
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
        rows = legacy_section.get("rows", [])
        if rows:
            blocks.append({
                "id": f"{section_id}-table",
                "type": "table",
                "columns": [{"id": "name", "label": "名称"}, {"id": "usage", "label": "用途"}, {"id": "points", "label": "要点"}],
                "rows": [
                    {"name": str(row[0]), "usage": str(row[1]), "points": str(row[2])}
                    for row in rows
                    if isinstance(row, (list, tuple)) and len(row) == 3
                ],
            })
        for card_index, card in enumerate(legacy_section.get("cards", []), start=1):
            card = _object(card, "legacy card")
            _keys(card, "legacy card", {"image", "label", "analysis"}, {"image", "label", "analysis", "quote"})
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


def _render_block(block: dict, base_dir: Path | None) -> str:
    kind = block["type"]
    if kind == "paragraph":
        return f'<p>{html.escape(block["text"])}</p>'
    if kind == "callout":
        return f'<aside class="callout callout-{block["kind"]}">{html.escape(block["text"])}</aside>'
    if kind == "list":
        return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in block["items"]) + "</ul>"
    if kind == "table":
        header = "".join(f"<th>{html.escape(column["label"])}</th>" for column in block["columns"])
        rows = "".join("<tr>" + "".join(f"<td>{html.escape(row[column["id"]])}</td>" for column in block["columns"]) + "</tr>" for row in block["rows"])
        return f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table></div>'
    if kind == "media":
        usage = f'<p class="usage">{html.escape(block["usage"])}</p>' if block["usage"] else ""
        return f'<article class="figure-card"><img src="{_image_uri(block["image"], base_dir)}" alt="{html.escape(block["alt"])}"><div class="card-copy"><p class="time">{html.escape(block["headline"])}</p><p class="analysis">{html.escape(block["explanation"])}</p>{usage}</div></article>'
    if kind == "feature_grid":
        return '<div class="feature-grid">' + "".join(f'<article data-category="{html.escape(card["category"])}"><h3>{html.escape(card["title"])}</h3><p>{html.escape(card["description"])}</p></article>' for card in block["cards"]) + "</div>"
    if kind == "code":
        return f'<pre><code class="language-{html.escape(block["language"])}">{html.escape(block["text"])}</code></pre>'
    if kind == "flow":
        labels = {node["id"]: node["label"] for node in block["nodes"]}
        return '<ol class="flow">' + "".join(f'<li>{html.escape(labels[edge["from"]])} → {html.escape(labels[edge["to"]])}{": " + html.escape(edge["label"]) if edge["label"] else ""}</li>' for edge in block["edges"]) + "</ol>"
    if kind == "accordion":
        return f'<details><summary>{html.escape(block["title"])}</summary>{"".join(_render_block(child, base_dir) for child in block["blocks"])}</details>'
    raise AssertionError(f"unknown normalized block: {kind}")


def render_video_note(note: dict, *, base_dir: str | Path | None = None) -> str:
    """Render a normalized v2 note without accepting user HTML or SVG."""
    base = None if base_dir is None else Path(base_dir)
    normalized = normalize_video_note(note, base_dir=base)
    title = html.escape(normalized["meta"]["title"])
    metadata = " · ".join(html.escape(str(value)) for value in (normalized["meta"]["platform"], normalized["meta"]["source_id"], normalized["meta"]["duration_seconds"], normalized["meta"]["author"]))
    content = "".join(f'<section><div class="section-heading"><h2>{html.escape(section["title"])}</h2></div><p class="section-summary">{html.escape(section["summary"])}</p>{"".join(_render_block(block, base) for block in section["blocks"])}</section>' for section in normalized["sections"])
    return f'''<!doctype html><html lang="{html.escape(normalized["meta"]["language"])}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>:root{{--ink:#17202a;--muted:#667085;--line:#d9dee7;--surface:#fff;--page:#f4f6f9;--accent:#b9423b;--accent-soft:#f8e8e6;--night:#233143}}*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;line-height:1.75}}main{{width:min(960px,calc(100% - 32px));margin:32px auto 64px;background:var(--surface);padding:56px 64px;box-shadow:0 12px 32px rgba(20,31,46,.1)}}header{{padding-bottom:28px;border-bottom:3px solid var(--accent)}}.kicker,.time{{color:var(--accent);font-size:13px;font-weight:700}}h1{{font-size:clamp(30px,5vw,48px);line-height:1.2;margin:0}}.metadata,.section-summary{{color:var(--muted)}}section{{margin-top:42px}}h2{{font-size:26px;line-height:1.25}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse}}th{{background:var(--night);color:#fff;text-align:left;padding:10px 12px}}td{{padding:10px 12px;border-bottom:1px solid var(--line)}}.figures{{display:grid;gap:22px}}.figure-card{{margin:18px 0;border:1px solid var(--line)}}.figure-card img{{display:block;width:100%;max-height:500px;object-fit:contain;background:#111827}}.card-copy{{padding:14px 18px 18px}}.time{{display:inline-block;margin:0 0 8px;padding:2px 9px;background:var(--accent-soft)}}.analysis{{margin:0}}.callout{{padding:12px;border-left:4px solid var(--accent);background:var(--accent-soft)}}.feature-grid{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}}.feature-grid article{{border:1px solid var(--line);padding:12px}}pre{{overflow:auto;background:#17202a;color:#fff;padding:14px}}@media print{{body{{background:#fff}}main{{width:auto;margin:0;padding:0;box-shadow:none}}.figure-card{{break-inside:avoid}}}}@media(max-width:640px){{main{{width:100%;margin:0;padding:30px 20px}}}}</style></head><body><main><header><p class="kicker">CODEX VIDEO NOTE</p><h1>{title}</h1><p class="metadata">{metadata}</p></header>{content}<footer>由 Codex 视频笔记工作流生成，图片已嵌入本 HTML，可离线打开或打印为 PDF。</footer></main></body></html>'''


def render_document(meta: dict, sections: list) -> str:
    """Retain the legacy Bilibili caller API while rendering through v2."""
    normalized = normalize_legacy_document(meta, sections)
    media_paths = [
        block["image"]
        for section in normalized["sections"]
        for block in section["blocks"]
        if block["type"] == "media"
    ]
    base_dir = None if not media_paths else Path(media_paths[0]).parent
    return render_video_note(normalized, base_dir=base_dir)
