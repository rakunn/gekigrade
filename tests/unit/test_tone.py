from __future__ import annotations

import numpy as np
import pytest

from gekigrade.grading.tone import (
    ACESCG_LUMINANCE,
    SRGB_LUMINANCE,
    compress_highlights,
    compress_output_gamut,
    recover_shadows,
)


def test_shadow_gradient_is_monotone_black_preserving_and_bounded() -> None:
    y = np.linspace(0.0, 2.0, 100001, dtype=np.float32)
    pixels = np.repeat(y[None, :, None], 3, axis=2)
    previous = pixels
    for stops in (0.0, 0.25, 0.75, 1.0, 2.0):
        result = recover_shadows(pixels, stops)
        assert np.isfinite(result).all()
        assert np.all(np.diff(result[0, :, 0]) >= 0)
        assert np.all(result >= previous)
        assert np.all(result <= pixels * 2**stops + 1e-7)
        np.testing.assert_array_equal(result[:, y >= 0.18], pixels[:, y >= 0.18])
        np.testing.assert_array_equal(result[:, 0], pixels[:, 0])
        np.testing.assert_array_equal(result[:, :, 0], result[:, :, 1])
        previous = result


def test_rational_shoulder_is_monotone_and_does_not_touch_lower_tones() -> None:
    y = np.linspace(0.0, 16.0, 100001, dtype=np.float32)
    pixels = np.repeat(y[None, :, None], 3, axis=2)
    previous = pixels
    for amount in (0.0, 0.25, 0.8, 1.0):
        result = compress_highlights(pixels, amount)
        assert np.isfinite(result).all()
        assert np.all(np.diff(result[0, :, 0]) >= 0)
        assert np.all(result <= previous)
        np.testing.assert_array_equal(result[:, y <= 0.5], pixels[:, y <= 0.5])
        np.testing.assert_array_equal(result[:, :, 0], result[:, :, 2])
        previous = result
    assert np.max(previous) < 1.0
    sample = compress_highlights(np.full((1, 1, 3), 1.0, dtype=np.float32), 1.0)
    np.testing.assert_array_equal(sample, np.full((1, 1, 3), 0.75, dtype=np.float32))


def test_luminance_operators_preserve_chromaticity_and_do_not_mutate() -> None:
    pixels = np.array([[[0.01, 0.02, 0.05], [2.0, 0.7, 0.1], [-0.1, 0.0, -0.2]]], dtype=np.float32)
    before = pixels.copy()
    for result in (recover_shadows(pixels, 2.0), compress_highlights(pixels, 1.0)):
        np.testing.assert_allclose(np.cross(pixels[:, :2], result[:, :2]), 0.0, atol=2e-8, rtol=0)
        np.testing.assert_array_equal(result[:, 2], pixels[:, 2])
    np.testing.assert_array_equal(pixels, before)
    np.testing.assert_array_equal(recover_shadows(pixels, 0), pixels)
    np.testing.assert_array_equal(compress_highlights(pixels, 0), pixels)


def test_output_compression_preserves_luminance_and_hue_direction() -> None:
    patches = np.array(
        [[[-0.1, 0.2, 0.6], [1.2, 0.6, 0.1], [0.9, -0.1, 0.2], [0.1, 0.5, 1.4]]],
        dtype=np.float32,
    )
    before = patches.copy()
    result = compress_output_gamut(patches)
    y = np.sum(patches * SRGB_LUMINANCE, axis=2, keepdims=True)
    new_y = np.sum(result * SRGB_LUMINANCE, axis=2, keepdims=True)
    np.testing.assert_allclose(new_y, y, rtol=0, atol=3e-8)
    np.testing.assert_allclose(np.cross(patches - y, result - new_y), 0, rtol=0, atol=3e-8)
    assert np.all(np.sum((patches - y) * (result - new_y), axis=2) > 0)
    assert result.min() > 0
    assert result.max() < 1
    np.testing.assert_array_equal(patches, before)
    np.testing.assert_array_equal(result, compress_output_gamut(patches))


def test_gamut_neutrals_interior_and_infeasible_luminance() -> None:
    y = np.linspace(0, 1, 1001, dtype=np.float32)
    neutrals = np.repeat(y[None, :, None], 3, axis=2)
    np.testing.assert_array_equal(compress_output_gamut(neutrals), neutrals)
    interior = np.array([[[0.1, 0.2, 0.3], [0.7, 0.8, 0.9]]], dtype=np.float32)
    np.testing.assert_array_equal(compress_output_gamut(interior), interior)
    outside = np.array([[[-1, -0.5, 1], [3, 2, 0]]], dtype=np.float32)
    np.testing.assert_array_equal(
        compress_output_gamut(outside), np.array([[[0, 0, 0], [1, 1, 1]]], dtype=np.float32)
    )


def test_gamut_compression_is_continuous_and_monotone_along_a_chroma_ray() -> None:
    excursion = np.linspace(0, 4, 50001, dtype=np.float32)
    direction = np.array([0.6, -0.2, 0.0], dtype=np.float64)
    direction -= np.sum(direction * SRGB_LUMINANCE)
    pixels = (0.5 + excursion[None, :, None] * direction).astype(np.float32)
    result = compress_output_gamut(pixels)
    chroma = np.linalg.norm(result - 0.5, axis=2)
    assert np.diff(chroma).min() >= -1e-7
    assert np.diff(chroma).max() < 0.0001
    assert result.min() >= 0
    assert result.max() <= 1


@pytest.mark.parametrize("value", [-0.01, 2.01, float("nan"), float("inf"), -float("inf")])
def test_shadow_parameter_bounds(value: float) -> None:
    with pytest.raises(ValueError, match="stops"):
        recover_shadows(np.ones((1, 1, 3), dtype=np.float32), value)


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), -float("inf")])
def test_highlight_parameter_bounds(value: float) -> None:
    with pytest.raises(ValueError, match="compression"):
        compress_highlights(np.ones((1, 1, 3), dtype=np.float32), value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_input_is_rejected_even_with_zero_controls(value: float) -> None:
    pixels = np.full((1, 1, 3), value, dtype=np.float32)
    with pytest.raises(ValueError, match="finite"):
        recover_shadows(pixels, 0)
    with pytest.raises(ValueError, match="finite"):
        compress_highlights(pixels, 0)
    with pytest.raises(ValueError, match="finite"):
        compress_output_gamut(pixels)


def test_luminance_constants_are_normalized() -> None:
    assert sum(ACESCG_LUMINANCE) == pytest.approx(1.0)
    assert sum(SRGB_LUMINANCE) == pytest.approx(1.0)
