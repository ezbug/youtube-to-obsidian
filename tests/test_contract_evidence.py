from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from contract_evidence import generate


def test_contract_evidence_is_direct_portable_and_complete(tmp_path):
    result = generate(tmp_path / "contract")

    assert result["passed"]
    assert result["schema_sha256"] == (
        "9255bbdf4965221faac53972a7f66b1d644588376047fb243716c9495e16c2fd"
    )
    assert len(result["cli_cases"]) == 10
    assert {case["actual_exit"] for case in result["cli_cases"]} >= {0, 2, 3}
    assert all(case["passed"] for case in result["cli_cases"])
    assert all(
        case["atomic_output_preserved"] is True
        for case in result["cli_cases"]
        if case["expected_exit"] != 0 and case["output_existed_before"]
    )
    assert result["legacy"]["passed"]
    assert (tmp_path / "contract" / "legacy-output.html").is_file()
    assert (tmp_path / "contract" / "normalized-all-blocks.json").is_file()
    assert (tmp_path / "contract" / "fixtures" / "malicious.json").is_file()
    assert "/" + "Users/" not in (
        tmp_path / "contract" / "contract-results.json"
    ).read_text(encoding="utf-8")
