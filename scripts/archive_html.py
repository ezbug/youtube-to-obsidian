#!/usr/bin/env python3
"""Archive an accepted video-note HTML with a representative, portable name."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Mapping


EXIT_USAGE = 2
DEFAULT_LOCAL_CONFIG = Path(__file__).resolve().parents[1] / "archive-config.local.json"
FORBIDDEN_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
GENERIC_TITLES = {
    "summary",
    "video note",
    "video notes",
    "视频总结",
    "视频笔记",
    "某某总结",
}


class ArchiveError(ValueError):
    """Raised when an accepted HTML cannot be archived safely."""


def _clean_component(value: str, *, field: str) -> str:
    cleaned = FORBIDDEN_FILENAME_CHARS.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        raise ArchiveError(f"{field} is empty after filename sanitization")
    return cleaned


def normalize_title(title: str) -> str:
    cleaned = _clean_component(title, field="title")
    if cleaned.casefold() in GENERIC_TITLES:
        raise ArchiveError(
            "title must summarize the video's topic and representative conclusion; "
            f"generic title {cleaned!r} is not accepted"
        )
    if len(cleaned) < 4:
        raise ArchiveError("title must contain at least 4 characters")
    return cleaned


def _load_config(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArchiveError(f"config file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"cannot read JSON config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ArchiveError(f"config must contain a JSON object: {path}")
    return payload


def resolve_archive_dir(
    *,
    cli_value: str | None,
    config_path: str | None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    if cli_value:
        return Path(cli_value).expanduser()

    env_value = env.get("VIDEO_NOTE_ARCHIVE_DIR", "").strip()
    if env_value:
        return Path(env_value).expanduser()

    selected_config = config_path or env.get("VIDEO_NOTE_CONFIG", "").strip()
    if selected_config:
        payload = _load_config(Path(selected_config).expanduser())
        configured = payload.get("archive_dir")
        if isinstance(configured, str) and configured.strip():
            return Path(configured).expanduser()
        raise ArchiveError(
            f'config field "archive_dir" must be a non-empty string: {selected_config}'
        )

    raise ArchiveError(
        "archive directory is not configured; pass --archive-dir, set "
        "VIDEO_NOTE_ARCHIVE_DIR, or pass --config/VIDEO_NOTE_CONFIG"
    )


def _same_bytes(path: Path, expected: bytes) -> bool:
    try:
        return path.read_bytes() == expected
    except OSError:
        return False


def _candidate_path(
    archive_dir: Path,
    stem: str,
    content: bytes,
    source_id: str | None,
) -> tuple[Path, bool]:
    first = archive_dir / f"{stem}.html"
    if not first.exists():
        return first, False
    if _same_bytes(first, content):
        return first, True

    collision_stem = stem
    if source_id:
        collision_stem = f"{stem}（{_clean_component(source_id, field='source-id')}）"
        identified = archive_dir / f"{collision_stem}.html"
        if not identified.exists():
            return identified, False
        if _same_bytes(identified, content):
            return identified, True

    counter = 2
    while True:
        numbered = archive_dir / f"{collision_stem}（{counter}）.html"
        if not numbered.exists():
            return numbered, False
        if _same_bytes(numbered, content):
            return numbered, True
        counter += 1


def archive_html(
    source: Path,
    archive_dir: Path,
    title: str,
    *,
    source_id: str | None = None,
    profile: str = "web",
) -> dict[str, object]:
    if profile not in {"web", "email"}:
        raise ArchiveError(f"unsupported profile: {profile}")
    if not source.is_file():
        raise ArchiveError(f"HTML source does not exist: {source}")

    content = source.read_bytes()
    if not content:
        raise ArchiveError(f"HTML source is empty: {source}")
    lowered = content[:4096].lower()
    if b"<html" not in lowered and b"<!doctype html" not in lowered:
        raise ArchiveError(f"source does not look like HTML: {source}")

    stem = normalize_title(title)
    if profile == "email":
        stem = f"{stem}—Email"

    archive_dir.mkdir(parents=True, exist_ok=True)
    if not archive_dir.is_dir():
        raise ArchiveError(f"archive destination is not a directory: {archive_dir}")

    target, reused = _candidate_path(archive_dir, stem, content, source_id)
    if not reused:
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=archive_dir,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o644)
            try:
                os.link(temp_name, target)
            except FileExistsError as exc:
                raise ArchiveError(
                    f"archive target appeared during atomic write; retry: {target}"
                ) from exc
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)

    if not _same_bytes(target, content):
        raise ArchiveError(f"byte verification failed after archiving: {target}")

    return {
        "path": str(target.resolve()),
        "profile": profile,
        "reused": reused,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", required=True, help="Accepted source HTML")
    parser.add_argument(
        "--title",
        required=True,
        help="Representative title describing the topic and main conclusion",
    )
    parser.add_argument("--source-id", help="Video ID used only to resolve collisions")
    parser.add_argument("--archive-dir", help="Destination directory (highest priority)")
    parser.add_argument("--config", help="JSON file containing an archive_dir field")
    parser.add_argument("--profile", choices=("web", "email"), default="web")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        selected_config = args.config
        if (
            not selected_config
            and not os.environ.get("VIDEO_NOTE_ARCHIVE_DIR", "").strip()
            and not os.environ.get("VIDEO_NOTE_CONFIG", "").strip()
            and DEFAULT_LOCAL_CONFIG.is_file()
        ):
            selected_config = str(DEFAULT_LOCAL_CONFIG)
        archive_dir = resolve_archive_dir(
            cli_value=args.archive_dir,
            config_path=selected_config,
        )
        result = archive_html(
            Path(args.html).expanduser(),
            archive_dir,
            args.title,
            source_id=args.source_id,
            profile=args.profile,
        )
    except (ArchiveError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
