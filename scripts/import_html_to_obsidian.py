from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


# Obsidian CLI 1.13.7 can split UTF-8 characters at its internal 8 KiB
# argument boundary. Keep content arguments well below that boundary.
MAX_CHUNK_BYTES = 4 * 1024
PLATFORMS = {"youtube": "YouTube", "bilibili": "Bilibili"}
SOURCE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
CLI_ERROR_RE = re.compile(r"^Error:\s", re.MULTILINE)


class ImportFailure(RuntimeError):
    """Raised when an import cannot be completed without risking data loss."""


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined_output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


def _json_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _validate_scalar(value: str, field: str) -> str:
    if not value or not value.strip():
        raise ImportFailure(f"{field} must not be empty")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ImportFailure(f"{field} contains a forbidden control character")
    return value.strip()


def _validate_title(value: str) -> str:
    title = _validate_scalar(value, "title")
    if title in {".", ".."} or "/" in title or "\\" in title:
        raise ImportFailure("title must be a single note name without path separators")
    if title.endswith(".md"):
        title = title[:-3].rstrip()
    if not title:
        raise ImportFailure("title must not be empty")
    return title


def validate_vault_relative_path(value: str) -> str:
    path = _validate_scalar(value, "folder")
    if "\\" in path or path.startswith("/"):
        raise ImportFailure("folder must be a Vault-relative POSIX path")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ImportFailure("folder must not contain empty, dot, or parent components")
    parsed = PurePosixPath(path)
    if parsed.is_absolute():
        raise ImportFailure("folder must be a Vault-relative POSIX path")
    return parsed.as_posix()


def validate_vault_name(value: str) -> str:
    vault = _validate_scalar(value, "vault")
    if vault in {".", ".."} or "/" in vault or "\\" in vault:
        raise ImportFailure("vault must be an Obsidian Vault name, not an absolute or relative path")
    return vault


def chunk_text(value: str, max_bytes: int = MAX_CHUNK_BYTES) -> Iterable[str]:
    if max_bytes < 4:
        raise ValueError("max_bytes must be at least 4")
    start = 0
    size = 0
    for index, character in enumerate(value):
        character_size = len(character.encode("utf-8"))
        if character_size > max_bytes:
            raise ValueError("a single character exceeds max_bytes")
        if size and size + character_size > max_bytes:
            yield value[start:index]
            start = index
            size = 0
        size += character_size
    if start < len(value):
        yield value[start:]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ImportFailure(f"cannot read HTML source {path}: {exc}") from exc
    return digest.hexdigest()


def _finder_uri(directory: Path) -> str:
    return directory.resolve().as_uri().rstrip("/") + "/"


def build_note_content(
    *,
    title: str,
    source: str,
    source_id: str,
    platform: str,
    html_path: Path,
    source_sha256: str,
    created: str,
    markdown: str,
) -> str:
    title = _validate_title(title)
    source = _validate_scalar(source, "source")
    source_id = _validate_scalar(source_id, "source_id")
    created = _validate_scalar(created, "created")
    if platform not in PLATFORMS:
        raise ImportFailure(f"unsupported platform: {platform}")
    if not SOURCE_URL_RE.match(source):
        raise ImportFailure("source must be an http(s) URL")
    if not SHA256_RE.fullmatch(source_sha256):
        raise ImportFailure("source_sha256 must be a 64-character hexadecimal hash")
    if not markdown.strip():
        raise ImportFailure("extracted Markdown is empty")

    archive_dir = html_path.resolve().parent
    archive_uri = _finder_uri(archive_dir)
    platform_label = PLATFORMS[platform]
    tags = ["clippings", "video-note", platform]
    tag_lines = "\n".join(f"  - {tag}" for tag in tags)
    body = markdown.rstrip()
    return (
        "---\n"
        f"title: {_json_scalar(title)}\n"
        f"source: {_json_scalar(source)}\n"
        f"source_id: {_json_scalar(source_id)}\n"
        f"created: {created}\n"
        "tags:\n"
        f"{tag_lines}\n"
        f"html_archive: {_json_scalar(str(archive_dir))}\n"
        f"html_archive_uri: {_json_scalar(archive_uri)}\n"
        f"source_html_sha256: {source_sha256}\n"
        "---\n\n"
        "> [!info] HTML 归档\n"
        f"> [在访达中打开 HTML 归档目录](<{archive_uri}>)\n"
        f"> HTML 文件：`{html_path.name}`\n"
        f"> 原始视频：[{platform_label}]({source})\n\n"
        f"{body}\n"
    )


def _looks_like_cli_error(result: CommandResult) -> bool:
    stdout = result.stdout.lstrip()
    stderr = result.stderr.lstrip()
    return (
        result.returncode != 0
        or bool(CLI_ERROR_RE.match(stdout))
        or bool(CLI_ERROR_RE.match(stderr))
    )


class ObsidianCLI:
    def __init__(self, binary: str, vault: str):
        self.binary = binary
        self.vault = validate_vault_name(vault)

    def invoke(self, *parts: str, allow_error: bool = False) -> CommandResult:
        args = (self.binary, f"vault={self.vault}", *parts)
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            check=False,
        )
        result = CommandResult(
            args=args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if not allow_error and _looks_like_cli_error(result):
            raise ImportFailure(
                f"Obsidian CLI failed: {' '.join(args)}\n{result.combined_output}"
            )
        return result

    def exists(self, path: str) -> bool:
        result = self.invoke("read", f"path={path}", allow_error=True)
        if not _looks_like_cli_error(result):
            return True
        output = result.combined_output.casefold()
        if "not found" in output or "does not exist" in output or "不存在" in output:
            return False
        raise ImportFailure(
            f"cannot determine whether Vault path exists: {path}\n{result.combined_output}"
        )

    def create(self, path: str, content: str) -> None:
        self.invoke("create", f"path={path}", f"content={content}")

    def append(self, path: str, content: str) -> None:
        self.invoke("append", f"path={path}", f"content={content}", "inline")

    def read(self, path: str) -> str:
        return self.invoke("read", f"path={path}").stdout

    def move(self, source: str, target: str) -> None:
        self.invoke("move", f"path={source}", f"to={target}")

    def delete(self, path: str) -> None:
        self.invoke("delete", f"path={path}")


def extract_markdown(html_path: Path, *, node_bin: str) -> dict[str, object]:
    adapter = Path(__file__).with_name("defuddle_adapter.mjs")
    if not adapter.is_file():
        raise ImportFailure(f"Defuddle adapter is missing: {adapter}")
    try:
        completed = subprocess.run(
            [node_bin, str(adapter), str(html_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ImportFailure(f"cannot run Node/Defuddle adapter: {exc}") from exc
    if completed.returncode != 0:
        raise ImportFailure(
            "Defuddle extraction failed:\n"
            + (completed.stderr or completed.stdout).strip()
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ImportFailure(f"Defuddle adapter returned invalid JSON: {exc}") from exc
    markdown = result.get("content")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ImportFailure("Defuddle returned empty Markdown")
    result["content"] = markdown
    return result


def _target_path(folder: str, title: str) -> str:
    return f"{folder}/{title}.md"


def _write_preview(path: Path, content: str) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _audit(
    *,
    mode: str,
    source_html: Path,
    source_sha256: str,
    target_path: str,
    note_content: str,
    extraction: dict[str, object],
    vault_write: bool,
    target_verified: bool,
    rolled_back: bool = False,
) -> dict[str, object]:
    return {
        "mode": mode,
        "source_html": str(source_html),
        "source_sha256": source_sha256,
        "target_path": target_path,
        "markdown_bytes": len(note_content.encode("utf-8")),
        "embedded_image_count": note_content.count("data:image/"),
        "extracted_word_count": extraction.get("wordCount"),
        "defuddle_title": extraction.get("title"),
        "vault_write": vault_write,
        "target_verified": target_verified,
        "rolled_back": rolled_back,
    }


def import_html(args: argparse.Namespace) -> dict[str, object]:
    html_path = Path(args.html).expanduser().resolve()
    if not html_path.is_file():
        raise ImportFailure(f"HTML source does not exist: {html_path}")
    if html_path.suffix.casefold() not in {".html", ".htm"}:
        raise ImportFailure("HTML source must have an .html or .htm extension")

    title = _validate_title(args.title or html_path.stem)
    folder = validate_vault_relative_path(args.folder)
    source = _validate_scalar(args.source, "source")
    source_id = _validate_scalar(args.source_id, "source_id")
    if args.platform not in PLATFORMS:
        raise ImportFailure(f"unsupported platform: {args.platform}")
    if not SOURCE_URL_RE.match(source):
        raise ImportFailure("source must be an http(s) URL")

    source_sha256 = sha256_path(html_path)
    if args.expected_html_sha256:
        expected = args.expected_html_sha256.casefold()
        if not SHA256_RE.fullmatch(expected):
            raise ImportFailure("--expected-html-sha256 must be a 64-character hexadecimal hash")
        if source_sha256 != expected:
            raise ImportFailure(
                f"HTML source hash mismatch: expected {expected}, got {source_sha256}"
            )

    extraction = extract_markdown(
        html_path,
        node_bin=args.node_bin or os.environ.get("VIDEO_NOTE_NODE", "node"),
    )
    note_content = build_note_content(
        title=title,
        source=source,
        source_id=source_id,
        platform=args.platform,
        html_path=html_path,
        source_sha256=source_sha256,
        created=args.created or date.today().isoformat(),
        markdown=str(extraction["content"]),
    )
    target = _target_path(folder, title)

    if args.preview:
        _write_preview(Path(args.preview), note_content)

    if not args.apply:
        return _audit(
            mode="dry-run",
            source_html=html_path,
            source_sha256=source_sha256,
            target_path=target,
            note_content=note_content,
            extraction=extraction,
            vault_write=False,
            target_verified=False,
        )

    cli = ObsidianCLI(args.obsidian_bin or os.environ.get("OBSIDIAN_BIN", "obsidian"), args.vault)
    if cli.exists(target):
        raise ImportFailure(f"refusing to overwrite existing Vault note: {target}")

    stage = f"{folder}/{title}.importing-{uuid.uuid4().hex[:12]}.md"
    stage_created = False
    target_created = False
    try:
        chunks = iter(chunk_text(note_content))
        first = next(chunks)
        cli.create(stage, first)
        stage_created = True
        for chunk in chunks:
            cli.append(stage, chunk)

        if cli.read(stage) != note_content:
            raise ImportFailure("staged Vault note does not match generated Markdown")
        if cli.exists(target):
            raise ImportFailure(f"target appeared during import: {target}")

        cli.move(stage, target)
        target_created = True
        if cli.read(target) != note_content:
            raise ImportFailure("final Vault note does not match generated Markdown")
        properties = cli.invoke("properties", f"path={target}", "format=yaml").stdout
        for required in ("title:", "source:", "source_id:", "html_archive:", "source_html_sha256:"):
            if required not in properties:
                raise ImportFailure(f"final Vault note is missing property: {required}")
        return _audit(
            mode="apply",
            source_html=html_path,
            source_sha256=source_sha256,
            target_path=target,
            note_content=note_content,
            extraction=extraction,
            vault_write=True,
            target_verified=True,
        )
    except Exception as exc:
        rollback_errors: list[str] = []
        if stage_created:
            try:
                if cli.exists(stage):
                    cli.delete(stage)
            except Exception as rollback_exc:
                rollback_errors.append(f"stage rollback failed: {rollback_exc}")
        if target_created:
            try:
                if cli.exists(target):
                    cli.delete(target)
            except Exception as rollback_exc:
                rollback_errors.append(f"target rollback failed: {rollback_exc}")
        detail = str(exc)
        if rollback_errors:
            detail += " | " + " | ".join(rollback_errors)
        raise ImportFailure(detail) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", required=True)
    parser.add_argument("--vault", required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--platform", choices=tuple(PLATFORMS), required=True)
    parser.add_argument("--title")
    parser.add_argument("--expected-html-sha256")
    parser.add_argument("--preview")
    parser.add_argument("--created")
    parser.add_argument("--node-bin")
    parser.add_argument("--obsidian-bin")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and args.dry_run:
        print("ERROR: --apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    try:
        audit = import_html(args)
    except ImportFailure as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
