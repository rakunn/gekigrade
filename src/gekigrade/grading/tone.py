"""Version 2 analytic tone and output-gamut operators. See docs/COLOR_PIPELINE.md."""

from __future__ import annotations

import math
from typing import cast

import numpy as np
import numpy.typing as npt

FloatImage = npt.NDArray[np.float32]
ACESCG_LUMINANCE = np.array([0.2722287168, 0.6740817658, 0.0536895174], dtype=np.float64)
SRGB_LUMINANCE = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)
GAMUT_METHOD = "linear-srgb-radial-soft-0.95-v1"


def _validate(pixels: FloatImage) -> None:
    if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.size == 0:
        raise ValueError("tone processing requires a nonempty HxWx3 RGB array")
    if not np.isfinite(pixels).all():
        raise ValueError("tone processing requires finite pixels")


def recover_shadows(pixels: FloatImage, stops: float) -> FloatImage:
    """Scale positive-Y RGB by 2**(stops * max(1-Y/0.18, 0)**2)."""
    _validate(pixels)
    if not math.isfinite(stops) or not 0.0 <= stops <= 2.0:
        raise ValueError("shadow recovery must be between 0 and 2 stops")
    if stops == 0.0:
        return pixels
    luminance = np.sum(pixels * ACESCG_LUMINANCE, axis=2, keepdims=True)
    weight = np.maximum(1.0 - np.maximum(luminance, 0.0) / 0.18, 0.0) ** 2
    gain = np.where(luminance > 0.0, np.exp2(stops * weight), 1.0)
    result = (pixels * gain).astype(np.float32)
    _validate(result)
    return result


def compress_highlights(pixels: FloatImage, amount: float) -> FloatImage:
    """Above Y=0.5, map Y to 0.5+(Y-0.5)/(1+2*amount*(Y-0.5))."""
    _validate(pixels)
    if not math.isfinite(amount) or not 0.0 <= amount <= 1.0:
        raise ValueError("highlight compression must be between 0 and 1")
    if amount == 0.0:
        return pixels
    luminance = np.sum(pixels * ACESCG_LUMINANCE, axis=2, keepdims=True)
    excess = np.maximum(luminance - 0.5, 0.0)
    target = 0.5 + excess / (1.0 + 2.0 * amount * excess)
    scale = np.divide(target, luminance, out=np.ones_like(luminance), where=luminance > 0.5)
    result = (pixels * scale).astype(np.float32)
    _validate(result)
    return result


def compress_output_gamut(pixels: FloatImage) -> FloatImage:
    """Fixed radial compression in linear sRGB; preserves feasible Y and RGB hue direction.

    The 95% interior is unchanged. Infeasible Y<=0 or Y>=1 becomes black/white;
    no in-gamut color can retain that luminance and nonzero chroma.
    """
    _validate(pixels)
    luminance = np.sum(pixels * SRGB_LUMINANCE, axis=2, keepdims=True)
    anchor = np.clip(luminance, 0.0, 1.0)
    delta = pixels - luminance
    bounds = np.where(delta > 0.0, 1.0 - anchor, anchor)
    # Only infeasible anchors have zero room. They are explicitly handled below.
    safe_bounds = np.where(bounds > 0.0, bounds, 1.0)
    excursion = np.max(np.abs(delta) / safe_bounds, axis=2, keepdims=True)
    excess = np.maximum(excursion - 0.95, 0.0)
    target = 0.95 + 0.05 * excess / (0.05 + excess)
    scale = np.divide(target, excursion, out=np.ones_like(excursion), where=excursion > 0.95)
    result = np.where((luminance <= 0.0) | (luminance >= 1.0), anchor, anchor + delta * scale)
    # Preserve interior float32 values exactly, including all feasible neutrals.
    result = np.where((excursion <= 0.95) & (luminance > 0.0) & (luminance < 1.0), pixels, result)
    return cast(FloatImage, result.astype(np.float32))
