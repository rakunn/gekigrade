"""Deterministic, unclamped stage measurements; no photographic pass/fail score."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import numpy.typing as npt

FloatImage = npt.NDArray[np.float32]


def measure_stage(
    pixels: FloatImage,
    *,
    to_encoded_srgb: Callable[[FloatImage], FloatImage] | None = None,
    scope: str = "output-frame",
) -> dict[str, Any]:
    """Measure every pixel in row tiles; transforms operate on independent copies.

    Strict out-of-range counts deliberately include numerical transform excursions.
    Extrema distinguish those from large errors. Clipping thresholds match analyze_srgb.
    """
    if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.size == 0:
        raise ValueError("stage measurement requires a nonempty HxWx3 RGB array")
    counts = dict.fromkeys(
        (
            "low_any",
            "low_all",
            "high_any",
            "high_all",
            "shadow_any",
            "shadow_all",
            "highlight_any",
            "highlight_all",
        ),
        0,
    )
    minimum = np.full(3, np.inf, dtype=np.float64)
    maximum = np.full(3, -np.inf, dtype=np.float64)
    for start in range(0, pixels.shape[0], 256):
        tile = pixels[start : start + 256]
        if not np.isfinite(tile).all():
            raise ValueError("stage measurement requires finite pixels")
        encoded = to_encoded_srgb(tile) if to_encoded_srgb is not None else tile
        if not np.isfinite(encoded).all():
            raise ValueError("stage conversion produced nonfinite pixels")
        minimum = np.minimum(minimum, np.min(encoded, axis=(0, 1)))
        maximum = np.maximum(maximum, np.max(encoded, axis=(0, 1)))
        for label, mask in (
            ("low", encoded < 0.0),
            ("high", encoded > 1.0),
            ("shadow", encoded <= np.float32(1.0 / 255.0)),
            ("highlight", encoded >= np.float32(254.0 / 255.0)),
        ):
            counts[f"{label}_any"] += int(np.count_nonzero(np.any(mask, axis=2)))
            counts[f"{label}_all"] += int(np.count_nonzero(np.all(mask, axis=2)))
    pixel_count = pixels.shape[0] * pixels.shape[1]
    percentages = {
        name + "_percent": round(value / pixel_count * 100.0, 8) for name, value in counts.items()
    }
    return {
        "measurement_space": "unclamped-encoded-sRGB",
        "scope": scope,
        "width": pixels.shape[1],
        "height": pixels.shape[0],
        "pixel_count": pixel_count,
        "finite": True,
        "channel_minimum": minimum.tolist(),
        "channel_maximum": maximum.tolist(),
        "out_of_gamut": {
            name: value for name, value in percentages.items() if name.startswith(("low", "high_"))
        },
        "clipping": {
            name: value
            for name, value in percentages.items()
            if name.startswith(("shadow", "highlight"))
        },
    }
