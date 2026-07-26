import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
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


_DATA_IMAGE_SOURCE = re.compile(
    r"\Adata:image/(?:jpeg|png|webp);base64,[a-z0-9+/]*={0,2}\Z",
    re.IGNORECASE,
)
_EVENT_ATTRIBUTE = re.compile(r"\Aon[a-z0-9_:-]+\Z", re.IGNORECASE)
_CSS_VARIABLE = re.compile(
    r"(?:\A|;)\s*--[^:;]+\s*:",
    re.IGNORECASE,
)
_CSS_URL = re.compile(r"url\s*\(", re.IGNORECASE)
_HIDDEN_CSS = (
    (
        "style:position:sticky",
        re.compile(
            r"(?:\A|;)\s*position\s*:\s*sticky(?:\s*!important)?\s*(?:;|\Z)",
            re.IGNORECASE,
        ),
    ),
    (
        "style:display:none",
        re.compile(
            r"(?:\A|;)\s*display\s*:\s*none(?:\s*!important)?\s*(?:;|\Z)",
            re.IGNORECASE,
        ),
    ),
    (
        "style:visibility:hidden",
        re.compile(
            r"(?:\A|;)\s*visibility\s*:\s*hidden(?:\s*!important)?\s*(?:;|\Z)",
            re.IGNORECASE,
        ),
    ),
    (
        "style:content-visibility:hidden",
        re.compile(
            r"(?:\A|;)\s*content-visibility\s*:\s*hidden"
            r"(?:\s*!important)?\s*(?:;|\Z)",
            re.IGNORECASE,
        ),
    ),
    (
        "style:mso-hide:all",
        re.compile(
            r"(?:\A|;)\s*mso-hide\s*:\s*all(?:\s*!important)?\s*(?:;|\Z)",
            re.IGNORECASE,
        ),
    ),
    (
        "style:opacity:0",
        re.compile(
            r"(?:\A|;)\s*opacity\s*:\s*0(?:\.0+)?"
            r"(?:\s*!important)?\s*(?:;|\Z)",
            re.IGNORECASE,
        ),
    ),
)


class _StaticEmailAudit(HTMLParser):
    prohibited_tags = {
        "button",
        "details",
        "form",
        "input",
        "script",
        "select",
        "style",
        "summary",
        "textarea",
    }
    resource_tags = {"audio", "embed", "iframe", "link", "object", "source", "video"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.violations = []

    def handle_starttag(self, tag, attrs):
        self._audit_element(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._audit_element(tag, attrs)

    def _audit_element(self, tag, attrs):
        tag = tag.casefold()
        normalized_attrs = [
            (name.casefold(), "" if value is None else value)
            for name, value in attrs
        ]
        attr_map = dict(normalized_attrs)
        if tag in self.prohibited_tags:
            self.violations.append(f"prohibited-tag:{tag}")
        if tag in self.resource_tags:
            self.violations.append(f"resource-tag:{tag}")

        for name, value in normalized_attrs:
            if _EVENT_ATTRIBUTE.fullmatch(name):
                self.violations.append(f"event:{name}")
            if name == "hidden" or (
                name == "aria-hidden" and value.strip().casefold() == "true"
            ):
                self.violations.append(f"attribute:{name}")
            if name.startswith(("data-copy", "data-filter")):
                self.violations.append(f"interactive-attribute:{name}")
            if name == "style":
                if _CSS_VARIABLE.search(value):
                    self.violations.append("style:css-variable")
                if _CSS_URL.search(value):
                    self.violations.append("style:url")
                for label, pattern in _HIDDEN_CSS:
                    if pattern.search(value):
                        self.violations.append(label)
            if name == "src":
                if not (tag == "img" and _DATA_IMAGE_SOURCE.fullmatch(value.strip())):
                    self.violations.append(f"resource:{tag}[src]")
            elif name in {"srcset", "poster", "data"}:
                self.violations.append(f"resource:{tag}[{name}]")
            elif name == "href":
                target = value.strip().casefold()
                if not (
                    tag == "a"
                    and target.startswith(("#", "http://", "https://"))
                ):
                    self.violations.append(f"resource:{tag}[href]")

        if tag == "img":
            if "src" not in attr_map:
                self.violations.append("image:missing-src")
            if not attr_map.get("alt", "").strip():
                self.violations.append("image:missing-alt")


def static_email_violations(document):
    parser = _StaticEmailAudit()
    parser.feed(document)
    parser.close()
    return parser.violations


def assert_once_in_order(document, markers):
    positions = []
    for marker in markers:
        assert document.count(marker) == 1, marker
        positions.append(document.index(marker))
    assert positions == sorted(positions)


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

    assert static_email_violations(document) == []
    assert '<table role="presentation"' in document
    assert re.search(r"max-width\s*:\s*760px", document, re.I)
    assert "style=\"" in document


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('<p ONCLICK = "bad">x</p>', "event:onclick"),
        ('<img onLoad\t=\t"bad" src="data:image/png;base64,AA==" alt="x">', "event:onload"),
        ('<div OnPointerEnter \n = "bad">x</div>', "event:onpointerenter"),
        ('<div style="position : sticky">x</div>', "style:position:sticky"),
        ('<div style=" --private-color : red">x</div>', "style:css-variable"),
        ('<div style=" --主题色 : red">x</div>', "style:css-variable"),
        (
            '<div style="background-image : url( https://assets.test/bg.png )">x</div>',
            "style:url",
        ),
        ('<div style="display : none">x</div>', "style:display:none"),
        ('<div style="VISIBILITY:\n HIDDEN">x</div>', "style:visibility:hidden"),
        ('<div style="content-visibility : hidden">x</div>', "style:content-visibility:hidden"),
        ('<div style="mso-hide : all">x</div>', "style:mso-hide:all"),
        ('<div style="opacity : 0">x</div>', "style:opacity:0"),
        ("<div HIDDEN>x</div>", "attribute:hidden"),
    ],
)
def test_static_email_audit_rejects_attribute_and_css_variants(snippet, expected):
    assert expected in static_email_violations(snippet)


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("<script>bad()</script>", "prohibited-tag:script"),
        ("<style>body{display:block}</style>", "prohibited-tag:style"),
        ("<button>bad</button>", "prohibited-tag:button"),
        ("<details><summary>bad</summary></details>", "prohibited-tag:details"),
        ('<form><input value="bad"></form>', "prohibited-tag:form"),
        ('<div DATA-FILTER = "all">bad</div>', "interactive-attribute:data-filter"),
        ('<div data-copy-target = "code">bad</div>', "interactive-attribute:data-copy-target"),
    ],
)
def test_static_email_audit_rejects_interactive_markup(snippet, expected):
    assert expected in static_email_violations(snippet)


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('<iframe src="https://assets.test/frame"></iframe>', "resource-tag:iframe"),
        ('<audio src="https://assets.test/audio"></audio>', "resource-tag:audio"),
        ('<video src="https://assets.test/video"></video>', "resource-tag:video"),
        ('<source src="https://assets.test/source">', "resource-tag:source"),
        ('<object data="https://assets.test/object"></object>', "resource-tag:object"),
        ('<embed src="https://assets.test/embed">', "resource-tag:embed"),
        ('<img src="https://assets.test/image.png" alt="remote">', "resource:img[src]"),
        ('<img srcset="https://assets.test/one.png 1x" alt="remote">', "resource:img[srcset]"),
        ('<link href="https://assets.test/theme.css" rel="stylesheet">', "resource-tag:link"),
        ('<div href="https://assets.test/runtime">x</div>', "resource:div[href]"),
        ('<a href="mailto:test@example.test">mail</a>', "resource:a[href]"),
        ('<a href="JaVaScRiPt : alert(1)">bad</a>', "resource:a[href]"),
    ],
)
def test_static_email_audit_rejects_runtime_resource_variants(snippet, expected):
    assert expected in static_email_violations(snippet)


def test_static_email_audit_allows_embedded_images_and_reader_links():
    snippet = (
        '<img src="data:image/webp;base64,AA==" alt="embedded">'
        '<a href="https://example.test/read">read</a>'
        '<a href="#section">section</a>'
    )
    assert static_email_violations(snippet) == []


def test_all_blocks_email_visibly_covers_every_semantic_callout():
    document = render_email(fixture("all-blocks.json"))

    expected = {
        "An official source fact.": ("#edfafa", "#0f8b99"),
        "A practical recommendation.": ("#ecfdf5", "#1f9d68"),
        "A clearly labelled inference.": ("#f5f0ff", "#7c4dce"),
        "An important notice.": ("#fff4e8", "#e98324"),
        "A key point.": ("#fffbed", "#e8c948"),
    }
    for text, (background, border) in expected.items():
        assert text in document
        assert f"background:{background};border-left:4px solid {border};" in document
    assert static_email_violations(document) == []


def test_email_expands_every_valid_accordion_child_once_in_order():
    note = fixture("all-blocks.json")
    accordion = next(
        block for block in note["sections"][0]["blocks"] if block["type"] == "accordion"
    )
    accordion["title"] = "ACCORDION-TITLE-UNIQUE"
    accordion["blocks"] = [
        {
            "id": "accordion-paragraph",
            "type": "paragraph",
            "text": "ACCORDION-PARAGRAPH-UNIQUE",
        },
        {
            "id": "accordion-list",
            "type": "list",
            "items": ["ACCORDION-LIST-ONE-UNIQUE", "ACCORDION-LIST-TWO-UNIQUE"],
        },
        {
            "id": "accordion-callout",
            "type": "callout",
            "kind": "notice",
            "text": "ACCORDION-CALLOUT-UNIQUE",
        },
    ]

    document = render_email(note)

    assert_once_in_order(
        document,
        [
            "ACCORDION-TITLE-UNIQUE",
            "ACCORDION-PARAGRAPH-UNIQUE",
            "ACCORDION-LIST-ONE-UNIQUE",
            "ACCORDION-LIST-TWO-UNIQUE",
            "ACCORDION-CALLOUT-UNIQUE",
        ],
    )
    assert static_email_violations(document) == []


def test_email_flattens_every_feature_card_once_in_category_and_card_order():
    note = fixture("all-blocks.json")
    grid = next(
        block for block in note["sections"][0]["blocks"] if block["type"] == "feature_grid"
    )
    grid["cards"] = [
        {
            "id": "feature-alpha-one",
            "category": "CATEGORY-ALPHA-UNIQUE",
            "title": "TITLE-ALPHA-ONE-UNIQUE",
            "description": "DESCRIPTION-ALPHA-ONE-UNIQUE",
            "link": "https://example.test/feature-alpha-one-unique",
        },
        {
            "id": "feature-alpha-two",
            "category": "CATEGORY-ALPHA-UNIQUE",
            "title": "TITLE-ALPHA-TWO-UNIQUE",
            "description": "DESCRIPTION-ALPHA-TWO-UNIQUE",
            "link": "https://example.test/feature-alpha-two-unique",
        },
        {
            "id": "feature-beta-one",
            "category": "CATEGORY-BETA-UNIQUE",
            "title": "TITLE-BETA-ONE-UNIQUE",
            "description": "DESCRIPTION-BETA-ONE-UNIQUE",
            "link": "https://example.test/feature-beta-one-unique",
        },
    ]

    document = render_email(note)

    assert_once_in_order(
        document,
        [
            "CATEGORY-ALPHA-UNIQUE",
            "TITLE-ALPHA-ONE-UNIQUE",
            "DESCRIPTION-ALPHA-ONE-UNIQUE",
            "https://example.test/feature-alpha-one-unique",
            "TITLE-ALPHA-TWO-UNIQUE",
            "DESCRIPTION-ALPHA-TWO-UNIQUE",
            "https://example.test/feature-alpha-two-unique",
            "CATEGORY-BETA-UNIQUE",
            "TITLE-BETA-ONE-UNIQUE",
            "DESCRIPTION-BETA-ONE-UNIQUE",
            "https://example.test/feature-beta-one-unique",
        ],
    )
    assert "筛选" not in document


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


def test_email_renders_every_media_field_and_data_uri_exactly_once_in_order():
    note = fixture("all-blocks.json")
    blocks = note["sections"][0]["blocks"]
    media_index = next(index for index, block in enumerate(blocks) if block["type"] == "media")
    blocks[media_index : media_index + 1] = [
        {
            "id": "media-jpeg-unique",
            "type": "media",
            "image": "media/frame.jpg",
            "alt": "ALT-JPEG-UNIQUE",
            "headline": "HEADLINE-JPEG-UNIQUE",
            "explanation": "EXPLANATION-JPEG-UNIQUE",
            "usage": "USAGE-JPEG-UNIQUE",
            "deep_link": "https://example.test/watch?media=jpeg&mode=email",
        },
        {
            "id": "media-png-unique",
            "type": "media",
            "image": "media/frame.png",
            "alt": "ALT-PNG-UNIQUE",
            "headline": "HEADLINE-PNG-UNIQUE",
            "explanation": "EXPLANATION-PNG-UNIQUE",
            "usage": "USAGE-PNG-UNIQUE",
            "deep_link": "https://example.test/watch?media=png&mode=email",
        },
        {
            "id": "media-webp-unique",
            "type": "media",
            "image": "media/frame.webp",
            "alt": "ALT-WEBP-UNIQUE",
            "headline": "HEADLINE-WEBP-UNIQUE",
            "explanation": "EXPLANATION-WEBP-UNIQUE",
            "usage": "USAGE-WEBP-UNIQUE",
            "deep_link": "https://example.test/watch?media=webp&mode=email",
        },
    ]

    document = render_email(note)
    expected_media_links = [
        (
            "https://example.test/watch?media=jpeg&amp;mode=email",
            "打开对应视频位置",
        ),
        (
            "https://example.test/watch?media=png&amp;mode=email",
            "打开对应视频位置",
        ),
        (
            "https://example.test/watch?media=webp&amp;mode=email",
            "打开对应视频位置",
        ),
    ]

    assert_once_in_order(
        document,
        [
            "data:image/jpeg;base64,",
            "ALT-JPEG-UNIQUE",
            "HEADLINE-JPEG-UNIQUE",
            "EXPLANATION-JPEG-UNIQUE",
            "USAGE-JPEG-UNIQUE",
            'href="https://example.test/watch?media=jpeg&amp;mode=email"',
            "data:image/png;base64,",
            "ALT-PNG-UNIQUE",
            "HEADLINE-PNG-UNIQUE",
            "EXPLANATION-PNG-UNIQUE",
            "USAGE-PNG-UNIQUE",
            'href="https://example.test/watch?media=png&amp;mode=email"',
            "data:image/webp;base64,",
            "ALT-WEBP-UNIQUE",
            "HEADLINE-WEBP-UNIQUE",
            "EXPLANATION-WEBP-UNIQUE",
            "USAGE-WEBP-UNIQUE",
            'href="https://example.test/watch?media=webp&amp;mode=email"',
        ],
    )
    media_links = re.findall(
        r'<a\b[^>]*href="(https://example\.test/watch\?media=(?:jpeg|png|webp)'
        r'&amp;mode=email)"[^>]*>([^<]*)</a>',
        document,
    )
    assert media_links == expected_media_links
    assert document.count(">打开对应视频位置</a>") == len(expected_media_links)
    assert static_email_violations(document) == []


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
