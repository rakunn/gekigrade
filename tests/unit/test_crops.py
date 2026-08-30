from __future__ import annotations

import pytest

from gekigrade.geometry.crops import generate_crop_candidates


@pytest.mark.parametrize(("width", "height"), [(4000, 3000), (3000, 4000), (1000, 1000)])
def test_crop_candidates_are_normalized_bounded_and_reproducible(width: int, height: int) -> None:
    candidates = generate_crop_candidates(width, height)

    assert {candidate["aspect_label"] for candidate in candidates} == {
        "original",
        "instagram-feed-4x5",
        "instagram-story-9x16",
        "square-1x1",
    }
    for candidate in candidates:
        assert 0.0 <= candidate["x"] < 1.0
        assert 0.0 <= candidate["y"] < 1.0
        assert 0.0 < candidate["width"] <= 1.0
        assert 0.0 < candidate["height"] <= 1.0
        assert candidate["x"] + candidate["width"] <= 1.0 + 1e-12
        assert candidate["y"] + candidate["height"] <= 1.0 + 1e-12


def test_feed_crop_uses_exact_integer_bounds_for_preview_and_full_resolution() -> None:
    candidates = generate_crop_candidates(4000, 3000)
    feed = next(candidate for candidate in candidates if candidate["id"] == "feed-4x5-center")

    assert feed["pixel_bounds"] == {"left": 800, "top": 0, "right": 3200, "bottom": 3000}
