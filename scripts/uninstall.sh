#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
skill_name=$(basename "$repo_root")
target=codex
category=

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target) target=$2; shift 2 ;;
    --category) category=$2; shift 2 ;;
    -h|--help)
      echo "Usage: uninstall.sh [--target codex|hermes] [--category name]"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$target" in
  codex) base=${CODEX_HOME:-"$HOME/.codex"}/skills ;;
  hermes) base=${HERMES_HOME:-"$HOME/.hermes"}/skills ;;
  *) echo "target must be codex or hermes" >&2; exit 2 ;;
esac
[ "$target" = hermes ] && [ -n "$category" ] && base=$base/$category
dest=$base/$skill_name

if [ -L "$dest" ] && [ "$(readlink "$dest")" = "$repo_root" ]; then
  rm "$dest"
  echo "Removed symlink $dest"
  exit 0
fi

if [ -f "$dest/.ezbug-skill-install-origin" ] && [ "$(cat "$dest/.ezbug-skill-install-origin")" = "$repo_root" ]; then
  rm -rf "$dest"
  echo "Removed copied installation $dest"
  exit 0
fi

echo "Refusing to remove an installation not owned by this repository: $dest" >&2
exit 1
