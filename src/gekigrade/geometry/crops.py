from __future__ import annotations

from typing import Any, Literal

Anchor = Literal["left", "center", "right", "top", "bottom"]
TrimAxis = Literal["horizontal", "vertical", "none"]
CROP_SCHEMA_VERSION = "2.0.0"


def _crop_dimensions(width: int, height: int, target_ratio: float) -> tuple[int, int]:
    source_ratio = width / height
    if source_ratio > target_ratio:
        crop_height = height
        crop_width = min(width, max(1, round(height * target_ratio)))
    else:
        crop_width = width
        crop_height = min(height, max(1, round(width / target_ratio)))
    return crop_width, crop_height


def _anchored_crop(width: int, height: int, target_ratio: float, anchor: Anchor) -> dict[str, int]:
    crop_width, crop_height = _crop_dimensions(width, height, target_ratio)
    horizontal_space = width - crop_width
    vertical_space = height - crop_height
    if horizontal_space:
        left = {
            "left": 0,
            "center": horizontal_space // 2,
            "right": horizontal_space,
        }[anchor]
        top = 0
    elif vertical_space:
        left = 0
        top = {
            "top": 0,
            "center": vertical_space // 2,
            "bottom": vertical_space,
        }[anchor]
    else:
        left = 0
        top = 0
    return {"left": left, "top": top, "right": left + crop_width, "bottom": top + crop_height}


def _candidate(
    *,
    identifier: str,
    aspect_label: str,
    anchor: Anchor,
    trim_axis: TrimAxis,
    width: int,
    height: int,
    bounds: dict[str, int],
) -> dict[str, Any]:
    return {
        "id": identifier,
        "aspect_label": aspect_label,
        "anchor": anchor,
        "trim_axis": trim_axis,
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
    candidates = [
        _candidate(
            identifier="original",
            aspect_label="original",
            anchor="center",
            trim_axis="none",
            width=width,
            height=height,
            bounds={"left": 0, "top": 0, "right": width, "bottom": height},
        )
    ]
    specifications = (
        ("feed-4x5", "instagram-feed-4x5", 4 / 5),
        ("story-9x16", "instagram-story-9x16", 9 / 16),
        ("square-1x1", "square-1x1", 1.0),
    )
    for identifier, label, ratio in specifications:
        crop_width, crop_height = _crop_dimensions(width, height, ratio)
        if crop_width < width:
            anchors: tuple[Anchor, ...] = ("left", "center", "right")
            trim_axis: TrimAxis = "horizontal"
        elif crop_height < height:
            anchors = ("top", "center", "bottom")
            trim_axis = "vertical"
        else:
            anchors = ("center",)
            trim_axis = "none"
        candidates.extend(
            _candidate(
                identifier=f"{identifier}-{anchor}",
                aspect_label=label,
                anchor=anchor,
                trim_axis=trim_axis,
                width=width,
                height=height,
                bounds=_anchored_crop(width, height, ratio, anchor),
            )
            for anchor in anchors
        )
    return candidates
