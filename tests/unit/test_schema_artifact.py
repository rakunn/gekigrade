from __future__ import annotations

import json
from pathlib import Path

from gekigrade.domain.models import EditPlan


def test_committed_schema_matches_the_runtime_model() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "schemas/edit-plan.schema.json"
    committed = json.loads(schema_path.read_text(encoding="utf-8"))

    assert committed == EditPlan.model_json_schema()
