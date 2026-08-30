from __future__ import annotations

import numpy as np

from gekigrade.analysis.metrics import analyze_srgb


def test_analysis_reports_fixed_histograms_and_known_clipping() -> None:
    pixels = np.array(
        [
            [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
            [[0.5, 0.25, 0.75], [0.1, 0.2, 0.3]],
        ],
        dtype=np.float32,
    )

    result = analyze_srgb(pixels)

    assert result["pixel_count"] == 4
    assert len(result["histograms"]["red"]) == 256
    assert len(result["histograms"]["luminance"]) == 256
    assert result["clipping"]["shadow_all_percent"] == 25.0
    assert result["clipping"]["highlight_all_percent"] == 25.0
    assert result["luminance"]["minimum"] == 0.0
    assert result["luminance"]["maximum"] == 1.0


def test_analysis_rejects_non_rgb_and_non_finite_pixels() -> None:
    with np.testing.assert_raises(ValueError):
        analyze_srgb(np.zeros((5, 5), dtype=np.float32))

    invalid = np.zeros((2, 2, 3), dtype=np.float32)
    invalid[0, 0, 0] = np.nan
    with np.testing.assert_raises(ValueError):
        analyze_srgb(invalid)
