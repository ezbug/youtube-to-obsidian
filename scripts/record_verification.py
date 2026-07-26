#!/usr/bin/env python3
"""Run and record the exact repository-mandated acceptance commands."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    ("uv", "run", "--extra", "dev", "pytest", "-q"),
    (
        "uv",
        "run",
        "--extra",
        "dev",
        "python",
        "scripts/acceptance_check.py",
    ),
)


def run(output: Path | None = None) -> dict:
    results = []
    for command in COMMANDS:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        results.append(
            {
                "command": " ".join(command),
                "argv": list(command),
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "passed": completed.returncode == 0,
            }
        )
    evidence = {
        "commands": results,
        "passed": all(result["passed"] for result in results),
    }
    destination = output or ROOT / "acceptance" / "required-verification.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return evidence


def main() -> int:
    evidence = run()
    for result in evidence["commands"]:
        print(f"$ {result['command']}")
        print(result["stdout"], end="")
        if result["stderr"]:
            print(result["stderr"], end="")
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
