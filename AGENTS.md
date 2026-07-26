# HTML Guide Design Standard

## Golden reference

The canonical visual reference is:

`references/html-golden/MindMap_Builder_中文实用指南_最新版.html`

Its bytes are immutable. The required SHA-256 is
`499f331d7638f61c9f873630ba413683fd24a916275a6e98377fad2a63614509`.

Before creating or modifying a video-note HTML renderer:

1. Read the complete golden file and `references/html-design-analysis.md`.
2. Identify its design tokens, components, interactions, breakpoints, and print
   behavior.
3. Render the golden in Chromium.
4. Treat it as the visual-quality baseline, not as factual content to copy.

Do not replace the design with a generic documentation template.

## Shared contract

- The canonical schema is
  `references/schema/video-note-v2.schema.json`.
- Unknown object fields are rejected.
- Bilibili and YouTube implementations may differ internally, but their public
  schema, CLI behavior, normalized semantic model, rendered design tokens,
  semantic block behavior, validation results, and error classes must be
  equivalent.
- Existing Bilibili `render_document(meta, sections)` callers are adapted
  through the legacy normalizer and must remain compatible.

## Required design language

- Responsive Chinese documentation shell.
- Sticky `250px` left navigation on desktop and single-column navigation at
  `960px` and below.
- Large gradient hero; white rounded content panels; restrained borders and
  shadows.
- Clear typography hierarchy and yellow key-point treatment.
- Distinct source, recommendation, inference, notice, and status treatments.
- Feature cards, filters, accordions, copyable code, renderer-owned SVG
  diagrams, embedded local images, and print-friendly CSS where applicable.

## Technical and security constraints

- Use semantic HTML5, plain CSS, and vanilla JavaScript.
- Produce one self-contained offline HTML file.
- Do not use frameworks, CDNs, external fonts, remote styles, or runtime
  network requests.
- Embed validated JPEG, PNG, and WebP assets. Missing or unsupported media must
  fail explicitly and leave no partial output.
- Only `http` and `https` are accepted for reader links. Renderer-owned
  `data:` media is permitted after validation; user HTML, SVG, scripts, event
  handlers, `javascript:`, `file:`, and arbitrary `data:` URLs are rejected.
- Escape every text and attribute value. User content must never enter raw SVG
  or script structure.
- Do not expose `raw_subtitles`, `ocr`, `ocr_text`, `confidence`, `grounding`,
  `grounding_score`, `timestamp_alignment`, or `extraction_trace` in HTML.
- Preserve accessibility labels, visible focus, logical focus order,
  `aria-pressed`, `aria-expanded`, and durable `aria-live` copy feedback.

## Web, email, and print profiles

- `web` may provide filters, accordions, copy controls, sticky navigation, and
  renderer-owned JavaScript.
- `email` targets Apple Mail 16+ and static HTML preview. It contains no
  JavaScript, sticky positioning, interactive-only content, external assets, or
  hidden information. Filters are flattened and accordions are expanded.
- Web print uses A4, `printBackground: true`, and `12mm` margins on every side.
  Navigation and interactive controls are excluded; content uses the available
  width and meaningful highlights remain visible.

## Required verification

Use:

`npx --yes --package @playwright/cli@0.1.17 playwright-cli`

The accepted browser is Headless Chromium `150.0.0.0`.

Before claiming completion:

1. Run `uv run --extra dev pytest -q`.
2. Run `uv run --extra dev python scripts/acceptance_check.py`.
3. Verify viewports `1440×1000`, `1024×900`, `768×1024`, and `390×844`.
4. Test navigation, filters, accordions, copy/clipboard, keyboard activation,
   focus, labels, offline reload, console/page/request errors, and print PDF.
5. Confirm candidate scroll width is at most `390px` at the `390px` viewport.
6. Compare measurements, screenshots, HTML, and normalized evidence across both
   repositories.

The only pre-approved intentional differences from the golden are:

- fixing its `394px` scroll width at the `390px` viewport;
- embedding/suppressing its missing favicon request;
- adding durable `aria-live` copy feedback and a real skip-link focus target.

Acceptance requires every hard gate, weighted score at least `90/100`, and no
unresolved critical, high, or medium independent-review finding.
