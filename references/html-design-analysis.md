# Golden HTML design analysis

Canonical file:
`html-golden/MindMap_Builder_中文实用指南_最新版.html`

Required SHA-256:
`499f331d7638f61c9f873630ba413683fd24a916275a6e98377fad2a63614509`

This document records measured/extracted values. It is the renderer contract,
not a loose mood board.

## Information architecture

The golden uses a two-part shell:

1. a section-linked table of contents;
2. a main reading column containing a gradient hero, status cards, white
   sections, diagrams/cards/examples, and a muted footer.

Video-note content maps to the same grammar: title and source badge in the hero,
platform-neutral status metadata, section navigation, semantic content blocks,
and a generation footer. Factual content from the golden is never copied.

## Design tokens

| Token | Exact value |
|---|---|
| `--bg` | `#f6f7fb` |
| `--panel` | `#fff` |
| `--text` | `#1d2330` |
| `--muted` | `#667085` |
| `--border` | `#e4e7ec` |
| `--accent` | `#6558e8` |
| `--accent2` | `#8b5cf6` |
| `--accent-soft` | `#efedff` |
| `--key` | `#fff2a8` |
| `--key-border` | `#e8c948` |
| `--blue` | `#2878f0` |
| `--purple` | `#7c4dce` |
| `--orange` | `#e98324` |
| `--red` | `#d64b4b` |
| `--green` | `#1f9d68` |
| `--cyan` | `#0f8b99` |
| `--shadow` | `0 10px 28px rgba(32,37,60,.075)` |
| `--r` | `18px` |

The hero background is
`linear-gradient(135deg,#fff 0%,#f2efff 60%,#eef8ff 100%)`.

## Typography

Body stack, exact order:

```css
-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
"Hiragino Sans GB", "Microsoft YaHei", sans-serif
```

Body line height is `1.65`. Code uses
`ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`. The visual hierarchy
comes from weight/size/spacing rather than an external font.

## Geometry and spacing

| Element | Extracted value | Acceptance |
|---|---:|---:|
| shell maximum width | `1280px` | `±2px` |
| desktop navigation width | `250px` | `±2px` |
| shell gap | `24px` | `±1px` |
| shell padding | `24px` | exact design token |
| navigation top offset | `16px` | exact behavior |
| navigation padding | `16px` | exact design token |
| hero radius | `28px` | `±1px` |
| hero padding | `34px` | `±2px` |
| section top gap | `24px` | `±2px` |
| section radius | `18px` | `±1px` |
| section padding | `26px` | `±2px` |
| feature-card gap | `14px` | exact design token |
| feature-card radius | `15px` | `±1px` |

Content-driven hero, section, card, and page heights are not fixed.

## Responsive layout

| Boundary | Shell/navigation | Cards |
|---|---|---:|
| `961px` and above | two columns; sticky `250px` nav | 3 |
| `960px` and below | one column; nav above content | 2 |
| `621px` | one column shell | 2 |
| `620px` and below | one column shell | 1 |

At `960px` the shell padding becomes `13px`; status cards and two-column
diagrams become one column. At `620px`, section and hero padding become `19px`.
At a `390px` viewport the candidate document scroll width must be `≤390px`.

## Components and semantic treatments

- Hero: source/platform badge, `h1`, lead, three-column desktop status grid.
- Key phrase: yellow linear highlight using `--key`.
- Key box: `2px` `--key-border`, pale yellow background, `15px` radius.
- Source/fact callout: cyan left border and `#edfafa` background.
- Inference callout: purple left border and `#f5f0ff` background.
- Practical notice/recommendation: orange left border and `#fff4e8` background.
- Feature grid: three/two/one responsive columns; each card has tag, title,
  description, and optional validated HTTP(S) link.
- Table: dynamic columns, left-aligned cells, `11px` cell padding.
- Media: embedded validated data URL, full-width constrained image, meaningful
  alt text, caption, and explanation.
- Flow: renderer-owned nodes/arrows only; user text is escaped.
- Code: dark code box with a real button and separate polite live region.
- Accordion: button with `aria-expanded`/`aria-controls`; content is expanded in
  print and email.

## Navigation and interaction

- `html { scroll-behavior: smooth; }`.
- Desktop navigation is sticky; tablet/mobile navigation is in normal flow.
- The first keyboard stop is a visible skip link. Enter updates the hash and
  moves focus to `main#vn-owned-main`.
- Filters use real buttons and expose state through `aria-pressed`.
- Accordions use real buttons; mouse, Enter, and Space update
  `aria-expanded`, visibility, and symbol.
- Copy uses the Clipboard API, copies text rather than markup, and announces
  `已复制` through a persistent `aria-live="polite"` region.
- There is no positive `tabindex` and no keyboard trap.

## Accessibility

- One `h1`, ordered section headings, labelled navigation and sections.
- Useful non-empty image alt text.
- Renderer-owned diagrams have a descriptive label; decorative SVG is hidden.
- All focusable controls receive a visible `3px` outline.
- Sticky navigation does not obscure anchor headings.
- Semantic state is not conveyed by color alone.

## Offline and security behavior

The output is a single HTML file with inline CSS, renderer-owned web JavaScript,
embedded favicon, and embedded validated media. It has no runtime network
dependency and succeeds after being moved into an empty directory.

All reader text/attributes are escaped. Only validated HTTP(S) links are
accepted. Evidence-only fields and sentinel values are rejected before render.
Missing or unsupported media causes a non-zero result and atomic output
preservation.

## Email profile

The static target is Apple Mail 16+ and static HTML preview:

- no JavaScript or script tag;
- no sticky navigation or interactive-only controls;
- no CSS-variable dependency;
- inline/email-compatible styling;
- all filters flattened into visible grouped content;
- all accordion content expanded;
- all media embedded.

## Print behavior

Browser PDF settings:

```text
format: A4
printBackground: true
margin: 12mm on top, right, bottom, and left
```

Navigation, filters, copy controls, and accordion toggles are hidden. Main
content uses full width; hidden cards and accordion panels become visible;
panels avoid page breaks where practical; meaningful backgrounds remain.

## Known golden defects and approved differences

The untouched golden measures `394px` scroll width at the `390px` viewport and
has no favicon declaration, causing a verified `/favicon.ico` 404. The
candidate intentionally fixes both. It also adds a durable live region and a
real skip-link focus target. No other geometry, token, responsive, or semantic
difference is pre-approved.
