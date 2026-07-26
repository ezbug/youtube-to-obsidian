from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "references" / "html-golden" / "MindMap_Builder_中文实用指南_最新版.html"
EXPECTED_SHA256 = "499f331d7638f61c9f873630ba413683fd24a916275a6e98377fad2a63614509"


def test_golden_reference_checksum():
    assert GOLDEN.is_file()
    assert sha256(GOLDEN.read_bytes()).hexdigest() == EXPECTED_SHA256
