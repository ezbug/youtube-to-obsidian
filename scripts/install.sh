#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
skill_name=$(basename "$repo_root")
target=codex
mode=symlink
category=

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target) target=$2; shift 2 ;;
    --mode) mode=$2; shift 2 ;;
    --category) category=$2; shift 2 ;;
    -h|--help)
      echo "Usage: install.sh [--target codex|hermes] [--mode symlink|copy] [--category name]"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$target" in
  codex) base=${CODEX_HOME:-"$HOME/.codex"}/skills ;;
  hermes) base=${HERMES_HOME:-"$HOME/.hermes"}/skills ;;
  *) echo "target must be codex or hermes" >&2; exit 2 ;;
esac

if [ "$target" = hermes ] && [ -n "$category" ]; then
  base=$base/$category
fi
dest=$base/$skill_name
mkdir -p "$base"

if [ -e "$dest" ] || [ -L "$dest" ]; then
  echo "Refusing to overwrite existing path: $dest" >&2
  exit 1
fi

case "$mode" in
  symlink) ln -s "$repo_root" "$dest" ;;
  copy)
    mkdir "$dest"
    rsync -a --exclude='.git' --exclude='.venv' --exclude='node_modules' --exclude='__pycache__' "$repo_root/" "$dest/"
    printf '%s\n' "$repo_root" > "$dest/.ezbug-skill-install-origin"
    ;;
  *) echo "mode must be symlink or copy" >&2; exit 2 ;;
esac

echo "Installed $skill_name at $dest ($mode)"
