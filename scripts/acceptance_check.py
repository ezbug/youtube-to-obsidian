#!/usr/bin/env python3
"""Generate Task 5 browser acceptance evidence with pinned Playwright CLI."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlsplit


PINNED_PLAYWRIGHT_COMMAND = (
    "npx",
    "--yes",
    "--package",
    "@playwright/cli@0.1.17",
    "playwright-cli",
)
VIEWPORTS = {
    "1440": (1440, 1000),
    "1024": (1024, 900),
    "768": (768, 1024),
    "390": (390, 844),
}
BOUNDARY_VIEWPORTS = {
    "961": (961, 900),
    "960": (960, 900),
    "621": (621, 900),
    "620": (620, 900),
}
COLOR_TOKENS = {
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
PDF_SETTINGS = {
    "format": "A4",
    "printBackground": True,
    "margins": {"top": "12mm", "right": "12mm", "bottom": "12mm", "left": "12mm"},
}
PDF_A4_POINTS = (595.28, 841.89)
PDF_A4_TOLERANCE_POINTS = 2.0
_FAVICON_CONSOLE_ERROR = "Failed to load resource: the server responded with a status of 404 (File not found)"
_VOLATILE_PARITY_KEYS = {
    "repository",
    "repo_root",
    "port",
    "served_url",
    "file_url",
    "session",
    "generated_at",
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_all_category_collision(note: dict) -> dict:
    """Return a fixture variant with both the all control and an all category."""
    result = copy.deepcopy(note)
    for section in result.get("sections", []):
        for block in section.get("blocks", []):
            if block.get("type") != "feature_grid" or not block.get("cards"):
                continue
            card = copy.deepcopy(block["cards"][0])
            card.update(
                {
                    "id": "acceptance-all-category-card",
                    "category": "all",
                    "title": "Acceptance category all",
                    "description": "Exercises category all without colliding with the all control.",
                    "link": None,
                }
            )
            block["cards"].append(card)
            return result
    raise ValueError("all-blocks fixture must contain a non-empty feature_grid")


def normalize_for_parity(value, *, repo_root: Path | None = None):
    """Remove runtime identity while preserving browser acceptance values."""
    if isinstance(value, dict):
        is_pdf_result = (
            value.get("pdf_created") is True
            and "settings" in value
            and "sha256" in value
        )
        return {
            key: (
                "<PDF-RUNTIME-HASH>"
                if is_pdf_result and key == "sha256"
                else normalize_for_parity(child, repo_root=repo_root)
            )
            for key, child in value.items()
            if key not in _VOLATILE_PARITY_KEYS
        }
    if isinstance(value, list):
        return [normalize_for_parity(child, repo_root=repo_root) for child in value]
    if isinstance(value, str) and repo_root is not None:
        return value.replace(str(repo_root), "<REPO>")
    return value


def failed_hard_checks(checks: list[dict]) -> list[dict]:
    return [
        {
            "name": check["name"],
            "actual": check.get("actual"),
            "expected": check.get("expected"),
        }
        for check in checks
        if not check["passed"]
    ]


def aggregate_candidate_events(event_sets: list[dict] | tuple[dict, ...]) -> dict:
    fields = {
        "candidate_console_errors": "consoleErrors",
        "candidate_page_errors": "pageErrors",
        "candidate_failed_requests": "requestFailures",
        "candidate_http_errors": "httpErrors",
        "candidate_requests": "requests",
    }
    return {
        output_name: sum(len(events[event_name]) for events in event_sets)
        for output_name, event_name in fields.items()
    }


def _copy_matches_source(result: dict) -> bool:
    return (
        result.get("announcement") == "已复制"
        and result.get("ariaLive") == "polite"
        and bool(result.get("sourceText"))
        and result.get("clipboardText") == result.get("sourceText")
    )


def _pdf_page_box_is_a4(box: dict) -> bool:
    media_box = box.get("mediaBox", [])
    return (
        box.get("pageCount", 0) > 0
        and len(media_box) == 4
        and abs((media_box[2] - media_box[0]) - PDF_A4_POINTS[0]) <= PDF_A4_TOLERANCE_POINTS
        and abs((media_box[3] - media_box[1]) - PDF_A4_POINTS[1]) <= PDF_A4_TOLERANCE_POINTS
    )


def _reference_defect_status(reference: dict, events: dict) -> dict:
    probe = reference["faviconProbe"]
    overflow = reference["viewports"]["390"]["document"]
    expected_url = f"{urlsplit(probe['documentUrl']).scheme}://{urlsplit(probe['documentUrl']).netloc}/favicon.ico"
    unexpected_console = [event for event in events["consoleErrors"] if not (event.get("text") == _FAVICON_CONSOLE_ERROR and event.get("url") == probe["url"])]
    unexpected_http = [event for event in events["httpErrors"] if not (event.get("status") == 404 and event.get("url") == probe["url"])]
    approved = (
        overflow["scrollWidth"] > overflow["clientWidth"]
        and probe["iconLinks"] == []
        and probe["url"] == expected_url
        and probe["status"] == 404
        and bool(events["consoleErrors"])
        and not unexpected_console
        and not events["pageErrors"]
        and not events["requestFailures"]
        and not unexpected_http
    )
    return {"approved": approved, "overflow": overflow, "favicon": probe, "unexpected": {"consoleErrors": unexpected_console, "pageErrors": events["pageErrors"], "requestFailures": events["requestFailures"], "httpErrors": unexpected_http}}


def _inspect_pdf_page_box(path: Path) -> dict:
    result = subprocess.run(["pdfinfo", "-box", str(path)], capture_output=True, text=True, check=False)
    output = result.stdout
    page_count = re.search(r"^Pages:\s+(\d+)$", output, re.MULTILINE)
    media_box = re.search(r"^MediaBox:\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)$", output, re.MULTILINE)
    if result.returncode or not page_count or not media_box:
        return {"inspector": "pdfinfo -box", "ok": False, "returncode": result.returncode, "output": output, "stderr": result.stderr, "pageCount": 0, "mediaBox": []}
    return {"inspector": "pdfinfo -box", "ok": True, "pageCount": int(page_count.group(1)), "mediaBox": [float(value) for value in media_box.groups()]}


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return


@contextmanager
def serve_repository(root: Path):
    handler = partial(QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class PlaywrightCLI:
    def __init__(self, root: Path, runtime_dir: Path):
        self.root = root
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "PLAYWRIGHT_MCP_OUTPUT_DIR": str(runtime_dir),
                "PLAYWRIGHT_MCP_ALLOW_UNRESTRICTED_FILE_ACCESS": "1",
                "PLAYWRIGHT_MCP_TIMEOUT_ACTION": "10000",
                "PLAYWRIGHT_MCP_TIMEOUT_NAVIGATION": "60000",
            }
        )

    def command(self, session: str, *args: str, raw: bool = False) -> list[str]:
        command = [*PINNED_PLAYWRIGHT_COMMAND, f"-s={session}"]
        if raw:
            command.append("--raw")
        command.extend(str(argument) for argument in args)
        return command

    def run(
        self,
        session: str,
        *args: str,
        raw: bool = False,
        timeout: int = 120,
    ) -> str:
        command = self.command(session, *args, raw=raw)
        result = subprocess.run(
            command,
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Playwright CLI failed ({result.returncode}): {' '.join(command)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result.stdout.strip()

    def value(self, session: str, code: str):
        output = self.run(session, "run-code", code, raw=True)
        return json.loads(output)

    def open(self, session: str) -> None:
        self.run(session, "open", "about:blank")

    def close(self, session: str, phase: str) -> dict:
        try:
            self.run(session, "close", timeout=30)
            return {"phase": phase, "closed": True}
        except Exception as exc:
            return {"phase": phase, "closed": False, "error": str(exc)}

    def attach_events(self, session: str) -> None:
        self.value(
            session,
            """async page => {
              page.__task5Events = {
                consoleErrors: [], pageErrors: [], requestFailures: [],
                httpErrors: [], requests: []
              };
              page.on('console', message => {
                if (message.type() === 'error')
                  page.__task5Events.consoleErrors.push({text: message.text(), url: message.location().url || null});
              });
              page.on('pageerror', error => {
                page.__task5Events.pageErrors.push(String(error));
              });
              page.on('requestfailed', request => {
                page.__task5Events.requestFailures.push({
                  url: request.url(),
                  error: request.failure()?.errorText || ''
                });
              });
              page.on('request', request => {
                page.__task5Events.requests.push({
                  method: request.method(), url: request.url()
                });
              });
              page.on('response', response => {
                if (response.status() >= 400)
                  page.__task5Events.httpErrors.push({
                    status: response.status(), url: response.url()
                  });
              });
              return true;
            }""",
        )

    def events(self, session: str) -> dict:
        return self.value(session, "async page => page.__task5Events")


LAYOUT_CODE = """async page => {
  await page.evaluate(() => document.fonts && document.fonts.ready);
  const result = await page.evaluate(() => {
    const rect = selector => {
      const element = document.querySelector(selector);
      if (!element) return null;
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        x: box.x, y: box.y, width: box.width, height: box.height,
        right: box.right, bottom: box.bottom,
        display: style.display,
        position: style.position,
        padding: [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft],
        borderRadius: style.borderRadius,
        backgroundColor: style.backgroundColor,
        color: style.color
      };
    };
    const root = getComputedStyle(document.documentElement);
    const shell = document.querySelector('.shell');
    const nav = document.querySelector('nav');
    const main = document.querySelector('main');
    const cards = document.querySelector('.feature-grid, .cards');
    const shellStyle = shell ? getComputedStyle(shell) : null;
    const cardsStyle = cards ? getComputedStyle(cards) : null;
    const columns = style => {
      if (!style || style === 'none') return 0;
      return style.split(' ').filter(Boolean).length;
    };
    const navBox = nav?.getBoundingClientRect();
    const mainBox = main?.getBoundingClientRect();
    return {
      userAgent: navigator.userAgent,
      title: document.title,
      viewport: {width: innerWidth, height: innerHeight},
      document: {
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        scrollHeight: document.documentElement.scrollHeight
      },
      shell: {
        ...rect('.shell'),
        gap: shellStyle?.gap || null,
        gridTemplateColumns: shellStyle?.gridTemplateColumns || null,
        columnCount: columns(shellStyle?.gridTemplateColumns)
      },
      nav: rect('nav'),
      main: rect('main'),
      hero: rect('.hero'),
      section: rect('section'),
      featureGrid: {
        ...rect('.feature-grid, .cards'),
        gridTemplateColumns: cardsStyle?.gridTemplateColumns || null,
        columnCount: columns(cardsStyle?.gridTemplateColumns)
      },
      navAboveMain: Boolean(navBox && mainBox && navBox.bottom <= mainBox.top + 1),
      headingsVisible: [...document.querySelectorAll('section h2')].every(heading => {
        const box = heading.getBoundingClientRect();
        return box.width > 0 && box.height > 0;
      }),
      colors: Object.fromEntries(
        %s.map(name => [name, root.getPropertyValue(name).trim()])
      )
    };
  });
  return result;
}""" % json.dumps(list(COLOR_TOKENS))


def _path_url(path: Path) -> str:
    return "file://" + quote(str(path))


def _served_url(port: int, relative_path: Path) -> str:
    return f"http://127.0.0.1:{port}/" + quote(relative_path.as_posix())


def _session(prefix: str, root: Path, run_nonce: str) -> str:
    suffix = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    return f"task5-{prefix}-{suffix}-{run_nonce}"


def _generate_candidate(root: Path, fixture: Path, output: Path) -> dict:
    command = [
        sys.executable,
        str(root / "scripts" / "render_html.py"),
        "--note",
        str(fixture),
        "--output",
        str(output),
        "--profile",
        "web",
    ]
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"candidate CLI failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return {
        "command": command,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "sha256": sha256_path(output),
        "bytes": output.stat().st_size,
    }


def _capture_layout_and_screenshots(
    cli: PlaywrightCLI,
    root: Path,
    page_name: str,
    url: str,
    screenshot_dir: Path,
    run_nonce: str,
    cleanup: list[dict],
    *,
    boundaries: bool,
) -> tuple[dict, dict]:
    session = _session(f"{page_name}-layout", root, run_nonce)
    cli.open(session)
    try:
        cli.attach_events(session)
        cli.run(session, "goto", url)
        favicon_probe = None
        if page_name == "reference":
            favicon_probe = cli.value(
                session,
                """async page => page.evaluate(async () => {
                  const url = new URL('/favicon.ico', location.origin).href;
                  const response = await fetch(url);
                  return {documentUrl: location.href, iconLinks: [...document.querySelectorAll('link[rel~="icon"]')].map(link => link.href), url: response.url, status: response.status};
                })""",
            )
        measurements = {}
        screenshot_hashes = {}
        for label, (width, height) in VIEWPORTS.items():
            print(f"[{page_name}] screenshot {label}", flush=True)
            cli.run(session, "resize", str(width), str(height))
            measurements[label] = cli.value(session, LAYOUT_CODE)
            screenshot_path = screenshot_dir / f"{label}.png"
            cli.run(
                session,
                "screenshot",
                "--filename",
                str(screenshot_path),
                "--full-page",
            )
            screenshot_hashes[label] = {
                "path": str(screenshot_path.relative_to(root)),
                "sha256": sha256_path(screenshot_path),
                "bytes": screenshot_path.stat().st_size,
            }
        boundary_measurements = {}
        if boundaries:
            for label, (width, height) in BOUNDARY_VIEWPORTS.items():
                cli.run(session, "resize", str(width), str(height))
                boundary_measurements[label] = cli.value(session, LAYOUT_CODE)
        return (
            {
                "viewports": measurements,
                "boundaries": boundary_measurements,
                "screenshots": screenshot_hashes,
                **({"faviconProbe": favicon_probe} if favicon_probe else {}),
            },
            cli.events(session),
        )
    finally:
        cleanup.append(cli.close(session, f"{page_name}-layout"))


def _focus_state(cli: PlaywrightCLI, session: str) -> dict:
    return cli.value(
        session,
        """async page => page.evaluate(() => {
          const element = document.activeElement;
          const style = getComputedStyle(element);
          return {
            tag: element.tagName.toLowerCase(),
            id: element.id || null,
            text: (element.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
            href: element.getAttribute?.('href') || null,
            role: element.getAttribute?.('role') || null,
            outlineStyle: style.outlineStyle,
            outlineWidth: style.outlineWidth,
            outlineColor: style.outlineColor
          };
        })""",
    )


def _accordion_state(cli: PlaywrightCLI, session: str) -> dict:
    return cli.value(
        session,
        """async page => page.evaluate(() => {
          const button = document.querySelector('.accordion-toggle');
          const panel = document.getElementById(button.getAttribute('aria-controls'));
          return {
            expanded: button.getAttribute('aria-expanded'),
            panelHidden: panel.hidden,
            symbol: button.querySelector('.accordion-symbol').textContent
          };
        })""",
    )


def _run_interactions(
    cli: PlaywrightCLI,
    root: Path,
    candidate_url: str,
    collision_url: str,
    run_nonce: str,
    cleanup: list[dict],
) -> tuple[dict, dict, dict]:
    session = _session("interactions", root, run_nonce)
    cli.open(session)
    try:
        cli.attach_events(session)
        cli.run(session, "resize", "1440", "1000")
        cli.run(session, "goto", candidate_url)
        origin = f"{urlsplit(candidate_url).scheme}://{urlsplit(candidate_url).netloc}"
        cli.value(session, f"async page => {{ await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], {{origin: {json.dumps(origin)}}}); return true; }}")

        cli.run(session, "press", "Tab")
        skip_before = _focus_state(cli, session)
        cli.run(session, "press", "Enter")
        skip_after = cli.value(
            session,
            """async page => page.evaluate(() => {
              const main = document.querySelector('main');
              return {
                hash: location.hash,
                targetId: main.id,
                activeElementId: document.activeElement?.id || null,
                targetTop: main.getBoundingClientRect().top,
                targetVisible: main.getBoundingClientRect().top < innerHeight
              };
            })""",
        )

        nav_count = cli.value(
            session, "async page => page.locator('nav a').count()"
        )
        anchors = []
        for index in range(nav_count):
            selector = f"nav a:nth-of-type({index + 1})"
            cli.run(session, "click", selector)
            anchors.append(
                cli.value(
                    session,
                    """async page => page.evaluate(() => {
                      const hash = location.hash;
                      const target = document.querySelector(hash);
                      const heading = target?.querySelector('h2');
                      const box = heading?.getBoundingClientRect();
                      return {
                        hash,
                        targetExists: Boolean(target),
                        heading: heading?.textContent.trim() || null,
                        headingVisible: Boolean(box && box.bottom > 0 && box.top < innerHeight)
                      };
                    })""",
                )
            )

        cli.run(session, "click", '.filter-btn[data-filter="setup"]')
        setup_filter = cli.value(
            session,
            """async page => page.evaluate(() => ({
              buttons: [...document.querySelectorAll('.filter-btn')].map(button => ({
                mode: button.dataset.filterMode,
                filter: button.dataset.filter || null,
                pressed: button.getAttribute('aria-pressed')
              })),
              visibleCategories: [...document.querySelectorAll('.feature-card')]
                .filter(card => !card.hidden).map(card => card.dataset.category)
            }))""",
        )
        cli.run(session, "click", '.filter-btn[data-filter-mode="all"]')
        all_filter = cli.value(
            session,
            """async page => page.evaluate(() => ({
              pressed: document.querySelector('.filter-btn[data-filter-mode="all"]')
                .getAttribute('aria-pressed'),
              visible: [...document.querySelectorAll('.feature-card')]
                .filter(card => !card.hidden).length,
              total: document.querySelectorAll('.feature-card').length
            }))""",
        )

        accordion_initial = _accordion_state(cli, session)
        cli.run(session, "click", ".accordion-toggle")
        accordion_mouse = _accordion_state(cli, session)
        cli.run(session, "press", "Enter")
        accordion_enter = _accordion_state(cli, session)
        cli.run(session, "press", "Space")
        accordion_space = _accordion_state(cli, session)

        cli.run(session, "click", ".copy")
        copy_result = cli.value(
            session,
            """async page => {
              await page.waitForTimeout(150);
              return page.evaluate(async () => {
                const status = document.querySelector('.copy-status');
                const code = document.querySelector('.codebox code');
                return {
                  announcement: status.textContent,
                  ariaLive: status.getAttribute('aria-live'),
                  sourceText: code.textContent,
                  buttonLabel: document.querySelector('.copy').getAttribute('aria-label'),
                  clipboardText: await navigator.clipboard.readText(),
                  clipboardPermissions: {origin: location.origin, granted: true}
                };
              });
            }""",
        )

        cli.run(session, "goto", candidate_url)
        focusable_count = cli.value(
            session,
            """async page => page.evaluate(() =>
              [...document.querySelectorAll(
                'a[href],button,[tabindex="0"],input,select,textarea'
              )].filter(element => {
                const style = getComputedStyle(element);
                return !element.hidden && style.display !== 'none' &&
                  style.visibility !== 'hidden' && !element.disabled;
              }).length
            )""",
        )
        focus_trace = []
        for _ in range(focusable_count + 1):
            cli.run(session, "press", "Tab")
            focus_trace.append(_focus_state(cli, session))

        cli.run(session, "goto", collision_url)
        cli.run(
            session,
            "click",
            '.filter-btn[data-filter-mode="category"][data-filter="all"]',
        )
        category_all = cli.value(
            session,
            """async page => page.evaluate(() => ({
              categoryPressed: document.querySelector(
                '.filter-btn[data-filter-mode="category"][data-filter="all"]'
              ).getAttribute('aria-pressed'),
              allControlPressed: document.querySelector(
                '.filter-btn[data-filter-mode="all"]'
              ).getAttribute('aria-pressed'),
              visibleCategories: [...document.querySelectorAll('.feature-card')]
                .filter(card => !card.hidden).map(card => card.dataset.category),
              hiddenCategories: [...document.querySelectorAll('.feature-card')]
                .filter(card => card.hidden).map(card => card.dataset.category)
            }))""",
        )
        cli.run(session, "click", '.filter-btn[data-filter-mode="all"]')
        collision_all_control = cli.value(
            session,
            """async page => page.evaluate(() => ({
              pressed: document.querySelector('.filter-btn[data-filter-mode="all"]')
                .getAttribute('aria-pressed'),
              visible: [...document.querySelectorAll('.feature-card')]
                .filter(card => !card.hidden).length,
              total: document.querySelectorAll('.feature-card').length
            }))""",
        )

        semantics = cli.value(
            session,
            """async page => page.evaluate(() => {
              const positiveTabindex = [...document.querySelectorAll('[tabindex]')]
                .filter(element => Number(element.getAttribute('tabindex')) > 0).length;
              const mediaAlt = [...document.querySelectorAll('img')].map(image => ({
                alt: image.alt, useful: image.alt.trim().length >= 4 &&
                  !/^(image|photo|picture|图片|图像)$/i.test(image.alt.trim())
              }));
              const flow = document.querySelector('.flow');
              const nav = document.querySelector('nav');
              const groups = [...document.querySelectorAll('[role="group"]')];
              return {
                positiveTabindex,
                mediaAlt,
                flowLabel: flow?.getAttribute('aria-label') || null,
                navLabel: nav?.getAttribute('aria-label') || null,
                groupLabels: groups.map(group => group.getAttribute('aria-label')),
                labelledSections: [...document.querySelectorAll('section')].every(section => {
                  const labelledby = section.getAttribute('aria-labelledby');
                  return Boolean(labelledby && document.getElementById(labelledby));
                }),
                liveRegions: [...document.querySelectorAll('[aria-live="polite"]')].length
              };
            })""",
        )

        interaction = {
            "skip_link": {"before": skip_before, "after": skip_after},
            "anchors": anchors,
            "filters": {
                "setup": setup_filter,
                "all_control": all_filter,
                "all_category_collision": category_all,
                "collision_all_control": collision_all_control,
            },
            "accordion": {
                "initial": accordion_initial,
                "mouse": accordion_mouse,
                "enter": accordion_enter,
                "space": accordion_space,
            },
            "copy": copy_result,
        }
        accessibility = {
            "focusable_count": focusable_count,
            "focus_order_trace": focus_trace,
            "semantics": semantics,
        }
        return interaction, accessibility, cli.events(session)
    finally:
        cleanup.append(cli.close(session, "interactions"))


def _run_offline(
    cli: PlaywrightCLI,
    root: Path,
    candidate_path: Path,
    run_nonce: str,
    cleanup: list[dict],
) -> tuple[dict, dict]:
    session = _session("offline", root, run_nonce)
    cli.open(session)
    try:
        cli.attach_events(session)
        cli.run(session, "resize", "390", "844")
        cli.run(session, "goto", _path_url(candidate_path))
        cli.run(session, "network-state-set", "offline")
        cli.run(session, "reload")
        result = cli.value(
            session,
            """async page => page.evaluate(() => ({
              urlScheme: location.protocol,
              title: document.title,
              sectionCount: document.querySelectorAll('section').length,
              images: [...document.images].map(image => ({
                srcScheme: image.src.split(':', 1)[0],
                complete: image.complete,
                naturalWidth: image.naturalWidth
              })),
              embeddedContentVisible: [...document.querySelectorAll('section')].every(section =>
                section.getBoundingClientRect().height > 0
              )
            }))""",
        )
        cli.run(session, "network-state-set", "online")
        return result, cli.events(session)
    finally:
        cleanup.append(cli.close(session, "offline"))


def _run_print(
    cli: PlaywrightCLI,
    root: Path,
    collision_url: str,
    pdf_path: Path,
    run_nonce: str,
    cleanup: list[dict],
) -> tuple[dict, dict]:
    session = _session("print", root, run_nonce)
    cli.open(session)
    try:
        cli.attach_events(session)
        cli.run(session, "resize", "1440", "1000")
        cli.run(session, "goto", collision_url)
        cli.run(session, "click", '.filter-btn[data-filter="setup"]')
        screen_hidden_cards = cli.value(
            session,
            """async page => page.evaluate(() =>
              [...document.querySelectorAll('.feature-card')].filter(card => card.hidden).length
            )""",
        )
        print_state = cli.value(
            session,
            """async page => {
              await page.emulateMedia({media: 'print'});
              return page.evaluate(() => {
                const display = selector => getComputedStyle(
                  document.querySelector(selector)
                ).display;
                const shell = document.querySelector('.shell').getBoundingClientRect();
                const main = document.querySelector('main').getBoundingClientRect();
                return {
                  navDisplay: display('nav'),
                  toolbarDisplay: display('.feature-toolbar'),
                  copyDisplay: display('.copy'),
                  toggleDisplay: display('.accordion-toggle'),
                  accordionPanelDisplay: display('.accordion-panel'),
                  filteredCardDisplays: [...document.querySelectorAll('.feature-card')]
                    .map(card => getComputedStyle(card).display),
                  mainWidth: main.width,
                  shellWidth: shell.width
                };
              });
            }""",
        )
        pdf_result = cli.value(
            session,
            f"""async page => {{
              await page.pdf({json.dumps({**PDF_SETTINGS, "path": str(pdf_path)})});
              return true;
            }}""",
        )
        result = {
            "settings": copy.deepcopy(PDF_SETTINGS),
            "pageBox": _inspect_pdf_page_box(pdf_path),
            "screen_hidden_cards_after_filter": screen_hidden_cards,
            "print_media": print_state,
            "pdf_created": pdf_result,
            "path": str(pdf_path.relative_to(root)),
            "bytes": pdf_path.stat().st_size,
            "sha256": sha256_path(pdf_path),
        }
        return result, cli.events(session)
    finally:
        cleanup.append(cli.close(session, "print"))


def _approximately(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= tolerance


def _hard_checks(
    layout: dict,
    console: dict,
    interactions: dict,
    accessibility: dict,
    offline: dict,
    print_result: dict,
) -> list[dict]:
    candidate = layout["candidate"]["viewports"]
    boundaries = layout["candidate"]["boundaries"]
    checks = []

    def add(name: str, passed: bool, actual, expected) -> None:
        checks.append(
            {"name": name, "passed": bool(passed), "actual": actual, "expected": expected}
        )

    add(
        "browser UA",
        "HeadlessChrome/150.0.0.0" in candidate["1440"]["userAgent"],
        candidate["1440"]["userAgent"],
        "contains HeadlessChrome/150.0.0.0",
    )
    for key, label in (
        ("candidate_console_errors", "candidate console errors"),
        ("candidate_page_errors", "candidate page errors"),
        ("candidate_failed_requests", "candidate failed requests"),
        ("candidate_http_errors", "candidate HTTP errors"),
    ):
        actual = console["counts"][key]
        add(label, actual == 0, actual, 0)
    add("reference approved defects only", layout["known_reference_defects"]["approved"], layout["known_reference_defects"], "only 390 overflow and verified /favicon.ico 404")
    add("browser session cleanup", all(item["closed"] for item in console["cleanup"]), console["cleanup"], "all sessions closed")
    add(
        "390 candidate scroll width",
        candidate["390"]["document"]["scrollWidth"] <= 390,
        candidate["390"]["document"]["scrollWidth"],
        "<= 390",
    )
    for viewport in ("1440", "1024"):
        expected_shell = 1280 if viewport == "1440" else 1024
        add(
            f"{viewport} shell width",
            _approximately(
                candidate[viewport]["shell"]["width"], expected_shell, 2
            ),
            candidate[viewport]["shell"]["width"],
            f"{expected_shell} +/- 2",
        )
        add(
            f"{viewport} nav width",
            _approximately(candidate[viewport]["nav"]["width"], 250, 2),
            candidate[viewport]["nav"]["width"],
            "250 +/- 2",
        )
        add(
            f"{viewport} feature columns",
            candidate[viewport]["featureGrid"]["columnCount"] == 3,
            candidate[viewport]["featureGrid"]["columnCount"],
            3,
        )
    add(
        "1440 shell gap",
        _approximately(float(candidate["1440"]["shell"]["gap"][:-2]), 24, 1),
        candidate["1440"]["shell"]["gap"],
        "24px +/- 1px",
    )
    add(
        "1024 two-column shell",
        candidate["1024"]["shell"]["columnCount"] == 2,
        candidate["1024"]["shell"]["columnCount"],
        2,
    )
    add(
        "768 nav above main",
        candidate["768"]["navAboveMain"],
        candidate["768"]["navAboveMain"],
        True,
    )
    add(
        "768 feature columns",
        candidate["768"]["featureGrid"]["columnCount"] == 2,
        candidate["768"]["featureGrid"]["columnCount"],
        2,
    )
    add(
        "390 feature columns",
        candidate["390"]["featureGrid"]["columnCount"] == 1,
        candidate["390"]["featureGrid"]["columnCount"],
        1,
    )
    for name, expected in COLOR_TOKENS.items():
        actual = candidate["1440"]["colors"][name]
        add(f"color token {name}", actual == expected, actual, expected)
    add(
        "hero radius",
        candidate["1440"]["hero"]["borderRadius"] == "28px",
        candidate["1440"]["hero"]["borderRadius"],
        "28px",
    )
    add(
        "section radius",
        candidate["1440"]["section"]["borderRadius"] == "18px",
        candidate["1440"]["section"]["borderRadius"],
        "18px",
    )
    for index, side in enumerate(("top", "right", "bottom", "left")):
        actual = float(candidate["1440"]["section"]["padding"][index][:-2])
        add(
            f"section padding {side}",
            _approximately(actual, 26, 2),
            actual,
            "26 +/- 2px",
        )
    add(
        "961 desktop breakpoint",
        boundaries["961"]["shell"]["columnCount"] == 2
        and boundaries["961"]["featureGrid"]["columnCount"] == 3,
        {
            "shell": boundaries["961"]["shell"]["columnCount"],
            "cards": boundaries["961"]["featureGrid"]["columnCount"],
        },
        {"shell": 2, "cards": 3},
    )
    add(
        "960 tablet breakpoint",
        boundaries["960"]["shell"]["columnCount"] == 1
        and boundaries["960"]["featureGrid"]["columnCount"] == 2
        and boundaries["960"]["navAboveMain"],
        {
            "shell": boundaries["960"]["shell"]["columnCount"],
            "cards": boundaries["960"]["featureGrid"]["columnCount"],
            "navAboveMain": boundaries["960"]["navAboveMain"],
        },
        {"shell": 1, "cards": 2, "navAboveMain": True},
    )
    add(
        "621 tablet card breakpoint",
        boundaries["621"]["featureGrid"]["columnCount"] == 2,
        boundaries["621"]["featureGrid"]["columnCount"],
        2,
    )
    add(
        "620 mobile card breakpoint",
        boundaries["620"]["featureGrid"]["columnCount"] == 1,
        boundaries["620"]["featureGrid"]["columnCount"],
        1,
    )

    skip = interactions["skip_link"]
    add(
        "first Tab reaches skip link",
        skip["before"]["text"] == "跳到主要内容"
        and skip["before"]["outlineWidth"] == "3px",
        skip["before"],
        "focused skip link with visible 3px outline",
    )
    add(
        "skip link Enter targets main",
        skip["after"]["hash"] == "#vn-owned-main"
        and skip["after"]["targetVisible"]
        and skip["after"]["activeElementId"] == "vn-owned-main",
        skip["after"],
        "#vn-owned-main visible and focused",
    )
    add(
        "all nav anchors resolve",
        bool(interactions["anchors"])
        and all(
            item["targetExists"] and item["headingVisible"]
            for item in interactions["anchors"]
        ),
        interactions["anchors"],
        "all targets and headings visible",
    )
    setup = interactions["filters"]["setup"]
    add(
        "setup filter state",
        setup["visibleCategories"] == ["setup"]
        and any(
            button["filter"] == "setup" and button["pressed"] == "true"
            for button in setup["buttons"]
        ),
        setup,
        "only setup visible and pressed",
    )
    collision = interactions["filters"]["all_category_collision"]
    add(
        "category named all is distinct",
        collision["categoryPressed"] == "true"
        and collision["allControlPressed"] == "false"
        and collision["visibleCategories"] == ["all"]
        and collision["hiddenCategories"] == ["setup"],
        collision,
        "category all only",
    )
    collision_control = interactions["filters"]["collision_all_control"]
    add(
        "all control restores all categories",
        collision_control["pressed"] == "true"
        and collision_control["visible"] == collision_control["total"],
        collision_control,
        "all cards visible",
    )
    accordion = interactions["accordion"]
    add(
        "accordion mouse activation",
        accordion["mouse"]
        == {"expanded": "true", "panelHidden": False, "symbol": "−"},
        accordion["mouse"],
        {"expanded": "true", "panelHidden": False, "symbol": "−"},
    )
    add(
        "accordion Enter activation",
        accordion["enter"]
        == {"expanded": "false", "panelHidden": True, "symbol": "＋"},
        accordion["enter"],
        {"expanded": "false", "panelHidden": True, "symbol": "＋"},
    )
    add(
        "accordion Space activation",
        accordion["space"]
        == {"expanded": "true", "panelHidden": False, "symbol": "−"},
        accordion["space"],
        {"expanded": "true", "panelHidden": False, "symbol": "−"},
    )
    copy_result = interactions["copy"]
    add(
        "copy live announcement",
        _copy_matches_source(copy_result),
        copy_result,
        "clipboard exactly matches source text and 已复制 polite announcement",
    )

    semantics = accessibility["semantics"]
    add(
        "no positive tabindex",
        semantics["positiveTabindex"] == 0,
        semantics["positiveTabindex"],
        0,
    )
    focus_trace = accessibility["focus_order_trace"]
    add(
        "logical focus order",
        len(focus_trace) == accessibility["focusable_count"] + 1
        and focus_trace[0]["text"] == "跳到主要内容"
        and focus_trace[-1]["tag"] == "body",
        focus_trace,
        "DOM focus sequence returns to body without trap",
    )
    add(
        "visible focus styles",
        all(
            item["tag"] == "body"
            or (
                item["outlineStyle"] != "none"
                and float(item["outlineWidth"][:-2]) >= 3
            )
            for item in focus_trace
        ),
        focus_trace,
        "all focused controls have >=3px outline",
    )
    add(
        "useful media alt",
        bool(semantics["mediaAlt"])
        and all(item["useful"] for item in semantics["mediaAlt"]),
        semantics["mediaAlt"],
        "all useful",
    )
    add(
        "labelled flow navigation controls and sections",
        bool(semantics["flowLabel"])
        and bool(semantics["navLabel"])
        and all(semantics["groupLabels"])
        and semantics["labelledSections"]
        and semantics["liveRegions"] > 0,
        semantics,
        "all labelled",
    )
    add(
        "offline self-contained content",
        offline["urlScheme"] == "file:"
        and offline["sectionCount"] > 0
        and offline["embeddedContentVisible"]
        and bool(offline["images"])
        and all(
            image["srcScheme"] == "data"
            and image["complete"]
            and image["naturalWidth"] > 0
            for image in offline["images"]
        ),
        offline,
        "file URL, visible sections, complete embedded data images",
    )
    print_media = print_result["print_media"]
    add(
        "print controls hidden",
        all(
            print_media[key] == "none"
            for key in (
                "navDisplay",
                "toolbarDisplay",
                "copyDisplay",
                "toggleDisplay",
            )
        ),
        {
            key: print_media[key]
            for key in (
                "navDisplay",
                "toolbarDisplay",
                "copyDisplay",
                "toggleDisplay",
            )
        },
        "all none",
    )
    add(
        "print content expanded",
        print_result["screen_hidden_cards_after_filter"] > 0
        and print_media["accordionPanelDisplay"] != "none"
        and all(display != "none" for display in print_media["filteredCardDisplays"]),
        {
            "screenHiddenCards": print_result["screen_hidden_cards_after_filter"],
            "accordionPanelDisplay": print_media["accordionPanelDisplay"],
            "filteredCardDisplays": print_media["filteredCardDisplays"],
        },
        "accordion and filtered cards visible",
    )
    add(
        "print main full width",
        _approximately(print_media["mainWidth"], print_media["shellWidth"], 1),
        {
            "mainWidth": print_media["mainWidth"],
            "shellWidth": print_media["shellWidth"],
        },
        "main width equals shell width +/- 1px",
    )
    add(
        "A4 PDF non-empty",
        print_result["pdf_created"] and print_result["bytes"] > 1000,
        print_result["bytes"],
        "> 1000 bytes",
    )
    add("canonical PDF settings", print_result["settings"] == PDF_SETTINGS, print_result["settings"], PDF_SETTINGS)
    add("A4 PDF page box", print_result["pageBox"].get("ok") and _pdf_page_box_is_a4(print_result["pageBox"]), print_result["pageBox"], {"points": PDF_A4_POINTS, "tolerance": PDF_A4_TOLERANCE_POINTS})
    return checks


def _visual_review(root: Path, layout: dict, checks: list[dict]) -> str:
    candidate = layout["candidate"]["viewports"]
    reference = layout["reference"]["viewports"]
    viewport_checks = {
        label: [
            check for check in checks if check["name"].startswith(label)
        ]
        for label in VIEWPORTS
    }
    lines = [
        "# Visual Review",
        "",
        "| Viewport | Reference | Candidate | Tolerance | Result | Approved intentional differences |",
        "|---|---|---|---|---|---|",
    ]
    for label in VIEWPORTS:
        result = (
            "PASS"
            if all(check["passed"] for check in viewport_checks[label])
            else "FAIL"
        )
        reference_summary = (
            f"shell {reference[label]['shell']['width']:.2f}px; "
            f"nav {reference[label]['nav']['width']:.2f}px; "
            f"cards {reference[label]['featureGrid']['columnCount']}; "
            f"scroll {reference[label]['document']['scrollWidth']}px"
        )
        candidate_summary = (
            f"shell {candidate[label]['shell']['width']:.2f}px; "
            f"nav {candidate[label]['nav']['width']:.2f}px; "
            f"cards {candidate[label]['featureGrid']['columnCount']}; "
            f"scroll {candidate[label]['document']['scrollWidth']}px"
        )
        difference = (
            "Reference horizontal overflow and missing favicon are approved defects."
            if label == "390"
            else "Content height and wrapping are not scored."
        )
        lines.append(
            f"| {label} | {reference_summary} | {candidate_summary} | "
            "shell/nav ±2px; gap ±1px; exact columns/tokens | "
            f"{result} | {difference} |"
        )
    lines.extend(
        [
            "",
            "## Global style checks",
            "",
            "| Check names | Tolerance | Result |",
            "|---|---|---|",
            f"| {', '.join(check['name'] for check in checks if check['name'].startswith('color token '))} | exact tokens | {'PASS' if all(check['passed'] for check in checks if check['name'].startswith('color token ')) else 'FAIL'} |",
            f"| hero radius; section radius | exact 28px / 18px | {'PASS' if all(check['passed'] for check in checks if check['name'] in {'hero radius', 'section radius'}) else 'FAIL'} |",
            f"| {', '.join(check['name'] for check in checks if check['name'].startswith('section padding '))} | 26px +/- 2px | {'PASS' if all(check['passed'] for check in checks if check['name'].startswith('section padding ')) else 'FAIL'} |",
            "",
            "Reference screenshots: `reference/{1440,1024,768,390}.png`.",
            "",
            "Candidate screenshots: `candidate/{1440,1024,768,390}.png`.",
            "",
            "The immutable golden file is measured, not modified. Content-driven height and wrapping are intentionally excluded.",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Task 5 acceptance with pinned Playwright CLI."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to the script repository).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.root.resolve()
    acceptance = root / "acceptance"
    screenshots = acceptance / "screenshots"
    fixture = root / "tests" / "fixtures" / "video-note-v2" / "all-blocks.json"
    golden = (
        root
        / "references"
        / "html-golden"
        / "MindMap_Builder_中文实用指南_最新版.html"
    )
    candidate = acceptance / "candidate.html"
    pdf_path = acceptance / "print-result.pdf"

    if shutil.which("npx") is None:
        print("npx is required", file=sys.stderr)
        return 2
    for required in (fixture, golden, root / "scripts" / "render_html.py"):
        if not required.is_file():
            print(f"required file missing: {required}", file=sys.stderr)
            return 2

    acceptance.mkdir(parents=True, exist_ok=True)
    for page_name in ("reference", "candidate"):
        (screenshots / page_name).mkdir(parents=True, exist_ok=True)

    print("[setup] generating candidate through renderer CLI", flush=True)
    run_nonce = secrets.token_hex(6)
    cleanup: list[dict] = []
    generation = _generate_candidate(root, fixture, candidate)
    fixture_note = json.loads(fixture.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix=".task5-", dir=root) as temp_name:
        temp_root = Path(temp_name)
        collision_fixture = temp_root / "all-blocks-category-all.json"
        collision_media = temp_root / "media"
        collision_media.mkdir()
        shutil.copy2(
            fixture.parent / "media" / "frame.jpg",
            collision_media / "frame.jpg",
        )
        collision_fixture.write_text(
            json.dumps(
                add_all_category_collision(fixture_note),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        collision_candidate = temp_root / "candidate-category-all.html"
        collision_generation = _generate_candidate(
            root, collision_fixture, collision_candidate
        )

        with tempfile.TemporaryDirectory(prefix="task5-playwright-") as runtime_name:
            cli = PlaywrightCLI(root, Path(runtime_name))
            version_result = subprocess.run(
                [*PINNED_PLAYWRIGHT_COMMAND, "--version"],
                cwd=root,
                env=cli.environment,
                capture_output=True,
                text=True,
                check=False,
            )
            if version_result.returncode != 0:
                print(version_result.stderr, file=sys.stderr)
                return 2

            with serve_repository(root) as port:
                candidate_url = _served_url(
                    port, candidate.relative_to(root)
                )
                reference_url = _served_url(port, golden.relative_to(root))
                collision_url = _served_url(
                    port, collision_candidate.relative_to(root)
                )

                print("[browser] capturing candidate layout", flush=True)
                candidate_layout, candidate_events = (
                    _capture_layout_and_screenshots(
                        cli,
                        root,
                        "candidate",
                        candidate_url,
                        screenshots / "candidate",
                        run_nonce,
                        cleanup,
                        boundaries=True,
                    )
                )
                print("[browser] capturing reference layout", flush=True)
                reference_layout, reference_events = (
                    _capture_layout_and_screenshots(
                        cli,
                        root,
                        "reference",
                        reference_url,
                        screenshots / "reference",
                        run_nonce,
                        cleanup,
                        boundaries=False,
                    )
                )
                print("[browser] exercising interactions and accessibility", flush=True)
                interactions, accessibility, interaction_events = _run_interactions(
                    cli, root, candidate_url, collision_url, run_nonce, cleanup
                )
                print("[browser] verifying offline file reload", flush=True)
                offline, offline_events = _run_offline(cli, root, candidate, run_nonce, cleanup)
                print("[browser] verifying print media and PDF", flush=True)
                print_result, print_events = _run_print(
                    cli, root, collision_url, pdf_path, run_nonce, cleanup
                )

                layout = {
                    "repository": str(root),
                    "port": port,
                    "playwright_cli": {
                        "command": " ".join(PINNED_PLAYWRIGHT_COMMAND),
                        "version": version_result.stdout.strip(),
                    },
                    "fixture": {
                        "path": str(fixture.relative_to(root)),
                        "sha256": sha256_path(fixture),
                    },
                    "golden": {
                        "path": str(golden.relative_to(root)),
                        "sha256": sha256_path(golden),
                    },
                    "candidate_html": {
                        **generation,
                        "path": str(candidate.relative_to(root)),
                        "served_url": candidate_url,
                        "file_url": _path_url(candidate),
                    },
                    "category_all_variant": {
                        "derived_from": str(fixture.relative_to(root)),
                        "sha256": collision_generation["sha256"],
                        "bytes": collision_generation["bytes"],
                    },
                    "candidate": candidate_layout,
                    "reference": reference_layout,
                }
                layout["known_reference_defects"] = _reference_defect_status(reference_layout, reference_events)
                console = {
                    "candidate_layout": candidate_events,
                    "reference_layout": reference_events,
                    "candidate_interactions": interaction_events,
                    "candidate_offline": offline_events,
                    "candidate_print": print_events,
                    "cleanup": cleanup,
                    "counts": {
                        **aggregate_candidate_events(
                            (
                                candidate_events,
                                interaction_events,
                                offline_events,
                                print_events,
                            )
                        ),
                        "reference_console_errors": len(
                            reference_events["consoleErrors"]
                        ),
                        "reference_page_errors": len(reference_events["pageErrors"]),
                        "reference_failed_requests": len(
                            reference_events["requestFailures"]
                        ),
                        "reference_http_errors": len(reference_events["httpErrors"]),
                        "reference_requests": len(reference_events["requests"]),
                    },
                }
                interactions["offline"] = offline
                interactions["print"] = print_result
                checks = _hard_checks(
                    layout,
                    console,
                    interactions,
                    accessibility,
                    offline,
                    print_result,
                )
                layout["hard_checks"] = checks
                layout["hard_failures"] = failed_hard_checks(checks)
                layout["normalized_sha256"] = hashlib.sha256(
                    (
                        json.dumps(
                            normalize_for_parity(layout, repo_root=root),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                ).hexdigest()

                _write_json(acceptance / "console-results.json", console)
                _write_json(
                    acceptance / "interaction-results.json", interactions
                )
                _write_json(
                    acceptance / "accessibility-results.json", accessibility
                )
                _write_json(acceptance / "layout-results.json", layout)
                (screenshots / "VISUAL_REVIEW.md").write_text(
                    _visual_review(root, layout, checks),
                    encoding="utf-8",
                )

    failures = failed_hard_checks(checks)
    if failures:
        print(
            f"Task 5 acceptance failed: {len(failures)} hard check(s)",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"- {failure['name']}: {failure['actual']}", file=sys.stderr)
        return 1
    print(f"Task 5 acceptance passed: {len(checks)} hard checks", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
