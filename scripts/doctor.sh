#!/bin/sh
set -u

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
failed=0

check_command() {
  command_name=$1
  if command -v "$command_name" >/dev/null 2>&1; then
    printf 'OK   command %s -> %s\n' "$command_name" "$(command -v "$command_name")"
  else
    printf 'MISS command %s\n' "$command_name"
    failed=1
  fi
}

[ -f "$repo_root/SKILL.md" ] && echo "OK   SKILL.md" || { echo "MISS SKILL.md"; failed=1; }
check_command python3
check_command rg
if [ -f "$repo_root/pyproject.toml" ]; then
  check_command uv
fi
if [ -f "$repo_root/package.json" ]; then
  check_command node
fi
if [ -f "$repo_root/requirements.system" ]; then
  while IFS= read -r command_name; do
    case "$command_name" in
      ""|\#*) continue ;;
    esac
    check_command "$command_name"
  done < "$repo_root/requirements.system"
fi

if rg -n --hidden -g '!*.woff2' -g '!*.png' -g '!*.jpg' -g '!**/doctor.sh' '/Users/|[A-Za-z]:\\\\Users\\\\|\.openclaw/workspace|\.claude/mcp-servers' "$repo_root" >/tmp/ezbug-skill-doctor-paths.$$ 2>/dev/null; then
  echo "FAIL personal or machine-specific path references:"
  sed -n '1,40p' /tmp/ezbug-skill-doctor-paths.$$
  failed=1
else
  echo "OK   no known personal path references"
fi
rm -f /tmp/ezbug-skill-doctor-paths.$$

if rg -n --hidden -g '!*.woff2' -g '!*.png' -g '!*.jpg' '(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY)' "$repo_root" >/tmp/ezbug-skill-doctor-secrets.$$ 2>/dev/null; then
  echo "FAIL possible credential material:"
  sed -n '1,20p' /tmp/ezbug-skill-doctor-secrets.$$
  failed=1
else
  echo "OK   no obvious credential material"
fi
rm -f /tmp/ezbug-skill-doctor-secrets.$$

exit "$failed"
