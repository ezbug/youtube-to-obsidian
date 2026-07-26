# Repository instructions

## Golden HTML baseline

- `references/html-golden/MindMap_Builder_中文实用指南_最新版.html` is an immutable visual reference, not an implementation template or generated output.
- Preserve its bytes exactly. Its SHA-256 must remain `499f331d7638f61c9f873630ba413683fd24a916275a6e98377fad2a63614509`.
- Run `uv run --extra dev pytest -q` after changes that touch the baseline or its checksum coverage.
- Future renderer work may reuse its visual grammar, but must not copy its factual content into generated notes.
