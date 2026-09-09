from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gekigrade.domain.models import EditPlan, EditPlanV2, edit_plan_schema


def test_committed_schema_matches_the_runtime_model() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "schemas/edit-plan.schema.json"
    committed = json.loads(schema_path.read_text(encoding="utf-8"))

    assert committed == edit_plan_schema()
    for version, model in (("1.0.0", EditPlan), ("2.0.0", EditPlanV2)):
        versioned = schema_path.with_name(f"edit-plan-{version}.schema.json")
        assert json.loads(versioned.read_text()) == model.model_json_schema()


def test_union_local_references_share_one_resource_root() -> None:
    schema = edit_plan_schema()

    def check(value: Any, *, root: bool = False) -> None:
        if isinstance(value, dict):
            if not root:
                assert "$id" not in value
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/")
                target: Any = schema
                for segment in reference[2:].split("/"):
                    target = target[segment.replace("~1", "/").replace("~0", "~")]
                assert isinstance(target, dict)
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(schema, root=True)
