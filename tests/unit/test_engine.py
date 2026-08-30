from __future__ import annotations

import numpy as np

from gekigrade.domain.models import CandidateRecipe
from gekigrade.grading.engine import _transform, apply_recipe
from gekigrade.grading.looks import get_look


def _warm_candidate_with_zero_look_strength() -> CandidateRecipe:
    return CandidateRecipe.model_validate(
        {
            "id": "warm-regression",
            "description": "Regression signal",
            "rotation_degrees": 0.0,
            "exposure_ev": 0.0,
            "temperature_mired_shift": 0.0,
            "contrast": 0.0,
            "black_lift": 0.0,
            "highlight_rolloff": 0.0,
            "saturation": 0.0,
            "vignette": 0.0,
            "sharpen": 0.0,
            "look": {"id": "warm-editorial", "version": "1.0.0", "strength": 0.0},
            "crop_id": "original",
        }
    )


def test_ocio_transform_does_not_mutate_its_input_buffer() -> None:
    pixels = np.array(
        [[[0.05, 0.18, 0.4], [0.7, 0.5, 0.2]]],
        dtype=np.float32,
    )
    before = pixels.copy()

    _transform(pixels, "ACEScg", "ACEScct")

    np.testing.assert_array_equal(pixels, before)


def test_zero_strength_look_is_independent_of_internal_look_temperature() -> None:
    pixels = np.linspace(0.02, 0.9, 8 * 8 * 3, dtype=np.float32).reshape(8, 8, 3)
    candidate = _warm_candidate_with_zero_look_strength()
    warm = get_look("warm-editorial", "1.0.0")
    no_temperature = warm.model_copy(
        update={"operations": warm.operations.model_copy(update={"temperature_mired_shift": 0.0})}
    )

    shifted_result = apply_recipe(pixels, candidate, warm)
    neutral_result = apply_recipe(pixels, candidate, no_temperature)

    np.testing.assert_allclose(shifted_result, neutral_result, rtol=0.0, atol=1e-6)
