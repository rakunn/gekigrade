from __future__ import annotations

from typing import Any


def _center_crop(width: int, height: int, target_ratio: float) -> dict[str, int]:
    source_ratio = width / height
    if source_ratio > target_ratio:
        crop_height = height
        crop_width = min(width, max(1, round(height * target_ratio)))
    else:
        crop_width = width
        crop_height = min(height, max(1, round(width / target_ratio)))
    left = (width - crop_width) // 2
    top = (height - crop_height) // 2
    return {"left": left, "top": top, "right": left + crop_width, "bottom": top + crop_height}


def _candidate(
    *, identifier: str, aspect_label: str, width: int, height: int, bounds: dict[str, int]
) -> dict[str, Any]:
    return {
        "id": identifier,
        "aspect_label": aspect_label,
        "x": bounds["left"] / width,
        "y": bounds["top"] / height,
        "width": (bounds["right"] - bounds["left"]) / width,
        "height": (bounds["bottom"] - bounds["top"]) / height,
        "reference_dimensions": {"width": width, "height": height},
        "pixel_bounds": bounds,
    }


def generate_crop_candidates(width: int, height: int) -> list[dict[str, Any]]:
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    specifications = (
        ("original", "original", width / height),
        ("feed-4x5-center", "instagram-feed-4x5", 4 / 5),
        ("story-9x16-center", "instagram-story-9x16", 9 / 16),
        ("square-1x1-center", "square-1x1", 1.0),
    )
    return [
        _candidate(
            identifier=identifier,
            aspect_label=label,
            width=width,
            height=height,
            bounds=_center_crop(width, height, ratio),
        )
        for identifier, label, ratio in specifications
    ]
