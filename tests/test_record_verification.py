from pathlib import Path
from subprocess import CompletedProcess

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import record_verification


def test_records_exact_required_commands_atomically(tmp_path, monkeypatch):
    completed = []

    def fake_run(command, **kwargs):
        completed.append((command, kwargs))
        return CompletedProcess(command, 0, "PASS\n", "")

    monkeypatch.setattr(record_verification.subprocess, "run", fake_run)
    output = tmp_path / "required-verification.json"

    evidence = record_verification.run(output)

    assert evidence["passed"]
    assert [item["command"] for item in evidence["commands"]] == [
        "uv run --extra dev pytest -q",
        "uv run --extra dev python scripts/acceptance_check.py",
    ]
    assert all(item["exit_code"] == 0 for item in evidence["commands"])
    assert all(kwargs["cwd"] == ROOT for _, kwargs in completed)
    assert output.is_file()
    assert not output.with_suffix(".json.tmp").exists()
