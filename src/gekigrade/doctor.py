from __future__ import annotations

import hashlib
import platform
import plistlib
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import OpenImageIO as oiio
import PyOpenColorIO as ocio
from PIL import Image

from gekigrade.adapters.rawtherapee import (
    DEFAULT_RAW_PROFILE,
    LENSFUN_DATABASE,
    RAWTHERAPEE_OUTPUT_PROFILE,
)
from gekigrade.adapters.tools import ExternalTool, ToolStatus, inspect_tool

ACESCG_PROFILE = Path("/System/Library/ColorSync/Profiles/ACESCG Linear.icc")
SRGB_PROFILE = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc")
RAWTHERAPEE_CLI = Path("/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli")
RAWTHERAPEE_PLIST = Path("/Applications/RawTherapee.app/Contents/Info.plist")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _profile_status(path: Path) -> dict[str, str | bool | None]:
    exists = path.is_file()
    return {
        "available": exists,
        "path": str(path),
        "sha256": sha256_file(path) if exists else None,
    }


def _rawtherapee_status() -> ToolStatus:
    version: str | None = None
    if RAWTHERAPEE_PLIST.is_file():
        with RAWTHERAPEE_PLIST.open("rb") as stream:
            raw_version = plistlib.load(stream).get("CFBundleShortVersionString")
            version = str(raw_version) if raw_version else None
    available = RAWTHERAPEE_CLI.is_file() and RAWTHERAPEE_CLI.stat().st_mode & 0o111 != 0
    return ToolStatus(
        name="rawtherapee",
        available=available,
        path=str(RAWTHERAPEE_CLI) if available else None,
        version=version,
        install_hint="Install with: brew install --cask rawtherapee",
    )


def _color_probe(magick_path: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="gekigrade-color-probe-") as directory:
        root = Path(directory)
        source = root / "source.jpg"
        first = root / "first.tif"
        second = root / "second.tif"
        patches = np.zeros((16, 16, 3), dtype=np.uint8)
        patches[:8, :8] = (32, 64, 128)
        patches[:8, 8:] = (224, 190, 128)
        patches[8:, :8] = (2, 2, 2)
        patches[8:, 8:] = (253, 253, 253)
        Image.fromarray(patches, mode="RGB").save(
            source,
            quality=100,
            subsampling=0,
            icc_profile=SRGB_PROFILE.read_bytes(),
        )
        for target in (first, second):
            result = subprocess.run(
                [
                    magick_path,
                    str(source),
                    "-profile",
                    str(ACESCG_PROFILE),
                    "-depth",
                    "16",
                    "-define",
                    "tiff:bits-per-sample=16",
                    "-compress",
                    "zip",
                    f"TIFF:{target}",
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                return {"passed": False, "error": result.stderr.strip()}
        buffer = oiio.ImageBuf(str(first))
        pixels = np.asarray(buffer.get_pixels(oiio.FLOAT), dtype=np.float32)[:, :, :3]
        roundtrip = np.ascontiguousarray(pixels.copy())
        config = ocio.Config.CreateFromBuiltinConfig("cg-config-v4.0.0_aces-v2.0_ocio-v2.5")
        config.getProcessor("ACEScg", "ACEScct").getDefaultCPUProcessor().applyRGB(roundtrip)
        config.getProcessor("ACEScct", "ACEScg").getDefaultCPUProcessor().applyRGB(roundtrip)
        rmse = float(np.sqrt(np.mean((roundtrip - pixels) ** 2, dtype=np.float64)))
        with Image.open(first) as working:
            profile_embedded = bool(working.info.get("icc_profile"))
        repeated = sha256_file(first) == sha256_file(second)
        return {
            "passed": repeated and profile_embedded and rmse < 0.00001,
            "repeat_tiff_file_hash_equal": repeated,
            "working_profile_embedded": profile_embedded,
            "ocio_roundtrip_rmse": rmse,
        }


def build_doctor_report(*, run_color_probe: bool = True) -> dict[str, Any]:
    tools = {
        "exiftool": inspect_tool(
            ExternalTool(
                name="exiftool",
                candidates=("exiftool", "/opt/homebrew/bin/exiftool"),
                version_args=("-ver",),
                install_hint="Install with: brew install exiftool",
            )
        ),
        "imagemagick": inspect_tool(
            ExternalTool(
                name="imagemagick",
                candidates=("magick", "/opt/homebrew/bin/magick"),
                version_args=("-version",),
                install_hint="Install with: brew install imagemagick",
            )
        ),
        "rawtherapee": _rawtherapee_status(),
    }
    profiles = {
        "acescg": _profile_status(ACESCG_PROFILE),
        "srgb": _profile_status(SRGB_PROFILE),
        "raw_development_pp3": _profile_status(DEFAULT_RAW_PROFILE),
        "rawtherapee_output": _profile_status(RAWTHERAPEE_OUTPUT_PROFILE),
        "lensfun_database": _profile_status(LENSFUN_DATABASE),
    }
    prerequisites_ready = (
        tools["exiftool"].available
        and tools["imagemagick"].available
        and profiles["acescg"]["available"] is True
        and profiles["srgb"]["available"] is True
    )
    color_probe: dict[str, Any] | None = None
    if run_color_probe and prerequisites_ready and tools["imagemagick"].path:
        color_probe = _color_probe(tools["imagemagick"].path)
    ready = prerequisites_ready and (color_probe is None or color_probe.get("passed") is True)
    ready_for_raw = (
        ready
        and tools["rawtherapee"].available
        and profiles["raw_development_pp3"]["available"] is True
        and profiles["rawtherapee_output"]["available"] is True
        and profiles["lensfun_database"]["available"] is True
    )
    return {
        "schema_version": "1.0.0",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "openimageio": oiio.VERSION_STRING,
            "opencolorio": ocio.GetVersion(),
        },
        "tools": {name: asdict(status) for name, status in tools.items()},
        "profiles": profiles,
        "color_probe": color_probe,
        "ready_for_jpeg": ready,
        "ready_for_raw": ready_for_raw,
        "raw_status": "adapter-ready" if ready_for_raw else "not-ready",
    }
