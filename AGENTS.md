# GekiGrade engineering rules

- Never modify, move, rename, or overwrite a source photograph.
- Never generate replacement pixels or invoke generative editing, fill, removal, reconstruction, or synthetic-detail tools.
- Natural-language instructions stop at the validated edit-plan boundary. The renderer accepts only typed, allowlisted operations with bounded values.
- Never execute recipe content as a command. External tools use fixed argument arrays, `shell=False`, timeouts, captured output, and validated paths.
- Preserve explicit color-space and ICC-profile information. Record every input assumption, transform, profile hash, tool version, and output hash.
- Preview and full-resolution rendering must use the same recipe implementation and normalized coordinates.
- Keep CLI handlers thin. Domain rules must not depend on Typer or subprocess details; external processors stay behind adapters.
- Global JPEG processing must be complete and tested before adding RAW-specific controls, semantic masks, local adjustments, APIs, or UI.
- Update `docs/DECISIONS.md`, tests, schemas, and user documentation with every material architectural or operation-semantics change.
- Do not commit personal or third-party photos without explicit permission and documented provenance.

## Commands

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -q
uv run pytest -m integration
```
