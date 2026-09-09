from __future__ import annotations

import numpy as np
import pytest

from gekigrade.analysis.stages import measure_stage
from gekigrade.domain.models import CandidateRecipe, CandidateRecipeV2
from gekigrade.grading.engine import apply_recipe, linear_to_encoded_srgb
from gekigrade.grading.looks import get_look
from gekigrade.pipeline.prepare import _example_plan, _example_plan_v2
from gekigrade.pipeline.render import evaluate_candidate


def test_stage_metrics_count_unclamped_values_without_mutation() -> None:
    pixels = np.array(
        [[[-0.2, 0.5, 1.2], [-0.1, -0.1, -0.1], [2, 2, 2], [0.5, 0.5, 0.5]]], dtype=np.float32
    )
    before = pixels.copy()
    metrics = measure_stage(pixels)
    assert metrics["pixel_count"] == 4
    assert metrics["out_of_gamut"] == {
        "low_any_percent": 50.0,
        "low_all_percent": 25.0,
        "high_any_percent": 50.0,
        "high_all_percent": 25.0,
    }
    assert metrics["clipping"] == {
        "shadow_any_percent": 50.0,
        "shadow_all_percent": 25.0,
        "highlight_any_percent": 50.0,
        "highlight_all_percent": 25.0,
    }
    np.testing.assert_array_equal(pixels, before)
    assert metrics == measure_stage(pixels)


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0"])
def test_observation_does_not_change_pixels_and_stage_scopes_are_explicit(version: str) -> None:
    pixels = np.linspace(0.001, 1.5, 300 * 8 * 3, dtype=np.float32).reshape(300, 8, 3)
    candidate = (
        CandidateRecipe.model_validate(_example_plan("a" * 64)["candidates"][1])
        if version == "1.0.0"
        else CandidateRecipeV2.model_validate(_example_plan_v2("a" * 64)["candidates"][1])
    )
    look = get_look(candidate.look.id, candidate.look.version)
    snapshots = {}
    result = apply_recipe(
        pixels, candidate, look, observe=lambda name, p: snapshots.update({name: p.copy()})
    )
    np.testing.assert_array_equal(result, apply_recipe(pixels, candidate, look))
    crop = {"x": 0.25, "y": 0.0, "width": 0.5, "height": 1.0}
    integer, qa = evaluate_candidate(pixels, candidate, crop, target_dimensions=(4, 16))
    for name, snapshot in snapshots.items():
        assert qa["stages"][name] == measure_stage(
            snapshot, to_encoded_srgb=linear_to_encoded_srgb, scope="full-frame-after-rotation"
        )
        assert qa["stages"][name]["pixel_count"] == 2400
    assert qa["stages"]["after_global_correction"] != qa["stages"]["after_creative_look"]
    for name in ("before_output_gamut", "before_output_clamp"):
        assert qa["stages"][name]["pixel_count"] == 64
    assert (
        qa["preclamp_low_percent"]
        == qa["stages"]["before_output_clamp"]["out_of_gamut"]["low_any_percent"]
    )
    second, second_qa = evaluate_candidate(pixels, candidate, crop, target_dimensions=(4, 16))
    np.testing.assert_array_equal(integer, second)
    assert qa == second_qa
    if version == "1.0.0":
        assert qa["stages"]["before_output_gamut"] == qa["stages"]["before_output_clamp"]
    else:
        # OCIO encodes exact linear white as ~1.0000067. Strict counts can remain
        # nonzero even though large excursions are removed; inspect magnitude too.
        assert max(qa["stages"]["before_output_clamp"]["channel_maximum"]) <= 1.00001
        assert max(qa["stages"]["before_output_gamut"]["channel_maximum"]) > 1.01


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_stage_measurement_rejects_nonfinite(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        measure_stage(np.full((1, 1, 3), value, dtype=np.float32))
