from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt


def _percent(mask: npt.NDArray[np.bool_]) -> float:
    return round(float(np.mean(mask, dtype=np.float64) * 100.0), 8)


def _histogram(channel: npt.NDArray[np.float32]) -> list[int]:
    histogram, _ = np.histogram(channel, bins=256, range=(0.0, 1.0))
    return histogram.astype(np.int64).tolist()


def analyze_srgb(pixels: npt.NDArray[np.float32]) -> dict[str, Any]:
    if pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("analysis requires an HxWx3 RGB array")
    if not np.isfinite(pixels).all():
        raise ValueError("analysis pixels must all be finite")
    rgb = np.clip(pixels.astype(np.float32, copy=False), 0.0, 1.0)
    luminance = (
        rgb[:, :, 0] * np.float32(0.2126)
        + rgb[:, :, 1] * np.float32(0.7152)
        + rgb[:, :, 2] * np.float32(0.0722)
    )
    shadow = rgb <= np.float32(1.0 / 255.0)
    highlight = rgb >= np.float32(254.0 / 255.0)
    channel_max = np.max(rgb, axis=2)
    channel_min = np.min(rgb, axis=2)
    saturation = np.divide(
        channel_max - channel_min,
        channel_max,
        out=np.zeros_like(channel_max),
        where=channel_max > 0,
    )
    laplacian = (
        -4.0 * luminance[1:-1, 1:-1]
        + luminance[:-2, 1:-1]
        + luminance[2:, 1:-1]
        + luminance[1:-1, :-2]
        + luminance[1:-1, 2:]
    )
    sharpness = float(np.var(laplacian, dtype=np.float64)) if laplacian.size else 0.0
    return {
        "schema_version": "1.0.0",
        "pixel_count": int(rgb.shape[0] * rgb.shape[1]),
        "histograms": {
            "red": _histogram(rgb[:, :, 0]),
            "green": _histogram(rgb[:, :, 1]),
            "blue": _histogram(rgb[:, :, 2]),
            "luminance": _histogram(luminance),
        },
        "clipping": {
            "shadow_any_percent": _percent(np.any(shadow, axis=2)),
            "shadow_all_percent": _percent(np.all(shadow, axis=2)),
            "highlight_any_percent": _percent(np.any(highlight, axis=2)),
            "highlight_all_percent": _percent(np.all(highlight, axis=2)),
        },
        "luminance": {
            "minimum": float(np.min(luminance)),
            "p01": float(np.percentile(luminance, 1)),
            "p05": float(np.percentile(luminance, 5)),
            "average": float(np.mean(luminance, dtype=np.float64)),
            "median": float(np.median(luminance)),
            "p95": float(np.percentile(luminance, 95)),
            "p99": float(np.percentile(luminance, 99)),
            "maximum": float(np.max(luminance)),
        },
        "saturation": {
            "average": float(np.mean(saturation, dtype=np.float64)),
            "p95": float(np.percentile(saturation, 95)),
        },
        "sharpness": {"laplacian_variance": sharpness},
    }
