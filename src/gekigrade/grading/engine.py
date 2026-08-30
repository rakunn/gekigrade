from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import OpenImageIO as oiio
import PyOpenColorIO as ocio
from PIL import Image, ImageFilter

from gekigrade.domain.models import CandidateRecipe
from gekigrade.grading.looks import LookDefinition

FloatImage = npt.NDArray[np.float32]

OCIO_CONFIG = "cg-config-v4.0.0_aces-v2.0_ocio-v2.5"
ACESCG_TO_XYZ = np.array(
    [
        [0.6624541811, 0.1340042065, 0.1561876870],
        [0.2722287168, 0.6740817658, 0.0536895174],
        [-0.0055746495, 0.0040607335, 1.0103391003],
    ],
    dtype=np.float64,
)
XYZ_TO_ACESCG = np.linalg.inv(ACESCG_TO_XYZ)
BRADFORD = np.array(
    [[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]],
    dtype=np.float64,
)
BRADFORD_INVERSE = np.linalg.inv(BRADFORD)


@lru_cache(maxsize=1)
def _ocio_config() -> ocio.Config:
    return ocio.Config.CreateFromBuiltinConfig(OCIO_CONFIG)


def _transform(pixels: FloatImage, source: str, target: str) -> FloatImage:
    result = np.array(pixels, dtype=np.float32, order="C", copy=True)
    processor = _ocio_config().getProcessor(source, target).getDefaultCPUProcessor()
    processor.applyRGB(result)
    return result


def read_linear_image(path: str) -> FloatImage:
    buffer = oiio.ImageBuf(path)
    if buffer.has_error:
        raise RuntimeError(f"OpenImageIO could not read working image: {buffer.geterror()}")
    pixels = np.asarray(buffer.get_pixels(oiio.FLOAT), dtype=np.float32)
    if pixels.ndim != 3 or pixels.shape[2] < 3:
        raise ValueError("working image is not RGB")
    return np.ascontiguousarray(pixels[:, :, :3])


def _rotate(pixels: FloatImage, degrees: float) -> FloatImage:
    if abs(degrees) < 1e-12:
        return pixels.copy()
    source = oiio.ImageBuf(pixels)
    rotated = oiio.ImageBufAlgo.rotate(source, math.radians(degrees), "lanczos3", 0.0, False)
    if rotated.has_error:
        raise RuntimeError(f"OpenImageIO rotation failed: {rotated.geterror()}")
    return np.asarray(rotated.get_pixels(oiio.FLOAT), dtype=np.float32)


def _cct_xy(kelvin: float) -> tuple[float, float]:
    temperature = min(25000.0, max(1667.0, kelvin))
    if temperature <= 4000:
        x = (
            -0.2661239e9 / temperature**3
            - 0.2343580e6 / temperature**2
            + 0.8776956e3 / temperature
            + 0.179910
        )
    else:
        x = (
            -3.0258469e9 / temperature**3
            + 2.1070379e6 / temperature**2
            + 0.2226347e3 / temperature
            + 0.240390
        )
    if temperature <= 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif temperature <= 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    return x, y


def _xy_to_xyz(x: float, y: float) -> npt.NDArray[np.float64]:
    return np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=np.float64)


def _temperature_adaptation(pixels: FloatImage, mired_shift: float) -> FloatImage:
    if abs(mired_shift) < 1e-12:
        return pixels
    base_kelvin = 6000.0
    target_kelvin = 1_000_000.0 / (1_000_000.0 / base_kelvin + mired_shift)
    source_white = _xy_to_xyz(*_cct_xy(base_kelvin))
    target_white = _xy_to_xyz(*_cct_xy(target_kelvin))
    source_cone = BRADFORD @ source_white
    target_cone = BRADFORD @ target_white
    adaptation = BRADFORD_INVERSE @ np.diag(target_cone / source_cone) @ BRADFORD
    rgb_matrix = XYZ_TO_ACESCG @ adaptation @ ACESCG_TO_XYZ
    adapted = np.einsum("...c,dc->...d", pixels, rgb_matrix, dtype=np.float64)
    return cast(FloatImage, adapted.astype(np.float32))


def _tone(
    acescct: FloatImage,
    *,
    contrast: float,
    black_lift: float,
    highlight_rolloff: float,
    saturation: float,
    shadow_hue_shift: float = 0.0,
) -> FloatImage:
    pivot = np.float32(0.4135884)
    result = pivot + (acescct - pivot) * np.float32(1.0 + contrast * 1.5)
    shadows = np.clip((pivot - result) / np.float32(0.25), 0.0, 1.0)
    result += np.float32(black_lift) * shadows
    highlights = np.maximum(result - np.float32(0.48), 0.0)
    result -= (
        np.float32(highlight_rolloff) * highlights * highlights / (np.float32(0.15) + highlights)
    )
    luminance = np.sum(
        result * np.array([0.2722287, 0.6740818, 0.0536895], dtype=np.float32),
        axis=2,
        keepdims=True,
    )
    result = luminance + (result - luminance) * np.float32(1.0 + saturation)
    if shadow_hue_shift:
        weight = np.clip((pivot - luminance) / np.float32(0.20), 0.0, 1.0)
        bias = np.array([-0.5, 0.15, 1.0], dtype=np.float32)
        result += weight * bias * np.float32(shadow_hue_shift)
    return cast(FloatImage, result.astype(np.float32))


def _vignette(pixels: FloatImage, strength: float) -> FloatImage:
    if strength <= 0:
        return pixels
    height, width = pixels.shape[:2]
    y = np.linspace(-1.0, 1.0, height, dtype=np.float32)[:, None]
    x = np.linspace(-1.0, 1.0, width, dtype=np.float32)[None, :]
    radius = np.sqrt(x * x + y * y) / math.sqrt(2.0)
    falloff = np.clip((radius - 0.35) / 0.65, 0.0, 1.0)
    smooth = falloff * falloff * (3.0 - 2.0 * falloff)
    vignetted = pixels * (1.0 - np.float32(strength) * smooth[:, :, None].astype(np.float32))
    return cast(FloatImage, vignetted)


def apply_recipe(pixels: FloatImage, recipe: CandidateRecipe, look: LookDefinition) -> FloatImage:
    result = _rotate(pixels, recipe.rotation_degrees)
    result *= np.float32(2.0**recipe.exposure_ev)
    result = _temperature_adaptation(result, recipe.temperature_mired_shift)
    acescct = _transform(result, "ACEScg", "ACEScct")
    acescct = _tone(
        acescct,
        contrast=recipe.contrast,
        black_lift=recipe.black_lift,
        highlight_rolloff=recipe.highlight_rolloff,
        saturation=recipe.saturation,
    )
    operations = look.operations
    look_input = _temperature_adaptation(
        _transform(acescct, "ACEScct", "ACEScg"), operations.temperature_mired_shift
    )
    look_cct = _transform(look_input, "ACEScg", "ACEScct")
    looked = _tone(
        look_cct,
        contrast=operations.contrast,
        black_lift=operations.black_lift,
        highlight_rolloff=operations.highlight_rolloff,
        saturation=operations.saturation,
        shadow_hue_shift=operations.shadow_hue_shift,
    )
    acescct = acescct + (looked - acescct) * np.float32(recipe.look.strength)
    result = _transform(acescct, "ACEScct", "ACEScg")
    return _vignette(result, recipe.vignette).astype(np.float32)


def crop_normalized(pixels: FloatImage, crop: dict[str, Any]) -> FloatImage:
    height, width = pixels.shape[:2]
    left = max(0, min(width - 1, round(float(crop["x"]) * width)))
    top = max(0, min(height - 1, round(float(crop["y"]) * height)))
    right = max(left + 1, min(width, round((float(crop["x"]) + float(crop["width"])) * width)))
    bottom = max(top + 1, min(height, round((float(crop["y"]) + float(crop["height"])) * height)))
    return np.ascontiguousarray(pixels[top:bottom, left:right, :])


def resize_float(pixels: FloatImage, width: int, height: int) -> FloatImage:
    source = oiio.ImageBuf(pixels)
    destination = oiio.ImageBuf(oiio.ImageSpec(width, height, 3, oiio.FLOAT))
    if not oiio.ImageBufAlgo.resize(destination, source, "lanczos3"):
        raise RuntimeError(f"OpenImageIO resize failed: {destination.geterror()}")
    return np.asarray(destination.get_pixels(oiio.FLOAT), dtype=np.float32)


def linear_to_encoded_srgb(pixels: FloatImage) -> FloatImage:
    return _transform(pixels, "ACEScg", "sRGB Encoded Rec.709 (sRGB)")


def sharpen_uint8(pixels: npt.NDArray[np.uint8], amount: float) -> npt.NDArray[np.uint8]:
    if amount <= 0:
        return pixels
    image = Image.fromarray(pixels, mode="RGB")
    sharpened = image.filter(
        ImageFilter.UnsharpMask(radius=1.2, percent=round(80 * amount), threshold=2)
    )
    return np.asarray(sharpened, dtype=np.uint8)
