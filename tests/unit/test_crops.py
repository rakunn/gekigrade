from __future__ import annotations

import pytest

from gekigrade.geometry.crops import generate_crop_candidates


@pytest.mark.parametrize(
    ("width", "height", "expected_count"),
    [(4000, 3000, 10), (3000, 4000, 10), (1000, 1000, 8)],
)
def test_crop_candidates_are_normalized_bounded_and_reproducible(
    width: int, height: int, expected_count: int
) -> None:
    candidates = generate_crop_candidates(width, height)

    assert len(candidates) == expected_count
    assert len({candidate["id"] for candidate in candidates}) == expected_count
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
    feed = {
        candidate["anchor"]: candidate
        for candidate in candidates
        if candidate["aspect_label"] == "instagram-feed-4x5"
    }

    assert set(feed) == {"left", "center", "right"}
    assert feed["left"]["pixel_bounds"] == {
        "left": 0,
        "top": 0,
        "right": 2400,
        "bottom": 3000,
    }
    assert feed["center"]["pixel_bounds"] == {
        "left": 800,
        "top": 0,
        "right": 3200,
        "bottom": 3000,
    }
    assert feed["right"]["pixel_bounds"] == {
        "left": 1600,
        "top": 0,
        "right": 4000,
        "bottom": 3000,
    }
    assert {candidate["trim_axis"] for candidate in feed.values()} == {"horizontal"}


def test_portrait_feed_candidates_cover_top_center_and_bottom() -> None:
    candidates = generate_crop_candidates(3000, 4000)
    feed = {
        candidate["anchor"]: candidate["pixel_bounds"]
        for candidate in candidates
        if candidate["aspect_label"] == "instagram-feed-4x5"
    }

    assert feed == {
        "top": {"left": 0, "top": 0, "right": 3000, "bottom": 3750},
        "center": {"left": 0, "top": 125, "right": 3000, "bottom": 3875},
        "bottom": {"left": 0, "top": 250, "right": 3000, "bottom": 4000},
    }


def test_every_anchor_reproduces_reference_and_preview_pixel_bounds() -> None:
    candidates = generate_crop_candidates(4000, 3000)

    for candidate in candidates:
        bounds = candidate["pixel_bounds"]
        full = {
            "left": round(candidate["x"] * 4000),
            "top": round(candidate["y"] * 3000),
            "right": round((candidate["x"] + candidate["width"]) * 4000),
            "bottom": round((candidate["y"] + candidate["height"]) * 3000),
        }
        preview = {
            "left": round(candidate["x"] * 1000),
            "top": round(candidate["y"] * 750),
            "right": round((candidate["x"] + candidate["width"]) * 1000),
            "bottom": round((candidate["y"] + candidate["height"]) * 750),
        }

        assert full == bounds
        assert preview == {edge: round(value / 4) for edge, value in bounds.items()}
