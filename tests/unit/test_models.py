from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from gekigrade.domain.models import EditPlan


def valid_plan_data() -> dict[str, object]:
    candidate = {
        "id": "01-natural-clean",
        "description": "Conservative correction",
        "rotation_degrees": 0.0,
        "exposure_ev": 0.0,
        "temperature_mired_shift": 0.0,
        "contrast": 0.0,
        "black_lift": 0.0,
        "highlight_rolloff": 0.1,
        "saturation": 0.0,
        "vignette": 0.0,
        "sharpen": 0.25,
        "look": {"id": "natural-clean", "version": "1.0.0", "strength": 0.5},
        "crop_id": "feed-4x5-center",
    }
    second = deepcopy(candidate)
    second.update(
        {
            "id": "02-warm-editorial",
            "look": {"id": "warm-editorial", "version": "1.0.0", "strength": 0.6},
        }
    )
    third = deepcopy(candidate)
    third.update(
        {
            "id": "03-muted-cinematic",
            "look": {"id": "muted-cinematic", "version": "1.0.0", "strength": 0.55},
        }
    )
    return {
        "schema_version": "1.0.0",
        "source_sha256": "a" * 64,
        "candidates": [candidate, second, third],
    }


def test_valid_plan_requires_exactly_three_distinct_candidates() -> None:
    plan = EditPlan.model_validate(valid_plan_data())

    assert [candidate.id for candidate in plan.candidates] == [
        "01-natural-clean",
        "02-warm-editorial",
        "03-muted-cinematic",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rotation_degrees", 5.01),
        ("exposure_ev", -2.01),
        ("temperature_mired_shift", 30.01),
        ("contrast", 0.251),
        ("black_lift", 0.031),
        ("highlight_rolloff", 0.501),
        ("saturation", -0.251),
        ("vignette", 0.251),
        ("sharpen", 1.001),
    ],
)
def test_plan_rejects_out_of_range_controls(field: str, value: float) -> None:
    data = valid_plan_data()
    data["candidates"][0][field] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        EditPlan.model_validate(data)


def test_plan_rejects_unknown_fields_and_candidate_ids() -> None:
    data = valid_plan_data()
    data["arbitrary_shell"] = "rm -rf something"

    with pytest.raises(ValidationError):
        EditPlan.model_validate(data)

    duplicate = valid_plan_data()
    duplicate["candidates"][1]["id"] = "01-natural-clean"  # type: ignore[index]
    with pytest.raises(ValidationError):
        EditPlan.model_validate(duplicate)


def test_plan_rejects_wrong_candidate_count_and_bad_hash() -> None:
    too_short = valid_plan_data()
    too_short["candidates"] = too_short["candidates"][:2]  # type: ignore[index]
    with pytest.raises(ValidationError):
        EditPlan.model_validate(too_short)

    bad_hash = valid_plan_data()
    bad_hash["source_sha256"] = "not-a-sha256"
    with pytest.raises(ValidationError):
        EditPlan.model_validate(bad_hash)


def test_versions_dispatch_to_separate_recipe_types_and_reject_mixed_controls() -> None:
    from gekigrade.domain.models import EDIT_PLAN_ADAPTER, CandidateRecipe, CandidateRecipeV2
    from gekigrade.pipeline.prepare import _example_plan_v2

    legacy = valid_plan_data()
    modern = _example_plan_v2("a" * 64)
    assert isinstance(EDIT_PLAN_ADAPTER.validate_python(legacy).candidates[0], CandidateRecipe)
    assert isinstance(EDIT_PLAN_ADAPTER.validate_python(modern).candidates[0], CandidateRecipeV2)
    legacy["schema_version"] = "2.0.0"
    modern["schema_version"] = "1.0.0"
    for data in (legacy, modern):
        with pytest.raises(ValidationError):
            EDIT_PLAN_ADAPTER.validate_python(data)
    modern["schema_version"] = "3.0.0"
    with pytest.raises(ValidationError):
        EDIT_PLAN_ADAPTER.validate_python(modern)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shadow_recovery_ev", -0.01),
        ("shadow_recovery_ev", 2.01),
        ("highlight_compression", -0.01),
        ("highlight_compression", 1.01),
        ("shadow_recovery_ev", float("nan")),
        ("highlight_compression", float("inf")),
        ("highlight_rolloff", 0.1),
        ("output_gamut_strength", 0.5),
    ],
)
def test_v2_bounds_and_fixed_gamut_contract(field: str, value: float) -> None:
    from gekigrade.domain.models import EDIT_PLAN_ADAPTER
    from gekigrade.pipeline.prepare import _example_plan_v2

    data = _example_plan_v2("a" * 64)
    data["candidates"][0][field] = value
    with pytest.raises(ValidationError):
        EDIT_PLAN_ADAPTER.validate_python(data)


def test_v2_requires_exactly_three_distinct_candidates_and_explicit_controls() -> None:
    from gekigrade.domain.models import EDIT_PLAN_ADAPTER
    from gekigrade.pipeline.prepare import _example_plan_v2

    for mutation in ("duplicate", "short", "missing"):
        data = _example_plan_v2("a" * 64)
        if mutation == "duplicate":
            data["candidates"][1]["id"] = data["candidates"][0]["id"]
        elif mutation == "short":
            data["candidates"].pop()
        else:
            del data["candidates"][0]["shadow_recovery_ev"]
        with pytest.raises(ValidationError):
            EDIT_PLAN_ADAPTER.validate_python(data)
